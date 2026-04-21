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
        "name": "",        # Evocative 2-4 word name, e.g. "Midnight Stack"
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

_SCHEMA_SYSTEM = """You are a precise data architect. Your ONLY job is to parse
unstructured research text into a strictly-typed JSON schema.

RULES:
1. Extract EVERY entity, field, page, and UI element mentioned in the research.
2. For entities: list ALL fields with proper types (string, number, boolean, enum, date, email, url, textarea, select, file).
3. For enum fields: list ALL possible values in an "options" array.
4. Mark fields as inList:true if they should appear in DataTable columns.
5. Mark fields as inForm:true if they should appear in create/edit forms.
6. Generate 15-20 rows of realistic mock data per entity.
7. Every navigation item MUST have a corresponding page in the pages array.
8. For admin/dashboard apps: every entity MUST have /entity, /entity/new, /entity/:id routes in pages.
   For consumer/public apps: pages should reflect the public-facing page structure.
9. Dashboard KPIs must reference real entity metrics (admin apps only).
10. Status badges: extract ALL statuses mentioned with semantic Tailwind classes.
11. design_system: extract the EXACT Tailwind classes for cards, badges, spacing.
    Also generate design_system.name — a short evocative 2-4 word name that
    captures the visual identity of THIS specific product (e.g. "Midnight Stack",
    "Harvest Table", "Neon Arena", "Silk & Steel", "Polar Grid").  It must be
    unique to the product — never generic like "Modern App" or "Clean Design".
12. api_config: generate REST API endpoints for each entity.
13. mock_db: create the complete db.json content with all entities and mock data.
14. SELF-CLASSIFY: if the research contains an ===APP_CLASSIFICATION=== section,
    use it to determine whether to use sidebar nav vs header nav, whether to include
    a dashboard section, and what page types to use. Always follow the research's guidance.
15. For unusual/edge-case app types not explicitly listed in the instructions below:
    read the research carefully, understand the app's nature, and build the schema
    that best represents what the research describes — do NOT fall back to a generic structure.

Output ONLY valid JSON. No markdown, no explanation."""

_SCHEMA_USER_TEMPLATE = """Parse this research into a project schema.

PROJECT: {description}
APP TYPE: {app_type}
STACK: {stack}

CRITICAL: The authoritative layout_archetype is "{app_type}" — this was determined before the research.
If the research ===CLASSIFICATION=== block shows a DIFFERENT layout_archetype, IGNORE it.
You MUST follow the rules for "{app_type}" exactly, regardless of what the research says.

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
    "name": "Midnight Stack",
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

The APP TYPE field in the user message specifies the authoritative layout_archetype.
Use that — NOT the ===CLASSIFICATION=== block in the research (the research may have been corrected by the researcher and may differ). Apply these rules based on the APP TYPE:

For layout_archetype = single_page_landing (is_single_page: yes):
- "entities" → empty []
- "sections" → list ALL page sections (hero, features, testimonials, pricing, FAQ, CTA, footer, etc.)
  All content is sections within the single page — NO separate routes exist.
- "dashboard" → empty {{}}
- "mock_db" → empty {{}}
- "navigation" → TOP HEADER with anchor links pointing to sections (e.g. /#features, /#pricing)
  Format: [{{"group": "main", "items": [{{"label": "...", "path": "/#section-id", "icon": "..."}}]}}]
- "pages" → EMPTY [] — never add /about, /pricing, /contact as separate routes

For layout_archetype = consumer_website OR portfolio OR marketplace:
- "entities" → domain data models with ALL fields and 10-15 realistic mock rows each
- "sections" → empty [] (pages are full components, not section lists)
- "pages" → ALL public-facing pages with domain-specific paths (minimum 5-6 pages)
  Extract from the ===PAGES=== section in the research.
  Each page: path, title, component, type: "content_page", purpose
- "dashboard" → empty {{}} (NOT an admin dashboard)
- "navigation" → TOP HEADER nav items (NOT sidebar groups)
  Format: [{{"group": "main", "items": [{{"label": "...", "path": "/path", "icon": "..."}}]}}]
- "mock_db" → all entity data with realistic domain content
- Images: https://picsum.photos/seed/[domain][N]/800/600 | Avatars: https://i.pravatar.cc/150?u=[unique]

For layout_archetype = admin_dashboard OR crm OR tms OR saas_dashboard OR ecommerce:
- "entities" → ALL data entities with fields and 15-20 mock rows (extract from ===ENTITIES=== in research)
- "pages" → all CRUD routes (/entity, /entity/new, /entity/:id) + /dashboard
- "sections" → empty []
- "navigation" → SIDEBAR groups from ===SIDEBAR=== in research
  Format: [{{"group": "Group Name", "items": [{{"label": "...", "path": "...", "icon": "..."}}]}}]
- "dashboard" → KPIs, charts, recent_table from ===DASHBOARD_KPIS=== in research
- "mock_db" → all entity data

For layout_archetype = blog:
- "entities" → Article, Author, Category, Tag — ALL fields, 15-20 mock rows each
- "sections" → empty []
- "pages" → /articles, /articles/:slug, /write, /categories, /tags/:tag, /authors/:username, /search
- "dashboard" → empty {{}}
- "navigation" → top header nav items
- "mock_db" → all entity data with realistic blog content

CRITICAL RULE: Use the APP TYPE from the user message to determine the structure — not ===CLASSIFICATION=== in the research.
single_page_landing → NEVER create separate page routes — sections only, pages must be empty [].
admin types → use sidebar nav groups, not top header.

IMPORTANT: Generate 15-20 rows of REALISTIC mock data per entity.
Use real-sounding names, realistic numbers, proper date formats, varied statuses.
Every mock row must have ALL fields defined."""


# ╔══════════════════════════════════════════════════════════════╗
# ║  PYTHON-ONLY RESEARCH PARSER                                 ║
# ║  Parses Gemini ===BLOCKS=== with regex — zero LLM cost       ║
# ║  Used to:                                                    ║
# ║    • Fill theme/fonts/brand/nav for ALL project types        ║
# ║    • Build FULL schema for single_page_landing (skip Claude) ║
# ║    • Reduce admin Claude call to entity-only sections        ║
# ╚══════════════════════════════════════════════════════════════╝

import re as _re_sp
import copy as _copy_sp


def _extract_block(research: str, header: str, max_chars: int = 3000) -> str:
    """Extract the text content of a ===HEADER=== block."""
    if header not in research:
        return ""
    start = research.index(header) + len(header)
    rest = research[start:]
    end_pos = rest.find("===")
    end = start + end_pos if end_pos != -1 else start + max_chars
    return research[start:end].strip()


def _parse_css_variables(block: str) -> dict:
    """Parse ===CSS_VARIABLES=== text → theme dict with bare HSL values."""
    theme: dict = {}
    if not block:
        return theme

    # Strip hsl() wrappers if present
    block = block.replace("hsl(", "").replace(")", "")

    _key_map = {
        "--primary": "primary",
        "--primary-foreground": "primary_foreground",
        "--secondary": "secondary",
        "--secondary-foreground": "secondary_foreground",
        "--accent": "accent",
        "--accent-foreground": "accent_foreground",
        "--background": "background",
        "--foreground": "foreground",
        "--card": "card",
        "--card-foreground": "card_foreground",
        "--muted": "muted",
        "--muted-foreground": "muted_foreground",
        "--border": "border",
        "--ring": "ring",
        "--destructive": "destructive",
        "--destructive-foreground": "destructive_foreground",
        "--radius": "radius",
        "--sidebar-background": "sidebar_bg",
        "--sidebar-foreground": "sidebar_fg",
    }

    for part in _re_sp.split(r"[|\n]", block):
        part = part.strip()
        m = _re_sp.match(r"--([\w-]+)\s*:\s*(.+)", part)
        if not m:
            continue
        css_key = f"--{m.group(1).strip()}"
        raw_val = m.group(2).strip().rstrip(",").strip()
        # Drop anything after a semicolon or pipe that leaked through
        raw_val = _re_sp.split(r"[;|]", raw_val)[0].strip()
        schema_key = _key_map.get(css_key)
        if schema_key and raw_val:
            theme[schema_key] = raw_val

    return theme


def _parse_fonts(block: str) -> dict:
    """Parse ===FONTS=== text → partial theme dict + overall_vibe."""
    result: dict = {}
    if not block:
        return result
    for line in block.splitlines():
        line = line.strip()
        if line.startswith("heading:"):
            m = _re_sp.match(r"heading:\s*([^(]+)(?:\(([^)]+)\))?", line)
            if m:
                result["heading_font"] = m.group(1).strip()
                url_raw = (m.group(2) or "").strip()
                if "fonts.googleapis.com" in url_raw:
                    result["heading_font_url"] = url_raw
        elif line.startswith("body:"):
            m = _re_sp.match(r"body:\s*([^(]+)(?:\(([^)]+)\))?", line)
            if m:
                result["body_font"] = m.group(1).strip()
                url_raw = (m.group(2) or "").strip()
                if "fonts.googleapis.com" in url_raw:
                    result["body_font_url"] = url_raw
        elif line.startswith("overall_vibe:"):
            result["overall_vibe"] = line.split(":", 1)[1].strip()
    return result


def _parse_header_nav(block: str) -> tuple:
    """Parse ===HEADER=== text → (brand_name, list[nav_item_dict])."""
    brand = ""
    nav_items: list = []
    if not block:
        return brand, nav_items
    for line in block.splitlines():
        line = line.strip()
        if line.startswith("logo:"):
            brand = line.split(":", 1)[1].strip()
        elif line.startswith("nav_items:"):
            items_str = line.split(":", 1)[1].strip()
            for raw in items_str.split(","):
                label = raw.strip().strip('"').strip("'")
                # Skip URL fragments or Tailwind utility tokens
                if not label or "http" in label or "bg-" in label or "px-" in label:
                    continue
                nav_items.append({
                    "label": label,
                    "path": f"#{label.lower().replace(' ', '-')}",
                    "icon": "Circle",
                })
    return brand, nav_items


def _parse_sidebar_nav(block: str) -> list:
    """Parse ===SIDEBAR=== text → list of nav-group dicts."""
    groups: list = []
    current: dict = {}
    if not block:
        return groups
    for line in block.splitlines():
        line_s = line.strip()
        m = _re_sp.match(r"\[group:\s*(.+?)\]", line_s)
        if m:
            if current and current.get("items"):
                groups.append(current)
            current = {"group": m.group(1).strip(), "items": []}
            continue
        if not current:
            continue
        m2 = _re_sp.match(r"-\s*label:\s*([^|]+)", line_s)
        if m2:
            label = m2.group(1).strip()
            path_m = _re_sp.search(r"path:\s*(/\S*)", line_s)
            icon_m = _re_sp.search(r"icon:\s*(\w+)", line_s)
            current["items"].append({
                "label": label,
                "path": path_m.group(1) if path_m else f"/{label.lower().replace(' ', '-')}",
                "icon": icon_m.group(1) if icon_m else "Circle",
            })
    if current and current.get("items"):
        groups.append(current)
    return groups


def _parse_sections(block: str) -> list:
    """Parse ===SECTIONS=== text → list of section dicts for landing pages."""
    sections: list = []
    current: dict = {}
    if not block:
        return sections
    for line in block.splitlines():
        ls = line.strip()
        m = _re_sp.match(r"\[section:\s*(\w[\w_]*)\]", ls)
        if m:
            if current:
                sections.append(current)
            current = {
                "type": m.group(1),
                "headline": "",
                "subheadline": "",
                "content": {},
                "animation": "",
            }
            continue
        if not current:
            continue
        if ls.startswith("headline:"):
            current["headline"] = ls.split(":", 1)[1].strip().strip('"').strip("'")
        elif ls.startswith("subheadline:"):
            current["subheadline"] = ls.split(":", 1)[1].strip().strip('"').strip("'")
        elif ls.startswith("layout:"):
            current["content"]["layout"] = ls.split(":", 1)[1].strip()
        elif ls.startswith("background:"):
            current["content"]["background"] = ls.split(":", 1)[1].strip()
        elif ls.startswith("cta_primary:"):
            val = ls.split(":", 1)[1].strip()
            current["content"]["cta_primary"] = val.split("|")[0].strip().strip('"')
        elif ls.startswith("animation:"):
            current["animation"] = ls.split(":", 1)[1].strip()
        elif ls.startswith("hero_image:"):
            current["content"]["hero_image"] = ls.split(":", 1)[1].strip()
        elif ls.startswith("items:"):
            current["content"]["items"] = ls.split(":", 1)[1].strip()
    if current:
        sections.append(current)
    return sections


def _parse_pages_block(block: str) -> list:
    """Parse ===PAGES=== text → list of page dicts for consumer sites.

    Captures path, title, description, AND sections list so Phase 2 gets
    explicit section specs per page instead of having to fish them out of
    the raw research blob.
    """
    pages: list = []
    current: dict = {}
    if not block:
        return pages
    for line in block.splitlines():
        ls = line.strip()
        m = _re_sp.match(r"\[page:\s*(.+?)\]", ls)
        if m:
            if current:
                pages.append(current)
            title = m.group(1).strip().title()
            current = {
                "title": title,
                "path": "/",
                "component": title.replace(" ", "") + "Page",
                "type": "custom",
                "description": "",
                "sections": [],   # new: section list for this page
            }
            continue
        if not current:
            continue
        if ls.startswith("path:"):
            current["path"] = ls.split(":", 1)[1].strip()
        elif ls.startswith("hero_headline:"):
            current["description"] = ls.split(":", 1)[1].strip().strip('"').strip("'")
        elif ls.startswith("purpose:") and not current.get("description"):
            current["description"] = ls.split(":", 1)[1].strip()
        elif ls.startswith("sections:"):
            # "sections: hero, services, team, contact-form"
            raw_sections = ls.split(":", 1)[1].strip()
            current["sections"] = [s.strip() for s in raw_sections.split(",") if s.strip()]
    if current:
        pages.append(current)
    return pages


def _parse_kpis(block: str) -> list:
    """Parse ===DASHBOARD_KPIS=== text → list of KPI dicts."""
    kpis: list = []
    if not block:
        return kpis
    for line in block.splitlines():
        ls = line.strip()
        if not ls or not ls[0].isdigit():
            continue
        parts = _re_sp.split(r"\s*\|\s*", ls)
        kpi: dict = {}
        for part in parts:
            part = _re_sp.sub(r"^\d+\.\s*", "", part).strip()
            if ":" in part:
                k, v = part.split(":", 1)
                kpi[k.strip().lower()] = v.strip()
        if kpi.get("label"):
            kpis.append({
                "label": kpi.get("label", ""),
                "value": kpi.get("value", "0"),
                "change": kpi.get("change", "+0%"),
                "trend": kpi.get("trend", "up"),
                "icon": kpi.get("icon", "TrendingUp"),
                "color": kpi.get("color", "text-primary"),
            })
    return kpis


def _parse_status_badges(block: str) -> dict:
    """Parse ===STATUS_BADGES=== text → {status: tailwind_classes}."""
    badges: dict = {}
    if not block:
        return badges
    for line in block.splitlines():
        ls = line.strip()
        if ":" in ls and ("bg-" in ls or "text-" in ls):
            k, v = ls.split(":", 1)
            status = k.strip().lower()
            classes = v.strip()
            if status and classes:
                badges[status] = classes
    return badges


def _derive_design_direction(research: str) -> str:
    """Derive design direction from Gemini research instead of random selection.

    Reads overall_vibe from ===FONTS=== and primary hue from ===CSS_VARIABLES===
    to pick the most fitting design direction for the schema builder.
    This replaces the previous random.choice() approach which could contradict
    Gemini's researched color palette.
    """
    fonts_block = _extract_block(research, "===FONTS===", max_chars=400)
    vibe = ""
    for line in fonts_block.splitlines():
        if line.strip().startswith("overall_vibe:"):
            vibe = line.split(":", 1)[1].strip().lower()
            break

    vibe_lower = vibe.lower()
    _vibe_map = [
        (["dark", "moody", "noir", "deep", "cinema", "cinematic"],
         "dark and moody with deep backgrounds and light text — tech/creative aesthetic"),
        (["minimal", "clean", "airy", "white", "simple"],
         "light and airy with generous whitespace and subtle shadows — minimal SaaS"),
        (["bold", "contrast", "vibrant", "vivid", "startup", "energetic"],
         "bold and high-contrast with a vivid accent color — conversion-focused startup"),
        (["warm", "earth", "amber", "terracotta", "human", "friendly"],
         "warm earth tones (amber, sand, terracotta) — human and approachable"),
        (["glass", "gradient", "glassmorphism"],
         "vibrant gradient hero with glassmorphism cards — modern web app"),
        (["corporate", "professional", "b2b", "navy", "enterprise"],
         "clean corporate palette (navy, slate, white) — professional B2B"),
        (["playful", "colorful", "fun", "consumer", "casual"],
         "playful and colorful (coral, teal, yellow) — consumer product"),
        (["neon", "developer", "technical", "dev", "code"],
         "sophisticated dark mode with neon accent — developer / technical tool"),
        (["elegant", "luxury", "premium", "serif", "refined", "upscale"],
         "elegant near-monochrome with serif headings — premium / luxury brand"),
        (["green", "eco", "sustain", "health", "natural", "organic"],
         "green-forward eco palette — sustainability / health product"),
        (["ai", "data", "analytics", "purple", "indigo", "intelligence"],
         "purple-to-indigo gradient — AI / data / analytics product"),
        (["energy", "fitness", "sport", "orange", "gaming", "action"],
         "orange and black high-energy — fitness / sports / gaming"),
    ]
    for keywords, direction in _vibe_map:
        if any(k in vibe_lower for k in keywords):
            return direction

    # Fall back to hue-based detection from primary CSS variable
    css_block = _extract_block(research, "===CSS_VARIABLES===", max_chars=600)
    primary_hsl = ""
    for part in _re_sp.split(r"[|\n]", css_block):
        m = _re_sp.match(r"\s*--primary\s*:\s*(.+)", part.strip())
        if m:
            primary_hsl = m.group(1).strip()
            break

    hue_m = _re_sp.search(r"(\d+(?:\.\d+)?)", primary_hsl)
    if hue_m:
        hue = float(hue_m.group(1))
        if hue < 30 or hue >= 330:
            return "bold and high-contrast with a vivid accent color — conversion-focused startup"
        if hue < 60:
            return "warm earth tones (amber, sand, terracotta) — human and approachable"
        if hue < 150:
            return "green-forward eco palette — sustainability / health product"
        if hue < 210:
            return "light and airy with generous whitespace and subtle shadows — minimal SaaS"
        if hue < 270:
            return "purple-to-indigo gradient — AI / data / analytics product"
        return "sophisticated dark mode with neon accent — developer / technical tool"

    # If still nothing, return the raw vibe string — still better than random
    return f"design consistent with the researched aesthetic: {vibe or 'clean and professional'}"


def _parse_schema_from_research(
    research: str,
    description: str,
    classification: dict,
    stack: str,
) -> dict:
    """Parse Gemini research ===BLOCKS=== into a schema dict — zero LLM cost.

    Covers: theme (CSS vars + fonts), brand, navigation, sections (landing),
    KPIs (admin), status badges, pages (consumer), design_system name.

    Does NOT cover (admin-only, still needs LLM):
      entity fields with proper types, 15-20 mock data rows, design_system
      Tailwind token strings (card_classes, section_spacing, etc.).
    """
    schema = _copy_sp.deepcopy(EMPTY_SCHEMA)
    layout = classification.get("layout_archetype", "single_page_landing")
    domain = classification.get("domain", "general")

    # ── Theme: CSS variables ──────────────────────────────────
    css_block = _extract_block(research, "===CSS_VARIABLES===", max_chars=1200)
    if css_block:
        schema["theme"].update(_parse_css_variables(css_block))

    # ── Theme: Fonts ──────────────────────────────────────────
    fonts_block = _extract_block(research, "===FONTS===", max_chars=600)
    parsed_fonts = _parse_fonts(fonts_block)
    for fk in ("heading_font", "heading_font_url", "body_font", "body_font_url"):
        if parsed_fonts.get(fk):
            schema["theme"][fk] = parsed_fonts[fk]
    if parsed_fonts.get("overall_vibe"):
        schema["design_system"]["overall_vibe"] = parsed_fonts["overall_vibe"]

    # ── Brand ─────────────────────────────────────────────────
    header_block = _extract_block(research, "===HEADER===", max_chars=600)
    brand_name, nav_items = _parse_header_nav(header_block)
    if not brand_name:
        clean = description.split("\n\n---\n\n")[0].strip()
        brand_name = clean[:50].split(".")[0].split(",")[0].strip() or "Project"
    schema["brand"]["name"] = brand_name
    schema["brand"]["domain"] = domain
    schema["brand"]["description"] = description.split("\n\n---\n\n")[0].strip()[:200]

    # ── Design system name ────────────────────────────────────
    ds_name = _extract_block(research, "===DESIGN_SYSTEM_NAME===", max_chars=100).strip().strip('"').strip("'")
    if ds_name and len(ds_name) < 60:
        schema["design_system"]["name"] = ds_name

    # ── Status badges ─────────────────────────────────────────
    badges_block = _extract_block(research, "===STATUS_BADGES===", max_chars=800)
    if badges_block:
        schema["status_badges"] = _parse_status_badges(badges_block)

    # ── Dashboard KPIs (admin archetypes) ────────────────────
    _is_admin = layout in {"admin_dashboard", "crm", "tms", "saas_dashboard", "ecommerce"}
    kpis_block = _extract_block(research, "===DASHBOARD_KPIS===", max_chars=1500)
    if kpis_block:
        kpis = _parse_kpis(kpis_block)
        if kpis:
            schema["dashboard"]["kpis"] = kpis

    # ── Navigation ────────────────────────────────────────────
    if _is_admin:
        sidebar_block = _extract_block(research, "===SIDEBAR===", max_chars=2500)
        groups = _parse_sidebar_nav(sidebar_block)
        if groups:
            schema["navigation"] = groups
    elif nav_items:
        schema["navigation"] = [{"group": "main", "items": nav_items}]

    # ── Sections (landing pages only) ─────────────────────────
    if layout == "single_page_landing":
        sections_block = _extract_block(research, "===SECTIONS===", max_chars=6000)
        parsed_secs = _parse_sections(sections_block)
        if parsed_secs:
            schema["sections"] = parsed_secs

    # ── Pages (consumer / blog / marketplace) ─────────────────
    elif layout in {"consumer_website", "marketplace", "portfolio", "blog"}:
        pages_block = _extract_block(research, "===PAGES===", max_chars=4000)
        parsed_pages = _parse_pages_block(pages_block)
        if parsed_pages:
            schema["pages"] = parsed_pages

    return schema


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

    Fast path (no LLM):
      - single_page_landing → Python-only parser (~0s, saves 20-40s)

    Focused path (Claude, reduced tokens):
      - admin archetypes → passes only ENTITIES/SIDEBAR/KPI blocks to Claude
        then overlays Python-parsed theme/fonts/brand on top

    Fallback path (Claude, full research):
      - consumer_website, blog, marketplace, portfolio

    Returns the canonical schema dict, or a fallback if parsing fails.
    """
    from app.services.project_generator import _ws_send

    await _ws_send(websocket, "progress", "📐 Building project schema...")

    # Classification dict for the Python parser helpers
    _classification = {
        "layout_archetype": app_type,
        "domain": description.split()[0].lower() if description else "general",
    }

    # ── Fast path: Python-only, zero LLM cost ────────────────────────────────
    # Covers: single_page_landing always, consumer/portfolio/marketplace when
    # Python parser yields enough structure (pages ≥ 2 + theme populated).
    _python_fast_types = {"single_page_landing", "consumer_website", "portfolio", "marketplace", "blog"}
    if app_type in _python_fast_types:
        py_schema = _parse_schema_from_research(research, description, _classification, stack)
        py_schema = _validate_schema(py_schema, layout_archetype=app_type)
        section_count = len(py_schema.get("sections", []))
        page_count = len(py_schema.get("pages", []))
        nav_items = sum(len(g.get("items", [])) for g in py_schema.get("navigation", []))
        has_theme = bool(py_schema.get("theme", {}).get("primary"))

        # Consumer/portfolio/marketplace require at least 2 pages to skip Claude.
        # Landing pages always use Python path — never fall through to Claude.
        # If sections are empty (Gemini formatted differently), build defaults from nav.
        if app_type == "single_page_landing" and section_count < 2:
            _default_sections = [
                {"type": "hero", "headline": "", "subheadline": "", "content": {}, "animation": "fade-up"},
                {"type": "features", "headline": "", "subheadline": "", "content": {}, "animation": "fade-up"},
                {"type": "how_it_works", "headline": "", "subheadline": "", "content": {}, "animation": "fade-up"},
                {"type": "testimonials", "headline": "", "subheadline": "", "content": {}, "animation": "fade-up"},
                {"type": "pricing", "headline": "", "subheadline": "", "content": {}, "animation": "fade-up"},
                {"type": "faq", "headline": "", "subheadline": "", "content": {}, "animation": "fade-up"},
                {"type": "cta_final", "headline": "", "subheadline": "", "content": {}, "animation": "fade-up"},
            ]
            py_schema["sections"] = _default_sections
            section_count = len(_default_sections)
        _sufficient = section_count >= 2 or (page_count >= 2 and has_theme)
        if _sufficient:
            label = f"{section_count} sections" if section_count else f"{page_count} pages"
            logger.info("Schema built (fast-parse): %s nav=%d", label, nav_items)
            await _ws_send(
                websocket, "progress",
                f"✅ Schema built: {label}, {nav_items} nav items (instant)",
            )
            return py_schema
        # Insufficient — fall through to Claude for this type

    # ── Design direction: derived from Gemini research (not random) ───────────
    _design_direction = _derive_design_direction(research)

    # ── Admin archetypes: pass only entity-relevant blocks to Claude ──────────
    _admin_archetypes = {"admin_dashboard", "crm", "tms", "saas_dashboard", "ecommerce"}
    _is_admin = app_type in _admin_archetypes
    if _is_admin:
        _focused_headers = [
            "===APP_CLASSIFICATION===",
            "===ENTITIES===",
            "===SIDEBAR===",
            "===DASHBOARD_KPIS===",
            "===STATUS_BADGES===",
        ]
        focused_parts: list[str] = []
        for hdr in _focused_headers:
            blk = _extract_block(research, hdr, max_chars=5000)
            if blk:
                focused_parts.append(f"{hdr}\n{blk}\n")
        research_for_claude = "\n".join(focused_parts) if focused_parts else research[:12000]
    else:
        research_for_claude = research[:24000]  # consumer / blog / marketplace

    user_prompt = _SCHEMA_USER_TEMPLATE.format(
        description=description,
        app_type=app_type,
        stack=stack,
        research=research_for_claude,
    )
    # Append design direction after the template — influences colors/fonts
    user_prompt += (
        f"\n\nDESIGN DIRECTION FOR THIS RUN: {_design_direction}\n"
        "Apply this design direction to theme colors, fonts, and overall_vibe. "
        "Make the palette feel PURPOSE-BUILT for this specific product — "
        "do NOT default to generic blue-tech colors unless the direction calls for it."
    )

    # Stream the schema response so the user sees heartbeat progress
    # instead of a silent 30-60s freeze.
    import httpx
    import time as _time

    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }

    payload = {
        "model": "claude-sonnet-4-6",  # Fast + cheap for parsing
        "max_tokens": 8000,            # Schema JSON never needs 16K
        "stream": True,
        "system": _SCHEMA_SYSTEM,
        "messages": [{"role": "user", "content": user_prompt}],
    }

    try:
        text_parts: list[str] = []
        _last_heartbeat = _time.monotonic()

        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=300.0)) as client:
            async with client.stream(
                "POST",
                "https://api.anthropic.com/v1/messages",
                headers=headers,
                json=payload,
            ) as response:
                if response.status_code != 200:
                    error_body = await response.aread()
                    logger.error("Schema parse API error %d: %s", response.status_code, error_body[:300])
                    await _ws_send(websocket, "progress", "⚠️ Schema parse failed — using research text directly")
                    return _build_fallback_schema(description, app_type)

                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    raw = line[6:]
                    if raw == "[DONE]":
                        break
                    try:
                        chunk = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    if chunk.get("type") == "content_block_delta":
                        delta = chunk.get("delta", {})
                        if delta.get("type") == "text_delta":
                            text_parts.append(delta.get("text", ""))

                    # Heartbeat every 12s so the user sees something
                    _now = _time.monotonic()
                    if _now - _last_heartbeat > 12:
                        _last_heartbeat = _now
                        await _ws_send(websocket, "progress", "📐 Building project structure...")

        text = "".join(text_parts)

        if not text:
            logger.error("Schema parse returned empty text")
            return _build_fallback_schema(description, app_type)

        # Parse the JSON response
        schema = _extract_json(text)
        if not schema:
            logger.error("Failed to parse schema JSON from %d chars", len(text))
            return _build_fallback_schema(description, app_type)

        # Validate and fill missing fields
        schema = _validate_schema(schema, layout_archetype=app_type)

        # ── Overlay Python-parsed theme/fonts for admin projects ──────────────
        # Claude only saw entity blocks, so theme may be generic; override with
        # Gemini's researched CSS variables and fonts.
        if _is_admin:
            py_schema = _parse_schema_from_research(research, description, _classification, stack)
            _theme_overrides = (
                "heading_font", "heading_font_url", "body_font", "body_font_url",
                "primary", "primary_foreground", "background", "foreground",
                "card", "card_foreground", "muted", "muted_foreground",
                "accent", "accent_foreground", "border", "ring",
                "sidebar_bg", "sidebar_fg",
            )
            for field in _theme_overrides:
                py_val = py_schema["theme"].get(field, "")
                empty_val = EMPTY_SCHEMA["theme"].get(field, "")
                if py_val and py_val != empty_val:
                    schema["theme"][field] = py_val
            # Brand name from research header (more accurate than LLM guess)
            if py_schema["brand"].get("name") and not schema["brand"].get("name"):
                schema["brand"]["name"] = py_schema["brand"]["name"]
            # Design system name from research if Claude left it blank
            if py_schema["design_system"].get("name") and not schema["design_system"].get("name"):
                schema["design_system"]["name"] = py_schema["design_system"]["name"]

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


def _validate_schema(schema: dict, layout_archetype: str = "") -> dict:
    """Fill missing fields with safe defaults and validate cross-references.

    `layout_archetype` is the AUTHORITATIVE archetype from the classifier. When
    set to `single_page_landing` we hard-lock pages=[] and skip nav→page
    expansion — prevents the coffee-shop-landing bug where nav items like
    "Menu"/"About"/"Contact" auto-generated /menu, /about, /contact routes
    even though the project is a single-page scroll site.
    """
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

    # Determine if this is a landing page. AUTHORITATIVE source: layout_archetype
    # from the classifier. Fall back to heuristic (sections present, no entities)
    # only when the caller didn't supply the archetype.
    if layout_archetype == "single_page_landing":
        _is_landing_schema = True
        # Pre-empt nav→page expansion below by clearing any pages Claude
        # or the research parser accidentally emitted.
        schema["pages"] = []
    else:
        _is_landing_schema = bool(schema.get("sections")) and not schema.get("entities")

    if not _is_landing_schema:
        # CROSS-REFERENCE CHECK: Every entity must have list + form pages (admin only)
        existing_entity_pages = {
            p.get("entity") for p in schema.get("pages", [])
            if p.get("type") in ("crud_list", "crud_form")
        }
        for entity in schema.get("entities", []):
            slug = entity.get("slug", "")
            name = entity.get("name", "Item")
            if slug and slug not in existing_entity_pages:
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

        # CROSS-REFERENCE CHECK: Navigation items must have pages.
        # Skip anchor links (/#section-id) — those are intra-page anchors, not routes.
        all_page_paths = {p.get("path", "") for p in schema.get("pages", [])}
        for group in schema.get("navigation", []):
            for item in group.get("items", []):
                item_path = item.get("path", "")
                if not item_path:
                    continue
                if item_path.startswith("#") or item_path.startswith("/#"):
                    continue  # anchor link — not a route
                if item_path not in all_page_paths:
                    component = item.get("label", "Page").replace(" ", "")
                    schema["pages"].append({
                        "path": item_path,
                        "title": item.get("label", "Page"),
                        "component": f"{component}Page",
                        "type": "custom",
                    })

    # For landing pages: ensure pages is empty (no spurious route stubs)
    if _is_landing_schema:
        schema["pages"] = []

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
            content = str(section["content"])
            if len(content) > 200:
                content = content[:200] + "..."
            lines.append(f"   content: {content}")
        if section.get("animation"):
            lines.append(f"   animation: {section['animation']}")
        lines.append("")

    return "\n".join(lines)


def schema_to_pages_spec(schema: dict) -> str:
    """Convert schema pages into an explicit per-page section spec for consumer websites.

    Unlike schema_to_sections_spec (which is for single-page landings), this produces
    a structured list of ALL pages with their required sections so Phase 2 doesn't have
    to extract them from the raw 16K research blob.
    """
    pages = schema.get("pages", [])
    if not pages:
        return ""

    lines = ["## PAGES TO BUILD (create ALL — every page, every section listed)\n"]
    for i, page in enumerate(pages, 1):
        path = page.get("path", "/")
        title = page.get("title", "Page").replace("Page", "").strip()
        desc = page.get("description", "")
        sections = page.get("sections", [])
        component = page.get("component", f"{title.replace(' ', '')}Page")

        lines.append(f"{i}. **{title}** → `{path}` (component: `{component}`)")
        if desc:
            lines.append(f"   Purpose: {desc}")
        if sections:
            lines.append(f"   Sections: {', '.join(sections)}")
        else:
            # Fallback hints per common page names
            _fallback_sections = {
                "home":      "hero, stats/trust-bar, services/features, testimonials, cta",
                "about":     "hero, story/mission, team-grid, values, timeline",
                "services":  "hero, service-cards-grid, process/how-it-works, faq",
                "contact":   "hero, contact-form, map/address, social-links",
                "pricing":   "hero, pricing-cards, feature-comparison, faq, cta",
                "work":      "hero, project-grid, case-study-cards, cta",
                "menu":      "hero, category-tabs, menu-item-grid, dietary-filters",
                "team":      "hero, team-member-grid, culture/values-section",
                "listings":  "hero, filter-bar, listing-cards-grid, pagination",
                "gallery":   "hero, masonry-image-grid, lightbox, category-filter",
            }
            page_key = title.lower().replace(" ", "")
            hint = next((v for k, v in _fallback_sections.items() if k in page_key), None)
            if hint:
                lines.append(f"   Sections (fallback): {hint}")
        lines.append("")

    return "\n".join(lines)


def schema_to_extra_pages_spec(schema: dict) -> str:
    """List all nav items that don't have a corresponding entity CRUD page.

    Admin/CRM panels often have sidebar items like 'Reports', 'Analytics',
    'Calendar', 'Help' that aren't entity-based. These are 'extra pages' that
    Phase 3 must build — this helper makes them explicit instead of relying on
    Claude to notice missing pages.
    """
    # Collect paths already covered by entity CRUD
    entity_paths: set = set()
    for entity in schema.get("entities", []):
        slug = entity.get("slug", "")
        if slug:
            entity_paths.update({f"/{slug}", f"/{slug}/new", f"/{slug}/:id"})
    entity_paths.add("/dashboard")
    entity_paths.add("/settings")

    extra: list = []
    for group in schema.get("navigation", []):
        for item in group.get("items", []):
            path = item.get("path", "")
            label = item.get("label", "")
            icon = item.get("icon", "Circle")
            # Skip entity CRUD paths, settings, dashboard, and anchor links
            if not path or path.startswith("#") or path in entity_paths:
                continue
            # Also skip if any entity slug appears in the path
            if any(f"/{slug}" in path for slug in
                   (e.get("slug", "") for e in schema.get("entities", []))):
                continue
            extra.append({"path": path, "label": label, "icon": icon})

    if not extra:
        return ""

    lines = ["## EXTRA PAGES (sidebar nav items without CRUD — build these as full pages)\n"]
    for p in extra:
        lines.append(f"- **{p['label']}** → `{p['path']}` (icon: {p['icon']})")
        # Add a hint for common page types
        label_lower = p["label"].lower()
        if any(k in label_lower for k in ["report", "analytic", "stat", "insight"]):
            lines.append("  Build: charts/graphs page with recharts — revenue trends, top metrics, date range filter")
        elif any(k in label_lower for k in ["calendar", "schedule", "appointment"]):
            lines.append("  Build: calendar grid view (7-col week grid) with event cards + month navigation")
        elif any(k in label_lower for k in ["help", "support", "faq", "docs"]):
            lines.append("  Build: FAQ accordion + search bar + contact support card")
        elif any(k in label_lower for k in ["notif", "inbox", "message"]):
            lines.append("  Build: notification list with read/unread state, type icons, timestamps")
        elif any(k in label_lower for k in ["map", "route", "location", "track"]):
            lines.append("  Build: map placeholder (div with bg-muted, pin icons, route cards below)")
        elif any(k in label_lower for k in ["profile", "account", "user"]):
            lines.append("  Build: user profile card + edit form + password change section")
        else:
            lines.append("  Build: full page appropriate for this feature — not a stub or placeholder")

    return "\n".join(lines)
