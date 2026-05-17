"""Live tests for migration 026 — set/update/delete_tenant_row RPCs.

Marked `@pytest.mark.live` so they only run when explicitly opted in
via `-m live`. Each test provisions a tenant schema with one
collection (`menu_items`), exercises the RPC, and cleans up.

Auth model under test:
  • The three RPCs check `auth.uid()` against `public.project_members`.
  • From a service-role client, `auth.uid()` returns NULL, so the
    naive RPC call from service_role would fail with `access_denied`.
  • We use the anon client + a JWT (signed with the project's JWT
    secret) carrying the user's `sub` claim, which is how PostgREST
    populates `auth.uid()` in production.

These tests purposely avoid touching the real chat UI flow — they
build the minimum project state (chat_sessions row + project_members
row + tenant_schema + data_model) directly via service_role, then
invoke the RPC as the authenticated user.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid as _uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


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


pytestmark = [
    pytest.mark.live,
    pytest.mark.asyncio,
]


# ── Live-mode gate ────────────────────────────────────────────────────

def _have_real_supabase() -> bool:
    url = os.environ.get("SUPABASE_URL", "").strip()
    svc = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
    anon = os.environ.get("SUPABASE_ANON_KEY", "").strip()
    secret = os.environ.get("SUPABASE_JWT_SECRET", "").strip()
    return bool(url and svc and anon and secret)


if not _have_real_supabase():
    pytestmark.append(
        pytest.mark.skip(reason="Missing SUPABASE_URL/SERVICE_KEY/ANON_KEY/JWT_SECRET")
    )


# ── Helpers ───────────────────────────────────────────────────────────

async def _pick_user_id() -> str:
    from app.supabase_client import managed_admin_client
    async with managed_admin_client() as c:
        res = await c.table("users").select("id").limit(1).execute()
        if not (res.data or []):
            pytest.skip("No users in public.users — cannot run live tests")
        return res.data[0]["id"]


async def _create_test_project() -> str:
    """Insert a chat_sessions row with the test data_model already set,
    then provision its tenant schema and apply DDL. Returns the project
    UUID.
    """
    from app.supabase_client import managed_admin_client
    from app.services.data_model import DataModel, FieldDefinition, TableDefinition
    from app.services.tenant_sql_generator import apply_tenant_sql, generate_tenant_sql

    user_id = await _pick_user_id()
    data_model = DataModel(
        version="1.0",
        tables=[
            TableDefinition(
                name="menu_items",
                singular_label="Menu Item",
                plural_label="Menu Items",
                description="Restaurant menu items.",
                fields=[
                    FieldDefinition(name="name",        type="text", required=True),
                    FieldDefinition(name="price_cents", type="integer", required=True),
                    FieldDefinition(name="description", type="text"),
                    FieldDefinition(name="is_available", type="boolean", default=True),
                ],
                public_read=True,
            ),
        ],
        singletons={},
    )

    async with managed_admin_client() as c:
        # 1) Project row, with data_model persisted (the write RPCs
        #    check it).
        ins = await (
            c.table("chat_sessions")
            .insert({
                "user_id":    user_id,
                "title":      "[TEST 026 set_tenant_row]",
                "data_model": data_model.model_dump(mode="json"),
            })
            .execute()
        )
        project_id = ins.data[0]["id"]

        # 2) Provision the tenant schema.
        rpc = await c.rpc(
            "provision_tenant_schema", {"p_project_id": project_id},
        ).execute()
        tenant_schema = rpc.data

        # 3) Apply DDL.
        sql = generate_tenant_sql(data_model, tenant_schema, project_id)
        result = await apply_tenant_sql(sql, c)
        assert result["success"], f"DDL apply failed: {result}"

    return project_id


async def _cleanup_project(project_id: str) -> None:
    from app.supabase_client import managed_admin_client
    try:
        async with managed_admin_client() as c:
            await c.rpc("drop_tenant_schema", {"p_project_id": project_id}).execute()
            await c.table("chat_sessions").delete().eq("id", project_id).execute()
    except Exception:
        pass


def _mint_user_jwt(user_id: str) -> str:
    """Build a Supabase-style JWT for `user_id`. Signed with the same
    HS256 secret PostgREST validates against (`SUPABASE_JWT_SECRET`).
    """
    import jwt  # PyJWT — installed via supabase-py
    secret = os.environ["SUPABASE_JWT_SECRET"]
    now = datetime.now(timezone.utc)
    payload = {
        "aud":  "authenticated",
        "role": "authenticated",
        "sub":  user_id,
        "exp":  int((now + timedelta(minutes=15)).timestamp()),
        "iat":  int(now.timestamp()),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


async def _make_member(project_id: str, user_id: str, role: str = "editor") -> None:
    from app.supabase_client import managed_admin_client
    async with managed_admin_client() as c:
        await (
            c.table("project_members")
            .upsert({
                "project_id": project_id,
                "user_id":    user_id,
                "role":       role,
            })
            .execute()
        )


async def _make_orphan_user() -> tuple[str, str]:
    """Pick an existing user that is NOT a member of any project we'll
    create. Returns (user_id, jwt). Used for the access_denied test.
    """
    from app.supabase_client import managed_admin_client
    async with managed_admin_client() as c:
        # Just pick another existing user (or fall back to a fake UUID
        # not in users — the auth RPC will reject any non-member).
        res = await c.table("users").select("id").limit(5).execute()
        rows = res.data or []
        for r in rows[::-1]:
            uid = r["id"]
            jwt_tok = _mint_user_jwt(uid)
            return uid, jwt_tok
    # Fallback: completely random UUID. is_project_member will still
    # return False because there's no membership row.
    uid = str(_uuid.uuid4())
    return uid, _mint_user_jwt(uid)


# ── Per-test fixture ──────────────────────────────────────────────────

@pytest_asyncio.fixture
async def project_ctx():
    """One project + member user per test, cleaned up after."""
    project_id = await _create_test_project()
    user_id    = await _pick_user_id()
    await _make_member(project_id, user_id, role="owner")
    user_jwt   = _mint_user_jwt(user_id)
    yield {
        "project_id": project_id,
        "user_id":    user_id,
        "user_jwt":   user_jwt,
    }
    await _cleanup_project(project_id)


# pytest-asyncio's event-loop scoping breaks the singleton admin
# client between tests; reset it.
@pytest.fixture(autouse=True)
def _reset_admin_singleton():
    from app import supabase_client as _sc
    _sc._admin_client = None
    _sc._admin_client_lock = None
    yield
    _sc._admin_client = None
    _sc._admin_client_lock = None


# Helper to call an RPC as a specific user (anon key + Authorization
# header carries the JWT, exactly how PostgREST identifies users in
# production).
async def _rpc_as(user_jwt: str, fn: str, args: dict):
    from app.supabase_client import managed_client
    async with managed_client(user_jwt) as c:
        return await c.rpc(fn, args).execute()


# ── 1) Member can INSERT ─────────────────────────────────────────────

async def test_member_can_insert_row(project_ctx):
    res = await _rpc_as(
        project_ctx["user_jwt"],
        "set_tenant_row",
        {
            "p_project_id": project_ctx["project_id"],
            "p_table_name": "menu_items",
            "p_payload":    {"name": "Pizza", "price_cents": 1500},
        },
    )
    row = res.data
    assert isinstance(row, dict), f"expected dict, got {type(row).__name__}: {row!r}"
    assert row["name"] == "Pizza"
    assert row["price_cents"] == 1500
    # Server-side fields populated by defaults
    assert row.get("id")
    assert row.get("created_at")
    assert row.get("updated_at")


# ── 2) Non-member rejected ───────────────────────────────────────────

async def test_non_member_gets_access_denied(project_ctx):
    _, orphan_jwt = await _make_orphan_user()
    with pytest.raises(Exception) as exc_info:
        await _rpc_as(
            orphan_jwt,
            "set_tenant_row",
            {
                "p_project_id": project_ctx["project_id"],
                "p_table_name": "menu_items",
                "p_payload":    {"name": "x", "price_cents": 1},
            },
        )
    msg = str(exc_info.value).lower()
    assert "access_denied" in msg or "insufficient_privilege" in msg


# ── 3) Invalid table name rejected ───────────────────────────────────

async def test_invalid_table_rejected(project_ctx):
    with pytest.raises(Exception) as exc_info:
        await _rpc_as(
            project_ctx["user_jwt"],
            "set_tenant_row",
            {
                "p_project_id": project_ctx["project_id"],
                "p_table_name": "does_not_exist",
                "p_payload":    {"name": "x"},
            },
        )
    assert "invalid_table" in str(exc_info.value).lower()


# ── 4) Unprovisioned project rejected ────────────────────────────────

async def test_unprovisioned_project_rejected():
    """A project that has no tenant_schema set should fail
    project_not_found rather than crash on dynamic SQL."""
    from app.supabase_client import managed_admin_client
    user_id = await _pick_user_id()
    async with managed_admin_client() as c:
        ins = await (
            c.table("chat_sessions")
            .insert({
                "user_id": user_id,
                "title":   "[TEST 026 unprovisioned]",
            })
            .execute()
        )
        project_id = ins.data[0]["id"]

    await _make_member(project_id, user_id, role="owner")
    user_jwt = _mint_user_jwt(user_id)

    try:
        with pytest.raises(Exception) as exc_info:
            await _rpc_as(
                user_jwt,
                "set_tenant_row",
                {
                    "p_project_id": project_id,
                    "p_table_name": "menu_items",
                    "p_payload":    {"name": "x"},
                },
            )
        assert "project_not_found" in str(exc_info.value).lower()
    finally:
        async with managed_admin_client() as c:
            await c.table("chat_sessions").delete().eq("id", project_id).execute()


# ── 5) UPDATE existing row ───────────────────────────────────────────

async def test_update_existing_row(project_ctx):
    insert = await _rpc_as(
        project_ctx["user_jwt"], "set_tenant_row",
        {
            "p_project_id": project_ctx["project_id"],
            "p_table_name": "menu_items",
            "p_payload":    {"name": "Tonno", "price_cents": 1700,
                             "description": "Tuna pizza"},
        },
    )
    row_id = insert.data["id"]

    upd = await _rpc_as(
        project_ctx["user_jwt"], "update_tenant_row",
        {
            "p_project_id": project_ctx["project_id"],
            "p_table_name": "menu_items",
            "p_row_id":     row_id,
            "p_payload":    {"name": "Tonno Classico"},
        },
    )
    row = upd.data
    assert row["id"] == row_id
    assert row["name"] == "Tonno Classico"
    # Non-payload fields preserved
    assert row["price_cents"] == 1700
    assert row["description"] == "Tuna pizza"


# ── 6) UPDATE only touches provided fields ───────────────────────────

async def test_update_preserves_unprovided_fields(project_ctx):
    insert = await _rpc_as(
        project_ctx["user_jwt"], "set_tenant_row",
        {
            "p_project_id": project_ctx["project_id"],
            "p_table_name": "menu_items",
            "p_payload":    {"name": "Margherita", "price_cents": 1200,
                             "description": "Classic tomato + mozzarella",
                             "is_available": True},
        },
    )
    row_id = insert.data["id"]

    upd = await _rpc_as(
        project_ctx["user_jwt"], "update_tenant_row",
        {
            "p_project_id": project_ctx["project_id"],
            "p_table_name": "menu_items",
            "p_row_id":     row_id,
            "p_payload":    {"price_cents": 1300},
        },
    )
    row = upd.data
    assert row["price_cents"] == 1300
    assert row["name"] == "Margherita"
    assert row["description"] == "Classic tomato + mozzarella"
    assert row["is_available"] is True


# ── 7) DELETE row ────────────────────────────────────────────────────

async def test_delete_removes_row(project_ctx):
    ins = await _rpc_as(
        project_ctx["user_jwt"], "set_tenant_row",
        {
            "p_project_id": project_ctx["project_id"],
            "p_table_name": "menu_items",
            "p_payload":    {"name": "Quattro Formaggi", "price_cents": 1800},
        },
    )
    row_id = ins.data["id"]

    await _rpc_as(
        project_ctx["user_jwt"], "delete_tenant_row",
        {
            "p_project_id": project_ctx["project_id"],
            "p_table_name": "menu_items",
            "p_row_id":     row_id,
        },
    )

    # Confirm via the read RPC — should no longer include this id.
    read = await _rpc_as(
        project_ctx["user_jwt"], "get_tenant_collection",
        {
            "p_project_id": project_ctx["project_id"],
            "p_table_name": "menu_items",
        },
    )
    ids = [r["id"] for r in (read.data or [])]
    assert row_id not in ids


# ── 8) DELETE non-existent → row_not_found ───────────────────────────

async def test_delete_nonexistent_raises_row_not_found(project_ctx):
    ghost_id = str(_uuid.uuid4())
    with pytest.raises(Exception) as exc_info:
        await _rpc_as(
            project_ctx["user_jwt"], "delete_tenant_row",
            {
                "p_project_id": project_ctx["project_id"],
                "p_table_name": "menu_items",
                "p_row_id":     ghost_id,
            },
        )
    assert "row_not_found" in str(exc_info.value).lower()


# ── 9) RLS bypass via SECURITY DEFINER ───────────────────────────────
#
# Tenant tables have RLS scoped to project membership; a normal anon
# client with the user's JWT can't even SELECT from a tenant table
# directly (it isn't in PostgREST's exposed schemas). The fact that
# the user CAN insert via set_tenant_row proves the SECURITY DEFINER
# elevation works end-to-end — the function runs as its owner and
# bypasses RLS, while the membership check inside the function does
# the actual gating.

async def test_security_definer_routes_around_rls(project_ctx):
    """We've already proven this implicitly in test 1, but make it
    explicit: a fresh authenticated user with no schema-level grant
    can still write via the RPC."""
    res = await _rpc_as(
        project_ctx["user_jwt"], "set_tenant_row",
        {
            "p_project_id": project_ctx["project_id"],
            "p_table_name": "menu_items",
            "p_payload":    {"name": "SECDEF probe", "price_cents": 1},
        },
    )
    assert isinstance(res.data, dict)
    assert res.data["name"] == "SECDEF probe"
