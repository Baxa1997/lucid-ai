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

    pipeline_user = build_pipeline_user(session, api_key, pm, user_jwt)
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
import os
import subprocess
import time
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
    package_manager: str,
    user_jwt: str | None,
    openai_api_key: str = "",
    openai_model: str = "",
) -> dict:
    """Build the pipeline_user config dict from current session state.

    Called before execute_task so the dict is always fresh.  The orchestrator
    may mutate it in-place during _hydrate_repo_url if repo_url was empty.
    Gemini auth is now Vertex ADC inside gemini_post — no per-call key.
    """
    git_provider = (
        session.repo_provider
        or ("gitlab" if "gitlab" in (session.repo_url or "").lower() else "github")
    )
    return {
        "anthropic_api_key": api_key,
        "openai_api_key": openai_api_key or os.environ.get("OPENAI_API_KEY", ""),
        "openai_model": openai_model,
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


# ── In-flight status question detection + answering ─────────────────
#
# When a user types a follow-up while the pipeline is still running we
# default to queuing — safe for edits ("make the hero red"). But pure
# status questions ("what stage are you at?") deserve an answer NOW,
# not 4 minutes from now when the build finishes.
#
# The detector is intentionally conservative: only EXPLICIT status
# phrasings count as a question. Any ambiguity falls through to the
# queue so we never accidentally swallow an edit. False negatives (real
# question → queued) degrade to current behaviour, which is fine. False
# positives (real edit → answered + discarded) would silently lose user
# work, which is not.

_STATUS_QUESTION_PREFIXES = (
    "status", "where are you", "where you at",
    "what stage", "what step", "what phase",
    "what's happening", "what is happening", "what are you doing",
    "what're you doing",
    "are you done", "are you finished", "are you ready", "are you almost",
    "is it done", "is it ready", "is it finished", "is it almost",
    "how long", "how much longer", "how is it going", "how's it going",
    "any progress", "progress update", "give me an update",
)


def _is_status_question(text: str) -> bool:
    """Return True when ``text`` is an EXPLICIT status check.

    Conservative on purpose — only matches phrasings that are almost
    impossible to read as an edit instruction. See the module-level
    note above for the reasoning.
    """
    s = (text or "").strip().lower()
    if not s:
        return False
    # Long messages are nearly always edits with detailed instructions,
    # not status checks. The cap is generous (covers "what stage are you
    # at — i want to ask before changing the headline" which is still
    # primarily a question, but the question prefix wins early).
    if len(s) > 200:
        return False
    return s.startswith(_STATUS_QUESTION_PREFIXES)


def _inflight_status_enabled() -> bool:
    """Return whether explicit status questions are answered immediately.

    The detector is intentionally conservative, so this defaults ON: users can
    ask "what stage are you at?" while generation continues in the background.
    Set ``INFLIGHT_STATUS_ANSWER_ENABLED=0`` to force status questions into the
    normal queued-message path.
    """
    raw = os.environ.get("INFLIGHT_STATUS_ANSWER_ENABLED", "1").strip().lower()
    return raw in ("1", "true", "yes", "on")


# Strong-references for fire-and-forget asyncio Tasks (status-question
# answers) so the event loop doesn't GC them while they're still running.
# add_done_callback discards each one when it completes — no leak.
_INFLIGHT_QA_TASKS: set[asyncio.Task] = set()


def _track_qa_task(task: asyncio.Task) -> None:
    _INFLIGHT_QA_TASKS.add(task)
    task.add_done_callback(_INFLIGHT_QA_TASKS.discard)


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
        editable_target: dict | None = None,
        _is_drain_call: bool = False,
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
            editable_target:  Optional ``{path, type, file}`` payload from the
                              preview iframe's click-to-edit handler. When
                              present, the pipeline synthesizes an EditIntent
                              directly from this target and skips Step 3b's
                              vocab build — the user has already pointed at
                              the exact element they want changed.
            _is_drain_call:   Internal flag — when True, this call is being
                              made by the outer drain loop, so we skip the
                              drain block at the end. Prevents nested drain
                              recursion when many items are queued.

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
                editable_target=editable_target,
            )
        )

        # ── 3. Store on session so ws.py finally-block can detach/cancel ──
        session.pipeline_task = pipeline_task

        # ── 4. Listen for stop messages ────────────────────────────
        stopped = await self._listen_for_stop(
            pipeline_task, websocket, session, chat_session_id or "",
            user_jwt=user_jwt,
        )

        # ── 5. Stopped path ────────────────────────────────────────
        if stopped:
            # Stop nukes any tasks the user queued during this run. Two
            # reasons: (a) "Stop" should feel like "halt everything", not
            # "halt the current item and then plough into more work";
            # (b) we have no way to know whether the user still wants
            # those queued edits applied to a potentially half-done
            # workspace. Cheap to re-type when they do.
            if session.pending_tasks:
                logger.info(
                    "[%s] Stop received — discarding %d queued task(s)",
                    getattr(session, "session_id", "?"),
                    len(session.pending_tasks),
                )
                session.pending_tasks.clear()
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
        pipeline_error: Exception | None = None
        try:
            workspace_path = pipeline_task.result()
            if workspace_path and session:
                session.workspace_dir = workspace_path
        except Exception as exc:
            pipeline_error = exc
            logger.warning(
                "Pipeline task raised — suppressing completion summary: %s",
                exc,
            )

        # Re-hydrate after completion — wizard pipeline creates the repo in
        # Phase 7, so session.repo_url may now be set for the first time.
        await self._hydrate_repo_url(session, pipeline_user, chat_session_id, user_jwt)

        # If the pipeline raised (Anthropic credits depleted, network failure,
        # etc.), the error event has already been emitted from
        # project_generator.call_claude_for_json. Don't follow it with a
        # "Task completed" + Changed files block — the file list is from
        # earlier successful steps, not from the failed codegen, and showing
        # both is confusing ("Why does it say completed if it errored?").
        if pipeline_error is not None:
            # Pipeline blew up — clear the queue so we don't apply edits to
            # a half-done workspace. The user can resend after they see the
            # error in chat.
            if session.pending_tasks and not _is_drain_call:
                logger.info(
                    "[%s] Pipeline error — discarding %d queued task(s)",
                    getattr(session, "session_id", "?"),
                    len(session.pending_tasks),
                )
                session.pending_tasks.clear()
            await ws_transition(
                session, websocket, WorkspaceState.READY,
                "Ready for next instruction.",
            )
            try:
                await websocket.send_json({
                    "type": "status",
                    "status": "ready",
                    "message": "Ready for next instruction.",
                })
            except Exception:
                pass
            return TaskResult(stopped=False, summary="", workspace_path=workspace_path)

        summary = await self._send_completion(
            session=session,
            websocket=websocket,
            task=task or enriched_task,
            chat_session_id=chat_session_id,
            user_jwt=user_jwt,
        )

        # ── 7. Drain queued follow-ups ─────────────────────────────
        # Any messages the user sent while this pipeline was running
        # were appended to session.pending_tasks by _listen_for_stop.
        # Apply them now in FIFO order, each as its own pipeline run.
        # We pop one at a time so a NEW message arriving DURING a drained
        # turn lands at the tail and runs after current draining items.
        # Failures inside drained turns are logged but don't abort the
        # rest of the queue — the user explicitly asked for each one.
        #
        # Only the OUTERMOST execute_task runs this drain — recursive
        # calls from inside the drain loop pass ``_is_drain_call=True``
        # so we don't open nested drain loops as queue depth grows.
        if _is_drain_call:
            return TaskResult(stopped=False, summary=summary, workspace_path=workspace_path)

        while session.pending_tasks:
            try:
                entry = session.pending_tasks.pop(0)
            except IndexError:
                break
            queued_text = (entry.get("text") or "").strip()
            queued_images = entry.get("images") or []
            if not queued_text and queued_images:
                queued_text = (
                    f"Analyze the {len(queued_images)} attached image(s) "
                    "and implement any changes they suggest."
                )
            if not queued_text:
                continue
            queued_mode = entry.get("mode", "edit")
            queued_web_search = entry.get("web_search", True)
            try:
                await websocket.send_json({
                    "type": "progress",
                    "message": (
                        f"▶️  {'Answering queued question' if queued_mode == 'discuss' else 'Applying queued change'}: "
                        f"{queued_text[:80]}"
                    ),
                })
            except Exception:
                pass
            logger.info(
                "[%s] Draining queued task (%d left after this): %.60s",
                getattr(session, "session_id", "?"),
                len(session.pending_tasks), queued_text,
            )
            try:
                context_briefing = ""
                if user_jwt and getattr(session, "project_id", ""):
                    context_briefing = await build_conversation_context(
                        session.user_id,
                        session.project_id,
                        user_jwt,
                        current_task=queued_text,
                    )
                discuss_prefix = (
                    "[DISCUSS MODE — Analyze and explain only. "
                    "Do NOT write, edit, or delete any files. Just answer the question.]\n\n"
                    if queued_mode == "discuss" else ""
                )
                web_search_note = (
                    ""
                    if queued_web_search
                    else "\n\n[Web search disabled — use only existing codebase knowledge.]"
                )
                drained_task = f"{discuss_prefix}{queued_text}{web_search_note}"
                if context_briefing:
                    drained_task = f"{context_briefing}\n\nCURRENT TASK: {drained_task}"

                _drained_result = await self.execute_task(
                    enriched_task=drained_task,
                    session=session,
                    websocket=websocket,
                    pipeline_user=pipeline_user,
                    chat_session_id=chat_session_id,
                    conversation_id=conversation_id,
                    user_jwt=user_jwt,
                    task=queued_text,
                    images=queued_images,
                    editable_target=entry.get("editable_target"),
                    _is_drain_call=True,
                )
            except Exception as _drain_err:
                logger.error(
                    "[%s] Queued task failed (continuing with rest): %s",
                    getattr(session, "session_id", "?"), _drain_err,
                    exc_info=True,
                )
                try:
                    await websocket.send_json({
                        "type": "warning",
                        "message": "A queued change failed — moving on to the next one.",
                    })
                except Exception:
                    pass
                continue

            # User clicked Stop during this queued task → halt the whole
            # drain (the inner execute_task already cleared pending_tasks
            # on its stop path, but we re-check defensively so we exit
            # the loop cleanly without surprise behaviour on re-entry).
            if _drained_result.stopped:
                logger.info(
                    "[%s] Stop received during drain — halting remaining queue",
                    getattr(session, "session_id", "?"),
                )
                session.pending_tasks.clear()
                break

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
                git_provider = (
                    session.repo_provider
                    or ("gitlab" if "gitlab" in url.lower() else "github")
                )
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
        session: AgentSession,
        chat_session_id: str = "",
        user_jwt: str | None = None,
    ) -> bool:
        """Listen for WS messages while the pipeline runs.

        Handles ping/pong heartbeats inline.  Returns True if the user sent
        a stop message (pipeline is already cancelled on return).

        Stop handling is:
          1. Always ACK receipt (``stop_ack``) — frontend can clear "stopping" UI.
          2. Idempotent — if pipeline is already done, ACK and return without re-cancel.
          3. Hard timeout (10s) on ``await pipeline_task`` — if the pipeline
             does not respect ``CancelledError`` (e.g. blocked in a Docker exec),
             force-kill the sandbox so the next ``exec_command`` fails fast.
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
                    elif msg_type in ("plan_confirm", "plan_reject"):
                        # The pipeline coroutine is parked on
                        # ``asyncio.wait_for(_plan_future, ...)``. The ws.py
                        # message loop that handles plan_confirm sits AFTER
                        # ``await execute_task(...)`` and is unreachable while
                        # the pipeline runs, so we must resolve the Future
                        # here — otherwise the message is read off the socket
                        # and silently discarded, the gate hangs for 30 min,
                        # and the user sees a frozen "researching" UI.
                        try:
                            from app.services.plan_store import (
                                resolve_plan_confirmation, _confirmation_key,
                                pending_plan_confirmations, clear_persisted_plan,
                            )
                            # Mirror the keying used by the gate registration
                            # (project_generator.py:6558) — chat_session_id
                            # if present, else the proxy/socket id. Producer
                            # side passes ``websocket=session.ws_proxy or
                            # websocket`` so we use the same fallback here.
                            _key_ws = session.ws_proxy or websocket
                            _key = _confirmation_key(_key_ws, chat_session_id or "")
                            if _key in pending_plan_confirmations:
                                if msg_type == "plan_confirm":
                                    logger.info(
                                        "[%s] Plan confirmed by user (via _listen_for_stop)",
                                        getattr(session, "session_id", "?"),
                                    )
                                    resolve_plan_confirmation(_key, {"confirmed": True})
                                else:
                                    correction = data.get("correction", "")
                                    logger.info(
                                        "[%s] Plan rejected by user (via _listen_for_stop) — correction: %s",
                                        getattr(session, "session_id", "?"),
                                        correction[:80],
                                    )
                                    resolve_plan_confirmation(_key, {
                                        "confirmed": False,
                                        "correction": correction,
                                    })
                            else:
                                logger.warning(
                                    "[%s] %s received but no pending Future for key=%s",
                                    getattr(session, "session_id", "?"),
                                    msg_type, _key,
                                )
                                if chat_session_id:
                                    await clear_persisted_plan(chat_session_id)
                                try:
                                    await websocket.send_json({
                                        "type": "warning",
                                        "message": (
                                            "This plan can no longer be resumed "
                                            "(server restarted or plan expired). "
                                            "Please send your task again."
                                        ),
                                    })
                                except Exception:
                                    pass
                        except Exception as _plan_err:
                            logger.error(
                                "Failed to handle %s in _listen_for_stop: %s",
                                msg_type, _plan_err, exc_info=True,
                            )
                    elif msg_type in ("message", "task", "chat_message", "user_message"):
                        # User sent a new prompt while the pipeline is still
                        # running. ``"message"`` is the type the existing
                        # frontend sends (see useAgentSession.js); the other
                        # aliases cover future / alternate clients. Two paths:
                        #   • Status question → answer NOW, in parallel, do
                        #     NOT touch the running pipeline.
                        #   • Anything else → queue + ack so execute_task's
                        #     completion path drains it after the current
                        #     build finishes.
                        # The detector is conservative — only EXPLICIT
                        # status phrasings go to the answer path. Ambiguous
                        # input always falls through to the safe queue so a
                        # mis-classified edit never gets silently discarded.
                        try:
                            text = (
                                data.get("task")
                                or data.get("content")
                                or data.get("message")
                                or ""
                            ).strip()
                            images = data.get("images") or []
                            editable_tgt = data.get("editable_target")
                            queued_mode = data.get("mode", "edit")
                            queued_web_search = data.get("web_search", True)
                            # Empty text AND no images AND no editable_target
                            # → genuinely empty message; drop silently.
                            if not text and not images and not editable_tgt:
                                continue

                            out_ws = session.ws_proxy or websocket
                            if chat_session_id:
                                try:
                                    user_content = text
                                    if not user_content and images:
                                        user_content = f"[{len(images)} image(s) attached]"
                                    if not user_content and editable_tgt:
                                        user_content = "[Selected preview element]"
                                    await ChatService.add_message(
                                        session_id=chat_session_id,
                                        role="user",
                                        content=user_content,
                                        event_type="UserTask",
                                        user_jwt=user_jwt,
                                    )
                                except Exception as persist_exc:
                                    logger.warning(
                                        "[%s] Failed to persist in-flight user message: %s",
                                        getattr(session, "session_id", "?"),
                                        persist_exc,
                                    )

                            if text and _is_status_question(text):
                                if _inflight_status_enabled():
                                    logger.info(
                                        "[%s] In-flight status question — answering "
                                        "in parallel: %.60s",
                                        getattr(session, "session_id", "?"),
                                        text,
                                    )
                                    # Save a strong reference so the event loop
                                    # doesn't GC the fire-and-forget task while
                                    # Gemini is still answering. _track_qa_task
                                    # removes it on completion.
                                    _qa_task = asyncio.create_task(
                                        self._answer_in_flight_question(
                                            session, out_ws, text,
                                        )
                                    )
                                    _track_qa_task(_qa_task)
                                else:
                                    try:
                                        await out_ws.send_json({
                                            "type": "chat_message",
                                            "role": "agent",
                                            "content": (
                                                "I'm still working on the current build. "
                                                "I'll apply queued edits right after it finishes."
                                            ),
                                        })
                                    except Exception:
                                        pass
                                continue

                            try:
                                from app.services.followup_intent import classify_followup_message

                                guard = classify_followup_message(
                                    text,
                                    mode=queued_mode,
                                    has_images=bool(images),
                                    editable_target=editable_tgt,
                                )
                            except Exception as guard_exc:
                                logger.warning(
                                    "[%s] In-flight follow-up guard failed: %s — queueing",
                                    getattr(session, "session_id", "?"),
                                    guard_exc,
                                )
                                guard = {"action": "proceed"}

                            guard_action = guard.get("action", "proceed")
                            if guard_action in ("reply", "clarify"):
                                try:
                                    await out_ws.send_json({
                                        "type": "chat_message",
                                        "role": "agent",
                                        "content": guard.get("message") or (
                                            "What would you like to change or ask about this project?"
                                        ),
                                    })
                                    # Same reason as ws.py — the guard
                                    # short-circuits the pipeline and the
                                    # FE's optimistic state=running must
                                    # be released so "Analyzing…" clears.
                                    await out_ws.send_json({"type": "status", "status": "ready"})
                                except Exception:
                                    pass
                                continue

                            if guard_action == "discuss":
                                queued_mode = "discuss"

                            queued_entry = {
                                "text": text,
                                "images": images,
                                "editable_target": editable_tgt,
                                "mode": queued_mode,
                                "web_search": queued_web_search,
                                "queued_at": time.time(),
                            }
                            session.pending_tasks.append(queued_entry)
                            queued_count = len(session.pending_tasks)
                            logger.info(
                                "[%s] Queued in-flight message (#%d) — %.60s",
                                getattr(session, "session_id", "?"),
                                queued_count, text or "(images only)",
                            )
                            try:
                                await out_ws.send_json({
                                    "type": "progress",
                                    "message": (
                                        f"📥 Queued — I'll "
                                        f"{'answer that' if queued_mode == 'discuss' else 'apply that'} "
                                        f"right after the current build finishes "
                                        f"({queued_count} pending)."
                                    ),
                                })
                            except Exception:
                                pass
                        except Exception as _enq_err:
                            logger.warning(
                                "[%s] Failed to enqueue in-flight task: %s",
                                getattr(session, "session_id", "?"), _enq_err,
                            )
                    elif msg_type in ("stop", "stop_task"):
                        # P0 #1 — always ACK the stop message on receipt
                        try:
                            await websocket.send_json({"type": "stop_ack"})
                        except Exception:
                            pass

                        # P0 #4 — idempotent: if pipeline finished during the
                        # race with receive_json, don't try to cancel a done task
                        if pipeline_task.done():
                            return True

                        pipeline_task.cancel()
                        try:
                            await websocket.send_json({
                                "type": "status",
                                "status": "stopping",
                                "message": "Stopping agent...",
                            })
                        except Exception:
                            pass

                        # P0 #2 — hard timeout on cancel-wait
                        try:
                            await asyncio.wait_for(pipeline_task, timeout=10.0)
                        except asyncio.TimeoutError:
                            # P0 #3 — pipeline didn't respect CancelledError
                            # (likely blocked in a Docker exec). Force-kill the
                            # sandbox; any in-flight exec_command will return
                            # an error and the pipeline can unwind.
                            logger.warning(
                                "Pipeline did not cancel within 10s — "
                                "force-killing sandbox for session %s",
                                getattr(session, "session_id", "?"),
                            )
                            try:
                                if session.sandbox_runner is not None:
                                    await session.sandbox_runner.teardown()
                            except Exception as exc:
                                logger.error(
                                    "Force-kill sandbox failed: %s", exc
                                )
                            # Short grace period for the pipeline to unwind
                            # after the container is gone.
                            try:
                                await asyncio.wait_for(pipeline_task, timeout=5.0)
                            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                                pass
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

    async def _answer_in_flight_question(
        self,
        session: AgentSession,
        websocket: Any,
        question_text: str,
    ) -> None:
        """Reply to a status question without touching the running pipeline.

        Fire-and-forget — the caller spawns this via ``asyncio.create_task``
        so the listen loop keeps consuming messages while the reply lands.
        Reads lightweight state from the session (workspace_state, current
        task, files-written-so-far) and asks Gemini Flash to phrase a
        1-2 sentence answer. Failures are logged + swallowed; the worst
        case is the user doesn't get an answer (better than crashing the
        listen loop).
        """
        try:
            # Gather what we know about the in-flight task. None of this
            # blocks — pure local reads.
            state = str(getattr(session, "workspace_state", "") or "")
            current_task = (getattr(session, "task", "") or "")[:240]
            ws_dir = getattr(session, "workspace_dir", "") or ""

            files_so_far: list[str] = []
            if ws_dir and os.path.isdir(ws_dir):
                _skip = {"node_modules", ".git", ".next", "dist", "build", "__pycache__"}
                try:
                    for root, dirs, files in os.walk(ws_dir):
                        dirs[:] = [d for d in dirs if d not in _skip]
                        for f in files:
                            rel = os.path.relpath(os.path.join(root, f), ws_dir)
                            files_so_far.append(rel)
                            if len(files_so_far) >= 40:
                                break
                        if len(files_so_far) >= 40:
                            break
                except Exception:
                    files_so_far = []

            files_summary = (
                f"{len(files_so_far)} files written so far"
                + (f" (e.g. {', '.join(files_so_far[:5])})" if files_so_far else "")
            )

            prompt = (
                "The user has asked a status question while an agent task is "
                "still running. Answer in 1-2 short sentences, conversational, "
                "no markdown. Do NOT promise to do anything — the user knows "
                "you're already working on something else. Only state what's "
                "true right now.\n\n"
                f"User question: {question_text}\n\n"
                f"Current state:\n"
                f"- Active task: {current_task or '(unknown)'}\n"
                f"- Workspace state: {state or '(starting)'}\n"
                f"- Progress: {files_summary}\n"
            )

            reply: str = ""
            try:
                from app.services.landing_gemini import structured_distill
                raw = await structured_distill(
                    prompt, 12.0, label="inflight_status",
                    response_schema=None, max_tokens=180,
                    temperature=0.3, model="gemini-3.5-flash",
                )
                reply = (raw or "").strip()
                # structured_distill returns JSON-mode text — strip wrapping
                # quotes / braces if Gemini stuffed the reply into a value.
                if reply.startswith('"') and reply.endswith('"'):
                    reply = reply[1:-1]
                elif reply.startswith("{") and reply.endswith("}"):
                    # Tiny attempt to pull a "reply"/"answer" field out.
                    import json as _json
                    try:
                        obj = _json.loads(reply)
                        if isinstance(obj, dict):
                            for k in ("reply", "answer", "message", "text"):
                                if isinstance(obj.get(k), str):
                                    reply = obj[k].strip()
                                    break
                    except Exception:
                        pass
            except Exception as exc:
                logger.warning("inflight Q&A: Gemini failed (%s) — using fallback", exc)

            if not reply:
                # Deterministic fallback — never leave the user hanging.
                reply = (
                    f"Still working — currently at: {state or 'preparing workspace'}. "
                    f"{files_summary}. I'll send the result when it's done."
                )

            try:
                await websocket.send_json({
                    "type": "chat_message",
                    "role": "agent",
                    "content": reply,
                    "messageType": "status_reply",
                })
            except Exception as exc:
                logger.warning("inflight Q&A: send failed: %s", exc)
        except Exception as exc:
            logger.warning("inflight Q&A: unexpected error: %s", exc)

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

        # Live summary (sent to the open websocket) keeps the file list so the
        # user can see what changed in real time. Persisted summary (saved to
        # chat history) drops the file list — re-entering the workspace was
        # showing a huge wall of paths next to every past task, which is noise
        # the side file tree already covers.
        live_parts: list[str] = []
        if last_msg:
            live_parts.append(last_msg)
        if files_changed:
            live_parts.append(f"\nChanged files:\n{files_changed}")
        live_summary = "\n".join(live_parts) if live_parts else "Task completed."

        chat_summary = (last_msg or "").strip()

        try:
            await websocket.send_json({
                "type": "step", "step": "finished",
                "label": "Finished", "done": True,
                "summary": live_summary,
            })
        except Exception:
            pass

        if chat_session_id and chat_summary:
            # Only persist when there is a real agent-authored message. The
            # fallback "Task completed." string is not meaningful content, and
            # the bare file-change list (no agent prose) is just noise — both
            # are skipped so chat history stays readable on re-entry.
            try:
                await ChatService.add_message(
                    session_id=chat_session_id,
                    role="assistant",
                    content=chat_summary,
                    event_type="AgentResponse",
                    user_jwt=user_jwt,
                )
            except Exception as exc:
                logger.warning("Failed to persist agent response: %s", exc)

        summary_text = live_summary

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
