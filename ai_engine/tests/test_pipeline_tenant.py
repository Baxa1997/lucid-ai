"""Unit tests for `app.services.pipeline_tenant`.

Three suites:

  • TestProvision — exercises `provision_tenant_for_project` end-to-end
    with a mocked supabase admin client. Lifted from the now-removed
    `test_website_pipeline_provision.py`.

  • TestSeedSkipPaths — minimal coverage of `seed_tenant_for_project`'s
    skip conditions (the rest of seed behavior is covered by
    `test_seed_tenant_data.py` directly against the underlying module).

  • TestResolveTenant — covers the new helper `resolve_tenant_for_project`
    that the admin pipeline will use to find which tenant_schema +
    DataModel to write into (parent's if linked, own if standalone).

All tests are mock-only — no Supabase, no Postgres, no LLM calls.
"""
from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


from app.services.data_model import (  # noqa: E402
    DataModel,
    FieldDefinition,
    TableDefinition,
)
from app.services.pipeline_tenant import (  # noqa: E402
    provision_tenant_for_project,
    resolve_tenant_for_project,
    seed_tenant_for_project,
)


UUID_VALID    = "11111111-2222-3333-4444-555555555555"
UUID_PARENT   = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
SCHEMA_NAME   = "tenant_112222333344"
PARENT_SCHEMA = "tenant_aabbccddeeee"


# ── Fixture builders ─────────────────────────────────────────────────

def _make_data_model(*, with_tables: bool = True) -> DataModel:
    if not with_tables:
        return DataModel(version="1.0", tables=[], singletons={})
    return DataModel(
        version="1.0",
        tables=[
            TableDefinition(
                name="menu_items",
                singular_label="Menu Item",
                plural_label="Menu Items",
                description="Items on the menu.",
                fields=[
                    FieldDefinition(name="name", type="text", required=True),
                    FieldDefinition(name="price_cents", type="integer", required=True),
                ],
            ),
        ],
        singletons={},
    )


def _make_admin_client(
    *,
    existing_schema: str | None = None,
    rpc_returns: str | None = SCHEMA_NAME,
    execute_ddl_raises: Exception | None = None,
) -> MagicMock:
    """A chainable mock client supporting .table(...).{select,update}(...).eq(...).limit(...).execute()
    and .rpc(name, params).execute() — enough for provision_tenant_for_project."""
    client = MagicMock()

    chain = MagicMock()
    chain.select.return_value = chain
    chain.update.return_value = chain
    chain.eq.return_value = chain
    chain.limit.return_value = chain
    execute_mock = AsyncMock()
    rows = [{"tenant_schema": existing_schema}] if existing_schema else [{"tenant_schema": None}]
    execute_mock.return_value = MagicMock(data=rows)
    chain.execute = execute_mock
    client.table.return_value = chain

    def _rpc(name: str, params: dict):
        rpc_obj = MagicMock()
        if name == "provision_tenant_schema":
            rpc_obj.execute = AsyncMock(return_value=MagicMock(data=rpc_returns))
        elif name == "execute_ddl":
            if execute_ddl_raises is not None:
                rpc_obj.execute = AsyncMock(side_effect=execute_ddl_raises)
            else:
                rpc_obj.execute = AsyncMock(return_value=MagicMock(data=None))
        else:
            rpc_obj.execute = AsyncMock(return_value=MagicMock(data=None))
        return rpc_obj

    client.rpc.side_effect = _rpc
    return client


def _patch_admin_client(client) -> "patch":
    """Make `managed_admin_client()` yield `client` for the duration of the patch."""
    @asynccontextmanager
    async def _fake_managed():
        yield client
    return patch(
        "app.supabase_client.managed_admin_client",
        new=_fake_managed,
    )


# ─────────────────────────────────────────────────────────────────────
#  TestProvision — provision_tenant_for_project
# ─────────────────────────────────────────────────────────────────────

class TestProvision:

    # ── Skip paths ────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_skips_when_feature_flag_off(self, monkeypatch):
        monkeypatch.setenv("TENANT_PROVISION_ENABLED", "0")
        result = await provision_tenant_for_project(
            data_model=_make_data_model(),
            project_id=UUID_VALID,
            websocket=None,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_skips_when_data_model_is_none(self):
        result = await provision_tenant_for_project(
            data_model=None, project_id=UUID_VALID, websocket=None,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_skips_when_data_model_has_no_tables(self):
        result = await provision_tenant_for_project(
            data_model=_make_data_model(with_tables=False),
            project_id=UUID_VALID,
            websocket=None,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_skips_when_project_id_is_not_a_uuid(self):
        result = await provision_tenant_for_project(
            data_model=_make_data_model(),
            project_id="_session_none_",
            websocket=None,
        )
        assert result is None

    # ── Happy paths ──────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_provisions_when_chat_session_has_null_tenant(self, monkeypatch):
        monkeypatch.setenv("TENANT_PROVISION_ENABLED", "1")
        client = _make_admin_client(existing_schema=None, rpc_returns=SCHEMA_NAME)

        with _patch_admin_client(client):
            result = await provision_tenant_for_project(
                data_model=_make_data_model(),
                project_id=UUID_VALID,
                websocket=None,
            )

        assert result == SCHEMA_NAME
        called = [c.args[0] for c in client.rpc.call_args_list]
        assert "provision_tenant_schema" in called
        assert called.count("execute_ddl") >= 1

    @pytest.mark.asyncio
    async def test_reuses_existing_tenant_schema(self, monkeypatch):
        monkeypatch.setenv("TENANT_PROVISION_ENABLED", "1")
        client = _make_admin_client(existing_schema=SCHEMA_NAME)

        with _patch_admin_client(client):
            result = await provision_tenant_for_project(
                data_model=_make_data_model(),
                project_id=UUID_VALID,
                websocket=None,
            )

        assert result == SCHEMA_NAME
        called = [c.args[0] for c in client.rpc.call_args_list]
        # Re-use path: no provision RPC, but DDL still re-applied.
        assert "provision_tenant_schema" not in called
        assert called.count("execute_ddl") >= 1

    # ── Failure swallowing ───────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_non_fatal_when_execute_ddl_raises(self, monkeypatch):
        monkeypatch.setenv("TENANT_PROVISION_ENABLED", "1")
        client = _make_admin_client(
            existing_schema=None,
            rpc_returns=SCHEMA_NAME,
            execute_ddl_raises=RuntimeError("simulated postgres error"),
        )
        with _patch_admin_client(client):
            result = await provision_tenant_for_project(
                data_model=_make_data_model(),
                project_id=UUID_VALID,
                websocket=None,
            )
        assert result is None


# ─────────────────────────────────────────────────────────────────────
#  TestSeedSkipPaths — seed_tenant_for_project's input gates
# ─────────────────────────────────────────────────────────────────────
#
# Full seed behaviour is exercised in test_seed_tenant_data.py
# (mocks the Gemini call and asserts on the SQL+RPC interaction).
# This suite only covers the three skip conditions in
# pipeline_tenant.seed_tenant_for_project itself, since those are
# the entry-point checks the admin pipeline will rely on.

class TestSeedSkipPaths:

    @pytest.mark.asyncio
    async def test_skips_when_feature_flag_off(self, monkeypatch):
        monkeypatch.setenv("TENANT_SEED_ENABLED", "0")
        result = await seed_tenant_for_project(
            data_model=_make_data_model(),
            tenant_schema=SCHEMA_NAME,
            website_plan={}, intent={}, purpose_data={},
            gemini_key="", project_id=UUID_VALID, websocket=None,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_skips_when_data_model_has_no_tables(self):
        result = await seed_tenant_for_project(
            data_model=_make_data_model(with_tables=False),
            tenant_schema=SCHEMA_NAME,
            website_plan={}, intent={}, purpose_data={},
            gemini_key="", project_id=UUID_VALID, websocket=None,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_skips_when_tenant_schema_missing(self):
        """If Stage 4.6 didn't return a schema, 4.7 must NOT try to
        seed — there's nothing to insert into. Without this the seeder
        would either crash on dynamic SQL or succeed against the wrong
        schema entirely."""
        result = await seed_tenant_for_project(
            data_model=_make_data_model(),
            tenant_schema=None,
            website_plan={}, intent={}, purpose_data={},
            gemini_key="", project_id=UUID_VALID, websocket=None,
        )
        assert result is None


# ─────────────────────────────────────────────────────────────────────
#  TestResolveTenant — resolve_tenant_for_project
# ─────────────────────────────────────────────────────────────────────
#
# The function does one or two reads against chat_sessions:
#   • Always: fetch the project's own (id, parent_project_id,
#     tenant_schema, data_model).
#   • If parent_project_id is set: fetch the parent's
#     (tenant_schema, data_model).
#
# Mock pattern: client.table("chat_sessions").select(...).eq(id, X)
#               .limit(1).execute() — returns a different `data` for
#               each call based on the .eq() id.
#
# We use a small dispatcher to give the chained mock per-id responses.

def _make_resolve_client(rows_by_id: dict[str, dict]) -> MagicMock:
    """Build a chainable mock where `eq("id", project_id).limit(1).execute()`
    returns the row registered for that id (or empty list if missing).

    `rows_by_id` maps id → the row dict to return. To simulate "no
    row found" for an id, omit it from the mapping."""
    client = MagicMock()
    state: dict[str, str | None] = {"current_id": None}

    chain = MagicMock()
    chain.select.return_value = chain
    chain.limit.return_value = chain

    def _eq(col: str, val: str):
        if col == "id":
            state["current_id"] = val
        return chain

    chain.eq.side_effect = _eq

    async def _execute():
        cid = state["current_id"]
        row = rows_by_id.get(cid)
        return MagicMock(data=[row] if row else [])

    chain.execute = _execute
    client.table.return_value = chain
    return client


class TestResolveTenant:

    @pytest.mark.asyncio
    async def test_standalone_project_returns_own_tenant(self):
        """Project has its own tenant_schema and no parent → return them."""
        own_data_model = _make_data_model().model_dump(mode="json")
        client = _make_resolve_client({
            UUID_VALID: {
                "id":                UUID_VALID,
                "parent_project_id": None,
                "tenant_schema":     SCHEMA_NAME,
                "data_model":        own_data_model,
                "visual_dna":        {"brand_name": "Standalone"},
            },
        })
        result = await resolve_tenant_for_project(UUID_VALID, client)
        assert result is not None
        tenant_schema, data_model, visual_dna = result
        assert tenant_schema == SCHEMA_NAME
        assert [t.name for t in data_model.tables] == ["menu_items"]
        assert visual_dna == {"brand_name": "Standalone"}

    @pytest.mark.asyncio
    async def test_linked_project_returns_parent_tenant(self):
        """Project has parent_project_id → resolve through the parent."""
        parent_data_model = _make_data_model().model_dump(mode="json")
        client = _make_resolve_client({
            UUID_VALID: {
                "id":                UUID_VALID,
                "parent_project_id": UUID_PARENT,
                "tenant_schema":     None,           # ignored for linked
                "data_model":        {},             # ignored for linked
                "visual_dna":        None,
            },
            UUID_PARENT: {
                "tenant_schema":     PARENT_SCHEMA,
                "data_model":        parent_data_model,
                "visual_dna":        {"brand_name": "Parent", "primary_color": "#ff3366"},
            },
        })
        result = await resolve_tenant_for_project(UUID_VALID, client)
        assert result is not None
        tenant_schema, data_model, visual_dna = result
        # Parent's schema, not child's NULL.
        assert tenant_schema == PARENT_SCHEMA
        assert [t.name for t in data_model.tables] == ["menu_items"]
        # Parent's visual_dna inherited verbatim.
        assert visual_dna == {"brand_name": "Parent", "primary_color": "#ff3366"}

    @pytest.mark.asyncio
    async def test_standalone_project_without_visual_dna_returns_none_for_third(self):
        """Old project rows (pre-migration-029) read visual_dna as None.
        Resolver must NOT raise — return None as the third tuple element."""
        own_data_model = _make_data_model().model_dump(mode="json")
        client = _make_resolve_client({
            UUID_VALID: {
                "id":                UUID_VALID,
                "parent_project_id": None,
                "tenant_schema":     SCHEMA_NAME,
                "data_model":        own_data_model,
                "visual_dna":        None,
            },
        })
        result = await resolve_tenant_for_project(UUID_VALID, client)
        assert result is not None
        _, _, visual_dna = result
        assert visual_dna is None

    @pytest.mark.asyncio
    async def test_linked_project_with_missing_parent_returns_none(self):
        """parent_project_id points at a row that doesn't exist → None."""
        client = _make_resolve_client({
            UUID_VALID: {
                "id":                UUID_VALID,
                "parent_project_id": UUID_PARENT,
                "tenant_schema":     None,
                "data_model":        {},
            },
            # UUID_PARENT intentionally absent.
        })
        result = await resolve_tenant_for_project(UUID_VALID, client)
        assert result is None

    @pytest.mark.asyncio
    async def test_project_without_tenant_returns_none(self):
        """Standalone project but tenant_schema is NULL → None."""
        client = _make_resolve_client({
            UUID_VALID: {
                "id":                UUID_VALID,
                "parent_project_id": None,
                "tenant_schema":     None,
                "data_model":        _make_data_model().model_dump(mode="json"),
            },
        })
        result = await resolve_tenant_for_project(UUID_VALID, client)
        assert result is None

    @pytest.mark.asyncio
    async def test_project_with_tenant_but_no_data_model_returns_none(self):
        """A row with tenant_schema set but data_model NULL/empty
        means a provisioning step landed partway. The admin pipeline
        can't safely write without knowing the table shapes."""
        client = _make_resolve_client({
            UUID_VALID: {
                "id":                UUID_VALID,
                "parent_project_id": None,
                "tenant_schema":     SCHEMA_NAME,
                "data_model":        None,
            },
        })
        result = await resolve_tenant_for_project(UUID_VALID, client)
        assert result is None

    @pytest.mark.asyncio
    async def test_missing_project_returns_none(self):
        """No chat_sessions row for project_id at all → None."""
        client = _make_resolve_client({})  # empty: nothing matches
        result = await resolve_tenant_for_project(UUID_VALID, client)
        assert result is None
