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

# Sessions expire after 2 hours of inactivity (production-safe)
SESSION_TTL_SECONDS = 2 * 60 * 60   # 2 hours
REAPER_INTERVAL_SECONDS = 2 * 60    # check every 2 minutes
MAX_SESSIONS_PER_USER = 3           # rate limit: max concurrent sessions
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
        "repo_context", "last_agent_message",
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
        self.repo_context: str = ""  # Scanned repo structure for agent context
        self.last_agent_message: str = ""  # Tracked for handoff summaries

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

    async def count_by_user(self, user_id: str) -> int:
        """Count active, non-expired sessions for a given user."""
        async with self._lock:
            return sum(
                1 for s in self._sessions.values()
                if s.user_id == user_id
                and s.is_alive
                and not s.is_expired()
            )


# Module-level singleton — imported by routers and app factory
store = SessionStore()


# ── Workspace scanning ──────────────────────────────────────

_KEY_FILES = (
    "package.json", "requirements.txt", "pyproject.toml",
    "Pipfile", "Cargo.toml", "go.mod", "pom.xml", "build.gradle",
    "README.md", "README.rst", "README",
    "Dockerfile", "docker-compose.yml", "docker-compose.yaml",
    ".env.example", ".env.sample",
    "Makefile", "tsconfig.json", "vite.config.ts", "vite.config.js",
    "next.config.js", "next.config.mjs",
    "webpack.config.js", "angular.json",
    # Design system context — helps agent match existing styles
    "tailwind.config.js", "tailwind.config.ts",
    "src/app/globals.css", "src/index.css", "src/styles/globals.css",
    "src/app/layout.js", "src/app/layout.tsx",
    ".eslintrc.json", ".eslintrc.js",
)
_MAX_KEY_FILE_CHARS = 2000  # cap per file to avoid huge payloads


def _scan_workspace(workspace_dir: str) -> str:
    """Build a text summary of the workspace for agent context.

    Returns a string like:
        Project structure:
        ├── src/
        │   ├── app.py
        │   └── utils.py
        ├── package.json
        └── README.md

        Key files:
        --- package.json ---
        { ... }
    """
    if not os.path.isdir(workspace_dir):
        return ""

    lines: list[str] = ["## Project structure\n"]
    count = 0

    for root, dirs, files in os.walk(workspace_dir):
        # Skip hidden dirs and common noise
        dirs[:] = [
            d for d in dirs
            if not d.startswith(".") and d not in (
                "node_modules", "__pycache__", "venv", ".venv",
                "dist", "build", ".next", ".git",
            )
        ]
        level = root.replace(workspace_dir, "").count(os.sep)
        if level > 3:
            continue  # max depth 3
        indent = "│   " * level
        basename = os.path.basename(root) or "."
        if level > 0:
            lines.append(f"{indent}├── {basename}/")
        for f in sorted(files):
            if f.startswith(".") and f not in (".env.example", ".env.sample"):
                continue
            lines.append(f"{indent}│   {f}")
            count += 1
            if count > 200:
                lines.append(f"{indent}│   ... (truncated)")
                break
        if count > 200:
            break

    # Read key config files
    key_contents: list[str] = []
    for kf in _KEY_FILES:
        path = os.path.join(workspace_dir, kf)
        if os.path.isfile(path):
            try:
                with open(path, "r", errors="replace") as fh:
                    body = fh.read(_MAX_KEY_FILE_CHARS)
                key_contents.append(f"\n--- {kf} ---\n{body}")
            except Exception:
                pass

    tree = "\n".join(lines)
    keys = "\n".join(key_contents) if key_contents else ""
    if keys:
        keys = "\n## Key project files\n" + keys

    return tree + keys


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

    # ── Rate limit: max concurrent sessions per user ─────────
    user_session_count = await store.count_by_user(user_id)
    if user_session_count >= MAX_SESSIONS_PER_USER:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=429,
            detail=f"Session limit reached ({MAX_SESSIONS_PER_USER} concurrent sessions). "
                   f"Please stop an existing session before starting a new one.",
        )

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
    # NOTE: We do NOT create OpenHands Agent/LLM/Conversation anymore.
    # Claude Code SDK handles all task execution directly.
    # OpenHands objects were injecting system prompts that blocked Claude
    # from writing code ("MUST refuse to improve or augment the code").
    # Now the session only manages: workspace_dir + git clone + repo scan.

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
            import shutil
            shutil.rmtree(workspace_dir, ignore_errors=True)
            raise RuntimeError(
                f"Failed to clone repository. Please check:\n"
                f"• Repository URL is correct\n"
                f"• Branch '{branch or 'main'}' exists\n"
                f"• Git token has access to this repository\n"
                f"\nError: {exc}"
            ) from exc

    # ── Scan cloned repo for context ─────────────────────────
    repo_context = await asyncio.to_thread(_scan_workspace, workspace_dir)
    session.repo_context = repo_context
    logger.info("Workspace created at %s for session %s", workspace_dir, session_id)

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
