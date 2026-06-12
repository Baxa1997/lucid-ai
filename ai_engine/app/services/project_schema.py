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
    # ── Project classification (top-level so every builder reads the same source) ──
    # archetype: "single_page_landing" | "consumer_website" | "admin_dashboard" | …
    # domain_kind: "saas" | "ecommerce" | "food" | "health" | "fintech" | "general" | …
    # These were previously pipeline locals (_layout_archetype / _domain) — folded
    # into the schema so any builder, persistence step, or quality gate reads them
    # from one place. Set by _parse_schema_from_research from the classifier output.
    "archetype": "",
    "domain_kind": "",
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
    # Design Director output — per-project visual identity (radius tokens, card
    # language, motion language, brand mark, header spec, …). Empty dict means
    # DD didn't run / timed out; builders fall back to theme tokens.
    "design": {},
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
    """Extract the text content of a ===HEADER=== block.

    Strict path first: literal `===HEADER===` match (what the prompt asks for).

    Markdown fallback: when Gemini drops the `===` wrappers and writes
    `## Sections` / `## Pages` instead, we hand back a wider slice — from
    the matched heading through the rest of the research, capped at
    max_chars*3. We can't cleanly bound the inner content (real section
    headings like `## Hero` sit at the same level as the wrapper itself),
    so we lean on the parsers (_parse_sections / _parse_pages_block) to
    filter wrapper names out via a denylist. Returning a wider slice is
    cheap; silently returning "" is what made the schema thin.
    """
    if header in research:
        start = research.index(header) + len(header)
        rest = research[start:]
        end_pos = rest.find("===")
        end = start + end_pos if end_pos != -1 else start + max_chars
        return research[start:end].strip()

    # Tolerant fallback — strip the === wrapper, match common markdown forms.
    bare = header.strip("=").strip()
    if not bare:
        return ""

    import re as _re_eb
    # `## Bare`, `### BARE:`, `**BARE**`, `# Bare` — case-insensitive.
    pattern = _re_eb.compile(
        rf"^[ \t]*(?:#{{1,4}}|\*\*)\s*{_re_eb.escape(bare)}\s*\*{{0,2}}\s*:?\s*$",
        _re_eb.IGNORECASE | _re_eb.MULTILINE,
    )
    m = pattern.search(research)
    if not m:
        return ""

    start = m.end()
    # Hand back a generous slice — the parsers know how to ignore wrappers.
    return research[start:start + max_chars * 3].strip()


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


# Section/nav-label keyword → Lucide icon name. Priority order matters:
# more specific keywords come before generic ones (so "loaves" hits Wheat
# before any softer match). All names are real lucide-react exports.
# Used by _parse_header_nav and the section→nav fallback so synthesized
# nav items get project-specific icons instead of every item being a
# generic Circle.
_SECTION_ICON_KEYWORDS: list[tuple[tuple[str, ...], str]] = [
    (("loaves", "bread", "bake", "bakery", "pastry"), "Wheat"),
    (("menu", "dish", "cuisine", "recipe", "kitchen"), "UtensilsCrossed"),
    (("product", "catalog", "shop", "store"), "ShoppingBag"),
    (("mill", "process", "craft", "how_it", "how-it", "make"), "Settings"),
    (("visit", "location", "find_us", "find-us", "where", "address"), "MapPin"),
    (("contact",), "Mail"),
    (("team", "founders", "staff", "people"), "Users"),
    (("gallery", "photos", "images", "portfolio"), "Image"),
    (("pricing", "plans", "tiers", "price"), "Tag"),
    (("testimonial", "reviews", "praise"), "Quote"),
    (("cta", "order", "signup", "subscribe", "join", "get_started", "get-started"), "ArrowRight"),
    (("features", "benefit", "why_us", "why-us"), "Sparkles"),
    (("faq", "questions", "help"), "HelpCircle"),
    (("services", "offering"), "Briefcase"),
    (("blog", "news", "article", "post"), "Newspaper"),
    (("event", "calendar", "schedule", "hours"), "Calendar"),
    (("story", "about", "intro", "history"), "BookOpen"),
    (("work", "case_study", "case-study", "projects"), "Layers"),
]


def _pick_section_icon(*texts: str) -> str:
    """Pick a Lucide icon name from any free-text section label / id /
    type. Lowercased substring search; first hit wins. Fallback: Circle.

    Used by both `_parse_header_nav` (when research provides nav items)
    and the section→nav fallback in `build_project_schema`, so icons are
    project-specific everywhere instead of every nav item rendering as
    a generic Circle.
    """
    haystack = " ".join(t.lower() for t in texts if t)
    if not haystack:
        return "Circle"
    for keywords, icon in _SECTION_ICON_KEYWORDS:
        for kw in keywords:
            if kw in haystack:
                return icon
    return "Circle"


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
                    "icon": _pick_section_icon(label),
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
    """Parse ===SECTIONS=== text → list of section dicts for landing pages.

    Strict syntax first: `[section: hero]` followed by `headline: …` lines.
    When Gemini drifts to markdown (`## Hero`, `### Pricing Section`), the
    second-pass scan turns each heading into a minimal section dict so the
    schema still gets populated. Strict + markdown are merged, deduped on type.
    """
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
        elif ls.startswith("imagery:"):
            current["content"]["imagery"] = ls.split(":", 1)[1].strip()
        elif ls.startswith("cta_primary:"):
            val = ls.split(":", 1)[1].strip()
            current["content"]["cta_primary"] = val.split("|")[0].strip().strip('"')
        elif ls.startswith("animation:"):
            current["animation"] = ls.split(":", 1)[1].strip()
        elif ls.startswith("hero_image:"):
            current["content"]["hero_image"] = ls.split(":", 1)[1].strip()
        elif ls.startswith("items:"):
            current["content"]["items"] = ls.split(":", 1)[1].strip()
        elif ls.startswith("content:"):
            # Upgraded ===SECTIONS=== prompt emits "content:" with the
            # per-section copy (cards/rows/items). Older prompts used "items:";
            # store under "items" to keep one canonical key downstream.
            current["content"]["items"] = ls.split(":", 1)[1].strip()
    if current:
        sections.append(current)

    # Markdown fallback: scan for `## Hero`, `### Pricing Section`, etc.
    # Block-level headings ("Sections", "Pages", "Header", "Footer", etc.)
    # are NOT real sections — they're labels for whole research blocks. The
    # denylist filters them. Translate display name → snake_case type so
    # downstream code can match the same vocabulary.
    if not sections:
        _BLOCK_HEADINGS = {
            "sections", "pages", "header", "footer", "navigation", "nav",
            "overview", "summary", "introduction", "design", "design system",
            "design_system", "layout", "layout_blueprint", "css_variables",
            "css variables", "fonts", "vibe", "copy_tone", "copy tone",
            "entities", "sidebar", "dashboard_kpis", "dashboard kpis", "kpis",
            "status_badges", "status badges", "key_components", "key components",
            "visual_dna", "visual dna", "design_system_name", "design system name",
        }
        for hm in _re_sp.finditer(
            r"^[ \t]*(?:#{2,4}|\*\*)\s*([A-Z][\w \-/&]{1,40}?)\s*\**\s*:?\s*$",
            block, _re_sp.MULTILINE,
        ):
            display = hm.group(1).strip().rstrip(":").strip()
            slug = _re_sp.sub(r"[^\w]+", "_", display.lower()).strip("_")
            if not slug or slug in _BLOCK_HEADINGS:
                continue
            sections.append({
                "type": slug,
                "headline": display,
                "subheadline": "",
                "content": {},
                "animation": "",
            })
            if len(sections) >= 12:
                break
    return sections


def _parse_pages_block(block: str) -> list:
    """Parse ===PAGES=== text → list of page dicts for consumer sites.

    Captures path, title, hero, AND a structured `sections` list per page so
    Phase 2 gets explicit per-section specs (headline, layout, imagery, etc.)
    instead of having to fish them out of the raw research blob.

    Two formats supported:

    1. RICH (preferred — emitted by the upgraded research prompt):

         [page: home]
         path: /
         purpose: ...
         hero_headline: "..."
         hero_subheadline: "..."
         sections:
           [section: hero]
             headline: "..."
             layout: ...
             imagery: ...
             content: ...
           [section: features]
             ...

       Each `[section: name]` becomes a dict with the same shape as
       `_parse_sections` returns for landing pages.

    2. LEGACY (still tolerated when Gemini drifts back to the old shape):

         [page: home]
         path: /
         sections: hero, services, testimonials, cta
         purpose: ...

       Each comma-separated entry becomes a minimal `{"type": <name>}` dict.

    A markdown `## Home` / `### About Us` fallback covers cases where Gemini
    skips the `[page: ...]` syntax entirely. In that path each page gets an
    empty sections list so downstream defaults can fill in.
    """
    pages: list = []
    current: dict = {}
    current_section: dict | None = None
    in_sections_block = False
    if not block:
        return pages
    for line in block.splitlines():
        ls = line.strip()

        # Page header opens — flush any in-flight section + page
        m = _re_sp.match(r"\[page:\s*(.+?)\]", ls)
        if m:
            if current_section is not None and current is not None:
                current.setdefault("sections", []).append(current_section)
                current_section = None
            if current:
                pages.append(current)
            title = m.group(1).strip().title()
            current = {
                "title": title,
                "path": "/",
                "component": title.replace(" ", "") + "Page",
                "type": "custom",
                "description": "",
                "sections": [],
            }
            in_sections_block = False
            continue

        if not current:
            continue

        # Nested `[section: <slug>]` inside a page
        sm = _re_sp.match(r"\[section:\s*(\w[\w_-]*)\]", ls)
        if sm:
            if current_section is not None:
                current.setdefault("sections", []).append(current_section)
            current_section = {
                "type": sm.group(1).strip().lower().replace("-", "_"),
                "headline": "",
                "subheadline": "",
                "content": {},
                "animation": "",
            }
            in_sections_block = True
            continue

        if current_section is not None:
            # Inside a [section: …] — collect its fields
            if ls.startswith("headline:"):
                current_section["headline"] = ls.split(":", 1)[1].strip().strip('"').strip("'")
                continue
            if ls.startswith("subheadline:"):
                current_section["subheadline"] = ls.split(":", 1)[1].strip().strip('"').strip("'")
                continue
            if ls.startswith("layout:"):
                current_section["content"]["layout"] = ls.split(":", 1)[1].strip()
                continue
            if ls.startswith("background:"):
                current_section["content"]["background"] = ls.split(":", 1)[1].strip()
                continue
            if ls.startswith("imagery:"):
                current_section["content"]["imagery"] = ls.split(":", 1)[1].strip()
                continue
            if ls.startswith("content:"):
                current_section["content"]["items"] = ls.split(":", 1)[1].strip()
                continue
            if ls.startswith("animation:"):
                current_section["animation"] = ls.split(":", 1)[1].strip()
                continue
            # Any other line ends the section's field block but stays inside
            # the page; close the section and let the page-level parser pick
            # up the line below.
            if ls and not ls.startswith("["):
                # Continue collecting page-level fields after closing section
                pass

        if ls.startswith("path:"):
            if current_section is not None:
                current.setdefault("sections", []).append(current_section)
                current_section = None
                in_sections_block = False
            current["path"] = ls.split(":", 1)[1].strip()
        elif ls.startswith("hero_headline:"):
            if current_section is not None:
                current.setdefault("sections", []).append(current_section)
                current_section = None
            current["description"] = ls.split(":", 1)[1].strip().strip('"').strip("'")
            current["hero_headline"] = current["description"]
        elif ls.startswith("hero_subheadline:"):
            if current_section is not None:
                current.setdefault("sections", []).append(current_section)
                current_section = None
            current["hero_subheadline"] = ls.split(":", 1)[1].strip().strip('"').strip("'")
        elif ls.startswith("hero_imagery:"):
            current["hero_imagery"] = ls.split(":", 1)[1].strip()
        elif ls.startswith("purpose:"):
            if current_section is not None:
                current.setdefault("sections", []).append(current_section)
                current_section = None
            purpose_text = ls.split(":", 1)[1].strip()
            current["purpose"] = purpose_text
            if not current.get("description"):
                current["description"] = purpose_text
        elif ls.startswith("sections:"):
            # Legacy form — `sections: hero, services, cta`. The upgraded
            # prompt emits a bare `sections:` line and follows with nested
            # `[section: ...]` blocks; in that case the value after `:` is
            # empty and we fall through to in_sections_block mode.
            raw = ls.split(":", 1)[1].strip()
            in_sections_block = True
            if raw:
                current["sections"] = [
                    {
                        "type": s.strip().lower().replace("-", "_"),
                        "headline": "",
                        "subheadline": "",
                        "content": {},
                        "animation": "",
                    }
                    for s in raw.split(",") if s.strip()
                ]

    if current_section is not None and current is not None:
        current.setdefault("sections", []).append(current_section)
    if current:
        pages.append(current)

    # Markdown fallback: `## Home`, `### About Us` → page entries.
    # Block-level headings ("Pages", "Header", "Footer", …) are NOT real
    # pages — they're block labels. Same denylist idea as _parse_sections.
    if not pages:
        _BLOCK_HEADINGS = {
            "pages", "sections", "header", "footer", "navigation", "nav",
            "overview", "summary", "introduction", "site map", "sitemap",
            "design", "design system", "design_system", "layout",
            "layout_blueprint", "css_variables", "css variables", "fonts",
            "vibe", "copy_tone", "copy tone", "entities", "sidebar",
            "dashboard_kpis", "dashboard kpis", "kpis", "status_badges",
            "status badges", "key_components", "key components",
            "visual_dna", "visual dna", "design_system_name", "design system name",
        }
        for hm in _re_sp.finditer(
            r"^[ \t]*(?:#{2,4}|\*\*)\s*([A-Z][\w \-/&]{1,40}?)\s*\**\s*:?\s*$",
            block, _re_sp.MULTILINE,
        ):
            display = hm.group(1).strip().rstrip(":").strip()
            low = display.lower().strip()
            if not low or low in _BLOCK_HEADINGS:
                continue
            slug = _re_sp.sub(r"[^\w]+", "-", low).strip("-")
            path = "/" if low in {"home", "homepage", "index", "landing"} else f"/{slug}"
            pages.append({
                "title": display,
                "path": path,
                "component": display.replace(" ", "") + "Page",
                "type": "custom",
                "description": "",
                "sections": [],
            })
            if len(pages) >= 12:
                break
    return pages


def attach_deep_research_to_schema(schema: dict, research: str) -> dict:
    """Pluck ===ENTITY_DEEP::Name=== / ===PAGE_DEEP::Name=== blocks from the
    research blob and attach to matching schema entities/pages as
    `deep_research`. Idempotent — overwrites only when a fresh block exists.

    Called from build_project_schema (no-op if Phase D ran nothing yet) AND
    again from project_generator after Phase D enriches the research, so the
    later pass actually picks up the deep blocks.
    """
    if not isinstance(schema, dict) or not research:
        return schema
    if schema.get("entities"):
        for ent in schema["entities"]:
            if not isinstance(ent, dict):
                continue
            name = (ent.get("name") or "").strip()
            if not name:
                continue
            deep = _extract_block(research, f"===ENTITY_DEEP::{name}===", max_chars=4000)
            if deep:
                ent["deep_research"] = deep
    if schema.get("pages"):
        for pg in schema["pages"]:
            if not isinstance(pg, dict):
                continue
            deep = ""
            for key in ("name", "title", "path"):
                candidate = (pg.get(key) or "").strip()
                if not candidate:
                    continue
                deep = _extract_block(research, f"===PAGE_DEEP::{candidate}===", max_chars=4000)
                if deep:
                    break
            if deep:
                pg["deep_research"] = deep
    return schema


def _parse_entity_screens_block(block: str) -> dict:
    """Parse ===ENTITY_SCREENS=== text → dict keyed by entity name (lowercased).

    Each entity maps to a list of screen dicts with fields appropriate to the
    screen kind (list / detail / create). Returned shape:

        {
            "shipment": [
                {"kind": "list",   "layout": "...", "filter_bar": "...", ...},
                {"kind": "detail", "layout": "...", "hero_strip": "...", ...},
                {"kind": "create", "layout": "...", "field_groups": "...", ...},
            ],
            "customer": [...],
        }

    Schema-merging code in build_project_schema attaches each entity's screens
    list back onto the entity dict so Phase 2 admin prompts can render the
    rich per-entity-per-screen spec — symmetric to how multi-page sites get
    rich per-page-per-section spec.
    """
    out: dict = {}
    if not block:
        return out
    current_entity: str | None = None
    current_screen: dict | None = None
    # All field keys we recognise per screen kind. Any other "key:" line is
    # ignored (Gemini sometimes invents extras — we don't blow up).
    _LIST_KEYS = {
        "layout", "filter_bar", "table_columns", "row_actions",
        "bulk_actions", "empty_state", "pagination",
    }
    _DETAIL_KEYS = {
        "layout", "hero_strip", "primary_panels", "side_rails",
        "contextual_actions",
    }
    _CREATE_KEYS = {
        "layout", "field_groups", "smart_defaults", "validation_quirks",
        "primary_cta",
    }
    _ALL_KEYS = _LIST_KEYS | _DETAIL_KEYS | _CREATE_KEYS

    def _flush_screen() -> None:
        nonlocal current_screen
        if current_entity and current_screen:
            out.setdefault(current_entity, []).append(current_screen)
        current_screen = None

    for line in block.splitlines():
        ls = line.strip()

        m_ent = _re_sp.match(r"\[entity:\s*(.+?)\]", ls)
        if m_ent:
            _flush_screen()
            current_entity = m_ent.group(1).strip().lower()
            continue

        if current_entity is None:
            continue

        m_scr = _re_sp.match(r"\[screen:\s*(\w+)\]", ls)
        if m_scr:
            _flush_screen()
            current_screen = {"kind": m_scr.group(1).strip().lower()}
            continue

        if current_screen is None:
            continue

        # Field line: "key: value"
        if ":" in ls:
            key, _, value = ls.partition(":")
            key = key.strip().lower()
            if key in _ALL_KEYS:
                current_screen[key] = value.strip()

    _flush_screen()
    return out


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


# Phrases that signal a "brand name" candidate is actually prose from a prompt
# (e.g. "This project is for a landing page for X"). When a candidate matches,
# we discard it and try the next extraction strategy.
_BRAND_PROSE_PREFIXES = (
    "this project", "this website", "this app", "this site",
    "a landing page", "a website", "an app", "the website",
    "a modern", "build a", "build an", "create a", "create an",
    "make a", "make an", "i want", "we want", "design a",
)
_BRAND_PROSE_FRAGMENTS = (
    "page for", "website for", "app for", "site for",
    "project is for", "project is about",
)
# Words that can't legitimately START a brand name (skip them when scanning
# for the leading capitalised noun phrase).
_BRAND_SKIP_OPENERS = {
    "this", "a", "an", "the", "build", "create", "modern",
    "make", "design", "i", "we", "my", "our",
}


def _looks_like_prose_brand(name: str) -> bool:
    """Return True if `name` looks like a sentence fragment, not a brand."""
    low = name.lower().strip()
    if not low:
        return True
    if any(low.startswith(p) for p in _BRAND_PROSE_PREFIXES):
        return True
    if any(frag in low for frag in _BRAND_PROSE_FRAGMENTS):
        return True
    # Real brand names are short. Anything > 5 words is almost certainly
    # a description, not a name.
    if len(low.split()) > 5:
        return True
    return False


def _extract_brand_from_text(text: str) -> str:
    """Pull a plausible brand name from arbitrary text.

    Tries multiple strategies in order:
      1. First quoted phrase (`"Maplewood Grove"`)
      2. "Brand is a/an…" sentence opener
      3. Leading 1-3 capitalised words (skipping prose openers like "A", "The")

    Returns "" if nothing reasonable is found — callers should provide
    a final fallback like "Project".
    """
    if not text:
        return ""
    import re as _re
    text = text.strip()

    # 1. First quoted phrase — handles "Maplewood Grove", 'Maplewood Grove',
    #    and Unicode curly quotes.
    for pat in (
        r'["“]([A-Za-z][^"“”\n]{1,40}?)["”]',
        r"'([A-Z][A-Za-z0-9 &\.\-]{1,40}?)'",
    ):
        m = _re.search(pat, text)
        if m:
            cand = m.group(1).strip(" .,—-")
            if cand and not _looks_like_prose_brand(cand):
                return cand

    # 2. "Brand is a/an…" or "Brand — a…" — common LLM expansion shape.
    m = _re.match(
        r"\s*([A-Z][A-Za-z0-9 &\.\-']{1,40}?)\s+(?:is|are|—|–|-)\s+(?:a|an|the)\s+",
        text,
    )
    if m:
        cand = m.group(1).strip(" .,—-")
        if cand and not _looks_like_prose_brand(cand):
            return cand

    # 3. Leading 1-3 capitalised words (skipping prose openers).
    capitalised: list[str] = []
    for raw in text.split()[:8]:
        clean_w = _re.sub(r"[^A-Za-z0-9&\-']", "", raw)
        if not clean_w:
            continue
        if clean_w.lower() in _BRAND_SKIP_OPENERS:
            if capitalised:
                break  # opener after a brand word ends the run
            continue
        if clean_w[0].isupper():
            capitalised.append(clean_w)
            if len(capitalised) >= 3:
                break
        elif capitalised:
            break
    if capitalised:
        cand = " ".join(capitalised)
        if not _looks_like_prose_brand(cand):
            return cand

    return ""


def _parse_schema_from_research(
    research: str,
    description: str,
    classification: dict,
    stack: str,
    original_description: str = "",
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

    # Mirror classification into top-level schema fields so every downstream
    # consumer (builders, persistence, quality gate) reads from a single source
    # of truth instead of pipeline locals.
    schema["archetype"] = layout
    schema["domain_kind"] = domain

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
    # Resolution order:
    #   1. Research's ===HEADER=== block (Gemini-extracted brand)
    #   2. Original (pre-expansion) user prompt — clean short input
    #   3. Expanded prompt — prose-heavy, last resort
    # If ===HEADER=== gives prose ("This project is for…"), we discard it
    # and try the prompts. _extract_brand_from_text rejects prose patterns.
    header_block = _extract_block(research, "===HEADER===", max_chars=600)
    brand_name, nav_items = _parse_header_nav(header_block)
    if brand_name and _looks_like_prose_brand(brand_name):
        logger.info("Discarding prose brand_name from research: %r", brand_name[:60])
        brand_name = ""
    if not brand_name:
        clean_expanded = description.split("\n\n---\n\n")[0].strip()
        for source in (original_description, clean_expanded):
            cand = _extract_brand_from_text(source)
            if cand:
                brand_name = cand
                break
        if not brand_name:
            brand_name = "Project"
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

    # ── Landing-page nav fallback ─────────────────────────────
    # When research's ===HEADER=== block is missing/malformed, nav_items
    # is empty and schema.navigation stays []. That's bad: the
    # deterministic navigation.js + MarketingFooter writers both gate on
    # navigation having items, so the page ends up with no nav at all.
    # Synthesize from sections — labels come from each section's
    # headline (project-specific), paths anchor to the section type,
    # icons are picked per-section. Hero / footer / cta sections are
    # excluded since they don't belong in the nav strip.
    _SECTIONS_NOT_IN_NAV = {"hero", "footer", "cta", "newsletter"}
    if (
        layout == "single_page_landing"
        and not schema["navigation"]
        and schema.get("sections")
    ):
        nav_from_sections: list = []
        seen_paths: set = set()
        for s in schema["sections"]:
            if not isinstance(s, dict):
                continue
            sec_type = (s.get("type") or "").strip()
            if not sec_type or sec_type in _SECTIONS_NOT_IN_NAV:
                continue
            path = f"#{sec_type}"
            if path in seen_paths:
                continue
            seen_paths.add(path)
            headline = (s.get("headline") or "").strip()
            # Marketing headlines are often a full sentence — too long
            # for a nav strip. Fall back to humanized type when the
            # headline is verbose or empty.
            if headline and len(headline) <= 30:
                label = headline
            else:
                label = sec_type.replace("_", " ").title()
            nav_from_sections.append({
                "label": label,
                "path": path,
                "icon": _pick_section_icon(label, sec_type),
            })
        if nav_from_sections:
            schema["navigation"] = [{"group": "main", "items": nav_from_sections}]
            logger.info(
                "Landing nav derived from %d sections (HEADER block was empty)",
                len(nav_from_sections),
            )

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
    original_description: str = "",
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
    from app.services.ws_emit import _ws_send

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
        py_schema = _parse_schema_from_research(
            research, description, _classification, stack,
            original_description=original_description,
        )
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

        # Multi-page archetypes: when Gemini's ===PAGES=== block was missing,
        # mis-formatted, or only emitted 1-2 entries, supplement with the
        # standard pages a site of this archetype always has. Mirrors the
        # single_page_landing fallback above so multi-page archetypes don't
        # silently fall through to Claude (which often has nothing to extract
        # either, since the underlying research is the same).
        _PAGE_DEFAULTS_BY_ARCHETYPE = {
            "consumer_website": [
                ("Home",      "/",          "Hero, value proposition, primary CTA"),
                ("About",     "/about",     "Mission, story, leadership"),
                ("Services",  "/services",  "Core offerings and capabilities"),
                ("Resources", "/resources", "Articles, guides, and downloadables"),
                ("Contact",   "/contact",   "Contact form, locations, channels"),
            ],
            "marketplace": [
                ("Home",     "/",                "Featured listings and search entry"),
                ("Browse",   "/browse",          "Filterable listings grid"),
                ("Listing",  "/listings/:id",    "Detail view with seller info"),
                ("Sell",     "/sell",            "Seller onboarding and create-listing flow"),
                ("Account",  "/account",         "Buyer/seller dashboard"),
            ],
            "portfolio": [
                ("Home",    "/",            "Hero with featured work"),
                ("Work",    "/work",        "Project gallery"),
                ("Project", "/work/:slug",  "Case study detail"),
                ("About",   "/about",       "Bio and skills"),
                ("Contact", "/contact",     "Contact form and socials"),
            ],
            "blog": [
                ("Home",       "/",                "Latest posts and featured article"),
                ("Articles",   "/articles",        "Browse all posts with filters"),
                ("Article",    "/articles/:slug",  "Single post view"),
                ("Categories", "/categories",      "Browse posts by topic"),
                ("About",      "/about",           "About the publication and authors"),
                ("Contact",    "/contact",         "Reach the editorial team"),
            ],
        }
        _multipage_archetypes = set(_PAGE_DEFAULTS_BY_ARCHETYPE.keys())
        if app_type in _multipage_archetypes and page_count < 3:
            _existing_paths = {(p.get("path") or "").lower() for p in py_schema.get("pages", [])}
            _existing_titles = {(p.get("title") or "").lower() for p in py_schema.get("pages", [])}
            for title, path, desc in _PAGE_DEFAULTS_BY_ARCHETYPE[app_type]:
                if path.lower() in _existing_paths or title.lower() in _existing_titles:
                    continue
                py_schema.setdefault("pages", []).append({
                    "title": title,
                    "path": path,
                    "component": title.replace(" ", "") + "Page",
                    "type": "custom",
                    "description": desc,
                    "sections": [],
                })
                if len(py_schema["pages"]) >= 7:
                    break
            page_count = len(py_schema["pages"])

        # Safety net: if landing has sections but nav is still empty (the
        # in-parser fallback at _parse_schema_from_research didn't fire — e.g.
        # all section types fell into the deny-list, or sections were filled
        # by the section-defaults block above which runs AFTER that fallback),
        # derive nav from sections here. The deterministic MarketingHeader
        # writer in project_generator.py gates on len(nav_groups) > 0, so an
        # empty nav silently disables the header.
        if (
            app_type == "single_page_landing"
            and nav_items == 0
            and section_count > 0
        ):
            _NOT_IN_NAV = {"hero", "footer", "cta", "cta_final", "newsletter"}
            _derived: list = []
            _seen: set = set()
            for s in py_schema.get("sections", []):
                if not isinstance(s, dict):
                    continue
                sec_type = (s.get("type") or "").strip()
                if not sec_type or sec_type in _NOT_IN_NAV:
                    continue
                path = f"#{sec_type}"
                if path in _seen:
                    continue
                _seen.add(path)
                headline = (s.get("headline") or "").strip()
                if headline and len(headline) <= 30:
                    label = headline
                else:
                    label = sec_type.replace("_", " ").title()
                _derived.append({
                    "label": label,
                    "path": path,
                    "icon": _pick_section_icon(label, sec_type),
                })
                if len(_derived) >= 6:
                    break
            if _derived:
                py_schema["navigation"] = [{"group": "main", "items": _derived}]
                nav_items = len(_derived)
                logger.info(
                    "Fast-parse safety net: derived %d nav items from sections",
                    nav_items,
                )

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

        # read=60s — Anthropic streaming chunks arrive sub-second; a 60s gap is
        # a stall. Keeps cancellation responsive when user presses Stop.
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=60.0)) as client:
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
            py_schema = _parse_schema_from_research(
                research, description, _classification, stack,
                original_description=original_description,
            )
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

        # ── Per-entity UI screens (admin-family only) ──────────────────
        # The upgraded ===ENTITY_SCREENS=== block emits structured list/
        # detail/create specs per entity. Attach each entity's screens to
        # the schema so schema_to_entity_screens_spec can render rich UI
        # spec into Phase 2 prompts (symmetric to schema_to_pages_spec).
        if _is_admin and schema.get("entities"):
            screens_block = _extract_block(research, "===ENTITY_SCREENS===", max_chars=20000)
            if screens_block:
                screens_by_entity = _parse_entity_screens_block(screens_block)
                if screens_by_entity:
                    for ent in schema["entities"]:
                        if not isinstance(ent, dict):
                            continue
                        # Match by entity name, case-insensitive. Singular/
                        # plural is preserved as Gemini emits it.
                        name = (ent.get("name") or "").strip().lower()
                        if name and name in screens_by_entity:
                            ent["screens"] = screens_by_entity[name]

        # Phase D deep_research blocks are attached AFTER this function returns
        # (the upstream pipeline calls attach_deep_research_to_schema once Phase D
        # has enriched the research blob).
        attach_deep_research_to_schema(schema, research)

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

    # Defensive: discard prose-shaped brand names (e.g. Claude or fallbacks
    # that emitted "This project is for…"). Try to recover one from the
    # brand.description before giving up to a generic placeholder.
    _brand_name_raw = (schema["brand"].get("name") or "").strip()
    if _brand_name_raw and _looks_like_prose_brand(_brand_name_raw):
        logger.info("Sanitizing prose brand_name in validate: %r", _brand_name_raw[:60])
        recovered = _extract_brand_from_text(_brand_name_raw) or _extract_brand_from_text(
            schema["brand"].get("description") or ""
        )
        schema["brand"]["name"] = recovered or "Project"

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

    # Stable ordinal for parallel-batching plumbing (Phase 2). Sections and
    # pages keep a fixed `_index` after validation so a batcher can group
    # them deterministically across retries — array position is reliable
    # today but `_index` makes the contract explicit and survives any
    # future schema rewrite that re-orders or filters lists.
    for i, section in enumerate(schema.get("sections", []) or []):
        if isinstance(section, dict):
            section["_index"] = i
    for i, page in enumerate(schema.get("pages", []) or []):
        if isinstance(page, dict):
            page["_index"] = i

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


def schema_to_entity_screens_spec(schema: dict) -> str:
    """Convert per-entity UI screens into a structured spec for admin Phase 2.

    Mirrors schema_to_pages_spec for multi-page sites. Each entity gets up
    to 3 screen blocks (list, detail, create) with the full per-screen
    field set so Claude has explicit UI structure — filter bar contents,
    table columns, panel layouts, field groups — instead of falling back
    to generic CRUD scaffolding from the data schema alone.

    Returns "" when no entity has screens attached (the data-schema-only
    spec from schema_to_entity_spec still flows in that case).
    """
    entities = schema.get("entities", []) or []
    entities_with_screens = [
        e for e in entities
        if isinstance(e, dict) and isinstance(e.get("screens"), list) and e["screens"]
    ]
    if not entities_with_screens:
        return ""

    lines = [
        "## ENTITY SCREENS — per-entity UI structure (build EVERY screen, every field)\n",
        "Each entity below has 1-3 named screens. Treat each screen as a real",
        "page in the admin: render its filter_bar/table_columns/row_actions for",
        "list views, its primary_panels/side_rails for detail views, and its",
        "field_groups for create/edit forms. Do NOT fall back to a generic",
        "DataTable + plain form template — the screens spec IS the design.\n",
    ]

    # Order screens deterministically so the output is stable (list, detail, create).
    _ORDER = {"list": 0, "detail": 1, "create": 2, "edit": 2}

    for ent in entities_with_screens:
        name = ent.get("name", "Entity")
        slug = ent.get("slug", "")
        screens = sorted(
            ent["screens"],
            key=lambda s: _ORDER.get((s.get("kind") or "").lower(), 9),
        )
        lines.append(f"### Entity: {name}" + (f" (slug: {slug})" if slug else ""))
        # Phase D deep research: a focused 600-1000 token paragraph from a
        # dedicated Gemini call (industry patterns, validation quirks,
        # workflow transitions). Render BEFORE the screens so Claude reads
        # the why before the structural what.
        deep = ent.get("deep_research")
        if deep:
            lines.append(f"  Industry context (from focused research):")
            for ln in deep.splitlines():
                if ln.strip():
                    lines.append(f"    {ln.rstrip()}")
            lines.append("")
        for s in screens:
            kind = (s.get("kind") or "").lower()
            lines.append(f"  Screen: [{kind}]")
            # Render each field on its own line if present. Field set varies by
            # kind, but we render any recognised field — tolerant of extras.
            for field in (
                "layout", "filter_bar", "table_columns", "row_actions",
                "bulk_actions", "empty_state", "pagination",
                "hero_strip", "primary_panels", "side_rails", "contextual_actions",
                "field_groups", "smart_defaults", "validation_quirks", "primary_cta",
            ):
                val = s.get(field)
                if val:
                    lines.append(f"    {field}: {val}")
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
    """Convert schema sections into an explicit per-section spec for landing pages.

    Mirrors schema_to_pages_spec (multi-page) so Phase 2 reads the same rich
    structure: layout, background, imagery, copy items, and animation per
    section. The parser puts these under `content` as a sub-dict; render each
    on its own line so Claude sees a structured spec instead of a stringified
    Python dict (the old behaviour, which truncated to 200 chars and ate most
    of the research signal).
    """
    sections = schema.get("sections", [])
    if not sections:
        return ""

    lines = ["## LANDING PAGE SECTIONS (build EVERY one — full domain-specific copy, no placeholders)\n"]
    for i, section in enumerate(sections, 1):
        s_type = section.get("type", "custom")
        headline = section.get("headline", "")
        subheadline = section.get("subheadline", "")
        animation = section.get("animation", "")
        content = section.get("content", {}) or {}

        lines.append(f"### {i}. [{s_type}]")
        if headline:
            lines.append(f"   headline: \"{headline}\"")
        if subheadline:
            lines.append(f"   subheadline: \"{subheadline}\"")

        # Rich format (preferred — `content` is a dict with structured fields)
        if isinstance(content, dict) and content:
            if content.get("layout"):
                lines.append(f"   layout: {content['layout']}")
            if content.get("background"):
                lines.append(f"   background: {content['background']}")
            if content.get("imagery"):
                lines.append(f"   imagery: {content['imagery']}")
            if content.get("hero_image"):
                lines.append(f"   hero_image: {content['hero_image']}")
            if content.get("items"):
                lines.append(f"   content: {content['items']}")
            if content.get("cta_primary"):
                lines.append(f"   cta_primary: \"{content['cta_primary']}\"")
        elif content:
            # Legacy: content is a string. Pass it through without truncation.
            lines.append(f"   content: {content}")

        if animation:
            lines.append(f"   animation: {animation}")
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
        desc = page.get("purpose", "") or page.get("description", "")
        sections = page.get("sections", [])
        component = page.get("component", f"{title.replace(' ', '')}Page")
        hero_headline = page.get("hero_headline", "")
        hero_subheadline = page.get("hero_subheadline", "")
        hero_imagery = page.get("hero_imagery", "")

        lines.append(f"### {i}. {title} → `{path}` (component: `{component}`)")
        if desc:
            lines.append(f"   Purpose: {desc}")
        if hero_headline:
            lines.append(f"   Hero headline: \"{hero_headline}\"")
        if hero_subheadline:
            lines.append(f"   Hero subheadline: \"{hero_subheadline}\"")
        if hero_imagery:
            lines.append(f"   Hero imagery: {hero_imagery}")
        # Phase D deep research — focused industry-pattern paragraph from a
        # dedicated Gemini call. Render before sections so Claude reads the
        # job-to-be-done + content beats before the structural spec.
        deep = page.get("deep_research")
        if deep:
            lines.append(f"   Industry context (from focused research):")
            for ln in deep.splitlines():
                if ln.strip():
                    lines.append(f"     {ln.rstrip()}")

        # `sections` may be:
        #   - a list of dicts (rich spec from upgraded research prompt)
        #   - a list of strings (legacy comma-separated form, still tolerated)
        #   - empty (fall back to per-page-name hints)
        if sections and isinstance(sections[0], dict):
            lines.append(f"   Sections ({len(sections)} required — build EVERY one):")
            for s_idx, sec in enumerate(sections, 1):
                stype = sec.get("type", "section")
                shead = sec.get("headline", "")
                ssub = sec.get("subheadline", "")
                content = sec.get("content", {}) or {}
                slayout = content.get("layout", "")
                sbg = content.get("background", "")
                simg = content.get("imagery", "")
                sitems = content.get("items", "")
                sanim = sec.get("animation", "")
                lines.append(f"     {s_idx}. [{stype}]")
                if shead:
                    lines.append(f"        headline: \"{shead}\"")
                if ssub:
                    lines.append(f"        subheadline: \"{ssub}\"")
                if slayout:
                    lines.append(f"        layout: {slayout}")
                if sbg:
                    lines.append(f"        background: {sbg}")
                if simg:
                    lines.append(f"        imagery: {simg}")
                if sitems:
                    lines.append(f"        content: {sitems}")
                if sanim:
                    lines.append(f"        animation: {sanim}")
        elif sections:
            # Legacy: list of strings
            section_names = [
                s if isinstance(s, str) else (s.get("type", "") if isinstance(s, dict) else "")
                for s in sections
            ]
            section_names = [s for s in section_names if s]
            if section_names:
                lines.append(f"   Sections: {', '.join(section_names)}")
        else:
            # Fallback hints per common page names — domain-agnostic skeletons
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
                lines.append(f"   Sections (fallback — Gemini did not specify, use these): {hint}")
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
