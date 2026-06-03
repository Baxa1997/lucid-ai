"""Pure regression tests for project data route helpers.

These do not touch Supabase. The live RPC behavior is already covered by
test_set_tenant_row.py and test_get_tenant_collection_authenticated.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.routers import projects  # noqa: E402


DATA_MODEL = {
    "version": "1.0",
    "tables": [
        {
            "name": "services",
            "singular_label": "Service",
            "plural_label": "Services",
            "description": "Editable service cards.",
            "fields": [
                {"name": "title", "type": "text", "required": True},
                {"name": "price", "type": "number"},
                {"name": "is_featured", "type": "boolean"},
                {"name": "image", "type": "image_url"},
            ],
            "public_read": True,
        }
    ],
}


def test_get_table_definition_requires_known_snake_case_table():
    assert projects._get_table_definition(DATA_MODEL, "services")["plural_label"] == "Services"
    assert projects._get_table_definition(DATA_MODEL, "unknown") is None
    assert projects._get_table_definition(DATA_MODEL, "services;drop") is None


def test_clean_payload_keeps_declared_editable_fields_only():
    table = projects._get_table_definition(DATA_MODEL, "services")
    cleaned = projects._clean_payload_for_table(
        table,
        {
            "id": "never",
            "title": "Consulting",
            "price": 250,
            "created_at": "never",
            "tenant_schema": "never",
            "image": "https://example.com/photo.png",
        },
    )
    assert cleaned == {
        "title": "Consulting",
        "price": 250,
        "image": "https://example.com/photo.png",
    }


def test_validate_order_by_allows_model_and_system_columns():
    table = projects._get_table_definition(DATA_MODEL, "services")
    assert projects._validate_order_by(table, "title") == "title"
    assert projects._validate_order_by(table, "created_at") == "created_at"


def test_validate_order_by_rejects_unknown_or_bad_identifier():
    table = projects._get_table_definition(DATA_MODEL, "services")
    with pytest.raises(HTTPException):
        projects._validate_order_by(table, "tenant_schema")
    with pytest.raises(HTTPException):
        projects._validate_order_by(table, "title desc")
