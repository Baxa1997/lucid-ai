"""Tests for member-based authorization on ``/files/read``.

The endpoint used to gate on ``session.user_id == requesting_user_id``
(owner-only). It now gates on ``MembershipService.is_member`` so invited
editors can read file contents through the same path as the owner.

Tests:
  • Owner can read (existing behavior preserved).
  • Invited editor can read (new behavior).
  • Non-member gets 403.
  • Auth missing → HTTPException 401 (via FastAPI dependency).
  • Disk-fallback path uses OWNER's user_id (not the requester's), so
    members can still read after the live session is reaped.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "x")
os.environ.setdefault("SUPABASE_JWT_SECRET", "x")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "x")
os.environ.setdefault("ENCRYPTION_KEY", "x" * 32)
os.environ.setdefault("INTERNAL_API_KEY", "x")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")

OWNER_ID = "11111111-1111-1111-1111-111111111111"
MEMBER_ID = "22222222-2222-2222-2222-222222222222"
OUTSIDER_ID = "33333333-3333-3333-3333-333333333333"
SESSION_ID = "agent-session-abc"
PROJECT_ID = "44444444-4444-4444-4444-444444444444"


def _run(coro):
    return asyncio.run(coro)


def _make_user(user_id: str):
    from app.auth import AuthenticatedUser
    return AuthenticatedUser(user_id=user_id, raw_jwt=None)


def _fake_live_session(workspace_dir: str):
    """Return a SimpleNamespace shaped like AgentSession for store hits."""
    return SimpleNamespace(
        user_id=OWNER_ID,
        project_id=PROJECT_ID,
        workspace_dir=workspace_dir,
    )


@asynccontextmanager
async def _bare_admin_client():
    """No-op admin client — used when the chat_sessions lookup path
    should never fire (live session present)."""
    yield None


# ── 1. Owner can read (live session) ──────────────────────────────────

def test_owner_can_read_file_via_live_session():
    from app.routers.files import read_file

    with tempfile.TemporaryDirectory() as ws:
        with open(os.path.join(ws, "hello.txt"), "w") as f:
            f.write("hello world")

        with patch("app.routers.files.store.get_or_none",
                   new=AsyncMock(return_value=_fake_live_session(ws))), \
             patch("app.routers.files.MembershipService.is_member",
                   new=AsyncMock(return_value=True)):
            body = _run(read_file(
                session_id=SESSION_ID,
                path="hello.txt",
                user=_make_user(OWNER_ID),
            ))
    assert body == {"content": "hello world"}


# ── 2. Invited editor can read (live session) ────────────────────────

def test_invited_editor_can_read_file():
    """The key new behavior: a project member who is NOT the workspace
    owner can read file contents."""
    from app.routers.files import read_file

    with tempfile.TemporaryDirectory() as ws:
        with open(os.path.join(ws, "page.tsx"), "w") as f:
            f.write("export default function() { return <div/>; }")

        is_member = AsyncMock(return_value=True)
        with patch("app.routers.files.store.get_or_none",
                   new=AsyncMock(return_value=_fake_live_session(ws))), \
             patch("app.routers.files.MembershipService.is_member", new=is_member):
            body = _run(read_file(
                session_id=SESSION_ID,
                path="page.tsx",
                user=_make_user(MEMBER_ID),  # ← NOT the owner
            ))
    assert "<div/>" in body["content"]
    # Verify the membership check actually ran against the project_id
    # on the live session, not against session_id or user_id.
    is_member.assert_awaited()
    args = is_member.await_args.args
    assert args[0] == PROJECT_ID
    assert args[1] == MEMBER_ID


# ── 3. Non-member rejected (403) ─────────────────────────────────────

def test_non_member_cannot_read_file():
    from fastapi import HTTPException
    from app.routers.files import read_file

    with tempfile.TemporaryDirectory() as ws:
        with open(os.path.join(ws, "a.txt"), "w") as f:
            f.write("secret")

        with patch("app.routers.files.store.get_or_none",
                   new=AsyncMock(return_value=_fake_live_session(ws))), \
             patch("app.routers.files.MembershipService.is_member",
                   new=AsyncMock(return_value=False)):
            try:
                _run(read_file(
                    session_id=SESSION_ID,
                    path="a.txt",
                    user=_make_user(OUTSIDER_ID),
                ))
                assert False, "should have raised 403"
            except HTTPException as exc:
                assert exc.status_code == 403


# ── 4. Disk fallback — member can still read after session reap ──────

def test_member_can_read_via_disk_fallback():
    """When the live session has been reaped, the workspace path is
    reconstructed under the OWNER's user_id (looked up via Supabase),
    not the requester's. Invited members must still be able to read."""
    from app.routers.files import read_file

    with tempfile.TemporaryDirectory() as base:
        ws = os.path.join(base, OWNER_ID, SESSION_ID)
        os.makedirs(ws)
        with open(os.path.join(ws, "README.md"), "w") as f:
            f.write("# project")

        with patch("app.routers.files.store.get_or_none",
                   new=AsyncMock(return_value=None)), \
             patch("app.routers.files._lookup_project_from_agent_session",
                   new=AsyncMock(return_value=(PROJECT_ID, OWNER_ID))), \
             patch("app.routers.files.MembershipService.is_member",
                   new=AsyncMock(return_value=True)), \
             patch("app.routers.files.settings.WORKSPACE_BASE_PATH", base):
            body = _run(read_file(
                session_id=SESSION_ID,
                path="README.md",
                user=_make_user(MEMBER_ID),
            ))

    assert body == {"content": "# project"}


# ── 5. Disk fallback — non-member rejected ───────────────────────────

def test_non_member_blocked_on_disk_fallback():
    from fastapi import HTTPException
    from app.routers.files import read_file

    with patch("app.routers.files.store.get_or_none",
               new=AsyncMock(return_value=None)), \
         patch("app.routers.files._lookup_project_from_agent_session",
               new=AsyncMock(return_value=(PROJECT_ID, OWNER_ID))), \
         patch("app.routers.files.MembershipService.is_member",
               new=AsyncMock(return_value=False)):
        try:
            _run(read_file(
                session_id=SESSION_ID,
                path="anything.txt",
                user=_make_user(OUTSIDER_ID),
            ))
            assert False, "should have raised 403"
        except HTTPException as exc:
            assert exc.status_code == 403


# ── 6. Unmapped session → 403 (no project to authorize against) ──────

def test_no_project_id_blocks_request():
    """If we can't tie the session to a project at all, deny rather than
    fall back to owner-only behavior."""
    from fastapi import HTTPException
    from app.routers.files import read_file

    with patch("app.routers.files.store.get_or_none",
               new=AsyncMock(return_value=None)), \
         patch("app.routers.files._lookup_project_from_agent_session",
               new=AsyncMock(return_value=(None, None))):
        try:
            _run(read_file(
                session_id="orphan-session",
                path="x.txt",
                user=_make_user(OWNER_ID),
            ))
            assert False, "should have raised 403"
        except HTTPException as exc:
            assert exc.status_code == 403


# ── 7. Auth-missing — get_current_user raises 401 ────────────────────

def test_no_auth_header_yields_401():
    """The dependency itself enforces auth; verify the unauthorized
    branch raises HTTPException(401) so the wrapping FastAPI app
    surfaces it to the client."""
    from fastapi import HTTPException
    from app.auth import get_current_user

    # FastAPI Request stub with no auth headers — Python-side dict access
    # via .headers.get returns None.
    request = SimpleNamespace(headers={})
    try:
        _run(get_current_user(request))
        assert False, "should have raised 401"
    except HTTPException as exc:
        assert exc.status_code == 401


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
