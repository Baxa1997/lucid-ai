"""WebSocket endpoint for real-time agent communication."""

from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

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

router = APIRouter()


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
    chat_session_id: Optional[str] = None

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
        project_id = raw.get("projectId", "")
        explicit_stop = False  # track if user explicitly stopped

        # ── 2. Try to reconnect to existing session ───────
        existing = await session_store.find_by_user_and_project(user_id, project_id) if project_id else None

        if existing:
            session = existing
            session.touch()
            logger.info("Reconnecting to existing session %s for project %s", session.session_id, project_id)
            await websocket.send_json({
                "type": "status",
                "status": "ready",
                "sessionId": session.session_id,
                "reconnected": True,
                "message": "Reconnected to existing workspace.",
            })
        else:
            # ── Create new session (clone repo if provided) ──
            session = await create_session(
                task=task or "Workspace initialization",
                user_id=user_id,
                repo_url=raw.get("repoUrl", ""),
                git_token=raw.get("gitToken", ""),
                branch=raw.get("branch", ""),
                git_user_name=raw.get("gitUserName", ""),
                git_user_email=raw.get("gitUserEmail", ""),
                model_provider=raw.get(
                    "modelProvider",
                    raw.get("model_provider", settings.DEFAULT_PROVIDER),
                ),
                api_key=raw.get("apiKey", raw.get("api_key", "")),
                project_id=project_id,
            )

        user_jwt = ws_user.raw_jwt

        # ── Persist chat session to DB ───────────────────
        try:
            chat_sess = await ChatService.create_session(
                user_id=user_id,
                user_jwt=user_jwt,
                agent_session_id=session.session_id,
                project_id=raw.get("projectId"),
                title=task[:255] if task else "New workspace session",
                model_provider=raw.get(
                    "modelProvider",
                    raw.get("model_provider", settings.DEFAULT_PROVIDER),
                ),
            )
            chat_session_id = chat_sess["id"]
            logger.info("Chat session %s created for user %s", chat_session_id, user_id)
        except Exception as exc:
            logger.warning("Failed to create chat session in DB: %s", exc)

        # ── 3. Mock path ─────────────────────────────────
        if not sdk.OPENHANDS_AVAILABLE:
            await websocket.send_json({
                "type": "status",
                "status": "mock_mode",
                "sessionId": session.session_id,
                "message": (
                    "Running in MOCK mode — OpenHands SDK not installed. "
                    "Install openhands-sdk, openhands-tools, openhands-workspace "
                    "to enable real agent execution."
                ),
            })
            await _run_mock_loop(websocket, session)
            return

        # ── 4. Real agent — workspace is ready ───────────
        await websocket.send_json({
            "type": "status",
            "status": "ready",
            "sessionId": session.session_id,
            "message": "Workspace ready. You can start giving tasks.",
        })

        streaming_task = asyncio.create_task(
            stream_events_to_ws(
                websocket, session,
                chat_session_id=chat_session_id,
                user_jwt=user_jwt,
            ),
        )

        # ── If task was included in handshake, run it immediately ─
        if task:
            # Save user's initial message
            if chat_session_id:
                try:
                    await ChatService.add_message(
                        session_id=chat_session_id, role="user",
                        content=task, event_type="InitialTask",
                        user_jwt=user_jwt,
                    )
                except Exception as exc:
                    logger.warning("Failed to persist user message: %s", exc)

            await websocket.send_json({
                "type": "agent_event",
                "event": "task_start",
                "content": f"Agent starting task: {task}",
            })

            session.conversation.send_message(task)
            await _run_conversation_with_timeout(websocket, session)

            # Push changes if repo was cloned
            await _auto_push_if_needed(websocket, session)

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

            if not content:
                # Skip empty messages (heartbeats, pongs, etc.) — don't send error
                continue

            if msg_type == "stop":
                explicit_stop = True
                await websocket.send_json({
                    "type": "status",
                    "status": "stopping",
                    "message": "Stopping agent...",
                })
                break

            if msg_type == "push":
                # Explicit push request from client
                await _auto_push_if_needed(websocket, session, content or "Manual push by user")
                continue

            logger.info("[%s] Follow-up: %s", session.session_id, content[:80])

            # Persist follow-up message
            if chat_session_id:
                try:
                    await ChatService.add_message(
                        session_id=chat_session_id, role="user",
                        content=content, event_type="FollowUp",
                        user_jwt=user_jwt,
                    )
                except Exception as exc:
                    logger.warning("Failed to persist follow-up message: %s", exc)

            await websocket.send_json({
                "type": "agent_event",
                "event": "task_start",
                "content": f"Processing: {content[:80]}...",
            })

            session.conversation.send_message(content)
            await _run_conversation_with_timeout(websocket, session)

            # Push after each follow-up task completes
            await _auto_push_if_needed(websocket, session)

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
            logger.info("Session %s kept alive for reconnection (TTL 24h)", session.session_id)

        # Mark chat session as inactive
        if chat_session_id and ws_user:
            try:
                await ChatService.deactivate_session(
                    chat_session_id, user_id=ws_user.user_id, user_jwt=ws_user.raw_jwt
                )
            except Exception as exc:
                logger.warning("Failed to mark chat session inactive: %s", exc)

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

        # Generate a change summary
        change_summary = await _get_change_summary(session)
        
        try:
            if change_summary:
                await websocket.send_json({
                    "type": "agent_event",
                    "event": "action",
                    "eventType": "ChangeSummary",
                    "content": change_summary,
                })
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
            await websocket.send_json({
                "type": "error",
                "message": f"Agent timed out after {CONVERSATION_TIMEOUT_SECONDS}s.",
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
            await websocket.send_json({
                "type": "agent_event",
                "event": "error",
                "eventType": "ConversationError",
                "content": f"Agent error: {error_msg[:300]}",
            })
        except (RuntimeError, Exception):
            pass


async def _auto_push_if_needed(
    websocket: WebSocket,
    session: AgentSession,
    commit_message: str | None = None,
) -> None:
    """Push changes to remote if the session has a repo and git_token."""
    if not session.repo_url or not session.git_token or not session.workspace_dir:
        logger.info(
            "Auto-push skipped (repo=%s, token=%s, dir=%s)",
            bool(session.repo_url), bool(session.git_token), bool(session.workspace_dir),
        )
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
