"""Plan confirmation gate + persisted plan store.

Extracted from project_generator.py (god-module split). The pipelines
emit a generation plan and block on user confirmation; ws.py resolves
the gate when the user clicks confirm/reject. Both sides meet here.

Plan confirmation: an asyncio.Future keyed by a STABLE string
(chat_session_id when available, else f"ws-{id(websocket)}"). A stable
key survives websocket reconnects: the pipeline runs in the background
while the user's tab refreshes, then a new ws connection can resolve
the same future.
  Future result: {"confirmed": True} or {"confirmed": False, "correction": "..."}

Persisted plans: keyed by chat_session_id, holds the most recent
emitted plan envelope so the ws.py reconnect path can re-emit it when
the user's tab refreshes during the confirmation gate. Cleared on
confirm/reject/timeout. Backed by chat_sessions.pending_plan so a plan
survives an ai_engine restart; the in-memory dict is the fast path.

NOTE: both dicts are process-local state — confirmation only works when
the resolving websocket lands on the same process as the waiting
pipeline (single-process deployment assumption, same as sessions.py).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

logger = logging.getLogger("lucid.plan_store")

pending_plan_confirmations: dict[str, asyncio.Future] = {}

PLAN_CONFIRM_TIMEOUT_SECONDS = 1800  # 30 minutes — abort if user doesn't confirm

_persisted_plans: dict[str, dict] = {}


def _confirmation_key(websocket, chat_session_id: str = "") -> str:
    """Build the stable confirmation-future key. Prefers chat_session_id."""
    return chat_session_id or f"ws-{id(websocket)}"


def register_plan_confirmation(key: str) -> asyncio.Future:
    """Register a pending plan confirmation under the given stable key."""
    loop = asyncio.get_event_loop()
    fut = loop.create_future()
    pending_plan_confirmations[key] = fut
    return fut


def resolve_plan_confirmation(key: str, result: dict):
    """Called by ws.py when user confirms or rejects the plan."""
    fut = pending_plan_confirmations.pop(key, None)
    if fut and not fut.done():
        fut.set_result(result)


async def save_persisted_plan(chat_session_id: str, plan_data: dict, task: str = "") -> None:
    """Stash a plan so ws.py can re-emit it on reconnect.

    Writes to both the in-memory cache (fast) and chat_sessions.pending_plan
    (durable across ai_engine restarts). DB write is best-effort — if the
    migration hasn't been applied, the in-memory store still works.
    """
    if not chat_session_id:
        return
    envelope = {
        "plan_data": plan_data,
        "task": task,
        "saved_at": time.time(),
    }
    _persisted_plans[chat_session_id] = envelope
    try:
        from app.supabase_client import db_client
        async with db_client(None) as sb:
            await (
                sb.table("chat_sessions")
                .update({"pending_plan": envelope})
                .eq("id", chat_session_id)
                .execute()
            )
    except Exception as exc:
        # Migration may not be applied yet, or column may not exist.
        # In-memory store still works for the current ai_engine process.
        logger.debug("save_persisted_plan: DB write failed (in-memory still set): %s", exc)


async def get_persisted_plan(chat_session_id: str) -> Optional[dict]:
    """Return the persisted plan envelope for a chat session, or None.

    Checks the in-memory cache first; falls back to DB so a plan that
    survived an ai_engine restart can be restored on the next reconnect.
    """
    if not chat_session_id:
        return None
    cached = _persisted_plans.get(chat_session_id)
    if cached:
        return cached
    try:
        from app.supabase_client import db_client
        async with db_client(None) as sb:
            res = await (
                sb.table("chat_sessions")
                .select("pending_plan")
                .eq("id", chat_session_id)
                .single()
                .execute()
            )
        envelope = (res.data or {}).get("pending_plan") if hasattr(res, "data") else None
        if envelope:
            _persisted_plans[chat_session_id] = envelope
            return envelope
    except Exception as exc:
        logger.debug("get_persisted_plan: DB read failed: %s", exc)
    return None


async def clear_persisted_plan(chat_session_id: str) -> None:
    """Drop the persisted plan once it's been confirmed / rejected / timed out."""
    if not chat_session_id:
        return
    _persisted_plans.pop(chat_session_id, None)
    try:
        from app.supabase_client import db_client
        async with db_client(None) as sb:
            await (
                sb.table("chat_sessions")
                .update({"pending_plan": None})
                .eq("id", chat_session_id)
                .execute()
            )
    except Exception as exc:
        logger.debug("clear_persisted_plan: DB write failed: %s", exc)
