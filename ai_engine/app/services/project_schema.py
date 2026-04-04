"""Project schema builder — structured contract between Gemini research and Claude generation.

Transforms Gemini's free-text blueprint into a canonical JSON schema that ALL 3
generation phases consume.  This eliminates consistency bugs:
  - Entity in navigation but not in routes
  - Field in DataTable but not in form
  - Dashboard KPI referencing a non-existent entity
  - Navigation item without a corresponding page

The schema is the SINGLE SOURCE OF TRUTH for the entire project.

Usage:
    from app.services.project_schema import build_project_schema
    schema = await build_project_schema(research, description, stack, app_type, api_key, ws)
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

logger = logging.getLogger("lucid.project_schema")


# ╔══════════════════════════════════════════════════════════════╗
# ║  CANONICAL SCHEMA SHAPE                                      ║
# ║  This is the contract — every field has a defined meaning     ║
# ╚══════════════════════════════════════════════════════════════╝

EMPTY_SCHEMA: dict[str, Any] = {
    "brand": {
        "name": "",
        "tagline": "",
        "description": "",
        "domain": "",
    },
    "theme": {
        "primary": "",
        "primary_foreground": "",
        "secondary": "",
        "secondary_foreground": "",
        "background": "",
        "foreground": "",
        "card": "",
        "card_foreground": "",
        "muted": "",
        "muted_foreground": "",
        "accent": "",
        "accent_foreground": "",
        "destructive": "",
        "destructive_foreground": "",
        "border": "",
        "ring": "",
        "radius": "0.5rem",
        "chart_colors": [],
        "sidebar_bg": "",
        "sidebar_fg": "",
        "heading_font": "",
        "heading_font_url": "",
        "body_font": "",
        "body_font_url": "",
        "dark_mode": {},
    },
    "navigation": [],  # [{group, items: [{label, icon, path, badge?}]}]
    "entities": [],     # [{name, slug, fields: [{name, type, required, inList, inForm, options?}], mockData: [...]}]
    "pages": [],        # [{path, title, component, type: "crud_list"|"crud_form"|"dashboard"|"settings"|"custom"}]
    "sections": [],     # [{type, headline, subheadline, content, animation}]  (landing pages only)
    "dashboard": {
        "kpis": [],     # [{label, value, change, trend, icon, color}]
        "charts": [],   # [{type, title, dataKey, series: [{name, values}]}]
        "recent_table": {
            "title": "",
            "columns": [],
            "rows": [],
        },
    },
    "status_badges": {},  # {status_name: tailwind_classes}
    "design_system": {    # Deterministic tokens all components import
        "card_classes": "",
        "badge_variants": {},
        "section_spacing": "",
        "max_width": "",
        "page_animation": {},
        "card_hover": {},
        "stagger_animation": {},
    },
    "api_config": {
        "base_url_env": "",   # e.g. "VITE_API_URL"
        "base_url_default": "",  # e.g. "http://localhost:3001"
        "endpoints": [],  # [{entity, basePath}]
    },
    "mock_db": {},  # Full db.json content: {entity_slug: [...rows]}
}


# ╔══════════════════════════════════════════════════════════════╗
# ║  SCHEMA EXTRACTION PROMPT                                    ║
# ║  Transforms Gemini research text → structured JSON           ║
# ╚══════════════════════════════════════════════════════════════╝

_SCHEMA_SYSTEM = """You are a precise data architect.  Your ONLY job is to parse
unstructured research text into a strictly-typed JSON schema.

RULES:
1. Extract EVERY entity, field, page, and UI element mentioned in the research.
2. For entities: list ALL fields with proper types (string, number, boolean, enum, date, email, url, textarea, select, file).
3. For enum fields: list ALL possible values in an "options" array.
4. Mark fields as inList:true if they should appear in DataTable columns.
5. Mark fields as inForm:true if they should appear in create/edit forms.
6. Generate 15-20 rows of realistic mock data per entity.
7. Every navigation item MUST have a corresponding page in the pages array.
8. Every entity MUST have a corresponding /entity and /entity/:id route in pages.
9. Dashboard KPIs must reference real entity metrics.
10. Status badges: extract ALL statuses mentioned with semantic Tailwind classes.
11. design_system: extract the EXACT Tailwind classes for cards, badges, spacing.
12. api_config: generate REST API endpoints for each entity.
13. mock_db: create the complete db.json content with all entities and mock data.

Output ONLY valid JSON. No markdown, no explanation."""

_SCHEMA_USER_TEMPLATE = """Parse this research into a project schema.

PROJECT: {description}
APP TYPE: {app_type}
STACK: {stack}

=== RESEARCH TEXT ===
{research}
=== END RESEARCH ===

Return JSON with this EXACT structure:
{{
  "brand": {{
    "name": "string",
    "tagline": "string", 
    "description": "string (2-3 sentences for SEO)",
    "domain": "string (e.g. logistics, healthcare, finance)"
  }},
  "theme": {{
    "primary": "HSL value (e.g. 221.2 83.2% 53.3%)",
    "primary_foreground": "HSL value",
    "secondary": "HSL value",
    "secondary_foreground": "HSL value",
    "background": "HSL value",
    "foreground": "HSL value",
    "card": "HSL value",
    "card_foreground": "HSL value",
    "muted": "HSL value",
    "muted_foreground": "HSL value",
    "accent": "HSL value",
    "accent_foreground": "HSL value",
    "destructive": "HSL value",
    "destructive_foreground": "HSL value",
    "border": "HSL value",
    "ring": "HSL value",
    "radius": "0.5rem",
    "chart_colors": ["HSL value", "HSL value", "HSL value", "HSL value", "HSL value"],
    "sidebar_bg": "HSL value",
    "sidebar_fg": "HSL value",
    "heading_font": "Inter",
    "heading_font_url": "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap",
    "body_font": "Inter",
    "body_font_url": "",
    "dark_mode": {{
      "background": "HSL value",
      "foreground": "HSL value",
      "card": "HSL value",
      "card_foreground": "HSL value",
      "muted": "HSL value",
      "muted_foreground": "HSL value",
      "border": "HSL value"
    }}
  }},
  "navigation": [
    {{
      "group": "Main",
      "items": [
        {{"label": "Dashboard", "icon": "LayoutDashboard", "path": "/dashboard"}},
        {{"label": "Orders", "icon": "ShoppingCart", "path": "/orders", "badge": "12"}}
      ]
    }}
  ],
  "entities": [
    {{
      "name": "Order",
      "slug": "orders",
      "fields": [
        {{"name": "id", "type": "string", "required": true, "inList": true, "inForm": false}},
        {{"name": "orderNumber", "type": "string", "required": true, "inList": true, "inForm": false}},
        {{"name": "customer", "type": "string", "required": true, "inList": true, "inForm": true, "placeholder": "Customer name"}},
        {{"name": "email", "type": "email", "required": true, "inList": false, "inForm": true, "placeholder": "customer@example.com"}},
        {{"name": "status", "type": "enum", "required": true, "inList": true, "inForm": true, "options": ["pending", "processing", "delivered", "cancelled"]}},
        {{"name": "total", "type": "number", "required": true, "inList": true, "inForm": true, "placeholder": "0.00"}},
        {{"name": "date", "type": "date", "required": true, "inList": true, "inForm": true}},
        {{"name": "notes", "type": "textarea", "required": false, "inList": false, "inForm": true}}
      ],
      "mockData": [
        {{"id": "1", "orderNumber": "ORD-001", "customer": "John Smith", "email": "john@acme.com", "status": "delivered", "total": 1249.99, "date": "2025-01-15", "notes": "Express shipping"}},
        "... 15-20 realistic rows total"
      ]
    }}
  ],
  "pages": [
    {{"path": "/dashboard", "title": "Dashboard", "component": "DashboardPage", "type": "dashboard"}},
    {{"path": "/orders", "title": "Orders", "component": "OrderListPage", "type": "crud_list", "entity": "orders"}},
    {{"path": "/orders/new", "title": "New Order", "component": "OrderFormPage", "type": "crud_form", "entity": "orders"}},
    {{"path": "/orders/:id", "title": "Edit Order", "component": "OrderFormPage", "type": "crud_form", "entity": "orders"}},
    {{"path": "/settings", "title": "Settings", "component": "SettingsPage", "type": "settings"}}
  ],
  "sections": [],
  "dashboard": {{
    "kpis": [
      {{"label": "Total Orders", "value": "3,847", "change": "+12.5%", "trend": "up", "icon": "ShoppingCart", "color": "primary"}},
      "... exactly 4 KPIs that reference real entities"
    ],
    "charts": [
      {{"type": "area", "title": "Revenue Over Time", "xAxis": "month", "series": [{{"name": "Revenue", "values": [4200, 5100, 4800, 6200, 7100, 6800, 7500, 8200, 7900, 8800, 9200, 10100]}}], "colors": ["chart-1"]}},
      {{"type": "bar", "title": "Orders by Status", "data": [{{"name": "Delivered", "value": 145}}, {{"name": "Processing", "value": 87}}, {{"name": "Pending", "value": 23}}], "colors": ["chart-1", "chart-2", "chart-3"]}}
    ],
    "recent_table": {{
      "title": "Recent Orders",
      "columns": ["orderNumber", "customer", "status", "total", "date"],
      "rows": "first 8 rows from orders mockData"
    }}
  }},
  "status_badges": {{
    "active": "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-400",
    "pending": "bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-400",
    "inactive": "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400",
    "processing": "bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400"
  }},
  "design_system": {{
    "card_classes": "rounded-xl border border-border bg-card shadow-sm hover:shadow-md transition-shadow",
    "badge_variants": {{
      "default": "bg-primary/10 text-primary",
      "success": "bg-emerald-100 text-emerald-800",
      "warning": "bg-amber-100 text-amber-800",
      "destructive": "bg-red-100 text-red-800"
    }},
    "section_spacing": "py-24 px-4 sm:px-6 lg:px-8",
    "max_width": "max-w-7xl mx-auto",
    "page_animation": {{"initial": {{"opacity": 0, "y": 8}}, "animate": {{"opacity": 1, "y": 0}}, "transition": {{"duration": 0.15}}}},
    "card_hover": {{"whileHover": {{"y": -2}}, "transition": {{"duration": 0.1}}}},
    "stagger_animation": {{"container": {{"staggerChildren": 0.04}}, "child": {{"initial": {{"opacity": 0, "y": 20}}, "animate": {{"opacity": 1, "y": 0}}}}}}
  }},
  "api_config": {{
    "base_url_env": "VITE_API_URL",
    "base_url_default": "http://localhost:3001",
    "endpoints": [
      {{"entity": "orders", "basePath": "/orders"}}
    ]
  }},
  "mock_db": {{
    "orders": ["... the SAME mockData from entities[0].mockData, 15-20 rows"],
    "customers": ["... if customer entity exists"]
  }}
}}

For LANDING PAGES (not admin):
- "entities" should be empty []
- "sections" should list all page sections with content
- "dashboard" should be empty
- "mock_db" should be empty
- "navigation" should be header nav items
- "pages" should include /about, /pricing, /contact, /blog etc.

IMPORTANT: Generate 15-20 rows of REALISTIC mock data per entity.
Use real-sounding names, realistic numbers, proper date formats, varied statuses.
Every mock row must have ALL fields defined."""


# ╔══════════════════════════════════════════════════════════════╗
# ║  build_project_schema() — Main entry point                   ║
# ╚══════════════════════════════════════════════════════════════╝

async def build_project_schema(
    research: str,
    description: str,
    stack: str,
    app_type: str,
    api_key: str,
    websocket=None,
) -> dict[str, Any]:
    """Parse Gemini research into a structured project schema.
    
    Uses Claude Sonnet (fast + cheap) for parsing — NOT code generation.
    Cost: ~$0.05-0.10 per call (4-8K output tokens with Sonnet).
    
    Returns the canonical schema dict, or a fallback if parsing fails.
    """
    from app.services.project_generator import call_claude_for_json, _ws_send

    await _ws_send(websocket, "progress", "📐 Building project schema...")

    user_prompt = _SCHEMA_USER_TEMPLATE.format(
        description=description,
        app_type=app_type,
        stack=stack,
        research=research[:12000],  # Cap research to stay in context budget
    )

    # We need raw JSON output here, not file-based tool_use.
    # Use a direct Claude call without the write_project_files tool.
    import httpx

    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }

    payload = {
        "model": "claude-sonnet-4-6",  # Fast + cheap for parsing
        "max_tokens": 16000,
        "system": _SCHEMA_SYSTEM,
        "messages": [{"role": "user", "content": user_prompt}],
    }

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers=headers,
                json=payload,
            )

        if response.status_code != 200:
            logger.error("Schema parse API error %d: %s", response.status_code, response.text[:300])
            await _ws_send(websocket, "progress", "⚠️ Schema parse failed — using research text directly")
            return _build_fallback_schema(description, app_type)

        data = response.json()
        text = ""
        for block in data.get("content", []):
            if block.get("type") == "text":
                text += block["text"]

        if not text:
            logger.error("Schema parse returned empty text")
            return _build_fallback_schema(description, app_type)

        # Parse the JSON response
        schema = _extract_json(text)
        if not schema:
            logger.error("Failed to parse schema JSON from %d chars", len(text))
            return _build_fallback_schema(description, app_type)

        # Validate and fill missing fields
        schema = _validate_schema(schema)

        entity_count = len(schema.get("entities", []))
        page_count = len(schema.get("pages", []))
        section_count = len(schema.get("sections", []))
        nav_items = sum(len(g.get("items", [])) for g in schema.get("navigation", []))
        mock_rows = sum(len(e.get("mockData", [])) for e in schema.get("entities", []))

        await _ws_send(
            websocket, "progress",
            f"✅ Schema built: {entity_count} entities, {page_count} pages, "
            f"{nav_items} nav items, {mock_rows} mock data rows",
        )

        logger.info(
            "Schema built: entities=%d pages=%d sections=%d nav=%d mockRows=%d",
            entity_count, page_count, section_count, nav_items, mock_rows,
        )

        return schema

    except Exception as exc:
        logger.error("build_project_schema failed: %s", exc, exc_info=True)
        await _ws_send(websocket, "progress", "⚠️ Schema parse failed — using fallback")
        return _build_fallback_schema(description, app_type)


# ╔══════════════════════════════════════════════════════════════╗
# ║  SCHEMA HELPERS                                              ║
# ╚══════════════════════════════════════════════════════════════╝

def _extract_json(text: str) -> Optional[dict]:
    """Extract JSON from Claude's response text."""
    import re

    cleaned = text.strip()

    # Strip markdown code fences
    if cleaned.startswith("```"):
        first_nl = cleaned.find("\n")
        if first_nl != -1:
            cleaned = cleaned[first_nl + 1:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:cleaned.rfind("```")]
    cleaned = cleaned.strip()

    # Attempt direct parse
    try:
        result = json.loads(cleaned)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass

    # Find outermost { ... }
    start = cleaned.find("{")
    if start == -1:
        return None

    depth = 0
    last_valid_end = -1
    for i in range(start, len(cleaned)):
        if cleaned[i] == "{":
            depth += 1
        elif cleaned[i] == "}":
            depth -= 1
            if depth == 0:
                last_valid_end = i
                break

    if last_valid_end > start:
        try:
            result = json.loads(cleaned[start:last_valid_end + 1])
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass

    return None


def _validate_schema(schema: dict) -> dict:
    """Fill missing fields with safe defaults and validate cross-references."""
    import copy

    # Ensure all top-level keys exist
    for key, default in EMPTY_SCHEMA.items():
        if key not in schema:
            schema[key] = copy.deepcopy(default)

    # Ensure brand has all fields
    brand_defaults = EMPTY_SCHEMA["brand"]
    for k, v in brand_defaults.items():
        if k not in schema.get("brand", {}):
            schema.setdefault("brand", {})[k] = v

    # Ensure theme has all fields
    theme_defaults = EMPTY_SCHEMA["theme"]
    for k, v in theme_defaults.items():
        if k not in schema.get("theme", {}):
            schema.setdefault("theme", {})[k] = v

    # Validate entities: ensure each has slug and mock data
    for entity in schema.get("entities", []):
        if not entity.get("slug"):
            entity["slug"] = entity.get("name", "item").lower().replace(" ", "_") + "s"
        if not entity.get("fields"):
            entity["fields"] = []
        if not entity.get("mockData"):
            entity["mockData"] = []

        # Ensure id field exists
        has_id = any(f.get("name") == "id" for f in entity["fields"])
        if not has_id:
            entity["fields"].insert(0, {
                "name": "id",
                "type": "string",
                "required": True,
                "inList": True,
                "inForm": False,
            })

    # CROSS-REFERENCE CHECK: Every entity must have list + form pages
    existing_entity_pages = {
        p.get("entity") for p in schema.get("pages", [])
        if p.get("type") in ("crud_list", "crud_form")
    }
    for entity in schema.get("entities", []):
        slug = entity.get("slug", "")
        name = entity.get("name", "Item")
        if slug and slug not in existing_entity_pages:
            # Add missing pages
            schema["pages"].extend([
                {
                    "path": f"/{slug}",
                    "title": f"{name}s",
                    "component": f"{name}ListPage",
                    "type": "crud_list",
                    "entity": slug,
                },
                {
                    "path": f"/{slug}/new",
                    "title": f"New {name}",
                    "component": f"{name}FormPage",
                    "type": "crud_form",
                    "entity": slug,
                },
                {
                    "path": f"/{slug}/:id",
                    "title": f"Edit {name}",
                    "component": f"{name}FormPage",
                    "type": "crud_form",
                    "entity": slug,
                },
            ])

    # CROSS-REFERENCE CHECK: Navigation items must have pages
    all_page_paths = {p.get("path", "") for p in schema.get("pages", [])}
    for group in schema.get("navigation", []):
        for item in group.get("items", []):
            item_path = item.get("path", "")
            if item_path and item_path not in all_page_paths:
                # Add a page for orphan nav items
                component = item.get("label", "Page").replace(" ", "")
                schema["pages"].append({
                    "path": item_path,
                    "title": item.get("label", "Page"),
                    "component": f"{component}Page",
                    "type": "custom",
                })

    # Build mock_db from entities if missing
    if not schema.get("mock_db"):
        schema["mock_db"] = {}
        for entity in schema.get("entities", []):
            slug = entity.get("slug", "")
            if slug and entity.get("mockData"):
                schema["mock_db"][slug] = entity["mockData"]

    # Build api_config endpoints from entities if missing
    if not schema.get("api_config", {}).get("endpoints"):
        schema.setdefault("api_config", {})
        schema["api_config"]["base_url_env"] = "VITE_API_URL"
        schema["api_config"]["base_url_default"] = "http://localhost:3001"
        schema["api_config"]["endpoints"] = [
            {"entity": e["slug"], "basePath": f"/{e['slug']}"}
            for e in schema.get("entities", [])
            if e.get("slug")
        ]

    # Validate design_system has all keys
    ds_defaults = EMPTY_SCHEMA["design_system"]
    for k, v in ds_defaults.items():
        if k not in schema.get("design_system", {}):
            schema.setdefault("design_system", {})[k] = v

    return schema


def _build_fallback_schema(description: str, app_type: str) -> dict:
    """Build a minimal schema when Claude parsing fails."""
    import copy

    schema = copy.deepcopy(EMPTY_SCHEMA)
    schema["brand"]["name"] = description[:50]
    schema["brand"]["description"] = description
    schema["brand"]["domain"] = app_type
    return schema


# ╔══════════════════════════════════════════════════════════════╗
# ║  SCHEMA → PROMPT HELPERS                                     ║
# ║  Convert schema sections into prompt-friendly text           ║
# ╚══════════════════════════════════════════════════════════════╝

def schema_to_entity_spec(schema: dict) -> str:
    """Convert schema entities into a spec string for Claude prompts."""
    entities = schema.get("entities", [])
    if not entities:
        return ""

    lines = ["## ENTITY SPECIFICATIONS (from schema — follow EXACTLY)\n"]
    for entity in entities:
        name = entity.get("name", "Item")
        slug = entity.get("slug", "items")
        fields = entity.get("fields", [])

        lines.append(f"### Entity: {name} (slug: {slug})")

        # List columns
        list_fields = [f for f in fields if f.get("inList")]
        if list_fields:
            lines.append("DataTable columns: " + " | ".join(
                f"{f['name']}({f['type']})" for f in list_fields
            ))

        # Form fields
        form_fields = [f for f in fields if f.get("inForm")]
        if form_fields:
            lines.append("Form fields:")
            for f in form_fields:
                opts = f""", options: {f['options']}""" if f.get("options") else ""
                req = " (required)" if f.get("required") else ""
                placeholder = f""", placeholder: "{f['placeholder']}" """ if f.get("placeholder") else ""
                lines.append(f"  - {f['name']}: {f['type']}{req}{opts}{placeholder}")

        # Mock data count
        mock_count = len(entity.get("mockData", []))
        lines.append(f"Mock data: {mock_count} rows provided in db.json")
        lines.append("")

    return "\n".join(lines)


def schema_to_navigation_spec(schema: dict) -> str:
    """Convert schema navigation into a spec string."""
    nav = schema.get("navigation", [])
    if not nav:
        return ""

    lines = ["## NAVIGATION STRUCTURE (from schema — follow EXACTLY)\n"]
    for group in nav:
        lines.append(f"[{group.get('group', 'Main')}]")
        for item in group.get("items", []):
            badge = f" | badge: \"{item['badge']}\"" if item.get("badge") else ""
            lines.append(f"  - {item.get('label', '')} | {item.get('icon', 'Circle')} | {item.get('path', '/')}{badge}")
        lines.append("")

    return "\n".join(lines)


def schema_to_dashboard_spec(schema: dict) -> str:
    """Convert schema dashboard into a spec string."""
    dashboard = schema.get("dashboard", {})
    if not dashboard or not dashboard.get("kpis"):
        return ""

    lines = ["## DASHBOARD SPECIFICATION (from schema — follow EXACTLY)\n"]

    # KPIs
    kpis = dashboard.get("kpis", [])
    if kpis:
        lines.append("KPI Cards (row 1):")
        for i, kpi in enumerate(kpis, 1):
            lines.append(
                f"  {i}. {kpi.get('label', '')} | value: {kpi.get('value', '')} | "
                f"change: {kpi.get('change', '')} | trend: {kpi.get('trend', '')} | "
                f"icon: {kpi.get('icon', '')} | color: {kpi.get('color', '')}"
            )

    # Charts
    charts = dashboard.get("charts", [])
    if charts:
        lines.append("\nCharts (row 2):")
        for i, chart in enumerate(charts, 1):
            lines.append(f"  Chart {i}: type={chart.get('type', 'area')} | title=\"{chart.get('title', '')}\"")
            if chart.get("series"):
                for s in chart["series"]:
                    values = s.get("values", [])
                    lines.append(f"    series: {s.get('name', '')} = {values[:6]}... ({len(values)} points)")

    # Recent table
    recent = dashboard.get("recent_table", {})
    if recent and recent.get("columns"):
        lines.append(f"\nRecent Activity Table: \"{recent.get('title', '')}\"")
        lines.append(f"  columns: {recent['columns']}")

    return "\n".join(lines)


def schema_to_theme_spec(schema: dict) -> str:
    """Convert schema theme into CSS variable spec."""
    theme = schema.get("theme", {})
    if not theme or not theme.get("primary"):
        return ""

    lines = ["## THEME (from schema — use these EXACT HSL values)\n"]
    for key in [
        "primary", "primary_foreground", "secondary", "secondary_foreground",
        "background", "foreground", "card", "card_foreground",
        "muted", "muted_foreground", "accent", "accent_foreground",
        "destructive", "destructive_foreground", "border", "ring",
    ]:
        val = theme.get(key, "")
        if val:
            css_key = key.replace("_", "-")
            lines.append(f"  --{css_key}: {val};")

    if theme.get("radius"):
        lines.append(f"  --radius: {theme['radius']};")

    # Chart colors
    chart_colors = theme.get("chart_colors", [])
    for i, color in enumerate(chart_colors, 1):
        lines.append(f"  --chart-{i}: {color};")

    # Fonts
    if theme.get("heading_font"):
        lines.append(f"\nFonts: heading={theme['heading_font']}, body={theme.get('body_font', theme['heading_font'])}")
        if theme.get("heading_font_url"):
            lines.append(f"  @import url('{theme['heading_font_url']}')")
        if theme.get("body_font_url"):
            lines.append(f"  @import url('{theme['body_font_url']}')")

    # Dark mode
    dark = theme.get("dark_mode", {})
    if dark:
        lines.append("\nDark mode overrides:")
        for k, v in dark.items():
            if v:
                lines.append(f"  --{k.replace('_', '-')}: {v};")

    return "\n".join(lines)


def schema_to_design_system_spec(schema: dict) -> str:
    """Convert schema design_system into a spec for the design-system.js file."""
    ds = schema.get("design_system", {})
    sb = schema.get("status_badges", {})

    lines = ["## DESIGN SYSTEM TOKENS (generate src/lib/design-system.js with these)\n"]

    if ds.get("card_classes"):
        lines.append(f"card: \"{ds['card_classes']}\"")
    if ds.get("section_spacing"):
        lines.append(f"sectionSpacing: \"{ds['section_spacing']}\"")
    if ds.get("max_width"):
        lines.append(f"maxWidth: \"{ds['max_width']}\"")

    if sb:
        lines.append("\nStatus badge variants:")
        for status, classes in sb.items():
            lines.append(f"  {status}: \"{classes}\"")

    if ds.get("page_animation"):
        lines.append(f"\nPage animation: {json.dumps(ds['page_animation'])}")
    if ds.get("card_hover"):
        lines.append(f"Card hover: {json.dumps(ds['card_hover'])}")
    if ds.get("stagger_animation"):
        lines.append(f"Stagger: {json.dumps(ds['stagger_animation'])}")

    return "\n".join(lines)


def schema_to_api_spec(schema: dict) -> str:
    """Convert schema api_config into a spec for service generation."""
    api = schema.get("api_config", {})
    if not api or not api.get("endpoints"):
        return ""

    env_var = api.get("base_url_env", "VITE_API_URL")
    default_url = api.get("base_url_default", "http://localhost:3001")

    lines = [f"## API CONFIGURATION\n"]
    lines.append(f"Base URL: import.meta.env.{env_var} || '{default_url}'")
    lines.append(f"Services must make REAL fetch() calls to the API URL.")
    lines.append(f"Mock data lives in db.json at the project root (served by json-server).\n")
    lines.append(f"Endpoints:")
    for ep in api["endpoints"]:
        lines.append(f"  - {ep['entity']}: {ep['basePath']} (GET, GET/:id, POST, PUT/:id, DELETE/:id)")

    lines.append(f"\nService pattern:")
    lines.append(f"  const API_URL = import.meta.env.{env_var} || '{default_url}';")
    lines.append(f"  getAll: (params) => fetch(`${{API_URL}}/orders?${{new URLSearchParams(params)}}`).then(r => r.json())")
    lines.append(f"  getById: (id) => fetch(`${{API_URL}}/orders/${{id}}`).then(r => r.json())")
    lines.append(f"  create: (data) => fetch(`${{API_URL}}/orders`, {{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(data)}}).then(r => r.json())")
    lines.append(f"  update: (id,data) => fetch(`${{API_URL}}/orders/${{id}}`, {{method:'PUT',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(data)}}).then(r => r.json())")
    lines.append(f"  delete: (id) => fetch(`${{API_URL}}/orders/${{id}}`, {{method:'DELETE'}})")

    return "\n".join(lines)


def schema_to_mock_db_json(schema: dict) -> str:
    """Generate the complete db.json content from the schema."""
    mock_db = schema.get("mock_db", {})
    if not mock_db:
        return ""

    try:
        return json.dumps(mock_db, indent=2, ensure_ascii=False)
    except (TypeError, ValueError):
        return ""


def schema_to_sections_spec(schema: dict) -> str:
    """Convert schema sections into a spec for landing page generation."""
    sections = schema.get("sections", [])
    if not sections:
        return ""

    lines = ["## LANDING PAGE SECTIONS (from schema — create ALL of these)\n"]
    for i, section in enumerate(sections, 1):
        s_type = section.get("type", "custom")
        headline = section.get("headline", "")
        lines.append(f"{i}. [{s_type}] \"{headline}\"")
        if section.get("subheadline"):
            lines.append(f"   subheadline: \"{section['subheadline']}\"")
        if section.get("content"):
            # Truncate long content
            content = str(section["content"])
            if len(content) > 200:
                content = content[:200] + "..."
            lines.append(f"   content: {content}")
        if section.get("animation"):
            lines.append(f"   animation: {section['animation']}")
        lines.append("")

    return "\n".join(lines)
