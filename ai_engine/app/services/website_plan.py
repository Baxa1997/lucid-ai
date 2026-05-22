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

import copy
import json
import logging
import re
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

    NOTE: Gemini's constrained-decoding compiler rejects schemas with
    "too many states for serving" when nested array×array×string-maxLength
    multiplications get large. Keep this schema LEAN — drop optional text
    fields, keep maxLength tight, and cap sections per page. The previous
    schema (with `purpose: maxLength=200` on both pages AND sections, plus
    sections maxItems=7) exploded the state machine and forced every plan
    call to fall back to the generic 6-page agency template.
    """
    return {
        "type": "OBJECT",
        "required": ["brand", "pages"],
        "properties": {
            "brand": {
                "type": "OBJECT",
                "required": ["name", "tagline", "domain"],
                "properties": {
                    "name":    {"type": "STRING", "maxLength": 50},
                    "tagline": {"type": "STRING", "maxLength": 80},
                    "domain":  {"type": "STRING", "maxLength": 30},
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
                        "route":   {"type": "STRING", "maxLength": 30},
                        "title":   {"type": "STRING", "maxLength": 40},
                        # Keep `purpose` on PAGES (one string per page) — it gives
                        # codegen real per-page context. Do NOT add purpose on
                        # sections (multiplies pages×sections×chars and explodes
                        # Gemini's state machine).
                        "purpose": {"type": "STRING", "maxLength": 120},
                        # Detail-page pair. When true the codegen layer
                        # also emits <route>/[slug]/page.js, reading rows
                        # from `detail_source` (a data_model table name
                        # or a mock_db collection). False/omitted = the
                        # route is a singleton marketing page.
                        "detail_template": {"type": "BOOLEAN"},
                        "detail_source":   {"type": "STRING", "maxLength": 40},
                        "sections": {
                            "type": "ARRAY",
                            "minItems": 2,
                            "maxItems": 6,
                            "items": {
                                "type": "OBJECT",
                                "required": ["type"],
                                "properties": {
                                    "type": {"type": "STRING", "maxLength": 24},
                                    # Optional cohesion hints — give codegen the
                                    # same layout/archetype signal landing has.
                                    # Tight maxLength so we don't re-trigger
                                    # Gemini's "too many states" explosion.
                                    "layout_hint": {"type": "STRING", "maxLength": 24},
                                    "archetype":   {"type": "STRING", "maxLength": 32},
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


_WEBSITE_CONVERSION_RESEARCH_PROMPT = """You are a senior CRO researcher studying MULTI-PAGE websites (not single landing pages). USE GOOGLE SEARCH — do NOT answer from training memory.

PROMPT: "{description}"
CATEGORY: {category}
SUBCATEGORY: {subcategory}

YOUR JOB — identify which conversion-optimized patterns appear on REAL multi-page websites in this domain, and CRUCIALLY on which pages they appear (home vs transactional vs informational).

1. Search for 6-10 real multi-page websites in this domain (not single-page landings). Use queries like:
     a) "best [category] websites 2025"
     b) the top 2-3 brand names in the user's space (e.g. tours → Intrepid, G Adventures, Viator; SaaS analytics → Mixpanel, Amplitude, Heap; luxury hotels → Aman, Six Senses, Belmond; immigration law → Fragomen, Berry Appleman). USE actual brand names from search results — never invent.
   Bias toward brands whose business model involves multi-step browsing (browse → detail → convert).

2. For each reference, list the SITEMAP (the visible top-nav routes — typically 5-12 pages) and which pages contain which conversion elements:

   • sticky_cta            — does any page surface a persistent "Book / Buy / Quote / Sign Up" CTA in a sticky header or floating bar?
   • hero_filter           — does the home or category page have a search/filter widget (dates, location, role, plan)?
   • trust_bar             — slim inline strip on home or product/listing pages with "X yrs / Y customers / Z%"?
   • mid_cta_banner        — re-engagement banner midway down home or transactional pages?
   • full_lead_form        — multi-field form (NOT just newsletter) on the contact, consultation, or quote page?
   • faq_section_on_pages  — which pages have an FAQ block?
   • testimonials_pages    — which pages embed testimonials (home only? service detail? case studies?)?

3. CONCLUSION — for THIS project, give:

   • REQUIRED_PAGES — list the page routes that nearly every reference includes (e.g. for a law firm: `/, /services-or-practice-areas, /attorneys, /case-results, /consultation, /contact`).
   • PER-PAGE CONVERSION ELEMENTS — for each REQUIRED_PAGE, which conversion elements should appear on it. Format:
       /: [trust_bar, mid_cta_banner, lead_form, faq]
       /attorneys: [team, testimonials, cta]
       /consultation: [lead_form / booking_form / quote_form, faq]
   • OMISSIONS — any conversion element that references universally avoid for this domain. Examples: "luxury hotels almost never use sticky CTAs", "architecture portfolios skip trust_bar — they let the work speak".

OUTPUT FORMAT — plain markdown. No JSON.

===REFERENCE_SITEMAPS===
1. <name> — <url>
   Sitemap: /, /<route>, /<route>, ...
   Sticky CTA: yes/no (where: header/floating-bar/none)
   Hero filter: yes/no (where: home/category/none)
   Trust bar: yes/no (which pages)
   Mid CTA banner: yes/no (which pages)
   Lead form: yes/no (which page, what kind: quote/booking/consultation)
   FAQ on pages: <comma-separated routes>
2. ... ≥6 refs ...

===CONCLUSION===
REQUIRED_PAGES: <comma-separated routes>
PER_PAGE_CONVERSION:
  /: [...]
  /<route>: [...]
  ... (one row per REQUIRED_PAGE)
OMISSIONS: <free-text notes on patterns to avoid>
"""


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
  features, benefits, process, how_it_works, press, team, pricing, faq,
  cta, mid_cta_banner, stats, trust_bar, locations, reservation, contact,
  contact_form, lead_form, quote_form, booking_form, newsletter,
  comparison, integrations

PER-SECTION COHESION HINTS (optional but strongly recommended on EVERY section — they let codegen render sibling layouts that vary instead of stacking identical centered cards)
  layout_hint — pick ONE: centered-stack | two-column | split-image-left | split-image-right | grid-3 | grid-4 | grid-2 | carousel | accordion | logo-strip | stat-band | timeline | comparison-table | media-quote
  archetype — pick ONE from the per-type list (omit on types not listed):
    hero: full-bleed-overlay | oversized-watermark | asymmetric-split | type-wrapping-product | video-mask | card-stack
    menu: two-column-dotted | photo-card-grid | categorized-rows
    gallery: asymmetric-12col | marquee-scroll | bento-mosaic
    testimonials: glass-cards-bg | marquee-row | big-quote-portrait
    features / value_prop / how_it_works / process: icon-grid-3 | numbered-stepper | split-image-bullets
  Vary layout_hint across sections WITHIN a page so adjacent sections never share the same hint.

══ CONVERSION-COMPLETENESS BY PAGE (apply when CONVERSION_RESEARCH supports it) ══
Read CONVERSION_RESEARCH ===CONCLUSION=== below. Anything marked REQUIRED there should
appear on the pages it belongs on — but APPLY IT PER-PAGE, not globally:

  • HOME (/) and TRANSACTIONAL PAGES (/shop, /listings, /properties, /book, /tours,
    /classes, /pricing, /reservations, /quote, /consultation, /apply, /trial) →
    these are entry points and conversion surfaces. Include:
      - trust_bar (slim inline strip with stats/logos, NOT a full stats band)
      - mid_cta_banner (re-engagement halfway down the page)
      - lead_form / quote_form / booking_form (whichever matches the CTA — see
        form-mapping below)
      - faq (when CONVERSION_RESEARCH marks it required for the domain)
    Plus the usual hero, value_prop, features, testimonials.

  • INFORMATIONAL PAGES (/about, /story, /team, /press, /sustainability) →
    DO NOT force conversion stack. These are narrative pages — use story,
    philosophy, team, press, stats (full), gallery. NO sticky_cta/mid_cta needed.
    A single closing `cta` block at the bottom is fine.

  • CONTACT-ADJACENT PAGES (/contact) → use a real conversion form
    (contact_form / lead_form / quote_form / booking_form), locations, hours,
    optionally faq for support questions.

  • DETAIL/LIST PAGES (/agents, /trainers, /chefs, /attorneys) → team-centric
    structure, NOT conversion-heavy. team + testimonials + cta is enough.

Form-mapping (pick the form type that matches the page's CTA, NOT the generic word "contact"):
  • Plumber / handyman / "free quote"          → quote_form
  • Tour / hotel / clinic / spa appointment   → booking_form
  • B2B SaaS demo / consultation / SDR funnel → lead_form
  • Restaurant table reservations             → reservation (existing type)
  • Bakery / florist / catering inquiry       → lead_form or quote_form
  • General "drop us a line"                  → contact_form
  • Newsletter only (pure audience build)     → newsletter (use ONLY when the
    site has no transactional CTA elsewhere)

QUALITY BAR
- Each page reads as a distinct chapter of the brand, not a copy.
- The home page tells the WHOLE brand story in 5-8 sections — include the
  conversion stack above when applicable. (Bumped from 4-6 to give room for
  trust_bar + mid_cta_banner without crowding the brand narrative.)
- Inner pages drill into ONE topic (Menu page is about food; About is about story).

Return ONLY the JSON. No markdown wrapper. No prose.

═══ CONVERSION_RESEARCH (grounded reference dump — read before deciding which pages need conversion stack) ═══
{conversion_research}
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


# ── Internal timing budget ─────────────────────────────────────────────
# The plan call is the hard requirement; conversion research is best-effort
# context. Cap research at this many seconds so a slow grounded call can't
# eat the entire timeout budget and starve the plan call. (Repro: a 90s
# global timeout was consumed by a 90s research timeout, leaving the plan
# call to race retry latency and fall through to the generic fallback.)
_CONVERSION_RESEARCH_MAX_TIMEOUT_S = 50.0
_PLAN_MODEL = "gemini-2.5-flash"
_PLAN_MAX_TOKENS = 2500

# Free-form retry shape hint — used when the structured-schema call hits
# Gemini's "too many states for serving" 400. We drop the schema but
# remind the model of the section vocabulary so it doesn't regress to a
# generic agency template.
_FREE_FORM_SHAPE_HINT = (
    "\n\nReturn ONLY a valid JSON object with this shape:\n"
    '{"brand":{"name":"...","tagline":"...","domain":"..."},'
    '"pages":[{"route":"/","title":"...","purpose":"...","sections":'
    '[{"type":"hero","layout_hint":"...","archetype":"..."}, ...]}, ...]}\n'
    "Stay faithful to the SECTION TYPES list above — use trust_bar, "
    "mid_cta_banner, lead_form, quote_form, booking_form on transactional "
    "pages when appropriate. Do NOT fall back to a generic "
    "hero/value_prop/features/cta shape if the project's domain calls "
    "for richer structure (SaaS → /product /pricing /customers /integrations; "
    "e-com → /shop /collections; hospitality → /rooms /experiences)."
)


async def _run_conversion_research(
    description: str,
    intent: dict[str, Any],
    overall_timeout_s: float,
) -> str:
    """Run the grounded conversion-research call with a bounded timeout.

    Returns the research text (≤8000 chars after the caller truncates) or
    "(research unavailable)" on any failure. Never raises — research is
    best-effort context; the plan call is the hard requirement.
    """
    bounded = min(overall_timeout_s, _CONVERSION_RESEARCH_MAX_TIMEOUT_S)
    try:
        from app.services.landing_brief import _grounded_research
        prompt = _WEBSITE_CONVERSION_RESEARCH_PROMPT.format(
            description=(description or "").strip(),
            category=(intent.get("business_category") or "general business").strip(),
            subcategory=(intent.get("business_subcategory") or "general").strip(),
        )
        return await _grounded_research(
            prompt, bounded, label="website_conversion_research", websocket=None,
        ) or "(research unavailable)"
    except Exception as exc:
        logger.warning("website_plan: conversion research failed (%s) — proceeding without", exc)
        return "(research unavailable)"


def _build_plan_format(
    description: str,
    intent: dict[str, Any],
    visual_dna: dict[str, Any],
    *,
    min_pages: int,
    max_pages: int,
    conversion_research: str,
) -> dict[str, Any]:
    """Pack everything _PLAN_PROMPT.format() needs into a single dict."""
    audience = (intent.get("target_audience") or {}).get("primary") or "general consumers"
    personality = intent.get("brand_personality") or []
    pers_str = ", ".join(personality[:4]) if personality else "modern, clear"
    return {
        "description":   (description or "").strip(),
        "category":      (intent.get("business_category") or "general business").strip(),
        "subcategory":   (intent.get("business_subcategory") or "general").strip(),
        "geography":     (intent.get("geographic_specifics") or "United States").strip(),
        "audience":      audience,
        "personality":   pers_str,
        "tone":          (intent.get("tone") or "friendly").strip(),
        "intensity":         (visual_dna or {}).get("cultural_intensity", "bold"),
        "layout_signature":  (visual_dna or {}).get("layout_signature", "modern responsive grid"),
        "anatomies":         _summarize_anatomies(visual_dna),
        "min_pages":         min_pages,
        "max_pages":         max_pages,
        "conversion_research": (conversion_research or "(none)")[:8000],
    }


async def _call_plan(
    prompt: str,
    *,
    schema: dict | None,
    timeout_s: float,
    label: str,
) -> dict | None:
    """Single Gemini plan call. Returns parsed dict or None on any failure.

    ``schema=None`` → free-form mode (we'll validate the shape ourselves).
    ``schema=dict`` → structured-output mode (Gemini constraint decoding).
    """
    from app.services.landing_gemini import structured_distill
    try:
        raw = await structured_distill(
            prompt,
            timeout_s,
            label=label,
            response_schema=schema,
            max_tokens=_PLAN_MAX_TOKENS,
            model=_PLAN_MODEL,
        )
        return json.loads(raw) if isinstance(raw, str) else raw
    except Exception as exc:
        logger.warning("website_plan: %s call failed (%s)", label, exc)
        return None


async def _emit_fallback_warning(websocket: Any, reason: str) -> None:
    """Surface a generic-plan fallback to the user via the workspace chat.

    Without this, when both plan calls fail (Vertex unreachable, repeated
    HTTP 400s, etc.) the user gets the generic 6-page agency template and
    has no idea their actual prompt was discarded. Mirrors the
    `_emit_fallback_warning` pattern used by landing_brief / landing_intent.
    """
    if websocket is None:
        return
    try:
        await websocket.send_json({
            "type": "warning",
            "code": "FALLBACK_PLAN",
            "message": (
                f"⚠️ Website plan unavailable ({reason}). "
                "Using a generic 6-page template — your prompt's domain wasn't applied. "
                "Check ai_engine logs (usually Vertex ADC, quota, or a schema 'too many states' loop)."
            ),
        })
    except Exception:
        pass


async def build_website_plan(
    description: str,
    intent: dict[str, Any],
    visual_dna: dict[str, Any],
    *,
    timeout_s: float = 60.0,
    purpose_data: dict | None = None,
    websocket: Any = None,
) -> dict[str, Any]:
    """Build the multi-page plan. Returns a plan dict — never raises.

    Pipeline:
      1. ``_run_conversion_research`` — grounded CRO research, bounded at
         50s so a slow research call can't starve the plan call.
      2. ``_call_plan`` with structured schema — primary path.
      3. ``_call_plan`` free-form retry on schema-400 ("too many states"),
         keeping the same section-vocabulary guidance via _FREE_FORM_SHAPE_HINT.
      4. Falls back to ``_fallback_with_brand`` only when BOTH plan calls
         fail — and emits a ``FALLBACK_PLAN`` websocket warning so the user
         knows their prompt was discarded.
    """
    min_pages, max_pages = _compute_page_range(intent, purpose_data)
    logger.info(
        "website_plan: page range %d-%d (category=%r subcategory=%r)",
        min_pages, max_pages,
        intent.get("business_category"), intent.get("business_subcategory"),
    )

    # ── Stage 1: grounded conversion research (bounded) ──────────────
    conversion_research = await _run_conversion_research(description, intent, timeout_s)

    # ── Stage 2: assemble plan prompt + schema ──────────────────────
    fmt = _build_plan_format(
        description, intent, visual_dna,
        min_pages=min_pages, max_pages=max_pages,
        conversion_research=conversion_research,
    )
    prompt = _PLAN_PROMPT.format(**fmt)
    schema = _build_plan_schema(min_pages, max_pages)

    # ── Stage 3: structured plan call ───────────────────────────────
    plan = await _call_plan(prompt, schema=schema, timeout_s=timeout_s, label="website_plan")
    if _looks_valid(plan):
        plan = _normalize(plan)
        logger.info(
            "website_plan: ok — brand=%r pages=%d (%s)",
            plan["brand"]["name"], len(plan["pages"]),
            ", ".join(p["route"] for p in plan["pages"]),
        )
        return plan

    # ── Stage 4: free-form retry (no schema) ────────────────────────
    # Schema-constrained call usually fails with "too many states for
    # serving". Re-prompt without the schema but with a stronger shape
    # hint so the model doesn't regress to the generic agency template.
    logger.warning("website_plan: structured plan invalid — retrying free-form")
    plan = await _call_plan(
        prompt + _FREE_FORM_SHAPE_HINT,
        schema=None, timeout_s=timeout_s, label="website_plan_freeform",
    )
    if _looks_valid(plan):
        plan = _normalize(plan)
        logger.info(
            "website_plan: free-form ok — brand=%r pages=%d (%s)",
            plan["brand"]["name"], len(plan["pages"]),
            ", ".join(p["route"] for p in plan["pages"]),
        )
        return plan

    # ── Stage 5: fallback (both calls failed) ───────────────────────
    logger.warning("website_plan: both plan calls failed — using fallback template")
    await _emit_fallback_warning(websocket, "structured + free-form plan calls both failed")
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
        # Normalize section types to lowercase snake_case.
        # layout_hint/archetype are normalized to lowercase kebab-case so
        # downstream lookups against the landing-style vocab match.
        secs = []
        for s in (p.get("sections") or []):
            if isinstance(s, dict) and s.get("type"):
                s["type"] = s["type"].lower().strip()
                if isinstance(s.get("layout_hint"), str):
                    s["layout_hint"] = s["layout_hint"].lower().strip()
                if isinstance(s.get("archetype"), str):
                    s["archetype"] = s["archetype"].lower().strip()
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
            ds = re.sub(r"[^a-z0-9_]+", "_", ds).strip("_") or "items"
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
    plan = copy.deepcopy(_FALLBACK_PLAN)
    suggested = (intent.get("business_subcategory") or
                 intent.get("business_category") or "Brand").strip()
    plan["brand"]["name"] = suggested[:60] or "Brand"
    domain = (intent.get("business_category") or "general").lower()
    plan["brand"]["domain"] = domain[:50] or "general"
    return plan
