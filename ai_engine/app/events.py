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


def now_iso() -> str:
    """Current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def format_sdk_event(event) -> Optional[dict]:
    """Convert an OpenHands SDK event into a JSON-serialisable dict
    suitable for WebSocket transmission to the frontend.
    """
    event_type = type(event).__name__

    # Extract content from whichever attribute exists
    content = ""
    for attr in ("content", "message", "text"):
        if hasattr(event, attr):
            content = str(getattr(event, attr))
            break

    # Special-case: conversation state update
    if (
        sdk.OPENHANDS_AVAILABLE
        and sdk.ConversationStateUpdateEvent
        and isinstance(event, sdk.ConversationStateUpdateEvent)
    ):
        return {
            "type": "agent_event",
            "event": "state_update",
            "content": content or str(event),
            "timestamp": now_iso(),
        }

    # Determine category from the class name
    if "Action" in event_type:
        event_category = "action"
    elif "Error" in event_type:
        event_category = "error"
    elif "State" in event_type or "Update" in event_type:
        event_category = "state"
    else:
        event_category = "observation"

    payload: dict = {
        "type": "agent_event",
        "event": event_category,
        "eventType": event_type,
        "content": content[:WS_EVENT_MAX_CHARS],
        "timestamp": now_iso(),
    }

    # Attach optional fields when present
    if hasattr(event, "command"):
        payload["command"] = str(event.command)
    if hasattr(event, "exit_code"):
        payload["exitCode"] = event.exit_code
    if hasattr(event, "path"):
        payload["path"] = str(event.path)
    if hasattr(event, "thought") and event.thought:
        payload["thought"] = str(event.thought)[:THOUGHT_MAX_CHARS]

    return payload


async def _flush_batch(batch: list[dict], chat_session_id: str) -> None:
    """Write a batch of event dicts to the database in a single session."""
    if not batch:
        return
    try:
        from app.database import async_session as db_session_factory
        from app.services.chat import ChatService

        async with db_session_factory() as db:
            for event_data in batch:
                await ChatService.add_message(
                    db,
                    session_id=chat_session_id,
                    role="assistant",
                    content=event_data["content"],
                    event_type=event_data.get("eventType", ""),
                )
    except Exception as exc:
        logger.warning("Failed to flush %d events to DB: %s", len(batch), exc)


async def stream_events_to_ws(
    websocket: WebSocket,
    session,
    *,
    chat_session_id: str | None = None,
) -> None:
    """Background task that drains the session's event buffer and
    forwards each item to the WebSocket client.

    When ``chat_session_id`` is provided, meaningful agent events are
    batched and flushed to the database periodically (every
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

                # Accumulate persistable events
                if (
                    chat_session_id
                    and event_data.get("content")
                    and event_data.get("event") in ("action", "observation", "error")
                ):
                    pending.append(event_data)

                # Flush when batch is full
                if len(pending) >= DB_BATCH_SIZE:
                    await _flush_batch(pending, chat_session_id)
                    pending.clear()
                    last_flush = time.monotonic()

            except asyncio.TimeoutError:
                pass
            except Exception:
                break

            # Flush on time interval even if batch isn't full
            if pending and (time.monotonic() - last_flush) >= DB_BATCH_INTERVAL:
                await _flush_batch(pending, chat_session_id)
                pending.clear()
                last_flush = time.monotonic()

    except asyncio.CancelledError:
        pass
    finally:
        # Flush remaining events on shutdown
        if pending and chat_session_id:
            await _flush_batch(pending, chat_session_id)
