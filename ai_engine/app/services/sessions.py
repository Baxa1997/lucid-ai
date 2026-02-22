"""Agent session lifecycle — store, create, destroy.

The ``AgentSession`` dataclass holds per-session SDK objects.
``SessionStore`` manages the in-memory dict and the asyncio lock.
This is the *only* module that touches the global session state.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from app.config import logger, settings, EVENT_BUFFER_MAX_SIZE
from app import sdk
from app.exceptions import SessionNotFoundError
from app.services.llm import resolve_llm


# ── Session dataclass ───────────────────────────────────────

class AgentSession:
    """Encapsulates a single user's agent session."""

    __slots__ = (
        "session_id", "user_id", "task", "repo_url",
        "created_at", "is_alive",
        "conversation", "workspace", "agent", "llm",
        "event_buffer", "container_id",
    )

    def __init__(
        self,
        session_id: str,
        user_id: str,
        task: str,
        repo_url: Optional[str] = None,
    ):
        self.session_id = session_id
        self.user_id = user_id
        self.task = task
        self.repo_url = repo_url
        self.created_at = datetime.now(timezone.utc)
        self.is_alive = True

        # SDK objects — populated by create_session()
        self.conversation: Any = None
        self.workspace: Any = None
        self.agent: Any = None
        self.llm: Any = None

        # Docker container ID (unused now, kept for compatibility)
        self.container_id: Optional[str] = None

        # Queue for streaming events to the WebSocket handler
        self.event_buffer: asyncio.Queue = asyncio.Queue(maxsize=EVENT_BUFFER_MAX_SIZE)


# ── In-memory session store ─────────────────────────────────

class SessionStore:
    """Thread-safe, in-memory session registry."""

    def __init__(self) -> None:
        self._sessions: dict[str, AgentSession] = {}
        self._lock = asyncio.Lock()

    async def add(self, session: AgentSession) -> None:
        async with self._lock:
            self._sessions[session.session_id] = session

    async def get(self, session_id: str) -> AgentSession:
        async with self._lock:
            session = self._sessions.get(session_id)
        if session is None:
            raise SessionNotFoundError(session_id)
        return session

    async def get_or_none(self, session_id: str) -> AgentSession | None:
        async with self._lock:
            return self._sessions.get(session_id)

    async def pop(self, session_id: str) -> AgentSession | None:
        async with self._lock:
            return self._sessions.pop(session_id, None)

    async def contains(self, session_id: str) -> bool:
        async with self._lock:
            return session_id in self._sessions

    async def list_all(self) -> list[AgentSession]:
        async with self._lock:
            return list(self._sessions.values())

    async def count(self) -> int:
        async with self._lock:
            return len(self._sessions)

    async def snapshot_ids(self) -> list[str]:
        async with self._lock:
            return list(self._sessions.keys())


# Module-level singleton — imported by routers and app factory
store = SessionStore()


# ── Session lifecycle ───────────────────────────────────────

async def create_session(
    *,
    task: str,
    user_id: str | None = None,
    repo_url: str | None = None,
    git_token: str | None = None,
    branch: str | None = None,
    git_user_name: str | None = None,
    git_user_email: str | None = None,
    model_provider: str | None = None,
    api_key: str | None = None,
    project_id: str | None = None,
) -> AgentSession:
    """Create and register a fully-initialised agent session.

    Two modes:
    1. Real mode (SDK installed): LocalConversation with a local workspace dir
    2. Mock mode (no SDK): Simulated agent responses
    """
    from app.events import format_sdk_event

    session_id = str(uuid.uuid4())
    provider = (model_provider or settings.DEFAULT_PROVIDER).lower()

    if not user_id:
        raise ValueError("create_session requires a non-empty user_id")

    # ── Mock path ────────────────────────────────────────────
    if not sdk.OPENHANDS_AVAILABLE:
        session = AgentSession(
            session_id=session_id,
            user_id=user_id,
            task=task,
            repo_url=repo_url,
        )
        await store.add(session)
        return session

    # ── Real path ────────────────────────────────────────────
    llm = resolve_llm(provider, api_key)

    # get_default_agent registers all tools before creating the agent
    agent = sdk.get_default_agent(llm=llm, cli_mode=True)

    # Create local workspace directory
    workspace_dir = os.path.join(settings.WORKSPACE_BASE_PATH, user_id, session_id)
    os.makedirs(workspace_dir, exist_ok=True)
    logger.info("Using LocalWorkspace at %s", workspace_dir)

    session = AgentSession(
        session_id=session_id,
        user_id=user_id,
        task=task,
        repo_url=repo_url,
    )
    session.llm = llm
    session.agent = agent
    session.workspace = workspace_dir

    def on_event(event):
        try:
            event_data = format_sdk_event(event)
            if event_data:
                try:
                    session.event_buffer.put_nowait(event_data)
                except asyncio.QueueFull:
                    pass
        except Exception as exc:
            logger.error("Event callback error: %s", exc)

    conversation = sdk.LocalConversation(
        agent=agent,
        workspace=workspace_dir,
        callbacks=[on_event],
    )
    session.conversation = conversation

    await store.add(session)
    logger.info("Session %s created — task: %s", session_id, task[:60])
    return session


async def destroy_session(session_id: str) -> None:
    """Stop and clean up an agent session."""
    session = await store.pop(session_id)
    if not session:
        return

    session.is_alive = False
    logger.info("Destroying session %s", session_id)

    if session.conversation and hasattr(session.conversation, "close"):
        try:
            await asyncio.to_thread(session.conversation.close)
            logger.info("Conversation closed for session %s", session_id)
        except Exception as exc:
            logger.error("Error closing conversation: %s", exc)

    # Clean up local workspace directory
    if isinstance(session.workspace, str) and os.path.isdir(session.workspace):
        shutil.rmtree(session.workspace, ignore_errors=True)
