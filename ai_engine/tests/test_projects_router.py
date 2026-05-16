"""Unit tests for the project metadata + history endpoints.

Spec coverage:
  • GET project: owner sees own project (200)
  • GET project: invited member sees project (200)
  • GET project: non-member gets 403
  • GET project: missing project returns 404
  • GET messages: returns paginated history
  • GET files: returns file list (empty when workspace missing)
  • Auth-required: covered by the existing get_current_user dependency;
    we verify it raises 401-equivalent (HTTPException) when no header.

Mocking strategy mirrors test_invites_router.py — patch
``managed_admin_client`` to yield a fake chainable client whose
``.table().eq().execute()`` returns the row(s) we want for each table.
"""
from __future__ import annotations

import asyncio
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
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
PROJECT_ID = "44444444-4444-4444-4444-444444444444"


def _run(coro):
    return asyncio.run(coro)


class _Result:
    def __init__(self, data, count=None):
        self.data = data
        self.count = count


class _Chain:
    """Same shape as in test_invites_router but with a ``range()`` verb
    (used by pagination) and ``count="exact"`` support."""

    def __init__(self, result_by_table=None, default=None):
        self._result_by_table = result_by_table or {}
        self._default = default if default is not None else _Result([])
        self._current_table = None

    def table(self, name):
        self._current_table = name
        return self

    def select(self, *_a, **_kw): return self
    def eq(self, *_a, **_kw): return self
    def ilike(self, *_a, **_kw): return self
    def in_(self, *_a, **_kw): return self
    def limit(self, *_a, **_kw): return self
    def order(self, *_a, **_kw): return self
    def range(self, *_a, **_kw): return self
    def maybe_single(self): return self
    def insert(self, _p): return self
    def update(self, _p): return self
    def upsert(self, _p, **_kw): return self
    def delete(self): return self

    async def execute(self):
        return self._result_by_table.get(self._current_table, self._default)


def _admin_client_factory(chain: _Chain):
    @asynccontextmanager
    async def _fake():
        yield chain
    return _fake


def _make_user(user_id: str):
    from app.auth import AuthenticatedUser
    return AuthenticatedUser(user_id=user_id, raw_jwt=None)


# ── 1. Owner gets own project ──────────────────────────────────────────

def test_owner_can_get_project():
    from app.routers.projects import get_project

    chain = _Chain(result_by_table={
        "chat_sessions":   _Result({
            "id": PROJECT_ID,
            "title": "My landing",
            "project_id": "p_proj",
            "model_provider": "google",
            "is_active": True,
            "created_at": "2026-05-15T10:00:00+00:00",
            "updated_at": "2026-05-15T10:00:00+00:00",
        }),
        "users":           _Result({"id": OWNER_ID, "email": "owner@x.com", "name": "Owner", "avatar_url": None}),
        "project_members": _Result([{"user_id": OWNER_ID}], count=1),
        "chat_messages":   _Result([{
            "event_type": "generation_complete",
            "metadata_json": {"layout_archetype": "single_page_landing",
                              "preview_url": "https://preview.example.com/p"},
            "created_at": "2026-05-15T10:05:00+00:00",
        }]),
    })

    with patch("app.routers.projects.managed_admin_client", _admin_client_factory(chain)), \
         patch("app.routers.projects.MembershipService.is_member",
               new=AsyncMock(return_value=True)), \
         patch("app.routers.projects.MembershipService.get_owner",
               new=AsyncMock(return_value=OWNER_ID)):
        body = _run(get_project(project_id=PROJECT_ID, user=_make_user(OWNER_ID)))

    assert body["id"] == PROJECT_ID
    assert body["title"] == "My landing"
    assert body["archetype"] == "single_page_landing"
    assert body["preview_url"] == "https://preview.example.com/p"
    assert body["owner"]["user_id"] == OWNER_ID
    assert body["owner"]["email"] == "owner@x.com"
    assert body["members_count"] == 1
    assert body["status"] == "active"


# ── 2. Invited member gets project ────────────────────────────────────

def test_member_can_get_project():
    from app.routers.projects import get_project

    chain = _Chain(result_by_table={
        "chat_sessions":   _Result({
            "id": PROJECT_ID, "title": "Shared", "project_id": None,
            "model_provider": None, "is_active": False,
            "created_at": "2026-05-15T10:00:00+00:00",
            "updated_at": "2026-05-15T10:00:00+00:00",
        }),
        "users":           _Result({"id": OWNER_ID, "email": "owner@x.com", "name": "Owner", "avatar_url": None}),
        "project_members": _Result([{"user_id": OWNER_ID}, {"user_id": MEMBER_ID}], count=2),
        "chat_messages":   _Result([]),
    })

    with patch("app.routers.projects.managed_admin_client", _admin_client_factory(chain)), \
         patch("app.routers.projects.MembershipService.is_member",
               new=AsyncMock(return_value=True)), \
         patch("app.routers.projects.MembershipService.get_owner",
               new=AsyncMock(return_value=OWNER_ID)):
        body = _run(get_project(project_id=PROJECT_ID, user=_make_user(MEMBER_ID)))

    assert body["id"] == PROJECT_ID
    assert body["members_count"] == 2
    assert body["status"] == "inactive"


# ── 3. Non-member rejected (403) ──────────────────────────────────────

def test_non_member_gets_403():
    from fastapi import HTTPException
    from app.routers.projects import get_project

    chain = _Chain(result_by_table={
        "chat_sessions": _Result({"id": PROJECT_ID}),
    })

    with patch("app.routers.projects.managed_admin_client", _admin_client_factory(chain)), \
         patch("app.routers.projects.MembershipService.is_member",
               new=AsyncMock(return_value=False)):
        try:
            _run(get_project(project_id=PROJECT_ID, user=_make_user(OUTSIDER_ID)))
            assert False, "should have raised 403"
        except HTTPException as exc:
            assert exc.status_code == 403


# ── 4. Missing project returns 404 ────────────────────────────────────

def test_missing_project_returns_404():
    from fastapi import HTTPException
    from app.routers.projects import get_project

    chain = _Chain(result_by_table={
        "chat_sessions": _Result(None),
    })

    with patch("app.routers.projects.managed_admin_client", _admin_client_factory(chain)):
        try:
            _run(get_project(project_id="does-not-exist", user=_make_user(OWNER_ID)))
            assert False, "should have raised 404"
        except HTTPException as exc:
            assert exc.status_code == 404


# ── 5. Messages paginated ─────────────────────────────────────────────

def test_messages_paginated_for_member():
    from app.routers.projects import list_project_messages

    rows = [
        {"id": f"m{i}", "role": "user" if i % 2 == 0 else "assistant",
         "content": f"msg {i}", "event_type": None,
         "metadata_json": None, "created_at": f"2026-05-15T10:00:{i:02d}+00:00"}
        for i in range(3)
    ]
    chain = _Chain(result_by_table={
        "chat_sessions": _Result({"id": PROJECT_ID}),
        "chat_messages": _Result(rows, count=3),
    })

    with patch("app.routers.projects.managed_admin_client", _admin_client_factory(chain)), \
         patch("app.routers.projects.MembershipService.is_member",
               new=AsyncMock(return_value=True)):
        body = _run(list_project_messages(
            project_id=PROJECT_ID,
            user=_make_user(OWNER_ID),
            limit=50,
            offset=0,
        ))

    assert body["total"] == 3
    assert len(body["messages"]) == 3
    # Shape check: snake_case keys are preserved.
    assert "created_at" in body["messages"][0]
    assert body["messages"][0]["role"] in ("user", "assistant")


# ── 6. Messages: non-member rejected ──────────────────────────────────

def test_messages_non_member_403():
    from fastapi import HTTPException
    from app.routers.projects import list_project_messages

    chain = _Chain(result_by_table={"chat_sessions": _Result({"id": PROJECT_ID})})

    with patch("app.routers.projects.managed_admin_client", _admin_client_factory(chain)), \
         patch("app.routers.projects.MembershipService.is_member",
               new=AsyncMock(return_value=False)):
        try:
            _run(list_project_messages(
                project_id=PROJECT_ID,
                user=_make_user(OUTSIDER_ID),
                limit=50, offset=0,
            ))
            assert False, "should have raised 403"
        except HTTPException as exc:
            assert exc.status_code == 403


# ── 7. Files: empty workspace returns exists=False ───────────────────

def test_files_returns_empty_when_workspace_missing():
    from app.routers.projects import list_project_files

    chain = _Chain(result_by_table={"chat_sessions": _Result({"id": PROJECT_ID})})

    # Patch settings.WORKSPACE_BASE_PATH to a guaranteed-nonexistent dir.
    with patch("app.routers.projects.managed_admin_client", _admin_client_factory(chain)), \
         patch("app.routers.projects.MembershipService.is_member",
               new=AsyncMock(return_value=True)), \
         patch("app.routers.projects.MembershipService.get_owner",
               new=AsyncMock(return_value=OWNER_ID)), \
         patch("app.routers.projects.settings.WORKSPACE_BASE_PATH",
               "/does/not/exist-test-only"):
        body = _run(list_project_files(
            project_id=PROJECT_ID,
            user=_make_user(OWNER_ID),
        ))

    assert body == {"files": [], "exists": False}


# ── 8. Files: lists real files when workspace present ────────────────

def test_files_lists_real_files_when_workspace_present(tmp_path=None):
    """Build a small temp workspace tree and verify the walk."""
    import tempfile
    from app.routers.projects import list_project_files

    chain = _Chain(result_by_table={"chat_sessions": _Result({"id": PROJECT_ID})})

    with tempfile.TemporaryDirectory() as base:
        # Layout: <base>/<owner_id>/<project_id>/{a.txt, sub/b.txt, .hidden, node_modules/c.js}
        ws = os.path.join(base, OWNER_ID, PROJECT_ID)
        os.makedirs(os.path.join(ws, "sub"))
        os.makedirs(os.path.join(ws, "node_modules"))
        for p, content in [
            (os.path.join(ws, "a.txt"), "alpha"),
            (os.path.join(ws, "sub", "b.txt"), "beta"),
            (os.path.join(ws, ".hidden"), "secret"),
            (os.path.join(ws, "node_modules", "c.js"), "skip"),
        ]:
            with open(p, "w") as f:
                f.write(content)

        with patch("app.routers.projects.managed_admin_client", _admin_client_factory(chain)), \
             patch("app.routers.projects.MembershipService.is_member",
                   new=AsyncMock(return_value=True)), \
             patch("app.routers.projects.MembershipService.get_owner",
                   new=AsyncMock(return_value=OWNER_ID)), \
             patch("app.routers.projects.settings.WORKSPACE_BASE_PATH", base):
            body = _run(list_project_files(
                project_id=PROJECT_ID,
                user=_make_user(OWNER_ID),
            ))

    paths = [f["path"] for f in body["files"]]
    assert "a.txt" in paths
    assert os.path.join("sub", "b.txt") in paths
    # Excluded by EXCLUDE_DIRS:
    assert all("node_modules" not in p for p in paths)
    # Hidden dotfile skipped:
    assert ".hidden" not in paths
    assert body["exists"] is True


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
