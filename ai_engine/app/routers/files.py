"""File management endpoints for reading/listing workspace files."""

from __future__ import annotations

import os
import re

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth import AuthenticatedUser, get_current_user
from app.config import logger, settings
from app.services.members import MembershipService
from app.services.sessions import store
from app.supabase_client import managed_admin_client
from postgrest.exceptions import APIError

router = APIRouter(prefix="/api/v1/files", tags=["files"])


# ── Exclude patterns for file listing ────────────────────────

EXCLUDE_DIRS = {
    ".git", "node_modules", "__pycache__", ".next",
    ".venv", "venv", ".mypy_cache", ".pytest_cache",
    "dist", "build", ".tox", ".eggs",
}


@router.get("/read")
async def read_file(
    session_id: str = Query(...),
    path: str = Query(...),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Read a file from the agent's workspace."""
    workspace = await _resolve_workspace(session_id, user.user_id)

    workspace_norm = os.path.normpath(workspace)
    full_path = os.path.normpath(os.path.join(workspace, path.lstrip("/")))
    if not (full_path == workspace_norm or full_path.startswith(workspace_norm + os.sep)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Path traversal not allowed.",
        )
    if not os.path.isfile(full_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File not found: {path}",
        )
    try:
        with open(full_path, "r", errors="replace") as f:
            content = f.read()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to read file: {exc}",
        )

    return {"content": content}


@router.get("/list")
async def list_files(
    session_id: str = Query(...),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """List all files in the agent's workspace as a recursive tree."""
    workspace = await _resolve_workspace(session_id, user.user_id)
    tree = _build_local_file_tree(workspace)
    return {"tree": tree}


BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".svg",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".zip", ".tar", ".gz", ".rar",
    ".pdf", ".exe", ".dll", ".so", ".dylib",
    ".mp3", ".mp4", ".avi", ".mov", ".wav",
}

MAX_EXPORT_FILES = 200
MAX_FILE_SIZE = 512 * 1024  # 512KB per file


@router.get("/export")
async def export_files(
    session_id: str = Query(...),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Return all workspace files with content for export to external repo."""
    workspace = await _resolve_workspace(session_id, user.user_id)
    files = []

    for dirpath, dirnames, filenames in os.walk(workspace):
        # Skip excluded directories
        dirnames[:] = [
            d for d in dirnames
            if d not in EXCLUDE_DIRS and not d.startswith(".")
        ]

        for filename in filenames:
            if filename.startswith("."):
                continue
            full_path = os.path.join(dirpath, filename)
            rel_path = os.path.relpath(full_path, workspace)
            ext = os.path.splitext(filename)[1].lower()

            # Skip binary files
            if ext in BINARY_EXTENSIONS:
                continue

            # Skip files that are too large
            try:
                if os.path.getsize(full_path) > MAX_FILE_SIZE:
                    continue
            except OSError:
                continue

            try:
                with open(full_path, "r", errors="replace") as f:
                    content = f.read()
                files.append({"path": rel_path, "content": content})
            except Exception:
                continue

            if len(files) >= MAX_EXPORT_FILES:
                break
        if len(files) >= MAX_EXPORT_FILES:
            break

    return {"files": files, "count": len(files)}


# ── Shared helpers ───────────────────────────────────────────

async def _lookup_project_from_agent_session(agent_session_id: str) -> tuple[str | None, str | None]:
    """Map an agent session_id back to (chat_sessions.id, owner_user_id).

    The agent session_id lives on chat_sessions.agent_session_id; the
    chat_sessions row itself carries the canonical project_id (its UUID
    PK) and the owner user_id. Returns (None, None) when no row exists.
    """
    try:
        async with managed_admin_client() as client:
            res = (
                await client.table("chat_sessions")
                .select("id, user_id")
                .eq("agent_session_id", agent_session_id)
                .limit(1)
                .execute()
            )
        rows = res.data or []
        if not rows:
            return None, None
        return rows[0].get("id"), rows[0].get("user_id")
    except APIError as exc:
        logger.warning("chat_sessions lookup by agent_session_id failed: %s", exc)
        return None, None


async def _resolve_workspace(session_id: str, user_id: str) -> str:
    """Return the workspace directory for a session.

    Authorization: project membership (not raw ownership). An invited
    editor reads file contents through the same path as the owner.

    Resolution flow:
      1. Live session in store: use its project_id for the membership
         check, and its workspace_dir for the path. The owner-of-record
         is ``session.user_id``.
      2. Session reaped: look up chat_sessions by ``agent_session_id``
         to recover (project_id, owner_user_id), then check membership
         and reconstruct the path under the OWNER's id (workspaces live
         on disk keyed by the owner — using the requester's id would
         break for invited members).
    """
    async def _deny_unless_member(project_id: str | None) -> None:
        """403 unless ``user_id`` is a member of ``project_id``."""
        if not project_id:
            # We couldn't tie this session to a project. Without a
            # project_id there's nothing membership-y to check against,
            # so block the request rather than silently allow it.
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to access this session.",
            )
        if not await MembershipService.is_member(project_id, user_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to access this session.",
            )

    # 1. Live session in the in-memory / Redis store.
    session = await store.get_or_none(session_id)
    if session is not None:
        # Prefer the project_id stored on the live session — it was set
        # at session creation time. If it's missing (older sessions), fall
        # back to a chat_sessions lookup by agent_session_id.
        project_id = session.project_id
        if not project_id:
            project_id, _ = await _lookup_project_from_agent_session(session_id)
        await _deny_unless_member(project_id)
        if session.workspace_dir:
            return session.workspace_dir
        # No workspace_dir on the live session? Fall through to the
        # disk path below, using the session's owner.
        owner_id = session.user_id

    # 2. Disk fallback — session has been reaped.
    else:
        project_id, owner_id = await _lookup_project_from_agent_session(session_id)
        await _deny_unless_member(project_id)
        if not owner_id:
            # We somehow have a member-authorized project with no owner
            # row. Treat as gone, not authorization failure.
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session {session_id} not found.",
            )

    workspace_dir = os.path.join(settings.WORKSPACE_BASE_PATH, owner_id, session_id)
    if not os.path.isdir(workspace_dir):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {session_id} not found.",
        )
    return workspace_dir


async def build_file_tree(session) -> list[dict]:
    """Build a file tree for the session's workspace."""
    if session.workspace_dir:
        return _build_local_file_tree(session.workspace_dir)
    return []


def _build_local_file_tree(root_dir: str) -> list[dict]:
    """Build a file tree from a local directory."""

    def walk_dir(dir_path: str) -> list[dict]:
        entries = []
        try:
            items = sorted(os.listdir(dir_path))
        except PermissionError:
            return entries

        for item in items:
            full_path = os.path.join(dir_path, item)
            rel_path = os.path.relpath(full_path, root_dir)

            if os.path.isdir(full_path):
                if item in EXCLUDE_DIRS or item.startswith("."):
                    continue
                entries.append({
                    "name": item,
                    "type": "folder",
                    "path": "/" + rel_path,
                    "children": walk_dir(full_path),
                })
            else:
                entries.append({
                    "name": item,
                    "type": "file",
                    "path": "/" + rel_path,
                })

        return entries

    return walk_dir(root_dir)


# ── File-change detection (used by WS streaming) ────────────

_FILE_CHANGE_COMMANDS = re.compile(
    r"\b(touch|mkdir|rm|rmdir|mv|cp|git\s+clone|git\s+checkout|"
    r"git\s+pull|wget|curl\s+-[oO]|unzip|tar|npm\s+init|pip\s+install|"
    r"npx|create-react-app|tee|dd|install)\b",
    re.IGNORECASE,
)

_FILE_CHANGE_EVENT_TYPES = {
    "FileWriteAction", "FileWriteObservation",
    "FileEditAction", "FileEditObservation",
    "FileCreateAction", "FileCreateObservation",
    "FileDeleteAction", "FileDeleteObservation",
    "CmdRunAction",
}


def should_refresh_file_tree(event_data: dict) -> bool:
    """Determine if an agent event indicates the workspace file tree changed."""
    event_type = event_data.get("eventType", "")

    if event_type in _FILE_CHANGE_EVENT_TYPES and event_type != "CmdRunAction":
        return True

    command = event_data.get("command", "") or event_data.get("content", "")
    if command and _FILE_CHANGE_COMMANDS.search(command):
        return True

    return False
