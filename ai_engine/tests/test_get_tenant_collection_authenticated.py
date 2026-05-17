"""Live tests for migration 027 — get_tenant_collection_authenticated.

Three suites:

  • TestStandaloneProject — basic auth + happy path for a project
    that owns its own tenant_schema (the typical case for the website
    pipeline today).

  • TestLinkedProject — verifies the `parent_project_id` resolution
    semantics introduced in Phase 3.1's `resolve_tenant_for_project`:
    an admin panel (its own chat_sessions row) reads from its parent
    website's tenant_schema. CRITICALLY: the project_members check
    must hit the ADMIN's row, not the parent's.

  • TestPaginationAndOrdering — argument shape guards + correct
    ordering / LIMIT / OFFSET behaviour.

Same auth model as test_set_tenant_row.py: mint a Supabase JWT
signed with `SUPABASE_JWT_SECRET`, send through the anon client so
PostgREST sees `auth.uid()`. Service-role client is used only for
setup/teardown.

Pattern duplication with test_set_tenant_row.py — the project
fixture builder etc. — is intentional. These tests need slightly
different data_model fixtures (more rows, more columns) and
splitting helpers across two test files makes both harder to read.
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


def _have_real_supabase() -> bool:
    return all(os.environ.get(k, "").strip() for k in (
        "SUPABASE_URL", "SUPABASE_SERVICE_KEY",
        "SUPABASE_ANON_KEY", "SUPABASE_JWT_SECRET",
    ))


if not _have_real_supabase():
    pytestmark.append(
        pytest.mark.skip(
            reason="Missing SUPABASE_URL/SERVICE_KEY/ANON_KEY/JWT_SECRET",
        )
    )


# ── Test data ────────────────────────────────────────────────────────

def _data_model_with_menu_table():
    from app.services.data_model import (
        DataModel, FieldDefinition, TableDefinition,
    )
    return DataModel(
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
                public_read=False,   # admin-only read path; the
                                     # authenticated RPC ignores this
                                     # flag, but we want to confirm
                                     # we're NOT relying on it.
            ),
        ],
        singletons={},
    )


# ── Setup / cleanup helpers ──────────────────────────────────────────

async def _pick_user_id() -> str:
    from app.supabase_client import managed_admin_client
    async with managed_admin_client() as c:
        res = await c.table("users").select("id").limit(2).execute()
        rows = res.data or []
        if not rows:
            pytest.skip("No users in public.users — cannot run live tests")
        return rows[0]["id"]


async def _pick_second_user_id() -> str | None:
    """Pick any user_id different from the first one; falls back to
    None when there's only one user in the test database."""
    from app.supabase_client import managed_admin_client
    async with managed_admin_client() as c:
        res = await c.table("users").select("id").limit(5).execute()
        rows = res.data or []
        if len(rows) >= 2:
            return rows[1]["id"]
        return None


async def _create_provisioned_project(
    title: str,
    *,
    parent_project_id: str | None = None,
) -> tuple[str, str]:
    """Insert a chat_sessions row, provision its tenant schema (only
    when this is a standalone project — linked projects deliberately
    share the parent's schema), and apply the menu_items DDL. Returns
    (project_id, tenant_schema). The tenant_schema is the EFFECTIVE
    one — for linked projects it's the parent's, not this row's.
    """
    from app.supabase_client import managed_admin_client
    from app.services.tenant_sql_generator import (
        apply_tenant_sql, generate_tenant_sql,
    )

    user_id = await _pick_user_id()
    data_model = _data_model_with_menu_table()

    async with managed_admin_client() as c:
        insert_row = {
            "user_id":    user_id,
            "title":      title,
            "data_model": data_model.model_dump(mode="json"),
        }
        if parent_project_id is not None:
            insert_row["parent_project_id"] = parent_project_id

        ins = await (
            c.table("chat_sessions").insert(insert_row).execute()
        )
        project_id = ins.data[0]["id"]

        if parent_project_id is None:
            # Standalone — provision and apply DDL.
            rpc = await c.rpc(
                "provision_tenant_schema", {"p_project_id": project_id},
            ).execute()
            tenant_schema = rpc.data
            sql = generate_tenant_sql(data_model, tenant_schema, project_id)
            res = await apply_tenant_sql(sql, c)
            assert res["success"], f"DDL apply failed: {res}"
        else:
            # Linked — pull the parent's tenant_schema back so the
            # caller knows where rows will land.
            parent = await (
                c.table("chat_sessions")
                .select("tenant_schema")
                .eq("id", parent_project_id)
                .limit(1).execute()
            )
            tenant_schema = parent.data[0]["tenant_schema"]

    return project_id, tenant_schema


async def _cleanup_project(project_id: str) -> None:
    from app.supabase_client import managed_admin_client
    try:
        async with managed_admin_client() as c:
            await c.rpc(
                "drop_tenant_schema", {"p_project_id": project_id},
            ).execute()
            await (
                c.table("chat_sessions")
                .delete().eq("id", project_id).execute()
            )
    except Exception:
        pass


def _mint_user_jwt(user_id: str) -> str:
    import jwt
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


async def _add_member(project_id: str, user_id: str, role: str = "editor") -> None:
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


async def _insert_seed_rows(project_id: str, rows: list[dict]) -> None:
    """Direct INSERT via the set_tenant_row RPC (already proven by
    test_set_tenant_row.py). We use the project owner's JWT for this
    so the auth check passes."""
    user_id = await _pick_user_id()
    await _add_member(project_id, user_id, role="owner")
    jwt_tok = _mint_user_jwt(user_id)
    from app.supabase_client import managed_client
    async with managed_client(jwt_tok) as c:
        for row in rows:
            await c.rpc(
                "set_tenant_row",
                {
                    "p_project_id": project_id,
                    "p_table_name": "menu_items",
                    "p_payload":    row,
                },
            ).execute()


async def _rpc_as(user_jwt: str, fn: str, args: dict):
    from app.supabase_client import managed_client
    async with managed_client(user_jwt) as c:
        return await c.rpc(fn, args).execute()


# ── Singleton admin-client reset (event-loop scoping fix) ─────────────

@pytest.fixture(autouse=True)
def _reset_admin_singleton():
    from app import supabase_client as _sc
    _sc._admin_client = None
    _sc._admin_client_lock = None
    yield
    _sc._admin_client = None
    _sc._admin_client_lock = None


# ── Shared fixtures ──────────────────────────────────────────────────

@pytest_asyncio.fixture
async def standalone_ctx():
    """A standalone (no parent_project_id) project with a single
    member + 5 seed menu_items rows."""
    project_id, _tenant = await _create_provisioned_project(
        "[TEST 027 standalone]",
    )
    user_id = await _pick_user_id()
    await _add_member(project_id, user_id, role="owner")
    user_jwt = _mint_user_jwt(user_id)

    # Insert deterministic seed rows so ordering / pagination tests
    # can predict the expected sequence.
    await _insert_seed_rows(project_id, [
        {"name": "Apple Tart",   "price_cents": 800},
        {"name": "Bistecca",     "price_cents": 4200},
        {"name": "Carbonara",    "price_cents": 1800},
        {"name": "Daiquiri",     "price_cents": 1400},
        {"name": "Espresso",     "price_cents": 400},
    ])

    yield {
        "project_id": project_id,
        "user_id":    user_id,
        "user_jwt":   user_jwt,
    }
    await _cleanup_project(project_id)


# ─────────────────────────────────────────────────────────────────────
#  TestStandaloneProject
# ─────────────────────────────────────────────────────────────────────

class TestStandaloneProject:

    async def test_member_reads_collection(self, standalone_ctx):
        res = await _rpc_as(
            standalone_ctx["user_jwt"],
            "get_tenant_collection_authenticated",
            {
                "p_project_id": standalone_ctx["project_id"],
                "p_table_name": "menu_items",
            },
        )
        rows = res.data
        assert isinstance(rows, list)
        assert len(rows) == 5
        names = {r["name"] for r in rows}
        assert names == {"Apple Tart", "Bistecca", "Carbonara",
                         "Daiquiri", "Espresso"}

    async def test_non_member_gets_access_denied(self, standalone_ctx):
        other_uid = await _pick_second_user_id()
        if other_uid is None:
            pytest.skip("Need at least 2 users in public.users for this test")
        other_jwt = _mint_user_jwt(other_uid)
        with pytest.raises(Exception) as exc_info:
            await _rpc_as(
                other_jwt,
                "get_tenant_collection_authenticated",
                {
                    "p_project_id": standalone_ctx["project_id"],
                    "p_table_name": "menu_items",
                },
            )
        assert "access_denied" in str(exc_info.value).lower()

    async def test_invalid_table_rejected(self, standalone_ctx):
        with pytest.raises(Exception) as exc_info:
            await _rpc_as(
                standalone_ctx["user_jwt"],
                "get_tenant_collection_authenticated",
                {
                    "p_project_id": standalone_ctx["project_id"],
                    "p_table_name": "definitely_not_a_table",
                },
            )
        assert "invalid_table" in str(exc_info.value).lower()

    async def test_unprovisioned_project_rejected(self):
        """A chat_sessions row that exists but has no tenant_schema
        (e.g. project was created but never went through the pipeline)
        should surface `no_tenant`, not crash on dynamic SQL."""
        from app.supabase_client import managed_admin_client
        user_id = await _pick_user_id()
        async with managed_admin_client() as c:
            ins = await (
                c.table("chat_sessions")
                .insert({
                    "user_id": user_id,
                    "title":   "[TEST 027 no-tenant]",
                })
                .execute()
            )
            project_id = ins.data[0]["id"]

        try:
            await _add_member(project_id, user_id, role="owner")
            jwt_tok = _mint_user_jwt(user_id)
            with pytest.raises(Exception) as exc_info:
                await _rpc_as(
                    jwt_tok,
                    "get_tenant_collection_authenticated",
                    {
                        "p_project_id": project_id,
                        "p_table_name": "menu_items",
                    },
                )
            assert "no_tenant" in str(exc_info.value).lower()
        finally:
            async with managed_admin_client() as c:
                await c.table("chat_sessions").delete().eq("id", project_id).execute()


# ─────────────────────────────────────────────────────────────────────
#  TestLinkedProject — parent_project_id resolution semantics
# ─────────────────────────────────────────────────────────────────────

class TestLinkedProject:

    async def test_linked_admin_reads_parent_tenant(self):
        """The admin project has no tenant_schema of its own. Reads
        through `get_tenant_collection_authenticated` must hit the
        parent's tenant_schema instead."""
        parent_id, _ = await _create_provisioned_project(
            "[TEST 027 linked parent]",
        )
        try:
            # Seed against the parent's tenant.
            await _insert_seed_rows(parent_id, [
                {"name": "ParentRow1", "price_cents": 100},
                {"name": "ParentRow2", "price_cents": 200},
                {"name": "ParentRow3", "price_cents": 300},
            ])

            admin_id, _ = await _create_provisioned_project(
                "[TEST 027 linked admin]",
                parent_project_id=parent_id,
            )
            try:
                user_id = await _pick_user_id()
                await _add_member(admin_id, user_id, role="owner")
                jwt_tok = _mint_user_jwt(user_id)

                res = await _rpc_as(
                    jwt_tok,
                    "get_tenant_collection_authenticated",
                    {
                        "p_project_id": admin_id,
                        "p_table_name": "menu_items",
                    },
                )
                rows = res.data
                assert isinstance(rows, list)
                assert len(rows) == 3
                assert {r["name"] for r in rows} == {
                    "ParentRow1", "ParentRow2", "ParentRow3",
                }
            finally:
                await _cleanup_project(admin_id)
        finally:
            await _cleanup_project(parent_id)

    async def test_linked_admin_inherits_data_model(self):
        """Table validation must check the PARENT's data_model, not
        an empty/missing data_model on the admin row."""
        parent_id, _ = await _create_provisioned_project(
            "[TEST 027 linked-dm parent]",
        )
        try:
            # Create admin with NO data_model of its own — only the
            # parent's data_model defines `menu_items`. If the RPC
            # was looking at the admin's data_model it'd return
            # invalid_table here.
            from app.supabase_client import managed_admin_client
            user_id = await _pick_user_id()
            async with managed_admin_client() as c:
                ins = await (
                    c.table("chat_sessions")
                    .insert({
                        "user_id":           user_id,
                        "title":             "[TEST 027 linked-dm admin]",
                        "parent_project_id": parent_id,
                        # data_model intentionally omitted → defaults to {}
                    })
                    .execute()
                )
                admin_id = ins.data[0]["id"]

            try:
                await _add_member(admin_id, user_id, role="owner")
                jwt_tok = _mint_user_jwt(user_id)
                res = await _rpc_as(
                    jwt_tok,
                    "get_tenant_collection_authenticated",
                    {
                        "p_project_id": admin_id,
                        "p_table_name": "menu_items",
                    },
                )
                # Empty since parent has no seed rows yet, but a list
                # — confirming validation passed.
                assert isinstance(res.data, list)
            finally:
                await _cleanup_project(admin_id)
        finally:
            await _cleanup_project(parent_id)

    async def test_linked_admin_membership_checked_on_admin_not_parent(self):
        """Two sub-cases proving independence of admin/parent member lists:

          (A) User in admin's project_members but NOT parent's
              → CAN read.
          (B) User in parent's project_members but NOT admin's
              → CANNOT read.
        """
        parent_id, _ = await _create_provisioned_project(
            "[TEST 027 indep-auth parent]",
        )
        try:
            admin_id, _ = await _create_provisioned_project(
                "[TEST 027 indep-auth admin]",
                parent_project_id=parent_id,
            )
            try:
                await _insert_seed_rows(parent_id, [
                    {"name": "SharedRow", "price_cents": 999},
                ])

                user_a_id = await _pick_user_id()
                user_b_id = await _pick_second_user_id()
                if user_b_id is None:
                    pytest.skip("Need at least 2 users to run this test")

                # Clean any pre-existing membership so we control
                # exactly who's in which list.
                from app.supabase_client import managed_admin_client
                async with managed_admin_client() as c:
                    for pid in (parent_id, admin_id):
                        await (
                            c.table("project_members")
                            .delete().eq("project_id", pid).execute()
                        )

                # (A) user_a → admin only
                await _add_member(admin_id, user_a_id, role="owner")
                jwt_a = _mint_user_jwt(user_a_id)
                res_a = await _rpc_as(
                    jwt_a,
                    "get_tenant_collection_authenticated",
                    {
                        "p_project_id": admin_id,
                        "p_table_name": "menu_items",
                    },
                )
                assert isinstance(res_a.data, list)
                assert len(res_a.data) == 1
                assert res_a.data[0]["name"] == "SharedRow"

                # (B) user_b → parent only, NOT admin
                await _add_member(parent_id, user_b_id, role="owner")
                jwt_b = _mint_user_jwt(user_b_id)
                with pytest.raises(Exception) as exc_info:
                    await _rpc_as(
                        jwt_b,
                        "get_tenant_collection_authenticated",
                        {
                            "p_project_id": admin_id,
                            "p_table_name": "menu_items",
                        },
                    )
                assert "access_denied" in str(exc_info.value).lower()
            finally:
                await _cleanup_project(admin_id)
        finally:
            await _cleanup_project(parent_id)


# ─────────────────────────────────────────────────────────────────────
#  TestPaginationAndOrdering
# ─────────────────────────────────────────────────────────────────────

class TestPaginationAndOrdering:

    async def test_default_order_by_created_at_desc(self, standalone_ctx):
        """Without explicit args, default is created_at DESC. Rows
        were inserted in alphabetical order (Apple → Espresso), so
        the descending order should be the reverse."""
        res = await _rpc_as(
            standalone_ctx["user_jwt"],
            "get_tenant_collection_authenticated",
            {
                "p_project_id": standalone_ctx["project_id"],
                "p_table_name": "menu_items",
            },
        )
        rows = res.data
        names = [r["name"] for r in rows]
        # Espresso was inserted LAST → comes FIRST under DESC.
        assert names[0] == "Espresso"
        assert names[-1] == "Apple Tart"

    async def test_explicit_order_by_other_column(self, standalone_ctx):
        res = await _rpc_as(
            standalone_ctx["user_jwt"],
            "get_tenant_collection_authenticated",
            {
                "p_project_id":      standalone_ctx["project_id"],
                "p_table_name":      "menu_items",
                "p_order_by":        "price_cents",
                "p_order_direction": "asc",
            },
        )
        prices = [r["price_cents"] for r in res.data]
        assert prices == sorted(prices)
        # Smallest seed value is 400 (Espresso); largest is 4200 (Bistecca).
        assert prices[0] == 400
        assert prices[-1] == 4200

    async def test_limit_caps_results(self, standalone_ctx):
        res = await _rpc_as(
            standalone_ctx["user_jwt"],
            "get_tenant_collection_authenticated",
            {
                "p_project_id": standalone_ctx["project_id"],
                "p_table_name": "menu_items",
                "p_limit":      2,
            },
        )
        assert len(res.data) == 2

    async def test_offset_skips_rows(self, standalone_ctx):
        full = await _rpc_as(
            standalone_ctx["user_jwt"],
            "get_tenant_collection_authenticated",
            {
                "p_project_id":      standalone_ctx["project_id"],
                "p_table_name":      "menu_items",
                "p_order_by":        "name",
                "p_order_direction": "asc",
            },
        )
        skipped = await _rpc_as(
            standalone_ctx["user_jwt"],
            "get_tenant_collection_authenticated",
            {
                "p_project_id":      standalone_ctx["project_id"],
                "p_table_name":      "menu_items",
                "p_order_by":        "name",
                "p_order_direction": "asc",
                "p_offset":          2,
            },
        )
        # `skipped` should be the tail of `full` after dropping 2.
        assert [r["name"] for r in skipped.data] == \
               [r["name"] for r in full.data[2:]]

    async def test_invalid_order_column_rejected(self, standalone_ctx):
        """The shape regex blocks anything with `;`, spaces, mixed
        case, etc. A classic SQL-injection attempt should never
        reach the dynamic SELECT."""
        with pytest.raises(Exception) as exc_info:
            await _rpc_as(
                standalone_ctx["user_jwt"],
                "get_tenant_collection_authenticated",
                {
                    "p_project_id": standalone_ctx["project_id"],
                    "p_table_name": "menu_items",
                    "p_order_by":   "id; DROP TABLE menu_items; --",
                },
            )
        assert "invalid_order_column" in str(exc_info.value).lower()

    async def test_invalid_direction_rejected(self, standalone_ctx):
        with pytest.raises(Exception) as exc_info:
            await _rpc_as(
                standalone_ctx["user_jwt"],
                "get_tenant_collection_authenticated",
                {
                    "p_project_id":      standalone_ctx["project_id"],
                    "p_table_name":      "menu_items",
                    "p_order_direction": "sideways",
                },
            )
        assert "invalid_order_direction" in str(exc_info.value).lower()

    async def test_negative_offset_rejected(self, standalone_ctx):
        with pytest.raises(Exception) as exc_info:
            await _rpc_as(
                standalone_ctx["user_jwt"],
                "get_tenant_collection_authenticated",
                {
                    "p_project_id": standalone_ctx["project_id"],
                    "p_table_name": "menu_items",
                    "p_offset":     -5,
                },
            )
        assert "invalid_offset" in str(exc_info.value).lower()

    async def test_limit_above_1000_rejected(self, standalone_ctx):
        with pytest.raises(Exception) as exc_info:
            await _rpc_as(
                standalone_ctx["user_jwt"],
                "get_tenant_collection_authenticated",
                {
                    "p_project_id": standalone_ctx["project_id"],
                    "p_table_name": "menu_items",
                    "p_limit":      5000,
                },
            )
        assert "invalid_limit" in str(exc_info.value).lower()
