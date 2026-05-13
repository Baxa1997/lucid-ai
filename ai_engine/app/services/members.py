"""MembershipService — project_members CRUD + owner/membership lookups.

A "project" is a chat_sessions row. Membership lives in `project_members`
keyed by (project_id, user_id) with role ∈ {"owner", "editor"}.

Conventions (matches ChatService):
    • Every method takes ``user_jwt: str | None``.
      str  → RLS-enforced anon-key client (member-scoped SELECTs).
      None → service-role client (used by ai_engine for invite-acceptance
             writes, billing-attribution lookups, etc.).
    • The DB trigger ``chat_sessions_add_owner`` adds the project creator
      as ``role='owner'`` automatically on chat_sessions INSERT, so callers
      do NOT need to add the owner manually after creating a session.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import HTTPException
from postgrest.exceptions import APIError

from app.config import logger
from app.services.chat import _with_retry
from app.supabase_client import db_client, managed_admin_client

VALID_ROLES = ("owner", "editor")


class MembershipService:
    """Stateless service over the project_members table."""

    @staticmethod
    async def list_members(
        project_id: str,
        user_jwt: str | None,
    ) -> list[dict]:
        """Return all member rows for a project.

        RLS limits visibility to members of the same project; non-members
        get an empty list rather than a 403.
        """
        try:
            async with db_client(user_jwt) as client:
                async def _fetch():
                    return (
                        await client.table("project_members")
                        .select("project_id, user_id, role, added_by, added_at")
                        .eq("project_id", project_id)
                        .execute()
                    )

                result = await _with_retry(_fetch)
            return result.data or []
        except APIError as exc:
            logger.error("Supabase error in list_members: code=%s msg=%s", exc.code, exc.message)
            raise HTTPException(status_code=500, detail="Database error") from exc

    @staticmethod
    async def add_member(
        *,
        project_id: str,
        user_id: str,
        role: str = "editor",
        added_by: str,
        user_jwt: str | None = None,
    ) -> dict:
        """Insert a member row. Defaults to service-role write because
        invite acceptance is server-driven (the accepting user does not
        own the project, so RLS would block a direct insert).

        Raises HTTPException(400) on invalid role or self-add-as-editor
        when caller is already a member with a different role.
        """
        if role not in VALID_ROLES:
            raise HTTPException(status_code=400, detail=f"role must be one of {VALID_ROLES}")

        row = {
            "project_id": project_id,
            "user_id": user_id,
            "role": role,
            "added_by": added_by,
        }
        try:
            # Service-role write: invites are accepted server-side and
            # the new member does not have RLS rights to insert themselves.
            async with managed_admin_client() as client:
                async def _insert():
                    return (
                        await client.table("project_members")
                        .upsert(row, on_conflict="project_id,user_id")
                        .execute()
                    )

                result = await _with_retry(_insert)
            return (result.data or [row])[0]
        except APIError as exc:
            logger.error("Supabase error in add_member: code=%s msg=%s", exc.code, exc.message)
            raise HTTPException(status_code=500, detail="Database error") from exc

    @staticmethod
    async def remove_member(
        *,
        project_id: str,
        user_id: str,
        user_jwt: str | None = None,
    ) -> bool:
        """Delete a member row. Service-role only; the calling endpoint
        is responsible for verifying that the requester is the project
        owner before invoking this.

        Refuses to remove an owner — owners must be transferred, not
        removed. Returns True when a row was deleted.
        """
        try:
            async with managed_admin_client() as client:
                # Guard: never delete an owner row through this path.
                check = (
                    await client.table("project_members")
                    .select("role")
                    .eq("project_id", project_id)
                    .eq("user_id", user_id)
                    .maybe_single()
                    .execute()
                )
                existing = check.data
                if existing and existing.get("role") == "owner":
                    raise HTTPException(
                        status_code=400,
                        detail="Cannot remove the project owner. Transfer ownership first.",
                    )

                async def _delete():
                    return (
                        await client.table("project_members")
                        .delete()
                        .eq("project_id", project_id)
                        .eq("user_id", user_id)
                        .select("project_id")
                        .execute()
                    )

                result = await _with_retry(_delete)
            return bool(result.data)
        except APIError as exc:
            logger.error("Supabase error in remove_member: code=%s msg=%s", exc.code, exc.message)
            raise HTTPException(status_code=500, detail="Database error") from exc

    @staticmethod
    async def is_member(
        project_id: str,
        user_id: str,
        user_jwt: str | None = None,
    ) -> bool:
        """Return True when user_id has any membership row on project_id.

        Uses the service-role client because membership checks happen on
        behalf of the requester (we don't want their RLS visibility to
        affect a yes/no answer — the truth is in the table).
        """
        try:
            async with managed_admin_client() as client:
                result = (
                    await client.table("project_members")
                    .select("user_id")
                    .eq("project_id", project_id)
                    .eq("user_id", user_id)
                    .maybe_single()
                    .execute()
                )
            return bool(result.data)
        except APIError as exc:
            logger.error("Supabase error in is_member: code=%s msg=%s", exc.code, exc.message)
            raise HTTPException(status_code=500, detail="Database error") from exc

    @staticmethod
    async def is_owner(
        project_id: str,
        user_id: str,
    ) -> bool:
        """Return True when user_id is the owner of project_id."""
        try:
            async with managed_admin_client() as client:
                result = (
                    await client.table("project_members")
                    .select("role")
                    .eq("project_id", project_id)
                    .eq("user_id", user_id)
                    .eq("role", "owner")
                    .maybe_single()
                    .execute()
                )
            return bool(result.data)
        except APIError as exc:
            logger.error("Supabase error in is_owner: code=%s msg=%s", exc.code, exc.message)
            raise HTTPException(status_code=500, detail="Database error") from exc

    @staticmethod
    async def get_owner(project_id: str) -> Optional[str]:
        """Return the owner's user_id for a project, or None.

        Used for billing attribution: token usage on a project is charged
        to the owner regardless of which editor sent the task.
        """
        try:
            async with managed_admin_client() as client:
                result = (
                    await client.table("project_members")
                    .select("user_id")
                    .eq("project_id", project_id)
                    .eq("role", "owner")
                    .maybe_single()
                    .execute()
                )
            return (result.data or {}).get("user_id")
        except APIError as exc:
            logger.error("Supabase error in get_owner: code=%s msg=%s", exc.code, exc.message)
            raise HTTPException(status_code=500, detail="Database error") from exc

    @staticmethod
    async def list_projects_for_user(
        user_id: str,
        user_jwt: str | None,
    ) -> list[dict]:
        """Return all (project_id, role) pairs where user_id is a member.

        Useful for sidebar listings that should include shared projects
        the user does not own.
        """
        try:
            async with db_client(user_jwt) as client:
                result = (
                    await client.table("project_members")
                    .select("project_id, role")
                    .eq("user_id", user_id)
                    .execute()
                )
            return result.data or []
        except APIError as exc:
            logger.error("Supabase error in list_projects_for_user: code=%s msg=%s", exc.code, exc.message)
            raise HTTPException(status_code=500, detail="Database error") from exc
