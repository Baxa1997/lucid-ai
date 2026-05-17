"""Tests for the LIVE COLLECTIONS prompt block emitted by page_generator.

Verifies that:
  • `_build_collections_block` returns an empty string when there are
    no tables in the data_model (the common case for landing pages).
  • It lists every table with its plural label and field names.
  • It teaches Claude the three non-negotiables: drop "use client",
    switch to async function, use getCollection() over JSON items.
  • Plumbing through `_build_user_prompt` does NOT break when
    `data_model` is None (backward compat with callers that don't pass it).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


from app.services.data_model import (  # noqa: E402
    DataModel,
    FieldDefinition,
    TableDefinition,
)
from app.services.page_generator import (  # noqa: E402
    _build_collections_block,
    _build_user_prompt,
)


def _make_data_model() -> DataModel:
    return DataModel(
        version="1.0",
        tables=[
            TableDefinition(
                name="menu_items",
                singular_label="Menu Item",
                plural_label="Menu Items",
                description="x",
                fields=[
                    FieldDefinition(name="name", type="text"),
                    FieldDefinition(name="price_cents", type="integer"),
                    FieldDefinition(name="description", type="text"),
                ],
            ),
            TableDefinition(
                name="testimonials",
                singular_label="Testimonial",
                plural_label="Testimonials",
                description="x",
                fields=[
                    FieldDefinition(name="quote", type="text"),
                    FieldDefinition(name="author", type="text"),
                ],
            ),
        ],
        singletons={},
    )


# ── _build_collections_block ─────────────────────────────────────────

def test_collections_block_empty_when_data_model_is_none():
    assert _build_collections_block(None) == ""


def test_collections_block_empty_when_no_tables():
    empty_model = DataModel(version="1.0", tables=[], singletons={})
    assert _build_collections_block(empty_model) == ""


def test_collections_block_lists_every_table():
    block = _build_collections_block(_make_data_model())
    assert "menu_items" in block
    assert "Menu Items" in block  # plural label
    assert "testimonials" in block
    assert "Testimonials" in block


def test_collections_block_lists_fields():
    block = _build_collections_block(_make_data_model())
    assert "name" in block
    assert "price_cents" in block
    assert "description" in block
    assert "quote" in block
    assert "author" in block


def test_collections_block_teaches_async_server_component():
    """Claude must know to (a) drop "use client", (b) use async function,
    (c) call getCollection() instead of reading items from JSON."""
    block = _build_collections_block(_make_data_model())
    assert "async function" in block
    # The block tells Claude to drop the directive. The directive itself
    # appears in the block text — but the surrounding instruction is what
    # matters; just check both pieces are present.
    assert "DROP" in block.upper() and "use client" in block
    assert "getCollection" in block
    # Singletons (title/intro) must remain on the JSON path
    assert "content." in block


def test_collections_block_warns_against_editable_on_items():
    block = _build_collections_block(_make_data_model())
    # Per-row content inside items.map() is NOT meant to be inline-editable.
    assert "items.map" in block
    assert "<Editable>" in block


# ── _build_user_prompt smoke (just plumbing) ─────────────────────────

def _minimal_page() -> dict:
    return {
        "route": "/",
        "title": "Home",
        "purpose": "Greet visitors",
        "sections": [{"type": "menu_preview", "purpose": "Show menu"}],
    }


def test_user_prompt_omits_collections_block_without_data_model():
    """Backward compat: callers that don't pass data_model still get a
    working prompt with no LIVE COLLECTIONS section."""
    prompt = _build_user_prompt(
        page=_minimal_page(),
        slug="home",
        section_specs=[{
            "type": "menu_preview", "component": "HomeMenuPreviewSection",
            "purpose": "Show menu", "anatomy": "",
        }],
        visual_dna={},
        page_images=None,
        data_model=None,
    )
    assert "LIVE COLLECTIONS" not in prompt


def test_user_prompt_includes_collections_block_with_data_model():
    prompt = _build_user_prompt(
        page=_minimal_page(),
        slug="home",
        section_specs=[{
            "type": "menu_preview", "component": "HomeMenuPreviewSection",
            "purpose": "Show menu", "anatomy": "",
        }],
        visual_dna={},
        page_images=None,
        data_model=_make_data_model(),
    )
    assert "LIVE COLLECTIONS" in prompt
    assert "menu_items" in prompt
    # The CONTENT / CODE SEPARATION block must still appear for
    # the singleton path on non-collection sections.
    assert "CONTENT" in prompt and "SEPARATION" in prompt
