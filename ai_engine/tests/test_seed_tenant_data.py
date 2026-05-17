"""Tests for the Stage 4.7 seed-data module (`seed_tenant_data`).

Three layers covered here:

  • `build_insert_sql` + `_format_sql_value` — pure-Python SQL building.
    No mocks needed. Tests the escaping, type coercion, and column
    alignment logic that determines whether the INSERT we send to
    Postgres is valid SQL.

  • `plan_seed_data` (with mocked `structured_distill`) — verifies
    that the parse-and-clean path drops unknown tables/fields, clamps
    row counts, and degrades to {} on a Gemini failure.

  • `apply_seed_data` (with fake admin client + fake image_search) —
    confirms image-field resolution swaps search queries for URLs and
    that the INSERT goes through the execute_ddl RPC.
"""
from __future__ import annotations

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
from app.services.seed_tenant_data import (  # noqa: E402
    _format_sql_value,
    apply_seed_data,
    build_insert_sql,
    plan_seed_data,
)


# ── Fixture helpers ──────────────────────────────────────────────────

def _make_menu_table() -> TableDefinition:
    return TableDefinition(
        name="menu_items",
        singular_label="Menu Item",
        plural_label="Menu Items",
        description="Restaurant menu items.",
        fields=[
            FieldDefinition(name="name", type="text", required=True),
            FieldDefinition(name="price_cents", type="integer", required=True),
            FieldDefinition(name="is_available", type="boolean"),
            FieldDefinition(name="photo_url", type="image_url"),
            FieldDefinition(name="description", type="text"),
        ],
    )


def _make_data_model_one_table() -> DataModel:
    return DataModel(
        version="1.0",
        tables=[_make_menu_table()],
        singletons={},
    )


# ── _format_sql_value ────────────────────────────────────────────────

def test_format_value_string_escapes_single_quotes():
    f = FieldDefinition(name="name", type="text")
    assert _format_sql_value("chef's special", f) == "'chef''s special'"


def test_format_value_none_renders_null():
    f = FieldDefinition(name="description", type="text")
    assert _format_sql_value(None, f) == "NULL"


def test_format_value_integer_passes_through():
    f = FieldDefinition(name="price_cents", type="integer")
    assert _format_sql_value(1850, f) == "1850"


def test_format_value_integer_coerces_string_with_currency():
    f = FieldDefinition(name="price_cents", type="integer")
    # "$18,50" → strip → 1850. Defensive against Gemini drift.
    assert _format_sql_value("$1850", f) == "1850"


def test_format_value_boolean_native():
    f = FieldDefinition(name="is_available", type="boolean")
    assert _format_sql_value(True, f) == "TRUE"
    assert _format_sql_value(False, f) == "FALSE"


def test_format_value_boolean_from_string():
    f = FieldDefinition(name="is_available", type="boolean")
    assert _format_sql_value("true", f) == "TRUE"
    assert _format_sql_value("False", f) == "FALSE"


def test_format_value_json_dumps_and_escapes():
    f = FieldDefinition(name="meta", type="json")
    rendered = _format_sql_value({"k": "v's"}, f)
    assert rendered.startswith("'") and rendered.endswith("'::jsonb")
    assert "v''s" in rendered  # escaped quote


# ── build_insert_sql ─────────────────────────────────────────────────

def test_build_insert_sql_basic():
    table = _make_menu_table()
    sql = build_insert_sql(
        table, "tenant_abc", [
            {"name": "Pizza", "price_cents": 1500, "is_available": True},
            {"name": "Carbonara", "price_cents": 1850, "is_available": False},
        ],
    )
    # Single qualified target
    assert "INSERT INTO tenant_abc.menu_items" in sql
    # Columns are exactly the keys actually provided in the batch
    assert "(name, price_cents, is_available)" in sql
    # Values escaped + typed
    assert "'Pizza'" in sql and "1500" in sql and "TRUE" in sql
    assert "'Carbonara'" in sql and "1850" in sql and "FALSE" in sql
    assert sql.strip().endswith(";")


def test_build_insert_sql_drops_unknown_columns():
    table = _make_menu_table()
    sql = build_insert_sql(
        table, "tenant_abc",
        [{"name": "Pizza", "made_up_column": "ignored"}],
    )
    assert "made_up_column" not in sql
    assert "name" in sql


def test_build_insert_sql_raises_on_empty_rows():
    table = _make_menu_table()
    with pytest.raises(ValueError):
        build_insert_sql(table, "tenant_abc", [])


def test_build_insert_sql_uses_union_of_columns():
    """Different rows can provide different subsets of columns; the
    column list should be the union, with NULL filled in for missing."""
    table = _make_menu_table()
    sql = build_insert_sql(
        table, "tenant_abc", [
            {"name": "Pizza", "price_cents": 1500},
            {"name": "Pasta", "is_available": True},
        ],
    )
    # All three columns appear once each
    assert sql.count("name") >= 1
    assert sql.count("price_cents") >= 1
    assert sql.count("is_available") >= 1
    # Second row had no price_cents → NULL in that position
    assert "NULL" in sql


# ── plan_seed_data (with mocked Gemini) ──────────────────────────────

@pytest.mark.asyncio
async def test_plan_seed_data_returns_empty_when_no_tables():
    empty_model = DataModel(version="1.0", tables=[], singletons={})
    result = await plan_seed_data(
        empty_model, {}, {}, {},
        gemini_key="", project_id="test",
    )
    assert result == {}


@pytest.mark.asyncio
async def test_plan_seed_data_filters_unknown_tables_and_fields():
    data_model = _make_data_model_one_table()
    gemini_blob = """
    {
      "menu_items": [
        {"name": "Pizza", "price_cents": 1500, "junk_col": "drop"},
        {"name": "Pasta", "price_cents": 1850}
      ],
      "unknown_table": [{"x": 1}]
    }
    """
    with patch(
        "app.services.seed_tenant_data.structured_distill",
        new=AsyncMock(return_value=gemini_blob),
    ):
        result = await plan_seed_data(
            data_model, {}, {}, {},
            gemini_key="", project_id="test",
        )
    assert "unknown_table" not in result
    assert "menu_items" in result
    assert len(result["menu_items"]) == 2
    # junk_col should have been dropped
    assert all("junk_col" not in row for row in result["menu_items"])


@pytest.mark.asyncio
async def test_plan_seed_data_strips_markdown_fence():
    data_model = _make_data_model_one_table()
    gemini_blob = '```json\n{"menu_items": [{"name": "X"}]}\n```'
    with patch(
        "app.services.seed_tenant_data.structured_distill",
        new=AsyncMock(return_value=gemini_blob),
    ):
        result = await plan_seed_data(
            data_model, {}, {}, {},
            gemini_key="", project_id="test",
        )
    assert result == {"menu_items": [{"name": "X"}]}


@pytest.mark.asyncio
async def test_plan_seed_data_clamps_row_count():
    """If Gemini emits 50 rows, we keep at most _MAX_ROWS_PER_TABLE (12)."""
    data_model = _make_data_model_one_table()
    rows = [{"name": f"Item {i}", "price_cents": 100 * i} for i in range(50)]
    import json as _json
    gemini_blob = _json.dumps({"menu_items": rows})
    with patch(
        "app.services.seed_tenant_data.structured_distill",
        new=AsyncMock(return_value=gemini_blob),
    ):
        result = await plan_seed_data(
            data_model, {}, {}, {},
            gemini_key="", project_id="test",
        )
    assert len(result["menu_items"]) <= 12


@pytest.mark.asyncio
async def test_plan_seed_data_returns_empty_on_gemini_raise():
    data_model = _make_data_model_one_table()
    with patch(
        "app.services.seed_tenant_data.structured_distill",
        new=AsyncMock(side_effect=RuntimeError("simulated 500")),
    ):
        result = await plan_seed_data(
            data_model, {}, {}, {},
            gemini_key="", project_id="test",
        )
    assert result == {}


@pytest.mark.asyncio
async def test_plan_seed_data_returns_empty_on_garbled_json():
    data_model = _make_data_model_one_table()
    with patch(
        "app.services.seed_tenant_data.structured_distill",
        new=AsyncMock(return_value="not json at all"),
    ):
        result = await plan_seed_data(
            data_model, {}, {}, {},
            gemini_key="", project_id="test",
        )
    assert result == {}


# ── apply_seed_data ──────────────────────────────────────────────────

def _make_admin_with_execute_ddl(*, raises: Exception | None = None) -> MagicMock:
    client = MagicMock()
    rpc_call = MagicMock()
    rpc_call.execute = AsyncMock(
        side_effect=raises if raises is not None else None,
    )
    client.rpc.return_value = rpc_call
    return client


@pytest.mark.asyncio
async def test_apply_seed_data_inserts_and_resolves_images():
    data_model = _make_data_model_one_table()
    seed_data = {
        "menu_items": [
            {"name": "Pizza", "price_cents": 1500,
             "photo_url": "warm cozy italian dining room"},
        ],
    }
    image_search = AsyncMock(return_value=[
        {"url": "https://images.unsplash.com/photo-XXX"},
    ])
    admin = _make_admin_with_execute_ddl()

    result = await apply_seed_data(
        seed_data, data_model, "tenant_abc", admin,
        image_search=image_search,
    )

    assert result["success"] is True
    assert result["tables_inserted"] == 1
    assert result["rows_inserted"] == 1
    # The Unsplash search was called with the Gemini-emitted query
    image_search.assert_awaited_with(
        "warm cozy italian dining room", count=1,
    )
    # And the rendered SQL included the resolved URL (not the search query)
    rpc_kwargs = admin.rpc.call_args_list[0]
    rpc_args = rpc_kwargs.args
    sent_sql = rpc_args[1]["p_sql"]
    assert "https://images.unsplash.com/photo-XXX" in sent_sql
    assert "warm cozy italian dining room" not in sent_sql


@pytest.mark.asyncio
async def test_apply_seed_data_leaves_existing_http_urls_alone():
    data_model = _make_data_model_one_table()
    seed_data = {
        "menu_items": [
            {"name": "Pizza", "price_cents": 1500,
             "photo_url": "https://example.com/already.jpg"},
        ],
    }
    image_search = AsyncMock(return_value=[
        {"url": "https://should-not-be-used"},
    ])
    admin = _make_admin_with_execute_ddl()

    await apply_seed_data(
        seed_data, data_model, "tenant_abc", admin,
        image_search=image_search,
    )
    image_search.assert_not_awaited()


@pytest.mark.asyncio
async def test_apply_seed_data_reports_table_on_rpc_failure():
    data_model = _make_data_model_one_table()
    seed_data = {
        "menu_items": [{"name": "X", "price_cents": 1}],
    }
    admin = _make_admin_with_execute_ddl(raises=RuntimeError("23502: NOT NULL"))

    result = await apply_seed_data(
        seed_data, data_model, "tenant_abc", admin,
        image_search=AsyncMock(),
    )
    assert result["success"] is False
    assert result["failed_table"] == "menu_items"
    assert "23502" in result["error"]


@pytest.mark.asyncio
async def test_apply_seed_data_handles_empty_input():
    data_model = _make_data_model_one_table()
    admin = _make_admin_with_execute_ddl()
    result = await apply_seed_data(
        {}, data_model, "tenant_abc", admin,
        image_search=AsyncMock(),
    )
    assert result["success"] is True
    assert result["rows_inserted"] == 0
    admin.rpc.assert_not_called()
