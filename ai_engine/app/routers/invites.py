"""REST endpoints for project invitations.

Flow
----
1. Owner POSTs /api/v1/projects/{id}/invite with an email. The router
   calls InvitationService.create_invite() and then asks Supabase Auth
   to send the magic-link email via admin.invite_user_by_email().
2. The email links the invitee to {APP_URL}/accept-invite?token=...
3. Once signed in, the frontend POSTs /api/v1/invites/{token}/accept.
   InvitationService verifies the token + email and inserts the
   membership row.

No new tables besides project_invites — accepted invites simply add a
row to the existing project_members table.

All endpoints require JWT auth via the existing ``get_current_user``
dependency (Authorization: Bearer ...).  X-Internal-Key callers (the
Next.js server-to-server path) also work because the dependency sets
``raw_jwt=None`` and the underlying service uses the service-role
client regardless.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from postgrest.exceptions import APIError
from pydantic import BaseModel

from app.auth import AuthenticatedUser, get_current_user
from app.config import logger, settings
from app.services.members import InvitationService, MembershipService
from app.supabase_client import managed_admin_client

router = APIRouter(prefix="/api/v1", tags=["invites"])


# ── Request / response models ─────────────────────────────────────────

class InviteCreateRequest(BaseModel):
    # Plain str rather than EmailStr — InvitationService.create_invite
    # runs its own regex check (and 400s on bad input), so we don't need
    # Pydantic's email-validator dependency in the import chain.
    email: str


# ── Helpers ───────────────────────────────────────────────────────────

async def _project_title(project_id: str) -> str:
    """Best-effort lookup of the project title for the email body.
    Falls back to a generic name when missing."""
    try:
        async with managed_admin_client() as client:
            res = (
                await client.table("chat_sessions")
                .select("title")
                .eq("id", project_id)
                .maybe_single()
                .execute()
            )
            return ((res.data if res else None) or {}).get("title") or "Lucid project"
    except APIError:
        return "Lucid project"


async def _user_display_name(user_id: str) -> str:
    """Resolve a user's display name (name or email) for the email body."""
    try:
        async with managed_admin_client() as client:
            res = (
                await client.table("users")
                .select("name, email")
                .eq("id", user_id)
                .maybe_single()
                .execute()
            )
        data = (res.data if res else None) or {}
        return data.get("name") or data.get("email") or "A Lucid user"
    except APIError:
        return "A Lucid user"


async def _send_invite_email(*, email: str, token: str, project_id: str,
                              project_name: str, inviter_name: str) -> dict:
    """Send the invitation magic-link via Supabase Auth admin API.

    Returns one of:
        {"status": "sent"}          — email queued by Supabase
        {"status": "user_exists"}   — invitee already has a Supabase account;
                                      Supabase refuses invite_user_by_email
                                      to existing users. Not a failure: the
                                      invitee gets the in-app banner via
                                      Realtime instead.
        {"status": "failed", "error": "<message>"}

    The invite row stays in the DB regardless — the owner can always copy
    the manual link from the UI.
    """
    accept_url = f"{settings.app_url}/accept-invite?token={token}"
    try:
        async with managed_admin_client() as client:
            # supabase-py async client surfaces admin under client.auth.admin
            await client.auth.admin.invite_user_by_email(
                email,
                {
                    "data": {
                        "invite_token":  token,
                        "project_id":    project_id,
                        "project_name":  project_name,
                        "inviter_name":  inviter_name,
                    },
                    "redirect_to": accept_url,
                },
            )
        return {"status": "sent"}
    except Exception as exc:
        msg = str(exc).lower()
        # Supabase's "already registered" rejection is the common case for
        # existing users. Treat as benign — the in-app banner handles it.
        if "already" in msg and ("registered" in msg or "exists" in msg):
            logger.info(
                "Skipped email for %s — user already exists; in-app banner only",
                email,
            )
            return {"status": "user_exists"}
        # Other failures: rate-limit, SMTP misconfig, network, etc.
        logger.warning("Supabase invite_user_by_email failed for %s: %s", email, exc)
        return {"status": "failed", "error": str(exc)}


# ── POST /api/v1/projects/{project_id}/invite ─────────────────────────

@router.post("/projects/{project_id}/invite")
async def create_project_invite(
    project_id: str,
    body: InviteCreateRequest,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Owner creates an invitation. Triggers a Supabase magic-link email."""
    invite = await InvitationService.create_invite(
        project_id=project_id,
        inviter_id=user.user_id,
        invitee_email=str(body.email),
    )

    project_name = await _project_title(project_id)
    inviter_name = await _user_display_name(user.user_id)
    send_result = await _send_invite_email(
        email=str(body.email),
        token=invite["token"],
        project_id=project_id,
        project_name=project_name,
        inviter_name=inviter_name,
    )

    # The invite row exists either way — the UI shows pending invites
    # from the DB, not from the email send result.
    response = {
        "invite_id":      invite["id"],
        "email":          invite["invitee_email"],
        "expires_at":     invite["expires_at"],
        "delivery":       send_result["status"],   # sent | user_exists | failed
        "email_sent":     send_result["status"] == "sent",
        "accept_url":     f"{settings.app_url}/accept-invite?token={invite['token']}",
    }
    if send_result["status"] == "user_exists":
        # Friendly note — not a warning. The invitee already has an
        # account and will see the banner instantly via Realtime.
        response["note"] = (
            f"{response['email']} already has an account — they'll see "
            "the invitation on their dashboard immediately."
        )
    elif send_result["status"] == "failed":
        response["warning"] = "Invite created but email send failed; share the link manually."
    return response


# ── GET /api/v1/invites/me ────────────────────────────────────────────

@router.get("/invites/me")
async def list_my_invites(
    user: AuthenticatedUser = Depends(get_current_user),
):
    """All pending invites whose invitee_email matches the current user's email.

    The accepting user's email is resolved from their public.users row
    (Supabase keeps it in sync via the new-user trigger). When the JWT
    payload carries an email claim we prefer it, since it requires no DB hop.
    """
    accepting_email = await _resolve_user_email(user)
    invites = await InvitationService.list_pending_invites_for_user(accepting_email)
    # We include the token here so the in-app "you have pending invites"
    # banner can accept directly. RLS already restricts this endpoint to
    # the invitee themselves (Bearer JWT + email match), so the token
    # isn't being leaked beyond the original recipient.
    sanitized = []
    for inv in invites:
        session = inv.get("chat_sessions") or {}
        sanitized.append({
            "invite_id":     inv["id"],
            "project_id":    inv["project_id"],
            "project_title": session.get("title"),
            "expires_at":    inv["expires_at"],
            "created_at":    inv["created_at"],
            "token":         inv["token"],
        })
    return {"invites": sanitized}


# ── POST /api/v1/invites/{token}/accept ───────────────────────────────

@router.post("/invites/{token}/accept")
async def accept_invite(
    token: str,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Trade an invite token for membership. Returns the project_id."""
    accepting_email = await _resolve_user_email(user)
    if not accepting_email:
        # No email on the JWT and no public.users row — should be impossible
        # if the user just signed in via OAuth, but guard anyway.
        raise HTTPException(status_code=400, detail="Authenticated user has no email on file")

    result = await InvitationService.accept_invite(
        token=token,
        accepting_user_id=user.user_id,
        accepting_email=accepting_email,
    )
    # The frontend redirects to the workspace using project_slug — the
    # chat_sessions.project_id text column — NOT project_id (the UUID PK).
    return {
        "project_id":    result["project_id"],
        "project_slug":  result.get("project_slug"),
        "project_title": result.get("project_title"),
    }


# ── GET /api/v1/projects/{project_id}/invites ─────────────────────────

@router.get("/projects/{project_id}/invites")
async def list_project_invites(
    project_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Owner view of all pending invites for a project."""
    invites = await InvitationService.list_pending_invites_for_project(
        project_id=project_id,
        requesting_user_id=user.user_id,
    )
    return {
        "invites": [
            {
                "invite_id":     i["id"],
                "email":         i["invitee_email"],
                "expires_at":    i["expires_at"],
                "created_at":    i["created_at"],
            }
            for i in invites
        ]
    }


# ── DELETE /api/v1/invites/{invite_id} ────────────────────────────────

@router.delete("/invites/{invite_id}")
async def revoke_invite(
    invite_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Owner cancels a pending invite."""
    revoked = await InvitationService.revoke_invite(
        invite_id=invite_id,
        requesting_user_id=user.user_id,
    )
    return {"status": "revoked" if revoked else "noop", "invite_id": invite_id}


# ── GET /api/v1/projects/{project_id}/members ─────────────────────────

@router.get("/projects/{project_id}/members")
async def list_project_members(
    project_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """List all members of a project. RLS limits visibility to members,
    so non-members get an empty list. Joins to public.users for name/email."""
    raw = await MembershipService.list_members(project_id, user_jwt=user.raw_jwt)

    # Enrich with display info from public.users. We do a single batched
    # lookup rather than N+1 round-trips.
    user_ids = list({m["user_id"] for m in raw})
    profiles_by_id: dict[str, dict] = {}
    if user_ids:
        try:
            async with managed_admin_client() as client:
                profiles = (
                    await client.table("users")
                    .select("id, email, name, avatar_url")
                    .in_("id", user_ids)
                    .execute()
                )
            for p in (profiles.data or []):
                profiles_by_id[p["id"]] = p
        except APIError:
            # Best-effort enrichment — fall back to raw ids when the
            # users table lookup fails.
            pass

    return {
        "members": [
            {
                "user_id":    m["user_id"],
                "role":       m["role"],
                "added_at":   m.get("added_at"),
                "email":      (profiles_by_id.get(m["user_id"]) or {}).get("email"),
                "name":       (profiles_by_id.get(m["user_id"]) or {}).get("name"),
                "avatar_url": (profiles_by_id.get(m["user_id"]) or {}).get("avatar_url"),
            }
            for m in raw
        ]
    }


# ── DELETE /api/v1/projects/{project_id}/members/{user_id} ────────────

@router.delete("/projects/{project_id}/members/{member_user_id}")
async def remove_project_member(
    project_id: str,
    member_user_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Owner removes a member from a project. Cannot remove the owner via
    this path (MembershipService.remove_member raises 400 in that case)."""
    if not await MembershipService.is_owner(project_id, user.user_id):
        raise HTTPException(status_code=403, detail="Only the project owner can remove members")

    if member_user_id == user.user_id:
        # The owner removing themselves would also be the "remove last owner"
        # case — explicit message is friendlier than the generic 400.
        raise HTTPException(status_code=400, detail="Owners cannot remove themselves")

    removed = await MembershipService.remove_member(
        project_id=project_id,
        user_id=member_user_id,
    )
    if not removed:
        raise HTTPException(status_code=404, detail="Member not found on this project")
    return {"status": "removed", "user_id": member_user_id, "project_id": project_id}


# ── Email resolution helper ───────────────────────────────────────────

async def _resolve_user_email(user: AuthenticatedUser) -> str:
    """Pull the email for the requesting user.

    Strategy: try JWT claim first (free), then fall back to a DB lookup.
    Supabase JWTs from OAuth flows include `email` in the payload, but
    some configurations strip it; the DB row is the source of truth.
    """
    # When raw_jwt is None (X-Internal-Key path) we have no claims to peek
    # at — go straight to the DB.
    if user.raw_jwt:
        try:
            from app.auth import decode_jwt
            payload = decode_jwt(user.raw_jwt)
            email = (payload.get("email") or "").strip().lower()
            if email:
                return email
        except Exception:
            pass

    try:
        async with managed_admin_client() as client:
            res = (
                await client.table("users")
                .select("email")
                .eq("id", user.user_id)
                .maybe_single()
                .execute()
            )
            return (((res.data if res else None) or {}).get("email") or "").strip().lower()
    except APIError:
        return ""
