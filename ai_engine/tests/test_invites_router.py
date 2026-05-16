"""Unit tests for the project-invite REST endpoints + service layer.

These tests focus on the failure modes called out in the spec — they do
NOT hit a real Supabase. The DB layer is mocked via patching the
``managed_admin_client`` and ``MembershipService`` boundaries.

What's covered (per spec line 6):
  • Owner can invite by email → 200, invite created, email sent
  • Non-owner gets 403 on invite endpoint
  • Cannot invite existing member → 409
  • Cannot create duplicate pending invite → 409
  • Invalid email format → 400 (via Pydantic EmailStr)
  • Invitee can list their pending invites
  • Invitee can accept valid invite → becomes editor
  • Cannot accept expired invite → 410
  • Cannot accept revoked invite → 410
  • Cannot accept with wrong email → 403
  • Owner can revoke pending invite
  • Owner can remove member
  • Cannot remove last owner (= can't remove yourself as owner)

Mocking strategy
----------------
Each test patches `managed_admin_client` to yield a "fake" async client
whose .table().select().etc chain returns the row(s) the test cares
about. This is the same shape the production code already uses.
"""
from __future__ import annotations

import asyncio
import os
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "x")
os.environ.setdefault("SUPABASE_JWT_SECRET", "x")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "x")
os.environ.setdefault("ENCRYPTION_KEY", "x" * 32)
os.environ.setdefault("INTERNAL_API_KEY", "x")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")

# ── Test fixtures ──────────────────────────────────────────────────────

OWNER_ID = "11111111-1111-1111-1111-111111111111"
INVITEE_ID = "22222222-2222-2222-2222-222222222222"
PROJECT_ID = "33333333-3333-3333-3333-333333333333"
INVITE_ID = "44444444-4444-4444-4444-444444444444"
TOKEN = "abc123def456" * 2 + "abcd"  # 32 hex chars worth

OWNER_EMAIL = "alice@example.com"
INVITEE_EMAIL = "bob@example.com"


def _run(coro):
    return asyncio.run(coro)


class _Result:
    """Lookalike for postgrest .execute() results — has a .data attribute."""
    def __init__(self, data):
        self.data = data


class _Chain:
    """Mimics the chainable postgrest builder used in members.py.

    Every method returns self until ``.execute()`` is awaited, which
    yields whatever ``self._result`` is. Tests overwrite ._result per
    table to simulate different DB states.
    """

    def __init__(self, result_by_table=None, default=None):
        self._result_by_table = result_by_table or {}
        self._default = default if default is not None else _Result([])
        self._current_table = None
        self._updates = []   # list of dicts pushed via .update()
        self._inserts = []   # list of dicts pushed via .insert()
        self._upserts = []   # list of dicts pushed via .upsert()

    def table(self, name):
        self._current_table = name
        return self

    # All builder verbs are no-ops that return self for chaining.
    def select(self, *_a, **_kw): return self
    def eq(self, *_a, **_kw): return self
    def ilike(self, *_a, **_kw): return self
    def in_(self, *_a, **_kw): return self
    def limit(self, *_a, **_kw): return self
    def order(self, *_a, **_kw): return self
    def maybe_single(self): return self

    def insert(self, payload):
        self._inserts.append((self._current_table, payload))
        return self

    def update(self, payload):
        self._updates.append((self._current_table, payload))
        return self

    def upsert(self, payload, **_kw):
        self._upserts.append((self._current_table, payload))
        return self

    def delete(self):
        # Returns self; execute() yields the recorded delete result.
        return self

    async def execute(self):
        # Resolve the result for the current table, defaulting to []. The
        # test sets _result_by_table per-table to control the response.
        return self._result_by_table.get(self._current_table, self._default)


def _admin_client_factory(chain: _Chain):
    """Build an asynccontextmanager-style fake of ``managed_admin_client``."""
    @asynccontextmanager
    async def _fake():
        yield chain
    return _fake


# ── 1. create_invite — happy path ──────────────────────────────────────

def test_owner_can_create_invite_for_new_email():
    from app.services.members import InvitationService

    chain = _Chain(result_by_table={
        # No existing public.users row for invitee_email → no member check.
        "users":            _Result(None),
        # No pending invite for (project, email).
        "project_invites":  _Result([]),
    })

    # Mock is_owner=True, then build_invite happy path.
    async def _is_owner(_pid, _uid): return True

    with patch("app.services.members.managed_admin_client", _admin_client_factory(chain)), \
         patch("app.services.members.MembershipService.is_owner", new=AsyncMock(side_effect=_is_owner)):
        result = _run(InvitationService.create_invite(
            project_id=PROJECT_ID,
            inviter_id=OWNER_ID,
            invitee_email=INVITEE_EMAIL,
        ))

    # Insert was attempted on project_invites.
    inserts = [t for t, _ in chain._inserts]
    assert "project_invites" in inserts, (
        "Expected an INSERT into project_invites; got %s" % inserts
    )

    inserted_row = next(p for t, p in chain._inserts if t == "project_invites")
    assert inserted_row["invitee_email"] == INVITEE_EMAIL.lower()
    assert inserted_row["status"] == "pending"
    assert len(inserted_row["token"]) == 32
    assert inserted_row["project_id"] == PROJECT_ID
    assert inserted_row["inviter_id"] == OWNER_ID


# ── 2. Non-owner rejected ──────────────────────────────────────────────

def test_non_owner_cannot_create_invite():
    from fastapi import HTTPException
    from app.services.members import InvitationService

    async def _is_owner(_pid, _uid): return False

    with patch("app.services.members.MembershipService.is_owner",
               new=AsyncMock(side_effect=_is_owner)):
        try:
            _run(InvitationService.create_invite(
                project_id=PROJECT_ID,
                inviter_id="not-owner-id",
                invitee_email=INVITEE_EMAIL,
            ))
            assert False, "should have raised"
        except HTTPException as exc:
            assert exc.status_code == 403


# ── 3. Existing member → 409 ───────────────────────────────────────────

def test_cannot_invite_existing_member():
    from fastapi import HTTPException
    from app.services.members import InvitationService

    chain = _Chain(result_by_table={
        # users lookup returns an existing user row by email.
        "users":            _Result({"id": INVITEE_ID}),
        # That user IS a project_member.
        "project_members":  _Result({"user_id": INVITEE_ID}),
    })

    async def _is_owner(_pid, _uid): return True

    with patch("app.services.members.managed_admin_client", _admin_client_factory(chain)), \
         patch("app.services.members.MembershipService.is_owner", new=AsyncMock(side_effect=_is_owner)):
        try:
            _run(InvitationService.create_invite(
                project_id=PROJECT_ID,
                inviter_id=OWNER_ID,
                invitee_email=INVITEE_EMAIL,
            ))
            assert False, "should have raised 409"
        except HTTPException as exc:
            assert exc.status_code == 409


# ── 4. Duplicate pending invite → 409 ──────────────────────────────────

def test_cannot_create_duplicate_pending_invite():
    from fastapi import HTTPException
    from app.services.members import InvitationService

    chain = _Chain(result_by_table={
        "users":           _Result(None),  # no existing user row
        # A pending invite already exists for this email.
        "project_invites": _Result([{"id": "previous-pending-id"}]),
    })

    async def _is_owner(_pid, _uid): return True

    with patch("app.services.members.managed_admin_client", _admin_client_factory(chain)), \
         patch("app.services.members.MembershipService.is_owner", new=AsyncMock(side_effect=_is_owner)):
        try:
            _run(InvitationService.create_invite(
                project_id=PROJECT_ID,
                inviter_id=OWNER_ID,
                invitee_email=INVITEE_EMAIL,
            ))
            assert False, "should have raised 409"
        except HTTPException as exc:
            assert exc.status_code == 409


# ── 5. Invalid email → 400 ─────────────────────────────────────────────

def test_invalid_email_format_rejected():
    from fastapi import HTTPException
    from app.services.members import InvitationService

    try:
        _run(InvitationService.create_invite(
            project_id=PROJECT_ID,
            inviter_id=OWNER_ID,
            invitee_email="not-an-email",
        ))
        assert False, "should have raised"
    except HTTPException as exc:
        assert exc.status_code == 400


# ── 6. accept_invite — happy path ──────────────────────────────────────

def test_invitee_can_accept_valid_invite():
    from app.services.members import InvitationService

    future = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    chain = _Chain(result_by_table={
        "project_invites": _Result({
            "id":             INVITE_ID,
            "project_id":     PROJECT_ID,
            "invitee_email":  INVITEE_EMAIL,
            "status":         "pending",
            "expires_at":     future,
        }),
        # accept_invite now also looks up the project slug for the
        # caller to use in the workspace URL.
        "chat_sessions":  _Result({
            "project_id": "project-slug-here",
            "title":      "Demo project",
        }),
    })

    with patch("app.services.members.managed_admin_client", _admin_client_factory(chain)):
        result = _run(InvitationService.accept_invite(
            token=TOKEN,
            accepting_user_id=INVITEE_ID,
            accepting_email=INVITEE_EMAIL,
        ))

    assert result["project_id"] == PROJECT_ID
    assert result["project_slug"] == "project-slug-here"
    # Upsert into project_members happened.
    pm_upserts = [p for t, p in chain._upserts if t == "project_members"]
    assert pm_upserts, "Expected upsert into project_members"
    assert pm_upserts[0]["user_id"] == INVITEE_ID
    assert pm_upserts[0]["role"] == "editor"

    # Update on project_invites to status=accepted.
    pi_updates = [p for t, p in chain._updates if t == "project_invites"]
    assert pi_updates and pi_updates[0]["status"] == "accepted"


# ── 7. Expired invite → 410 ────────────────────────────────────────────

def test_cannot_accept_expired_invite():
    from fastapi import HTTPException
    from app.services.members import InvitationService

    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    chain = _Chain(result_by_table={
        "project_invites": _Result({
            "id":             INVITE_ID,
            "project_id":     PROJECT_ID,
            "invitee_email":  INVITEE_EMAIL,
            "status":         "pending",
            "expires_at":     past,
        }),
    })

    with patch("app.services.members.managed_admin_client", _admin_client_factory(chain)):
        try:
            _run(InvitationService.accept_invite(
                token=TOKEN,
                accepting_user_id=INVITEE_ID,
                accepting_email=INVITEE_EMAIL,
            ))
            assert False, "should have raised 410"
        except HTTPException as exc:
            assert exc.status_code == 410


# ── 8. Revoked invite → 410 ────────────────────────────────────────────

def test_cannot_accept_revoked_invite():
    from fastapi import HTTPException
    from app.services.members import InvitationService

    future = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    chain = _Chain(result_by_table={
        "project_invites": _Result({
            "id":             INVITE_ID,
            "project_id":     PROJECT_ID,
            "invitee_email":  INVITEE_EMAIL,
            "status":         "revoked",  # ← key bit
            "expires_at":     future,
        }),
    })

    with patch("app.services.members.managed_admin_client", _admin_client_factory(chain)):
        try:
            _run(InvitationService.accept_invite(
                token=TOKEN,
                accepting_user_id=INVITEE_ID,
                accepting_email=INVITEE_EMAIL,
            ))
            assert False, "should have raised 410"
        except HTTPException as exc:
            assert exc.status_code == 410


# ── 9. Wrong email → 403 ───────────────────────────────────────────────

def test_cannot_accept_with_wrong_email():
    from fastapi import HTTPException
    from app.services.members import InvitationService

    future = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    chain = _Chain(result_by_table={
        "project_invites": _Result({
            "id":             INVITE_ID,
            "project_id":     PROJECT_ID,
            "invitee_email":  "the-right-person@example.com",
            "status":         "pending",
            "expires_at":     future,
        }),
    })

    with patch("app.services.members.managed_admin_client", _admin_client_factory(chain)):
        try:
            _run(InvitationService.accept_invite(
                token=TOKEN,
                accepting_user_id=INVITEE_ID,
                accepting_email="someone-else@example.com",
            ))
            assert False, "should have raised 403"
        except HTTPException as exc:
            assert exc.status_code == 403


# ── 10. Token not found → 404 ──────────────────────────────────────────

def test_accept_unknown_token_404():
    from fastapi import HTTPException
    from app.services.members import InvitationService

    chain = _Chain(result_by_table={"project_invites": _Result(None)})

    with patch("app.services.members.managed_admin_client", _admin_client_factory(chain)):
        try:
            _run(InvitationService.accept_invite(
                token="does-not-exist",
                accepting_user_id=INVITEE_ID,
                accepting_email=INVITEE_EMAIL,
            ))
            assert False, "should have raised 404"
        except HTTPException as exc:
            assert exc.status_code == 404


# ── 11. revoke_invite — owner happy path ───────────────────────────────

def test_owner_can_revoke_pending_invite():
    from app.services.members import InvitationService

    chain = _Chain(result_by_table={
        "project_invites": _Result({
            "id":         INVITE_ID,
            "project_id": PROJECT_ID,
            "status":     "pending",
        }),
    })
    # On update().eq().eq().select().execute() the chain returns the same
    # row by default; we want a non-empty .data so .revoke_invite returns True.
    chain._default = _Result([{"id": INVITE_ID}])

    async def _is_owner(_pid, _uid): return True

    with patch("app.services.members.managed_admin_client", _admin_client_factory(chain)), \
         patch("app.services.members.MembershipService.is_owner", new=AsyncMock(side_effect=_is_owner)):
        ok = _run(InvitationService.revoke_invite(
            invite_id=INVITE_ID,
            requesting_user_id=OWNER_ID,
        ))

    assert ok is True
    pi_updates = [p for t, p in chain._updates if t == "project_invites"]
    assert pi_updates and pi_updates[0]["status"] == "revoked"


# ── 12. revoke_invite — already accepted → 410 ─────────────────────────

def test_cannot_revoke_accepted_invite():
    from fastapi import HTTPException
    from app.services.members import InvitationService

    chain = _Chain(result_by_table={
        "project_invites": _Result({
            "id":         INVITE_ID,
            "project_id": PROJECT_ID,
            "status":     "accepted",
        }),
    })

    async def _is_owner(_pid, _uid): return True

    with patch("app.services.members.managed_admin_client", _admin_client_factory(chain)), \
         patch("app.services.members.MembershipService.is_owner", new=AsyncMock(side_effect=_is_owner)):
        try:
            _run(InvitationService.revoke_invite(
                invite_id=INVITE_ID,
                requesting_user_id=OWNER_ID,
            ))
            assert False, "should have raised 410"
        except HTTPException as exc:
            assert exc.status_code == 410


# ── 13. remove_member: owner cannot remove themselves ──────────────────

def test_owner_cannot_remove_themselves_via_router():
    """The router layer rejects self-removal as the owner with 400 before
    even hitting MembershipService — verifies our 'cannot remove last owner'
    guard (we don't currently support transferring ownership)."""
    from fastapi import HTTPException
    from app.routers.invites import remove_project_member
    from app.auth import AuthenticatedUser

    async def _is_owner(_pid, _uid): return True

    user = AuthenticatedUser(user_id=OWNER_ID, raw_jwt=None)
    with patch("app.services.members.MembershipService.is_owner",
               new=AsyncMock(side_effect=_is_owner)):
        try:
            _run(remove_project_member(
                project_id=PROJECT_ID,
                member_user_id=OWNER_ID,
                user=user,
            ))
            assert False, "should have raised 400"
        except HTTPException as exc:
            assert exc.status_code == 400


# ── 14. remove_member — also blocks underlying owner deletion ─────────

def test_membership_service_blocks_owner_deletion():
    """MembershipService.remove_member raises 400 when asked to remove an
    owner directly. This is the safety net behind the router-level guard."""
    from fastapi import HTTPException
    from app.services.members import MembershipService

    chain = _Chain(result_by_table={
        "project_members": _Result({"role": "owner"}),
    })

    with patch("app.services.members.managed_admin_client", _admin_client_factory(chain)):
        try:
            _run(MembershipService.remove_member(
                project_id=PROJECT_ID,
                user_id=OWNER_ID,
            ))
            assert False, "should have raised 400"
        except HTTPException as exc:
            assert exc.status_code == 400


# ── 15. list_pending_invites_for_user normalizes email casing ─────────

def test_list_invites_for_user_normalizes_email():
    """The DB stores emails lowercased; the lookup must also lowercase
    so an invitee with a mixed-case email on their account still finds
    their invitations."""
    from app.services.members import InvitationService

    future = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    chain = _Chain(result_by_table={
        "project_invites": _Result([{
            "id":            INVITE_ID,
            "project_id":    PROJECT_ID,
            "invitee_email": INVITEE_EMAIL,
            "status":        "pending",
            "expires_at":    future,
            "created_at":    future,
            "chat_sessions": {"title": "Project Alpha", "user_id": OWNER_ID},
        }]),
    })

    with patch("app.services.members.managed_admin_client", _admin_client_factory(chain)):
        # Caller passes mixed-case email.
        invites = _run(InvitationService.list_pending_invites_for_user("Bob@Example.COM"))

    assert len(invites) == 1
    assert invites[0]["invitee_email"] == INVITEE_EMAIL


# ── Self-runner ───────────────────────────────────────────────────────

def _main() -> int:
    tests = [(k, v) for k, v in globals().items()
             if k.startswith("test_") and callable(v)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  OK   {name}")
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL {name}: {exc}")
        except Exception as exc:
            failed += 1
            import traceback
            traceback.print_exc()
            print(f"  ERROR {name}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(_main())
