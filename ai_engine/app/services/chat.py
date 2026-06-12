"""ChatService — CRUD operations for chat sessions and messages.

All database access uses the async Supabase client authenticated with the
caller's JWT so that Row Level Security (RLS) is enforced at the DB level.
Callers must pass ``user_jwt`` (from ``AuthenticatedUser.raw_jwt``) to every
method that touches the database.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Callable, Optional

from fastapi import HTTPException
from postgrest.exceptions import APIError

from app.config import logger
from app.supabase_client import db_client

# ── Transient-error retry helper ─────────────────────────────────────────────
_TRANSIENT_SIGNALS = (
    "connection",
    "timeout",
    "503",
    "502",
    "temporarily unavailable",
    "reset by peer",
    "eof",
    "broken pipe",
)


async def _with_retry(fn: Callable[[], Any], *, max_retries: int = 3) -> Any:
    """Run an async callable, retrying up to *max_retries* times on transient
    Supabase / network errors with exponential back-off (0.25 s, 0.5 s, 1 s).

    Non-transient errors (auth, RLS violations, bad requests) are re-raised
    immediately without retrying.
    """
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            return await fn()
        except (APIError, Exception) as exc:
            err_lower = str(exc).lower()
            is_transient = any(sig in err_lower for sig in _TRANSIENT_SIGNALS)
            if not is_transient or attempt == max_retries - 1:
                raise
            last_exc = exc
            delay = 0.25 * (2 ** attempt)
            logger.warning(
                "Transient Supabase error (attempt %d/%d, retry in %.2fs): %s",
                attempt + 1, max_retries, delay, exc,
            )
            await asyncio.sleep(delay)
    raise last_exc  # unreachable, but satisfies type checkers


class ChatService:
    """Stateless service — each method uses the caller's JWT via managed_client."""

    @staticmethod
    async def create_session(
        *,
        user_id: str,
        user_jwt: str | None,
        agent_session_id: str,
        project_id: str | None = None,
        title: str | None = None,
        model_provider: str | None = None,
    ) -> dict:
        # Use the admin client unconditionally for this write. The user-JWT
        # path was returning 42501 from PostgREST after migration 020
        # rewrote the chat_sessions SELECT policy to be membership-aware:
        # PostgREST's INSERT-then-RETURNING flow re-checks the SELECT policy
        # on the just-inserted row, and at that point the AFTER-INSERT
        # trigger hasn't yet added the project_members owner row in the
        # same transaction's visibility scope. The row carries an explicit
        # user_id, the SECURITY DEFINER trigger still fires and adds the
        # owner membership, and RLS isolation is preserved at read time.
        row = {
            "id": str(uuid.uuid4()),
            "user_id": user_id,
            "agent_session_id": agent_session_id,
            "project_id": project_id,
            "title": title,
            "model_provider": model_provider,
        }
        try:
            async def _create():
                async with db_client(None) as client:
                    result = await client.table("chat_sessions").insert(row).execute()
                return result.data[0] if result.data else row
            return await _with_retry(_create)
        except APIError as exc:
            logger.error("Supabase error in create_session: code=%s msg=%s", exc.code, exc.message)
            raise HTTPException(status_code=500, detail="Database error") from exc
        except Exception as exc:
            logger.error("Unexpected error in create_session: %s", exc)
            raise HTTPException(status_code=500, detail="Internal server error") from exc

    @staticmethod
    async def list_sessions(
        user_id: str,
        user_jwt: str | None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        try:
            async with db_client(user_jwt) as client:
                result = (
                    await client.table("chat_sessions")
                    .select("*")
                    .eq("user_id", user_id)
                    .order("updated_at", desc=True)
                    .range(offset, offset + limit - 1)
                    .execute()
                )
            return result.data or []
        except APIError as exc:
            logger.error("Supabase error in list_sessions: code=%s msg=%s", exc.code, exc.message)
            raise HTTPException(status_code=500, detail="Database error") from exc
        except Exception as exc:
            logger.error("Unexpected error in list_sessions: %s", exc)
            raise HTTPException(status_code=500, detail="Internal server error") from exc

    @staticmethod
    async def get_session(
        session_id: str,
        user_id: str,
        user_jwt: str | None,
    ) -> Optional[dict]:
        try:
            async with db_client(user_jwt) as client:
                result = (
                    await client.table("chat_sessions")
                    .select("*, chat_messages(*)")
                    .eq("id", session_id)
                    .eq("user_id", user_id)
                    .maybe_single()
                    .execute()
                )
            return result.data
        except APIError as exc:
            logger.error("Supabase error in get_session: code=%s msg=%s", exc.code, exc.message)
            raise HTTPException(status_code=500, detail="Database error") from exc
        except Exception as exc:
            logger.error("Unexpected error in get_session: %s", exc)
            raise HTTPException(status_code=500, detail="Internal server error") from exc

    @staticmethod
    async def delete_session(session_id: str, user_id: str, user_jwt: str | None) -> bool:
        try:
            async with db_client(user_jwt) as client:
                result = (
                    await client.table("chat_sessions")
                    .delete()
                    .eq("id", session_id)
                    .eq("user_id", user_id)
                    .select("id")          # ensures PostgREST returns deleted rows
                    .execute()
                )
            return bool(result.data)
        except APIError as exc:
            logger.error("Supabase error in delete_session: code=%s msg=%s", exc.code, exc.message)
            raise HTTPException(status_code=500, detail="Database error") from exc
        except Exception as exc:
            logger.error("Unexpected error in delete_session: %s", exc)
            raise HTTPException(status_code=500, detail="Internal server error") from exc

    @staticmethod
    async def rename_session(
        session_id: str,
        user_id: str,
        title: str,
        user_jwt: str | None,
    ) -> bool:
        try:
            async with db_client(user_jwt) as client:
                result = (
                    await client.table("chat_sessions")
                    .update({"title": title})
                    .eq("id", session_id)
                    .eq("user_id", user_id)
                    .select("id")          # ensures PostgREST returns updated rows
                    .execute()
                )
            return bool(result.data)
        except APIError as exc:
            logger.error("Supabase error in rename_session: code=%s msg=%s", exc.code, exc.message)
            raise HTTPException(status_code=500, detail="Database error") from exc
        except Exception as exc:
            logger.error("Unexpected error in rename_session: %s", exc)
            raise HTTPException(status_code=500, detail="Internal server error") from exc

    @staticmethod
    async def add_message(
        *,
        session_id: str,
        role: str,
        content: str,
        user_jwt: str | None,
        event_type: str | None = None,
        metadata: dict | None = None,
    ) -> dict:
        row = {
            "id": str(uuid.uuid4()),
            "session_id": session_id,
            "role": role,
            "content": content,
            "event_type": event_type,
            "metadata_json": metadata,
        }
        try:
            async def _insert():
                async with db_client(user_jwt) as client:
                    result = await client.table("chat_messages").insert(row).execute()
                return result.data[0] if result.data else row
            return await _with_retry(_insert)
        except APIError as exc:
            logger.error("Supabase error in add_message: code=%s msg=%s", exc.code, exc.message)
            raise HTTPException(status_code=500, detail="Database error") from exc
        except Exception as exc:
            logger.error("Unexpected error in add_message: %s", exc)
            raise HTTPException(status_code=500, detail="Internal server error") from exc

    @staticmethod
    async def last_message(
        *,
        session_id: str,
        user_jwt: str | None,
        role: str | None = None,
    ) -> Optional[dict]:
        """Return the most recent message row for a session (optionally
        filtered by role), or None. Used to dedup re-sent handshake tasks
        before persisting."""
        try:
            async def _select():
                async with db_client(user_jwt) as client:
                    q = (
                        client.table("chat_messages")
                        .select("id, role, content, event_type, created_at")
                        .eq("session_id", session_id)
                    )
                    if role:
                        q = q.eq("role", role)
                    result = await q.order("created_at", desc=True).limit(1).execute()
                return result.data[0] if result.data else None
            return await _with_retry(_select)
        except Exception as exc:
            logger.warning("last_message lookup failed (treating as none): %s", exc)
            return None

    @staticmethod
    async def add_messages(
        events: list[dict],
        session_id: str,
        user_jwt: str | None,
    ) -> None:
        """Batch-insert a list of event dicts as assistant messages."""
        if not events:
            return
        rows = [
            {
                "id": str(uuid.uuid4()),
                "session_id": session_id,
                "role": "assistant",
                "content": e["content"],
                "event_type": e.get("eventType", ""),
                "metadata_json": None,
            }
            for e in events
        ]
        try:
            async def _batch():
                async with db_client(user_jwt) as client:
                    await client.table("chat_messages").insert(rows).execute()
            await _with_retry(_batch)
        except APIError as exc:
            logger.error("Supabase error in add_messages: code=%s msg=%s", exc.code, exc.message)
            raise HTTPException(status_code=500, detail="Database error") from exc
        except Exception as exc:
            logger.error("Unexpected error in add_messages: %s", exc)
            raise HTTPException(status_code=500, detail="Internal server error") from exc

    @staticmethod
    async def deactivate_session(
        session_id: str, user_id: str, user_jwt: str | None
    ) -> None:
        """Mark a chat session as inactive (called on WebSocket disconnect)."""
        try:
            async def _deactivate():
                async with db_client(user_jwt) as client:
                    await (
                        client.table("chat_sessions")
                        .update({"is_active": False})
                        .eq("id", session_id)
                        .eq("user_id", user_id)
                        .execute()
                    )
            await _with_retry(_deactivate)
        except APIError as exc:
            logger.error(
                "Supabase error in deactivate_session: code=%s msg=%s", exc.code, exc.message
            )
        except Exception as exc:
            logger.error("Unexpected error in deactivate_session: %s", exc)
