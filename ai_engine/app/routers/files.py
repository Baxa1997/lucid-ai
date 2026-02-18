"""File management endpoints for reading/listing workspace files."""

from __future__ import annotations

import os
import re
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
    session = await _get_session_for_user(session_id, user.user_id)

    # Prevent path traversal
    if ".." in path:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Path traversal not allowed.",
        )

    workspace = session.workspace

    if isinstance(workspace, str):
        # Local workspace — read from filesystem
        full_path = os.path.join(workspace, path.lstrip("/"))
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
    elif session.container_id:
        # Docker workspace — exec cat inside container
        safe_path = path.replace('"', '\\"')
        exit_code, content = await docker_manager.exec_command(
            session_id, f'cat "{safe_path}"',
        )
        if exit_code != 0:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"File not found or unreadable: {path}",
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Session has no workspace.",
        )

    return {"content": content}


@router.get("/list")
async def list_files(
    session_id: str = Query(...),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """List all files in the agent's workspace as a recursive tree."""
    session = await _get_session_for_user(session_id, user.user_id)
    tree = await build_file_tree(session)
    return {"tree": tree}


# ── Shared helpers ───────────────────────────────────────────

async def _get_session_for_user(session_id: str, user_id: str):
    """Get session, verifying ownership."""
    if not await store.contains(session_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {session_id} not found.",
        )
    session = await store.get(session_id)
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
    """Build a file tree by running `find` inside the Docker container."""
    root = settings.WORKSPACE_MOUNT_PATH

    exclude = (
        "-name .git -prune -o "
        "-name node_modules -prune -o "
        "-name __pycache__ -prune -o "
        "-name .next -prune -o "
        "-name .venv -prune -o "
        "-name venv -prune -o "
    )

    # Get all paths
    exit_code, raw_output = await docker_manager.exec_command(
        session_id,
        f"find {root} {exclude}-print 2>/dev/null | sort",
    )
    if exit_code != 0:
        return []

    lines = [l.strip() for l in raw_output.strip().split("\n") if l.strip()]
    lines = [l for l in lines if l and l != root]

    if not lines:
        return []

    # Get directories
    dir_exit, dir_output = await docker_manager.exec_command(
        session_id,
        f"find {root} {exclude}-type d -print 2>/dev/null",
    )
    dir_set = set()
    if dir_exit == 0:
        for d in dir_output.strip().split("\n"):
            d = d.strip()
            if d.startswith(root):
                dir_set.add(d[len(root):].lstrip("/"))
            elif d:
                dir_set.add(d.lstrip("/"))

    # Build tree from flat paths
    tree_root: dict[str, Any] = {"children": {}}

    for line in lines:
        if line.startswith(root):
            rel = line[len(root):].lstrip("/")
        else:
            rel = line.lstrip("/")

        if not rel:
            continue

        parts = rel.split("/")
        current = tree_root

        for part in parts:
            if part not in current["children"]:
                current["children"][part] = {
                    "name": part,
                    "children": {},
                }
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
