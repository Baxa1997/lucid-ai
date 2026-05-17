"""Unit tests for the Stage 4.6 helper `_provision_tenant_for_project`.

Pure mock-based tests — no Supabase, no Postgres. The function is
extracted from the main pipeline specifically so it can be tested in
isolation; the upstream stages (intent, research, plan, planner) are
not invoked here.

What we exercise:
  • Skip paths: feature flag off, no data_model, no tables, non-UUID
    project_id — the helper returns None silently in each case.
  • Happy path (fresh): the chat_sessions row has tenant_schema=NULL,
    so we call provision_tenant_schema then apply DDL via execute_ddl.
  • Happy path (already provisioned): the row already has a
    tenant_schema, so we skip the RPC and re-apply DDL with the
    persisted schema name.
  • Non-fatal failure: a broken RPC bubbles back as None (not a raise).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


from app.services.data_model import (  # noqa: E402
    DataModel,
    FieldDefinition,
    TableDefinition,
)
from app.services.website_pipeline import (  # noqa: E402
    _provision_tenant_for_project,
)


# ── Helpers ──────────────────────────────────────────────────────────

UUID_VALID   = "11111111-2222-3333-4444-555555555555"
SCHEMA_NAME  = "tenant_112222333344"


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
    """Build a MagicMock that quacks like the supabase-py AsyncClient
    we use, with `.table(...)` and `.rpc(...)` chains for the calls
    the helper makes.
    """
    client = MagicMock()

    # .table("chat_sessions").select(...).eq(...).limit(...).execute()
    # — and .table(...).update(...).eq(...).execute(). Use the same
    # chainable mock for both; the executable terminus returns the
    # select rows (the update doesn't read .data so this is benign).
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

    # .rpc("provision_tenant_schema", ...).execute() AND
    # .rpc("execute_ddl", ...).execute()
    # — both go through the same .rpc(...).execute() shape; we differentiate
    # by the rpc name to return different things.
    def _rpc(name: str, params: dict):
        rpc_obj = MagicMock()
        if name == "provision_tenant_schema":
            rpc_obj.execute = AsyncMock(
                return_value=MagicMock(data=rpc_returns),
            )
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
    """Returns a context manager that patches `managed_admin_client`
    inside the website_pipeline module to yield `client`."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _fake_managed():
        yield client

    return patch(
        "app.supabase_client.managed_admin_client",
        new=_fake_managed,
    )


# ── Skip paths ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_skips_when_feature_flag_off(monkeypatch):
    monkeypatch.setenv("TENANT_PROVISION_ENABLED", "0")
    result = await _provision_tenant_for_project(
        data_model=_make_data_model(),
        project_id=UUID_VALID,
        websocket=None,
    )
    assert result is None


@pytest.mark.asyncio
async def test_skips_when_data_model_is_none():
    result = await _provision_tenant_for_project(
        data_model=None,
        project_id=UUID_VALID,
        websocket=None,
    )
    assert result is None


@pytest.mark.asyncio
async def test_skips_when_data_model_has_no_tables():
    result = await _provision_tenant_for_project(
        data_model=_make_data_model(with_tables=False),
        project_id=UUID_VALID,
        websocket=None,
    )
    assert result is None


@pytest.mark.asyncio
async def test_skips_when_project_id_is_not_a_uuid():
    result = await _provision_tenant_for_project(
        data_model=_make_data_model(),
        project_id="_session_none_",
        websocket=None,
    )
    assert result is None


# ── Happy paths ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_provisions_when_chat_session_has_null_tenant(monkeypatch):
    monkeypatch.setenv("TENANT_PROVISION_ENABLED", "1")
    client = _make_admin_client(existing_schema=None, rpc_returns=SCHEMA_NAME)

    with _patch_admin_client(client):
        result = await _provision_tenant_for_project(
            data_model=_make_data_model(),
            project_id=UUID_VALID,
            websocket=None,
        )

    assert result == SCHEMA_NAME
    # provision_tenant_schema must have been called with the UUID
    called_rpcs = [c.args[0] for c in client.rpc.call_args_list]
    assert "provision_tenant_schema" in called_rpcs
    # …and execute_ddl must have been called at least once per DDL stmt
    assert called_rpcs.count("execute_ddl") >= 1


@pytest.mark.asyncio
async def test_reuses_existing_tenant_schema(monkeypatch):
    monkeypatch.setenv("TENANT_PROVISION_ENABLED", "1")
    client = _make_admin_client(existing_schema=SCHEMA_NAME)

    with _patch_admin_client(client):
        result = await _provision_tenant_for_project(
            data_model=_make_data_model(),
            project_id=UUID_VALID,
            websocket=None,
        )

    assert result == SCHEMA_NAME
    # provision_tenant_schema must NOT have been called — we reused.
    called_rpcs = [c.args[0] for c in client.rpc.call_args_list]
    assert "provision_tenant_schema" not in called_rpcs
    # execute_ddl is still called for the (idempotent) re-apply
    assert called_rpcs.count("execute_ddl") >= 1


# ── Failure swallowing ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_non_fatal_when_execute_ddl_raises(monkeypatch):
    monkeypatch.setenv("TENANT_PROVISION_ENABLED", "1")
    client = _make_admin_client(
        existing_schema=None,
        rpc_returns=SCHEMA_NAME,
        execute_ddl_raises=RuntimeError("simulated postgres error"),
    )

    with _patch_admin_client(client):
        result = await _provision_tenant_for_project(
            data_model=_make_data_model(),
            project_id=UUID_VALID,
            websocket=None,
        )

    # The helper should swallow the error and return None.
    assert result is None
