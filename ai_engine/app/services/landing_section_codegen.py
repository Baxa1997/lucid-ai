"""Parallel per-section codegen for landing pages.

Replaces Phase 1 + Phase 2 single-call codegen for the landing flow with
N parallel Claude calls, one per section. Each call generates exactly one
section component file that reads its copy / items / images from
`@/content/landing.json` (written upstream by landing_content.py).

Why parallel-per-section:
  • Faster wall-time (8 sections in ~30-60s vs 3-5 min serial)
  • Smaller per-call token budget → fewer truncations / hallucinations
  • Each call has tight context — only the section it's building
  • Cohesion comes from shared design tokens + 2 sibling specs passed as context

This module reuses the existing `call_claude_for_json` helper for retry,
billing, and streaming behavior — no new HTTP / model code.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)

# Per-section budget. Sections are small, focused components — even a complex
# pricing table fits in <8K tokens of JSX. 16K leaves room for thorough copy.
_SECTION_MAX_TOKENS = 16000


# ── Section anatomy: minimal fallback skeletons ──────────────────────
# We deliberately keep these THIN and CATEGORY-AGNOSTIC. The real per-section
# anatomy comes from `brief["visual_dna"]["section_anatomies"][section_type]`,
# which Gemini writes from grounded research. These fallbacks fire only when:
#   • visual_dna research failed entirely, OR
#   • Gemini didn't produce an anatomy for THIS specific section type.
# A fallback's job is to ship a structurally-correct section, not a beautiful
# one. Cohesion + culture come from visual_dna; pixel correctness comes from
# PROJECT_DESIGN_TOKENS + the global rules in the system prompt.
_FALLBACK_SKELETONS: dict[str, str] = {
    "hero": (
        "STRUCTURAL FLOOR — hero section minimum:\n"
        "  • Outer <section> is `relative isolate min-h-[600px] md:min-h-[720px] lg:min-h-[820px] overflow-hidden`.\n"
        "  • THREE stacked layers when bg media exists: media (z-0) → readability overlay (z-10) → content (z-20).\n"
        "  • Foreground content includes (in order): eyebrow tag → headline (text-5xl md:text-6xl lg:text-7xl, leading-[1.05]) → 1-line subhead (text-lg md:text-xl, max-w-xl) → ≥1 CTA + 0-1 secondary.\n"
        "  • Wrap content group in <Reveal variant=\"fade-up\">.\n"
        "  • COMPOSITION (left-aligned vs centered, full-bleed photo vs split, where the eyebrow/cta land) is driven by visual_dna.layout_signature + visual_dna.section_flavors.hero. Apply ≥2 motifs/textures from visual_dna in concrete accent positions."
    ),
    "menu": (
        "STRUCTURAL FLOOR — list-of-items section minimum:\n"
        "  • Heading group at top (eyebrow + h2 + 1-line subhead).\n"
        "  • Items rendered grouped by category (when item.label or item.category exists), or as a single grid otherwise.\n"
        "  • Each item shows: title (font-semibold), description (text-sm text-muted-foreground), price/value (when present, font-semibold text-primary).\n"
        "  • Photo-led grid when section.images[i] exists for items; editorial text-only rows when not.\n"
        "  • Cards in a row share aspect ratios + heights; mt-auto on price/CTA so footers align.\n"
        "  • Decorative elements (category dividers, bullet markers, frame ornaments) come from visual_dna.decorative_motifs."
    ),
    "gallery": (
        "STRUCTURAL FLOOR — image grid section minimum:\n"
        "  • Heading group above (eyebrow + h2 + subhead, max-w-2xl).\n"
        "  • Responsive grid: `grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3 md:gap-4` (or asymmetric variant — pick from visual_dna.layout_signature).\n"
        "  • Tiles use varied aspect ratios (aspect-[4/5] | aspect-square | aspect-[3/4]) for editorial rhythm — never uniform thumbnails.\n"
        "  • Each tile: `relative overflow-hidden` with photo as <Image fill object-cover> + hover scale.\n"
        "  • Section root needs `overflow-hidden` to contain decorative blobs."
    ),
    "testimonials": (
        "STRUCTURAL FLOOR — testimonial cards minimum:\n"
        "  • Heading group above.\n"
        "  • ≥3 quote cards (grid or carousel — pick from visual_dna.section_flavors.testimonials).\n"
        "  • Each card: optional star row (lucide Star, fill-primary), quote body (italic or display serif when quote is hero-level), attribution row (avatar circle with initials OR <Image>, name font-semibold, role/location text-sm muted).\n"
        "  • Cards in a row share heights via h-full + items-stretch.\n"
        "  • Decorative quote-mark glyph or culturally-resonant frame element from visual_dna.decorative_motifs."
    ),
    "features": (
        "STRUCTURAL FLOOR — capability/benefits section minimum:\n"
        "  • Heading group above (eyebrow + h2 + subhead).\n"
        "  • Render items in one of: 3-col icon grid | split-image-bullets | numbered-stepper | editorial-numbered-list | bento — pick from visual_dna.layout_signature + section_flavors.\n"
        "  • Each item: icon chip (h-10 w-10 or h-12 w-12, fixed) OR oversized numeral (when stepper/editorial), title (font-semibold), description (text-sm text-muted-foreground).\n"
        "  • Cards equalize via h-full + items-stretch; gap-4 md:gap-6 lg:gap-8.\n"
        "  • Lucide icons should match visual_dna.iconography_anchors when item.icon is available."
    ),
    "press": (
        "STRUCTURAL FLOOR — press / publications section minimum (NEVER overlapping cards):\n"
        "  • Heading group with eyebrow ('PRESS' or research-grounded label).\n"
        "  • FLAT logo strip — `grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-x-6 md:gap-x-10 gap-y-8 items-center`. Each cell renders the publication name as an uppercase wordmark (NOT an Unsplash image).\n"
        "  • Pull-quote block below — single editorial quote (text-xl md:text-2xl font-serif italic) + attribution. Carousel when 3+ quotes.\n"
        "  • NEVER absolute-positioned, rotated, or overlapping cards. Press is in-flow only.\n"
        "  • Section MUST be at least min-h-[480px] content-wise; fill all 3 blocks before any spacer."
    ),
    "story": (
        "STRUCTURAL FLOOR — narrative / about section minimum:\n"
        "  • Two-column split (asymmetric is fine — driven by visual_dna.layout_signature): copy column with eyebrow + h2 + multi-paragraph body + optional small CTA, image/accent column with hero photo OR a stat panel OR a quote pull-out.\n"
        "  • Body copy uses `text-base md:text-lg text-muted-foreground leading-relaxed`, max-w-prose for readability.\n"
        "  • Optional decorative element from visual_dna.decorative_motifs as a divider or accent."
    ),
    "process": (
        "STRUCTURAL FLOOR — process / how-it-works section minimum:\n"
        "  • Heading group above.\n"
        "  • Sequential steps (3-5 typical): horizontal stepper on lg+, vertical on mobile. Each step has numbered indicator + title + description.\n"
        "  • Optional connecting line behind the indicators (h-px bg-border, hidden on mobile).\n"
        "  • Numbered indicator style (circle, square, hand-drawn glyph) comes from visual_dna.decorative_motifs."
    ),
    "stats": (
        "STRUCTURAL FLOOR — big numbers band:\n"
        "  • 2-4 columns separated by `divide-x divide-border`.\n"
        "  • Each item: huge number (text-5xl sm:text-6xl font-bold text-primary, optional count-up on scroll), label below (uppercase tracking-widest text-muted-foreground).\n"
        "  • Optional small description per item (text-sm) when section.items[i].description exists."
    ),
    "faq": (
        "STRUCTURAL FLOOR — FAQ accordion:\n"
        "  • Heading group above (eyebrow + h2).\n"
        "  • Vertical accordion using <details>+<summary> OR useState. Each row: question (font-semibold), answer (text-muted-foreground) revealed on toggle.\n"
        "  • Plus icon rotates 45° on open. max-w-3xl mx-auto for readability."
    ),
    "pricing": (
        "STRUCTURAL FLOOR — pricing tiers:\n"
        "  • Heading group above + optional billing-period toggle (useState).\n"
        "  • 2-3 plan cards: each with plan name, big price, billing-period note, feature list (check icons), CTA.\n"
        "  • Highlight the recommended tier via `ring-2 ring-primary` + small 'Recommended' pill.\n"
        "  • Cards equalize heights; CTAs align via mt-auto."
    ),
    "cta": (
        "STRUCTURAL FLOOR — CTA band:\n"
        "  • Full-width band with `bg-primary text-primary-foreground`, generous padding (py-16 lg:py-24), centered or left-aligned.\n"
        "  • Giant headline (text-4xl md:text-5xl lg:text-6xl font-bold) + supporting line + primary CTA pill (`bg-background text-foreground`).\n"
        "  • Optional decorative motif from visual_dna in a corner accent position."
    ),
    "team": (
        "STRUCTURAL FLOOR — team grid:\n"
        "  • Heading group above.\n"
        "  • Grid of avatar cards (3-4 col): rounded portrait (rounded-full or rounded-2xl), name (font-semibold), role (text-sm muted), optional 1-line bio.\n"
        "  • Cards equalize heights, hover-lift."
    ),
    "locations": (
        "STRUCTURAL FLOOR — locations / addresses:\n"
        "  • Heading group above.\n"
        "  • Cards or rows per location: name (font-semibold), address, phone (tel:), hours table, optional map link.\n"
        "  • Optional embedded map or photo per location."
    ),
    "reservation": (
        "STRUCTURAL FLOOR — booking / reservation form:\n"
        "  • Two-column layout: form (left or right), info panel with brand business_info (address/phone/hours).\n"
        "  • Form fields: name, email, phone, date (input type=date), party-size or quantity (input type=number), notes textarea, submit.\n"
        "  • Real validation (required + email regex) + success state on submit. Mark file 'use client'."
    ),
    "contact": (
        "STRUCTURAL FLOOR — contact form section:\n"
        "  • Two-column: form (name, email, message textarea, submit) + info panel (address, phone, email, hours).\n"
        "  • Real validation + success state."
    ),
    "newsletter": (
        "STRUCTURAL FLOOR — newsletter signup:\n"
        "  • Centered band: heading + 1-line description + inline email input + submit button.\n"
        "  • Real email validation + success state. Optional GDPR/privacy line below."
    ),
    # NOTE: "footer" intentionally NOT listed here. Footer fallback is
    # selected dynamically from _FOOTER_VARIANTS by `_pick_footer_variant`
    # so the shape varies with brand personality / category rather than
    # collapsing every project into the same minimalist row.
}


# ── Header fallback variants ─────────────────────────────────────────
# Same idea as footer variants: when Gemini omits visual_dna.header, do
# not collapse every project into the same sticky 3-part bar.
_HEADER_VARIANTS: dict[str, str] = {
    "solid-bar": (
        "STRUCTURAL FLOOR — site header (solid sticky bar):\n"
        "  • <header> is `sticky top-0 z-50 w-full border-b border-border bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/80`.\n"
        "  • Inner: `container mx-auto flex h-16 items-center justify-between px-4 sm:px-6 lg:px-8` — brand left, nav center (gap-8 text-sm, max 6 links), CTA right.\n"
        "  • Mobile drawer below the bar when hamburger toggled. ESC closes; click outside closes.\n"
        "  • Best for straightforward professional brands."
    ),
    "utility-split": (
        "STRUCTURAL FLOOR — site header (utility row + main nav):\n"
        "  • TWO rows on desktop. Top utility row: `h-9 border-b border-border bg-muted/40 text-xs` showing 1-2 business_info items (email/phone/location) from landing.brand.business_info and social links when present.\n"
        "  • Main row: `h-16 bg-background/95 backdrop-blur` with brand left, nav center, CTA right.\n"
        "  • Mobile collapses to one sticky row with brand, CTA, hamburger; utility details move inside the drawer.\n"
        "  • Best for schools, academies, clinics, service businesses, and local venues where contact/location matter."
    ),
    "centered-logo": (
        "STRUCTURAL FLOOR — site header (centered-logo editorial):\n"
        "  • Desktop row uses left nav group, centered wordmark, right nav/CTA group. Height `h-20`; background `bg-background/90 backdrop-blur` with a soft border.\n"
        "  • Wordmark uses heading font and one restrained motif accent. Nav is uppercase, small, evenly spaced.\n"
        "  • Mobile collapses to brand left + hamburger right.\n"
        "  • Best for refined, elegant, boutique, and ceremonial brands."
    ),
    "floating-pill": (
        "STRUCTURAL FLOOR — site header (floating pill):\n"
        "  • <header> is `fixed top-4 inset-x-0 z-50 px-4`. Inner shell is `container mx-auto flex h-14 items-center justify-between rounded-full border border-border bg-background/85 px-4 shadow-lg backdrop-blur-md`.\n"
        "  • Brand left, compact nav center, CTA right as solid primary pill. On scroll, increase opacity and shadow.\n"
        "  • Mobile drawer opens as a rounded panel below the floating shell.\n"
        "  • Best for high-energy, SaaS, creator, event, and conversion-heavy landing pages."
    ),
}


# ── Footer fallback variants ─────────────────────────────────────────
# When `visual_dna.section_anatomies.footer` is missing, we pick ONE of
# these patterns based on brand personality + category. This prevents the
# same 4-column megacolumn footer appearing on every generation. Each
# entry is a structural floor — visual_dna composes the actual look on
# top (decorative motifs, surface treatment, typography flavor).
_FOOTER_VARIANTS: dict[str, str] = {
    "minimalist-row": (
        "STRUCTURAL FLOOR — site footer (minimalist single row):\n"
        "  • `border-t border-border bg-background`.\n"
        "  • Inner: `container mx-auto flex flex-col gap-4 px-6 py-8 sm:flex-row sm:items-center sm:justify-between`.\n"
        "  • Left: brand monogram + © year. Center (sm+): inline links from landing.footer.links (text-xs uppercase tracking-widest). Right: 3-4 social icons.\n"
        "  • Optional single accent motif from visual_dna.decorative_motifs as the only flourish.\n"
        "  • NO multi-column grid; NO newsletter form; NO contact column. This footer says LESS on purpose."
    ),
    "mega-columns": (
        "STRUCTURAL FLOOR — site footer (4-column mega-columns):\n"
        "  • `border-t border-border bg-background` OR `bg-foreground text-background` if visual_dna.cultural_palette_emphasis suggests dark surface.\n"
        "  • Inner: `container mx-auto grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-10 px-6 py-14`.\n"
        "  • Column 1: brand mark + tagline + short description from landing.brand. 2-3 social icons below.\n"
        "  • Column 2: 'Explore' (or category-specific label) — links from landing.footer.links (text-sm).\n"
        "  • Column 3: 'Visit' — business_info: address (with MapPin icon), phone (tel:), email (mailto:), hours.\n"
        "  • Column 4: newsletter signup OR a single editorial pull-quote/tagline.\n"
        "  • Bottom strip: `border-t border-border/50 mt-12 pt-6 flex flex-col sm:flex-row items-center justify-between text-xs text-muted-foreground`. © year + brand on left; small links (Privacy / Terms) on right."
    ),
    "cta-band": (
        "STRUCTURAL FLOOR — site footer (CTA band + thin footer bar):\n"
        "  • TWO bands stacked.\n"
        "  • UPPER BAND: full-width `bg-primary text-primary-foreground` panel, py-16 lg:py-20. Centered: oversized invitation headline (text-3xl md:text-5xl font-bold, max-w-3xl), short supporting line, ONE primary CTA pill (use landing.ctas.primary). Optional decorative motif from visual_dna in a corner.\n"
        "  • LOWER BAND: thin minimalist bar `bg-background border-t border-border py-6`. Inner: `container mx-auto flex flex-col gap-3 px-6 sm:flex-row sm:items-center sm:justify-between`. Brand + © year left, social icon row right, small Privacy/Terms links if relevant.\n"
        "  • Suited to brands with assertive/high-energy personality — the footer is a final pitch, not a directory."
    ),
    "centered-stack": (
        "STRUCTURAL FLOOR — site footer (centered stack):\n"
        "  • `border-t border-border bg-background` (or a soft cream/muted surface if visual_dna calls for it).\n"
        "  • Inner: `container mx-auto flex flex-col items-center gap-6 px-6 py-16 text-center`.\n"
        "  • TOP: large brand mark / wordmark (font-serif text-3xl md:text-4xl, can carry a decorative motif from visual_dna as ornament above/below).\n"
        "  • MIDDLE: single line of inline nav links (gap-6 text-sm tracking-widest uppercase), then optional contact line (city · phone · email), then a row of 4-5 social icons.\n"
        "  • BOTTOM: small © year + tagline.\n"
        "  • Suited to refined/minimal/sophisticated personalities — a quiet, ceremonial close."
    ),
}


def _pick_footer_variant(brief: dict | None) -> str:
    """Deterministically pick a footer skeleton key based on brand context.

    The pick is stable per-brand (same brief → same variant) and skewed
    toward the variant that best fits the brand's personality + category.
    Falls back to minimalist-row when context is missing.
    """
    b = brief or {}
    pers = (b.get("personality") or {})
    energy = (pers.get("energy") or "").strip().lower()
    tone = (pers.get("tone") or "").strip().lower()
    vibe = " ".join(pers.get("vibe_keywords") or []).lower()
    category = (b.get("category") or "").strip().lower()
    purpose = ((b.get("_research") or {}).get("primary_purpose") or "").lower()
    brand = b.get("brand") or {}
    info = brand.get("business_info") or {}
    social = brand.get("social") or []
    sections = b.get("sections") or []
    info_count = sum(1 for k in ("address", "phone", "email", "hours", "city") if info.get(k))
    navish_section_count = len([
        s for s in sections
        if (s.get("type") or "").lower() not in {"hero", "footer", "cta", "cta_band", "newsletter"}
    ])

    # Heuristics — order matters; first match wins.
    # cta-band: assertive, high-energy, conversion-driven brands
    if energy == "high" or any(w in vibe for w in ("bold", "assertive", "energetic", "playful")) \
       or purpose in ("lead-gen", "signup", "convert"):
        return "cta-band"

    # centered-stack: refined / sophisticated / ceremonial brands
    if any(w in tone for w in ("refined", "elegant", "sophisticated", "minimal")) \
       or any(w in vibe for w in ("refined", "elegant", "minimal", "sophisticated", "quiet", "understated")):
        return "centered-stack"

    # mega-columns: content-heavy or contact-heavy brands. Lots of info to
    # surface in the footer. This intentionally catches education/language/
    # tutoring sites like LinguistFlow — otherwise they collapse to the same
    # minimalist row despite having address, phone, email, social, and many nav
    # targets.
    if (
        category in (
            "restaurant", "hotel", "resort", "hospitality", "spa", "retail",
            "ecommerce", "marketplace", "agency", "studio", "education",
            "school", "academy", "language", "tutoring", "course", "coaching",
            "clinic", "healthcare", "fitness", "wellness", "real estate",
            "nonprofit", "community",
        )
        or info_count >= 2
        or bool(social)
        or navish_section_count >= 5
    ):
        return "mega-columns"

    # minimalist-row: low-energy or info-light brands; safe default
    return "minimalist-row"


def _pick_header_variant(brief: dict | None) -> str:
    """Deterministically pick a fallback header skeleton by brand context."""
    b = brief or {}
    pers = (b.get("personality") or {})
    energy = (pers.get("energy") or "").strip().lower()
    tone = (pers.get("tone") or "").strip().lower()
    vibe = " ".join(pers.get("vibe_keywords") or []).lower()
    category = (b.get("category") or "").strip().lower()
    brand = b.get("brand") or {}
    info = brand.get("business_info") or {}
    info_count = sum(1 for k in ("address", "phone", "email", "hours", "city") if info.get(k))

    if energy == "high" or any(w in vibe for w in ("bold", "energetic", "playful", "launch")):
        return "floating-pill"
    if any(w in tone for w in ("refined", "elegant", "sophisticated", "minimal")) \
       or any(w in vibe for w in ("refined", "elegant", "minimal", "sophisticated", "quiet")):
        return "centered-logo"
    if category in (
        "education", "school", "academy", "language", "tutoring", "course",
        "coaching", "clinic", "healthcare", "real estate", "nonprofit",
    ) or info_count >= 2:
        return "utility-split"
    return "solid-bar"

# Aliases — section types that map to the same fallback. Aliases live here
# rather than in _FALLBACK_SKELETONS so the canonical list reads cleanly.
_TYPE_ALIASES: dict[str, str] = {
    "value_prop":       "features",
    "benefits":         "features",
    "how_it_works":     "process",
    "steps":            "process",
    "experience":       "gallery",
    "experiences":      "gallery",
    "events":           "gallery",
    "publications":     "press",
    "logos":            "press",
    "awards":           "press",
    "philosophy":       "story",
    "about":            "story",
    "marketing_header": "header",
    "navbar":           "header",
    "marketing_footer": "footer",
    "site_footer":      "footer",
    "booking_form":     "reservation",
    "contact_form":     "contact",
    "menu_highlights":  "menu",
    "featured_dishes":  "menu",
    "destinations":     "gallery",
    "rooms":            "gallery",
    "accommodations":   "gallery",
    "products":         "menu",
    "portfolio":        "gallery",
    "instagram_feed":   "gallery",
}


_GENERIC_FALLBACK = (
    "STRUCTURAL FLOOR — generic content section minimum:\n"
    "  • Heading group at top (eyebrow + h2 + optional 1-line subhead).\n"
    "  • Primary content block: list of items, grid, or single narrative paragraph — pick what fits visual_dna.layout_signature.\n"
    "  • At least one supporting block (CTA row, micro-stat triple, attribution row, info card) so the section feels complete.\n"
    "  • Decorative integration from visual_dna.decorative_motifs in accent positions only."
)


def _canonical_section_type(section_type: str) -> str:
    """Resolve a section type to its canonical key in _FALLBACK_SKELETONS."""
    t = (section_type or "").strip().lower()
    return _TYPE_ALIASES.get(t, t)


def _resolve_anatomy(
    section_type: str,
    visual_dna: dict | None,
    *,
    brief: dict | None = None,
) -> tuple[str, str]:
    """Return (anatomy_text, source) for a section.

    Priority:
      1. visual_dna.section_anatomies[section_type]   — research-grounded
      2. visual_dna.section_anatomies[canonical_type] — research via alias
      3a. footer: dynamic pick from _FOOTER_VARIANTS  — varies by brand
      3b. _FALLBACK_SKELETONS[canonical_type]         — minimal floor
      4. _GENERIC_FALLBACK                            — last resort

    Source is one of: "research", "research-aliased", "fallback",
    "fallback:<variant>" (footer only), or "generic".

    `brief` is optional and only consulted for variants that need brand
    context (currently the footer picker).
    """
    raw_type = (section_type or "").strip().lower()
    canonical = _canonical_section_type(raw_type)
    anatomies = ((visual_dna or {}).get("section_anatomies") or {})

    custom = anatomies.get(raw_type)
    if isinstance(custom, str) and len(custom.strip()) >= 40:
        return custom.strip(), "research"

    custom_alias = anatomies.get(canonical)
    if isinstance(custom_alias, str) and len(custom_alias.strip()) >= 40:
        return custom_alias.strip(), "research-aliased"

    if canonical == "header":
        variant = _pick_header_variant(brief)
        return _HEADER_VARIANTS[variant], f"fallback:{variant}"

    if canonical == "footer":
        variant = _pick_footer_variant(brief)
        return _FOOTER_VARIANTS[variant], f"fallback:{variant}"

    fallback = _FALLBACK_SKELETONS.get(canonical)
    if fallback:
        return fallback, "fallback"

    return _GENERIC_FALLBACK, "generic"


def _section_filename(section: dict[str, Any]) -> str:
    """`HeroSection.jsx` from {id: 'hero', type: 'hero'}.

    Uses the section's unique `id` (deduped upstream by _normalize_brief) so
    two sections with the same `type` (e.g. two "features" blocks) don't
    collide on the same filename. Falls back to type then "section".
    """
    raw = (section.get("id") or section.get("type") or "section").strip()
    parts = re.split(r"[\s_\-]+", raw)
    pascal = "".join(p.capitalize() for p in parts if p)
    if not pascal:
        pascal = "Section"
    if not pascal.endswith("Section"):
        pascal = f"{pascal}Section"
    return f"{pascal}.jsx"


def _component_name(filename: str) -> str:
    """Strip extension from filename to get React component name."""
    return filename.rsplit(".", 1)[0]


def _section_content_looks_valid(content: str, section_id: str, component_name: str) -> tuple[bool, str]:
    """Fast contract check before writing Claude output to disk.

    The build validator catches syntax errors later; this catches the more
    damaging class of "valid JSX that ignores the pipeline contract" before it
    ships: hardcoded copy, wrong section id, or no runtime landing.json import.
    """
    if not content.strip():
        return False, "empty content"
    if "@/content/landing.json" not in content:
        return False, "does not import runtime landing.json"
    if "landing.sections" not in content or ".find" not in content:
        return False, "does not select section from landing.sections"
    if section_id and section_id not in content:
        return False, "does not reference requested section id"
    if f"export default function {component_name}" not in content:
        return False, "missing matching default export"
    if 'href="#"' in content or "href='#'" in content:
        return False, "contains placeholder href"
    if "style={{ fontFamily" in content or "fontFamily:" in content:
        return False, "uses inline fontFamily"
    return True, "ok"


def _layout_content_looks_valid(content: str, kind: str, component_name: str) -> tuple[bool, str]:
    """Validate header/footer output still reads all runtime data from JSON."""
    if not content.strip():
        return False, "empty content"
    if "@/content/landing.json" not in content:
        return False, "does not import runtime landing.json"
    if f"export default function {component_name}" not in content:
        return False, "missing matching default export"
    if 'href="#"' in content or "href='#'" in content:
        return False, "contains placeholder href"
    if "style={{ fontFamily" in content or "fontFamily:" in content:
        return False, "uses inline fontFamily"
    if kind == "header" and "landing.nav" not in content:
        return False, "header does not read landing.nav"
    if kind == "footer" and "landing.footer" not in content:
        return False, "footer does not read landing.footer"
    if "landing.brand" not in content:
        return False, "does not read landing.brand"
    return True, "ok"


def _fallback_section_component(section: dict[str, Any], component_name: str, file_path: str) -> dict[str, Any]:
    """Deterministic, data-driven section fallback.

    Used only when Claude fails or violates the runtime JSON contract. The
    fallback keeps every nav anchor live, renders the brief's real content, and
    includes functional forms for form-like sections so the generated app stays
    usable instead of silently dropping a page segment.
    """
    section_id = (section.get("id") or section.get("type") or "section").strip()
    section_type = (section.get("type") or "").strip().lower()
    needs_form = section_type in {"contact", "contact_form", "reservation", "booking_form", "newsletter"}
    form_jsx = """
      <form onSubmit={handleSubmit} className="mt-8 grid gap-4 rounded-2xl border border-border bg-background p-5 shadow-sm">
        <div className="grid gap-4 sm:grid-cols-2">
          <label className="grid gap-2 text-sm font-medium text-foreground">
            Name
            <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required className="rounded-md border border-border bg-background px-3 py-2 outline-none ring-primary/20 focus:ring-4" />
          </label>
          <label className="grid gap-2 text-sm font-medium text-foreground">
            Email
            <input type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} required className="rounded-md border border-border bg-background px-3 py-2 outline-none ring-primary/20 focus:ring-4" />
          </label>
        </div>
        <label className="grid gap-2 text-sm font-medium text-foreground">
          Message
          <textarea value={form.message} onChange={(e) => setForm({ ...form, message: e.target.value })} rows={4} className="rounded-md border border-border bg-background px-3 py-2 outline-none ring-primary/20 focus:ring-4" />
        </label>
        <button type="submit" className="inline-flex items-center justify-center rounded-full bg-primary px-6 py-3 text-sm font-semibold text-primary-foreground transition hover:opacity-90">
          {submitted ? "Sent" : (section.cta?.label || landing.ctas?.primary?.label || "Send")}
        </button>
      </form>""" if needs_form else ""

    content = f'''{"'use client';" if needs_form else ""}
{"import { useState } from \"react\";" if needs_form else ""}
import Image from "next/image";
import Link from "next/link";
import landing from "@/content/landing.json";

export default function {component_name}() {{
  const section = landing.sections.find((s) => s.id === "{section_id}");
  {"const [form, setForm] = useState({ name: \"\", email: \"\", message: \"\" });" if needs_form else ""}
  {"const [submitted, setSubmitted] = useState(false);" if needs_form else ""}
  {"const handleSubmit = (event) => { event.preventDefault(); setSubmitted(true); };" if needs_form else ""}

  if (!section) return null;

  const items = section.items || [];
  const image = section.images?.find(Boolean);
  const cta = section.cta || landing.ctas?.primary;

  return (
    <section id="{section_id}" className="bg-background py-20 md:py-28 lg:py-32">
      <div className="container mx-auto grid max-w-7xl gap-10 px-4 sm:px-6 lg:grid-cols-[0.95fr_1.05fr] lg:items-center lg:px-8">
        <div className="max-w-2xl">
          {{section.nav_label && (
            <p className="mb-3 text-xs font-semibold uppercase tracking-[0.2em] text-primary">{{section.nav_label}}</p>
          )}}
          <h2 className="font-[family-name:var(--font-heading)] text-3xl font-bold tracking-tight text-foreground sm:text-4xl lg:text-5xl">
            {{section.headline}}
          </h2>
          {{section.subheadline && (
            <p className="mt-4 text-lg leading-relaxed text-muted-foreground md:text-xl">{{section.subheadline}}</p>
          )}}
          {{section.body && (
            <p className="mt-5 text-base leading-relaxed text-muted-foreground md:text-lg">{{section.body}}</p>
          )}}
          {{cta?.href && (
            <Link href={{cta.href}} className="mt-8 inline-flex items-center justify-center rounded-full bg-primary px-6 py-3 text-sm font-semibold text-primary-foreground transition hover:opacity-90">
              {{cta.label || "Explore"}}
            </Link>
          )}}
          {form_jsx}
        </div>
        <div className="grid gap-4">
          {{image ? (
            <div className="relative aspect-[4/3] overflow-hidden rounded-3xl border border-border bg-muted shadow-lg">
              <Image src={{image}} alt={{section.image_alts?.[0] || section.headline || landing.brand.name}} fill sizes="(min-width: 1024px) 48vw, 100vw" className="object-cover" />
              <div className="absolute inset-0 bg-gradient-to-t from-foreground/35 to-transparent" />
            </div>
          ) : (
            <div className="relative aspect-[4/3] overflow-hidden rounded-3xl border border-border bg-gradient-to-br from-primary/15 via-accent/10 to-muted p-8">
              <div className="absolute -right-10 -top-10 h-40 w-40 rounded-full bg-primary/10" />
              <p className="relative max-w-sm font-[family-name:var(--font-heading)] text-4xl font-bold text-foreground">{{landing.brand.name}}</p>
            </div>
          )}}
          {{items.length > 0 && (
            <div className="grid gap-4 sm:grid-cols-2">
              {{items.slice(0, 4).map((item, index) => (
                <article key={{item.title || item.label || index}} className="h-full rounded-2xl border border-border bg-card p-5 shadow-sm">
                  <p className="text-sm font-semibold uppercase tracking-[0.16em] text-primary">{{item.label || item.value || `0${{index + 1}}`}}</p>
                  <h3 className="mt-3 text-lg font-semibold text-foreground">{{item.title || item.name}}</h3>
                  {{item.description && <p className="mt-2 text-sm leading-relaxed text-muted-foreground">{{item.description}}</p>}}
                </article>
              ))}}
            </div>
          )}}
        </div>
      </div>
    </section>
  );
}}
'''
    return {"path": file_path, "content": content, "fallback": True}


def _system_prompt(
    brand_name: str,
    motif: str,
    palette: dict,
    typography: dict,
    design_system: dict,
    *,
    personality: dict | None = None,
    references: list[dict] | None = None,
    design_tokens: dict | None = None,
    visual_dna: dict | None = None,
) -> str:
    """Per-call system prompt — small, focused, no rules unrelated to a single section."""
    palette_lines = "\n".join(f"  --{k}: {v};" for k, v in palette.items())
    ds = design_system or {}
    dt = design_tokens or {}
    pers = personality or {}
    vd = visual_dna or {}
    pers_block = ""
    if pers:
        vibe = ", ".join(pers.get("vibe_keywords") or [])
        pers_block = (
            f"\nPERSONALITY (tune copy tone, color usage, and motion intensity to match):\n"
            f"  Tone: {pers.get('tone', 'confident')}\n"
            f"  Vibe keywords: {vibe or 'modern, clear'}\n"
            f"  Energy: {pers.get('energy', 'medium')}\n"
        )

    # ── VISUAL DNA — the lead design directive ─────────────────────────
    # Rich, concrete cultural cues distilled from grounded design research.
    # When present, these are the PRIMARY visual instruction — the abstract
    # motion/accent_shape/surface enums below are still authoritative for
    # tokens (radius, padding, transition) but the LOOK and FEEL of the
    # section is driven by visual_dna. When absent (research failed), the
    # codegen falls back to the legacy enums-only path and produces the
    # generic "modern luxe" output.
    visual_dna_block = ""
    if vd:
        intensity = (vd.get("cultural_intensity") or "bold").strip().lower()
        motifs = vd.get("decorative_motifs") or []
        textures = vd.get("signature_textures") or []
        icons = vd.get("iconography_anchors") or []
        photo = (vd.get("photography_style") or "").strip()
        layout_sig = (vd.get("layout_signature") or "").strip()
        palette_emph = (vd.get("cultural_palette_emphasis") or "").strip()
        type_voice = (vd.get("typography_voice") or "").strip()
        flavors = vd.get("section_flavors") or {}

        intensity_note = (
            "Cultural cues sit in ACCENT POSITIONS only (a small motif under headlines, "
            "an iconography anchor next to CTAs, a single textural detail). The page reads "
            "modern-upscale with cultural FLAVOR — restraint stays."
            if intensity == "subtle"
            else "Cultural cues take a STRONGER role (larger textures over hero photos, "
                 "decorative motifs as section dividers, iconography woven into headings, "
                 "full-bleed cultural patterns where appropriate). The page reads UNMISTAKABLY "
                 "of this category and culture."
        )

        parts = [
            "\nVISUAL DNA — THIS IS THE PRIMARY VISUAL DIRECTIVE FOR THIS BRAND.",
            "Concrete, culturally-specific visual cues distilled from real grounded research.",
            "Your section MUST visibly reflect VISUAL_DNA in ≥3 distinct ways (color emphasis, "
            "decorative motif, iconography accent, photography style, typography voice, OR layout "
            "signature). A generic 'modern sans + photos + 3-col grid' output that could fit any "
            "brand in this category is a FAILURE.",
            f"\nCultural intensity: {intensity}",
            f"  → {intensity_note}",
        ]
        if palette_emph:
            parts.append(f"\nCultural palette emphasis:\n  {palette_emph}")
        if type_voice:
            parts.append(f"\nTypography voice:\n  {type_voice}")
        if photo:
            parts.append(f"\nPhotography style:\n  {photo}")
        if layout_sig:
            parts.append(f"\nLayout signature:\n  {layout_sig}")
        if motifs:
            parts.append("\nDecorative motifs (USE these as accents — pick ones that fit this section):")
            for m in motifs[:6]:
                parts.append(f"  • {m}")
        if textures:
            parts.append("\nSignature textures (use AT MOST one per section — don't stack):")
            for t in textures[:3]:
                parts.append(f"  • {t}")
        if icons:
            parts.append(
                "\nIconography anchors (when section calls for icons, prefer Lucide icons that "
                "evoke these — not generic Zap/Star/Check; pick lucide names that match these ideas):"
            )
            for ic in icons[:6]:
                parts.append(f"  • {ic}")
        if flavors:
            parts.append("\nPer-section visual flavor (apply when generating that section type):")
            for stype, note in flavors.items():
                if note and isinstance(note, str):
                    parts.append(f"  • {stype}: {note}")
        parts.append(
            "\nAPPLY VISUAL DNA HOLISTICALLY — don't slap a single decorative motif on a generic "
            "skeleton and call it done. The cumulative effect of color emphasis + iconography + "
            "photography + layout signature should make this brand feel UNMISTAKABLY of its "
            "category and culture, not Stripe-with-a-different-logo."
        )
        visual_dna_block = "\n".join(parts) + "\n"

    ref_block = ""
    if references:
        ref_lines = []
        for r in references[:4]:
            name = r.get("name") or r.get("url", "")
            why = r.get("why") or ""
            features = ", ".join((r.get("notable_features") or [])[:3])
            ref_lines.append(f"  • {name} — {why}{(' Features: ' + features) if features else ''}")
        if ref_lines:
            ref_block = (
                "\nREFERENCE SITES (research-grounded — these REAL sites informed this brief; emulate their patterns):\n"
                + "\n".join(ref_lines) + "\n"
            )

    return f"""You are a senior front-end engineer writing ONE attention-grabbing section component for a Next.js landing page. The bar is: real motion, real images, real visual interest, real INTERACTIVITY — never a static text block.

OUTPUT — ONE file via the write_project_files tool:
  • path: src/components/sections/<ComponentName>.jsx (provided in the user message)
  • content: the full source for that component, ready to import

MANDATORY RULES
0. STRING LITERALS — when you write English copy as a JS string (object property, array element,
   variable initializer), use **double quotes** ALWAYS. English copy is full of apostrophes
   ("chef's", "evening's", "don't") which silently terminate single-quoted literals and break
   `next build` with "Unexpected token". Examples:
     ✗ detail: 'Reserved exclusively for the chef's tasting menu.'
     ✓ detail: "Reserved exclusively for the chef's tasting menu."
   The ONLY single-quoted strings allowed are: `'use client'`, import paths (`from '@/...'`),
   and CSS-class strings inside className (where there are no apostrophes). Default to `"..."` for
   all human copy, no exceptions.
1. The component reads its content from `@/content/landing.json` — never hardcode copy.
   Pattern:
     import landing from "@/content/landing.json";
     const section = landing.sections.find((s) => s.id === "<section-id>");
     // then render section.headline, section.subheadline, section.items, section.cta, section.images, etc.
1.5 VISUAL DNA IS THE LEAD DESIGN DIRECTIVE. The DESIGN CONTEXT block below carries a VISUAL DNA
    section with concrete, culturally-specific cues (decorative motifs, signature textures,
    iconography anchors, photography style, layout signature, palette emphasis, typography
    voice, per-section flavors). YOUR SECTION MUST VISIBLY REFLECT VISUAL_DNA IN ≥3 DISTINCT
    WAYS. Examples of "visibly reflecting":
      • Adopt the cultural_palette_emphasis on a hero gradient or accent surface.
      • Use one decorative_motif as a divider, eyebrow ornament, or hero accent.
      • Pick lucide-react icons that match an iconography_anchor (lantern → 'Lamp', tea cup
        → 'Coffee', olive branch → 'Leaf') instead of generic Zap/Star.
      • Apply the photography_style mood as the photo subject + lighting choice in alt text
        and image_treatment intent.
      • Write headings in the typography_voice register (calligraphy-like serif for Chinese,
        rustic warm serif for Italian, etc.) — match the heading_font's cultural grain.
      • Echo the layout_signature in the section's overall composition.
    A section that could fit any business in this category — generic SaaS-flavored skeleton
    with the brand name swapped in — is a FAILURE. The cumulative cultural specificity should
    make this brand feel UNMISTAKABLY of its category and culture.
    If VISUAL DNA is not present in DESIGN CONTEXT below, fall back to the abstract enums
    (motif / accent_shape / surface) — but keep the same goal: avoid generic SaaS output.
2. Use TAILWIND CLASSES for styling — never inline `style={{...}}` for colors, fonts, or spacing.
   For FONTS specifically: NEVER write `style={{ fontFamily: ... }}`. Headlines / display
   text use `font-[family-name:var(--font-heading)]` (Tailwind arbitrary-property class);
   body text inherits from `<body>` automatically and needs no declaration. Inlining a
   hardcoded family name paints UNDER the next/font CSS variable and produces a visible
   doubled-text artifact (regular + serif stacked).
   For LAYOUT COLORS (page background, section surfaces, headings, body copy, borders,
   the primary→accent gradient set), USE SEMANTIC TOKENS so the palette can change
   between generations: bg-primary, text-foreground, bg-muted, border-border, bg-card,
   text-muted-foreground, plus opacity variants (text-foreground/80, bg-primary/50)
   and gradients across them (from-primary via-accent to-background).
   For STATUS COLORS (form success/error, validation hints), Tailwind named palettes
   are fine: text-green-600, text-red-600, text-amber-600.
   For BRAND-FIXED visuals (a logo SVG fill that must match the company's exact brand
   color, a partner badge, a flag icon), hex/rgb is fine.
   AVOID hex/rgb or Tailwind palette numbers (bg-blue-500, bg-[#2563eb]) for the main
   layout colors — those lock the page to one look and stop tracking the brief's
   palette. The post-gen pipeline logs warnings when it sees layout-color drift; treat
   it as a signal, not a hard error.
3. Render the section to match the provided layout_hint.
4. The component MUST default-export a React function whose name matches the file name
   (e.g. HeroSection.jsx → export default function HeroSection()).
5. The outermost element MUST be `<section id="<section-id>" className="...">` so anchor links work.
6. Use lucide-react icons ONLY when section.items[i].icon is set. Map the string to an import,
   e.g. `import {{ Zap, ShieldCheck }} from "lucide-react"` and resolve via a small map.
7. Mark files that use React hooks, onClick, or `<Reveal>` with `'use client';` as the first line.
8. NO CSS modules, NO styled-components, NO dynamic Tailwind class strings via template literals
   that Tailwind can't parse. All Tailwind classes must be statically present in the JSX.
9. NEVER render body / subheadline / description prose with INLINE PILL CHIPS embedded between
   words. If `section.subheadline` or `section.body` contains text, render it as a single
   continuous `<p>` — NEVER split it into a row of `inline-flex rounded-full px-3 py-1` pill
   spans interleaved with plain words. The pill-in-paragraph pattern looks like un-filled
   template placeholders ("Featured in [pill]culinary publications[/pill] and praised for
   [pill]authentic tapas[/pill]") and is FORBIDDEN. Pills are reserved for:
     • Eyebrow tags above a headline (single short label, ≤3 words).
     • Filter/category buttons in interactive controls (real onClick handlers).
     • Status / "new" badges sitting on top of a card image.
   If you need to highlight a phrase mid-paragraph, use `<span className="text-primary font-semibold">`
   inline — no rounded pill background, no padding box.
10. NEVER detect words inside section.subheadline/body and wrap them in pill spans based on a
    word list, regex, or `.replace()`. Render the string verbatim.
11. NEVER render `{{` or `}}` characters as visible UI. If you see curly-brace placeholder
    syntax inside a copy string (e.g. `"Featured in {{culinary publications}}"`), strip the
    braces and render the inner text inline as a normal `<span className="text-primary
    font-semibold">culinary publications</span>` — NEVER as a rounded pill chip. Treat
    curly-brace text as emphasis, not as form fields.
12. BORDER-RADIUS IS MANDATORY on every clickable affordance and card-like surface.
    The design system defines `--radius` (mapped to Tailwind `rounded-lg`). NEVER ship sharp
    90° corners on these elements:
      • <button>, <Button>, <Link asButton>, CTA links → `rounded-md` MINIMUM
        (use `rounded-lg`, `rounded-xl`, or `rounded-full` for pill-shaped CTAs)
      • <input>, <textarea>, <select>, search bars → `rounded-md` or `rounded-lg`
      • Cards, surfaces, and stat tiles (`bg-card border` containers) → `rounded-2xl` standard,
        `rounded-3xl` for hero/feature blocks
      • Avatars, brand-mark badges, status dots → `rounded-full`
      • Image containers, photo tiles → `rounded-xl` or `rounded-2xl`
    NEVER use `rounded-none` unless the brief explicitly says "brutalist" or "sharp" motif.
    If you write a card, button, or input WITHOUT a `rounded-*` class, you have produced
    a defect — the design system relies on radius for visual identity.
13. JSX SIBLINGS INSIDE ANY EXPRESSION MUST BE WRAPPED. JSX expressions can return
    exactly ONE element. The same rule applies to ternaries, `&&` short-circuits,
    `.map()` callbacks, IIFEs, and any function returning JSX. Multiple siblings
    inside `( ... )` produce a SWC syntax error ("Expected ',', got '<...'").
    Always wrap multiple siblings in a fragment or div:
      ✗  {{hasImage ? ( <Image .../> <div className="overlay" /> ) : ( <Fallback /> )}}
      ✓  {{hasImage ? ( <> <Image .../> <div className="overlay" /> </> ) : ( <Fallback /> )}}
      ✓  {{hasImage ? ( <div className="relative"><Image .../><div className="overlay"/></div> ) : ...}}
      ✗  {{hasImage && ( <Image .../> <div className="overlay" /> )}}
      ✓  {{hasImage && ( <> <Image .../> <div className="overlay" /> </> )}}
    Same rule for `.map()` callbacks: `items.map(x => <><Title/><Body/></>)` not
    `items.map(x => <Title/> <Body/>)`. The outer element of a map iteration must carry
    `key={{...}}` — fragments accept it via `<Fragment key={{...}}>` or a wrapping <div>.
14. CARD GRIDS MUST WRAP — NEVER COMPRESS CARDS INTO A SINGLE NON-WRAPPING ROW.
    Cards squeezed below their natural content width get clipped letters ("V T" instead of
    "Walking Tours"). To prevent this:
      • PREFER CSS Grid with explicit responsive breakpoints:
        `grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-6`
        — guarantees cards wrap to new rows at every viewport size.
      • OR use auto-fit for truly fluid grids:
        `grid grid-cols-[repeat(auto-fit,minmax(240px,1fr))] gap-6`
        — each card is AT LEAST 240px wide, expands to fill, wraps when needed.
      • IF you use flex for cards, you MUST add `flex-wrap` AND `min-w-[200px]` on each card.
        Never `flex` without `flex-wrap` for card collections.
      • NEVER apply `overflow-hidden` to the CARD TEXT CONTAINER (the div holding title +
        description). Overflow-hidden is allowed ONLY on image containers and decorative blobs.
      • NEVER apply `whitespace-nowrap` to card titles or descriptions — let multi-word titles
        wrap to 2 lines instead of getting clipped.
      • NEVER apply a fixed `w-24` / `w-32` / `w-40` / `w-48` to cards unless the design
        signature is icon-only chips (single ≤2-word label). Real cards (title + description
        + icon) need at least `w-full` inside a grid cell OR `min-w-[200px]` in a flex row.
      • For intentional text truncation (long descriptions in a fixed-height card), use
        `line-clamp-2` or `line-clamp-3` on the description block ONLY — never on the title.

ANIMATION (REQUIRED — every section MUST animate on scroll)
  • Import the Reveal component:  `import Reveal from "@/components/ui/Reveal";`
  • Wrap the eyebrow / headline / subheadline group in `<Reveal variant="fade-up">`.
  • Stagger card / list items with increasing `delay`: 0, 80, 160, 240ms (use the index).
  • Allowed variants: fade-up | fade-down | fade-left | fade-right | fade | scale.
  • Hover transitions on cards / buttons / images: `transition-all duration-300 hover:-translate-y-1 hover:shadow-lg` (calibrate to design_system.motion).

INTERACTIVITY (REQUIRED — the section must DO something, not just sit there)
  • The user prompt will tell you the exact interactive behavior under "INTERACTIVITY". Implement it FOR REAL with React state — NEVER stub it as a non-functional class.
  • Patterns by section type (use these unless the brief says otherwise):
      menu     → clickable item cards open a modal/drawer with full description, photo, and details. Use useState for open state. Backdrop closes. Esc closes.
      gallery  → click any tile opens a lightbox overlay (full-image, prev/next arrows, Esc to close, arrow-key nav). useState for the open index.
      faq      → accordion. Use useState OR `<details>`. Smooth height transition on open.
      testimonials → carousel/marquee with autoplay; manual prev/next arrows; pause on hover. useState for active index, useEffect for autoplay timer.
      pricing  → toggle pill switches between monthly / yearly prices (live). useState boolean.
      features → cards have hover lift + reveal a secondary detail line on hover (or use the item.details field if present).
      stats    → numbers count up on scroll into view. useEffect + IntersectionObserver + requestAnimationFrame.
      contact_form / reservation / booking_form / newsletter → REAL form with controlled inputs (useState), client-side validation (required fields, email regex), and a success state shown after submit (does NOT need a backend; show a thank-you panel + reset).
      cta / cta_band → primary button MUST have a real href that smooth-scrolls to a valid section anchor on the page (or a tel:/mailto: link from landing.brand.business_info).
  • Every <Link> / <a> / <button> MUST have a real, working target — never `href="#"` placeholders.
  • Mark the file `'use client';` whenever you use useState/useEffect/onClick/onSubmit. (Required for these patterns.)
  • Keyboard accessibility: modals/drawers/lightboxes close on Esc; carousels respond to arrow keys; focus is trapped inside open modals (use a simple ref + focus on mount).
  • ARIA: aria-label on icon-only buttons, aria-expanded on toggles, role="dialog" + aria-modal on overlays.

IMAGES — PHOTOS BEAT ICONS
  • HARD RULE: when items represent VISUAL/PHYSICAL things (destinations, hotels, dishes, products, properties, rooms, people, events, packages),
    you MUST render each card with a real photo as the primary visual — NOT a Lucide icon-in-rounded-square.
    Use `section.images[i]` (URL string, parallel-indexed with `section.items[i]`) as a `<Image fill>` background of the card.
    Aspect ratios: `aspect-[4/5]` for portraits, `aspect-square` or `aspect-[3/4]` for grids — vary across cards for editorial feel.
    The Lucide icon (if item.icon present) becomes a SMALL accent inside a glass chip on the photo, NOT the centerpiece.
    Only fall back to icon-only cards for ABSTRACT items (perks, benefits, capabilities, plans).

  • PHOTO MISSING? If `section.images[i]` is falsy for a card that *should* have a photo (destinations, dishes, etc.),
    render a soft gradient placeholder + the headline word over it — `bg-gradient-to-br from-muted to-muted/40 relative`
    with the city/dish name as a large serif overlay. NEVER fall back to a generic icon-square card; that erases the section's visual identity.
    CRITICAL: Always GUARD the <Image> render with a JSX truthy check on the URL. NEVER render an
    <Image> tag when the URL is an empty string — Next/Image produces a broken-image icon AND the
    alt text shows in the corner. Pattern (treat the section.images[i] truthy check as REQUIRED):
      const img = section.images?.[i] || "";
      <div className="relative aspect-[4/5] overflow-hidden rounded-2xl bg-muted">
        IF img is truthy → render <Image src=img alt=item.title fill className="object-cover" />
        ELSE             → render gradient placeholder div with item.title overlaid as serif text
    This guarantees the alt text is INSIDE the image element when the image loads and never leaks
    out as visible UI when the image fails.

  • SECTION TYPES THAT ARE INHERENTLY PHOTO-LED (always use photos when images exist):
      gallery, menu, destinations, hotels, properties, rooms, products, team, press,
      featured_listings, popular_destinations, guest_favorites (cards), portfolio.

  • IMAGE SHAPE — section.images is an ARRAY OF URL STRINGS (not objects).
    Each entry is just a fully-qualified Unsplash https:// URL.
    READ it directly: `const heroImg = section.images?.[0];` then `<Image src={{heroImg}} ... />`.
    For alt text, optionally read `section.image_alts?.[i]` (parallel array) — fallback to a hardcoded English string like the section's headline summary.

  • FORBIDDEN — hardcoded image URL constants. NEVER define a local `const UNSPLASH_IMAGES = {{...}}`
    or `const IMAGE_MAP = {{...}}` or any dict mapping item titles/keys to Unsplash URLs in your
    component file. The pipeline guarantees `section.images[i]` (parallel-indexed with
    `section.items[i]`) is already populated in landing.json by the time your component runs.
    If `section.images[i]` is empty/falsy, render the gradient placeholder described above —
    NEVER guess a URL from training data. Hardcoded URLs go stale, return 404, and bypass the
    binder's domain whitelist.
      ✗  const UNSPLASH_IMAGES = {{ peking_duck: "https://images.unsplash.com/photo-...", ... }};
      ✗  const HERO_IMG = "https://images.unsplash.com/photo-...";
      ✓  const heroImg = section.images?.[0];   // empty string → render placeholder
  • Use Next.js `<Image>` from "next/image" with `fill` + `sizes` for hero/large blocks, or fixed `width`/`height` for thumbnails.
  • Wrap each <Image> in a `relative overflow-hidden` container with the design_system.accent_shape rounding.
  • Apply design_system.image_treatment:
      natural   → no overlay
      overlay   → add `<div className="absolute inset-0 bg-gradient-to-t from-foreground/60 to-transparent" />`
      duotone   → add `<div className="absolute inset-0 bg-primary/30 mix-blend-multiply" />`
      bordered  → outer `border border-border` ring
      masked    → outer `[mask-image:linear-gradient(to_bottom,black_75%,transparent)]`
  • Hover: `<Image className="object-cover transition-transform duration-700 hover:scale-105" />`.

GALLERY SECTIONS — render section.images as a real responsive grid:
  layout=grid-3/grid-4 → `grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3 md:gap-4`.
  Make tiles different aspect ratios (aspect-[4/5] | aspect-square | aspect-[3/4]) for a curated look — no uniform thumbnails.

FORM SECTIONS (type contact_form | reservation | booking_form | contact | newsletter)
  • Render an actual `<form>` with proper labels, focus rings, and a submit button.
  • Reservation fields: name, email, phone, date (input type="date"), party size (input type="number"), special requests (textarea).
  • Newsletter: just email + submit.
  • Form panel: `bg-card border border-border` plus the corner-radius implied by design_system.accent_shape (see DESIGN SYSTEM below).
  • Submit button: full primary CTA styling, full width on mobile.
  • Wrap form fields in `<Reveal>` with staggered delay.

DESIGN SYSTEM (apply CONSISTENTLY across the section — this is what makes the whole landing look like one product, not 8 random components)
  Motion intensity: {ds.get("motion", "subtle")}      → subtle = duration-500 only on entrance; energetic = +hover lifts; dramatic = +scale on enter; organic = +slow ease, gentle blur-in.
  Accent shape:     {ds.get("accent_shape", "rounded")}  → squared: rounded-md on every interactive surface, rounded-lg on cards (NEVER rounded-none); rounded: rounded-xl on cards, rounded-md+ on buttons; pill: rounded-full on buttons + rounded-2xl on cards; blob: rounded-[40%_60%_70%_30%/40%_50%_60%_50%] on image masks; hairline: rounded-md on buttons, rounded-lg on cards, with a thin 1px border (NEVER rounded-none — the radius and the hairline border coexist). Rule 12 below is the hard floor — every accent_shape must respect it.
  Surface:          {ds.get("surface", "elevated")}    → flat: bg-card no shadow; elevated: shadow-md hover:shadow-xl; bordered: border-2 border-border no shadow; layered: stacked z-translucent panels (bg-card/80 backdrop-blur); duotone: alternating bg-muted/bg-card per card.
  Image treatment:  {ds.get("image_treatment", "natural")} (see IMAGES rules).
  Section rhythm:   {ds.get("section_rhythm", "balanced")} → tight: py-12 sm:py-16; balanced: py-16 sm:py-20 lg:py-24; airy: py-24 sm:py-32.

PROJECT_DESIGN_TOKENS — USE THESE EXACT TAILWIND CLASS STRINGS VERBATIM (do NOT pick alternates, do NOT improvise radii / shadows / paddings; these strings have already been resolved from the design system above and ARE the spec):
  • Section <section> root padding (vertical):  {dt.get("section_padding_class", "py-16 sm:py-20 lg:py-24")}
  • Card surface (cards, panels, form wrappers): {dt.get("card_class", "bg-card shadow-md hover:shadow-xl rounded-xl")}
  • Button / pill / chip border-radius:          {dt.get("button_radius_class", "rounded-md")}
  • Image / photo / media border-radius:         {dt.get("image_radius_class", "rounded-xl")}
  • Standard transition for hover effects:       {dt.get("transition_class", "transition-all duration-300")}
  • Standard hover lift for interactive cards:   {dt.get("hover_lift_class", "hover:-translate-y-1 hover:shadow-lg")}
  Examples of correct usage:
    <section id="..." className="{dt.get("section_padding_class", "py-16 sm:py-20 lg:py-24")} bg-background">
    <article className="{dt.get("card_class", "bg-card shadow-md hover:shadow-xl rounded-xl")} {dt.get("transition_class", "transition-all duration-300")} {dt.get("hover_lift_class", "hover:-translate-y-1 hover:shadow-lg")} p-6">
    <Image className="object-cover {dt.get("image_radius_class", "rounded-xl")}" ... />
    <button className="{dt.get("button_radius_class", "rounded-md")} bg-primary text-primary-foreground px-5 py-3">
  RULE: every card / panel / form / featured-list-item MUST start with the CARD class string above — no ad-hoc shadow / radius combinations. Every image container MUST use the image radius. Every button MUST use the button radius. The section root MUST use the section padding. This guarantees every section in the build matches.

DESIGN CONTEXT
  Brand: {brand_name}
  Motif: {motif}
  Heading font: {typography.get("heading_font", "Inter")}
  Body font:    {typography.get("body_font", "Inter")}
  Palette (already wired as CSS vars in globals.css):
{palette_lines}
{visual_dna_block}{pers_block}{ref_block}

SECTION ANATOMY (driven by VISUAL DNA + the user message)
  Every section's user message includes ONE of:
    • "ANATOMY — IMPLEMENT THIS EXACT SPEC" — the spec is research-grounded, written from real
      research about THIS brand. Treat its structural skeleton (z-stacking, container hierarchy,
      grid shape, scale tokens, decorative integration) as non-negotiable.
    • "ANATOMY — STARTING POINT" — a generic structural floor. Use its z-stacking + overflow
      + headline scale as invariants, but compose the actual look from VISUAL DNA above.
  Centered-text-on-flat-color is a guaranteed FAILURE either way — every section must offer
  something visually distinct.

SECTION DENSITY (every non-hero section must feel COMPLETE — sparse = broken)
  Every non-hero section MUST render at least 3 distinct visual content blocks before any
  whitespace/spacer:
    Block A — Heading group (eyebrow + h2 + 1-line subhead OR a short lede paragraph).
    Block B — Primary content (cards / list / form / image grid / quote / stat band).
    Block C — Supporting block (a real, distinct UI element — secondary CTA + meta line,
              attribution row, info-tile triple, micro-stat strip, "as seen in" wordmark
              row, accordion teaser, address card, or trust badges). NEVER fill Block C
              with a giant decorative oversized word watermark; it must be REAL content.
  If section.items has fewer than the required minimum for its type, repeat brand.business_info
  rows, sibling-section CTAs, or attribution lines to reach density — never leave a 600px tall
  section with a single centered headline floating in space.
  DO NOT render a section that is just `<h2>` + `<p>` with nothing below it.

OTHER SECTION RULES (sections without an archetype block fall back to these)
  stats — Big numbers band: 2-4 columns, each item.value in text-5xl sm:text-6xl font-bold text-primary, item.label below in uppercase tracking-widest text-muted-foreground.

  faq — Vertical accordion. `<details>` + `<summary>` pattern (no client JS). Plus icon rotates 45° on open via `[&[open]_.fa-icon]:rotate-45`.

  pricing — 2-3 plan cards. Highlight one via `ring-2 ring-primary` + small "Recommended" pill at top. Big price, feature list with check icons.

  cta / cta_band — Full-width band with bg-primary, primary-foreground text, large headline, supporting line, contrasting button (bg-background text-foreground).

  contact_form / reservation / booking_form / newsletter — One real form (see FORM SECTIONS above). Pair with a left-side info panel showing brand.business_info (address / phone / email / hours) for visual weight balance.

  team — Grid of avatar cards (rounded-full or rounded-2xl images), name, role, optional 1-line bio.

  press / logos / publications — A press section MUST have THREE rendered blocks (no exceptions):
    [1] Heading group — eyebrow ("PRESS"), headline (h2), 1-line plain subheadline.
        Render section.subheadline verbatim as a single `<p>` (no pill splitting).
    [2] Logo strip — 4-column wordmarks. `grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-x-6
        md:gap-x-10 gap-y-8 items-center`. Each cell `flex items-center justify-center h-16 md:h-20`.
        INSIDE each cell render the publication name as a uppercase wordmark
        (`<span className="text-sm md:text-base font-semibold tracking-[0.18em] text-foreground/70
        uppercase whitespace-nowrap text-center">{{name}}</span>`). Pull names from
        section.items[i].label OR section.items[i].title. NEVER use `<Image>` or fake SVG logos
        (Unsplash returns garbage for "michelin guide logo"). Add subtle vertical dividers between
        cells with `divide-x divide-border` on the parent on md+.
    [3] Pull-quote block — render section.items[i].description (the 8-16-word quote) as a real
        editorial pull quote: `<figure>` with quote in `text-xl md:text-2xl font-serif italic
        text-foreground leading-snug` and attribution (`item.title` or `item.label`) below in
        `text-sm uppercase tracking-[0.18em] text-muted-foreground`. If 3+ items have quotes,
        render a small carousel (autoplay 6s, 1 quote at a time, dots indicator). Otherwise
        a centered single quote with `max-w-3xl mx-auto`.
    NEVER render the section as JUST a pill row of 3 award-name pills with no logo strip and
    no quote. NEVER place a giant decorative watermark word in the section background.
    NEVER write the body as pill chips embedded in a paragraph (see Rule 9 / 11 above).
    NEVER use absolute-positioned, rotated, or overlapping award/press cards (no `card-stack`
    pattern, no `absolute inset-0`, no `rotate-[Xdeg]` on the cards, no negative margins that
    pull cards over each other). Press cards/logos render in a FLAT, in-flow grid only — every
    cell occupies its own row/column with no overlap. If you write `position: absolute` or
    `rotate-` on a press card, you have produced a defect.
    Section MUST be at least `min-h-[480px]` content-wise — fill all 3 blocks before any spacer.

  experience / journey / process / steps — When the section uses a horizontal carousel of cards,
    EVERY card in the visible viewport MUST have the SAME height. Use `flex` on the track with
    `[&>*]:h-[420px] sm:[&>*]:h-[460px]` (or grid + `auto-rows-fr`). Each card uses
    `relative overflow-hidden rounded-2xl` with the photo as `<Image fill className="object-cover" />`
    INSIDE the card — never let an oversized image push a single card taller than its siblings.
    Bottom-align text via a `bg-gradient-to-t from-foreground/85 via-foreground/40 to-transparent`
    overlay with the title + body in `text-background` on the lower third.

PIXEL-PRECISE LAYOUT TOKENS (use exactly these — they keep the whole page on one rhythm)
  • Outer section: `<section id="..." className="<bg> py-20 md:py-28 lg:py-36">` — vertical rhythm is fixed.
  • Decorative bleed containment: if THIS section uses ANY absolute-positioned decorative element
    (oversized watermark word, decorative blob, motif shape, accent ring, gradient orb, image that
    extends past the section edge for editorial effect, rotated card, sticker badge offset with
    negative inset), the outer `<section>` MUST include `overflow-hidden` (or `overflow-x-clip` if
    the section needs sticky/scrolling children). Decorative elements that escape the section root
    appear over the next section, the footer, the page edges, or as stray dark shapes in empty
    space — every shipped page with a black blob in a corner traces back to a missing `overflow-hidden`
    on the section that owns the decoration. Add it preemptively whenever you introduce an absolute
    decorative element; do NOT wait to see it leak.
  • Container: `<div className="container max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">` — never wider, never narrower.
  • Eyebrow:    `text-xs tracking-[0.2em] uppercase text-primary font-semibold mb-3`
  • H2:         `text-3xl sm:text-4xl lg:text-5xl font-bold tracking-tight font-[family-name:var(--font-heading)]`
  • Subhead:    `mt-4 text-lg md:text-xl text-muted-foreground max-w-2xl`  (max-w-3xl when centered)
  • Body copy:  `text-base md:text-lg text-muted-foreground leading-relaxed`
  • Section gap: between heading group and content block use `mt-12 md:mt-16 lg:mt-20`.
  • Card grid gap: `gap-4 md:gap-6 lg:gap-8` — never `gap-2` (cramped) or `gap-12` (too sparse).
  • Card chrome: `bg-card border border-border rounded-2xl p-6 lg:p-8 transition-all duration-300 hover:-translate-y-1 hover:shadow-lg hover:border-primary/30`.
  • Button primary: `inline-flex items-center justify-center px-6 py-3 rounded-full bg-primary text-primary-foreground font-medium hover:opacity-90 transition`.
  • Button secondary: same but `bg-card border border-border text-foreground hover:bg-muted`.
  • Anti-stretching: NEVER let text run wider than `max-w-prose` (~65ch); NEVER let card columns exceed 4 on lg.

CONTRAST & READABILITY (NON-NEGOTIABLE — every line of text must be plainly legible)
  • Body / paragraph text MUST use `text-foreground` or `text-muted-foreground` — NEVER `text-foreground/40`,
    `text-muted-foreground/60`, or any sub-60% opacity for paragraph copy. Eyebrow tags and timestamps may use
    `text-muted-foreground` (full opacity) but never lower.
  • Headlines: ALWAYS `text-foreground` (or `text-primary-foreground` when sitting on `bg-primary`). Never apply opacity to headlines.
  • Text over IMAGES: stack a real overlay (`bg-foreground/60`, or `bg-gradient-to-t from-foreground/70 to-transparent`)
    BEFORE rendering text, then render text in `text-background`. Never put white text directly on unprocessed photos.
  • Text over `bg-primary`: must be `text-primary-foreground`. Text over `bg-card` / `bg-muted`: must be `text-foreground` (NOT muted).
  • DARK INVERSE SURFACES — when a section, footer, or panel uses ANY of
    `bg-foreground`, `bg-secondary` (when secondary is a dark brand color),
    `bg-foreground/95`, or any dark `bg-*` shade that inverts the page:
      ✗ NEVER use `text-foreground` (same hue as bg → invisible).
      ✗ NEVER use `text-muted-foreground` (slightly darker hue → invisible).
      ✓ Body text MUST be `text-background` (full opacity, max contrast).
      ✓ Muted/secondary text on dark surfaces uses `text-background/70`
        (NEVER lower; 70% on dark inverse stays AA-readable).
      ✓ Headings: `text-background` (no opacity).
      ✓ Links: `text-background hover:text-primary-foreground`
        (or hover to `text-accent` if accent is light).
      ✓ Borders: `border-background/15` for hairlines on dark panels.
      ✓ Form inputs on dark surfaces: `bg-background/10 text-background
        placeholder:text-background/50 border-background/20`.
      ✓ Pairing `bg-primary-foreground` text on `bg-secondary` is a
        FAILURE when secondary isn't paired with primary-foreground in
        the palette — use `text-background` instead. The only safe
        cross-token pairing is `bg-primary` ↔ `text-primary-foreground`.
    This is the #1 footer / dark-band failure mode — invisible nav links and
    body copy because the writer reached for `text-foreground` or
    `text-muted-foreground` reflexively. Always invert text on inverse surfaces.
  • Watermark / decorative oversized type uses `text-foreground/[0.06]` — that is the ONLY place a sub-10% opacity is allowed,
    and it must NEVER be the only text in its block (it sits BEHIND a real headline).
  • Buttons MUST visibly differ from the surface they sit on:
      Primary CTA on light surface  → `bg-primary text-primary-foreground` (solid, no transparency).
      Primary CTA on dark/photo hero → `bg-background text-foreground` (inverted) — never `bg-white/20` glass on a photo
      that already has busy detail; if you want glass, add `backdrop-blur-md bg-background/80` (≥80%, not 20%).
      Secondary button → `border border-border bg-card text-foreground hover:bg-muted` — visible border is REQUIRED.
      NEVER ship a button that is `bg-white text-white`, `bg-primary/10 text-primary` on `bg-card` (too low contrast),
      or any combo where label and surface differ by less than ~3:1 luminance.
  • Disabled / hover states still show a label — never fade label opacity below 70%.

CARD ALIGNMENT — PIXEL-PERFECT (cards in a row MUST line up; no jagged grids)
  • Every card in a multi-card row MUST share the SAME outer chrome: same padding, radius, border, height behavior.
  • Apply `h-full` to each card and `items-stretch` (or `grid auto-rows-fr`) on the parent so cards in a row equalize.
  • Internal vertical layout: `flex flex-col gap-3` — title, description, then `mt-auto` on the footer/CTA so footers ALIGN
    across cards with different copy lengths.
  • Card photos in a grid: lock aspect ratio (`aspect-[4/5]`, `aspect-square`, or `aspect-[3/4]`) and use `object-cover`.
    Mixing aspects across the SAME row is forbidden — vary aspects only across DIFFERENT rows for editorial rhythm.
  • Headings inside cards: clamp to 2 lines (`line-clamp-2`); descriptions clamp to 3 lines (`line-clamp-3`) so visual weight stays even.
  • Icon chips inside cards: fixed size — `h-10 w-10` or `h-12 w-12`. Never let a chip resize with the icon.
  • Buttons inside cards: same height (`h-10` or `h-11`) and same padding across all cards in the row.
  • Grid columns: pick ONE of `grid-cols-1 md:grid-cols-2 lg:grid-cols-3`, `grid-cols-1 md:grid-cols-2`, or 4-up — DO NOT mix column counts mid-section.
  • The bottom edges of all cards in a row MUST end on the same Y. If copy varies, push the CTA down with `mt-auto`.

CARD WIDTH — FLUID GRID ONLY (no narrow/collapsed cards, no card-stack improvisations)
  This applies to EVERY section that renders multiple cards (features, value_prop, benefits,
  process, services, why-choose-us, capabilities, team, etc.) — not just press.
  ✗ FORBIDDEN PATTERNS — these produce squished/unreadable cards and are defects:
    • Fixed pixel widths on cards (`w-[280px]`, `w-72`, `min-w-[200px]`). Cards must be GRID-FLUID.
    • Negative margins between cards (`-ml-4`, `-space-x-2`, `-mt-8`) to make them overlap.
    • `position: absolute` / `absolute inset-0` on a card in a list of cards.
    • `rotate-[Xdeg]` on cards (the only legitimate rotated card is a SINGLE editorial accent).
    • Horizontal scroll (`overflow-x-auto flex flex-nowrap`) for ≤ 6 items — use a grid instead.
    • Oversized numbered watermarks `01 02 03` rendered as a background BEHIND cards
      (Claude improvises this from editorial design refs but it always collapses widths). If
      numbering is desired, put it INSIDE each card as a small eyebrow (`text-sm text-primary
      font-bold`), NEVER as an absolute-positioned giant numeral behind the card.
    • Cards rendered in a `flex flex-row` WITHOUT `flex-1` / `basis-0` so each child gets equal width.
  ✓ REQUIRED — every multi-card section uses ONE of:
    • `grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-6` (3-up, for 3-6 items)
    • `grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6` (4-up, for 4 or 8 items)
    • `grid grid-cols-1 md:grid-cols-2 gap-6 lg:gap-8` (2-up, for 2 or 4 large items)
    Each cell is `w-full` — fluid; the grid handles distribution. If you write a width unit
    on a card (px / rem / Tailwind w-N), you have produced a defect.

CONTENT INSIDE A CARD — overflow + multi-column safety
  This applies to ANY content INSIDE a card or panel: stat triples, label/value rows,
  big-number callouts, side-by-side metrics, mini-tables. The card itself can be
  fluid (per above) and still produce broken layouts INSIDE if these rules slip:
  ✗ FORBIDDEN INSIDE-CARD PATTERNS:
    • Oversized numeric / currency headlines without responsive scaling.
      `text-7xl` on a $1,800 number inside a narrow card WILL clip. Always use a
      RESPONSIVE scale: `text-4xl sm:text-5xl md:text-6xl` and never bigger than
      `text-6xl` for headline numbers inside a card. The hero outside cards is
      a different rule — INSIDE cards, smaller scales are mandatory.
    • Stat / metric triples rendered as `flex flex-row` WITHOUT `min-w-0` on each
      child. flex children default to `min-w-0: auto` which makes long labels
      ("DISPOSABLE INCOME") push siblings off-screen or smash into each other.
      EVERY flex/grid child rendering text MUST have `min-w-0` so text can shrink/wrap.
    • Adjacent label/value columns with no gap. `gap-3` minimum between columns;
      `gap-4` or `gap-6` for stat triples. NEVER `gap-0` or no gap class.
    • Long uppercase labels with `tracking-widest` and no wrapping. "INCOME EXPENSES
      DISPOSABLE" laid out as 3 inline columns at `text-xs uppercase tracking-widest`
      MUST use `break-words` or shorter labels — otherwise letters from one label
      run into the next.
  ✓ REQUIRED INSIDE-CARD PATTERNS:
    • Containers that hold a single oversized headline: outer card has
      `overflow-hidden` or the headline uses `break-all` / `tabular-nums` with
      responsive scaling. Test mentally: "if the number were $999,999 would it fit?"
      If no, scale down or add overflow handling.
    • Stat triples / metric rows: `grid grid-cols-3 gap-4` with each cell
      `min-w-0 flex flex-col items-start gap-1`. Label uses `text-[11px] sm:text-xs
      uppercase tracking-wider text-muted-foreground` (NOT tracking-widest on long
      words). Value uses `text-lg sm:text-xl font-bold tabular-nums`.
    • For currency values: `tabular-nums` so digits align; consider `text-balance`
      on multi-word labels.
    • If a card holds both a giant headline AND a sub-metric row, the headline scales
      down at the card's responsive breakpoints (`text-4xl md:text-5xl`), not at the
      viewport's. A card that takes 50% of the viewport at lg+ has the layout
      constraints of a `md` screen, not a `lg` screen — choose scales accordingly.

SECTION SURFACE RHYTHM (forces the page to alternate, not look monotone)
  The user prompt for THIS section tells you which surface to use. Pick the OUTER section className
  from this menu — and pair it with the INNER card surface that has guaranteed contrast against it:

    Section bg = `bg-background`   → inner cards: `bg-card border border-border` (lighter pop).
    Section bg = `bg-muted/40`     → inner cards: `bg-background border border-border/60` (lighter pop).
    Section bg = `bg-card`         → inner cards: `bg-muted/40 border border-border` (subtle indent).
    Section bg = `bg-primary/5`    → inner cards: `bg-background border border-primary/20` (warm tint).
    Section bg = `bg-foreground`   → inner cards: `bg-background/10 border border-background/15` (dark mode panel).

  Required diversity rules:
    • NEVER ship two adjacent sections with the same outer bg. The user message lists the previous
      section's bg so you can pick a different one.
    • A section's inner card MUST always be a DIFFERENT shade than the section bg — never bg-card on
      bg-card, never bg-background on bg-background. Cards must POP off the section, not blend.
    • Form inputs (input, textarea, select) MUST use `bg-background border border-border` on a non-
      background section, or `bg-muted/40 border border-border` when the section IS bg-background.
      An input that visually disappears against its container is a FAILURE.

QUALITY BAR
  • Sections that are GENERIC (centered headline + 3 plain icon cards) are a FAILURE — every section must offer something visually distinct.
  • Hero must NOT be a centered text block on flat color — it must use a real background image with overlay.
  • Adjacent sections must visually differ (varying bg, layout, or rhythm). The user message tells you which background to use.
  • Cards have hover-lift + accent border or icon — never flat text-only.
  • Spacing follows the PIXEL-PRECISE LAYOUT TOKENS above.
  • DO NOT produce a section that is purely a paragraph of text — every section earns its place visually.
  • DO NOT default to a 3-column icon-card grid for non-photo sections — pick a NO-PHOTO LAYOUT VARIANT from the user message.
"""


def _user_prompt(
    section: dict[str, Any],
    siblings: list[dict[str, Any]],
    component_name: str,
    file_path: str,
    *,
    section_index: int = 0,
    section_count: int = 1,
    voice_context: dict[str, Any] | None = None,
    visual_dna: dict | None = None,
) -> str:
    """Per-section user prompt — section spec + 2 sibling specs for cohesion."""
    sib_summaries = []
    for sib in siblings:
        sib_summaries.append(
            f"  - id={sib.get('id')!r} type={sib.get('type')!r} layout={sib.get('layout_hint')!r} archetype={sib.get('archetype','')!r}"
        )
    sib_block = "\n".join(sib_summaries) or "  (none)"

    # Visual rhythm cue: rotate through a 4-bg cycle so adjacent sections
    # always differ AND the page reads as visually balanced (not just
    # alternating two colors). Hero (index 0) is the photo hero so we still
    # use bg-background under it. The cycle is tuned so primary/5 (warm
    # tint) appears once near the middle for visual anchor.
    _BG_CYCLE = ["bg-background", "bg-muted/40", "bg-card", "bg-primary/5"]
    if section_index == 0:
        bg_hint = "bg-background"
    else:
        bg_hint = _BG_CYCLE[section_index % len(_BG_CYCLE)]
    prev_bg_hint = "bg-background" if section_index <= 1 else _BG_CYCLE[(section_index - 1) % len(_BG_CYCLE)]

    has_images = bool(section.get("image_queries") or section.get("images"))
    image_hint = ""
    if not has_images:
        # Pick a no-photo layout variant deterministically from section_index so
        # adjacent text-only sections naturally land on different patterns.
        # This is the main lever against the "everything looks like a 3-col
        # icon-card grid" monotony problem.
        _NO_PHOTO_VARIANTS = [
            (
                "EDITORIAL_NUMBERED_LIST",
                "Large oversized numerals (text-7xl md:text-8xl font-serif tabular-nums text-primary/80) "
                "in the leftmost column, each followed by item title (text-2xl font-semibold) and a "
                "1-2 sentence body. Hairline `border-t border-border/60` between rows. NO cards, NO icons, "
                "NO uniform tiles — pure editorial typography. Container max-w-4xl mx-auto."
            ),
            (
                "ASYMMETRIC_BENTO_TEXT",
                "Bento grid `grid grid-cols-12 gap-4 lg:gap-6 auto-rows-[180px]`. First item spans "
                "`col-span-12 lg:col-span-7 row-span-2` with a big serif title + body. Second spans "
                "`col-span-12 lg:col-span-5 row-span-1` with stat-style content. Remaining items in "
                "`col-span-6 lg:col-span-4` tiles. Each tile uses `bg-card border border-border rounded-2xl p-6 lg:p-8` — "
                "but vary surface tint: alternate between `bg-card`, `bg-muted/40`, and `bg-primary/5`."
            ),
            (
                "VERTICAL_TIMELINE",
                "Vertical line down the center (or left side on mobile). Each item is a card alternating "
                "`md:translate-x-0` vs `md:translate-x-[55%]` so it zig-zags. Connector dot at the line "
                "(`w-3 h-3 rounded-full bg-primary` absolutely positioned). Card uses `bg-card border border-border` "
                "with the title, description, and (if item.label or item.value) a metric line. Container max-w-5xl."
            ),
            (
                "PULL_QUOTE_NARRATIVE",
                "Single dominant pull-quote in HUGE serif (text-4xl md:text-6xl font-serif leading-[1.1]) "
                "spanning two grid columns. Right column has 3 supporting items as stacked text-only blocks "
                "with a hairline `border-l border-border pl-6` and small uppercase eyebrow. NO card chrome. "
                "Background is `bg-muted/20`. Container max-w-6xl."
            ),
            (
                "STAT_BAND_DETAILED",
                "Headline + intro centered. Below: a 2- or 3-column band of stat blocks (HUGE number + label) "
                "separated by `divide-x divide-border`. Below the band: items as 2-column text rows (icon-chip + "
                "title left, multi-sentence description right) — NOT identical card tiles. The stat band is the "
                "centerpiece; items are supporting copy."
            ),
            (
                "PILL_CLUSTER_NARRATIVE",
                "Centered narrative paragraph (text-xl md:text-2xl text-muted-foreground max-w-3xl) with KEY "
                "category words rendered as inline pill chips (`inline-flex items-center px-3 py-1 rounded-full "
                "bg-primary/10 text-primary text-sm font-medium`). Below the paragraph: a single horizontal row "
                "of larger pills (one per item) — clickable, each opens a slide-down detail panel below the row. "
                "NO grid of cards."
            ),
            (
                "SPLIT_HERO_BLOCK",
                "Two-column split: left col is a giant typographic block (eyebrow + serif headline that wraps "
                "across multiple lines + body + CTA), right col is a single tall accent panel `aspect-[3/4] "
                "bg-gradient-to-br from-primary/15 via-accent/10 to-muted/40 rounded-3xl relative overflow-hidden` "
                "with the brand initial as a HUGE watermark (text-[12rem] font-serif text-primary/20 absolute) and "
                "the items rendered as a small list overlaid bottom-left. NO grid of identical cards."
            ),
        ]
        _variant_idx = section_index % len(_NO_PHOTO_VARIANTS)
        _vname, _vspec = _NO_PHOTO_VARIANTS[_variant_idx]
        image_hint = (
            f"\nNO-PHOTO LAYOUT — IMPLEMENT THIS PATTERN ({_vname}, picked from section position):\n"
            f"  {_vspec}\n"
            "Reason: this section has no images, and a generic 3-column icon-card grid would make the page "
            "feel like a template. Above pattern is structurally distinct from icon-cards. Apply the design "
            "system tokens (motif, accent_shape, motion) within this skeleton."
        )

    # Anatomy injection — research-driven first, fallback skeleton second.
    # `_resolve_anatomy` checks visual_dna.section_anatomies[type] (Gemini-
    # written from grounded research), falls back to _FALLBACK_SKELETONS
    # (minimal structural floor), and finally _GENERIC_FALLBACK. The source
    # determines the framing language: research-grounded anatomies get
    # "IMPLEMENT THIS"; fallback skeletons get "STARTING POINT — overlay
    # cultural cues from visual_dna" so Claude treats them as a floor not
    # a ceiling.
    section_type = (section.get("type") or "").lower()
    anatomy_text, anatomy_source = _resolve_anatomy(section_type, visual_dna)
    if anatomy_source in ("research", "research-aliased"):
        archetype_block = (
            f"\nANATOMY — IMPLEMENT THIS EXACT SPEC (research-grounded for this brand's {section_type} section):\n"
            f"{anatomy_text}\n"
            "Tailwind class choices and copy positioning details are yours, but the structural skeleton, "
            "decorative integration, and scale tokens above are the spec. This anatomy was written from real "
            "grounded research about THIS brand — it is the right shape for this project."
        )
    else:
        archetype_block = (
            f"\nANATOMY — STARTING POINT (generic fallback for {section_type} — no research-grounded anatomy was produced for this brand):\n"
            f"{anatomy_text}\n"
            "This is a STRUCTURAL FLOOR, not a final design. The composition, decorative integration, and "
            "any cultural inflection should be driven by the VISUAL DNA block in the system prompt. Use the "
            "floor's z-stacking, overflow, and headline scale as invariants — but compose the actual look "
            "(asymmetric vs centered, photo-led vs editorial, motif placements) from visual_dna."
        )

    # Interactivity directive from the brief — this is the "must DO something"
    # instruction that gets implemented as React state + handlers.
    interactivity = (section.get("interactivity") or "").strip()
    interactivity_block = ""
    if interactivity:
        interactivity_block = (
            f"\nINTERACTIVITY — IMPLEMENT THIS BEHAVIOR FOR REAL (state + handlers, no stubs):\n"
            f"  {interactivity}\n"
            "Use useState/useEffect as needed. Mark the file 'use client'. "
            "Every clickable element must have a real, working onClick/href. "
            "Add keyboard handlers (Esc closes overlays; arrow keys for carousels) and ARIA attributes."
        )

    # Optional voice/context block — sourced from grounded research signals
    # merged into the brief by enrich_brief_with_signals(). When present,
    # this primes the copywriter on real customer phrasing, regional
    # anchors, and category jargon. Empty / missing fields are skipped.
    voice_block = ""
    vc = voice_context or {}
    voice_phrases   = list(vc.get("voice_phrases") or [])[:6]
    industry_terms  = list(vc.get("industry_terms") or [])[:8]
    regional_refs   = list(vc.get("regional_refs") or [])[:4]
    white_space     = list(vc.get("white_space") or [])[:3]
    if voice_phrases or industry_terms or regional_refs or white_space:
        parts: list[str] = ["\nVOICE & CONTEXT (grounded research signals — use as inspiration, NOT verbatim filler):"]
        if voice_phrases:
            parts.append("  • Real customer phrasing — mirror this register/cadence in headlines, eyebrow text, and CTA microcopy. Do NOT paste these as testimonials unless this is the testimonials section. Do NOT quote them word-for-word in the hero:")
            for p in voice_phrases:
                parts.append(f"      – {p}")
        if industry_terms:
            terms = ", ".join(industry_terms)
            parts.append(f"  • Industry vocabulary — weave 1-2 of these naturally into THIS section's body copy where they fit (skip if forced): {terms}")
        if regional_refs:
            parts.append("  • Regional anchors — for proof / about / location-flavored sections, reference 1 of these by name (skip for generic feature sections):")
            for r in regional_refs:
                if isinstance(r, dict):
                    parts.append(f"      – {r.get('name','')}: {r.get('context','')}")
                else:
                    parts.append(f"      – {r}")
        if white_space:
            parts.append("  • Differentiation angles competitors aren't taking — if THIS section is value-prop / differentiators, work one of these in:")
            for w in white_space:
                parts.append(f"      – {w}")
        parts.append("  IMPORTANT: this is voice priming, not a checklist. If a signal doesn't fit this section's role, ignore it. Never sacrifice clarity to shoehorn a phrase.")
        voice_block = "\n".join(parts)

    # Purpose directive — highest-priority structural law. Emitted whenever
    # analyze_intent picked a known purpose (hiring / lead_generation /
    # ecommerce / booking). Empty string skips the block.
    purpose_directive = (vc.get("purpose_directive") or "").strip()
    purpose_block = ""
    if purpose_directive:
        purpose_block = (
            "\n\nPURPOSE DIRECTIVE — read this BEFORE writing any JSX:\n"
            "This block declares what KIND of page this is and what its sections must accomplish. "
            "If it conflicts with COPY_DECK or LAYOUT_BLUEPRINT on STRUCTURAL questions (which sections "
            "exist, what's mandatory, what's forbidden), the directive wins. The deck still owns exact "
            "string content for sections that DO exist.\n"
            f"{purpose_directive}"
        )

    return f"""Build ONE section component.

FILE PATH:     {file_path}
COMPONENT:     {component_name}
SECTION ID:    {section.get('id')}
SECTION TYPE:  {section.get('type')}
LAYOUT HINT:   {section.get('layout_hint')}
ROLE:          {section.get('role','')}
POSITION:      section {section_index + 1} of {section_count}.
SECTION BG:    `{bg_hint}` — use this on the outer <section>. Previous section was `{prev_bg_hint}`, so DO NOT
               repeat that surface. Pair the bg with inner-card surfaces per the SECTION SURFACE RHYTHM rules.

SECTION SPEC (this is also what `landing.sections.find(s => s.id === {section.get('id')!r})` returns at runtime; use the FIELDS to know what to render, but read VALUES from the JSON at runtime):
{json.dumps(section, indent=2, ensure_ascii=False)}

ADJACENT SECTIONS (these are around yours — your job is to look DIFFERENT from them, not similar):
{sib_block}{voice_block}

ANTI-MONOTONY RULE — CRITICAL:
  • If an adjacent section uses a 3-column card grid, YOURS MUST NOT.
  • If an adjacent section uses centered eyebrow + headline + grid, vary YOUR opening (try left-aligned, asymmetric, or split layout).
  • Pick a DIFFERENT dominant structural shape from your neighbors:
    {{single column narrative, 2-column split, 3-col grid, bento asymmetric, full-bleed band, timeline,
      stat-band, accordion list, marquee row, comparison table, pull-quote dominant, pill cluster}}.
  • If you and a neighbor have the SAME `type`, your `archetype` is your differentiator — use it.
  • Cohesion comes from SHARED design tokens (palette, fonts, accent_shape, motion) — NOT from copying their layout.
{image_hint}{archetype_block}{interactivity_block}{purpose_block}

Generate the component now. Output via write_project_files with exactly ONE file."""


async def _generate_one_section(
    section: dict[str, Any],
    siblings: list[dict[str, Any]],
    *,
    brand_name: str,
    motif: str,
    palette: dict,
    typography: dict,
    design_system: dict,
    personality: dict,
    references: list[dict],
    design_tokens: dict,
    section_index: int,
    section_count: int,
    api_key: str,
    websocket: Any,
    voice_context: dict[str, Any] | None = None,
    visual_dna: dict | None = None,
) -> dict[str, str] | None:
    """Generate one section component. Returns {'path', 'content'} or None on failure."""
    from app.services.project_generator import call_claude_for_json

    filename = _section_filename(section)
    component = _component_name(filename)
    file_path = f"src/components/sections/{filename}"

    sys_p = _system_prompt(
        brand_name, motif, palette, typography, design_system,
        personality=personality, references=references,
        design_tokens=design_tokens,
        visual_dna=visual_dna,
    )
    usr_p = _user_prompt(
        section, siblings, component, file_path,
        section_index=section_index, section_count=section_count,
        voice_context=voice_context,
        visual_dna=visual_dna,
    )

    # One retry per section. The model-fallback inside call_claude_for_json
    # handles upstream errors (sonnet → opus) but doesn't retry when Claude
    # returns a successful 200 with empty / missing-jsx content — which is
    # the case we keep losing sections to. Two attempts catches both:
    # transient infra blips AND occasional empty tool_use outputs.
    section_id = section.get("id")
    max_attempts = 2
    last_failure_reason = "unknown"

    for attempt in range(1, max_attempts + 1):
        try:
            result = await asyncio.wait_for(
                call_claude_for_json(
                    system_prompt=sys_p,
                    user_prompt=usr_p,
                    api_key=api_key,
                    websocket=websocket,
                    max_tokens=_SECTION_MAX_TOKENS,
                ),
                timeout=90.0,
            )
        except asyncio.TimeoutError:
            last_failure_reason = "timeout after 90s"
            logger.warning(
                "section %s: codegen timed out on attempt %d/%d — skipping",
                section_id, attempt, max_attempts,
            )
            continue
        except Exception as exc:
            last_failure_reason = f"exception: {exc}"
            logger.warning(
                "section %s: codegen attempt %d/%d threw — %s",
                section_id, attempt, max_attempts, exc,
            )
            continue

        if not result or "files" not in result:
            last_failure_reason = "empty result (no 'files' key)"
            logger.warning(
                "section %s: codegen attempt %d/%d returned empty result",
                section_id, attempt, max_attempts,
            )
            continue

        files = result.get("files") or []
        if not files:
            last_failure_reason = "Claude returned 0 files"
            logger.warning(
                "section %s: codegen attempt %d/%d returned 0 files",
                section_id, attempt, max_attempts,
            )
            continue

        # Claude was asked for one file — take the first JSX/TSX.
        saw_jsx = False
        for f in files:
            path = (f.get("path") or "").strip()
            content = (f.get("content") or "")
            if path.endswith((".jsx", ".tsx")) and content:
                saw_jsx = True
                valid, reason = _section_content_looks_valid(
                    content,
                    str(section_id or ""),
                    component,
                )
                if not valid:
                    last_failure_reason = f"contract validation failed: {reason}"
                    logger.warning(
                        "section %s: codegen attempt %d/%d failed contract validation — %s",
                        section_id, attempt, max_attempts, reason,
                    )
                    break
                if attempt > 1:
                    logger.info("section %s: succeeded on retry (attempt %d)", section_id, attempt)
                # Force the path to our canonical location so Claude can't pick a different folder
                return {"path": file_path, "content": content}

        if not saw_jsx:
            last_failure_reason = "no .jsx/.tsx file in result"
        logger.warning(
            "section %s: codegen attempt %d/%d did not produce an acceptable component — %s",
            section_id, attempt, max_attempts, last_failure_reason,
        )

    logger.error(
        "section %s: codegen FAILED after %d attempts — last failure: %s; writing deterministic fallback",
        section_id, max_attempts, last_failure_reason,
    )
    return _fallback_section_component(section, component, file_path)


def _pick_siblings(sections: list[dict[str, Any]], idx: int) -> list[dict[str, Any]]:
    """Two neighboring sections (prev + next) for cohesion context."""
    sibs: list[dict[str, Any]] = []
    if idx - 1 >= 0:
        sibs.append(sections[idx - 1])
    if idx + 1 < len(sections):
        sibs.append(sections[idx + 1])
    return sibs


async def generate_landing_sections(
    *,
    brief: dict[str, Any],
    workspace_path: str,
    api_key: str,
    websocket: Any = None,
    concurrency: int = 3,
) -> dict[str, Any]:
    """Generate all section components in parallel.

    Returns a dict with:
      sections: [{id, type, file_path, ok}]
      page_imports: list of import statements for app/page.jsx
      page_renders: list of <Component /> tags in render order
    """
    # Phase-0 ships MarketingHeader + MarketingFooter as layout-level
    # components reading from landing.json. Generating a per-section
    # HeaderSection / FooterSection here would just render a duplicate
    # below the page content, so we skip those types upfront.
    _LAYOUT_TYPES = {"header", "marketing_header", "navbar", "footer", "marketing_footer", "site_footer"}
    sections = [
        s for s in (brief.get("sections") or [])
        if (s.get("type") or "").lower() not in _LAYOUT_TYPES
    ]
    brand_name = (brief.get("brand") or {}).get("name", "")
    motif = (brief.get("motif") or "minimal").strip().lower()
    palette = dict(brief.get("palette") or {})
    typography = dict(brief.get("typography") or {})
    design_system = dict(brief.get("design_system") or {})
    personality = dict(brief.get("personality") or {})
    references = list(brief.get("references") or [])
    # design_tokens — pre-computed Tailwind class literals derived from
    # design_system in landing_brief._build_design_tokens. Falls back here
    # in case the brief came from an older path that didn't compute them.
    design_tokens = dict(brief.get("design_tokens") or {})
    if not design_tokens:
        from app.services.landing_brief import _build_design_tokens
        design_tokens = _build_design_tokens(design_system)

    # Visual DNA — concrete cultural cues from research. Lead design
    # directive when present; codegen falls back to enums-only if absent.
    visual_dna = dict(brief.get("visual_dna") or {})

    # Voice/context fields populated by enrich_brief_with_signals when the
    # research stage succeeded. Absent on briefs built without research,
    # in which case the user prompt skips the VOICE & CONTEXT block.
    # purpose_directive carries the ===PURPOSE_DIRECTIVE=== block text
    # built from analyze_intent's output (named_roles, primary_purpose,
    # urgency_signals). Empty string when no purpose was detected — every
    # section prompt then skips the directive block.
    voice_context = {
        "voice_phrases":     brief.get("voice_phrases") or [],
        "industry_terms":    brief.get("industry_terms") or [],
        "regional_refs":     brief.get("regional_refs") or [],
        "white_space":       brief.get("white_space") or [],
        "purpose_directive": brief.get("purpose_directive") or "",
    }

    sem = asyncio.Semaphore(concurrency)

    total = len(sections)
    async def _bounded(idx: int, s: dict[str, Any]) -> tuple[int, dict[str, Any], dict[str, str] | None]:
        async with sem:
            try:
                res = await _generate_one_section(
                    s,
                    _pick_siblings(sections, idx),
                    brand_name=brand_name,
                    motif=motif,
                    palette=palette,
                    typography=typography,
                    design_system=design_system,
                    personality=personality,
                    references=references,
                    design_tokens=design_tokens,
                    section_index=idx,
                    section_count=total,
                    api_key=api_key,
                    websocket=websocket,
                    voice_context=voice_context,
                    visual_dna=visual_dna,
                )
            except Exception as exc:
                logger.warning("section %s: unhandled exception in _bounded — %s", s.get("id"), exc)
                res = None
            return idx, s, res

    if websocket is not None:
        try:
            await websocket.send_json({
                "type": "progress",
                "message": f"⚡ Generating {len(sections)} sections in parallel...",
            })
        except Exception:
            pass

    results = await asyncio.gather(
        *(_bounded(i, s) for i, s in enumerate(sections)),
        return_exceptions=True,
    )
    # Restore original order — filter out any stray BaseException results defensively
    valid_results = [r for r in results if not isinstance(r, BaseException)]
    if len(valid_results) < len(results):
        logger.error("generate_landing_sections: %d section(s) raised unhandled exceptions", len(results) - len(valid_results))
    valid_results.sort(key=lambda x: x[0])

    sections_meta: list[dict[str, Any]] = []
    page_imports: list[str] = []
    page_renders: list[str] = []

    sections_dir = os.path.join(workspace_path, "src", "components", "sections")
    os.makedirs(sections_dir, exist_ok=True)

    for _, section, res in valid_results:
        filename = _section_filename(section)
        component = _component_name(filename)
        file_path = f"src/components/sections/{filename}"
        ok = bool(res)

        if ok:
            disk_path = os.path.join(workspace_path, file_path)
            os.makedirs(os.path.dirname(disk_path), exist_ok=True)
            with open(disk_path, "w", encoding="utf-8") as fh:
                fh.write(res["content"])
            page_imports.append(
                f'import {component} from "@/components/sections/{component}";'
            )
            page_renders.append(f"<{component} />")
        else:
            logger.warning("section %s (%s): codegen FAILED — skipping", section.get("id"), section.get("type"))

        sections_meta.append({
            "id": section.get("id"),
            "type": section.get("type"),
            "file_path": file_path,
            "component": component,
            "ok": ok,
            "fallback": bool(res and res.get("fallback")),
        })

    if websocket is not None:
        ok_count = sum(1 for m in sections_meta if m["ok"])
        fallback_count = sum(1 for m in sections_meta if m.get("fallback"))
        suffix = f" ({fallback_count} fallback)" if fallback_count else ""
        try:
            await websocket.send_json({
                "type": "progress",
                "message": f"✅ {ok_count}/{len(sections_meta)} sections generated{suffix}",
            })
        except Exception:
            pass

    return {
        "sections": sections_meta,
        "page_imports": page_imports,
        "page_renders": page_renders,
    }


# ── Layout components (MarketingHeader + MarketingFooter) ─────────────

def _layout_system_prompt(
    component_name: str,
    file_path: str,
    archetype_label: str,
    anatomy: str,
    brand_name: str,
    motif: str,
    palette: dict,
    typography: dict,
    design_system: dict,
    personality: dict,
    references: list[dict],
    design_tokens: dict | None = None,
    visual_dna: dict | None = None,
    brand: dict | None = None,
    category: str = "",
) -> str:
    """System prompt for header/footer codegen — anatomy-driven, JSON-fed."""
    palette_lines = "\n".join(f"  --{k}: {v};" for k, v in palette.items())
    ds = design_system or {}
    dt = design_tokens or {}
    pers = personality or {}
    vd = visual_dna or {}
    b = brand or {}
    vibe = ", ".join(pers.get("vibe_keywords") or [])
    pers_block = (
        f"\nPERSONALITY (tone the layout to match this voice):\n"
        f"  Tone: {pers.get('tone', 'confident')}\n"
        f"  Vibe: {vibe or 'modern, clear'}\n"
        f"  Energy: {pers.get('energy', 'medium')}\n"
    )

    # Always-present brand context block. When visual_dna comes back empty
    # (Gemini call failure), this is the only source of per-brand variation
    # that Claude sees — without it every fallback footer ends up looking
    # the same generic 4-col template.
    brand_context_lines = []
    if b.get("tagline"):
        brand_context_lines.append(f"  Tagline: {b['tagline']}")
    desc = (b.get("description") or "").strip()
    if desc:
        # Cap to keep the prompt tight; Claude only needs the gist.
        brand_context_lines.append(f"  Description: {desc[:240]}")
    if category:
        brand_context_lines.append(f"  Category: {category}")
    info = b.get("business_info") or {}
    if isinstance(info, dict) and info:
        info_keys = [k for k in ("address", "phone", "email", "hours", "city") if info.get(k)]
        if info_keys:
            brand_context_lines.append(
                f"  Business info available in landing.brand.business_info: {', '.join(info_keys)} "
                f"(SURFACE these in the footer when the anatomy has room — phone as tel:, email as mailto:, address inline)"
            )
    social = b.get("social") or []
    if social:
        brand_context_lines.append(
            f"  Social handles available in landing.brand.social: {len(social)} entries (render as icon-only links)"
        )
    brand_block = ""
    if brand_context_lines:
        brand_block = "\nBRAND CONTEXT (always-on — use to vary the look even when visual_dna is empty):\n" + "\n".join(brand_context_lines) + "\n"

    # Compact visual_dna block for header/footer — header is small, doesn't
    # need the full per-section flavors, just enough to flavor the brand
    # mark, nav style, and footer mood.
    visual_dna_block = ""
    if vd:
        intensity = (vd.get("cultural_intensity") or "bold").strip().lower()
        motifs = (vd.get("decorative_motifs") or [])[:3]
        type_voice = (vd.get("typography_voice") or "").strip()
        palette_emph = (vd.get("cultural_palette_emphasis") or "").strip()
        parts = [
            "\nVISUAL DNA (apply to brand mark, nav typography, and footer mood — accent positions, not full takeover):",
            f"  Intensity: {intensity}",
        ]
        if palette_emph:
            parts.append(f"  Palette emphasis: {palette_emph}")
        if type_voice:
            parts.append(f"  Typography voice: {type_voice}")
        if motifs:
            parts.append("  Decorative cues you may use sparingly (logo lockup accent, footer divider, social-icon row treatment):")
            for m in motifs:
                parts.append(f"    • {m}")
        parts.append(
            "  The header/footer carries the brand identity quietly — don't overload them. "
            "One brand-mark accent + one nav-type voice + restraint everywhere else."
        )
        visual_dna_block = "\n".join(parts) + "\n"
    ref_block = ""
    if references:
        rls = []
        for r in references[:3]:
            n = r.get("name") or r.get("url", "")
            why = r.get("why", "")
            rls.append(f"  • {n} — {why}")
        if rls:
            ref_block = "\nREFERENCE SITES (real sites this brief is grounded in):\n" + "\n".join(rls) + "\n"

    # Anatomy framing — research-grounded specs are authoritative; fallback
    # skeletons are floors that visual_dna composes on top of. Fallback
    # variants (e.g. "fallback:cta-band") are called out by name so Claude
    # implements the SHAPE described in the anatomy rather than defaulting
    # to its training-data instinct ("every footer is a 4-column megacolumn").
    label = (archetype_label or "").strip()
    if label.startswith("research"):
        anatomy_intro = "IMPLEMENT THIS EXACT SPEC (research-grounded for this brand)"
        anatomy_outro = (
            "Tailwind class choices and decorative details are yours, but the structural skeleton, "
            "scroll behavior, and decorative integration above are the spec. This anatomy was written "
            "from real research about THIS brand."
        )
    elif label.startswith("fallback:"):
        variant_name = label.split(":", 1)[1]
        anatomy_intro = (
            f"IMPLEMENT THIS SHAPE — variant: '{variant_name}'. "
            f"This shape was chosen for THIS brand's personality and category. "
            f"Do NOT silently swap to another footer pattern (a 4-col megacolumn is NOT a centered-stack, "
            f"a CTA-band is NOT a minimalist-row)"
        )
        anatomy_outro = (
            f"You MUST follow the '{variant_name}' structural floor above. "
            "Compose the visual look (decorative motifs, surface treatment, typography flavor) "
            "from the VISUAL DNA + BRAND CONTEXT blocks below — but the SHAPE is fixed."
        )
    else:
        anatomy_intro = "STARTING POINT (generic fallback — no research-grounded anatomy was produced for this layout)"
        anatomy_outro = (
            "This is a STRUCTURAL FLOOR. Use the floor's scroll behavior, container hierarchy, and "
            "responsive bones as invariants — but compose the actual look (brand mark style, nav "
            "typography, social row treatment, footer accent) from the VISUAL DNA block above."
        )

    return f"""You are a senior front-end engineer writing ONE Next.js layout component (header OR footer) for a landing page.

OUTPUT — ONE file via the write_project_files tool:
  • path: {file_path}
  • content: full source ready to import

MANDATORY RULES
1. The component reads its content from `@/content/landing.json` — never hardcode brand name, links, or copy. Pattern:
     import landing from "@/content/landing.json";
     const brand = landing.brand;
     const nav = landing.nav || [];     // header only
     const footer = landing.footer || {{}};   // footer only
     const cta = landing.ctas?.primary;
     const social = brand.social || [];
     const info = brand.business_info || {{}};
2. Use TAILWIND CLASSES ONLY. NEVER use the `style={{}}` prop on any element — not for colors, not for fonts, not for spacing, not for anything. The ONLY exception is `style={{ backgroundImage: `url(...)` }}` when applying a dynamic image. For fonts: brand wordmark, headlines, and any serif/display copy use the Tailwind class `font-[family-name:var(--font-heading)]`. Body / nav / button text inherits the body font from `<body>` automatically — do NOT re-declare it. NEVER write `style={{ fontFamily: ... }}` — that ships a hardcoded family name that paints UNDER the next/font CSS variable and produces a visible double-rendered text artifact (regular + serif overlapping). The `typography.heading_font` value below is INFORMATIONAL ONLY (it tells you what font is loaded as `--font-heading`); never embed the literal name in JSX.
3. Default-export a React function named `{component_name}` (matching filename).
4. Mark `'use client';` as the FIRST line if you use useState / useEffect / onClick.
5. Lucide-react icons for social (Instagram, Twitter, Facebook, Linkedin, Youtube, Github) and any UI affordances (Menu, X, ChevronDown). Map social.label string → icon via a small const dict.
6. NO CSS modules, NO styled-components, NO dynamic class strings Tailwind can't parse.
7. BORDER-RADIUS IS MANDATORY. Buttons, CTAs, and pill-style nav items use the
   PROJECT_DESIGN_TOKENS button_radius_class (below). Mobile-menu icon buttons use the same.
   Newsletter input/email-capture inputs use `rounded-md`. Logo lockup container `rounded-md`
   if it has a background color, no radius if it's transparent. NEVER use `rounded-none`
   on any header/footer element.

ANATOMY — {anatomy_intro}:
{anatomy}
{anatomy_outro}

INTERACTIVITY (REQUIRED)
  • Sticky/fixed headers: useEffect listens to window.scrollY → setScrolled(true) past 8px → flips classes (transparent → solid w/ backdrop-blur). NO exceptions on mobile-only headers.
  • Mobile menu: useState `open`. Hamburger button toggles. ESC closes. Click outside closes (use a backdrop div with onClick).
  • Mega-menu: useState tracks open panel by index. Hover or click opens. Esc / clicking another link closes.
  • Footer newsletter form (if applicable): useState for email + submitted; client-side email validation; success state.
  • Every <Link> / <a> MUST resolve to a valid in-page anchor (`#features`, `#contact`) or external URL — never `href="#"` placeholders. Use anchors derived from `landing.nav` and `landing.footer.links`.
  • aria-label on all icon-only buttons; aria-expanded on toggles.

DESIGN CONTEXT
  Brand: {brand_name}
  Motif: {motif}
  Heading font: {typography.get("heading_font", "Inter")}
  Body font:    {typography.get("body_font", "Inter")}
  Palette (already wired as CSS vars in globals.css):
{palette_lines}
  Motion: {ds.get("motion", "subtle")}
  Accent shape: {ds.get("accent_shape", "rounded")}
  Surface: {ds.get("surface", "elevated")}

PROJECT_DESIGN_TOKENS — USE THESE EXACT TAILWIND CLASS STRINGS VERBATIM in the layout (do NOT improvise alternates):
  • Button / pill / CTA border-radius:   {dt.get("button_radius_class", "rounded-md")}
  • Standard transition for hover:       {dt.get("transition_class", "transition-all duration-300")}
  Examples:
    <Link className="{dt.get("button_radius_class", "rounded-md")} bg-primary text-primary-foreground px-5 py-2.5 {dt.get("transition_class", "transition-all duration-300")} hover:opacity-90">
    <button aria-label="Open menu" className="{dt.get("button_radius_class", "rounded-md")} p-2 {dt.get("transition_class", "transition-all duration-300")} hover:bg-muted">
{brand_block}{visual_dna_block}{pers_block}{ref_block}

CONTRAST & READABILITY (NON-NEGOTIABLE)
  • Nav links: `text-foreground/80 hover:text-foreground` on solid header surfaces; on transparent-pill / floating-glass
    headers add `backdrop-blur-md bg-background/80` to the pill so links remain readable over any photo behind. Never put
    `text-white` on a transparent header that sits above a light hero image.
  • Header CTA button: `bg-primary text-primary-foreground` (solid). On a transparent-pill, the CTA still uses the SAME
    solid pill chip — never a low-opacity outline that disappears on light photos.
  • The header MUST remain readable when scrolled past the hero (where the page surface is `bg-background`, light): when
    the user scrolls past 8px, swap to a SOLID surface (`bg-background/95 backdrop-blur` + `border-b border-border`) so
    the navigation never becomes invisible.
  • Footer link text: `text-muted-foreground hover:text-foreground` (full opacity). Footer headings: `text-foreground`.
  • DARK FOOTER SURFACES — when the footer wrapper uses `bg-foreground`,
    `bg-secondary` (dark brand color), or any dark `bg-*`:
      ✗ NEVER `text-foreground` or `text-muted-foreground` (invisible — same hue as bg).
      ✗ NEVER `text-primary-foreground` (only pairs with `bg-primary`, NOT secondary).
      ✓ Body / nav links: `text-background hover:text-background/80`.
      ✓ Muted descriptions (newsletter sublabel, copyright): `text-background/70`.
      ✓ Headings (column labels): `text-background` full opacity.
      ✓ Newsletter input: `bg-background/10 text-background placeholder:text-background/50 border-background/20`.
      ✓ Section divider line: `border-background/15`.
    This is the #1 footer failure: white-on-white or brown-on-brown text
    because the writer reached for `text-foreground` / `text-muted-foreground`
    on an inverse surface. Always invert text colors on inverse surfaces.

QUALITY BAR
  • Looks like a real, professional layout for this brand — not a generic template.
  • Real interactivity (state + handlers), not stubs.
  • Pixel-clean spacing (gap-6 / gap-8 / py-3 / py-4 / h-14 / h-16, not random values).
  • Header: max 6 nav links, each 1 short word. The nav array passed in is already short — DO NOT repeat words or
    re-expand them ("Stays" stays "Stays", never "Accommodations Showcase").
"""


def _layout_user_prompt(
    section: dict[str, Any],
    component_name: str,
    file_path: str,
) -> str:
    return f"""Build ONE layout component.

FILE PATH:    {file_path}
COMPONENT:    {component_name}
ARCHETYPE:    (see anatomy in system prompt)

DERIVED CONTEXT (read at runtime from landing.json — your component must do this, do NOT inline):
  • landing.brand               — {{name, tagline, description, business_info, social}}
  • landing.nav                 — [{{label, href}}] for the header
  • landing.footer              — {{brand, links: [{{label, href}}]}} for the footer
  • landing.ctas.primary        — {{label, href}} for the CTA button

Generate the component now. Output via write_project_files with exactly ONE file."""


async def _generate_layout_component(
    *,
    kind: str,  # "header" | "footer"
    archetype_label: str,
    anatomy: str,
    brand_name: str,
    motif: str,
    palette: dict,
    typography: dict,
    design_system: dict,
    personality: dict,
    references: list[dict],
    design_tokens: dict,
    api_key: str,
    websocket: Any,
    visual_dna: dict | None = None,
    brand: dict | None = None,
    category: str = "",
) -> dict[str, str] | None:
    from app.services.project_generator import call_claude_for_json

    component = "MarketingHeader" if kind == "header" else "MarketingFooter"
    file_path = f"src/components/layout/{component}.jsx"

    sys_p = _layout_system_prompt(
        component, file_path, archetype_label, anatomy,
        brand_name, motif, palette, typography, design_system, personality, references,
        design_tokens=design_tokens,
        visual_dna=visual_dna,
        brand=brand,
        category=category,
    )
    usr_p = _layout_user_prompt({}, component, file_path)

    # One retry per layout component, mirroring the section retry. Header
    # and footer falling back to deterministic stubs is the OLD behavior;
    # giving Claude one more shot at producing a real component is cheaper
    # than the user seeing a generic stub.
    max_attempts = 2
    last_failure_reason = "unknown"

    for attempt in range(1, max_attempts + 1):
        try:
            result = await asyncio.wait_for(
                call_claude_for_json(
                    system_prompt=sys_p,
                    user_prompt=usr_p,
                    api_key=api_key,
                    websocket=websocket,
                    max_tokens=_SECTION_MAX_TOKENS,
                ),
                timeout=90.0,
            )
        except asyncio.TimeoutError:
            last_failure_reason = "timeout after 90s"
            logger.warning(
                "layout %s: codegen timed out on attempt %d/%d — falling back to stub",
                kind, attempt, max_attempts,
            )
            continue
        except Exception as exc:
            last_failure_reason = f"exception: {exc}"
            logger.warning(
                "layout %s: codegen attempt %d/%d threw — %s",
                kind, attempt, max_attempts, exc,
            )
            continue

        if not result or not result.get("files"):
            last_failure_reason = "empty result"
            logger.warning(
                "layout %s: codegen attempt %d/%d returned empty result",
                kind, attempt, max_attempts,
            )
            continue

        saw_jsx = False
        for f in result["files"]:
            path = (f.get("path") or "").strip()
            content = (f.get("content") or "")
            if path.endswith((".jsx", ".tsx")) and content:
                saw_jsx = True
                valid, reason = _layout_content_looks_valid(content, kind, component)
                if not valid:
                    last_failure_reason = f"contract validation failed: {reason}"
                    logger.warning(
                        "layout %s: codegen attempt %d/%d failed contract validation — %s",
                        kind, attempt, max_attempts, reason,
                    )
                    break
                if attempt > 1:
                    logger.info("layout %s: succeeded on retry (attempt %d)", kind, attempt)
                return {"path": file_path, "content": content}

        if not saw_jsx:
            last_failure_reason = "no .jsx/.tsx file in result"
        logger.warning(
            "layout %s: codegen attempt %d/%d did not produce an acceptable component — %s",
            kind, attempt, max_attempts, last_failure_reason,
        )

    logger.error(
        "layout %s: codegen FAILED after %d attempts — last failure: %s",
        kind, max_attempts, last_failure_reason,
    )
    return None


async def generate_layout_components(
    *,
    brief: dict[str, Any],
    workspace_path: str,
    api_key: str,
    websocket: Any = None,
) -> dict[str, bool]:
    """Generate MarketingHeader.jsx + MarketingFooter.jsx in parallel.

    Writes the two files into `src/components/layout/`. Returns a dict
    {header: bool, footer: bool} indicating success per component. Failures
    are logged but non-fatal — Phase-0 already wrote archetype-matched stub
    files so the layout import never 404s.
    """
    brand = dict(brief.get("brand") or {})
    brand_name = brand.get("name", "")
    category = (brief.get("category") or "").strip()
    motif = (brief.get("motif") or "minimal").strip().lower()
    palette = dict(brief.get("palette") or {})
    typography = dict(brief.get("typography") or {})
    design_system = dict(brief.get("design_system") or {})
    personality = dict(brief.get("personality") or {})
    references = list(brief.get("references") or [])
    design_tokens = dict(brief.get("design_tokens") or {})
    if not design_tokens:
        from app.services.landing_brief import _build_design_tokens
        design_tokens = _build_design_tokens(design_system)
    visual_dna = dict(brief.get("visual_dna") or {})

    # Resolve header + footer anatomies via the same pipeline used for sections:
    # research first (visual_dna.section_anatomies), fallback skeleton second.
    # Pass the brief so the footer fallback picker can pick a variant by
    # personality / category instead of always returning the same shape.
    header_anatomy, header_source = _resolve_anatomy("header", visual_dna, brief=brief)
    footer_anatomy, footer_source = _resolve_anatomy("footer", visual_dna, brief=brief)

    if websocket is not None:
        try:
            await websocket.send_json({
                "type": "progress",
                "message": f"⚡ Generating header ({header_source}) + footer ({footer_source}) in parallel...",
            })
        except Exception:
            pass

    header_task = _generate_layout_component(
        kind="header",
        archetype_label=header_source,
        anatomy=header_anatomy,
        brand_name=brand_name, motif=motif, palette=palette, typography=typography,
        design_system=design_system, personality=personality, references=references,
        design_tokens=design_tokens,
        api_key=api_key, websocket=websocket,
        visual_dna=visual_dna,
        brand=brand, category=category,
    )
    footer_task = _generate_layout_component(
        kind="footer",
        archetype_label=footer_source,
        anatomy=footer_anatomy,
        brand_name=brand_name, motif=motif, palette=palette, typography=typography,
        design_system=design_system, personality=personality, references=references,
        design_tokens=design_tokens,
        api_key=api_key, websocket=websocket,
        visual_dna=visual_dna,
        brand=brand, category=category,
    )

    header_res, footer_res = await asyncio.gather(header_task, footer_task, return_exceptions=True)
    if isinstance(header_res, BaseException):
        logger.error("generate_layout_components: header threw — %s", header_res)
        header_res = None
    if isinstance(footer_res, BaseException):
        logger.error("generate_layout_components: footer threw — %s", footer_res)
        footer_res = None

    layout_dir = os.path.join(workspace_path, "src", "components", "layout")
    os.makedirs(layout_dir, exist_ok=True)

    out: dict[str, bool] = {"header": False, "footer": False}

    for kind, res in (("header", header_res), ("footer", footer_res)):
        if not res:
            logger.warning("layout %s: codegen FAILED — pipeline will fall back to deterministic stub", kind)
            continue
        disk = os.path.join(workspace_path, res["path"])
        os.makedirs(os.path.dirname(disk), exist_ok=True)
        with open(disk, "w", encoding="utf-8") as fh:
            fh.write(res["content"])
        out[kind] = True
        logger.info("layout %s: wrote %s (%d bytes)", kind, disk, len(res["content"]))

    if websocket is not None:
        ok = sum(1 for v in out.values() if v)
        try:
            await websocket.send_json({
                "type": "progress",
                "message": f"✅ {ok}/2 layout components generated",
            })
        except Exception:
            pass

    return out


_FIXED_TOP_HEADER_PATTERN = re.compile(
    r"\bfixed\s+top-0\b|\bposition:\s*fixed\b|\bfloating\s+(glass\s+)?pill\b",
    re.IGNORECASE,
)


def _header_is_fixed_top(header_anatomy: str) -> bool:
    """Detect whether the resolved header anatomy describes a fixed-top header
    (which floats over content and needs the page to add pt compensation).

    Matches `fixed top-0`, `position: fixed`, or "floating pill" phrasing in
    the anatomy text. Sticky/in-flow headers don't match.
    """
    return bool(header_anatomy and _FIXED_TOP_HEADER_PATTERN.search(header_anatomy))


def write_landing_page_shell(
    workspace_path: str,
    page_imports: list[str],
    page_renders: list[str],
    *,
    header_anatomy: str = "",
) -> str:
    """Write `app/page.jsx` that imports + renders all section components in order.

    The shell is dead-simple — no header/footer here (those are layout-level
    components written by Phase 0 builders). Sections render top-to-bottom.

    `header_anatomy` is the resolved header anatomy text (research-grounded or
    fallback). When it describes a fixed-top header (`fixed top-0`, floating
    pill), <main> gets `pt-20 lg:pt-24` so content doesn't slide under the
    floating header. Sticky/in-flow headers stay padding-free.
    """
    app_dir = os.path.join(workspace_path, "src", "app")
    os.makedirs(app_dir, exist_ok=True)
    # Drop skeleton page.js so Next.js doesn't see two route entrypoints.
    for stale in ("page.js", "page.tsx"):
        sp = os.path.join(app_dir, stale)
        if os.path.exists(sp):
            try:
                os.remove(sp)
            except OSError:
                pass
    # Remove ALL route groups (e.g. `(marketing)`) — the Next.js template ships
    # `src/app/(marketing)/page.js` which resolves to "/" and conflicts with
    # the page.jsx we just wrote at the root. Vercel's build then fails with:
    #   ENOENT: ... `.next/server/app/(marketing)/page_client-reference-manifest.js`
    # because the duplicate route doesn't get a clean compile pass.
    import shutil as _shutil
    try:
        for entry in os.listdir(app_dir):
            full = os.path.join(app_dir, entry)
            if (
                os.path.isdir(full)
                and entry.startswith("(")
                and entry.endswith(")")
            ):
                _shutil.rmtree(full, ignore_errors=True)
                logger.info("write_landing_page_shell: removed conflicting route group %s", entry)
    except OSError as _route_err:
        logger.warning("write_landing_page_shell: route-group cleanup failed (non-fatal): %s", _route_err)

    target = os.path.join(app_dir, "page.jsx")
    imports_block = "\n".join(page_imports)
    renders_block = "\n      ".join(page_renders) if page_renders else "<div />"
    needs_top_pad = _header_is_fixed_top(header_anatomy)
    main_class = "min-h-screen bg-background text-foreground"
    if needs_top_pad:
        main_class += " pt-20 lg:pt-24"
    src = f"""{imports_block}

export default function Page() {{
  return (
    <main className="{main_class}">
      {renders_block}
    </main>
  );
}}
"""
    with open(target, "w", encoding="utf-8") as fh:
        fh.write(src)
    logger.info("write_landing_page_shell: wrote %s with %d sections", target, len(page_renders))
    return target
