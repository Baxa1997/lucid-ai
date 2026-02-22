"""WebSocket endpoint for real-time agent communication."""

from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import update

from app.auth import AuthenticatedUser, authenticate_websocket, authenticate_from_handshake
from app.models import ChatSession as ChatSessionModel
from app.config import (
    logger,
    settings,
    WS_INIT_TIMEOUT_SECONDS,
    MOCK_STEP_DELAY_SECONDS,
    CONVERSATION_TIMEOUT_SECONDS,
)
from app import sdk
from app.database import async_session
from app.events import now_iso, stream_events_to_ws
from app.services.chat import ChatService
from app.services.sessions import (
    AgentSession,
    create_session,
    destroy_session,
)

router = APIRouter()


@router.websocket("/api/v1/ws")
async def websocket_agent(websocket: WebSocket):
    """Real-time agent communication channel.

    Protocol
    --------
    1. Client sends initial config ``{ "task": "...", ... }``
    2. Server creates a session and streams agent events back
    3. Client may send follow-ups ``{ "type": "message", "content": "..." }``
    4. On disconnect the sandbox is cleaned up
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

        task = raw.get("task", "")
        if not task:
            await websocket.send_json({
                "type": "error",
                "message": "Missing required field: 'task'",
            })
            await websocket.close(code=4001, reason="Missing task")
            return

        await websocket.send_json({
            "type": "status",
            "status": "initializing",
            "message": "Setting up agent workspace...",
        })

        user_id = ws_user.user_id

        # ── 2. Create session (per-session Docker sandbox) ─
        session = await create_session(
            task=task,
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
            project_id=raw.get("projectId", ""),
        )

        # ── Persist chat session to DB ───────────────────
        try:
            async with async_session() as db:
                chat_sess = await ChatService.create_session(
                    db,
                    user_id=user_id,
                    agent_session_id=session.session_id,
                    project_id=raw.get("projectId"),
                    title=task[:255],
                    model_provider=raw.get("modelProvider", raw.get("model_provider")),
                )
                chat_session_id = chat_sess.id
                logger.info("Chat session %s created for user %s", chat_session_id, user_id)
        except Exception as exc:
            logger.warning("Failed to create chat session in DB: %s", exc)

        # Save user's initial message
        if chat_session_id:
            try:
                async with async_session() as db:
                    await ChatService.add_message(
                        db, session_id=chat_session_id, role="user",
                        content=task, event_type="InitialTask",
                    )
            except Exception as exc:
                logger.warning("Failed to persist user message: %s", exc)

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

        # ── 4. Real agent loop ───────────────────────────
        await websocket.send_json({
            "type": "status",
            "status": "ready",
            "sessionId": session.session_id,
            "message": "Agent session ready. Starting task...",
        })

        streaming_task = asyncio.create_task(
            stream_events_to_ws(websocket, session, chat_session_id=chat_session_id),
        )

        await websocket.send_json({
            "type": "agent_event",
            "event": "task_start",
            "content": f"Agent starting task: {task}",
        })

        session.conversation.send_message(task)
        await _run_conversation_with_timeout(websocket, session)

        # ── 5. Follow-up loop ────────────────────────────
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type", "message")
            content = data.get("content", "")

            if not content:
                await websocket.send_json({"type": "error", "message": "Empty content"})
                continue

            if msg_type == "stop":
                await websocket.send_json({
                    "type": "status",
                    "status": "stopping",
                    "message": "Stopping agent...",
                })
                break

            logger.info("[%s] Follow-up: %s", session.session_id, content[:80])

            # Persist follow-up message
            if chat_session_id:
                try:
                    async with async_session() as db:
                        await ChatService.add_message(
                            db, session_id=chat_session_id, role="user",
                            content=content, event_type="FollowUp",
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
        if session:
            await destroy_session(session.session_id)

        # Mark chat session as inactive
        if chat_session_id:
            try:
                async with async_session() as db:
                    await db.execute(
                        update(ChatSessionModel)
                        .where(ChatSessionModel.id == chat_session_id)
                        .values(is_active=False)
                    )
                    await db.commit()
            except Exception as exc:
                logger.warning("Failed to mark chat session inactive: %s", exc)

        logger.info("WebSocket session cleaned up")


async def _run_conversation_with_timeout(
    websocket: WebSocket,
    session: AgentSession,
) -> None:
    """Run ``conversation.run()`` with a timeout.

    Sends a "completed" or "timeout" status to the client when done.
    """
    try:
        await asyncio.wait_for(
            asyncio.to_thread(session.conversation.run),
            timeout=CONVERSATION_TIMEOUT_SECONDS,
        )
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
            await websocket.send_json({
                "type": "error",
                "message": f"Agent timed out after {CONVERSATION_TIMEOUT_SECONDS}s.",
            })
        except (RuntimeError, Exception):
            pass  # Client already disconnected


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
