"""Unit tests for `app.services.admin_plan.build_admin_plan`.

Pure deterministic logic — no LLM, no I/O, no mocks. The plan output
shape is the contract Stage 5 (foundation) and Stage 6 (CRUD codegen)
will read from, so these tests double as the schema check.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.admin_plan import build_admin_plan  # noqa: E402
from app.services.data_model import (  # noqa: E402
    DataModel,
    FieldDefinition,
    TableDefinition,
)


def _table(name: str, singular: str, plural: str) -> TableDefinition:
    return TableDefinition(
        name=name,
        singular_label=singular,
        plural_label=plural,
        description=f"{plural} for the admin.",
        fields=[
            FieldDefinition(name="name", type="text", required=True),
        ],
        public_read=False,
    )


def _make_three_table_model() -> DataModel:
    return DataModel(
        version="1.0",
        tables=[
            _table("contacts", "Contact", "Contacts"),
            _table("leads",    "Lead",    "Leads"),
            _table("orders",   "Order",   "Orders"),
        ],
        singletons={},
    )


def _visual_dna(brand: str = "OpsCo", color: str = "#0f172a") -> dict:
    return {
        "brand_name":    brand,
        "primary_color": color,
        "logo_url":      None,
    }


# ─────────────────────────────────────────────────────────────────────
#  TestAdminPlanGeneration
# ─────────────────────────────────────────────────────────────────────

class TestAdminPlanGeneration:

    def test_each_table_gets_three_pages(self):
        plan = build_admin_plan(_make_three_table_model(), _visual_dna())
        pages = plan["pages"]

        # 3 shared pages (login, dashboard, layout) + 3 per entity × 3 entities = 12
        assert len(pages) == 12, (
            f"expected 12 pages (3 shared + 9 entity), got {len(pages)}: "
            f"{[p['route'] for p in pages]}"
        )

        # Per-entity assertions: list / new / edit
        for entity in ("contacts", "leads", "orders"):
            page_types = sorted(
                p["page_type"] for p in pages if p.get("entity") == entity
            )
            assert page_types == ["create", "edit", "list"], (
                f"{entity} pages missing one of create/edit/list: {page_types}"
            )

        # Routes follow the spec'd shape
        contact_routes = sorted(
            p["route"] for p in pages if p.get("entity") == "contacts"
        )
        assert contact_routes == [
            "/contacts",
            "/contacts/:id",
            "/contacts/new",
        ]

    def test_navigation_includes_each_entity(self):
        plan = build_admin_plan(_make_three_table_model(), _visual_dna())
        nav = plan["navigation"]

        # Dashboard always first, then one item per table
        assert nav[0]["label"] == "Dashboard"
        assert nav[0]["route"] == "/"

        entity_routes = [item["route"] for item in nav[1:]]
        assert entity_routes == [
            "/contacts",
            "/leads",
            "/orders",
        ]

        # Every nav item carries an icon string
        for item in nav:
            assert isinstance(item.get("icon"), str) and item["icon"], (
                f"nav item {item!r} missing icon"
            )

    def test_branding_inherited_from_visual_dna(self):
        plan = build_admin_plan(
            _make_three_table_model(),
            _visual_dna(brand="MyAdmin", color="#ff0066"),
        )
        assert plan["branding"]["brand_name"] == "MyAdmin"
        assert plan["branding"]["primary_color"] == "#ff0066"
        # The compatibility `brand` shim still resolves to the brand name
        # so Stage 6 codegen can read `plan["brand"]["name"]`.
        assert plan["brand"]["name"] == "MyAdmin"

    def test_empty_data_model_produces_minimal_plan(self):
        empty = DataModel(version="1.0", tables=[], singletons={})
        plan = build_admin_plan(empty, _visual_dna())

        # Only the 3 shared pages (login, dashboard, layout)
        assert len(plan["pages"]) == 3, (
            f"expected 3 shared pages, got {len(plan['pages'])}"
        )
        page_types = sorted(p["page_type"] for p in plan["pages"])
        assert page_types == ["auth", "dashboard", "layout"]

        # Nav has only Dashboard
        assert plan["navigation"] == [
            {"label": "Dashboard", "route": "/", "icon": "home"},
        ]

    def test_table_with_snake_case_name_becomes_kebab_route(self):
        """Multi-word table names like `purchase_orders` should route
        as `/purchase-orders` in the React admin app."""
        dm = DataModel(
            version="1.0",
            tables=[_table("purchase_orders", "Purchase Order", "Purchase Orders")],
            singletons={},
        )
        plan = build_admin_plan(dm, _visual_dna())
        routes = [p["route"] for p in plan["pages"] if p.get("entity") == "purchase_orders"]
        assert "/purchase-orders" in routes
        assert "/purchase-orders/new" in routes
        assert "/purchase-orders/:id" in routes

    def test_icon_picker_matches_keyword(self):
        """The icon mapping table is small; assert a couple of expected
        matches so future renames of the icon-lookup don't silently
        break sidebar emission."""
        dm = DataModel(
            version="1.0",
            tables=[
                _table("contacts",       "Contact",     "Contacts"),
                _table("orders",         "Order",       "Orders"),
                _table("appointments",   "Appointment", "Appointments"),
                _table("widgets",        "Widget",      "Widgets"),  # unmapped
            ],
            singletons={},
        )
        plan = build_admin_plan(dm, _visual_dna())
        nav = {item["route"]: item["icon"] for item in plan["navigation"]}
        assert nav["/contacts"]     == "users"
        assert nav["/orders"]       == "shopping-cart"
        assert nav["/appointments"] == "calendar-check"
        # Unmapped → default `table` icon
        assert nav["/widgets"]      == "table"

    def test_pages_carry_page_name_field(self):
        """Stage 6 reads `page_name` for the breadcrumb / window title."""
        plan = build_admin_plan(_make_three_table_model(), _visual_dna())
        for page in plan["pages"]:
            assert "page_name" in page, f"page {page!r} missing page_name"
            assert isinstance(page["page_name"], str) and page["page_name"], (
                f"page_name empty for {page!r}"
            )
