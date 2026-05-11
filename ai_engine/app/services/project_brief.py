"""Project Brief — Stage B1 of the multi-page website pipeline.

Where landing_brief.py produces a single self-contained section list, this
module produces a PROJECT-level brief that every page in a multi-page site
shares: brand identity, palette, typography, design system, voice — plus a
``pages[]`` array describing each page's slug, goal, and the section types
it should ship with.

This is the first call in the multi-page pipeline. Per-page section
*content* (headlines, body, items) is filled in later by a per-page
section-codegen pass (B2/B3) that consumes the goal + section_types hint
from this brief.

Design choices:
  • Brand/palette/typography/design_system mirror landing_brief's shape
    so existing downstream consumers (design-system-builder, globals.css
    builder, header/footer renderers) keep working unmodified.
  • The brief is intentionally "thin" — no per-section content yet. Each
    page entry is just (slug, title, page_goal, primary_cta, section_types,
    nav_label). The lean Brief → parallel section codegen pattern fills
    the rest in B2.
  • All public functions are FAIL-SOFT. ``build_project_brief`` returns a
    fallback brief when Gemini is unavailable so downstream stages always
    see a valid object.

Public entry point: ``build_project_brief``.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

# Flash is enough — no tools, no grounding. Distillation only.
_BRIEF_MODEL = os.environ.get("PROJECT_BRIEF_MODEL", "gemini-2.5-flash")

logger = logging.getLogger(__name__)


# ── Allowed enums (also enforced post-hoc in _normalize_brief) ────────

_HEADER_ARCHETYPES = ["transparent-pill", "solid-bar", "centered-logo", "side-rail", "mega-menu"]
_FOOTER_ARCHETYPES = ["mega-columns", "minimalist-row", "cta-band-footer", "centered-stack"]

# Section types a multi-page site can use. Per-page lists are subsets of
# this set. The section codegen library (re-used from landing) implements
# every type listed here; new types must be added there too.
_VALID_SECTION_TYPES = {
    # universal
    "hero", "cta", "footer",
    # content
    "value_prop", "features", "services", "products", "menu", "gallery",
    "story", "story_long", "team", "process", "how_it_works",
    # social proof
    "testimonials", "trust_signals", "press", "stats", "case_studies",
    # commerce / conversion
    "pricing", "comparison", "faq",
    # location / contact
    "locations", "hours", "contact", "contact_form",
    # forms
    "booking_form", "application_form", "signup_form", "newsletter",
    # hiring
    "open_roles", "culture", "day_in_life",
}


# ── Structured-output schema (Gemini responseSchema) ─────────────────

_PROJECT_BRIEF_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "required": [
        "brand", "palette", "typography", "design_system",
        "pages", "ctas", "domain_keywords",
    ],
    "properties": {
        "brand": {
            "type": "OBJECT",
            "required": ["name", "tagline", "description", "domain"],
            "properties": {
                "name":        {"type": "STRING"},
                "tagline":     {"type": "STRING"},
                "description": {"type": "STRING"},
                "domain":      {"type": "STRING"},
                "business_info": {
                    "type": "OBJECT",
                    "properties": {
                        "address": {"type": "STRING"},
                        "phone":   {"type": "STRING"},
                        "email":   {"type": "STRING"},
                        "hours":   {"type": "STRING"},
                    },
                },
            },
        },
        "palette": {
            "type": "OBJECT",
            "required": [
                "primary", "secondary", "accent", "background",
                "foreground", "muted", "border", "card",
            ],
            "properties": {
                "primary":    {"type": "STRING"},
                "secondary":  {"type": "STRING"},
                "accent":     {"type": "STRING"},
                "background": {"type": "STRING"},
                "foreground": {"type": "STRING"},
                "muted":      {"type": "STRING"},
                "border":     {"type": "STRING"},
                "card":       {"type": "STRING"},
            },
        },
        "typography": {
            "type": "OBJECT",
            "required": ["heading_font", "body_font"],
            "properties": {
                "heading_font": {"type": "STRING"},
                "body_font":    {"type": "STRING"},
                "scale":        {"type": "STRING"},
            },
        },
        "motif": {"type": "STRING"},
        "design_system": {
            "type": "OBJECT",
            "required": ["motion", "accent_shape", "surface", "image_treatment", "section_rhythm"],
            "properties": {
                "motion":          {"type": "STRING"},
                "accent_shape":    {"type": "STRING"},
                "surface":         {"type": "STRING"},
                "image_treatment": {"type": "STRING"},
                "section_rhythm":  {"type": "STRING"},
            },
        },
        "header_archetype": {"type": "STRING"},
        "footer_archetype": {"type": "STRING"},
        "personality": {
            "type": "OBJECT",
            "properties": {
                "tone":          {"type": "STRING"},
                "vibe_keywords": {"type": "ARRAY", "items": {"type": "STRING"}},
                "energy":        {"type": "STRING"},
            },
        },
        # ── Pages (the multi-page-specific part) ─────────────────
        # One entry per page. The ORDER matters — pages[0] is the home
        # route ("/"); subsequent entries map to /<slug>. nav_label is
        # what appears in the header nav (≠ title, which appears in the
        # browser tab + page H1).
        "pages": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "required": ["slug", "title", "page_goal", "section_types"],
                "properties": {
                    "slug":          {"type": "STRING"},
                    "title":         {"type": "STRING"},
                    "nav_label":     {"type": "STRING"},
                    "page_goal":     {"type": "STRING"},
                    "primary_cta": {
                        "type": "OBJECT",
                        "properties": {
                            "label": {"type": "STRING"},
                            "href":  {"type": "STRING"},
                        },
                    },
                    "section_types": {
                        "type": "ARRAY",
                        "items": {"type": "STRING"},
                    },
                },
            },
        },
        # Project-level CTAs (shown in the global header). Per-page CTAs
        # live on the page entry above; these are the brand-level
        # conversion targets.
        "ctas": {
            "type": "OBJECT",
            "required": ["primary"],
            "properties": {
                "primary": {
                    "type": "OBJECT",
                    "required": ["label", "href"],
                    "properties": {
                        "label": {"type": "STRING"},
                        "href":  {"type": "STRING"},
                    },
                },
                "secondary": {
                    "type": "OBJECT",
                    "properties": {
                        "label": {"type": "STRING"},
                        "href":  {"type": "STRING"},
                    },
                },
            },
        },
        "domain_keywords": {
            "type": "ARRAY",
            "items": {"type": "STRING"},
        },
    },
}


# ── Distillation prompt ───────────────────────────────────────────────

_PROJECT_BRIEF_PROMPT = """You are briefing an engineer to build a MULTI-PAGE WEBSITE. Output a single Brief that every page in the site will share.

USER PROMPT: "{description}"
DOMAIN: {domain}
PRIMARY PURPOSE: {primary_purpose}
GEOGRAPHIC SCOPE: {geographic_scope} ({geographic_specifics})

Your job: produce a JSON Brief matching the response schema. Apply these rules:

1. BRAND. Invent a plausible brand name in proper Title Case (e.g. "Texas Logistics Solutions", "Brooklyn Smiles" — NOT "texas_logistics_solutions" or "brooklyn-smiles"). Add a one-line tagline and a 2-3 sentence description grounded in the prompt. Pick a domain (e.g. lower-cased single word + .com — "brooklynsmiles.com").

2. PALETTE. 8 colors as hex (#rrggbb). Must be cohesive — pick a primary that fits the category (warm earth tones for restaurants, navy/teal for B2B, etc.). Background/foreground/muted/border must read well together.

3. TYPOGRAPHY. heading_font and body_font as Google-Fonts-friendly names. Match the tone (serif for editorial/luxury, geometric sans for SaaS, rounded sans for friendly consumer).

4. DESIGN SYSTEM. Five fields, each one short phrase:
   • motion — fade-rise / parallax-subtle / static
   • accent_shape — soft-rounded / sharp / pill / brutalist-block
   • surface — flat / soft-shadow / heavy-card / glassmorphic
   • image_treatment — full-bleed-photo / illustration / desaturated / vignette
   • section_rhythm — alternating-bg / consistent-spacing / paneled

5. PAGES — the multi-page-specific deliverable.
   • Pick 3-6 pages based on the primary purpose:
     - lead_generation:  home, services, case_studies, about, contact
     - ecommerce:        home, shop, product (template), about, contact
     - hiring:           home, open_positions, life, benefits, apply
     - brand_awareness:  home, story, work, contact
     - booking:          home, services, book, about, contact
     - signup (saas):    home, features, pricing, docs, signup
   • pages[0] MUST be the home page with slug "" (empty = "/").
   • For each page emit:
       - slug:          URL-safe lowercase, no leading slash. Empty for home.
       - title:         Browser-tab + H1 title.
       - nav_label:     2-3 word nav button label.
       - page_goal:     ONE sentence describing what the visitor should
                        leave the page knowing or doing.
       - primary_cta:   {{label, href}} — the page's main conversion.
       - section_types: 4-7 section type tokens from the canonical set:
                        hero, value_prop, features, services, products,
                        menu, gallery, story, story_long, team, process,
                        how_it_works, testimonials, trust_signals, press,
                        stats, case_studies, pricing, comparison, faq,
                        locations, hours, contact, contact_form,
                        booking_form, application_form, signup_form,
                        newsletter, open_roles, culture, day_in_life,
                        cta, footer.
                        Order matters. The first MUST be "hero".
                        Footer is rendered globally and is NOT included
                        in section_types.
   • DO NOT repeat the same section_types list across all pages — each
     page's section list should reflect its own goal. Examples:
       home  → hero, value_prop, features, testimonials, cta
       about → hero, story_long, team, stats, cta
       contact → hero, contact_form, locations, hours

6. HEADER + FOOTER. Pick one of {headers} for header_archetype and one
   of {footers} for footer_archetype. Match the brand vibe.

7. CTAs (project level). Primary = the dominant conversion on the home
   page (e.g. "Get a Quote" → /contact, "Book a table" → /book). Secondary
   is optional.

8. DOMAIN KEYWORDS. 5-10 words/phrases that capture the niche — used
   downstream for image search and copy seeding.

OUTPUT: just the JSON object. No prose, no markdown fences.
"""


# ── Public entry point ───────────────────────────────────────────────

async def build_project_brief(
    *,
    description: str,
    classification: dict | None = None,
    intent: dict | None = None,
    websocket: Any = None,
    timeout_s: float = 60.0,
) -> dict[str, Any]:
    """Distill a multi-page Project Brief from prompt + intent.

    Returns a dict matching ``_PROJECT_BRIEF_SCHEMA`` with all required
    fields populated. Falls back to ``_fallback_brief`` on any failure
    so downstream stages never see None. Auth is handled inside
    ``gemini_post`` via Vertex ADC — callers do not pass keys.
    """
    classification = classification or {}
    intent = intent or {}
    description = (description or "").strip()
    domain = (classification.get("domain") or "general").strip()
    primary_purpose = (intent.get("primary_purpose") or "lead_generation").strip()
    geo_scope = (intent.get("geographic_scope") or "national").strip()
    geo_specifics = (intent.get("geographic_specifics") or "United States").strip()

    if websocket is not None:
        try:
            await websocket.send_json({
                "type": "progress",
                "message": "📐 Building project brief…",
            })
        except Exception:
            pass

    prompt = _PROJECT_BRIEF_PROMPT.format(
        description=description,
        domain=domain,
        primary_purpose=primary_purpose,
        geographic_scope=geo_scope,
        geographic_specifics=geo_specifics,
        headers=_HEADER_ARCHETYPES,
        footers=_FOOTER_ARCHETYPES,
    )

    raw = await _structured_call(prompt, timeout_s)
    if not raw:
        logger.warning("project_brief: Gemini returned empty — fallback")
        return _fallback_brief(description, domain, primary_purpose)

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("project_brief: JSON parse failed (%s) — fallback. Head: %s",
                       exc, raw[:200])
        return _fallback_brief(description, domain, primary_purpose)

    brief = _normalize_brief(parsed, description, domain, primary_purpose)
    logger.info(
        "project_brief: ok — brand=%r purpose=%s pages=%d sections=%d",
        brief["brand"]["name"], primary_purpose,
        len(brief["pages"]),
        sum(len(p["section_types"]) for p in brief["pages"]),
    )
    return brief


# ── Gemini call ──────────────────────────────────────────────────────

async def _structured_call(prompt: str, timeout_s: float) -> str:
    """Flash + JSON output, no tools."""
    from app.services.gemini_http import gemini_post

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.4,
            "maxOutputTokens": 4096,
            "responseMimeType": "application/json",
            "responseSchema": _PROJECT_BRIEF_SCHEMA,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }

    status, data, _ = await gemini_post(
        model=_BRIEF_MODEL,
        payload=payload,
        timeout_s=timeout_s,
        label="project_brief",
    )
    if status != 200 or data is None:
        return ""

    # Token billing — best-effort, mirror landing_intent's pattern.
    try:
        from app.services.billing_meter import report_token_usage
        u = data.get("usageMetadata") or {}
        _in = int(u.get("promptTokenCount", 0) or 0)
        _out = int(u.get("candidatesTokenCount", 0) or 0) + int(u.get("thoughtsTokenCount", 0) or 0)
        if _in or _out:
            report_token_usage(None, _in, _out, source="gemini_project_brief")
    except Exception:
        pass

    try:
        from knowledge.loader import safe_gemini_text
        text = safe_gemini_text(data).strip()
    except Exception:
        text = ""
        try:
            text = (
                ((data.get("candidates") or [{}])[0]).get("content", {}).get("parts", [{}])[0].get("text", "")
            ).strip()
        except Exception:
            text = ""
    return text


# ── Normalization & fallback ─────────────────────────────────────────

def _slugify(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")
    return s


def _normalize_page_entry(raw: dict | None, fallback: dict) -> dict:
    raw = raw or {}
    slug_in = (raw.get("slug") or "").strip().lstrip("/").lower()
    # Keep "" for home; otherwise URL-sanitize.
    slug = "" if slug_in in ("", "/", "home", "index") else _slugify(slug_in)

    title = (raw.get("title") or fallback.get("title") or slug.replace("-", " ").title() or "Home").strip()
    nav_label = (raw.get("nav_label") or title).strip()
    page_goal = (raw.get("page_goal") or fallback.get("page_goal") or "").strip()

    cta_raw = raw.get("primary_cta") or {}
    primary_cta = {
        "label": (cta_raw.get("label") or fallback.get("primary_cta", {}).get("label") or "Get Started").strip(),
        "href":  (cta_raw.get("href")  or fallback.get("primary_cta", {}).get("href")  or "/contact").strip(),
    }

    raw_types = raw.get("section_types") or []
    types: list[str] = []
    for t in raw_types:
        if not isinstance(t, str):
            continue
        token = t.strip().lower()
        if token in _VALID_SECTION_TYPES and token not in types:
            types.append(token)
    # First MUST be hero.
    if "hero" in types:
        types.remove("hero")
    types.insert(0, "hero")
    # Strip footer — rendered globally, not as a section.
    types = [t for t in types if t != "footer"]
    # Cap at 8 to avoid runaway model output.
    types = types[:8]
    # If model gave us nothing usable, fall back.
    if len(types) <= 1:
        types = list(fallback.get("section_types") or ["hero", "value_prop", "cta"])

    return {
        "slug":          slug,
        "title":         title,
        "nav_label":     nav_label,
        "page_goal":     page_goal,
        "primary_cta":   primary_cta,
        "section_types": types,
    }


def _normalize_brief(raw: dict, description: str, domain: str, primary_purpose: str) -> dict:
    """Coerce model output to the schema; fill missing fields from fallback."""
    fb = _fallback_brief(description, domain, primary_purpose)
    out: dict[str, Any] = {}

    # Brand
    brand_raw = raw.get("brand") or {}
    fb_brand = fb["brand"]
    out["brand"] = {
        "name":        (brand_raw.get("name") or fb_brand["name"]).strip(),
        "tagline":     (brand_raw.get("tagline") or fb_brand["tagline"]).strip(),
        "description": (brand_raw.get("description") or fb_brand["description"]).strip(),
        "domain":      (brand_raw.get("domain") or fb_brand["domain"]).strip(),
        "business_info": dict(brand_raw.get("business_info") or {}),
    }

    # Palette — required hex strings; fall back per-key.
    pal_raw = raw.get("palette") or {}
    pal_fb = fb["palette"]
    out["palette"] = {k: (pal_raw.get(k) or pal_fb[k]).strip() for k in pal_fb}

    # Typography
    typo_raw = raw.get("typography") or {}
    typo_fb = fb["typography"]
    out["typography"] = {
        "heading_font": (typo_raw.get("heading_font") or typo_fb["heading_font"]).strip(),
        "body_font":    (typo_raw.get("body_font")    or typo_fb["body_font"]).strip(),
        "scale":        (typo_raw.get("scale")        or typo_fb["scale"]).strip(),
    }

    # Design system
    ds_raw = raw.get("design_system") or {}
    ds_fb = fb["design_system"]
    out["design_system"] = {k: (ds_raw.get(k) or ds_fb[k]).strip() for k in ds_fb}

    out["motif"] = (raw.get("motif") or fb["motif"]).strip()

    header = (raw.get("header_archetype") or fb["header_archetype"]).strip().lower()
    out["header_archetype"] = header if header in _HEADER_ARCHETYPES else fb["header_archetype"]
    footer = (raw.get("footer_archetype") or fb["footer_archetype"]).strip().lower()
    out["footer_archetype"] = footer if footer in _FOOTER_ARCHETYPES else fb["footer_archetype"]

    # Personality (optional but useful downstream)
    pers_raw = raw.get("personality") or {}
    out["personality"] = {
        "tone":          (pers_raw.get("tone") or fb["personality"]["tone"]).strip(),
        "vibe_keywords": [k.strip() for k in (pers_raw.get("vibe_keywords") or []) if isinstance(k, str) and k.strip()][:6],
        "energy":        (pers_raw.get("energy") or "").strip(),
    }

    # Pages — the multi-page-specific part.
    raw_pages = raw.get("pages") or []
    fb_pages = fb["pages"]
    norm_pages: list[dict[str, Any]] = []
    seen_slugs: set[str] = set()
    for i, p in enumerate(raw_pages[:6]):
        if not isinstance(p, dict):
            continue
        fallback_for_page = fb_pages[i] if i < len(fb_pages) else fb_pages[-1]
        np = _normalize_page_entry(p, fallback_for_page)
        if np["slug"] in seen_slugs:
            continue
        seen_slugs.add(np["slug"])
        norm_pages.append(np)
    if not norm_pages:
        norm_pages = [dict(p) for p in fb_pages]
    # Force the first entry to be the home page (slug="").
    if norm_pages[0]["slug"] != "":
        # Prepend a synthesized home page.
        norm_pages.insert(0, dict(fb_pages[0]))
    out["pages"] = norm_pages

    # Project-level CTAs
    cta_raw = raw.get("ctas") or {}
    primary = cta_raw.get("primary") or {}
    out["ctas"] = {
        "primary": {
            "label": (primary.get("label") or fb["ctas"]["primary"]["label"]).strip(),
            "href":  (primary.get("href")  or fb["ctas"]["primary"]["href"]).strip(),
        },
    }
    secondary = cta_raw.get("secondary") or {}
    if secondary.get("label") and secondary.get("href"):
        out["ctas"]["secondary"] = {
            "label": secondary["label"].strip(),
            "href":  secondary["href"].strip(),
        }

    # Domain keywords
    kws = [k.strip() for k in (raw.get("domain_keywords") or []) if isinstance(k, str) and k.strip()]
    out["domain_keywords"] = kws[:10] if kws else fb["domain_keywords"]

    return out


# ── Fallback ─────────────────────────────────────────────────────────

# Per-purpose page templates. The pipeline must produce SOMETHING coherent
# even when Gemini fails entirely; these templates are the floor.
_FALLBACK_PAGES_BY_PURPOSE: dict[str, list[dict[str, Any]]] = {
    "lead_generation": [
        {"slug": "",            "title": "Home",          "nav_label": "Home",
         "page_goal": "Convince visitors we solve their problem and route them to a quote.",
         "primary_cta": {"label": "Get a Quote", "href": "/contact"},
         "section_types": ["hero", "value_prop", "services", "trust_signals", "case_studies", "cta"]},
        {"slug": "services",    "title": "Services",      "nav_label": "Services",
         "page_goal": "Detail what we do and outcomes per service line.",
         "primary_cta": {"label": "Get a Quote", "href": "/contact"},
         "section_types": ["hero", "services", "process", "case_studies", "cta"]},
        {"slug": "case-studies","title": "Case Studies",  "nav_label": "Work",
         "page_goal": "Prove track record with real client outcomes.",
         "primary_cta": {"label": "Talk to Sales", "href": "/contact"},
         "section_types": ["hero", "case_studies", "testimonials", "cta"]},
        {"slug": "about",       "title": "About",         "nav_label": "About",
         "page_goal": "Establish credibility through team + story.",
         "primary_cta": {"label": "Contact Us", "href": "/contact"},
         "section_types": ["hero", "story_long", "team", "stats", "cta"]},
        {"slug": "contact",     "title": "Contact",       "nav_label": "Contact",
         "page_goal": "Capture qualified leads with a quote form.",
         "primary_cta": {"label": "Send Message", "href": "#contact-form"},
         "section_types": ["hero", "contact_form", "locations", "hours"]},
    ],
    "ecommerce": [
        {"slug": "",         "title": "Home",     "nav_label": "Home",
         "page_goal": "Showcase featured products and route shoppers to the catalog.",
         "primary_cta": {"label": "Shop Now", "href": "/shop"},
         "section_types": ["hero", "products", "value_prop", "testimonials", "cta"]},
        {"slug": "shop",     "title": "Shop",     "nav_label": "Shop",
         "page_goal": "Browse the full product catalog.",
         "primary_cta": {"label": "View Cart", "href": "/cart"},
         "section_types": ["hero", "products", "trust_signals"]},
        {"slug": "about",    "title": "About",    "nav_label": "About",
         "page_goal": "Brand story + values for shoppers who care.",
         "primary_cta": {"label": "Shop Collection", "href": "/shop"},
         "section_types": ["hero", "story", "stats", "cta"]},
        {"slug": "contact",  "title": "Contact",  "nav_label": "Contact",
         "page_goal": "Customer support + retail locations.",
         "primary_cta": {"label": "Email Us", "href": "mailto:hello@example.com"},
         "section_types": ["hero", "contact_form", "locations", "hours"]},
    ],
    "hiring": [
        {"slug": "",                "title": "Careers",         "nav_label": "Home",
         "page_goal": "Convert candidates by leading with the opportunity, not the company.",
         "primary_cta": {"label": "Apply Now", "href": "/apply"},
         "section_types": ["hero", "value_prop", "open_roles", "stats", "testimonials", "cta"]},
        {"slug": "open-positions",  "title": "Open Positions",  "nav_label": "Positions",
         "page_goal": "List every open role with pay, requirements, schedule.",
         "primary_cta": {"label": "Apply Now", "href": "/apply"},
         "section_types": ["hero", "open_roles", "faq", "cta"]},
        {"slug": "life",            "title": "Life Here",       "nav_label": "Life",
         "page_goal": "Show what daily work feels like — equipment, schedule, team.",
         "primary_cta": {"label": "Apply Now", "href": "/apply"},
         "section_types": ["hero", "day_in_life", "culture", "testimonials", "cta"]},
        {"slug": "benefits",        "title": "Benefits",        "nav_label": "Benefits",
         "page_goal": "Detail compensation + benefits with real numbers.",
         "primary_cta": {"label": "Apply Now", "href": "/apply"},
         "section_types": ["hero", "value_prop", "stats", "faq", "cta"]},
        {"slug": "apply",           "title": "Apply",           "nav_label": "Apply",
         "page_goal": "Capture applications with a structured form.",
         "primary_cta": {"label": "Submit Application", "href": "#apply-form"},
         "section_types": ["hero", "application_form", "faq"]},
    ],
    "brand_awareness": [
        {"slug": "",         "title": "Home",     "nav_label": "Home",
         "page_goal": "Communicate who we are and what we make.",
         "primary_cta": {"label": "Explore Work", "href": "/work"},
         "section_types": ["hero", "story", "gallery", "press", "cta"]},
        {"slug": "story",    "title": "Story",    "nav_label": "Story",
         "page_goal": "Brand narrative + values + history.",
         "primary_cta": {"label": "See Our Work", "href": "/work"},
         "section_types": ["hero", "story_long", "team", "press"]},
        {"slug": "work",     "title": "Work",     "nav_label": "Work",
         "page_goal": "Portfolio of projects, products, or recent wins.",
         "primary_cta": {"label": "Get in Touch", "href": "/contact"},
         "section_types": ["hero", "gallery", "case_studies", "cta"]},
        {"slug": "contact",  "title": "Contact",  "nav_label": "Contact",
         "page_goal": "Open the door to inquiries.",
         "primary_cta": {"label": "Send Message", "href": "#contact-form"},
         "section_types": ["hero", "contact_form", "locations"]},
    ],
    "booking": [
        {"slug": "",         "title": "Home",      "nav_label": "Home",
         "page_goal": "Drive visitors to a booking with the offering at the top.",
         "primary_cta": {"label": "Book Now", "href": "/book"},
         "section_types": ["hero", "services", "testimonials", "trust_signals", "cta"]},
        {"slug": "services", "title": "Services",  "nav_label": "Services",
         "page_goal": "Describe each bookable service with duration + price.",
         "primary_cta": {"label": "Book a Service", "href": "/book"},
         "section_types": ["hero", "services", "faq", "cta"]},
        {"slug": "book",     "title": "Book",      "nav_label": "Book",
         "page_goal": "Capture bookings through an in-page form.",
         "primary_cta": {"label": "Confirm Booking", "href": "#booking-form"},
         "section_types": ["hero", "booking_form", "hours", "locations"]},
        {"slug": "about",    "title": "About",     "nav_label": "About",
         "page_goal": "Build trust with team + story + credentials.",
         "primary_cta": {"label": "Book Now", "href": "/book"},
         "section_types": ["hero", "story", "team", "press", "cta"]},
        {"slug": "contact",  "title": "Contact",   "nav_label": "Contact",
         "page_goal": "Hours, location, secondary contact channels.",
         "primary_cta": {"label": "Get Directions", "href": "/contact#map"},
         "section_types": ["hero", "locations", "hours", "contact_form"]},
    ],
    "signup": [
        {"slug": "",         "title": "Home",      "nav_label": "Home",
         "page_goal": "Convince visitors to sign up for the product.",
         "primary_cta": {"label": "Start Free", "href": "/signup"},
         "section_types": ["hero", "value_prop", "features", "testimonials", "cta"]},
        {"slug": "features", "title": "Features",  "nav_label": "Features",
         "page_goal": "Show every capability of the product.",
         "primary_cta": {"label": "Start Free", "href": "/signup"},
         "section_types": ["hero", "features", "comparison", "testimonials", "cta"]},
        {"slug": "pricing",  "title": "Pricing",   "nav_label": "Pricing",
         "page_goal": "Clear pricing tiers + what each unlocks.",
         "primary_cta": {"label": "Start Free", "href": "/signup"},
         "section_types": ["hero", "pricing", "faq", "cta"]},
        {"slug": "signup",   "title": "Sign Up",   "nav_label": "Sign Up",
         "page_goal": "Capture account signups.",
         "primary_cta": {"label": "Create Account", "href": "#signup-form"},
         "section_types": ["hero", "signup_form", "trust_signals"]},
    ],
}


def _fallback_brief(description: str, domain: str, primary_purpose: str) -> dict:
    """Minimal valid brief for when Gemini is unavailable.

    Picks a per-purpose page template + a neutral palette/typography. Every
    field downstream stages need is populated.
    """
    purpose = primary_purpose if primary_purpose in _FALLBACK_PAGES_BY_PURPOSE else "lead_generation"
    pages = [dict(p) for p in _FALLBACK_PAGES_BY_PURPOSE[purpose]]

    brand_name = (description.split()[0] if description else (domain or "Untitled")).title()[:40] or "Untitled"

    return {
        "brand": {
            "name":        brand_name,
            "tagline":     "Built for what's next",
            "description": (description or f"A {domain} business website.")[:240],
            "domain":      f"{_slugify(brand_name) or 'site'}.com",
            "business_info": {},
        },
        "palette": {
            "primary":    "#1f2937",
            "secondary":  "#475569",
            "accent":     "#dc5426",
            "background": "#ffffff",
            "foreground": "#0f172a",
            "muted":      "#f1f5f9",
            "border":     "#e2e8f0",
            "card":       "#ffffff",
        },
        "typography": {
            "heading_font": "Inter",
            "body_font":    "Inter",
            "scale":        "modular-1.25",
        },
        "motif": "modern editorial",
        "design_system": {
            "motion":          "fade-rise",
            "accent_shape":    "soft-rounded",
            "surface":         "soft-shadow",
            "image_treatment": "full-bleed-photo",
            "section_rhythm":  "consistent-spacing",
        },
        "header_archetype": "solid-bar",
        "footer_archetype": "minimalist-row",
        "personality": {
            "tone":          "friendly",
            "vibe_keywords": ["clear", "modern", "trustworthy"],
            "energy":        "confident",
        },
        "pages": pages,
        "ctas": {
            "primary": dict(pages[0]["primary_cta"]),
        },
        "domain_keywords": [domain or "general", purpose, "website"],
    }
