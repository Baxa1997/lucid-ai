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

import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import HTTPException
from postgrest.exceptions import APIError

from app.config import logger
from app.services.chat import _with_retry
from app.supabase_client import db_client, managed_admin_client

VALID_ROLES = ("owner", "editor")

# RFC-5322-lite — good enough for "is this remotely an email" gating.
# We deliberately do NOT validate the deliverability or MX; Supabase
# will bounce nonexistent addresses when it tries to send the magic
# link, and the user sees the bounce in their dashboard.
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

# Invite tokens are 32 hex chars (16 raw bytes) — see secrets.token_hex(16).
# Long enough that brute-force enumeration is infeasible, short enough that
# the resulting accept-invite URL fits comfortably in an email body.
_INVITE_TOKEN_BYTES = 16

# How long a fresh invite stays acceptable. 7 days mirrors GitHub's default
# and is long enough to survive a holiday weekend.
_INVITE_TTL_DAYS = 7


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
                # `maybe_single()` may return None (not {data: None}) when
                # no row matches, depending on postgrest-py version — guard.
                existing = check.data if check else None
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

                # Clean up orphan chat_sessions the removed user owns for the
                # same project slug. Background: before resolve_shared_session
                # landed, an invited user opening /workspace/<slug> would spawn
                # a fresh chat_sessions row under their own user_id (instead of
                # reusing the owner's). Those orphan rows survive after the
                # member is removed and keep appearing on the user's dashboard
                # as "New workspace session" cards — exactly the bug we want
                # to close. Look up the slug from the owner's session, then
                # nuke any session the removed user has at that slug. cascade
                # handles chat_messages / project_members(self) cleanup.
                try:
                    slug_lookup = (
                        await client.table("chat_sessions")
                        .select("project_id")
                        .eq("id", project_id)
                        .maybe_single()
                        .execute()
                    )
                    slug_data = slug_lookup.data if slug_lookup else None
                    slug = slug_data.get("project_id") if slug_data else None
                    if slug:
                        orphan_r = await (
                            client.table("chat_sessions")
                            .delete()
                            .eq("user_id", user_id)
                            .eq("project_id", slug)
                            .select("id")
                            .execute()
                        )
                        if orphan_r.data:
                            logger.info(
                                "remove_member: cleaned %d orphan session(s) "
                                "for user %s at slug %s",
                                len(orphan_r.data), user_id, slug,
                            )
                except Exception as _orphan_err:
                    # Non-fatal — the primary removal already succeeded. Log
                    # and move on so the API still reports success.
                    logger.warning(
                        "remove_member: orphan cleanup failed for user %s "
                        "on project %s: %s",
                        user_id, project_id, _orphan_err,
                    )
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
            return bool(result and result.data)
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
            return bool(result and result.data)
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
            return ((result.data if result else None) or {}).get("user_id")
        except APIError as exc:
            logger.error("Supabase error in get_owner: code=%s msg=%s", exc.code, exc.message)
            raise HTTPException(status_code=500, detail="Database error") from exc

    @staticmethod
    async def resolve_shared_session(
        project_slug: str,
        current_user_id: str,
    ) -> Optional[dict]:
        """Locate the OWNER's chat_session for a project the current user joined via invite.

        chat_sessions.project_id is a public URL slug — multiple rows (one per
        user who ever opened that slug) can share it. project_members.project_id
        is the chat_sessions UUID PK. So when an invited member opens
        /workspace/<slug>, we need to find the chat_sessions row that:
          • has chat_sessions.project_id == slug
          • is owned by someone else (user_id != current_user_id)
          • lists current_user_id in project_members for that PK

        Returns ``{"session_id": <uuid>, "owner_user_id": <uuid>}`` for the
        oldest matching row (the original session), or None when the current
        user has no shared session under that slug — caller falls back to the
        per-user session lookup in that case.
        """
        try:
            async with managed_admin_client() as client:
                # Sessions with this slug NOT owned by current user, oldest first.
                sess_r = await (
                    client.table("chat_sessions")
                    .select("id, user_id, created_at")
                    .eq("project_id", project_slug)
                    .neq("user_id", current_user_id)
                    .order("created_at", desc=False)
                    .execute()
                )
                candidates = sess_r.data or []
                if not candidates:
                    return None
                candidate_ids = [s["id"] for s in candidates]
                # Filter to ones where current user is actually a member.
                mem_r = await (
                    client.table("project_members")
                    .select("project_id")
                    .eq("user_id", current_user_id)
                    .in_("project_id", candidate_ids)
                    .execute()
                )
                member_pks = {r["project_id"] for r in (mem_r.data or [])}
                for s in candidates:
                    if s["id"] in member_pks:
                        return {"session_id": s["id"], "owner_user_id": s["user_id"]}
                return None
        except APIError as exc:
            logger.error(
                "Supabase error in resolve_shared_session: code=%s msg=%s",
                exc.code, exc.message,
            )
            return None

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


# ── InvitationService ─────────────────────────────────────────────────────
#
# Email-based invitations layered on top of project_members. Each invite is
# (project, email, token) — independent of whether the invitee already has a
# Lucid account. When they click the magic link Supabase Auth sends them, the
# frontend trades the token for membership via accept_invite().
#
# Permissions: only the project owner may create / revoke / list invites. The
# invitee identifies themselves by holding a JWT whose email matches the
# row's invitee_email (case-insensitive). Verifying the email server-side
# (not just the token) prevents anyone with a leaked URL from grabbing
# someone else's invite.

class InvitationService:
    """Stateless service over the project_invites table.

    All writes go through the service-role client. RLS protects READS for
    UI use cases (owner list + invitee list); the API endpoints layer
    on the ownership / membership checks the policies cannot express
    without coupling to the requesting user.
    """

    # ── Helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def _is_valid_email(email: str) -> bool:
        return bool(email and _EMAIL_RE.match(email))

    @staticmethod
    def _is_expired(row: dict) -> bool:
        """True when the invite's expires_at is in the past.

        We don't currently run a cron to flip status='expired' — instead
        every read path checks expires_at and treats stale rows as 410.
        """
        exp = row.get("expires_at")
        if not exp:
            return False
        # Supabase returns ISO 8601 with timezone; normalize to aware UTC.
        if isinstance(exp, str):
            # Postgrest returns e.g. "2026-05-22T10:30:00+00:00".
            exp_dt = datetime.fromisoformat(exp.replace("Z", "+00:00"))
        else:
            exp_dt = exp
        return exp_dt < datetime.now(timezone.utc)

    # ── Create ──────────────────────────────────────────────────────────

    @staticmethod
    async def create_invite(
        *,
        project_id: str,
        inviter_id: str,
        invitee_email: str,
    ) -> dict:
        """Issue a fresh invite for ``invitee_email`` on ``project_id``.

        Validates:
          • inviter is the project owner
          • email is syntactically valid
          • invitee is not already a project member (by email lookup)
          • no other pending invite exists for the same (project, email)

        Returns the inserted row: {id, project_id, invitee_email, token,
        status, expires_at, created_at}.
        """
        if not InvitationService._is_valid_email(invitee_email):
            raise HTTPException(status_code=400, detail="Invalid email format")

        # Owner check — invites are owner-only.
        if not await MembershipService.is_owner(project_id, inviter_id):
            raise HTTPException(status_code=403, detail="Only the project owner can send invites")

        normalized_email = invitee_email.strip().lower()

        try:
            async with managed_admin_client() as client:
                # Reject if a public.users row with this email is already
                # a project_members entry. We look up the email in users
                # first (case-insensitive); if present and a member, 409.
                user_row = (
                    await client.table("users")
                    .select("id")
                    .ilike("email", normalized_email)
                    .maybe_single()
                    .execute()
                )
                existing_user_id = ((user_row.data if user_row else None) or {}).get("id")
                if existing_user_id:
                    member_check = (
                        await client.table("project_members")
                        .select("user_id")
                        .eq("project_id", project_id)
                        .eq("user_id", existing_user_id)
                        .maybe_single()
                        .execute()
                    )
                    if member_check and member_check.data:
                        raise HTTPException(
                            status_code=409,
                            detail="This person is already a member of the project",
                        )

                # Reject if a pending invite already exists. The DB has a
                # partial UNIQUE index that would also block this, but a
                # pre-check produces a friendlier error than a 23505.
                pending = (
                    await client.table("project_invites")
                    .select("id")
                    .eq("project_id", project_id)
                    .ilike("invitee_email", normalized_email)
                    .eq("status", "pending")
                    .limit(1)
                    .execute()
                )
                if pending.data:
                    raise HTTPException(
                        status_code=409,
                        detail="An invitation is already pending for this email",
                    )

                expires_at = datetime.now(timezone.utc) + timedelta(days=_INVITE_TTL_DAYS)
                row = {
                    "project_id":    project_id,
                    "inviter_id":    inviter_id,
                    "invitee_email": normalized_email,
                    "token":         secrets.token_hex(_INVITE_TOKEN_BYTES),
                    "status":        "pending",
                    "expires_at":    expires_at.isoformat(),
                }

                async def _insert():
                    return (
                        await client.table("project_invites")
                        .insert(row)
                        .execute()
                    )
                result = await _with_retry(_insert)
            inserted = (result.data or [row])[0]
            return inserted
        except HTTPException:
            raise
        except APIError as exc:
            # 23505 = unique_violation — race with the partial unique index.
            if getattr(exc, "code", None) == "23505":
                raise HTTPException(
                    status_code=409,
                    detail="An invitation is already pending for this email",
                ) from exc
            logger.error("Supabase error in create_invite: code=%s msg=%s", exc.code, exc.message)
            raise HTTPException(status_code=500, detail="Database error") from exc

    # ── Accept ──────────────────────────────────────────────────────────

    @staticmethod
    async def accept_invite(
        *,
        token: str,
        accepting_user_id: str,
        accepting_email: str,
    ) -> dict:
        """Trade ``token`` for membership in the invited project.

        Verifies:
          • token exists and status == 'pending'
          • not expired (expires_at > now())
          • accepting_email matches invitee_email (case-insensitive)

        On success: inserts project_members(role='editor') and flips the
        invite to status='accepted'. Returns
        ``{project_id, project_slug, project_title}`` so the caller can
        redirect into the project workspace, whose route key is the
        chat_sessions.project_id text slug (NOT the UUID PK).

        Errors:
          • 404 — token not found
          • 410 — expired or revoked
          • 403 — email mismatch
          • 409 — already accepted
        """
        if not token:
            raise HTTPException(status_code=404, detail="Invite not found")

        try:
            async with managed_admin_client() as client:
                # Look up the invite by token.
                lookup = (
                    await client.table("project_invites")
                    .select("id, project_id, invitee_email, status, expires_at")
                    .eq("token", token)
                    .maybe_single()
                    .execute()
                )
                invite = lookup.data if lookup else None
                if not invite:
                    raise HTTPException(status_code=404, detail="Invite not found")

                if invite["status"] == "accepted":
                    raise HTTPException(status_code=409, detail="Invite already accepted")
                if invite["status"] == "revoked":
                    raise HTTPException(status_code=410, detail="Invite has been revoked")
                if invite["status"] != "pending":
                    raise HTTPException(status_code=410, detail="Invite is no longer valid")
                if InvitationService._is_expired(invite):
                    raise HTTPException(status_code=410, detail="Invite has expired")

                if (accepting_email or "").strip().lower() != (invite["invitee_email"] or "").strip().lower():
                    raise HTTPException(
                        status_code=403,
                        detail="This invite was sent to a different email address",
                    )

                project_id = invite["project_id"]

                # Insert the membership row. We don't need a separate
                # already-member check: the project_members PK is
                # (project_id, user_id), so an upsert is a no-op when the
                # row exists (and we still flip the invite status so the
                # user gets a "you're already in" experience that ends on
                # the project page).
                await client.table("project_members").upsert(
                    {
                        "project_id": project_id,
                        "user_id":    accepting_user_id,
                        "role":       "editor",
                        "added_by":   accepting_user_id,
                    },
                    on_conflict="project_id,user_id",
                ).execute()

                # Flip status. We do this after the insert so a failed
                # insert leaves the invite reusable on retry.
                await (
                    client.table("project_invites")
                    .update({
                        "status":      "accepted",
                        "accepted_at": datetime.now(timezone.utc).isoformat(),
                    })
                    .eq("id", invite["id"])
                    .execute()
                )

                # Resolve the URL slug (chat_sessions.project_id text) so
                # the caller can navigate into the workspace. The
                # workspace route is keyed by the slug, not the UUID PK.
                slug_lookup = (
                    await client.table("chat_sessions")
                    .select("project_id, title")
                    .eq("id", project_id)
                    .maybe_single()
                    .execute()
                )
                slug_row = (slug_lookup.data if slug_lookup else None) or {}

            return {
                "project_id":    project_id,
                "project_slug":  slug_row.get("project_id"),
                "project_title": slug_row.get("title"),
            }
        except HTTPException:
            raise
        except APIError as exc:
            logger.error("Supabase error in accept_invite: code=%s msg=%s", exc.code, exc.message)
            raise HTTPException(status_code=500, detail="Database error") from exc

    # ── Revoke ──────────────────────────────────────────────────────────

    @staticmethod
    async def revoke_invite(
        *,
        invite_id: str,
        requesting_user_id: str,
    ) -> bool:
        """Cancel a pending invite. Owner-only.

        Returns True when a row was flipped; False when the invite did not
        exist as 'pending' (already accepted / already revoked).
        """
        try:
            async with managed_admin_client() as client:
                lookup = (
                    await client.table("project_invites")
                    .select("id, project_id, status")
                    .eq("id", invite_id)
                    .maybe_single()
                    .execute()
                )
                invite = lookup.data if lookup else None
                if not invite:
                    raise HTTPException(status_code=404, detail="Invite not found")
                if invite["status"] == "accepted":
                    raise HTTPException(status_code=410, detail="Invite already accepted — cannot revoke")
                if invite["status"] != "pending":
                    return False  # already revoked, treated as idempotent success

                if not await MembershipService.is_owner(invite["project_id"], requesting_user_id):
                    raise HTTPException(status_code=403, detail="Only the project owner can revoke invites")

                result = (
                    await client.table("project_invites")
                    .update({
                        "status":     "revoked",
                        "revoked_at": datetime.now(timezone.utc).isoformat(),
                    })
                    .eq("id", invite_id)
                    .eq("status", "pending")  # guard against TOCTOU
                    .select("id")
                    .execute()
                )
            return bool(result and result.data)
        except HTTPException:
            raise
        except APIError as exc:
            logger.error("Supabase error in revoke_invite: code=%s msg=%s", exc.code, exc.message)
            raise HTTPException(status_code=500, detail="Database error") from exc

    # ── List for a project (owner view) ─────────────────────────────────

    @staticmethod
    async def list_pending_invites_for_project(
        *,
        project_id: str,
        requesting_user_id: str,
    ) -> list[dict]:
        """All non-expired pending invites for ``project_id``. Owner-only."""
        if not await MembershipService.is_owner(project_id, requesting_user_id):
            raise HTTPException(status_code=403, detail="Only the project owner can list invites")

        try:
            async with managed_admin_client() as client:
                result = (
                    await client.table("project_invites")
                    .select("id, invitee_email, status, expires_at, created_at")
                    .eq("project_id", project_id)
                    .eq("status", "pending")
                    .order("created_at", desc=True)
                    .execute()
                )
            rows = result.data or []
            return [r for r in rows if not InvitationService._is_expired(r)]
        except APIError as exc:
            logger.error("Supabase error in list_pending_invites_for_project: code=%s msg=%s", exc.code, exc.message)
            raise HTTPException(status_code=500, detail="Database error") from exc

    # ── List for current user (invitee view) ────────────────────────────

    @staticmethod
    async def list_pending_invites_for_user(user_email: str) -> list[dict]:
        """All non-expired pending invites whose invitee_email matches.

        Joins to chat_sessions to surface project title + owner so the
        invitee can decide which to accept without round-trips.
        """
        if not user_email:
            return []
        normalized = user_email.strip().lower()

        try:
            async with managed_admin_client() as client:
                # Postgrest implicit-join syntax: include the related
                # chat_sessions row inline. The relationship is via the FK
                # we declared in 021.
                result = (
                    await client.table("project_invites")
                    .select(
                        "id, project_id, invitee_email, token, status, expires_at, "
                        "created_at, chat_sessions(title, user_id)"
                    )
                    .ilike("invitee_email", normalized)
                    .eq("status", "pending")
                    .order("created_at", desc=True)
                    .execute()
                )
            rows = result.data or []
            return [r for r in rows if not InvitationService._is_expired(r)]
        except APIError as exc:
            logger.error("Supabase error in list_pending_invites_for_user: code=%s msg=%s", exc.code, exc.message)
            raise HTTPException(status_code=500, detail="Database error") from exc
