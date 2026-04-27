"""Event bus: Redis Stream backed event proxy for pipeline events.

WebSocketProxy is a drop-in replacement for FastAPI's WebSocket that:

  1. Publishes every ``send_json()`` call to a Redis Stream (XADD) keyed by
     ``session_id`` so events survive client disconnects.
  2. Forwards events to the real WebSocket if one is currently attached.
  3. Falls back to an in-memory asyncio.Queue when Redis is unavailable.

The underlying WebSocket is *swappable* — the pipeline never sees the real
connection and keeps running regardless of disconnects:

    proxy = WebSocketProxy(session_id, websocket)
    run_pipeline(..., websocket=proxy, ...)

    # Client disconnects — pipeline keeps going silently:
    proxy.detach()

    # Client reconnects — replay missed events then resume live:
    await proxy.replay(new_websocket)
    proxy.attach(new_websocket)
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from app.config import logger
from app.services.redis_client import get_redis

# ── Stream config ─────────────────────────────────────────────
_STREAM_PREFIX = "lucid:events:"
_STREAM_MAX_LEN = 500   # keep last 500 events per session (MAXLEN ~)
_STREAM_TTL = 3600      # expire stream after 1 hour of no activity


class WebSocketProxy:
    """Drop-in WebSocket replacement — only ``send_json()`` is required.

    The pipeline receives this object instead of a real ``WebSocket``.
    It implements the exact same ``send_json()`` interface so zero pipeline
    code needs to change.
    """

    def __init__(self, session_id: str, websocket: Any = None) -> None:
        self._session_id = session_id
        self._ws: Any = websocket
        # In-memory fallback queue used when Redis is unavailable.
        # QueueFull events are silently dropped (stream overflow protection).
        self._queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=500)
        # Chat persistence — when bound, every chat-relevant send_json call
        # spawns a background task that mirrors the event into chat_messages
        # so reload/reconnect can replay the full conversation. Unbound proxies
        # (e.g. reconnects before a chat session row exists) are no-ops.
        self._chat_session_id: str | None = None
        self._user_jwt: str | None = None

    # ── Lifecycle helpers ─────────────────────────────────────

    def attach(self, websocket: Any) -> None:
        """Swap in a new WebSocket connection (called on client reconnect)."""
        self._ws = websocket

    def detach(self) -> None:
        """Remove the WebSocket reference without cancelling the pipeline.

        After detach, ``send_json()`` still publishes to Redis — events
        accumulate in the stream so they can be replayed on the next connect.
        """
        self._ws = None

    @property
    def is_attached(self) -> bool:
        """True if a live WebSocket is currently attached."""
        return self._ws is not None

    @property
    def session_id(self) -> str:
        return self._session_id

    def bind_chat(self, chat_session_id: str, user_jwt: str | None) -> None:
        """Enable DB persistence of chat-bound events for this proxy.

        Called from the WS handler once the chat_sessions row exists. Until
        bound, send_json never touches the DB. After bind, every chat-relevant
        event is mirrored into chat_messages via a background task — the
        pipeline is never blocked on Supabase, and a DB error never affects
        the live stream.
        """
        self._chat_session_id = chat_session_id
        self._user_jwt = user_jwt

    # ── Core interface ────────────────────────────────────────

    async def send_json(self, data: dict) -> None:
        """Publish to Redis Stream AND forward to WebSocket (if attached).

        Redis publish happens first so the event is never lost even if the
        WebSocket send fails.
        """
        # ── 1. Persist to Redis Stream ────────────────────────
        redis = get_redis()
        if redis:
            try:
                key = f"{_STREAM_PREFIX}{self._session_id}"
                await redis.xadd(
                    key,
                    {"data": json.dumps(data, default=str)},
                    maxlen=_STREAM_MAX_LEN,
                    approximate=True,
                )
                # Refresh TTL on each publish so active sessions never expire
                await redis.expire(key, _STREAM_TTL)
            except Exception as exc:
                logger.debug("EventBus: Redis publish failed (%s) — using queue fallback", exc)
                try:
                    self._queue.put_nowait(data)
                except asyncio.QueueFull:
                    pass  # oldest events implicitly dropped
        else:
            try:
                self._queue.put_nowait(data)
            except asyncio.QueueFull:
                pass

        # ── 2. Forward to live WebSocket ──────────────────────
        ws = self._ws
        if ws is not None:
            try:
                await ws.send_json(data)
            except Exception:
                # Send failed — detach silently; pipeline continues
                self._ws = None

        # ── 3. Mirror chat-relevant events to chat_messages ──
        # Fire-and-forget — DB writes never block the live event stream.
        # Only fires when bound (after the chat_sessions row exists).
        if self._chat_session_id:
            try:
                asyncio.create_task(self._persist_chat_event(data))
            except RuntimeError:
                # No running loop (shouldn't happen in WS context, but defensive)
                pass

    # ── Chat persistence ──────────────────────────────────────

    async def _persist_chat_event(self, data: dict) -> None:
        """Mirror a chat-bound event into chat_messages.

        Skipped for UI-only events (progress, step, phase, file_change,
        preview_*, agent_event, control signals). When the event IS chat-bound,
        we map it to a row that the frontend's chat_history loader already
        knows how to render — plain text bubbles for chat_message/complete/
        error/warning, and the structured `{messageType: "plan", planData: …}`
        JSON shape for plans (see useAgentSession.js:907).

        All errors swallowed — chat persistence is best-effort, never fatal.
        """
        try:
            msg_type = data.get("type", "")

            role: str | None = None
            content: str = ""
            event_type: str = ""

            if msg_type == "chat_message":
                role = data.get("role") or "assistant"
                # Structured plan messages — preserve the JSON envelope so
                # the history loader can re-hydrate the plan card on reload.
                if data.get("messageType") == "plan" and data.get("planData"):
                    content = json.dumps({
                        "messageType": "plan",
                        "planData": data.get("planData"),
                    })
                    event_type = "Plan"
                else:
                    content = data.get("content") or ""
                    event_type = "ChatMessage"

            elif msg_type in ("plan", "plan_emit"):
                # Standalone plan emission (not wrapped in chat_message).
                role = "assistant"
                plan_data = data.get("planData") or data.get("plan") or {
                    k: v for k, v in data.items() if k not in ("type",)
                }
                content = json.dumps({
                    "messageType": "plan",
                    "planData": plan_data,
                })
                event_type = "Plan"

            elif msg_type == "complete":
                role = "assistant"
                content = data.get("message") or ""
                event_type = "Complete"

            elif msg_type == "error":
                role = "assistant"
                content = "⚠️ " + (data.get("message") or "")
                event_type = "Error"

            elif msg_type == "warning":
                role = "assistant"
                content = "⚠️ " + (data.get("message") or "")
                event_type = "Warning"

            else:
                return  # not a chat-bound event

            if role is None or not content.strip():
                return

            # Late import keeps event_bus dependency-light + dodges any
            # circular import risk through app.services.chat.
            from app.services.chat import ChatService

            await ChatService.add_message(
                session_id=self._chat_session_id,
                role=role,
                content=content,
                event_type=event_type,
                user_jwt=self._user_jwt,
            )
        except Exception as exc:
            logger.warning(
                "Chat event persist failed (non-fatal, type=%s): %s",
                data.get("type"), exc,
            )

    # ── Replay ────────────────────────────────────────────────

    async def replay(self, websocket: Any, last_id: str = "0-0") -> int:
        """Send all buffered events to a freshly-connected WebSocket.

        Reads events from the Redis Stream starting *after* ``last_id``
        (exclusive).  Use ``"0-0"`` to replay everything.  Returns the count
        of replayed events.

        After replaying, sends a ``{"type": "_cursor", "id": "<last_stream_id>"}``
        message so the client can track its position for the next reconnect.

        When Redis is unavailable the in-memory queue is drained instead.
        """
        count = 0
        last_replayed_id: str | None = None
        redis = get_redis()

        if redis:
            try:
                key = f"{_STREAM_PREFIX}{self._session_id}"
                # XRANGE with exclusive start: use "(last_id" syntax to skip
                # already-seen events.  "0-0" means "from the very beginning".
                range_start = f"({last_id}" if last_id and last_id != "0-0" else last_id
                # XRANGE returns list of (stream_id, {field: value}) pairs
                events = await redis.xrange(key, min=range_start)
                for eid, fields in events:
                    raw = fields.get("data", "{}")
                    try:
                        await websocket.send_json(json.loads(raw))
                        count += 1
                        last_replayed_id = eid
                    except Exception:
                        break  # WebSocket closed mid-replay
            except Exception as exc:
                logger.warning("EventBus: Redis replay failed: %s", exc)
        else:
            # Drain the in-memory queue
            while not self._queue.empty():
                try:
                    await websocket.send_json(self._queue.get_nowait())
                    count += 1
                except Exception:
                    break

        if count:
            logger.info(
                "EventBus: replayed %d events for session %s",
                count,
                self._session_id,
            )

        # Always send a cursor event so the client can track its stream position.
        # If nothing was replayed, send the current stream tip so future
        # reconnects don't needlessly re-replay the entire history.
        if last_replayed_id is None and redis:
            try:
                key = f"{_STREAM_PREFIX}{self._session_id}"
                tip = await redis.xrevrange(key, count=1)
                if tip:
                    last_replayed_id = tip[0][0]
            except Exception:
                pass

        if last_replayed_id is not None:
            try:
                await websocket.send_json({"type": "_cursor", "id": last_replayed_id})
            except Exception:
                pass

        return count
