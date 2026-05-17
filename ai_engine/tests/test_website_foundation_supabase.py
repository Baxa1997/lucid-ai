"""Tests for the Supabase plumbing emitted by `_build_foundation_files`.

Verifies that when a project has a provisioned tenant_schema AND a
data_model with at least one table, the foundation step writes:

  • .env.local                 — NEXT_PUBLIC_SUPABASE_URL + anon key + project_id
  • src/lib/supabase.js        — createClient(url, anonKey) export
  • src/lib/db.js              — getCollection(name) wrapper around the
                                 get_tenant_collection RPC

And that none of these files are written when ANY of the preconditions
is missing (no tenant_schema, no tables, non-UUID project_id).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


from app.services.data_model import (  # noqa: E402
    DataModel,
    FieldDefinition,
    TableDefinition,
)
from app.services.website_pipeline import (  # noqa: E402
    _build_foundation_files,
    _build_supabase_plumbing,
)


UUID_VALID = "11111111-2222-3333-4444-555555555555"


def _make_plan() -> dict:
    return {
        "brand": {"name": "Trattoria Bianca", "tagline": "Brooklyn neighborhood Italian"},
        "pages": [
            {"route": "/", "title": "Home"},
            {"route": "/menu", "title": "Menu"},
        ],
    }


def _make_data_model(with_tables: bool = True) -> DataModel:
    if not with_tables:
        return DataModel(version="1.0", tables=[], singletons={})
    return DataModel(
        version="1.0",
        tables=[
            TableDefinition(
                name="menu_items",
                singular_label="Menu Item",
                plural_label="Menu Items",
                description="x",
                fields=[FieldDefinition(name="name", type="text")],
            ),
        ],
        singletons={},
    )


# ── _build_supabase_plumbing (pure, isolated) ────────────────────────

def test_plumbing_env_local_includes_three_vars(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://abc.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "eyJTOKEN")
    env_local, _, _ = _build_supabase_plumbing(
        project_id=UUID_VALID,
        data_model=_make_data_model(),
    )
    assert "NEXT_PUBLIC_SUPABASE_URL=https://abc.supabase.co" in env_local
    assert "NEXT_PUBLIC_SUPABASE_ANON_KEY=eyJTOKEN" in env_local
    assert f"NEXT_PUBLIC_LUCID_PROJECT_ID={UUID_VALID}" in env_local


def test_plumbing_supabase_js_uses_anon_env_vars():
    _, supabase_js, _ = _build_supabase_plumbing(
        project_id=UUID_VALID,
        data_model=_make_data_model(),
    )
    assert "createClient" in supabase_js
    assert "NEXT_PUBLIC_SUPABASE_URL" in supabase_js
    assert "NEXT_PUBLIC_SUPABASE_ANON_KEY" in supabase_js
    # service_role MUST never appear in client-shipped code
    assert "service_role" not in supabase_js.lower()


def test_plumbing_db_js_wires_to_get_tenant_collection_rpc():
    _, _, db_js = _build_supabase_plumbing(
        project_id=UUID_VALID,
        data_model=_make_data_model(),
    )
    assert "get_tenant_collection" in db_js
    assert "p_project_id" in db_js
    assert "p_table_name" in db_js
    assert "getCollection" in db_js
    # PROJECT_ID is read from the public env var, never hardcoded
    assert "NEXT_PUBLIC_LUCID_PROJECT_ID" in db_js


def test_plumbing_db_js_lists_collections_as_doc_comment():
    """The autocomplete-style comment helps callers know what they can fetch."""
    _, _, db_js = _build_supabase_plumbing(
        project_id=UUID_VALID,
        data_model=_make_data_model(),
    )
    assert "menu_items" in db_js


# ── _build_foundation_files gating ───────────────────────────────────

def test_foundation_emits_supabase_files_when_tenant_present(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://abc.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "eyJTOKEN")
    files = _build_foundation_files(
        plan=_make_plan(),
        visual_dna={"cultural_intensity": "bold"},
        design_signal={},
        data_model=_make_data_model(),
        tenant_schema="tenant_abc",
        project_id=UUID_VALID,
    )
    assert ".env.local" in files
    assert "src/lib/supabase.js" in files
    assert "src/lib/db.js" in files


def test_foundation_skips_supabase_files_when_no_tenant_schema():
    files = _build_foundation_files(
        plan=_make_plan(),
        visual_dna={"cultural_intensity": "bold"},
        design_signal={},
        data_model=_make_data_model(),
        tenant_schema=None,           # not provisioned
        project_id=UUID_VALID,
    )
    assert ".env.local" not in files
    assert "src/lib/supabase.js" not in files
    assert "src/lib/db.js" not in files


def test_foundation_skips_when_data_model_has_no_tables():
    files = _build_foundation_files(
        plan=_make_plan(),
        visual_dna={"cultural_intensity": "bold"},
        design_signal={},
        data_model=_make_data_model(with_tables=False),
        tenant_schema="tenant_abc",
        project_id=UUID_VALID,
    )
    assert ".env.local" not in files


def test_foundation_skips_when_project_id_not_uuid():
    files = _build_foundation_files(
        plan=_make_plan(),
        visual_dna={"cultural_intensity": "bold"},
        design_signal={},
        data_model=_make_data_model(),
        tenant_schema="tenant_abc",
        project_id="_session_none_",
    )
    assert ".env.local" not in files


def test_foundation_still_emits_other_files_without_tenant():
    """Skipping the Supabase plumbing must not affect the other
    foundation files (site config, navigation, design-system)."""
    files = _build_foundation_files(
        plan=_make_plan(),
        visual_dna={"cultural_intensity": "bold"},
        design_signal={},
        data_model=None,
        tenant_schema=None,
        project_id="",
    )
    assert "src/config/site.js" in files
    assert "src/config/navigation.js" in files
    assert "src/lib/design-system.js" in files
