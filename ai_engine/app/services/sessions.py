"""Agent session lifecycle — store, create, destroy.

The ``AgentSession`` dataclass holds per-session SDK objects.
``SessionStore`` manages a hybrid store: in-memory dict (primary) backed by
Redis (persistence layer). Redis failures are silently swallowed so the
application degrades gracefully to in-memory-only mode.

On process restart, sessions not yet in memory are lazily recovered from Redis
the first time they are looked up (e.g. when a WebSocket reconnects).
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from app.config import logger, settings, EVENT_BUFFER_MAX_SIZE
from app.workspace_states import WorkspaceState

# Sessions expire after 2 hours of inactivity (production-safe)
SESSION_TTL_SECONDS = 2 * 60 * 60   # 2 hours
REAPER_INTERVAL_SECONDS = 2 * 60    # check every 2 minutes
MAX_SESSIONS_PER_USER = 3           # rate limit: max concurrent sessions
from app import sdk
from app.exceptions import SessionNotFoundError
from app.services.llm import resolve_llm
from app.services.vcs.git import clone_repo


def _safe_put(queue: asyncio.Queue, item) -> None:
    """Thread-safe helper to put an item into an asyncio queue."""
    try:
        queue.put_nowait(item)
    except asyncio.QueueFull:
        logger.warning(
            "Event buffer full (maxsize=%d) — dropping event: %.120s",
            queue.maxsize,
            str(item),
        )

# ── Session dataclass ───────────────────────────────────────

class AgentSession:
    """Encapsulates a single user's agent session."""

    __slots__ = (
        "session_id", "user_id", "task", "repo_url",
        "branch", "git_token", "repo_provider",
        "created_at", "last_active", "is_alive",
        "conversation", "workspace", "workspace_dir",
        "agent", "llm",
        "event_buffer", "container_id", "project_id",
        "repo_context", "last_agent_message",
        "workspace_state",  # current WorkspaceState — set only via workspace_states.transition()
        "pipeline_task",    # asyncio.Task | None — the currently running pipeline
        "ws_proxy",         # WebSocketProxy | None — detachable event publisher
        "sandbox_runner",   # SandboxRunner — swappable execution backend
        # FIFO queue of {text, images, editable_target, queued_at} dicts.
        # Mutated by _listen_for_stop when a new task arrives while one is
        # running, then drained by execute_task's completion path so the
        # user's follow-up edits never get silently dropped.
        "pending_tasks",
    )

    def __init__(
        self,
        session_id: str,
        user_id: str,
        task: str,
        repo_url: Optional[str] = None,
        branch: Optional[str] = None,
        git_token: Optional[str] = None,
        repo_provider: Optional[str] = None,
    ):
        self.session_id = session_id
        self.user_id = user_id
        self.task = task
        self.repo_url = repo_url
        self.branch = branch or "main"
        self.git_token = git_token
        self.repo_provider = (repo_provider or "").strip().lower()
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

        # Authoritative state — mutated only via workspace_states.transition()
        self.workspace_state: str = WorkspaceState.ENTRY

        # Pipeline task detached from the WebSocket lifecycle
        self.pipeline_task: Any = None   # asyncio.Task | None
        self.ws_proxy: Any = None        # WebSocketProxy | None

        # Execution backend — set by create_session() via create_runner()
        self.sandbox_runner: Any = None  # SandboxRunner

        # Pending tasks queued while a pipeline is running. Each entry is
        # ``{text, images, editable_target, queued_at}``. Drained after
        # the current pipeline completes — see AgentOrchestrator.execute_task.
        self.pending_tasks: list = []

    def touch(self) -> None:
        """Update last_active timestamp."""
        self.last_active = time.monotonic()

    def is_expired(self) -> bool:
        """Check if this session has been inactive for longer than TTL."""
        return (time.monotonic() - self.last_active) > SESSION_TTL_SECONDS


# ── Redis key helpers ────────────────────────────────────────

_KEY_SESSION = "lucid:session:{}"       # hash → JSON metadata
_KEY_USER_SESSIONS = "lucid:user:{}:sessions"  # set → session_ids

# Tag for encrypted token values in the Redis session blob. Git PATs are
# AES-encrypted at rest in the DB (integrations table) — the Redis copy must
# not be the one place they sit in plaintext.
_ENC_TOKEN_PREFIX = "enc:v1:"


def _encrypt_token(token: str | None) -> str | None:
    """Encrypt a git token for Redis persistence.

    Fail-closed: if encryption isn't possible (e.g. ENCRYPTION_KEY unset),
    the token is NOT persisted — losing a token on process restart is
    recoverable (it's re-resolved from integrations on reconnect), leaking
    it is not.
    """
    if not token:
        return token
    try:
        from app.services.crypto import encrypt
        enc = encrypt(token)
        return f"{_ENC_TOKEN_PREFIX}{enc.iv}:{enc.encrypted}"
    except Exception as exc:
        logger.warning("Session git_token encryption failed — token not persisted: %s", exc)
        return None


def _decrypt_token(value: str | None) -> str | None:
    """Reverse _encrypt_token(). Legacy plaintext values pass through."""
    if not value or not isinstance(value, str):
        return value
    if not value.startswith(_ENC_TOKEN_PREFIX):
        return value  # pre-encryption session blob — accept as-is
    try:
        from app.services.crypto import decrypt
        iv_hex, ct_hex = value[len(_ENC_TOKEN_PREFIX):].split(":", 1)
        return decrypt(ct_hex, iv_hex)
    except Exception as exc:
        logger.warning("Session git_token decryption failed — dropping token: %s", exc)
        return None


def _serialize(session: AgentSession) -> dict:
    """Return a JSON-serializable dict of the session's persistent metadata."""
    # Convert monotonic last_active to a wall-clock timestamp so it survives
    # process restart (time.monotonic() resets each run).
    last_active_wall = time.time() - (time.monotonic() - session.last_active)
    return {
        "session_id": session.session_id,
        "user_id": session.user_id,
        "task": session.task,
        "repo_url": session.repo_url,
        "repo_provider": session.repo_provider,
        "branch": session.branch,
        "git_token": _encrypt_token(session.git_token),
        "created_at": session.created_at.isoformat(),
        "last_active_wall": last_active_wall,
        "is_alive": session.is_alive,
        "project_id": session.project_id,
        "repo_context": session.repo_context,
        "last_agent_message": session.last_agent_message,
        "workspace_dir": session.workspace_dir,
        "workspace_state": session.workspace_state,
        "container_id": session.container_id,
    }


def _deserialize(data: dict) -> AgentSession:
    """Reconstruct an AgentSession from stored metadata."""
    session = AgentSession(
        session_id=data["session_id"],
        user_id=data["user_id"],
        task=data["task"],
        repo_url=data.get("repo_url"),
        repo_provider=data.get("repo_provider"),
        branch=data.get("branch", "main"),
        git_token=_decrypt_token(data.get("git_token")),
    )
    session.created_at = datetime.fromisoformat(data["created_at"])
    # Restore last_active as a monotonic value with the correct elapsed time.
    stored_wall: float = data.get("last_active_wall", time.time())
    elapsed = max(0.0, time.time() - stored_wall)
    session.last_active = time.monotonic() - elapsed

    session.is_alive = data.get("is_alive", True)
    session.project_id = data.get("project_id", "")
    session.repo_context = data.get("repo_context", "")
    session.last_agent_message = data.get("last_agent_message", "")
    session.workspace_dir = data.get("workspace_dir", "")
    session.workspace_state = data.get("workspace_state", WorkspaceState.ENTRY)
    session.container_id = data.get("container_id")
    return session


# ── Session store ────────────────────────────────────────────

class SessionStore:
    """Thread-safe session registry backed by in-memory dict + Redis.

    The in-memory dict is the primary store — all reads/writes hit it first.
    Redis is the persistence layer: metadata is written on every mutation so
    that sessions survive a process restart. Redis failures are silently
    logged and never propagate to callers.

    On restart, a session not yet in memory is lazily recovered from Redis
    the first time it is looked up.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, AgentSession] = {}
        self._lock = asyncio.Lock()

    # ── Redis helpers (private, never raise) ─────────────────

    async def _persist(self, session: AgentSession) -> None:
        from app.services.redis_client import get_redis
        redis = get_redis()
        if redis is None:
            return
        try:
            payload = json.dumps(_serialize(session))
            await redis.set(
                _KEY_SESSION.format(session.session_id),
                payload,
                ex=SESSION_TTL_SECONDS,
            )
            user_key = _KEY_USER_SESSIONS.format(session.user_id)
            await redis.sadd(user_key, session.session_id)
            # User-sessions set TTL = 2× session TTL so it outlives any session
            await redis.expire(user_key, SESSION_TTL_SECONDS * 2)
        except Exception as exc:
            logger.warning("Redis persist failed for %s: %s", session.session_id, exc)

    async def _delete_from_redis(self, session: AgentSession) -> None:
        from app.services.redis_client import get_redis
        redis = get_redis()
        if redis is None:
            return
        try:
            await redis.delete(_KEY_SESSION.format(session.session_id))
            await redis.srem(
                _KEY_USER_SESSIONS.format(session.user_id), session.session_id
            )
        except Exception as exc:
            logger.warning("Redis delete failed for %s: %s", session.session_id, exc)

    async def _recover(self, session_id: str) -> AgentSession | None:
        """Try to reconstruct a session from Redis after a process restart."""
        from app.services.redis_client import get_redis
        redis = get_redis()
        if redis is None:
            return None
        try:
            raw = await redis.get(_KEY_SESSION.format(session_id))
            if raw is None:
                return None
            return _deserialize(json.loads(raw))
        except Exception as exc:
            logger.warning("Redis recovery failed for %s: %s", session_id, exc)
            return None

    # ── Public interface ──────────────────────────────────────

    async def add(self, session: AgentSession) -> None:
        async with self._lock:
            self._sessions[session.session_id] = session
        await self._persist(session)

    async def get(self, session_id: str) -> AgentSession:
        async with self._lock:
            session = self._sessions.get(session_id)
        if session is not None:
            return session
        # Not in memory — attempt Redis recovery (e.g. after process restart)
        session = await self._recover(session_id)
        if session is None:
            raise SessionNotFoundError(session_id)
        async with self._lock:
            self._sessions[session.session_id] = session
        logger.info("Session %s recovered from Redis", session_id)
        return session

    async def get_or_none(self, session_id: str) -> AgentSession | None:
        async with self._lock:
            session = self._sessions.get(session_id)
        if session is not None:
            return session
        return await self._recover(session_id)

    async def pop(self, session_id: str) -> AgentSession | None:
        async with self._lock:
            session = self._sessions.pop(session_id, None)
        if session is not None:
            await self._delete_from_redis(session)
        return session

    async def contains(self, session_id: str) -> bool:
        async with self._lock:
            if session_id in self._sessions:
                return True
        # Fall back to Redis
        from app.services.redis_client import get_redis
        redis = get_redis()
        if redis is None:
            return False
        try:
            return bool(await redis.exists(_KEY_SESSION.format(session_id)))
        except Exception:
            return False

    async def list_all(self) -> list[AgentSession]:
        async with self._lock:
            return list(self._sessions.values())

    async def count(self) -> int:
        async with self._lock:
            return len(self._sessions)

    async def snapshot_ids(self) -> list[str]:
        async with self._lock:
            return list(self._sessions.keys())

    async def touch(self, session_id: str) -> None:
        """Refresh the Redis TTL for a session after in-memory activity."""
        async with self._lock:
            session = self._sessions.get(session_id)
        if session is not None:
            await self._persist(session)

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

    async def list_by_user(self, user_id: str) -> list[AgentSession]:
        """Return all live, non-expired sessions for a user, oldest first."""
        async with self._lock:
            xs = [
                s for s in self._sessions.values()
                if s.user_id == user_id and s.is_alive and not s.is_expired()
            ]
        xs.sort(key=lambda s: s.created_at)
        return xs


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
    repo_provider: str | None = None,
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
    # When the cap is hit, transparently reclaim slots from sessions whose
    # WebSocket has dropped (user closed the tab / navigated away). Their
    # pipelines were preserved on disconnect so an accidental tab-close
    # could resume; but if the user is starting a new prompt, that previous
    # workspace is abandoned — kill it instead of blocking the new session.
    # If all 3 still have a live WS attached, that's truly concurrent use:
    # surface the 429.
    user_sessions = await store.list_by_user(user_id)
    if len(user_sessions) >= MAX_SESSIONS_PER_USER:
        # Oldest-first: abandoned ones are likely the most stale anyway.
        reclaimable = [
            s for s in user_sessions
            if s.ws_proxy is None or not s.ws_proxy.is_attached
        ]
        if reclaimable:
            victim = reclaimable[0]
            logger.info(
                "Session cap hit for user %s — reclaiming abandoned session %s "
                "(WS detached, started %s)",
                user_id, victim.session_id, victim.created_at.isoformat(),
            )
            # destroy_session cancels the running pipeline (if any) first.
            try:
                await destroy_session(victim.session_id)
            except Exception as exc:
                logger.warning("Reclaim: destroy_session failed: %s", exc)
        else:
            from fastapi import HTTPException
            raise HTTPException(
                status_code=429,
                detail=(
                    f"Session limit reached ({MAX_SESSIONS_PER_USER} concurrent "
                    "sessions, all currently active in another tab). "
                    "Close one of the other workspaces to start a new prompt."
                ),
            )

    # ── Always create a real workspace ─────────────────────────
    # NOTE: The old mock gate (sdk.OPENHANDS_AVAILABLE) is removed.
    # The pipeline uses subprocess fallbacks for clone/push when
    # the OpenHands SDK is not installed.
    if not sdk.OPENHANDS_AVAILABLE:
        logger.info("OpenHands SDK not installed — workspace will use subprocess git")

    # ── Real path ────────────────────────────────────────────
    # NOTE: We do NOT create OpenHands Agent/LLM/Conversation anymore.
    # Claude Code SDK handles all task execution directly.
    # OpenHands objects were injecting system prompts that blocked Claude
    # from writing code ("MUST refuse to improve or augment the code").
    # Now the session only manages: workspace_dir + git clone + repo scan.

    # ── Resolve the workspace directory ──────────────────────
    # Shared-workspace model for collaboration: when this project_id
    # already has a built preview workspace on disk (created during a
    # prior pipeline run by the owner or another member), point this
    # session at it instead of creating an empty per-user dir.
    #
    # This is what makes invited members see the actual project — without
    # this hop, they'd land in WORKSPACE_BASE_PATH/<their-id>/<new-session>/
    # which is freshly created and empty, and the pipeline would treat
    # them as starting a new project from scratch.
    #
    # For first-time runs (no preview yet), we fall through to the
    # per-user dir; the pipeline later promotes the preview into
    # PREVIEW_WS_ROOT once it's built, and subsequent connects use it.
    from app.paths import preview_workspace_path

    workspace_dir = None
    if project_id:
        shared = preview_workspace_path(project_id)
        if os.path.isdir(shared):
            workspace_dir = shared
            logger.info(
                "Session %s attaching to shared workspace %s (project %s)",
                session_id, shared, project_id,
            )

    if workspace_dir is None:
        workspace_dir = os.path.join(
            settings.WORKSPACE_BASE_PATH, user_id, session_id
        )
        os.makedirs(workspace_dir, exist_ok=True)

    session = AgentSession(
        session_id=session_id,
        user_id=user_id,
        task=task,
        repo_url=repo_url,
        repo_provider=repo_provider,
        branch=branch,
        git_token=git_token,
    )
    session.workspace_dir = workspace_dir
    session.project_id = project_id or ""

    # ── Create the sandbox runner ────────────────────────────
    # LocalRunner is the default (no Docker required). DockerRunner can be
    # enabled per-session in the future via a settings flag or user preference.
    from app.services.sandbox import create_runner
    runner = create_runner(
        session_id=session_id,
        workspace_dir=workspace_dir,
        user_id=user_id,
        use_docker=False,
    )
    await runner.setup()
    session.sandbox_runner = runner

    # NOTE: Repo cloning is handled by workspace_manager.get_or_create_workspace()
    # inside run_pipeline(). We do NOT clone here to avoid:
    #   1. Double-cloning (once here, once in workspace_manager)
    #   2. Fatal failures — if clone fails here, the entire WS connection dies
    #      before the pipeline can even start. workspace_manager has better
    #      retry/recovery logic.

    # ── Scan workspace for context (will be empty if no clone) ──
    repo_context = ""
    if repo_url and os.path.exists(os.path.join(workspace_dir, ".git")):
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

    # Cancel any still-running pipeline BEFORE tearing down the sandbox or
    # workspace. Every destroy path (reaper, reclaim, explicit stop) goes
    # through here — without this, the TTL reaper could rip the filesystem
    # out from under an in-flight generation that keeps writing into it.
    pipeline_task = session.pipeline_task
    if pipeline_task is not None and not pipeline_task.done():
        pipeline_task.cancel()
        try:
            await asyncio.wait_for(pipeline_task, timeout=5)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        except Exception as exc:
            logger.warning(
                "destroy_session: pipeline cancellation for %s raised: %s",
                session_id, exc,
            )

    # Teardown the sandbox runner (no-op for LocalRunner, stops container for Docker)
    if session.sandbox_runner is not None:
        try:
            await session.sandbox_runner.teardown()
        except Exception as exc:
            logger.error("SandboxRunner teardown error for %s: %s", session_id, exc)

    if session.conversation and hasattr(session.conversation, "close"):
        try:
            await asyncio.to_thread(session.conversation.close)
            logger.info("Conversation closed for session %s", session_id)
        except Exception as exc:
            logger.error("Error closing conversation: %s", exc)

    # Clean up local workspace directory.
    # Skip when ANY of the following is true:
    #   1. Path is itself a preview workspace (lucid_ws_*) — shared, managed
    #      by local_preview; the dev server is still using it.
    #   2. Path is the SYMLINK TARGET of an existing preview workspace —
    #      i.e. the source was promoted via landing_pipeline. Deleting it
    #      would leave the preview_ws symlink dangling and the next reconnect
    #      finds an empty workspace ("Internal Server Error" on the iframe).
    #   3. Session has project_id set — code is persisted, the user can
    #      re-enter; the workspace_manager TTL reaper handles eventual cleanup.
    if session.workspace_dir and os.path.isdir(session.workspace_dir):
        from app.paths import is_preview_workspace, PREVIEW_WS_ROOT
        _wd = session.workspace_dir
        _wd_real = os.path.realpath(_wd)
        _is_promoted = False
        try:
            if os.path.isdir(PREVIEW_WS_ROOT):
                for _name in os.listdir(PREVIEW_WS_ROOT):
                    _link = os.path.join(PREVIEW_WS_ROOT, _name)
                    if os.path.islink(_link) and os.path.realpath(_link) == _wd_real:
                        _is_promoted = True
                        break
        except OSError:
            pass
        _has_project = bool(getattr(session, "project_id", "") or "")
        if is_preview_workspace(_wd) or _is_promoted or _has_project:
            logger.info(
                "destroy_session: keeping workspace %s (preview=%s promoted=%s project=%s)",
                _wd, is_preview_workspace(_wd), _is_promoted, _has_project,
            )
        else:
            shutil.rmtree(_wd, ignore_errors=True)


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
