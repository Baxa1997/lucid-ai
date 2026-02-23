"""SQLAlchemy models for chat persistence.

Previously, the ``User`` model was a read-only reference to an external
table. Now, the ai_engine owns the ``users`` table as well as
``ChatSession`` and ``ChatMessage``. All IDs are primary keys of type UUID.
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
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class User(Base):
    """The ``users`` table is now managed by the ai_engine."""

    __tablename__ = "users"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    email = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=True)

    chat_sessions = relationship("ChatSession", back_populates="user", passive_deletes=True)


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    user_id = Column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
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

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    session_id = Column(UUID(as_uuid=False), ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False)
    role = Column(String(20), nullable=False)      # "user" | "assistant" | "system"
    content = Column(Text, nullable=False)
    event_type = Column(String(100), nullable=True)  # e.g. "CmdRunAction"
    metadata_json = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    session = relationship("ChatSession", back_populates="messages")

    __table_args__ = (
        Index("ix_chat_messages_session_id", "session_id"),
    )
