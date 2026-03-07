"""Agent session lifecycle — store, create, destroy.

The ``AgentSession`` dataclass holds per-session SDK objects.
``SessionStore`` manages the in-memory dict and the asyncio lock.
This is the *only* module that touches the global session state.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from app.config import logger, settings, EVENT_BUFFER_MAX_SIZE

# Sessions expire after 24 hours of inactivity
SESSION_TTL_SECONDS = 24 * 60 * 60  # 24 hours
REAPER_INTERVAL_SECONDS = 5 * 60    # check every 5 minutes
from app import sdk
from app.exceptions import SessionNotFoundError
from app.services.llm import resolve_llm
from app.services.git_operations import clone_repo


def _safe_put(queue: asyncio.Queue, item) -> None:
    """Thread-safe helper to put an item into an asyncio queue."""
    try:
        queue.put_nowait(item)
    except asyncio.QueueFull:
        pass  # Drop oldest? For now, just skip

# ── Session dataclass ───────────────────────────────────────

class AgentSession:
    """Encapsulates a single user's agent session."""

    __slots__ = (
        "session_id", "user_id", "task", "repo_url",
        "branch", "git_token",
        "created_at", "last_active", "is_alive",
        "conversation", "workspace", "workspace_dir",
        "agent", "llm",
        "event_buffer", "container_id", "project_id",
    )

    def __init__(
        self,
        session_id: str,
        user_id: str,
        task: str,
        repo_url: Optional[str] = None,
        branch: Optional[str] = None,
        git_token: Optional[str] = None,
    ):
        self.session_id = session_id
        self.user_id = user_id
        self.task = task
        self.repo_url = repo_url
        self.branch = branch or "main"
        self.git_token = git_token
        self.created_at = datetime.now(timezone.utc)
        self.last_active = time.monotonic()
        self.is_alive = True
        self.project_id: str = ""

        # SDK objects — populated by create_session()
        self.conversation: Any = None
        self.workspace: Any = None       # SDK Workspace object
        self.workspace_dir: str = ""     # Local filesystem path
        self.agent: Any = None
        self.llm: Any = None

        # Docker sandbox container ID — set when a container is created
        self.container_id: str | None = None

        # Queue for streaming events to the WebSocket handler
        self.event_buffer: asyncio.Queue = asyncio.Queue(maxsize=EVENT_BUFFER_MAX_SIZE)

    def touch(self) -> None:
        """Update last_active timestamp."""
        self.last_active = time.monotonic()

    def is_expired(self) -> bool:
        """Check if this session has been inactive for longer than TTL."""
        return (time.monotonic() - self.last_active) > SESSION_TTL_SECONDS


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

    async def find_by_user_and_project(
        self, user_id: str, project_id: str
    ) -> AgentSession | None:
        """Find an active session for a given user + project."""
        async with self._lock:
            for s in self._sessions.values():
                if (
                    s.user_id == user_id
                    and s.project_id == project_id
                    and s.is_alive
                    and not s.is_expired()
                ):
                    return s
        return None

    async def expired_sessions(self) -> list[str]:
        """Return IDs of all expired sessions."""
        async with self._lock:
            return [
                sid for sid, s in self._sessions.items()
                if s.is_expired()
            ]


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
    1. Real mode (SDK installed): Conversation with a local workspace
       — clones repo, runs agent in workspace directory
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
        session.project_id = project_id or ""
        await store.add(session)
        return session

    # ── Real path ────────────────────────────────────────────
    llm = resolve_llm(provider, api_key)

    # get_default_agent creates an agent with terminal, file_editor, etc.
    agent = sdk.get_default_agent(
        llm=llm,
        cli_mode=True,
    )

    # Create the workspace directory on the host
    workspace_dir = os.path.join(
        settings.WORKSPACE_BASE_PATH, user_id, session_id
    )
    os.makedirs(workspace_dir, exist_ok=True)

    session = AgentSession(
        session_id=session_id,
        user_id=user_id,
        task=task,
        repo_url=repo_url,
        branch=branch,
        git_token=git_token,
    )
    session.llm = llm
    session.agent = agent
    session.workspace_dir = workspace_dir
    session.project_id = project_id or ""

    # ── Clone repo if provided ───────────────────────────────
    if repo_url and repo_url.strip():
        try:
            await clone_repo(
                repo_url=repo_url,
                token=git_token or "",
                branch=branch or "main",
                workspace_dir=workspace_dir,
                git_user_name=git_user_name,
                git_user_email=git_user_email,
            )
            logger.info(
                "Repo %s cloned into %s (branch=%s)",
                repo_url, workspace_dir, branch,
            )
        except Exception as exc:
            logger.error("Failed to clone repo %s: %s", repo_url, exc)
            # Continue without the clone — the workspace dir still exists
            # and the agent can work from scratch

    # ── Create SDK Workspace ─────────────────────────────────
    workspace_obj = sdk.Workspace(working_dir=workspace_dir)
    session.workspace = workspace_obj
    logger.info("Workspace created at %s for session %s", workspace_dir, session_id)

    # ── Event callback (called from SDK thread — must be thread-safe) ──
    loop = asyncio.get_event_loop()

    def on_event(event):
        """Forward SDK events to the session's asyncio buffer.
        
        This is called from conversation.run() which runs in a thread
        (via asyncio.to_thread), so we use call_soon_threadsafe to safely
        enqueue to the asyncio Queue.
        """
        try:
            event_data = format_sdk_event(event)
            if event_data:
                # Thread-safe way to put into asyncio.Queue
                loop.call_soon_threadsafe(_safe_put, session.event_buffer, event_data)
        except Exception as exc:
            logger.error("Event callback error: %s", exc)

    # ── Create SDK Conversation ──────────────────────────────
    conversation = sdk.Conversation(
        agent,
        workspace=workspace_obj,
        callbacks=[on_event],
        max_iteration_per_run=settings.MAX_ITERATIONS,
        visualizer=None,  # we stream via our own WebSocket
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
    if session.workspace_dir and os.path.isdir(session.workspace_dir):
        shutil.rmtree(session.workspace_dir, ignore_errors=True)


async def reap_expired_sessions() -> None:
    """Background task: periodically destroy sessions older than TTL."""
    while True:
        try:
            await asyncio.sleep(REAPER_INTERVAL_SECONDS)
            expired = await store.expired_sessions()
            for sid in expired:
                logger.info("Reaping expired session %s", sid)
                await destroy_session(sid)
            if expired:
                logger.info("Reaped %d expired sessions", len(expired))
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error("Session reaper error: %s", exc)
