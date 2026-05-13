"""Build the multi-page website plan from intent + visual_dna.

Produces the `plan` dict that `website_orchestrator.generate_website` expects:
  {
    "brand": {"name": "...", "tagline": "...", "domain": "..."},
    "pages": [
      {"route": "/", "title": "Home", "purpose": "...",
       "sections": [{"type": "hero", "purpose": "..."}, ...]},
      ...
    ],
  }

One Gemini Flash call. Tight schema with maxLength constraints so the
output stays compact and predictable. Falls back to a sensible default
plan on any failure so the pipeline never blocks.
"""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


_PLAN_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "required": ["brand", "pages"],
    "properties": {
        "brand": {
            "type": "OBJECT",
            "required": ["name", "tagline", "domain"],
            "properties": {
                "name":    {"type": "STRING", "maxLength": 60},
                "tagline": {"type": "STRING", "maxLength": 120},
                "domain":  {"type": "STRING", "maxLength": 50},
            },
        },
        "pages": {
            "type": "ARRAY",
            "minItems": 3,
            "maxItems": 7,
            "items": {
                "type": "OBJECT",
                "required": ["route", "title", "sections"],
                "properties": {
                    "route":   {"type": "STRING", "maxLength": 40},
                    "title":   {"type": "STRING", "maxLength": 60},
                    "purpose": {"type": "STRING", "maxLength": 200},
                    "sections": {
                        "type": "ARRAY",
                        "minItems": 2,
                        "maxItems": 7,
                        "items": {
                            "type": "OBJECT",
                            "required": ["type"],
                            "properties": {
                                "type":    {"type": "STRING", "maxLength": 30},
                                "purpose": {"type": "STRING", "maxLength": 200},
                            },
                        },
                    },
                },
            },
        },
    },
}


_PLAN_PROMPT = """You are designing the page structure for a brand-coherent website.

BRAND CONTEXT
  Description:   {description}
  Category:      {category}
  Subcategory:   {subcategory}
  Geography:     {geography}
  Audience:      {audience}
  Personality:   {personality}
  Tone:          {tone}

VISUAL_DNA HINTS (from grounded research)
  Cultural intensity:   {intensity}
  Layout signature:     {layout_signature}
  Available anatomies:  {anatomies}

YOUR TASK
Pick the optimal page structure for THIS brand. Output a JSON plan with:

1. BRAND
   - name:    short brand name (≤60 chars, can be invented if not given)
   - tagline: ≤120 chars
   - domain:  one of: food_and_beverage, fitness_wellness, retail_fashion,
              hospitality_travel, services_local, real_estate, saas,
              healthcare, education, creative_arts, general

2. PAGES (3-7 pages)
   First page MUST be route="/" (home).
   For each page:
     - route: e.g. "/", "/menu", "/about", "/contact"
     - title: short page title
     - purpose: 1-2 sentence brief on what this page communicates
     - sections: 2-7 sections appropriate for the page

PAGE GUIDANCE BY DOMAIN
- food_and_beverage:   /, /menu, /story, /events (or /private-events), /contact
- fitness_wellness:    /, /classes, /trainers, /membership, /contact
- hospitality_travel:  /, /rooms, /dining, /experiences, /contact
- services_local:      /, /services, /portfolio, /about, /contact
- retail_fashion:      /, /shop, /collections, /lookbook, /about, /contact
- saas:                /, /features, /pricing, /about, /contact   (NOT /docs or /blog)
- creative_arts:       /, /work, /process, /about, /contact
- portfolio:           /, /work, /services, /about, /contact      (NOT /journal/blog)

══ FORBIDDEN PAGES ══ (these are NOT marketing-style pages, never include them)
- /privacy, /terms, /legal     → these are compliance boilerplate (footer links only)
- /blog, /journal, /news        → these imply a full content system, not one designed page
- /login, /signup, /dashboard   → these are app routes, not marketing pages
- /docs, /api, /reference       → these are docs systems (different generator pattern)
- /faq                          → use a `faq` section on /about or / instead
- /admin, /settings, /account   → these are app routes

SECTION TYPES (pick from these — they map to known anatomies)
  hero, menu, gallery, story, philosophy, testimonials, value_prop,
  features, process, how_it_works, press, team, pricing, faq, cta,
  stats, locations, reservation, contact, newsletter

QUALITY BAR
- Each page reads as a distinct chapter of the brand, not a copy.
- The home page tells the WHOLE brand story in 4-6 sections.
- Inner pages drill into ONE topic (Menu page is about food; About is about story).

Return ONLY the JSON. No markdown wrapper. No prose.
"""


_FALLBACK_PLAN = {
    "brand": {"name": "Brand", "tagline": "", "domain": "general"},
    "pages": [
        {"route": "/",        "title": "Home",
         "sections": [{"type": "hero"}, {"type": "value_prop"}, {"type": "cta"}]},
        {"route": "/about",   "title": "About",
         "sections": [{"type": "hero"}, {"type": "story"}, {"type": "team"}]},
        {"route": "/contact", "title": "Contact",
         "sections": [{"type": "hero"}, {"type": "contact"}]},
    ],
}


def _summarize_anatomies(visual_dna: dict) -> str:
    a = (visual_dna or {}).get("section_anatomies") or {}
    keys = sorted(a.keys())
    return ", ".join(keys) if keys else "(none — using defaults)"


async def build_website_plan(
    description: str,
    intent: dict[str, Any],
    visual_dna: dict[str, Any],
    *,
    timeout_s: float = 60.0,
) -> dict[str, Any]:
    """Build the multi-page plan. Returns a plan dict — never raises."""
    from app.services.landing_gemini import structured_distill

    audience = (intent.get("target_audience") or {}).get("primary") or "general consumers"
    personality = intent.get("brand_personality") or []
    pers_str = ", ".join(personality[:4]) if personality else "modern, clear"

    fmt = {
        "description":   (description or "").strip(),
        "category":      (intent.get("business_category") or "general business").strip(),
        "subcategory":   (intent.get("business_subcategory") or "general").strip(),
        "geography":     (intent.get("geographic_specifics") or "United States").strip(),
        "audience":      audience,
        "personality":   pers_str,
        "tone":          (intent.get("tone") or "friendly").strip(),
        "intensity":     (visual_dna or {}).get("cultural_intensity", "bold"),
        "layout_signature": (visual_dna or {}).get("layout_signature", "modern responsive grid"),
        "anatomies":     _summarize_anatomies(visual_dna),
    }
    prompt = _PLAN_PROMPT.format(**fmt)

    try:
        raw = await structured_distill(
            prompt,
            timeout_s,
            label="website_plan",
            response_schema=_PLAN_SCHEMA,
            max_tokens=2500,
            model="gemini-2.5-flash",
        )
        plan = json.loads(raw) if isinstance(raw, str) else raw
        if not _looks_valid(plan):
            logger.warning("website_plan: model returned invalid plan — using fallback")
            return _fallback_with_brand(intent)
        # Normalize home route as first page
        plan = _normalize(plan)
        logger.info(
            "website_plan: ok — brand=%r pages=%d (%s)",
            plan["brand"]["name"], len(plan["pages"]),
            ", ".join(p["route"] for p in plan["pages"]),
        )
        return plan
    except Exception as exc:
        logger.warning("website_plan: build failed (%s) — using fallback", exc)
        return _fallback_with_brand(intent)


def _looks_valid(plan: Any) -> bool:
    if not isinstance(plan, dict):
        return False
    brand = plan.get("brand")
    pages = plan.get("pages")
    if not isinstance(brand, dict) or not isinstance(pages, list):
        return False
    if not brand.get("name"):
        return False
    if not pages:
        return False
    # Every page must have a route, title, and at least 1 section
    for p in pages:
        if not isinstance(p, dict):
            return False
        if not p.get("route") or not p.get("title"):
            return False
        secs = p.get("sections")
        if not isinstance(secs, list) or not secs:
            return False
    return True


_FORBIDDEN_ROUTES = {
    "/privacy", "/privacy-policy", "/terms", "/terms-of-service", "/legal",
    "/cookie-policy", "/cookies",
    "/blog", "/journal", "/news", "/articles", "/posts",
    "/login", "/signup", "/sign-up", "/register", "/dashboard",
    "/docs", "/documentation", "/api", "/reference",
    "/faq", "/help",  # use sections instead
    "/admin", "/settings", "/account", "/profile",
}


def _normalize(plan: dict) -> dict:
    """Ensure home is first, dedupe routes, drop forbidden routes, lowercase types."""
    pages = plan.get("pages") or []
    seen: set[str] = set()
    home_pages: list[dict] = []
    other_pages: list[dict] = []
    for p in pages:
        route = (p.get("route") or "/").strip()
        if route.lower().rstrip("/") in _FORBIDDEN_ROUTES:
            logger.info("website_plan: dropping forbidden route %r", route)
            continue
        if route in seen:
            continue
        seen.add(route)
        # Normalize section types to lowercase snake_case
        secs = []
        for s in (p.get("sections") or []):
            if isinstance(s, dict) and s.get("type"):
                s["type"] = s["type"].lower().strip()
                secs.append(s)
        p["sections"] = secs
        if route == "/" or route == "":
            p["route"] = "/"
            home_pages.append(p)
        else:
            other_pages.append(p)
    # If no home page in the output, synthesize one from the first page
    if not home_pages and other_pages:
        first = dict(other_pages[0])
        first["route"] = "/"
        first["title"] = "Home"
        home_pages = [first]
        other_pages = other_pages[1:]
    plan["pages"] = home_pages + other_pages
    return plan


def _fallback_with_brand(intent: dict) -> dict:
    """Build the minimal fallback plan but inject brand info from intent."""
    plan = json.loads(json.dumps(_FALLBACK_PLAN))  # deep copy
    suggested = (intent.get("business_subcategory") or
                 intent.get("business_category") or "Brand").strip()
    plan["brand"]["name"] = suggested[:60] or "Brand"
    domain = (intent.get("business_category") or "general").lower()
    plan["brand"]["domain"] = domain[:50] or "general"
    return plan
