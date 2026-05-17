"""Deterministic admin-page plan from a `DataModel`.

Pure derivation — no LLM, no Gemini, no Claude. Given a validated
DataModel, produces the page + navigation structure Stages 5/6 will
emit code for.

The output shape mirrors what the website pipeline's `build_website_plan`
returns (pages + brand), but the page items carry an extra
`page_type` + `entity` field so Stage 6 CRUD codegen knows which
template to render.

Why deterministic
-----------------
For admin panels the page set is rote: list / new / edit for every
entity, plus a fixed (login, layout, dashboard) trio. An LLM here
would burn budget producing the same shape every time. The data_model
is the only input that varies, and we have it.

Icon mapping
------------
We default to `lucide-react` icon names because shadcn/ui templates
ship Lucide. A built-in keyword lookup picks a sensible icon per
entity name; Step 3.5 (foundation) will hard-link these into the
generated sidebar component.
"""
from __future__ import annotations

import logging
from typing import Any

from app.services.data_model import DataModel, TableDefinition

logger = logging.getLogger(__name__)


# ── Icon mapping ─────────────────────────────────────────────────────
# Keys are substring matches against the table.name (snake_case plural).
# First match wins; unmatched tables get the generic `table` icon.
# This list is intentionally short — Step 3.5 may swap the icon library
# (heroicons, tabler) without re-tuning here. Adding a synonym is one line.
_ICON_BY_NAME_SUBSTRING: list[tuple[str, str]] = [
    ("lead",       "user-plus"),
    ("contact",    "users"),
    ("customer",   "users"),
    ("client",     "users"),
    ("patient",    "users"),
    ("tenant",     "users"),
    ("student",    "graduation-cap"),
    ("employee",   "user-cog"),
    ("staff",      "user-cog"),
    ("driver",     "truck"),
    ("agent",      "headphones"),
    ("ticket",     "ticket"),
    ("order",      "shopping-cart"),
    ("invoice",    "file-text"),
    ("payment",    "credit-card"),
    ("quote",      "file-text"),
    ("transaction","arrow-left-right"),
    ("product",    "package"),
    ("item",       "package"),
    ("inventory",  "boxes"),
    ("property",   "home"),
    ("vehicle",    "car"),
    ("shipment",   "truck"),
    ("route",      "map"),
    ("delivery",   "truck"),
    ("reservation","calendar"),
    ("booking",    "calendar"),
    ("appointment","calendar-check"),
    ("event",      "calendar"),
    ("viewing",    "calendar"),
    ("project",    "folder"),
    ("task",       "check-square"),
    ("milestone",  "flag"),
    ("course",     "book"),
    ("lesson",     "book-open"),
    ("grade",      "award"),
    ("deal",       "trending-up"),
    ("opportunity","trending-up"),
    ("activity",   "activity"),
    ("message",    "message-square"),
    ("note",       "sticky-note"),
    ("maintenance","wrench"),
    ("request",    "inbox"),
    ("supplier",   "truck"),
    ("article",    "file-text"),
    ("menu",       "utensils"),
    ("table",      "table"),  # generic fallback baked into the list too
]
_DEFAULT_ICON = "table"


def _pick_icon(table_name: str) -> str:
    lowered = (table_name or "").lower()
    for substring, icon in _ICON_BY_NAME_SUBSTRING:
        if substring in lowered:
            return icon
    return _DEFAULT_ICON


# ── Shared page set ──────────────────────────────────────────────────
# Every admin gets these three regardless of data_model contents.
# Kept as a module-level list so tests can assert presence without
# depending on the exact order pages are appended.
_SHARED_PAGES: list[dict[str, Any]] = [
    {
        "route":     "/admin/login",
        "page_type": "auth",
        "entity":    None,
        "page_name": "Sign In",
    },
    {
        "route":     "/admin",
        "page_type": "dashboard",
        "entity":    None,
        "page_name": "Dashboard",
    },
    # The layout isn't a "page" in the Next.js routable sense, but Stage 6
    # treats it as one for codegen purposes — same template per emit.
    {
        "route":     "/admin/_layout",
        "page_type": "layout",
        "entity":    None,
        "page_name": "Admin Layout",
    },
]


def _entity_pages_for(table: TableDefinition) -> list[dict[str, Any]]:
    """Standard CRUD page trio for one entity table."""
    slug = table.name.replace("_", "-")
    return [
        {
            "route":     f"/admin/{slug}",
            "page_type": "list",
            "entity":    table.name,
            "page_name": table.plural_label or table.name,
        },
        {
            "route":     f"/admin/{slug}/new",
            "page_type": "create",
            "entity":    table.name,
            "page_name": f"Add {table.singular_label or table.name}",
        },
        {
            "route":     f"/admin/{slug}/[id]",
            "page_type": "edit",
            "entity":    table.name,
            "page_name": f"Edit {table.singular_label or table.name}",
        },
    ]


def build_admin_plan(
    data_model: DataModel,
    visual_dna: dict[str, Any],
) -> dict[str, Any]:
    """Derive the admin's page + navigation structure from a DataModel.

    The output dict has three top-level keys:
      • `pages` — every Next.js page the admin needs (entity CRUD +
        shared shell). Each page carries `page_type` ('list'/'create'/
        'edit'/'dashboard'/'auth'/'layout') and `entity` (the
        table.name, or None for shared shells).
      • `navigation` — sidebar items. Always starts with Dashboard,
        then one entry per entity table.
      • `branding` — primary_color + brand_name pulled from visual_dna,
        used by the layout/sidebar component.

    Idempotent + deterministic. Re-running on the same inputs yields
    byte-identical output.
    """
    tables: list[TableDefinition] = list(data_model.tables) if data_model else []

    pages: list[dict[str, Any]] = list(_SHARED_PAGES)
    for table in tables:
        pages.extend(_entity_pages_for(table))

    navigation: list[dict[str, Any]] = [
        {"label": "Dashboard", "route": "/admin", "icon": "home"},
    ]
    for table in tables:
        slug = table.name.replace("_", "-")
        navigation.append({
            "label": table.plural_label or table.name,
            "route": f"/admin/{slug}",
            "icon":  _pick_icon(table.name),
        })

    branding = {
        "primary_color": (visual_dna or {}).get("primary_color"),
        "brand_name":    (visual_dna or {}).get("brand_name"),
        # Tagline isn't part of the spec but it's free to pass through
        # when present — Step 3.5's layout component will ignore it
        # gracefully if absent.
        "tagline":       (visual_dna or {}).get("tagline"),
    }

    logger.info(
        "build_admin_plan: %d entity table(s) → %d pages, %d nav items",
        len(tables), len(pages), len(navigation),
    )

    return {
        "pages":      pages,
        "navigation": navigation,
        "branding":   branding,
        "product":    "admin",
        # Kept for parity with website_plan output — Stage 6 codegen
        # reads brand.name from this shape. Mirror it here so the
        # admin pipeline doesn't need a separate accessor.
        "brand":      {
            "name":    branding["brand_name"] or "Admin",
            "tagline": branding["tagline"] or "",
        },
    }
