"""Tests for the DataModel schema (services/data_model.py).

Pure unit tests — no Supabase, no LLM. The module is the data
*contract* between website + admin generators, so the tests focus on
the rules a downstream consumer can rely on:
  • Valid models pass `validate_data_model` with zero errors
  • Pydantic rejects unknown field types at construction time
  • Snake-case is enforced for table + field names
  • Duplicate tables and duplicate fields-within-table are flagged
  • enum_values coherence (only with type='text', non-empty)
  • indexes must reference real fields of their table
  • Normalization is total: garbage in → safe-or-empty out
  • Round-trips through JSON without loss
"""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.services.data_model import (
    DataModel,
    FieldDefinition,
    TableDefinition,
    normalize_field_name,
    normalize_table_name,
    validate_data_model,
)


# ── Fixtures ──────────────────────────────────────────────────────────

def _valid_restaurant_model() -> DataModel:
    """A complete-ish restaurant data model used as a positive-control
    fixture across several tests."""
    return DataModel(
        tables=[
            TableDefinition(
                name="menu_items",
                singular_label="Menu Item",
                plural_label="Menu Items",
                description="Dishes the restaurant sells.",
                fields=[
                    FieldDefinition(name="name", type="text", required=True, max_length=120),
                    FieldDefinition(name="description", type="text"),
                    FieldDefinition(name="price_cents", type="number", required=True),
                    FieldDefinition(name="photo_url", type="image_url"),
                    FieldDefinition(
                        name="category",
                        type="text",
                        enum_values=["starter", "main", "dessert", "drink"],
                    ),
                    FieldDefinition(name="is_available", type="boolean", default="true"),
                ],
                indexes=["category"],
                public_read=True,
            ),
            TableDefinition(
                name="reservations",
                singular_label="Reservation",
                plural_label="Reservations",
                description="Bookings made by guests.",
                fields=[
                    FieldDefinition(name="guest_name", type="text", required=True),
                    FieldDefinition(name="guest_email", type="email", required=True),
                    FieldDefinition(name="party_size", type="number", required=True),
                    FieldDefinition(name="reserved_for", type="datetime", required=True),
                ],
                public_read=False,  # admin-only
            ),
        ],
        singletons={
            "hero": {"title": "Golden Dragon", "tagline": "Modern Sichuan, downtown."},
            "footer": {"phone": "+1-555-0100"},
        },
    )


# ── Happy path ────────────────────────────────────────────────────────

def test_valid_model_has_no_errors():
    errors = validate_data_model(_valid_restaurant_model())
    assert errors == [], f"expected zero errors, got {errors!r}"


def test_each_pydantic_class_has_a_docstring():
    """The data model is a contract — every class must self-document."""
    for cls in (DataModel, TableDefinition, FieldDefinition):
        assert cls.__doc__ and cls.__doc__.strip(), (
            f"{cls.__name__} is missing a class docstring"
        )


# ── Pydantic-level rejections ─────────────────────────────────────────

def test_unknown_field_type_rejected_at_construction():
    with pytest.raises(ValidationError):
        FieldDefinition(name="oops", type="binary")  # type: ignore[arg-type]


def test_extra_keys_rejected():
    """`extra='forbid'` means emitters can't smuggle in unknown keys."""
    with pytest.raises(ValidationError):
        FieldDefinition.model_validate({
            "name": "x", "type": "text", "secret_flag": True,
        })


# ── Naming / snake_case ───────────────────────────────────────────────

def test_non_snakecase_table_name_flagged():
    model = DataModel(tables=[
        TableDefinition(
            name="MenuItems",  # PascalCase — not allowed
            singular_label="Menu Item",
            plural_label="Menu Items",
            description="x",
            fields=[FieldDefinition(name="name", type="text")],
        ),
    ])
    errors = validate_data_model(model)
    assert any("snake_case" in e for e in errors), errors


def test_non_snakecase_field_name_flagged():
    model = DataModel(tables=[
        TableDefinition(
            name="menu_items",
            singular_label="Menu Item",
            plural_label="Menu Items",
            description="x",
            fields=[FieldDefinition(name="PriceCents", type="number")],
        ),
    ])
    errors = validate_data_model(model)
    assert any("snake_case" in e for e in errors), errors


def test_table_name_starting_with_digit_flagged():
    model = DataModel(tables=[
        TableDefinition(
            name="3things",
            singular_label="x", plural_label="x", description="x",
            fields=[FieldDefinition(name="name", type="text")],
        ),
    ])
    errors = validate_data_model(model)
    assert any("snake_case" in e for e in errors), errors


# ── Duplicates ────────────────────────────────────────────────────────

def test_duplicate_table_names_flagged():
    model = DataModel(tables=[
        TableDefinition(
            name="posts", singular_label="Post", plural_label="Posts",
            description="x", fields=[FieldDefinition(name="title", type="text")],
        ),
        TableDefinition(
            name="posts", singular_label="Post", plural_label="Posts",
            description="x", fields=[FieldDefinition(name="title", type="text")],
        ),
    ])
    errors = validate_data_model(model)
    assert any("duplicate table" in e for e in errors), errors


def test_duplicate_field_names_within_table_flagged():
    model = DataModel(tables=[
        TableDefinition(
            name="posts", singular_label="Post", plural_label="Posts",
            description="x",
            fields=[
                FieldDefinition(name="title", type="text"),
                FieldDefinition(name="title", type="text"),
            ],
        ),
    ])
    errors = validate_data_model(model)
    assert any("duplicate field" in e for e in errors), errors


# ── enum_values coherence ────────────────────────────────────────────

def test_enum_values_on_non_text_field_flagged():
    model = DataModel(tables=[
        TableDefinition(
            name="x", singular_label="x", plural_label="x", description="x",
            fields=[
                FieldDefinition(
                    name="n", type="number", enum_values=["1", "2"],
                ),
            ],
        ),
    ])
    errors = validate_data_model(model)
    assert any("enum_values" in e for e in errors), errors


def test_empty_enum_values_flagged():
    model = DataModel(tables=[
        TableDefinition(
            name="x", singular_label="x", plural_label="x", description="x",
            fields=[FieldDefinition(name="s", type="text", enum_values=[])],
        ),
    ])
    errors = validate_data_model(model)
    assert any("empty" in e for e in errors), errors


# ── required + default coherence ─────────────────────────────────────

def test_required_with_null_default_flagged():
    model = DataModel(tables=[
        TableDefinition(
            name="x", singular_label="x", plural_label="x", description="x",
            fields=[
                FieldDefinition(
                    name="s", type="text", required=True, default="NULL",
                ),
            ],
        ),
    ])
    errors = validate_data_model(model)
    assert any("cannot default to NULL" in e for e in errors), errors


def test_required_with_real_default_is_fine():
    """required=True + non-NULL default is valid (e.g. status='pending')."""
    model = DataModel(tables=[
        TableDefinition(
            name="orders", singular_label="Order", plural_label="Orders",
            description="x",
            fields=[
                FieldDefinition(
                    name="status", type="text", required=True, default="'pending'",
                ),
            ],
        ),
    ])
    assert validate_data_model(model) == []


# ── Default coercion ──────────────────────────────────────────────────
# Gemini Pro emits SQL defaults in their natural JSON type — false for
# BOOLEAN DEFAULT FALSE, 0 for INTEGER DEFAULT 0. The Pydantic validator
# coerces these to the SQL-literal string form so the downstream SQL
# generator can keep treating .default as str.

def test_default_coerces_bool_false_to_sql_literal():
    f = FieldDefinition(name="is_featured", type="boolean", default=False)
    assert f.default == "FALSE"


def test_default_coerces_bool_true_to_sql_literal():
    f = FieldDefinition(name="is_active", type="boolean", default=True)
    assert f.default == "TRUE"


def test_default_coerces_int_zero():
    f = FieldDefinition(name="quantity", type="integer", default=0)
    assert f.default == "0"


def test_default_coerces_float():
    f = FieldDefinition(name="rating", type="number", default=4.5)
    assert f.default == "4.5"


def test_default_string_passes_through():
    f = FieldDefinition(name="status", type="text", default="'pending'")
    assert f.default == "'pending'"


def test_default_none_stays_none():
    f = FieldDefinition(name="notes", type="text")
    assert f.default is None


# ── Indexes ───────────────────────────────────────────────────────────

def test_index_on_unknown_field_flagged():
    model = DataModel(tables=[
        TableDefinition(
            name="posts", singular_label="Post", plural_label="Posts",
            description="x",
            fields=[FieldDefinition(name="title", type="text")],
            indexes=["category"],  # not a field
        ),
    ])
    errors = validate_data_model(model)
    assert any("indexes" in e and "category" in e for e in errors), errors


# ── Normalization ─────────────────────────────────────────────────────

@pytest.mark.parametrize("raw, expected", [
    ("Menu Item Name", "menu_item_name"),
    ("created-at", "created_at"),
    ("Price (cents)", "price_cents"),
    ("URL", "url"),
    ("___leading", "leading"),
    ("trailing___", "trailing"),
    ("  multi   spaces  ", "multi_spaces"),
    ("", ""),
    ("___", ""),
])
def test_normalize_field_name(raw, expected):
    assert normalize_field_name(raw) == expected


@pytest.mark.parametrize("raw, expected", [
    ("menu items", "menu_items"),
    ("Blog Posts", "blog_posts"),
    ("ProductCategories", "productcategories"),  # no camel-split — by design
])
def test_normalize_table_name(raw, expected):
    assert normalize_table_name(raw) == expected


# ── Round-trip ────────────────────────────────────────────────────────

def test_json_roundtrip_preserves_model():
    original = _valid_restaurant_model()
    raw = original.model_dump_json()
    rebuilt = DataModel.model_validate_json(raw)
    assert rebuilt.model_dump() == original.model_dump()


def test_dict_roundtrip_preserves_model():
    original = _valid_restaurant_model()
    rebuilt = DataModel.model_validate(json.loads(original.model_dump_json()))
    assert rebuilt == original


# ── Singletons ────────────────────────────────────────────────────────

def test_singletons_are_opaque_freeform_json():
    """Singletons store arbitrary JSON — validator doesn't inspect them."""
    model = DataModel(
        tables=[],
        singletons={
            "hero": {"title": "Hi", "ctas": [{"label": "Book", "url": "/book"}]},
            "footer_phone": "+1-555-0100",
            "feature_count": 42,
            "is_dark_mode": True,
        },
    )
    assert validate_data_model(model) == []
    raw = model.model_dump()
    assert raw["singletons"]["feature_count"] == 42
    assert raw["singletons"]["is_dark_mode"] is True


# ── Empty model ───────────────────────────────────────────────────────

def test_empty_model_is_valid():
    """A project with zero tables and zero singletons is technically valid
    (some landings have no collections at all)."""
    assert validate_data_model(DataModel()) == []
