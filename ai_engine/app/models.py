"""SQLAlchemy models for chat persistence.

The ``User`` model is a read-only reference to the Prisma-managed ``users``
table — Alembic migrations will NOT touch it.  Only ``ChatSession`` and
``ChatMessage`` are owned by the ai_engine.
"""

import uuid

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy.orm import relationship

from app.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class User(Base):
    """Read-only mirror of Prisma's ``users`` table (for FK relationships)."""

    __tablename__ = "users"

    id = Column(String, primary_key=True)
    email = Column(String, unique=True)
    name = Column(String, nullable=True)

    chat_sessions = relationship("ChatSession", back_populates="user", passive_deletes=True)


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id = Column(String, primary_key=True, default=_uuid)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    agent_session_id = Column(String, index=True)
    project_id = Column(String, nullable=True)
    title = Column(String(255), nullable=True)
    model_provider = Column(String(50), nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user = relationship("User", back_populates="chat_sessions")
    messages = relationship(
        "ChatMessage", back_populates="session", cascade="all, delete-orphan", passive_deletes=True,
    )

    __table_args__ = (
        Index("ix_chat_sessions_user_id", "user_id"),
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(String, primary_key=True, default=_uuid)
    session_id = Column(String, ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False)
    role = Column(String(20), nullable=False)      # "user" | "assistant" | "system"
    content = Column(Text, nullable=False)
    event_type = Column(String(100), nullable=True)  # e.g. "CmdRunAction"
    metadata_json = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    session = relationship("ChatSession", back_populates="messages")

    __table_args__ = (
        Index("ix_chat_messages_session_id", "session_id"),
    )
