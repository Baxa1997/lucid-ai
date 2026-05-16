"""REST endpoints for project metadata, message history, and file lists.

A "project" here is a ``chat_sessions`` row plus the things hanging off it:
  • messages (chat_messages, member-readable)
  • members  (project_members, surfaced via the invites router elsewhere)
  • files    (the agent-generated workspace on disk, member-readable)

Auth: all endpoints require either a Supabase Auth JWT (Bearer) or the
X-Internal-Key path. Per-route authorization is membership-based —
not just ownership — so an invited editor can read project metadata,
messages, and files.
"""
from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from postgrest.exceptions import APIError

from app.auth import AuthenticatedUser, get_current_user
from app.config import logger, settings
from app.services.chat import _with_retry
from app.services.members import MembershipService
from app.supabase_client import managed_admin_client

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])


# Excluded paths when listing generated files — mirrors files.py so the two
# endpoints return the same shape of workspace tree.
_EXCLUDE_DIRS = {
    ".git", "node_modules", "__pycache__", ".next",
    ".venv", "venv", ".mypy_cache", ".pytest_cache",
    "dist", "build", ".tox", ".eggs",
}


# ── Shared auth helper ────────────────────────────────────────────────

async def _require_member(project_id: str, user: AuthenticatedUser) -> None:
    """Raise 404 if the project doesn't exist, 403 if the user isn't a member.

    We deliberately return 404 (not 403) for the project-missing case so
    we don't leak whether a chat_sessions row exists to non-members.
    """
    # Existence check first.
    try:
        async with managed_admin_client() as client:
            res = (
                await client.table("chat_sessions")
                .select("id")
                .eq("id", project_id)
                .maybe_single()
                .execute()
            )
        if not res or not res.data:
            raise HTTPException(status_code=404, detail="Project not found")
    except APIError as exc:
        logger.error("Supabase error in _require_member existence check: %s", exc)
        raise HTTPException(status_code=500, detail="Database error") from exc

    if not await MembershipService.is_member(project_id, user.user_id):
        raise HTTPException(status_code=403, detail="Not authorized to access this project")


# ── GET /api/v1/projects/{project_id} ─────────────────────────────────

@router.get("/{project_id}")
async def get_project(
    project_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Return everything the workspace page needs to render the header,
    sidebar, and Share dialog.

    Shape:
        {
          id, title, project_id, archetype, status,
          created_at, updated_at,
          owner: {user_id, email, name, avatar_url},
          members_count,
          latest_generation: {...} | null,
          preview_url: str | null
        }

    ``archetype`` and ``preview_url`` are best-effort: they're not first-class
    columns on ``chat_sessions`` — we surface them when present on the latest
    chat_messages row's ``metadata_json`` (set by the pipeline) and null
    otherwise. Don't promise them to the UI; the UI must tolerate null.
    """
    await _require_member(project_id, user)

    try:
        async with managed_admin_client() as client:
            session_res = (
                await client.table("chat_sessions")
                .select("id, title, project_id, model_provider, is_active, "
                        "created_at, updated_at")
                .eq("id", project_id)
                .maybe_single()
                .execute()
            )
            session = session_res.data if session_res else None
            if not session:
                # Race: row vanished between _require_member and here.
                raise HTTPException(status_code=404, detail="Project not found")

            # Owner row + profile.
            owner_id = await MembershipService.get_owner(project_id)
            owner_profile: dict[str, Any] = {}
            if owner_id:
                op = (
                    await client.table("users")
                    .select("id, email, name, avatar_url")
                    .eq("id", owner_id)
                    .maybe_single()
                    .execute()
                )
                owner_profile = (op.data if op else {}) or {}

            # Members count — service role, so we see all members.
            count_res = (
                await client.table("project_members")
                .select("user_id", count="exact")
                .eq("project_id", project_id)
                .execute()
            )
            members_count = getattr(count_res, "count", None)
            if members_count is None:
                members_count = len(count_res.data or [])

            # Latest pipeline event (best-effort). The chat_messages table
            # is append-only, so the last row by created_at is the most
            # recent state. event_type / metadata_json carry the
            # archetype + preview_url when the pipeline ran.
            last_event_res = (
                await client.table("chat_messages")
                .select("event_type, metadata_json, created_at")
                .eq("session_id", project_id)
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )
            last_event = (last_event_res.data or [None])[0]
    except HTTPException:
        raise
    except APIError as exc:
        logger.error("Supabase error in get_project: %s", exc)
        raise HTTPException(status_code=500, detail="Database error") from exc

    archetype = None
    preview_url = None
    latest_generation = None
    if last_event:
        meta = last_event.get("metadata_json") or {}
        archetype = meta.get("layout_archetype") or meta.get("archetype")
        preview_url = meta.get("preview_url") or meta.get("deploy_url")
        latest_generation = {
            "event_type": last_event.get("event_type"),
            "created_at": last_event.get("created_at"),
        }

    return {
        "id":              session["id"],
        "title":           session.get("title"),
        "project_id":      session.get("project_id"),
        "model_provider":  session.get("model_provider"),
        "is_active":       session.get("is_active"),
        "archetype":       archetype,
        "status":          "active" if session.get("is_active") else "inactive",
        "created_at":      session.get("created_at"),
        "updated_at":      session.get("updated_at"),
        "owner": {
            "user_id":    owner_profile.get("id") or owner_id,
            "email":      owner_profile.get("email"),
            "name":       owner_profile.get("name"),
            "avatar_url": owner_profile.get("avatar_url"),
        },
        "members_count":   members_count,
        "latest_generation": latest_generation,
        "preview_url":     preview_url,
    }


# ── GET /api/v1/projects/{project_id}/messages ────────────────────────

@router.get("/{project_id}/messages")
async def list_project_messages(
    project_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """Paginated chat history for the project, oldest first within the page.

    We sort by created_at ASC at the DB level so the UI can append a
    newer page on top without an extra reverse step. ``limit``/``offset``
    paginate from the OLDEST message; pass offset=N to skip the first N.
    To stream "load more" at the top of a chat, use the count + offset
    to compute the right slice.
    """
    await _require_member(project_id, user)

    try:
        async with managed_admin_client() as client:
            res = (
                await client.table("chat_messages")
                .select("id, role, content, event_type, metadata_json, created_at",
                        count="exact")
                .eq("session_id", project_id)
                .order("created_at", desc=False)
                .range(offset, offset + limit - 1)
                .execute()
            )
        return {
            "messages": [
                {
                    "id":         m["id"],
                    "role":       m["role"],
                    "content":    m["content"],
                    "event_type": m.get("event_type"),
                    "metadata":   m.get("metadata_json"),
                    "created_at": m.get("created_at"),
                }
                for m in (res.data or [])
            ],
            "total":  getattr(res, "count", None) or len(res.data or []),
            "limit":  limit,
            "offset": offset,
        }
    except APIError as exc:
        logger.error("Supabase error in list_project_messages: %s", exc)
        raise HTTPException(status_code=500, detail="Database error") from exc


# ── GET /api/v1/projects/{project_id}/files ───────────────────────────

@router.get("/{project_id}/files")
async def list_project_files(
    project_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Flat list of paths under the project's generated workspace.

    Returns ``[]`` when the workspace doesn't exist (generation hasn't run
    yet, or the workspace was reaped). This is intentionally non-fatal:
    every member sees the same answer regardless of whether the workspace
    is currently mounted.

    NOTE: workspace directories on disk live under the OWNER's user_id —
    we look that up from project_members rather than using the requesting
    user's id, so an invited member sees the same files the owner does.
    File CONTENTS are not returned here; the /api/v1/files/read endpoint
    handles content reads (still owner-scoped at the time of writing).
    """
    await _require_member(project_id, user)

    owner_id = await MembershipService.get_owner(project_id)
    if not owner_id:
        # No owner row — should be impossible after the trigger ran, but
        # don't crash; treat as empty workspace.
        return {"files": [], "exists": False}

    workspace_dir = os.path.join(settings.WORKSPACE_BASE_PATH, owner_id, project_id)
    if not os.path.isdir(workspace_dir):
        return {"files": [], "exists": False}

    paths: list[dict[str, Any]] = []
    try:
        for dirpath, dirnames, filenames in os.walk(workspace_dir):
            dirnames[:] = [
                d for d in dirnames
                if d not in _EXCLUDE_DIRS and not d.startswith(".")
            ]
            for filename in filenames:
                if filename.startswith("."):
                    continue
                full = os.path.join(dirpath, filename)
                rel = os.path.relpath(full, workspace_dir)
                try:
                    size = os.path.getsize(full)
                except OSError:
                    continue
                paths.append({"path": rel, "size": size})
    except OSError as exc:
        logger.warning("Workspace walk failed for %s: %s", workspace_dir, exc)

    paths.sort(key=lambda p: p["path"])
    return {"files": paths, "exists": True, "count": len(paths)}
