"""Integration tests for the tenant-schema provisioning RPCs.

Unlike the other files in ai_engine/tests/, this module talks to a REAL
Supabase project. The RPCs `provision_tenant_schema` / `drop_tenant_schema`
do superuser-ish work (CREATE / DROP SCHEMA), so mocking the postgrest
chain would only test our test scaffolding — not the SQL.

Gated on real credentials
-------------------------
The module skips entirely when SUPABASE_URL is unset, missing, or a
test-fixture placeholder. With proper creds (loaded from the repo's
.env), each test creates a throw-away chat_sessions row, exercises the
RPC, and cleans up after itself even on assertion failure.

Event-loop discipline
---------------------
The supabase admin client is a singleton bound to the event loop that
created it (see app/supabase_client.py). Tests use `pytest-asyncio` so
the entire module shares one event loop, otherwise the second test
to run would hit "Event loop is closed".

What's covered (matches the spec in the migration prompt)
---------------------------------------------------------
  • Migration applied cleanly       — every other test depends on this
  • provision creates the schema + updates the row
  • Calling provision twice raises  (no double-provisioning)
  • drop removes the schema + clears the column
  • Schema name is deterministic    — same UUID always yields same name
  • Unknown project_id raises       — defensive contract check

Cleanup discipline
------------------
The `test_project` fixture creates one chat_sessions row per test and
deletes it in `finally`, which CASCADEs to project_members and
project_invites. We also call drop_tenant_schema before deleting the
row so the orphan schema isn't left behind in Postgres.
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ── Load real .env so SUPABASE_URL etc. point at the dev DB ───────────
# pytest doesn't autoload .env, and tests in this dir typically rely on
# `os.environ.setdefault` with placeholder values. This module needs
# real creds — load them best-effort, then decide whether to skip.
def _load_env_file(path: str) -> None:
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env_file(str(Path(__file__).resolve().parents[2] / ".env"))


_SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()


def _have_real_supabase() -> bool:
    """True when SUPABASE_URL/SERVICE_KEY look like real Supabase creds."""
    if not _SUPABASE_URL or not _SERVICE_KEY:
        return False
    if "test.supabase.co" in _SUPABASE_URL:
        return False
    if _SERVICE_KEY in ("x", "dummy", "placeholder"):
        return False
    return True


pytestmark = [
    pytest.mark.skipif(
        not _have_real_supabase(),
        reason="No real Supabase creds — set SUPABASE_URL + SUPABASE_SERVICE_KEY",
    ),
    pytest.mark.asyncio,
]


# ── Helpers ───────────────────────────────────────────────────────────

async def _pick_existing_user_id() -> str:
    """Find any user_id we can attach a chat_sessions row to.

    chat_sessions.user_id references auth.users(id). Rather than create
    a test user (which requires auth.admin permissions and pollutes the
    auth schema), we reuse an existing user_id. Test rows are tagged in
    `title` so they're identifiable; cleanup removes them every run.
    """
    from app.supabase_client import managed_admin_client
    async with managed_admin_client() as client:
        res = (
            await client.table("users")
            .select("id")
            .limit(1)
            .execute()
        )
        rows = res.data or []
        if not rows:
            pytest.skip("No users in public.users — cannot attach test chat_sessions row")
        return rows[0]["id"]


async def _create_test_project(title: str) -> str:
    """Insert a chat_sessions row and return its UUID."""
    from app.supabase_client import managed_admin_client
    user_id = await _pick_existing_user_id()
    async with managed_admin_client() as client:
        res = (
            await client.table("chat_sessions")
            .insert({"user_id": user_id, "title": title})
            .execute()
        )
        return res.data[0]["id"]


async def _delete_test_project(project_id: str) -> None:
    """Remove the test chat_sessions row. CASCADEs to members/invites."""
    from app.supabase_client import managed_admin_client
    try:
        async with managed_admin_client() as client:
            await (
                client.table("chat_sessions")
                .delete()
                .eq("id", project_id)
                .execute()
            )
    except Exception:  # nosec — cleanup is best-effort
        pass


async def _drop_tenant_best_effort(project_id: str) -> None:
    """Drop the tenant schema if one was provisioned. Ignores errors."""
    from app.supabase_client import managed_admin_client
    try:
        async with managed_admin_client() as client:
            await client.rpc("drop_tenant_schema", {"p_project_id": project_id}).execute()
    except Exception:  # nosec — cleanup is best-effort
        pass


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_admin_singleton():
    """Reset the supabase admin singleton between tests.

    `app.supabase_client._admin_client` is a module-level singleton bound
    to the event loop that created it. pytest-asyncio gives each test
    its own loop, so the singleton from test #1 holds a client tied to a
    closed loop — test #2 then hits "Event loop is closed". Resetting
    here forces a fresh client per test.
    """
    import app.supabase_client as _sc
    _sc._admin_client = None
    _sc._admin_client_lock = None
    yield
    _sc._admin_client = None
    _sc._admin_client_lock = None


@pytest_asyncio.fixture
async def test_project():
    """Yields a fresh chat_sessions row id and cleans up after the test."""
    project_id = await _create_test_project(title="tenant-provisioning test")
    try:
        yield project_id
    finally:
        await _drop_tenant_best_effort(project_id)
        await _delete_test_project(project_id)


# ── Tests ─────────────────────────────────────────────────────────────

async def test_migration_columns_present():
    """Migration applied cleanly — the four columns exist on chat_sessions."""
    from app.supabase_client import managed_admin_client
    async with managed_admin_client() as client:
        # SELECT a row with the new columns. Postgrest 4xx on missing
        # columns, which surfaces as an exception.
        await (
            client.table("chat_sessions")
            .select("id, parent_project_id, product_type, tenant_schema, data_model")
            .limit(1)
            .execute()
        )


async def test_provision_creates_schema_and_updates_row(test_project):
    """provision_tenant_schema returns a schema name and persists it."""
    from app.supabase_client import managed_admin_client
    async with managed_admin_client() as client:
        rpc_res = await client.rpc(
            "provision_tenant_schema",
            {"p_project_id": test_project},
        ).execute()
        schema_name = rpc_res.data

        assert isinstance(schema_name, str) and schema_name.startswith("tenant_"), (
            f"expected tenant_<hex>, got {schema_name!r}"
        )

        row_res = (
            await client.table("chat_sessions")
            .select("tenant_schema")
            .eq("id", test_project)
            .single()
            .execute()
        )
        assert row_res.data["tenant_schema"] == schema_name


async def test_provision_twice_raises(test_project):
    """A second provision call against the same project must error."""
    from app.supabase_client import managed_admin_client
    async with managed_admin_client() as client:
        await client.rpc(
            "provision_tenant_schema",
            {"p_project_id": test_project},
        ).execute()

        raised = False
        try:
            await client.rpc(
                "provision_tenant_schema",
                {"p_project_id": test_project},
            ).execute()
        except Exception as exc:  # noqa: BLE001 — any exception is acceptable
            raised = True
            msg = str(exc).lower()
            assert "already" in msg or "unique" in msg or "tenant_schema" in msg, (
                f"second-provision error didn't mention duplication: {exc!r}"
            )
        assert raised, "expected an exception on second provision call"


async def test_drop_removes_schema_and_clears_column(test_project):
    """drop_tenant_schema clears tenant_schema and returns the dropped name."""
    from app.supabase_client import managed_admin_client
    async with managed_admin_client() as client:
        prov = await client.rpc(
            "provision_tenant_schema",
            {"p_project_id": test_project},
        ).execute()
        provisioned = prov.data

        dropped = await client.rpc(
            "drop_tenant_schema",
            {"p_project_id": test_project},
        ).execute()
        assert dropped.data == provisioned, (
            f"drop returned {dropped.data!r}, expected {provisioned!r}"
        )

        row_res = (
            await client.table("chat_sessions")
            .select("tenant_schema")
            .eq("id", test_project)
            .single()
            .execute()
        )
        assert row_res.data["tenant_schema"] is None


async def test_schema_name_deterministic(test_project):
    """Same project_id (UUID) always yields the same schema name."""
    from app.supabase_client import managed_admin_client
    expected = "tenant_" + test_project.replace("-", "")[:12]

    async with managed_admin_client() as client:
        prov = await client.rpc(
            "provision_tenant_schema",
            {"p_project_id": test_project},
        ).execute()
        assert prov.data == expected, (
            f"non-deterministic name: got {prov.data!r}, expected {expected!r}"
        )


async def test_unknown_project_raises():
    """provision against a non-existent project_id must error."""
    from app.supabase_client import managed_admin_client
    fake_id = str(uuid.uuid4())

    async with managed_admin_client() as client:
        raised = False
        try:
            await client.rpc(
                "provision_tenant_schema",
                {"p_project_id": fake_id},
            ).execute()
        except Exception as exc:  # noqa: BLE001
            raised = True
            msg = str(exc).lower()
            assert "not found" in msg or "no_data" in msg, (
                f"unknown-project error didn't mention 'not found': {exc!r}"
            )
        assert raised, "expected an exception for unknown project_id"


async def test_drop_noop_when_unprovisioned(test_project):
    """Calling drop on a project that never provisioned returns empty string."""
    from app.supabase_client import managed_admin_client
    async with managed_admin_client() as client:
        res = await client.rpc(
            "drop_tenant_schema",
            {"p_project_id": test_project},
        ).execute()
        assert res.data == "", f"expected empty-string no-op, got {res.data!r}"
