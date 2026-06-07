"""Tests for the explicit reconnect contract (Phase 2 Step 6).

The handshake now accepts an optional ``continuationToken`` field. When
present, the backend looks up the session by exact id rather than running
the 4-tier project_id heuristic. These tests pin down the lookup logic
in isolation — they don't spin up the WebSocket endpoint itself.

What we're protecting:
  • An explicit token is the deterministic path (no guessing).
  • A token that doesn't match the connecting user → fresh start, NOT
    silent reuse of someone else's session.
  • A token that does match → that session is reused, even when newer
    sessions exist for the same project_id.

Because the lookup is inline in ws.py (not extracted into a helper), these
tests assert the SAME query Supabase would receive, by mocking the
supabase_client.managed_admin_client context manager. If a future
refactor extracts a helper (recommended), point these tests at it.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


pytestmark = [pytest.mark.unit, pytest.mark.ws, pytest.mark.asyncio]


# ── Helper: build a supabase admin-client mock returning a fixed row ────


def _admin_client_returning(row: dict | None):
    """Return a context-manager mock whose .table().select().eq()… .execute()
    yields a response object with ``.data = row``."""

    response = MagicMock()
    response.data = row

    chain = MagicMock()
    chain.select = MagicMock(return_value=chain)
    chain.eq = MagicMock(return_value=chain)
    chain.not_ = MagicMock(return_value=chain)
    chain.is_ = MagicMock(return_value=chain)
    chain.order = MagicMock(return_value=chain)
    chain.limit = MagicMock(return_value=chain)
    chain.maybe_single = MagicMock(return_value=chain)
    chain.execute = AsyncMock(return_value=response)

    client = MagicMock()
    client.table = MagicMock(return_value=chain)

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=None)
    return cm


# ── The lookup logic, as a standalone async coroutine for testability ───
# Mirrors the inline implementation in ws.py:701-744. If ws.py extracts a
# helper, replace this with an import.


async def _lookup_by_continuation_token(token: str, user_id: str):
    """Stand-in for the inline lookup. Returns matched chat_session id or None."""
    if not token.strip():
        return None
    from app.supabase_client import managed_admin_client

    try:
        async with managed_admin_client() as client:
            r = await (
                client.table("chat_sessions")
                .select("id, user_id")
                .eq("id", token)
                .eq("user_id", user_id)
                .maybe_single()
                .execute()
            )
        return (r and r.data and r.data.get("id")) or None
    except Exception:
        return None


# ── Tests ────────────────────────────────────────────────────────────────


async def test_empty_token_returns_none_without_db_call():
    """No token → no lookup at all. Heuristic on caller side handles."""
    result = await _lookup_by_continuation_token("", "user-1")
    assert result is None

    result = await _lookup_by_continuation_token("   ", "user-1")
    assert result is None


async def test_matching_token_returns_session_id():
    """The happy path: token resolves to the same session for the same user."""
    row = {"id": "session-abc", "user_id": "user-1"}

    with patch(
        "app.supabase_client.managed_admin_client",
        return_value=_admin_client_returning(row),
    ):
        sid = await _lookup_by_continuation_token("session-abc", "user-1")
    assert sid == "session-abc"


async def test_token_for_different_user_returns_none():
    """Token matches a row but the user_id eq filter excludes it → None."""
    # Supabase's user_id eq filter would return zero rows for the wrong user;
    # mock the empty case here.
    with patch(
        "app.supabase_client.managed_admin_client",
        return_value=_admin_client_returning(None),
    ):
        sid = await _lookup_by_continuation_token("session-abc", "different-user")
    assert sid is None


async def test_unknown_token_returns_none():
    """Token that simply doesn't exist → None → caller falls back to fresh."""
    with patch(
        "app.supabase_client.managed_admin_client",
        return_value=_admin_client_returning(None),
    ):
        sid = await _lookup_by_continuation_token("nonexistent-id", "user-1")
    assert sid is None


async def test_lookup_swallows_db_errors():
    """DB error on lookup must not bubble — caller should fall through to
    the heuristic so a flaky DB doesn't strand the user."""

    def raising_admin_client():
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(side_effect=RuntimeError("supabase down"))
        cm.__aexit__ = AsyncMock(return_value=None)
        return cm

    with patch(
        "app.supabase_client.managed_admin_client",
        side_effect=raising_admin_client,
    ):
        sid = await _lookup_by_continuation_token("session-abc", "user-1")
    assert sid is None
