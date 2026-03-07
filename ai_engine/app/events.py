"""Event formatting and WebSocket streaming helpers."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Optional

from fastapi import WebSocket

from app.config import (
    WS_EVENT_MAX_CHARS,
    THOUGHT_MAX_CHARS,
    DB_BATCH_SIZE,
    DB_BATCH_INTERVAL,
    logger,
)
from app import sdk
from app.services.chat import ChatService


def now_iso() -> str:
    """Current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def format_sdk_event(event) -> Optional[dict]:
    """Convert an OpenHands SDK event into a JSON-serialisable dict
    suitable for WebSocket transmission to the frontend.

    Handles all SDK V1 event types:
    - ActionEvent    → agent thinking + tool calls
    - MessageEvent   → agent messages to user
    - ObservationEvent / ObservationBaseEvent → tool results
    - ConversationErrorEvent → errors
    - ConversationStateUpdateEvent → state changes
    - Condensation / TokenEvent / PauseEvent → internal (skip)
    """
    event_type = type(event).__name__

    # ── Skip internal / uninteresting events ────────────────
    if event_type in ("Condensation", "CondensationRequest", "TokenEvent",
                       "PauseEvent", "SystemPromptEvent",
                       "ConversationStateUpdateEvent", "CondensationSummaryEvent"):
        return None

    # Skip observation events — they are tool return values.
    # The ActionEvent already shows what the tool did.
    if "Observation" in event_type:
        return None

    # ── Extract content based on event type ─────────────────
    content = ""
    thought = ""
    tool_name = ""
    tool_args = ""
    event_category = "observation"

    if event_type == "ActionEvent":
        event_category = "action"
        is_read_only = False

        # Extract thinking / reasoning
        if hasattr(event, "thought") and event.thought:
            # thought is Sequence[TextContent] — each has .text
            parts = []
            for tc in event.thought:
                if hasattr(tc, "text") and tc.text:
                    parts.append(tc.text)
            thought = "\n".join(parts)

        if hasattr(event, "reasoning_content") and event.reasoning_content:
            if not thought:
                thought = str(event.reasoning_content)

        # Extract tool call info
        if hasattr(event, "tool_name") and event.tool_name:
            tool_name = str(event.tool_name)

        if hasattr(event, "action") and event.action:
            action = event.action
            # action is a tool schema Action — try to get its arguments
            if hasattr(action, "model_dump"):
                action_data = action.model_dump(exclude_none=True)
                file_path = action_data.get("path", "")
                cmd = action_data.get("command", "")

                # str_replace_editor tool — command is str_replace/view/create/insert
                _EDITOR_CMDS = {"str_replace", "view", "create", "insert", "undo_edit"}
                if cmd in _EDITOR_CMDS:
                    file_name = file_path.split("/")[-1] if file_path else ""
                    if cmd == "str_replace":
                        content = f"Editing file: {file_name or file_path}"
                    elif cmd == "create":
                        content = f"Created file: {file_name or file_path}"
                    elif cmd == "insert":
                        content = f"Inserting into: {file_name or file_path}"
                    elif cmd == "view":
                        content = f"Viewing file: {file_name or file_path}"
                        is_read_only = True
                    elif cmd == "undo_edit":
                        content = f"Undid edit: {file_name or file_path}"
                elif cmd:
                    # Real shell command (execute_bash, terminal, etc.)
                    content = f"Running: `{cmd}`"
                elif file_path and "content" in action_data:
                    content = f"Editing file: {file_path}"
                elif file_path:
                    content = f"File: {file_path}"
                else:
                    content = str(action_data)[:WS_EVENT_MAX_CHARS]
                tool_args = str(action_data)
            else:
                content = str(action)

        # If we have thought but no content, use thought as content
        if thought and not content:
            content = thought

        # Mark read-only exploration commands so frontend can hide them
        _READ_ONLY = ("ls", "cat", "head", "tail", "find", "tree", "grep",
                      "wc", "pwd", "echo", "file", "stat", "which", "type",
                      "readlink", "du", "df")
        if content.startswith("Running:"):
            cmd_str = content.replace("Running: `", "").lstrip("`").split()[0]
            if cmd_str.lower().rstrip("`") in _READ_ONLY:
                is_read_only = True

        # Also mark view/open file_editor actions as read-only
        if tool_name == "file_editor":
            action_lower = content.lower()
            if action_lower.startswith("file:") or "view" in action_lower:
                is_read_only = True

    elif event_type == "MessageEvent":
        event_category = "action"

        # Extract from extended_content (list of TextContent)
        if hasattr(event, "extended_content") and event.extended_content:
            parts = []
            for tc in event.extended_content:
                if hasattr(tc, "text") and tc.text:
                    parts.append(tc.text)
            content = "\n".join(parts)

        # Fallback to llm_message
        if not content and hasattr(event, "llm_message") and event.llm_message:
            msg = event.llm_message
            if hasattr(msg, "content"):
                if isinstance(msg.content, str):
                    content = msg.content
                elif isinstance(msg.content, list):
                    parts = []
                    for item in msg.content:
                        if hasattr(item, "text") and item.text:
                            parts.append(item.text)
                    content = "\n".join(parts)

    elif event_type == "ConversationErrorEvent":
        event_category = "error"
        if hasattr(event, "detail"):
            content = str(event.detail)
        elif hasattr(event, "code"):
            content = f"Error: {event.code}"

    elif event_type == "ConversationStateUpdateEvent":
        event_category = "state"
        if hasattr(event, "key") and hasattr(event, "value"):
            content = f"{event.key}: {event.value}"

    elif "Observation" in event_type:
        event_category = "observation"
        if hasattr(event, "tool_name"):
            tool_name = str(event.tool_name)
        # Try model_dump for observation content
        if hasattr(event, "model_dump"):
            try:
                data = event.model_dump(exclude_none=True, exclude={"id", "timestamp", "source", "kind"})
                if "tool_call_id" in data:
                    del data["tool_call_id"]
                if "tool_name" in data:
                    del data["tool_name"]
                content = str(data)[:WS_EVENT_MAX_CHARS] if data else ""
            except Exception:
                pass

    elif event_type == "CondensationSummaryEvent":
        event_category = "state"
        if hasattr(event, "summary"):
            content = str(event.summary)

    else:
        # Generic fallback — try common attribute names
        for attr in ("content", "message", "text", "detail", "summary"):
            if hasattr(event, attr):
                val = getattr(event, attr)
                if val:
                    content = str(val)
                    break
        # Last resort: model_dump
        if not content and hasattr(event, "model_dump"):
            try:
                data = event.model_dump(exclude_none=True, exclude={"id", "timestamp", "source", "kind"})
                content = str(data)[:WS_EVENT_MAX_CHARS] if data else ""
            except Exception:
                pass

    # ── Skip events with no meaningful content ─────────────
    if not content and not thought and not tool_name:
        return None

    # ── Build payload ───────────────────────────────────────
    payload: dict = {
        "type": "agent_event",
        "event": event_category,
        "eventType": event_type,
        "content": content[:WS_EVENT_MAX_CHARS] if content else "",
        "timestamp": now_iso(),
    }

    if thought:
        payload["thought"] = thought[:THOUGHT_MAX_CHARS]
    if tool_name:
        payload["toolName"] = tool_name
    if tool_args:
        payload["toolArgs"] = tool_args[:WS_EVENT_MAX_CHARS]

    # Attach optional fields for backward compat
    if hasattr(event, "command"):
        payload["command"] = str(event.command)
    if hasattr(event, "exit_code"):
        payload["exitCode"] = event.exit_code
    if hasattr(event, "path"):
        payload["path"] = str(event.path)
    # Attach readOnly flag for exploration commands
    if event_type == "ActionEvent" and locals().get("is_read_only"):
        payload["readOnly"] = True

    return payload


async def _flush_batch(
    batch: list[dict],
    chat_session_id: str,
    user_jwt: str | None,
) -> None:
    """Write a batch of event dicts to the database in a single insert."""
    if not batch:
        return
    try:
        await ChatService.add_messages(batch, chat_session_id, user_jwt=user_jwt)
    except Exception as exc:
        logger.warning("Failed to flush %d events to DB: %s", len(batch), exc)


async def stream_events_to_ws(
    websocket: WebSocket,
    session,
    *,
    chat_session_id: str | None = None,
    user_jwt: str | None = None,
) -> None:
    """Background task that drains the session's event buffer and
    forwards each item to the WebSocket client.

    When ``chat_session_id`` and ``user_jwt`` are provided, meaningful agent
    events are batched and flushed to the database periodically (every
    ``DB_BATCH_SIZE`` events or ``DB_BATCH_INTERVAL`` seconds).
    """
    pending: list[dict] = []
    last_flush = time.monotonic()

    try:
        while session.is_alive:
            try:
                event_data = await asyncio.wait_for(
                    session.event_buffer.get(), timeout=1.0,
                )
                await websocket.send_json(event_data)

                # Auto-refresh file tree on file-changing events
                from app.routers.files import should_refresh_file_tree, build_file_tree
                if should_refresh_file_tree(event_data):
                    try:
                        tree = await build_file_tree(session)
                        await websocket.send_json({
                            "type": "file_tree",
                            "tree": tree,
                            "timestamp": now_iso(),
                        })
                    except Exception as tree_err:
                        logger.warning("File tree refresh failed: %s", tree_err)

                # Accumulate persistable events (only when JWT is available for RLS)
                if (
                    chat_session_id
                    and user_jwt
                    and event_data.get("content")
                    and event_data.get("event") in ("action", "observation", "error")
                ):
                    pending.append(event_data)

                # Flush when batch is full
                if len(pending) >= DB_BATCH_SIZE:
                    await _flush_batch(pending, chat_session_id, user_jwt)
                    pending.clear()
                    last_flush = time.monotonic()

            except asyncio.TimeoutError:
                pass
            except Exception as exc:
                logger.warning("Event stream error (session=%s): %s", getattr(session, "session_id", "?"), exc)
                break

            # Flush on time interval even if batch isn't full
            if pending and (time.monotonic() - last_flush) >= DB_BATCH_INTERVAL:
                await _flush_batch(pending, chat_session_id, user_jwt)
                pending.clear()
                last_flush = time.monotonic()

    except asyncio.CancelledError:
        pass
    finally:
        # Flush remaining events on shutdown
        if pending and chat_session_id and user_jwt:
            await _flush_batch(pending, chat_session_id, user_jwt)
