"""File management endpoints for reading/listing workspace files."""

from __future__ import annotations

import os
import re
import shlex
import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth import AuthenticatedUser, get_current_user
from app.config import logger, settings
from app.services.sessions import store
from app.services.docker_workspace import docker_manager

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


# ── Shared helpers ───────────────────────────────────────────

async def _resolve_workspace(session_id: str, user_id: str) -> str:
    """Return the workspace directory for a session.

    First checks the live session store. If the session is gone (completed/
    destroyed), falls back to constructing the expected on-disk path so that
    file reads still work after the WebSocket closes.
    """
    session = await store.get_or_none(session_id)
    if session is not None:
        if session.user_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to access this session.",
            )
        if isinstance(session.workspace, str):
            return session.workspace

    # Session gone — reconstruct path from disk convention
    workspace_dir = os.path.join(settings.WORKSPACE_BASE_PATH, user_id, session_id)
    if not os.path.isdir(workspace_dir):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {session_id} not found.",
        )
    return workspace_dir


# Kept for backwards compatibility (used by events.py)
async def _get_session_for_user(session_id: str, user_id: str):
    session = await store.get_or_none(session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {session_id} not found.",
        )
    if session.user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this session.",
        )
    return session


async def build_file_tree(session) -> list[dict]:
    """Build a file tree for the session's workspace.

    Works for both local directories and Docker containers.
    """
    workspace = session.workspace

    if isinstance(workspace, str):
        return _build_local_file_tree(workspace)
    elif session.container_id:
        return await _build_docker_file_tree(session.session_id)
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


async def _build_docker_file_tree(session_id: str) -> list[dict]:
    """Build a file tree by running `find` inside the Docker container.

    Uses a single find call with -printf '%y\\t%p\\n' to get both the
    entry type (d/f) and path in one Docker exec round-trip.
    """
    root = settings.WORKSPACE_MOUNT_PATH

    exclude = (
        "-name .git -prune -o "
        "-name node_modules -prune -o "
        "-name __pycache__ -prune -o "
        "-name .next -prune -o "
        "-name .venv -prune -o "
        "-name venv -prune -o "
    )

    # Single call: output "<type>\t<path>" per entry (GNU find, available on Debian/Ubuntu images)
    exit_code, raw_output = await docker_manager.exec_command(
        session_id,
        f"find {root} {exclude}-printf '%y\\t%p\\n' 2>/dev/null | sort -t'\\t' -k2",
    )
    if exit_code != 0:
        return []

    dir_set: set[str] = set()
    lines: list[str] = []

    for raw_line in raw_output.splitlines():
        raw_line = raw_line.strip()
        if not raw_line or "\t" not in raw_line:
            continue
        entry_type, entry_path = raw_line.split("\t", 1)
        if entry_path == root:
            continue
        rel = entry_path[len(root):].lstrip("/") if entry_path.startswith(root) else entry_path.lstrip("/")
        if not rel:
            continue
        lines.append(rel)
        if entry_type == "d":
            dir_set.add(rel)

    if not lines:
        return []

    # Build tree from flat relative paths
    tree_root: dict[str, Any] = {"children": {}}

    for rel in lines:
        parts = rel.split("/")
        current = tree_root
        for part in parts:
            if part not in current["children"]:
                current["children"][part] = {"name": part, "children": {}}
            current = current["children"][part]

    def convert(node: dict, parent_path: str = "") -> list[dict]:
        result_list = []
        for name, child in sorted(node["children"].items()):
            rel_path = f"{parent_path}/{name}" if parent_path else name
            full_path = f"{root}/{rel_path}"
            if child["children"] or rel_path in dir_set:
                result_list.append({
                    "name": name,
                    "type": "folder",
                    "path": full_path,
                    "children": convert(child, rel_path),
                })
            else:
                result_list.append({
                    "name": name,
                    "type": "file",
                    "path": full_path,
                })
        return result_list

    return convert(tree_root)


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
