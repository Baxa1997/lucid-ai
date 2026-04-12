"""AgentOrchestrator — clean service boundary for AI task execution.

Encapsulates the full lifecycle of a single agent task:
  1. Hydrate session.repo_url from DB if empty (wizard projects)
  2. Launch run_pipeline as a cancellable asyncio.Task
  3. Listen for stop messages on the WebSocket
  4. On stopped: transition state, return stopped result
  5. On complete: hydrate repo_url again, build summary, persist to DB,
     transition state, return completion result

This removes the ~350-line duplicate pipeline-execution blocks that
previously existed in ws.py for the initial task and follow-up loop.

Usage
-----
    from app.services.agent_orchestrator import (
        agent_orchestrator,
        build_pipeline_user,
        build_enriched_task,
        build_conversation_context,
    )

    pipeline_user = build_pipeline_user(session, api_key, gemini_api_key, pm, user_jwt)
    enriched = await build_enriched_task(task, session, project_id, user_id, user_jwt)
    result = await agent_orchestrator.execute_task(
        enriched_task=enriched,
        session=session,
        websocket=websocket,
        pipeline_user=pipeline_user,
        chat_session_id=chat_session_id,
        conversation_id=conversation_id,
        user_jwt=user_jwt,
        task=task,          # raw task for DB summary
        images=[],
    )
    if result.stopped:
        explicit_stop = True
"""

from __future__ import annotations

import asyncio
import subprocess
import uuid
from dataclasses import dataclass
from typing import Any

from app.config import logger
from app.services.chat import ChatService
from app.services.pipeline import run_pipeline, PLATFORM_GITHUB_TOKEN
from app.services.sessions import AgentSession
from app.supabase_client import db_client
from app.workspace_states import WorkspaceState, transition as ws_transition


# ── Result type ───────────────────────────────────────────────


@dataclass
class TaskResult:
    stopped: bool
    summary: str = ""
    workspace_path: str | None = None


# ── Module-level helpers (public API) ─────────────────────────


def build_pipeline_user(
    session: AgentSession,
    api_key: str,
    gemini_api_key: str,
    package_manager: str,
    user_jwt: str | None,
) -> dict:
    """Build the pipeline_user config dict from current session state.

    Called before execute_task so the dict is always fresh.  The orchestrator
    may mutate it in-place during _hydrate_repo_url if repo_url was empty.
    """
    git_provider = "gitlab" if "gitlab" in (session.repo_url or "").lower() else "github"
    return {
        "anthropic_api_key": api_key,
        "gemini_api_key": gemini_api_key,
        "git_provider": git_provider,
        "github_repo": session.repo_url or "",
        "github_token": session.git_token or "",
        "gitlab_repo": session.repo_url or "",
        "gitlab_token": session.git_token or "",
        "selected_branch": session.branch or "main",
        "git_token": session.git_token or "",
        "package_manager": package_manager,
        "user_jwt": user_jwt or "",
    }


async def build_enriched_task(
    task: str,
    session: AgentSession,
    project_id: str,
    user_id: str,
    user_jwt: str | None,
) -> str:
    """Enrich a raw task with conversation context and repo structure.

    Used for initial tasks (handshake task).  Follow-up tasks are enriched
    inline in ws.py because they use a different format (no repo_ctx).
    """
    context_parts: list[str] = []

    if project_id and user_jwt:
        prev_context = await build_conversation_context(
            user_id, project_id, user_jwt, current_task=task
        )
        if prev_context:
            context_parts.append(prev_context)

    repo_ctx = getattr(session, "repo_context", "")
    if repo_ctx:
        context_parts.append(
            "I have cloned the repository into the workspace directory. "
            "Here is the project layout and key configuration files:\n\n"
            f"{repo_ctx}"
        )

    if not context_parts:
        return task

    if "[LUCID_PROJECT]" in task[:200]:
        return (
            f"{task}\n\n---\n\n## Previous conversation context\n\n"
            + "\n\n---\n\n".join(context_parts)
        )

    return (
        "\n\n---\n\n".join(context_parts)
        + f"\n\n---\n\nNow, here is my task:\n{task}\n"
    )


def _task_similarity(a: str, b: str) -> float:
    """Jaccard similarity on meaningful word tokens.

    Returns 0.0–1.0.  Returns 1.0 (assume related) when either side is too
    short to judge — avoids incorrectly suppressing context for very terse tasks.
    """
    _STOP = {
        "a", "an", "the", "and", "or", "in", "on", "at", "to", "for", "of",
        "with", "is", "it", "this", "that", "my", "i", "can", "you", "please",
        "make", "add", "fix", "update", "change", "create", "build", "new",
        "need", "want", "also", "just", "so", "do", "the", "be", "was",
    }

    def _tokens(s: str) -> set:
        return {w for w in s.lower().split() if len(w) > 2 and w not in _STOP}

    ta, tb = _tokens(a), _tokens(b)
    if len(ta) < 3 or len(tb) < 3:
        return 1.0  # too short to judge — assume related
    intersection = len(ta & tb)
    union = len(ta | tb)
    return intersection / union if union else 1.0


async def build_conversation_context(
    user_id: str,
    project_id: str,
    user_jwt: str,
    max_messages: int = 20,
    current_task: str = "",
) -> str:
    """Load recent messages from DB to rebuild agent context.

    Retrieves the most recent chat session for this user+project and builds a
    summary of past work so the agent does not start from scratch.

    Skips injection when the current task is clearly unrelated to the previous
    session's task (Jaccard similarity < 0.10 on meaningful word tokens).
    """
    try:
        # Try with summary columns first (fast path), fall back if missing
        try:
            async with db_client(user_jwt) as client:
                result = await (
                    client.table("chat_sessions")
                    .select("id, title, summary, last_task")
                    .eq("user_id", user_id)
                    .eq("project_id", project_id)
                    .order("updated_at", desc=True)
                    .limit(1)
                    .execute()
                )
        except Exception:
            async with db_client(user_jwt) as client:
                result = await (
                    client.table("chat_sessions")
                    .select("id, title")
                    .eq("user_id", user_id)
                    .eq("project_id", project_id)
                    .order("updated_at", desc=True)
                    .limit(1)
                    .execute()
                )

        if not result.data:
            return ""

        prev_session = result.data[0]
        session_id = prev_session["id"]

        # ── Gap 2: skip context if new task is clearly unrelated ─────────────
        # Compare current task against the stored last_task using word-token
        # Jaccard similarity.  A very low score means the user has switched to
        # a completely different topic — injecting stale context would confuse
        # the agent more than it helps.
        last_task = prev_session.get("last_task") or ""
        if current_task and last_task:
            sim = _task_similarity(current_task, last_task)
            if sim < 0.10:
                logger.info(
                    "build_conversation_context: skipping injection — "
                    "task similarity %.2f < 0.10 (new task unrelated to previous)",
                    sim,
                )
                return ""

        # Fast path: use the stored handoff note
        if prev_session.get("summary"):
            return (
                "## What happened in the previous session\n\n"
                f"{prev_session['summary']}\n\n"
                "Use this context to understand what was already done. "
                "Do not repeat completed work."
            )

        # Slow path: load the last N messages
        async with db_client(user_jwt) as client:
            msgs = await (
                client.table("chat_messages")
                .select("role, content, event_type")
                .eq("session_id", session_id)
                .order("created_at", desc=True)
                .limit(max_messages)
                .execute()
            )

        if not msgs.data:
            return ""

        messages = list(reversed(msgs.data))

        # Skip context injection if the previous session produced no real agent
        # output — e.g. a wizard run that failed before doing any work.
        # Injecting context from a failed session causes false [LUCID_PROJECT]
        # header detection and pollutes the new task's plan display.
        has_agent_output = any(
            m.get("role") == "assistant"
            and m.get("event_type") in ("AgentResponse", "ChangeSummary")
            for m in messages
        )
        if not has_agent_output:
            return ""

        lines = ["## Previous conversation context\n"]

        for msg in messages:
            role = msg.get("role", "")
            content = (msg.get("content") or "")[:500]
            event_type = msg.get("event_type") or ""

            if not content.strip():
                continue

            if role == "user":
                lines.append(f"**User asked:** {content}")
            elif event_type == "ChangeSummary":
                lines.append(f"**Changes made:** {content}")
            elif role == "assistant" and event_type in ("MessageEvent", "ActionEvent"):
                if len(content) > 30:
                    lines.append(f"**Agent:** {content[:300]}")

        if len(lines) <= 1:
            return ""

        return "\n".join(lines)

    except Exception as exc:
        logger.warning("Failed to load conversation context: %s", exc)
        return ""


# ── Orchestrator class ────────────────────────────────────────


class AgentOrchestrator:
    """Stateless service that executes one agent task end-to-end.

    Stateless means it holds no per-task state — all context is passed as
    arguments.  The module-level singleton is safe for concurrent use.
    """

    async def execute_task(
        self,
        *,
        enriched_task: str,
        session: AgentSession,
        websocket: Any,
        pipeline_user: dict,
        chat_session_id: str,
        conversation_id: str,
        user_jwt: str | None,
        task: str = "",
        images: list | None = None,
    ) -> TaskResult:
        """Run the pipeline for one task and return the result.

        Args:
            enriched_task:    Task text with conversation context injected.
            session:          Active AgentSession.
            websocket:        WebSocket or WebSocketProxy (same interface).
            pipeline_user:    Config dict built by build_pipeline_user().
                              Mutated in-place if repo_url is hydrated.
            chat_session_id:  Supabase chat_sessions.id for DB persistence.
            conversation_id:  Unique ID for this WS connection (workspace key).
            user_jwt:         Raw JWT for Supabase calls, or None for admin.
            task:             Raw (un-enriched) task text used for DB summaries.
            images:           Optional list of image attachments.

        Returns:
            TaskResult with stopped=True if the user pressed Stop,
            or stopped=False with summary and workspace_path on completion.
        """
        # ── 1. Hydrate repo_url before pipeline (wizard projects) ──
        await self._hydrate_repo_url(session, pipeline_user, chat_session_id, user_jwt)

        # ── 2. Create cancellable pipeline task ────────────────────
        _proxy = session.ws_proxy or websocket
        pipeline_task_id = str(uuid.uuid4())[:8]
        pipeline_task = asyncio.create_task(
            run_pipeline(
                task=enriched_task,
                user=pipeline_user,
                websocket=_proxy,
                task_id=pipeline_task_id,
                conversation_id=conversation_id,
                chat_session_id=chat_session_id or "",
                images=images or [],
                session=session,
            )
        )

        # ── 3. Store on session so ws.py finally-block can detach/cancel ──
        session.pipeline_task = pipeline_task

        # ── 4. Listen for stop messages ────────────────────────────
        stopped = await self._listen_for_stop(pipeline_task, websocket)

        # ── 5. Stopped path ────────────────────────────────────────
        if stopped:
            await ws_transition(
                session, websocket, WorkspaceState.READY,
                "Task stopped. Ready for next instruction.",
            )
            try:
                await websocket.send_json({
                    "type": "status",
                    "status": "ready",
                    "message": "Task stopped. Ready for next instruction.",
                })
            except Exception:
                pass
            return TaskResult(stopped=True)

        # ── 6. Completion path ─────────────────────────────────────
        workspace_path: str | None = None
        try:
            workspace_path = pipeline_task.result()
            if workspace_path and session:
                session.workspace_dir = workspace_path
        except Exception:
            pass

        # Re-hydrate after completion — wizard pipeline creates the repo in
        # Phase 7, so session.repo_url may now be set for the first time.
        await self._hydrate_repo_url(session, pipeline_user, chat_session_id, user_jwt)

        summary = await self._send_completion(
            session=session,
            websocket=websocket,
            task=task or enriched_task,
            chat_session_id=chat_session_id,
            user_jwt=user_jwt,
        )
        return TaskResult(stopped=False, summary=summary, workspace_path=workspace_path)

    # ── Private helpers ───────────────────────────────────────

    async def _hydrate_repo_url(
        self,
        session: AgentSession,
        pipeline_user: dict | None,
        chat_session_id: str,
        user_jwt: str | None,
    ) -> None:
        """Hydrate session.repo_url from DB when it is empty (wizard projects).

        Mutates session.repo_url, session.git_token, session.branch, and
        pipeline_user in-place so the pipeline receives the correct repo URL.
        No-op when session.repo_url is already set.
        """
        if session.repo_url or not chat_session_id or not user_jwt:
            return
        try:
            async with db_client(user_jwt) as client:
                result = await (
                    client.table("chat_sessions")
                    .select("platform_repo_url")
                    .eq("id", chat_session_id)
                    .maybe_single()
                    .execute()
                )
            url = result.data and result.data.get("platform_repo_url")
            if not url:
                return
            session.repo_url = url
            session.git_token = session.git_token or PLATFORM_GITHUB_TOKEN or ""
            session.branch = session.branch or "main"
            logger.info("session.repo_url hydrated from chat_session: %s", url)

            # Update pipeline_user in-place so the pipeline gets the fresh URL
            if pipeline_user is not None:
                git_provider = "gitlab" if "gitlab" in url.lower() else "github"
                pipeline_user.update({
                    "git_provider": git_provider,
                    "github_repo": url,
                    "github_token": session.git_token,
                    "gitlab_repo": url,
                    "gitlab_token": session.git_token,
                    "git_token": session.git_token,
                })
        except Exception as exc:
            logger.warning("Failed to hydrate session.repo_url: %s", exc)

    async def _listen_for_stop(
        self,
        pipeline_task: asyncio.Task,
        websocket: Any,
    ) -> bool:
        """Listen for WS messages while the pipeline runs.

        Handles ping/pong heartbeats inline.  Returns True if the user sent
        a stop message (pipeline is already cancelled on return).
        """
        while not pipeline_task.done():
            msg_coro = None
            try:
                msg_coro = asyncio.ensure_future(websocket.receive_json())
                done_set, _ = await asyncio.wait(
                    {pipeline_task, msg_coro},
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if msg_coro in done_set:
                    data = msg_coro.result()
                    msg_type = data.get("type", "")
                    if msg_type == "ping":
                        try:
                            await websocket.send_json({"type": "pong"})
                        except Exception:
                            pass
                    elif msg_type in ("stop", "stop_task"):
                        pipeline_task.cancel()
                        try:
                            await websocket.send_json({
                                "type": "status",
                                "status": "stopping",
                                "message": "Stopping agent...",
                            })
                        except Exception:
                            pass
                        try:
                            await pipeline_task
                        except (asyncio.CancelledError, Exception):
                            pass
                        return True
                else:
                    # Pipeline finished — cancel the pending receive
                    msg_coro.cancel()
                    try:
                        await msg_coro
                    except (asyncio.CancelledError, Exception):
                        pass
                    msg_coro = None
                    break
            except Exception:
                break
            finally:
                # Always clean up msg_coro — prevents task leaks on exception
                if msg_coro is not None and not msg_coro.done():
                    msg_coro.cancel()
                    try:
                        await msg_coro
                    except (asyncio.CancelledError, Exception):
                        pass
        return False

    async def _send_completion(
        self,
        session: AgentSession,
        websocket: Any,
        task: str,
        chat_session_id: str | None,
        user_jwt: str | None,
    ) -> str:
        """Build summary, send finished event, persist to DB, transition state."""
        files_changed = await self._get_files_changed(session)
        last_msg = self._extract_last_agent_message(session)

        finish_summary: list[str] = []
        if last_msg:
            finish_summary.append(last_msg)
        if files_changed:
            finish_summary.append(f"\nChanged files:\n{files_changed}")
        summary_text = "\n".join(finish_summary) if finish_summary else "Task completed."

        try:
            await websocket.send_json({
                "type": "step", "step": "finished",
                "label": "Finished", "done": True,
                "summary": summary_text,
            })
        except Exception:
            pass

        if chat_session_id and summary_text and summary_text != "Task completed.":
            # Only persist when there is a real agent-authored summary.
            # The fallback "Task completed." string is not meaningful content —
            # skipping it prevents a generic bubble from showing in chat history
            # every time the user re-enters the workspace.
            try:
                await ChatService.add_message(
                    session_id=chat_session_id,
                    role="assistant",
                    content=summary_text,
                    event_type="AgentResponse",
                    user_jwt=user_jwt,
                )
            except Exception as exc:
                logger.warning("Failed to persist agent response: %s", exc)

        await self._update_session_summary(chat_session_id, task, session, user_jwt)

        await ws_transition(
            session, websocket, WorkspaceState.READY,
            "Task completed. Ready for next instruction.",
        )
        try:
            await websocket.send_json({
                "type": "status",
                "status": "ready",
                "message": "Task completed. Ready for next instruction.",
            })
        except Exception:
            pass

        return summary_text

    async def _update_session_summary(
        self,
        chat_session_id: str | None,
        task: str,
        session: AgentSession,
        user_jwt: str | None,
    ) -> None:
        """Build a structured handoff note and save to chat_sessions.

        The note captures task, files changed, and the last agent message so
        the next session immediately understands the conversation history.
        """
        if not chat_session_id or not user_jwt:
            return
        try:
            files_changed = await self._get_files_changed(session)
            last_agent_msg = self._extract_last_agent_message(session)

            # Sanitize: if the task is a wizard [LUCID_PROJECT] header, extract
            # only the human-readable description so it doesn't get re-detected as
            # a wizard task when injected as conversation context for future sessions.
            import re as _re_sum
            _task_for_summary = task or ""
            if "[LUCID_PROJECT]" in _task_for_summary[:300]:
                _m = _re_sum.search(r'description=([^|\n]+)', _task_for_summary)
                if _m:
                    _task_for_summary = _m.group(1).strip()

            parts = [f"Task: {_task_for_summary[:400]}"]
            if files_changed:
                parts.append(f"Files changed:\n{files_changed}")
            if last_agent_msg:
                parts.append(f"Result: {last_agent_msg[:600]}")

            summary = "\n\n".join(parts)
            async with db_client(user_jwt) as client:
                await (
                    client.table("chat_sessions")
                    .update({
                        "last_task": _task_for_summary[:500],
                        "summary": summary[:2000],
                    })
                    .eq("id", chat_session_id)
                    .execute()
                )
            logger.info("Session summary updated for %s", chat_session_id)
        except Exception as exc:
            logger.warning("Failed to update session summary: %s", exc)

    async def _get_files_changed(self, session: AgentSession) -> str:
        """Return a compact list of files changed in the workspace."""
        if not session.workspace_dir:
            return ""
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                ["git", "status", "--porcelain"],
                cwd=session.workspace_dir,
                capture_output=True, text=True, timeout=10,
            )
            status = result.stdout.strip()
            if not status:
                return ""

            files: list[str] = []
            for line in status.split("\n"):
                if not line.strip():
                    continue
                code = line[:2].strip()
                path = line[3:].strip()
                if code == "??":
                    files.append(f"  + {path} (new)")
                elif code in ("M", "MM", "AM"):
                    files.append(f"  ~ {path} (modified)")
                elif code == "A":
                    files.append(f"  + {path} (added)")
                elif code == "D":
                    files.append(f"  - {path} (deleted)")
                elif code == "R":
                    files.append(f"  → {path} (renamed)")
                else:
                    files.append(f"  {code} {path}")
            return "\n".join(files[:30])
        except Exception:
            return ""

    def _extract_last_agent_message(self, session: AgentSession) -> str:
        """Return the last meaningful agent message, cleaned for human display."""
        raw = getattr(session, "last_agent_message", "") or ""
        if not raw:
            return ""

        if raw.strip().startswith("{") and "message" in raw:
            try:
                import ast
                data = ast.literal_eval(raw.strip())
                if isinstance(data, dict) and "message" in data:
                    return data["message"]
            except (ValueError, SyntaxError):
                pass

        skip_prefixes = ("Running: `", "Viewing file:", "File:", "{'")
        if any(raw.startswith(p) for p in skip_prefixes):
            return ""

        return raw


# ── Module-level singleton ────────────────────────────────────

agent_orchestrator = AgentOrchestrator()
