"""ChatService — CRUD operations for chat sessions and messages.

All database access uses the async Supabase client authenticated with the
caller's JWT so that Row Level Security (RLS) is enforced at the DB level.
Callers must pass ``user_jwt`` (from ``AuthenticatedUser.raw_jwt``) to every
method that touches the database.
"""

from __future__ import annotations

import uuid
from typing import Optional

from fastapi import HTTPException
from postgrest.exceptions import APIError

from app.config import logger
from app.supabase_client import db_client


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
        row = {
            "id": str(uuid.uuid4()),
            "user_id": user_id,
            "agent_session_id": agent_session_id,
            "project_id": project_id,
            "title": title,
            "model_provider": model_provider,
        }
        try:
            async with db_client(user_jwt) as client:
                result = await client.table("chat_sessions").insert(row).execute()
            return result.data[0] if result.data else row
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
            async with db_client(user_jwt) as client:
                result = await client.table("chat_messages").insert(row).execute()
            return result.data[0] if result.data else row
        except APIError as exc:
            logger.error("Supabase error in add_message: code=%s msg=%s", exc.code, exc.message)
            raise HTTPException(status_code=500, detail="Database error") from exc
        except Exception as exc:
            logger.error("Unexpected error in add_message: %s", exc)
            raise HTTPException(status_code=500, detail="Internal server error") from exc

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
            async with db_client(user_jwt) as client:
                await client.table("chat_messages").insert(rows).execute()
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
            async with db_client(user_jwt) as client:
                await (
                    client.table("chat_sessions")
                    .update({"is_active": False})
                    .eq("id", session_id)
                    .eq("user_id", user_id)
                    .execute()
                )
        except APIError as exc:
            logger.error(
                "Supabase error in deactivate_session: code=%s msg=%s", exc.code, exc.message
            )
        except Exception as exc:
            logger.error("Unexpected error in deactivate_session: %s", exc)
