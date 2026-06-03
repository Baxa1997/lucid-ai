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
import re
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from postgrest.exceptions import APIError
from pydantic import BaseModel, Field

from app.auth import AuthenticatedUser, get_current_user
from app.config import logger, settings
from app.services.members import MembershipService
from app.supabase_client import db_client, managed_admin_client

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])


# Excluded paths when listing generated files — mirrors files.py so the two
# endpoints return the same shape of workspace tree.
_EXCLUDE_DIRS = {
    ".git", "node_modules", "__pycache__", ".next",
    ".venv", "venv", ".mypy_cache", ".pytest_cache",
    "dist", "build", ".tox", ".eggs",
}

_TENANT_TABLE_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_TENANT_COLUMN_RE = re.compile(r"^[a-z_][a-z0-9_]*$")
_SYSTEM_ROW_FIELDS = {"id", "created_at", "updated_at"}


class TenantRowPayload(BaseModel):
    payload: dict[str, Any] = Field(default_factory=dict)


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


def _normalize_data_model(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}


def _list_data_model_tables(data_model: dict[str, Any]) -> list[dict[str, Any]]:
    tables = data_model.get("tables")
    if not isinstance(tables, list):
        return []
    return [table for table in tables if isinstance(table, dict)]


def _get_table_definition(
    data_model: dict[str, Any],
    table_name: str,
) -> dict[str, Any] | None:
    if not _TENANT_TABLE_RE.fullmatch(table_name or ""):
        return None
    for table in _list_data_model_tables(data_model):
        if table.get("name") == table_name:
            return table
    return None


def _table_field_names(table: dict[str, Any]) -> set[str]:
    fields = table.get("fields")
    if not isinstance(fields, list):
        return set()
    names: set[str] = set()
    for field in fields:
        if isinstance(field, dict) and isinstance(field.get("name"), str):
            names.add(field["name"])
    return names


def _clean_payload_for_table(
    table: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Drop system columns and fields not declared in the project's DataModel."""
    if not isinstance(payload, dict):
        return {}
    allowed = _table_field_names(table)
    return {
        key: value
        for key, value in payload.items()
        if key in allowed and key not in _SYSTEM_ROW_FIELDS
    }


def _validate_order_by(table: dict[str, Any], order_by: str) -> str:
    if not _TENANT_COLUMN_RE.fullmatch(order_by or ""):
        raise HTTPException(status_code=400, detail="Invalid order column")
    allowed = _table_field_names(table) | _SYSTEM_ROW_FIELDS
    if order_by not in allowed:
        raise HTTPException(status_code=400, detail="Unknown order column")
    return order_by


def _require_user_jwt_for_tenant_rpc(user: AuthenticatedUser) -> str:
    """Tenant RPCs depend on auth.uid(), so they need the user's JWT."""
    if not user.raw_jwt:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="A user session token is required for project data edits",
        )
    return user.raw_jwt


async def _get_effective_data_model(project_id: str) -> dict[str, Any]:
    """Return the DataModel/tenant metadata used by this project.

    Linked admin panels keep membership on their own row but operate on
    the parent website's tenant schema and DataModel. This mirrors the
    database RPC behavior in migrations 027/028.
    """
    try:
        async with managed_admin_client() as client:
            res = (
                await client.table("chat_sessions")
                .select("id, parent_project_id, tenant_schema, data_model")
                .eq("id", project_id)
                .maybe_single()
                .execute()
            )
            project = (res.data if res else None) or {}
            if not project:
                raise HTTPException(status_code=404, detail="Project not found")

            parent_project_id = project.get("parent_project_id")
            effective = project
            if parent_project_id:
                parent_res = (
                    await client.table("chat_sessions")
                    .select("id, tenant_schema, data_model")
                    .eq("id", parent_project_id)
                    .maybe_single()
                    .execute()
                )
                effective = (parent_res.data if parent_res else None) or {}
                if not effective:
                    raise HTTPException(
                        status_code=409,
                        detail="Linked project parent was not found",
                    )
    except HTTPException:
        raise
    except APIError as exc:
        logger.error("Supabase error loading data model for %s: %s", project_id, exc)
        raise HTTPException(status_code=500, detail="Database error") from exc

    data_model = _normalize_data_model(effective.get("data_model"))
    return {
        "project_id": project_id,
        "parent_project_id": project.get("parent_project_id"),
        "effective_project_id": effective.get("id") or project_id,
        "tenant_schema": effective.get("tenant_schema"),
        "data_model": data_model,
        "tables": _list_data_model_tables(data_model),
    }


def _raise_tenant_rpc_error(exc: APIError) -> None:
    message = str(exc)
    lower = message.lower()
    if "access_denied" in lower or "insufficient_privilege" in lower:
        raise HTTPException(status_code=403, detail="Not authorized to edit this project data") from exc
    if "row_not_found" in lower:
        raise HTTPException(status_code=404, detail="Data row not found") from exc
    if "project_not_found" in lower:
        raise HTTPException(status_code=404, detail="Project data is not provisioned") from exc
    if "no_tenant" in lower:
        raise HTTPException(status_code=409, detail="Project data is not provisioned yet") from exc
    if "invalid_" in lower or "unknown_order" in lower:
        raise HTTPException(status_code=400, detail="Invalid project data request") from exc
    logger.error("Tenant data RPC failed: %s", exc)
    raise HTTPException(status_code=500, detail="Project data operation failed") from exc


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


# ── Project DataModel + tenant row CRUD ───────────────────────────────

@router.get("/{project_id}/data-model")
async def get_project_data_model(
    project_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Return the generated Supabase-backed collections for Settings > Data."""
    await _require_member(project_id, user)
    model_info = await _get_effective_data_model(project_id)
    return {
        **model_info,
        "provisioned": bool(model_info.get("tenant_schema")),
        "table_count": len(model_info.get("tables") or []),
    }


@router.get("/{project_id}/data/{table_name}")
async def list_project_data_rows(
    project_id: str,
    table_name: str,
    user: AuthenticatedUser = Depends(get_current_user),
    order_by: str = Query("created_at"),
    order_direction: str = Query("desc"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    """List rows from one generated collection.

    The actual read goes through the authenticated Supabase RPC so linked
    admin panels and per-project table allowlists match production runtime
    behavior.
    """
    await _require_member(project_id, user)
    user_jwt = _require_user_jwt_for_tenant_rpc(user)
    model_info = await _get_effective_data_model(project_id)
    table = _get_table_definition(model_info["data_model"], table_name)
    if table is None:
        raise HTTPException(status_code=404, detail="Project data table not found")
    order_by = _validate_order_by(table, order_by)
    direction = (order_direction or "").lower()
    if direction not in {"asc", "desc"}:
        raise HTTPException(status_code=400, detail="Invalid order direction")

    try:
        async with db_client(user_jwt) as client:
            res = await client.rpc(
                "get_tenant_collection_authenticated",
                {
                    "p_project_id": project_id,
                    "p_table_name": table_name,
                    "p_order_by": order_by,
                    "p_order_direction": direction,
                    "p_limit": limit,
                    "p_offset": offset,
                },
            ).execute()
        rows = res.data if isinstance(res.data, list) else []
        return {"table": table, "rows": rows, "limit": limit, "offset": offset}
    except APIError as exc:
        _raise_tenant_rpc_error(exc)


@router.post("/{project_id}/data/{table_name}", status_code=status.HTTP_201_CREATED)
async def create_project_data_row(
    project_id: str,
    table_name: str,
    body: TenantRowPayload,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Insert one row into a generated collection."""
    await _require_member(project_id, user)
    user_jwt = _require_user_jwt_for_tenant_rpc(user)
    model_info = await _get_effective_data_model(project_id)
    table = _get_table_definition(model_info["data_model"], table_name)
    if table is None:
        raise HTTPException(status_code=404, detail="Project data table not found")
    payload = _clean_payload_for_table(table, body.payload)
    if not payload:
        raise HTTPException(status_code=400, detail="No editable fields in payload")

    try:
        async with db_client(user_jwt) as client:
            res = await client.rpc(
                "set_tenant_row",
                {
                    "p_project_id": project_id,
                    "p_table_name": table_name,
                    "p_payload": payload,
                },
            ).execute()
        return {"row": res.data}
    except APIError as exc:
        _raise_tenant_rpc_error(exc)


@router.patch("/{project_id}/data/{table_name}/{row_id}")
async def update_project_data_row(
    project_id: str,
    table_name: str,
    row_id: str,
    body: TenantRowPayload,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Patch one row in a generated collection."""
    await _require_member(project_id, user)
    user_jwt = _require_user_jwt_for_tenant_rpc(user)
    try:
        parsed_row_id = str(uuid.UUID(row_id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid row id") from exc

    model_info = await _get_effective_data_model(project_id)
    table = _get_table_definition(model_info["data_model"], table_name)
    if table is None:
        raise HTTPException(status_code=404, detail="Project data table not found")
    payload = _clean_payload_for_table(table, body.payload)
    if not payload:
        raise HTTPException(status_code=400, detail="No editable fields in payload")

    try:
        async with db_client(user_jwt) as client:
            res = await client.rpc(
                "update_tenant_row",
                {
                    "p_project_id": project_id,
                    "p_table_name": table_name,
                    "p_row_id": parsed_row_id,
                    "p_payload": payload,
                },
            ).execute()
        return {"row": res.data}
    except APIError as exc:
        _raise_tenant_rpc_error(exc)


@router.delete("/{project_id}/data/{table_name}/{row_id}")
async def delete_project_data_row(
    project_id: str,
    table_name: str,
    row_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Delete one row from a generated collection."""
    await _require_member(project_id, user)
    user_jwt = _require_user_jwt_for_tenant_rpc(user)
    try:
        parsed_row_id = str(uuid.UUID(row_id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid row id") from exc

    model_info = await _get_effective_data_model(project_id)
    if _get_table_definition(model_info["data_model"], table_name) is None:
        raise HTTPException(status_code=404, detail="Project data table not found")

    try:
        async with db_client(user_jwt) as client:
            await client.rpc(
                "delete_tenant_row",
                {
                    "p_project_id": project_id,
                    "p_table_name": table_name,
                    "p_row_id": parsed_row_id,
                },
            ).execute()
        return {"success": True}
    except APIError as exc:
        _raise_tenant_rpc_error(exc)


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
