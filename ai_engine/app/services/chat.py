"""ChatService — CRUD operations for chat sessions and messages."""

from __future__ import annotations

import json
import uuid
from typing import Optional

from sqlalchemy import select, update, delete, desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import ChatSession, ChatMessage


class ChatService:
    """Stateless service — each method receives a DB session."""

    @staticmethod
    async def create_session(
        db: AsyncSession,
        *,
        user_id: str,
        agent_session_id: str,
        project_id: str | None = None,
        title: str | None = None,
        model_provider: str | None = None,
    ) -> ChatSession:
        chat_session = ChatSession(
            id=str(uuid.uuid4()),
            user_id=user_id,
            agent_session_id=agent_session_id,
            project_id=project_id,
            title=title,
            model_provider=model_provider,
        )
        db.add(chat_session)
        await db.commit()
        await db.refresh(chat_session)
        return chat_session

    @staticmethod
    async def list_sessions(
        db: AsyncSession,
        user_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ChatSession]:
        result = await db.execute(
            select(ChatSession)
            .where(ChatSession.user_id == user_id)
            .order_by(desc(ChatSession.updated_at))
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_session(
        db: AsyncSession,
        session_id: str,
        user_id: str,
    ) -> Optional[ChatSession]:
        result = await db.execute(
            select(ChatSession)
            .options(selectinload(ChatSession.messages))
            .where(ChatSession.id == session_id, ChatSession.user_id == user_id)
        )
        return result.scalars().first()

    @staticmethod
    async def delete_session(db: AsyncSession, session_id: str, user_id: str) -> bool:
        result = await db.execute(
            delete(ChatSession).where(
                ChatSession.id == session_id,
                ChatSession.user_id == user_id,
            )
        )
        await db.commit()
        return result.rowcount > 0

    @staticmethod
    async def rename_session(
        db: AsyncSession,
        session_id: str,
        user_id: str,
        title: str,
    ) -> bool:
        result = await db.execute(
            update(ChatSession)
            .where(ChatSession.id == session_id, ChatSession.user_id == user_id)
            .values(title=title)
        )
        await db.commit()
        return result.rowcount > 0

    @staticmethod
    async def add_message(
        db: AsyncSession,
        *,
        session_id: str,
        role: str,
        content: str,
        event_type: str | None = None,
        metadata: dict | None = None,
    ) -> ChatMessage:
        message = ChatMessage(
            id=str(uuid.uuid4()),
            session_id=session_id,
            role=role,
            content=content,
            event_type=event_type,
            metadata_json=json.dumps(metadata) if metadata else None,
        )
        db.add(message)
        await db.commit()
        await db.refresh(message)
        return message
