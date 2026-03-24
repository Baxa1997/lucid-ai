"""WebSocket endpoint for real-time agent communication."""

from __future__ import annotations

import asyncio
import os
import uuid
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException

from app.auth import AuthenticatedUser, authenticate_websocket, authenticate_from_handshake
from app.config import (
    logger,
    settings,
    WS_INIT_TIMEOUT_SECONDS,
    MOCK_STEP_DELAY_SECONDS,
    CONVERSATION_TIMEOUT_SECONDS,
)
from app import sdk
from app.events import now_iso, stream_events_to_ws
from app.services.chat import ChatService
from app.services.sessions import (
    AgentSession,
    create_session,
    destroy_session,
    store as session_store,
)
from app.services.git_operations import push_changes, get_git_status
from app.services.task_pipeline import run_pipeline
from app.services.workspace_manager import workspace_manager
from app.supabase_client import db_client

router = APIRouter()


# ── Context replay helper ────────────────────────────────────

async def _build_conversation_context(
    user_id: str,
    project_id: str,
    user_jwt: str,
    max_messages: int = 20,
) -> str:
    """Load recent messages from DB to rebuild agent context.

    Retrieves the most recent chat session for this user+project
    and builds a summary of past work so the agent isn't starting
    from scratch.
    """
    try:
        # Find the most recent chat session for this project
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
            # summary/last_task columns may not exist yet — fall back
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

        # If we have a stored handoff note, use it (fast path)
        if prev_session.get("summary"):
            return (
                "## What happened in the previous session\n\n"
                f"{prev_session['summary']}\n\n"
                "Use this context to understand what was already done. "
                "Do not repeat completed work."
            )

        # Otherwise, load the last N messages (slow path)
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

        # Build a compact summary of past conversation
        messages = list(reversed(msgs.data))
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
            elif role == "assistant" and event_type in (
                "MessageEvent", "ActionEvent",
            ):
                # Only include meaningful agent messages, skip tool noise
                if len(content) > 30:
                    lines.append(f"**Agent:** {content[:300]}")

        if len(lines) <= 1:
            return ""  # No meaningful messages found

        return "\n".join(lines)

    except Exception as exc:
        logger.warning("Failed to load conversation context: %s", exc)
        return ""


def _build_agent_guidelines() -> str:
    """Return comprehensive operating guidelines for the agent."""
    return ""


@router.websocket("/api/v1/ws")
async def websocket_agent(websocket: WebSocket):
    """Real-time agent communication channel.

    Protocol
    --------
    1. Client sends initial config ``{ "task": "...", ... }``
    2. Server creates a session and streams agent events back
    3. Client may send follow-ups ``{ "type": "message", "content": "..." }``
    4. On disconnect the workspace is cleaned up
    """
    await websocket.accept()
    logger.info("WebSocket connection accepted")

    # Authenticate from query param (if present)
    ws_user: Optional[AuthenticatedUser] = await authenticate_websocket(websocket)
    if ws_user is None and websocket.client_state.name == "DISCONNECTED":
        return  # closed by authenticate_websocket due to invalid token

    session: Optional[AgentSession] = None
    streaming_task: Optional[asyncio.Task] = None
    pipeline_task: Optional[asyncio.Task] = None
    chat_session_id: Optional[str] = None
    conversation_id: str = str(uuid.uuid4())  # unique per WS connection

    try:
        # ── 1. Receive initial config ────────────────────
        raw = await asyncio.wait_for(
            websocket.receive_json(), timeout=WS_INIT_TIMEOUT_SECONDS,
        )

        # If not authenticated from query param, try handshake token
        if ws_user is None:
            ws_user = authenticate_from_handshake(raw)

        # ── Reject unauthenticated connections ────────────
        if ws_user is None:
            logger.warning("WebSocket rejected — no valid authentication")
            await websocket.send_json({
                "type": "error",
                "message": "Authentication required. Provide a valid JWT token.",
            })
            await websocket.close(code=4010, reason="Authentication required")
            return

        task = raw.get("task", "")  # Task is now OPTIONAL in handshake

        await websocket.send_json({
            "type": "status",
            "status": "initializing",
            "message": "Setting up agent workspace...",
        })

        user_id = ws_user.user_id
        user_jwt = ws_user.raw_jwt
        project_id = raw.get("projectId", "")
        explicit_stop = False  # track if user explicitly stopped

        # ── 1.5 Resolve LLM Settings (Handshake > Supabase > Default) ──
        model_provider = raw.get("modelProvider") or raw.get("model_provider")
        api_key = raw.get("apiKey") or raw.get("api_key")

        if not model_provider or not api_key:
            try:
                async with db_client(user_jwt) as client:
                    q = client.table("user_settings").select("*").eq("user_id", user_id).maybe_single()
                    res = await q.execute()
                    if res.data:
                        logger.info("Applying saved LLM settings for user %s", user_id)
                        if not model_provider:
                            model_provider = res.data.get("llm_model")
                        if not api_key:
                            enc = res.data.get("api_key_enc")
                            iv = res.data.get("api_key_iv")
                            if enc and iv:
                                from app.utils.crypto import decrypt_api_key
                                try:
                                    api_key = decrypt_api_key(enc, iv)
                                except Exception as dec_err:
                                    logger.error("Failed to decrypt API key: %s", dec_err)
            except Exception as db_err:
                logger.warning("Failed to fetch user settings from Supabase: %s", db_err)

        if not model_provider:
            model_provider = settings.DEFAULT_PROVIDER

        # Fallback: if no API key from handshake or user settings, use server's .env key
        if not api_key:
            api_key = os.environ.get("ANTHROPIC_API_KEY") or settings.ANTHROPIC_API_KEY or ""
            if api_key:
                logger.info("Using server fallback ANTHROPIC_API_KEY for user %s", user_id)
        
        logger.info("[%s] Using model: %s, api_key prefix: %s (len=%d)", project_id or "new-session", model_provider, str(api_key or "")[:15], len(str(api_key or "")))

        # Resolve Gemini API key for pre-exploration
        gemini_api_key = os.environ.get("GOOGLE_API_KEY") or settings.GOOGLE_API_KEY or ""

        # ── 2. Try to reconnect to existing session ───────
        existing = await session_store.find_by_user_and_project(user_id, project_id) if project_id else None

        if existing:
            session = existing
            session.touch()

            # ── Refresh session with latest handshake data ─────
            # Always update from the fresh handshake to fix stale sessions
            # that may have empty or wrong repo_url/git_token/branch.
            fresh_repo_url = raw.get("repoUrl", "")
            fresh_git_token = raw.get("gitToken", "")
            fresh_branch = raw.get("branch", "")

            if fresh_repo_url:
                session.repo_url = fresh_repo_url
                logger.info("Session repo_url refreshed → %s", fresh_repo_url[:60])
            if fresh_git_token:
                session.git_token = fresh_git_token
                logger.info("Session git_token refreshed (len=%d)", len(fresh_git_token))
            if fresh_branch:
                session.branch = fresh_branch
                logger.info("Session branch refreshed → %s", fresh_branch)

            logger.info("Reconnecting to existing session %s for project %s", session.session_id, project_id)
            await websocket.send_json({
                "type": "status",
                "status": "ready",
                "sessionId": session.session_id,
                "reconnected": True,
                "message": "Reconnected to existing workspace.",
            })
        else:
            # ── Create new session (no clone — workspace_manager handles it) ──
            try:
                # Resolve git_token — frontend may send it, or extract from JWT
                git_token = raw.get("gitToken", "")
                if not git_token and user_jwt:
                    # Extract git token from JWT user_metadata as fallback
                    try:
                        import jwt as pyjwt
                        decoded = pyjwt.decode(user_jwt, options={"verify_signature": False})
                        user_meta = decoded.get("user_metadata", {})
                        repo_url_raw = raw.get("repoUrl", "")
                        if "github" in repo_url_raw.lower():
                            gh = user_meta.get("github_integration", {})
                            git_token = gh.get("token", "")
                        elif "gitlab" in repo_url_raw.lower():
                            gl = user_meta.get("gitlab_integration", {})
                            git_token = gl.get("token", "")
                        if git_token:
                            logger.info("git_token extracted from JWT (len=%d)", len(git_token))
                    except Exception as jwt_err:
                        logger.warning("Failed to extract git_token from JWT: %s", jwt_err)

                session = await create_session(
                    task=task or "Workspace initialization",
                    user_id=user_id,
                    repo_url=raw.get("repoUrl", ""),
                    git_token=git_token,
                    branch=raw.get("branch", ""),
                    git_user_name=raw.get("gitUserName", ""),
                    git_user_email=raw.get("gitUserEmail", ""),
                    model_provider=model_provider,
                    api_key=api_key or "",
                    project_id=project_id,
                )
            except HTTPException as rate_err:
                # Rate limit or other HTTP error from create_session
                logger.warning("Session creation rejected: %s", rate_err.detail)
                await websocket.send_json({
                    "type": "error",
                    "message": rate_err.detail,
                })
                await websocket.close(code=4029, reason="Rate limited")
                return

        user_jwt = ws_user.raw_jwt

        # ── Persist chat session to DB (new sessions only) ──
        # On reconnect we skip this — a chat session already exists for this
        # agent session and creating another would leave orphan records.
        if not existing:
            try:
                chat_sess = await ChatService.create_session(
                    user_id=user_id,
                    user_jwt=user_jwt,
                    agent_session_id=session.session_id,
                    project_id=raw.get("projectId"),
                    title=task[:255] if task else "New workspace session",
                    model_provider=model_provider,
                )
                chat_session_id = chat_sess["id"]
                logger.info("Chat session %s created for user %s", chat_session_id, user_id)
            except Exception as exc:
                logger.warning("Failed to create chat session in DB: %s", exc)
        else:
            # On reconnect — look up the existing chat session_id so
            # follow-up messages are still persisted after the reconnect.
            try:
                async with db_client(ws_user.raw_jwt) as client:
                    # First try by agent_session_id
                    result = await (
                        client.table("chat_sessions")
                        .select("id")
                        .eq("user_id", user_id)
                        .eq("agent_session_id", session.session_id)
                        .order("created_at", desc=True)
                        .limit(1)
                        .execute()
                    )
                if result.data:
                    chat_session_id = result.data[0]["id"]
                    logger.info("Reconnected to existing chat session %s", chat_session_id)
                elif project_id:
                    # Fallback: find by project_id
                    async with db_client(ws_user.raw_jwt) as client:
                        result = await (
                            client.table("chat_sessions")
                            .select("id")
                            .eq("user_id", user_id)
                            .eq("project_id", project_id)
                            .order("created_at", desc=True)
                            .limit(1)
                            .execute()
                        )
                    if result.data:
                        chat_session_id = result.data[0]["id"]
                        logger.info("Found chat session %s by project_id %s", chat_session_id, project_id)
            except Exception as exc:
                logger.warning("Failed to look up existing chat session on reconnect: %s", exc)

        # ── 3. Pipeline handles agent execution ──────────
        # NOTE: The old mock gate (sdk.OPENHANDS_AVAILABLE) is removed.
        # run_pipeline() has its own subprocess fallbacks for clone/push
        # and does NOT require the OpenHands SDK.
        if not sdk.OPENHANDS_AVAILABLE:
            logger.info("OpenHands SDK not installed — pipeline will use subprocess fallbacks")

        # ── 4. Session ready — workspace initialized ─────────
        # Only send the "ready" message for NEW sessions.
        # For reconnects we already sent it at step 2 above (reconnected=True).
        if not existing:
            ready_msg = "Workspace ready. You can start giving tasks."
            if session.repo_url:
                ready_msg = (
                    f"Workspace initialized. "
                    f"The agent has terminal, file editor, and browser tools available. "
                    f"You can start giving tasks."
                )
            await websocket.send_json({
                "type": "status",
                "status": "ready",
                "sessionId": session.session_id,
                "message": ready_msg,
            })

        # NOTE: stream_events_to_ws removed — OpenHands Conversation no longer
        # created. Claude Code SDK sends events directly via WebSocket.
        streaming_task = None

        # ── If task was included in handshake, run it immediately ─
        if task and not existing:
            # ── Step 1: Save user task to DB ──────────────────
            if chat_session_id:
                try:
                    await ChatService.add_message(
                        session_id=chat_session_id, role="user",
                        content=task, event_type="UserTask",
                        user_jwt=user_jwt,
                    )
                except Exception as exc:
                    logger.warning("Failed to persist user message: %s", exc)

            # ── Step: Got Task ────────────────────────────────
            await websocket.send_json({
                "type": "step", "step": "got_task",
                "label": "Got task", "done": True,
            })
            await websocket.send_json({
                "type": "agent_event", "event": "task_start",
                "content": f"Agent starting task: {task}",
            })

            # ── Step: Understanding ───────────────────────────
            # (Cloning step was already sent during create_session)
            repo_ctx = getattr(session, "repo_context", "")

            await websocket.send_json({
                "type": "step", "step": "understanding",
                "label": "Understanding the project", "done": True,
            })

            # Build the enriched message
            enriched_task = task
            context_parts = []

            if project_id and user_jwt:
                prev_context = await _build_conversation_context(
                    user_id, project_id, user_jwt
                )
                if prev_context:
                    context_parts.append(prev_context)

            if repo_ctx:
                context_parts.append(
                    f"I have cloned the repository into the workspace directory. "
                    f"Here is the project layout and key configuration files:\n\n"
                    f"{repo_ctx}"
                )

            if context_parts:
                enriched_task = (
                    "\n\n---\n\n".join(context_parts)
                    + f"\n\n---\n\n"
                    f"Now, here is my task:\n{task}\n"
                    f"{_build_agent_guidelines()}"
                )
            else:
                enriched_task = f"{task}{_build_agent_guidelines()}"

            # ── Step: Working — run full pipeline (cancellable) ──
            # Note: initial task comes from WS query params, no images possible
            initial_images = []
            pipeline_task_id = str(uuid.uuid4())[:8]
            pipeline_user = {
                "anthropic_api_key": api_key,
                "gemini_api_key": gemini_api_key,
                "git_provider": "gitlab" if "gitlab" in (session.repo_url or "").lower() else "github",
                "github_repo": session.repo_url or "",
                "github_token": session.git_token or "",
                "gitlab_repo": session.repo_url or "",
                "gitlab_token": session.git_token or "",
                "selected_branch": session.branch or "main",
                "git_token": session.git_token or "",
            }

            # Run pipeline as a cancellable task so stop messages work
            pipeline_task = asyncio.create_task(
                run_pipeline(
                    task=enriched_task,
                    user=pipeline_user,
                    websocket=websocket,
                    task_id=pipeline_task_id,
                    conversation_id=conversation_id,
                    images=initial_images,
                )
            )

            # Listen for stop messages while pipeline runs
            pipeline_stopped = False
            while not pipeline_task.done():
                try:
                    # Wait for either pipeline completion or a WS message
                    msg_coro = asyncio.ensure_future(websocket.receive_json())
                    done, pending = await asyncio.wait(
                        {pipeline_task, msg_coro},
                        return_when=asyncio.FIRST_COMPLETED,
                    )

                    if msg_coro in done:
                        data = msg_coro.result()
                        msg_type = data.get("type", "message")
                        if msg_type == "ping":
                            await websocket.send_json({"type": "pong"})
                        elif msg_type in ("stop", "stop_task"):
                            explicit_stop = True
                            pipeline_stopped = True
                            pipeline_task.cancel()
                            await websocket.send_json({
                                "type": "status",
                                "status": "stopping",
                                "message": "Stopping agent...",
                            })
                            try:
                                await pipeline_task
                            except (asyncio.CancelledError, Exception):
                                pass
                            break
                    else:
                        # Pipeline finished, cancel the pending receive
                        msg_coro.cancel()
                        try:
                            await msg_coro
                        except (asyncio.CancelledError, Exception):
                            pass
                        break
                except Exception:
                    break

            if pipeline_stopped:
                await websocket.send_json({
                    "type": "status",
                    "status": "ready",
                    "message": "Task stopped. Ready for next instruction.",
                })
            else:
                # Pipeline completed normally — update session workspace_dir
                # to the workspace manager path (where Claude actually worked)
                try:
                    result_path = pipeline_task.result()
                    if result_path and session:
                        session.workspace_dir = result_path
                except Exception:
                    pass

                # Build summary
                files_changed = await _get_files_changed(session)
                last_msg = _extract_last_agent_message(session)

                finish_summary = []
                if last_msg:
                    finish_summary.append(last_msg)
                if files_changed:
                    finish_summary.append(f"\nChanged files:\n{files_changed}")

                summary_text = "\n".join(finish_summary) if finish_summary else "Task completed."

                await websocket.send_json({
                    "type": "step", "step": "finished",
                    "label": "Finished", "done": True,
                    "summary": summary_text,
                })

                # ── Save structured agent response to DB ──────────
                if chat_session_id:
                    try:
                        await ChatService.add_message(
                            session_id=chat_session_id, role="assistant",
                            content=summary_text,
                            event_type="AgentResponse",
                            user_jwt=user_jwt,
                        )
                    except Exception as exc:
                        logger.warning("Failed to persist agent response: %s", exc)

                # Save task summary for future context replay
                await _update_session_summary(
                    chat_session_id, task, session, user_jwt
                )

                await websocket.send_json({
                    "type": "status",
                    "status": "ready",
                    "message": "Task completed. Ready for next instruction.",
                })

        # ── 5. Follow-up loop ────────────────────────────
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type", "message")
            content = data.get("content", "")
            followup_images = data.get("images", [])

            # Heartbeat ping — respond and continue
            if msg_type == "ping":
                await websocket.send_json({"type": "pong"})
                continue

            # Stop must be checked BEFORE empty-content guard
            # because stop messages typically have no content field
            if msg_type in ("stop", "stop_task"):
                explicit_stop = True
                await websocket.send_json({
                    "type": "status",
                    "status": "stopping",
                    "message": "Stopping agent...",
                })
                break

            if not content and not followup_images:
                # Skip truly empty messages (no text AND no images)
                continue

            # Image-only messages need a default task description
            if not content and followup_images:
                content = f"Analyze the {len(followup_images)} attached image(s) and implement any changes they suggest."

            if msg_type == "push":
                # Explicit push request from client
                new_branch = data.get("newBranch")
                await _auto_push_if_needed(
                    websocket, session, 
                    commit_message=content or "Manual push by user",
                    new_branch=new_branch
                )
                continue

            logger.info("[%s] Follow-up: %s", session.session_id, content[:80])

            # ── Save follow-up user message to DB ─────────────
            if chat_session_id:
                try:
                    await ChatService.add_message(
                        session_id=chat_session_id, role="user",
                        content=content, event_type="UserTask",
                        user_jwt=user_jwt,
                    )
                except Exception as exc:
                    logger.warning("Failed to persist follow-up message: %s", exc)

            # ── Step: Got Task ────────────────────────────────
            await websocket.send_json({
                "type": "step", "step": "got_task",
                "label": "Got task", "done": True,
            })
            await websocket.send_json({
                "type": "agent_event", "event": "task_start",
                "content": f"Processing: {content[:80]}...",
            })

            # ── Step: Understanding ───────────────────────────
            await websocket.send_json({
                "type": "step", "step": "understanding",
                "label": "Understanding the project", "done": True,
            })

            # Rebuild context if this is a reconnection or a project with history
            context_briefing = await _build_conversation_context(user_id, project_id, user_jwt)
            image_note = f"\n\n[User attached {len(followup_images)} image(s) — they will be analyzed for visual context.]" if followup_images else ""
            full_task = f"{context_briefing}\n\nCURRENT TASK: {content}{image_note}{_build_agent_guidelines()}" if context_briefing else f"{content}{image_note}{_build_agent_guidelines()}"

            # ── Step: Working — run pipeline as cancellable task ──
            logger.info("[%s] Starting task: %s", session.session_id, content[:100])
            pipeline_task_id = str(uuid.uuid4())[:8]
            pipeline_user = {
                "anthropic_api_key": api_key,
                "gemini_api_key": gemini_api_key,
                "git_provider": "gitlab" if "gitlab" in (session.repo_url or "").lower() else "github",
                "github_repo": session.repo_url or "",
                "github_token": session.git_token or "",
                "gitlab_repo": session.repo_url or "",
                "gitlab_token": session.git_token or "",
                "selected_branch": session.branch or "main",
                "git_token": session.git_token or "",
            }

            # Run pipeline as cancellable task (same pattern as initial task)
            pipeline_task = asyncio.create_task(
                run_pipeline(
                    task=full_task,
                    user=pipeline_user,
                    websocket=websocket,
                    task_id=pipeline_task_id,
                    conversation_id=conversation_id,
                    images=followup_images,
                )
            )

            # Listen for stop messages while pipeline runs
            followup_stopped = False
            while not pipeline_task.done():
                try:
                    msg_coro = asyncio.ensure_future(websocket.receive_json())
                    done_set, _ = await asyncio.wait(
                        {pipeline_task, msg_coro},
                        return_when=asyncio.FIRST_COMPLETED,
                    )

                    if msg_coro in done_set:
                        stop_data = msg_coro.result()
                        stop_msg_type = stop_data.get("type", "message")
                        if stop_msg_type == "ping":
                            await websocket.send_json({"type": "pong"})
                        elif stop_msg_type in ("stop", "stop_task"):
                            explicit_stop = True
                            followup_stopped = True
                            pipeline_task.cancel()
                            await websocket.send_json({
                                "type": "status",
                                "status": "stopping",
                                "message": "Stopping agent...",
                            })
                            try:
                                await pipeline_task
                            except (asyncio.CancelledError, Exception):
                                pass
                            break
                    else:
                        # Pipeline finished, cancel the pending receive
                        msg_coro.cancel()
                        try:
                            await msg_coro
                        except (asyncio.CancelledError, Exception):
                            pass
                        break
                except Exception:
                    break

            if followup_stopped:
                await websocket.send_json({
                    "type": "status",
                    "status": "ready",
                    "message": "Task stopped. Ready for next instruction.",
                })
            else:
                # Pipeline completed normally — update session workspace_dir
                try:
                    result_path = pipeline_task.result()
                    if result_path and session:
                        session.workspace_dir = result_path
                except Exception:
                    pass

                # ── Step: Finished ────────────────────────────────
                files_changed = await _get_files_changed(session)
                last_msg = _extract_last_agent_message(session)

                finish_summary = []
                if last_msg:
                    finish_summary.append(last_msg)
                if files_changed:
                    finish_summary.append(f"\nChanged files:\n{files_changed}")

                summary_text = "\n".join(finish_summary) if finish_summary else "Task completed."

                await websocket.send_json({
                    "type": "step", "step": "finished",
                    "label": "Finished", "done": True,
                    "summary": summary_text,
                })

                # ── Save structured agent response to DB ──────────
                if chat_session_id:
                    try:
                        await ChatService.add_message(
                            session_id=chat_session_id, role="assistant",
                            content=summary_text,
                            event_type="AgentResponse",
                            user_jwt=user_jwt,
                        )
                    except Exception as exc:
                        logger.warning("Failed to persist agent response: %s", exc)

                # Update session summary with latest task
                await _update_session_summary(
                    chat_session_id, content, session, user_jwt
                )

                await websocket.send_json({
                    "type": "status",
                    "status": "ready",
                    "message": "Task completed. Ready for next instruction.",
                })

    except WebSocketDisconnect:
        logger.info(
            "WebSocket disconnected%s",
            f" — session {session.session_id}" if session else "",
        )
    except asyncio.TimeoutError:
        logger.warning("WebSocket initial config timeout")
        try:
            await websocket.send_json({
                "type": "error",
                "message": "Timeout waiting for initial configuration.",
            })
        except Exception:
            pass
    except Exception as exc:
        logger.error("WebSocket error (session=%s): %s", getattr(session, "session_id", "?"), exc, exc_info=True)
        try:
            await websocket.send_json({
                "type": "error",
                "message": "An internal error occurred. Please try again.",
            })
        except Exception:
            pass
    finally:
        # Cancel any running pipeline task to prevent orphaned Claude subprocesses
        if pipeline_task and not pipeline_task.done():
            pipeline_task.cancel()
            try:
                await pipeline_task
            except (asyncio.CancelledError, Exception):
                pass
            logger.info("Pipeline task cancelled on disconnect")

        if streaming_task:
            streaming_task.cancel()
            try:
                await streaming_task
            except asyncio.CancelledError:
                pass

        # Only destroy if user explicitly stopped — otherwise keep alive for reconnection
        if session and explicit_stop:
            await destroy_session(session.session_id)
            logger.info("Session %s destroyed (user stopped)", session.session_id)
        elif session:
            session.touch()
            logger.info("Session %s kept alive for reconnection (TTL 2h)", session.session_id)

        # Mark chat session as inactive
        if chat_session_id and ws_user:
            try:
                await ChatService.deactivate_session(
                    chat_session_id, user_id=ws_user.user_id, user_jwt=ws_user.raw_jwt
                )
            except Exception as exc:
                logger.warning("Failed to mark chat session inactive: %s", exc)

        # Destroy workspace for this conversation
        await workspace_manager.destroy_workspace(conversation_id)
        logger.info("Workspace destroyed for conversation %s", conversation_id)

        logger.info("WebSocket session cleaned up")


async def _get_change_summary(session: AgentSession) -> str:
    """Generate a summary of file changes made by the agent."""
    if not session.workspace_dir:
        return ""
    try:
        import subprocess
        result = await asyncio.to_thread(
            subprocess.run,
            ["git", "diff", "--stat", "HEAD"],
            cwd=session.workspace_dir,
            capture_output=True, text=True, timeout=10,
        )
        diff_stat = result.stdout.strip()
        if not diff_stat:
            # Check for untracked files
            result2 = await asyncio.to_thread(
                subprocess.run,
                ["git", "status", "--porcelain"],
                cwd=session.workspace_dir,
                capture_output=True, text=True, timeout=10,
            )
            status = result2.stdout.strip()
            if not status:
                return ""
            # Parse untracked/modified files
            files = []
            for line in status.split("\n"):
                if line.strip():
                    status_code = line[:2].strip()
                    file_path = line[3:].strip()
                    if status_code == "??":
                        files.append(f"  + {file_path} (new)")
                    elif status_code in ("M", "MM"):
                        files.append(f"  ~ {file_path} (modified)")
                    elif status_code in ("D",):
                        files.append(f"  - {file_path} (deleted)")
                    else:
                        files.append(f"  {status_code} {file_path}")
            if files:
                return "📋 **Changes made:**\n" + "\n".join(files)
            return ""
        return "📋 **Changes made:**\n```\n" + diff_stat + "\n```"
    except Exception as exc:
        logger.warning("Failed to get change summary: %s", exc)
        return ""


async def _update_session_summary(
    chat_session_id: str | None,
    task: str,
    session: AgentSession,
    user_jwt: str | None,
) -> None:
    """Build a structured 'handoff note' and save to chat_sessions.

    The note captures three things so the next agent session immediately
    understands the conversation history:

    1. **Task** — what the user asked
    2. **Files changed** — which files were created / modified / deleted
    3. **Last agent message** — the final explanation or result
    """
    if not chat_session_id or not user_jwt:
        return

    try:
        # ── 1. Gather file changes from git ──────────────────
        files_changed = await _get_files_changed(session)

        # ── 2. Extract last meaningful agent message ─────────
        last_agent_msg = _extract_last_agent_message(session)

        # ── 3. Build the handoff note ────────────────────────
        parts = []
        parts.append(f"Task: {task[:400]}")

        if files_changed:
            parts.append(f"Files changed:\n{files_changed}")

        if last_agent_msg:
            parts.append(f"Result: {last_agent_msg[:600]}")

        summary = "\n\n".join(parts)

        update_data: dict = {
            "last_task": task[:500] if task else "",
            "summary": summary[:2000],
        }

        async with db_client(user_jwt) as client:
            await (
                client.table("chat_sessions")
                .update(update_data)
                .eq("id", chat_session_id)
                .execute()
            )
        logger.info("Session summary updated for %s", chat_session_id)
    except Exception as exc:
        # Gracefully handle missing columns — don't break the flow
        logger.warning("Failed to update session summary: %s", exc)


async def _get_files_changed(session: AgentSession) -> str:
    """Return a compact list of files changed in the workspace.

    Format:
      + src/new_file.js (new)
      ~ src/existing.css (modified)
      - old_config.json (deleted)
    """
    if not session.workspace_dir:
        return ""
    try:
        import subprocess
        result = await asyncio.to_thread(
            subprocess.run,
            ["git", "status", "--porcelain"],
            cwd=session.workspace_dir,
            capture_output=True, text=True, timeout=10,
        )
        status = result.stdout.strip()
        if not status:
            return ""

        files = []
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

        return "\n".join(files[:30])  # Cap at 30 files
    except Exception:
        return ""


def _extract_last_agent_message(session: AgentSession) -> str:
    """Return the last meaningful agent message, cleaned for human display.

    Reads from ``session.last_agent_message`` which is tracked by the
    ``on_event`` callback in real-time — NOT from the event buffer
    (which is already drained by ``stream_events_to_ws``).
    """
    raw = getattr(session, "last_agent_message", "") or ""
    if not raw:
        return ""

    # Clean up raw Python dict strings like:
    # "{'message': 'The login page...', 'kind': 'FinishAction'}"
    if raw.strip().startswith("{") and "message" in raw:
        try:
            import ast
            data = ast.literal_eval(raw.strip())
            if isinstance(data, dict) and "message" in data:
                return data["message"]
        except (ValueError, SyntaxError):
            pass

    # Filter out non-user-facing content
    skip_prefixes = (
        "Running: `",
        "Viewing file:",
        "File:",
        "{'",
    )
    if any(raw.startswith(p) for p in skip_prefixes):
        return ""

    return raw


async def _run_conversation_with_timeout(
    websocket: WebSocket,
    session: AgentSession,
) -> None:
    """Run ``conversation.run()`` with a timeout.

    Catches all errors gracefully so the WebSocket stays alive
    and the user can send follow-up tasks.
    """
    try:
        await asyncio.wait_for(
            asyncio.to_thread(session.conversation.run),
            timeout=CONVERSATION_TIMEOUT_SECONDS,
        )

        # Give the streaming task time to drain remaining events
        await asyncio.sleep(0.5)

        # NOTE: Change summary is now sent as a structured 'finished' step
        # by the task handler in ws.py. No duplicateChangeSummary here.
        try:
            await websocket.send_json({
                "type": "status",
                "status": "completed",
                "message": "Agent task completed.",
            })
        except (RuntimeError, Exception):
            pass  # Client already disconnected — normal race condition
    except asyncio.TimeoutError:
        logger.warning("Session %s timed out after %ds", session.session_id, CONVERSATION_TIMEOUT_SECONDS)
        try:
            error_msg = f"Agent timed out after {CONVERSATION_TIMEOUT_SECONDS}s."
            if session.last_agent_message:
                session.last_agent_message += f"\n[Task aborted: {error_msg}]"
            else:
                session.last_agent_message = f"Error: {error_msg}"
            
            await websocket.send_json({
                "type": "error",
                "message": error_msg,
            })
        except (RuntimeError, Exception):
            pass
    except Exception as exc:
        # Catch ConversationRunError, LLM errors, etc.
        # Do NOT re-raise — keep the WebSocket alive for follow-up tasks
        error_msg = str(exc)
        # Extract the readable part from ConversationRunError
        if "ConversationRunError" in type(exc).__name__:
            # e.g. "Conversation run failed for id=...: litellm.NotFoundError: ..."
            parts = error_msg.split(": ", 1)
            error_msg = parts[-1] if len(parts) > 1 else error_msg
        logger.error(
            "Conversation run error (session=%s): %s",
            session.session_id, error_msg,
        )
        try:
            error_content = f"Agent error: {error_msg[:300]}"
            session.last_agent_message = error_content
            await websocket.send_json({
                "type": "agent_event",
                "event": "error",
                "eventType": "ConversationError",
                "content": error_content,
            })
        except (RuntimeError, Exception):
            pass


async def _auto_push_if_needed(
    websocket: WebSocket,
    session: AgentSession,
    commit_message: str | None = None,
    new_branch: str | None = None,
) -> None:
    """Push changes to remote if the session has a repo and git_token."""
    print(f"DEBUG _auto_push_if_needed called")
    print(f"DEBUG   repo_url: {bool(session.repo_url)} = {session.repo_url}")
    print(f"DEBUG   git_token: {bool(session.git_token)} (len={len(session.git_token) if session.git_token else 0})")
    print(f"DEBUG   workspace_dir: {bool(session.workspace_dir)} = {session.workspace_dir}")
    print(f"DEBUG   branch: {session.branch}")

    if not session.repo_url or not session.git_token or not session.workspace_dir:
        skip_reason = []
        if not session.repo_url:
            skip_reason.append("no repo_url")
        if not session.git_token:
            skip_reason.append("no git_token")
        if not session.workspace_dir:
            skip_reason.append("no workspace_dir")
        reason_str = ", ".join(skip_reason)
        logger.info("Auto-push skipped: %s", reason_str)
        print(f"DEBUG auto-push SKIPPED: {reason_str}")
        try:
            await websocket.send_json({
                "type": "warning",
                "message": f"⚠️ Auto-push skipped: {reason_str}. Changes are saved locally.",
            })
        except Exception:
            pass
        return

    try:
        # Check for changes first
        status = await get_git_status(session.workspace_dir)
        if not status:
            await websocket.send_json({
                "type": "agent_event",
                "event": "observation",
                "eventType": "GitStatus",
                "content": "No changes to push.",
                "timestamp": now_iso(),
            })
            return

        await websocket.send_json({
            "type": "agent_event",
            "event": "action",
            "eventType": "GitPushAction",
            "content": f"Pushing changes to {session.repo_url}...\n\nFiles changed:\n{status}",
            "timestamp": now_iso(),
        })

        result = await push_changes(
            workspace_dir=session.workspace_dir,
            token=session.git_token,
            commit_message=commit_message or f"Lucid AI: {session.task[:100]}",
            branch=session.branch,
            new_branch=new_branch,
        )

        await websocket.send_json({
            "type": "agent_event",
            "event": "observation",
            "eventType": "GitPushObservation",
            "content": (
                f"✅ Changes pushed successfully!\n\n{result.get('summary', '')}"
                if result.get("pushed")
                else f"ℹ️ {result.get('summary', 'No changes to push')}"
            ),
            "timestamp": now_iso(),
        })

        # Emit a structured push result event for the frontend PR card
        if result.get("pushed"):
            target_branch = new_branch or session.branch or "main"
            repo_url = session.repo_url or ""
            # Build a GitHub/GitLab compare URL for easy PR creation
            pr_url = ""
            if "github.com" in repo_url:
                # https://github.com/owner/repo/compare/main...branch
                clean_url = repo_url.rstrip(".git").rstrip("/")
                pr_url = f"{clean_url}/compare/{session.branch}...{target_branch}" if new_branch else ""
            elif "gitlab" in repo_url:
                clean_url = repo_url.rstrip(".git").rstrip("/")
                pr_url = f"{clean_url}/-/merge_requests/new?merge_request[source_branch]={target_branch}" if new_branch else ""

            await websocket.send_json({
                "type": "git_push_result",
                "pushed": True,
                "branch": target_branch,
                "repoUrl": repo_url,
                "prUrl": pr_url,
                "summary": result.get("summary", ""),
                "newBranch": bool(new_branch),
                "timestamp": now_iso(),
            })

        # If we pushed to a new branch, update the session to track it
        if result.get("pushed") and new_branch:
            logger.info("Updating session %s branch to %s", session.session_id, new_branch)
            session.branch = new_branch

    except Exception as exc:
        logger.error("Git push failed for session %s: %s", session.session_id, exc)
        try:
            await websocket.send_json({
                "type": "agent_event",
                "event": "error",
                "eventType": "GitPushError",
                "content": f"Failed to push changes: {exc}",
                "timestamp": now_iso(),
            })
        except Exception:
            pass


# ── Mock agent loop ──────────────────────────────────────────

_MOCK_STEPS: list[dict] = [
    {
        "type": "agent_event", "event": "action",
        "eventType": "ThinkAction",
        "content": "Analyzing task…",
        "thought": "Let me break this down into steps…",
    },
    {
        "type": "agent_event", "event": "action",
        "eventType": "CmdRunAction",
        "content": "mkdir -p /workspace && cd /workspace",
        "command": "mkdir -p /workspace && cd /workspace",
    },
    {
        "type": "agent_event", "event": "observation",
        "eventType": "CmdOutputObservation",
        "content": "Directory created successfully.",
        "exitCode": 0,
    },
    {
        "type": "agent_event", "event": "action",
        "eventType": "FileWriteAction",
        "content": 'print("Hello, World!")',
        "path": "/workspace/hello.py",
    },
    {
        "type": "agent_event", "event": "observation",
        "eventType": "FileWriteObservation",
        "content": "File written: /workspace/hello.py",
        "path": "/workspace/hello.py",
    },
    {
        "type": "agent_event", "event": "action",
        "eventType": "CmdRunAction",
        "content": "python /workspace/hello.py",
        "command": "python /workspace/hello.py",
    },
    {
        "type": "agent_event", "event": "observation",
        "eventType": "CmdOutputObservation",
        "content": "Hello, World!",
        "exitCode": 0,
    },
    {
        "type": "status", "status": "completed",
        "message": (
            "[MOCK] Task completed. This is a simulated response. "
            "Install the OpenHands SDK packages to enable real "
            "Docker-sandboxed agent execution."
        ),
    },
]


async def _run_mock_loop(websocket: WebSocket, session: AgentSession) -> None:
    """Simulate agent behaviour when the SDK is not installed."""
    for step in _MOCK_STEPS:
        # Copy before mutating — _MOCK_STEPS is module-level; concurrent
        # WebSocket connections would overwrite each other's timestamp/content.
        step_copy = dict(step)
        step_copy["timestamp"] = now_iso()
        if step_copy.get("eventType") == "ThinkAction":
            step_copy["content"] = f'Analyzing task: "{session.task}"'
        await websocket.send_json(step_copy)
        await asyncio.sleep(MOCK_STEP_DELAY_SECONDS)

    try:
        while True:
            data = await websocket.receive_json()
            content = data.get("content", "")
            if content:
                await websocket.send_json({
                    "type": "agent_event",
                    "event": "observation",
                    "eventType": "MockResponse",
                    "content": (
                        f'[MOCK] Received: "{content}"\n'
                        "The agent would process this in production mode."
                    ),
                    "timestamp": now_iso(),
                })
    except (WebSocketDisconnect, Exception):
        pass
