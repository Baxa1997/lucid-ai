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


# Hard ceiling — protects against runaway intents and budget blowups.
# Plan stage = N parallel Claude calls in Stage 6, so unbounded page counts
# would blow up cost + latency. 20 covers the largest realistic consumer
# marketing surfaces (multi-product SaaS, multi-location healthcare).
_HARD_MAX_PAGES = 20


def _build_plan_schema(min_pages: int, max_pages: int) -> dict[str, Any]:
    """Build the Gemini responseSchema with dynamic page count bounds.

    `min_pages` / `max_pages` come from `_compute_page_range` — keyed off
    `intent.business_category` so a portfolio site doesn't get padded to
    12 pages and a marketplace doesn't get capped at 7.
    """
    return {
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
                "minItems": max(1, min_pages),
                "maxItems": min(_HARD_MAX_PAGES, max_pages),
                "items": {
                    "type": "OBJECT",
                    "required": ["route", "title", "sections"],
                    "properties": {
                        "route":   {"type": "STRING", "maxLength": 40},
                        "title":   {"type": "STRING", "maxLength": 60},
                        "purpose": {"type": "STRING", "maxLength": 200},
                        # Detail-page pair. When true the codegen layer
                        # also emits <route>/[slug]/page.js, reading rows
                        # from `detail_source` (a data_model table name
                        # or a mock_db collection). False/omitted = the
                        # route is a singleton marketing page.
                        "detail_template": {"type": "BOOLEAN"},
                        "detail_source":   {"type": "STRING", "maxLength": 60},
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


def _compute_page_range(
    intent: dict[str, Any] | None,
    purpose_data: dict[str, Any] | None,
) -> tuple[int, int]:
    """Return (min_pages, max_pages) keyed off business_category.

    Ranges follow the shape agreed in the design discussion:
      - portfolio / local_services   → 5-9
      - consumer_website (default)   → 6-12
      - real_estate / healthcare /
        education (bigger surface)   → 7-14
      - marketplace / ecommerce      → 8-15
      - saas (multi-product)         → 8-18
    Hard ceiling _HARD_MAX_PAGES (20) is enforced inside `_build_plan_schema`.
    """
    category = (intent or {}).get("business_category", "").strip().lower()
    subcategory = (intent or {}).get("business_subcategory", "").strip().lower()
    purpose = ((purpose_data or {}).get("primary_purpose") or "").strip().lower()

    if (
        "marketplace" in category or "marketplace" in subcategory
        or "ecommerce" in category or "ecommerce" in subcategory
        or category in ("retail_fashion", "retail", "fashion")
        or "shop" in subcategory
    ):
        return (8, 15)

    if category == "saas" or "saas" in subcategory or purpose == "signup":
        return (8, 18)

    if (
        category in ("creative_arts", "portfolio", "services_local")
        or "portfolio" in subcategory
    ):
        return (5, 9)

    if category in ("real_estate", "healthcare", "education"):
        return (7, 14)

    # Default consumer_website
    return (6, 12)


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

2. PAGES ({min_pages}-{max_pages} pages — aim for a real, browseable website, not a brochure)
   First page MUST be route="/" (home).
   For each page:
     - route: e.g. "/", "/menu", "/about", "/contact"
     - title: short page title
     - purpose: 1-2 sentence brief on what this page communicates
     - sections: 2-7 sections appropriate for the page
     - detail_template: true ONLY for list/index routes where users click
       into individual items (products, listings, articles, properties,
       courses, tours, agents, dishes). The codegen will emit a paired
       <route>/[slug]/page.js detail view. Default false.
     - detail_source: when detail_template is true, the data collection
       name (e.g. "products", "listings", "properties"). Use the entity
       name from the brand's domain — singular or plural is fine, codegen
       normalizes. Omit when detail_template is false.

   Examples of detail_template = true:
     /shop → products | /listings → listings | /properties → properties
     /agents → agents | /trainers → trainers | /tours → tours
     /courses → courses | /menu → dishes | /work → projects
   Examples of detail_template = false:
     /, /about, /contact, /pricing, /story, /process, /sustainability

PAGE GUIDANCE BY DOMAIN (use these as a floor, not a ceiling — add more if the brand warrants it)
- food_and_beverage:   /, /menu, /story, /chefs, /events, /private-events, /gallery, /reservations, /contact
- fitness_wellness:    /, /classes, /trainers, /membership, /schedule, /transformations, /facilities, /about, /contact
- hospitality_travel:  /, /rooms, /suites, /dining, /experiences, /amenities, /gallery, /local-guide, /contact
- services_local:      /, /services, /portfolio, /process, /team, /pricing, /testimonials, /about, /contact
- retail_fashion:      /, /shop, /collections, /lookbook, /editorial, /sustainability, /story, /stores, /contact
- saas:                /, /features, /pricing, /solutions, /customers, /integrations, /security, /about, /contact   (NOT /docs or /blog)
- creative_arts:       /, /work, /process, /clients, /services, /story, /press, /contact
- portfolio:           /, /work, /services, /process, /clients, /press, /about, /contact      (NOT /journal/blog)
- real_estate:         /, /listings, /buy, /sell, /agents, /neighborhoods, /resources, /about, /contact
- healthcare:          /, /services, /doctors, /locations, /patient-resources, /insurance, /about, /contact
- education:           /, /programs, /admissions, /campus-life, /faculty, /tuition, /about, /contact

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
        {"route": "/",         "title": "Home",
         "sections": [{"type": "hero"}, {"type": "value_prop"}, {"type": "features"}, {"type": "testimonials"}, {"type": "cta"}]},
        {"route": "/services", "title": "Services",
         "sections": [{"type": "hero"}, {"type": "features"}, {"type": "process"}, {"type": "cta"}]},
        {"route": "/work",     "title": "Our Work",
         "sections": [{"type": "hero"}, {"type": "gallery"}, {"type": "testimonials"}]},
        {"route": "/process",  "title": "How We Work",
         "sections": [{"type": "hero"}, {"type": "process"}, {"type": "how_it_works"}, {"type": "cta"}]},
        {"route": "/about",    "title": "About",
         "sections": [{"type": "hero"}, {"type": "story"}, {"type": "team"}, {"type": "stats"}]},
        {"route": "/contact",  "title": "Contact",
         "sections": [{"type": "hero"}, {"type": "contact"}, {"type": "locations"}]},
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
    purpose_data: dict | None = None,
) -> dict[str, Any]:
    """Build the multi-page plan. Returns a plan dict — never raises."""
    from app.services.landing_gemini import structured_distill

    audience = (intent.get("target_audience") or {}).get("primary") or "general consumers"
    personality = intent.get("brand_personality") or []
    pers_str = ", ".join(personality[:4]) if personality else "modern, clear"

    min_pages, max_pages = _compute_page_range(intent, purpose_data)
    logger.info(
        "website_plan: page range %d-%d (category=%r subcategory=%r)",
        min_pages, max_pages,
        intent.get("business_category"), intent.get("business_subcategory"),
    )

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
        "min_pages":     min_pages,
        "max_pages":     max_pages,
    }
    prompt = _PLAN_PROMPT.format(**fmt)
    plan_schema = _build_plan_schema(min_pages, max_pages)

    try:
        raw = await structured_distill(
            prompt,
            timeout_s,
            label="website_plan",
            response_schema=plan_schema,
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


# Routes whose semantics ALWAYS imply "list of items + per-item detail page".
# Maps the route slug (without leading slash) to the canonical data source
# name used for the detail JSON file. Overrides Gemini if it sets the flag
# wrong, AND fills in detail_source when Gemini forgot.
_FORCE_DETAIL_TEMPLATE: dict[str, str] = {
    "products":     "products",
    "product":      "products",
    "shop":         "products",
    "catalog":      "products",
    "store":        "products",
    "listings":     "listings",
    "listing":      "listings",
    "properties":   "properties",
    "property":     "properties",
    "homes":        "properties",
    "courses":      "courses",
    "course":       "courses",
    "tours":        "tours",
    "tour":         "tours",
    "agents":       "agents",
    "agent":        "agents",
    "trainers":     "trainers",
    "trainer":      "trainers",
    "doctors":      "doctors",
    "doctor":       "doctors",
    "cars":         "cars",
    "car":          "cars",
    "vehicles":     "vehicles",
    "vehicle":      "vehicles",
    "jobs":         "jobs",
    "job":          "jobs",
    "open-roles":   "jobs",
    "rooms":        "rooms",
    "room":         "rooms",
    "suites":       "suites",
    "destinations": "destinations",
    "experiences":  "experiences",
    "collections":  "collections",
    "case-studies": "case_studies",
    "case-study":   "case_studies",
    "customers":    "customers",
    "events":       "events",
    "event":        "events",
    "classes":      "classes",
    "class":        "classes",
    "services":     "services",   # ambiguous, but more often list-with-detail
    "service":      "services",
    "team":         "team_members",
    "instructors":  "instructors",
    "speakers":     "speakers",
    "menu":         "menu_items",
    "dishes":       "dishes",
}

# Routes whose semantics NEVER warrant a detail page — these are flat
# marketing surfaces with no per-item drilldown. Even if Gemini flips
# detail_template to true, we force it back to false to avoid emitting
# nonsensical /[slug] routes.
_NEVER_DETAIL_TEMPLATE: frozenset[str] = frozenset({
    "",            # home
    "about",
    "contact",
    "pricing",
    "story",
    "process",
    "how-it-works",
    "how_it_works",
    "sustainability",
    "history",
    "mission",
    "values",
    "philosophy",
    "credentials",
    "accreditation",
    "amenities",
    "membership",
    "memberships",
    "benefits",
    "press",
    "patient-resources",
    "insurance",
    "campus-life",
    "tuition",
    "admissions",
    "security",
    "integrations",
    "features",
    "solutions",
    "neighborhoods",
    "local-guide",
    "stores",
    "dining",
    "reservations",
    "schedule",
    "transformations",
    "facilities",
    "editorial",
    "lookbook",
    "private-events",
    "buy",
    "sell",
    "financing",
    "compare",
})


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
        # Normalize detail_template + detail_source. Three-stage logic:
        #   1. Home page (/) can never be a detail template — no slug parent.
        #   2. _NEVER_DETAIL_TEMPLATE → force false (overrides Gemini's true).
        #   3. _FORCE_DETAIL_TEMPLATE → force true (overrides Gemini's false)
        #      and inject the canonical detail_source.
        #   4. Otherwise → trust Gemini's call.
        is_home = route == "/" or route == ""
        route_key = route.strip("/").split("/")[0].lower() if route else ""
        gemini_wants_detail = p.get("detail_template") is True

        if is_home:
            wants_detail = False
            forced_source = None
        elif route_key in _NEVER_DETAIL_TEMPLATE:
            wants_detail = False
            forced_source = None
            if gemini_wants_detail:
                logger.info(
                    "website_plan: forcing detail_template=False on %r (in NEVER list)",
                    route,
                )
        elif route_key in _FORCE_DETAIL_TEMPLATE:
            wants_detail = True
            forced_source = _FORCE_DETAIL_TEMPLATE[route_key]
            if not gemini_wants_detail:
                logger.info(
                    "website_plan: forcing detail_template=True on %r (in FORCE list, source=%s)",
                    route, forced_source,
                )
        else:
            wants_detail = gemini_wants_detail
            forced_source = None

        p["detail_template"] = wants_detail
        if wants_detail:
            # Forced sources win — they reflect canonical data-model naming.
            ds = forced_source or (p.get("detail_source") or "").strip().lower()
            if not ds:
                ds = route_key or "items"
            # Slugify: snake_case-safe characters only, no spaces/dots.
            import re as _re_sluggy
            ds = _re_sluggy.sub(r"[^a-z0-9_]+", "_", ds).strip("_") or "items"
            p["detail_source"] = ds
        else:
            p.pop("detail_source", None)
        if is_home:
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
