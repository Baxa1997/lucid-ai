"""Landing-page Stage 4-light — Research signal extraction.

Takes the 8 grounded markdown research dumps (4 domain + 4 design)
and distills them into structured signals consumable by the existing
Brief schema. Two parallel Flash calls; ~5-10s wall time.

This is the pragmatic middle ground between the original spec's
heavyweight Stage 4 + 5 synthesis (full content_strategy + design_system
schemas) and zero synthesis (just dumping raw markdown into Claude's
context). The goal is empirical: see whether research-grounded fields
improve generation quality before committing to more.

What we extract:
  • Domain signals — industry terms, audience phrases, regional refs,
    competitor section orders, top competitors, white-space angles.
  • Design signals — chosen typography pairing, chosen palette (8 HSL
    slots), dominant paradigm, hero pattern, per-section approaches,
    top reference URLs.

What we do NOT extract: anything Claude can derive from the raw brief
fields it already gets. We only pull signal that's currently missing.

Public surface:
  • ``extract_research_signals(intent, domain_research, design_research)``
        → dict[str, dict] with keys "domain" and "design".
  • ``enrich_brief_with_signals(brief, signals)``
        → mutated brief. Overrides typography / palette / archetype /
          adds voice_phrases + regional_refs. Returns the same dict.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any

from app.services.landing_gemini import structured_distill


_HSL_RE = re.compile(r"^\s*(\d{1,3}(?:\.\d+)?)\s+(\d{1,3}(?:\.\d+)?)%\s+(\d{1,3}(?:\.\d+)?)%\s*$")


def _hsl_saturation(hsl: str) -> float | None:
    """Parse an HSL string like '30 35% 45%' and return the saturation
    percentage (0-100). Returns None for unparseable input."""
    if not isinstance(hsl, str):
        return None
    m = _HSL_RE.match(hsl)
    if not m:
        return None
    try:
        return float(m.group(2))
    except Exception:
        return None


def _hsl_lightness(hsl: str) -> float | None:
    if not isinstance(hsl, str):
        return None
    m = _HSL_RE.match(hsl)
    if not m:
        return None
    try:
        return float(m.group(3))
    except Exception:
        return None


def _palette_mood(bg_l: float | None, prim_sat: float | None) -> str:
    """Single-word mood read from background lightness + primary saturation —
    purely for the log line so we can scan logs and see the design vibe."""
    if bg_l is None:
        bg_l = 90
    if prim_sat is None:
        prim_sat = 0
    if bg_l < 25:
        return "moody" if prim_sat >= 50 else "dark-neutral"
    if bg_l < 60:
        return "mid-tone"
    if prim_sat < 30:
        return "bland-saas"
    if prim_sat < 50:
        return "muted-warm"
    return "bold-warm"


def _log_picked_palette(palette: dict, *, source: str) -> None:
    """Log the picked palette in a single readable block so any generation
    can be diagnosed from logs alone. Also surfaces saturation warnings.

    Output shape (multi-line, one INFO log line):

        palette PICKED (source=gemini_research) name='Warm Tuscan Earth' mood=bold-warm
          primary    hsl( 15 65% 45%)  sat=65% lit=45%
          secondary  hsl(180 30% 35%)
          accent     hsl( 40 80% 55%)  sat=80%
          background hsl( 35 25% 96%)  sat=25% lit=96%   ← tinted neutral ✓
          foreground hsl( 20 30% 18%)
          muted      hsl( 30 20% 92%)
          border     hsl( 30 20% 85%)
          card       hsl( 40 30% 98%)
    """
    name = (palette.get("name") or "(unnamed)").strip()
    prim = (palette.get("primary") or "").strip()
    bg = (palette.get("background") or "").strip()
    prim_sat = _hsl_saturation(prim)
    bg_sat = _hsl_saturation(bg)
    bg_l = _hsl_lightness(bg)
    mood = _palette_mood(bg_l, prim_sat)

    def _tag(slot: str) -> str:
        hsl = (palette.get(slot) or "").strip()
        sat = _hsl_saturation(hsl)
        lit = _hsl_lightness(hsl)
        markers = []
        if slot == "primary" and sat is not None:
            markers.append(f"sat={sat:.0f}%")
            if sat < 40:
                markers.append("⚠ BLAND (sat<40%)")
        elif slot == "accent" and sat is not None:
            markers.append(f"sat={sat:.0f}%")
        elif slot == "background" and sat is not None:
            markers.append(f"sat={sat:.0f}%")
            if lit is not None:
                markers.append(f"lit={lit:.0f}%")
            if sat == 0 and lit in (0, 100):
                markers.append("⚠ PURE WHITE/BLACK")
            elif sat > 5:
                markers.append("← tinted ✓")
            if lit is not None and lit < 25:
                markers.append("← DARK BG (Bella Luna pattern)")
        return f"  {slot:<10} hsl({hsl:>14})" + (
            "   " + "  ".join(markers) if markers else ""
        )

    slots = ("primary", "secondary", "accent", "background", "foreground", "muted", "border", "card")
    body = "\n".join(_tag(s) for s in slots)
    logger.info(
        "palette PICKED (source=%s) name=%r mood=%s\n%s",
        source, name, mood, body,
    )

logger = logging.getLogger(__name__)


# ── Schemas (Gemini responseSchema) ──────────────────────────────────

_DOMAIN_SIGNALS_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "required": [
        "industry_terms",
        "audience_phrases",
        "regional_touchpoints",
        "competitor_section_orders",
        "top_competitors",
        "white_space",
    ],
    "properties": {
        "industry_terms": {
            "type": "ARRAY",
            "items": {"type": "STRING"},
        },
        "audience_phrases": {
            "type": "ARRAY",
            "items": {"type": "STRING"},
        },
        "regional_touchpoints": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "required": ["name", "context"],
                "properties": {
                    "name":    {"type": "STRING"},
                    "context": {"type": "STRING"},
                },
            },
        },
        "competitor_section_orders": {
            "type": "ARRAY",
            "items": {
                "type": "ARRAY",
                "items": {"type": "STRING"},
            },
        },
        "top_competitors": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "required": ["name", "url"],
                "properties": {
                    "name":        {"type": "STRING"},
                    "url":         {"type": "STRING"},
                    "positioning": {"type": "STRING"},
                    "tier":        {"type": "STRING"},
                },
            },
        },
        "white_space": {
            "type": "ARRAY",
            "items": {"type": "STRING"},
        },
    },
}


# Visual DNA — concrete cultural / category-specific visual cues.
#
# This is the answer to "every restaurant looks the same." The legacy
# design_signals schema compresses 50KB of grounded research into a few
# enum-ish fields (dominant_paradigm: "editorial-warm", motif: "luxe").
# By the time Claude sees them, all the *Chinese-vs-Italian* visual
# specificity has been erased — both projects look identically luxe.
#
# visual_dna preserves the cultural texture as RICH, CONCRETE strings
# Claude can act on directly: "ink-wash gradient as section divider",
# "lantern silhouette icon", "rice-paper grain over hero photo". No
# enum compression. No paradigm-name shorthand.
#
# `cultural_intensity` is a single dial Gemini sets per project — it
# decides whether the cultural cues sit in restrained accent positions
# ("subtle" — modern luxe with a red seal-stamp) or take over the page
# ("bold" — full-bleed rice-paper texture, calligraphy headers).
_VISUAL_DNA_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    # Only the STRUCTURAL fields are required — these every brand can
    # produce (section_anatomies = layout shape; cultural_intensity =
    # mode dial; layout_signature / typography_voice / palette_emphasis =
    # universal design framing). The ORNAMENTAL fields (decorative_motifs,
    # signature_textures, iconography_anchors, photography_style,
    # section_flavors) are made optional because demanding them from
    # restraint-required categories (enterprise SaaS, fintech, healthcare,
    # legal, etc.) was causing Gemini to return empty responses — the
    # model couldn't satisfy the schema and gave up. Bold/cultural brands
    # still populate all 10; restrained brands now populate the structural
    # 5 and skip ornaments. Verified by paired test: SaaS brief that
    # previously timed out now completes with the relaxed schema.
    "required": [
        "cultural_intensity",
        "cultural_palette_emphasis",
        "typography_voice",
        "layout_signature",
        "section_anatomies",
    ],
    "properties": {
        "decorative_motifs": {
            "type": "ARRAY",
            "items": {"type": "STRING", "maxLength": 100},
            "maxItems": 5,
        },
        "signature_textures": {
            "type": "ARRAY",
            "items": {"type": "STRING", "maxLength": 100},
            "maxItems": 3,
        },
        "iconography_anchors": {
            "type": "ARRAY",
            "items": {"type": "STRING", "maxLength": 40},
            "maxItems": 6,
        },
        "photography_style":         {"type": "STRING", "maxLength": 250},
        "layout_signature":          {"type": "STRING", "maxLength": 250},
        "cultural_palette_emphasis": {"type": "STRING", "maxLength": 250},
        "typography_voice":          {"type": "STRING", "maxLength": 250},
        "section_flavors": {
            "type": "OBJECT",
            "properties": {
                "hero":         {"type": "STRING"},
                "menu":         {"type": "STRING"},
                "gallery":      {"type": "STRING"},
                "story":        {"type": "STRING"},
                "philosophy":   {"type": "STRING"},
                "testimonials": {"type": "STRING"},
                "value_prop":   {"type": "STRING"},
                "reservations": {"type": "STRING"},
                "features":     {"type": "STRING"},
                "process":      {"type": "STRING"},
            },
        },
        # ── Per-section structural anatomy (research-driven) ──────────────
        # Concrete layout spec per section type — where things go, what
        # shapes, what scale, which decorative cues land where. Each entry
        # is 80-150 words written in the same format as a developer-facing
        # design spec. Codegen reads this VERBATIM as the section's
        # "ARCHETYPE" anatomy block (replacing the formerly-hardcoded
        # _HERO_ARCHETYPES / _MENU_ARCHETYPES / etc. libraries). Empty or
        # missing entries fall back to the minimal _FALLBACK_SKELETONS
        # in landing_section_codegen.py — pipeline never blocks on a
        # missing anatomy. Keys correspond to section.type values used
        # in the brief's sections array.
        # maxLength=250 on every anatomy is structural — Gemini enforces it
        # at the schema level so the model literally cannot pad. Solves the
        # runaway-generation bug where value_prop / pricing would balloon to
        # thousands of chars and break the JSON parse for other fields.
        "section_anatomies": {
            "type": "OBJECT",
            "properties": {
                "hero":         {"type": "STRING", "maxLength": 250},
                "menu":         {"type": "STRING", "maxLength": 250},
                "gallery":      {"type": "STRING", "maxLength": 250},
                "story":        {"type": "STRING", "maxLength": 250},
                "philosophy":   {"type": "STRING", "maxLength": 250},
                "testimonials": {"type": "STRING", "maxLength": 250},
                "value_prop":   {"type": "STRING", "maxLength": 250},
                "features":     {"type": "STRING", "maxLength": 250},
                "process":      {"type": "STRING", "maxLength": 250},
                "how_it_works": {"type": "STRING", "maxLength": 250},
                "press":        {"type": "STRING", "maxLength": 250},
                "team":         {"type": "STRING", "maxLength": 250},
                "pricing":      {"type": "STRING", "maxLength": 250},
                "faq":          {"type": "STRING", "maxLength": 250},
                "cta":          {"type": "STRING", "maxLength": 250},
                "stats":        {"type": "STRING", "maxLength": 250},
                "locations":    {"type": "STRING", "maxLength": 250},
                "reservation":  {"type": "STRING", "maxLength": 250},
                "contact":      {"type": "STRING", "maxLength": 250},
                "newsletter":   {"type": "STRING", "maxLength": 250},
                "header":       {"type": "STRING", "maxLength": 250},
                "footer":       {"type": "STRING", "maxLength": 250},
            },
        },
        "cultural_intensity": {
            "type": "STRING",
            "enum": ["subtle", "bold"],
        },
    },
}


_DESIGN_SIGNALS_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "required": [
        "chosen_typography",
        "chosen_palette",
        "dominant_paradigm",
        "hero_pattern",
        "per_section_approaches",
        "reference_urls",
    ],
    "properties": {
        "chosen_typography": {
            "type": "OBJECT",
            "required": ["heading_font", "body_font", "rationale"],
            "properties": {
                "heading_font": {"type": "STRING"},
                "body_font":    {"type": "STRING"},
                "rationale":    {"type": "STRING"},
            },
        },
        "chosen_palette": {
            "type": "OBJECT",
            "required": [
                "name", "primary", "secondary", "accent",
                "background", "foreground", "muted", "border", "card",
            ],
            "properties": {
                "name":       {"type": "STRING"},
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
        "dominant_paradigm": {"type": "STRING"},
        "hero_pattern": {
            "type": "OBJECT",
            "required": ["name", "rationale"],
            "properties": {
                "name":      {"type": "STRING"},
                "rationale": {"type": "STRING"},
            },
        },
        "per_section_approaches": {
            "type": "OBJECT",
            "properties": {
                "menu":         {"type": "STRING"},
                "gallery":      {"type": "STRING"},
                "story":        {"type": "STRING"},
                "testimonials": {"type": "STRING"},
                "features":     {"type": "STRING"},
                "value_prop":   {"type": "STRING"},
                "process":      {"type": "STRING"},
                "how_it_works": {"type": "STRING"},
            },
        },
        "reference_urls": {
            "type": "ARRAY",
            "items": {"type": "STRING"},
        },
    },
}


# ── Prompts ──────────────────────────────────────────────────────────

_VISUAL_DNA_EXTRACT_PROMPT = """You are extracting the VISUAL DNA — the concrete, culturally-specific visual elements — that distinguish a {category} ({subcategory}) brand serving {audience_primary} in {geo_specifics}.

Brand personality: {personality}
Tone: {tone}

Below are FOUR grounded research dumps. Use them as the primary source. Pull specifics from the research; do not invent generic SaaS-style elements. The output must look DIFFERENT from a generic "modern luxe" brand — every field should make this brand feel like THIS category in THIS region, not Stripe or Linear.

===VISUAL_RESEARCH===
{visual}

===TYPOGRAPHY_RESEARCH===
{typography}

===COLOR_RESEARCH===
{color}

===LAYOUT_RESEARCH===
{layout}

EXTRACT into JSON matching the schema. Every string must be CONCRETE and ACTIONABLE — Claude reads these directly and renders them as JSX.

• decorative_motifs (3-5 items): SPECIFIC decorative ELEMENTS this brand should use as visual accents. Each is a thing, not an adjective. Examples by category:
    Chinese fine dining: "red seal-stamp accent under headlines", "ink-wash gradient as section divider", "lantern silhouette as bullet marker"
    Italian trattoria:   "olive-branch hand-drawn flourish next to CTAs", "wood-grain hint on card edges", "chalkboard-script eyebrow tags"
    Spanish tapas bar:   "azulejo tile pattern band between sections", "small flamenco fan ornament next to menu sections", "warm sun-rays radiating behind hero headline"
    SaaS fintech:        "thin gradient ring around stat numbers", "monospace ticker line under hero", "subtle grid mesh background"
  Avoid generic items ("rounded corners", "drop shadow") — every motif must reference the brand's actual cultural / category context.

• signature_textures (2-3 items): textural backgrounds or surface treatments that fit this brand. Be concrete: "rice-paper grain over hero photo at 8% opacity", "azulejo tile band as section bg", "wood-grain hint on cards via bg-[url(/textures/wood.svg)]". If no specific texture fits, return an empty array — do NOT pad with "subtle gradient".

• iconography_anchors (4-6 items): culturally-resonant ICONOGRAPHY this brand should reference. NOT Lucide icon names — IDEAS for icons that match the cultural context. Examples:
    Chinese: "lantern", "tea cup", "jade dot", "bamboo stem", "calligraphy stroke"
    Italian: "olive branch", "wine glass", "pasta swirl", "tomato outline", "pizza-peel silhouette"
    Spanish: "olive", "jamón leg outline", "sherry glass", "fan", "flamenco-frill line"

• photography_style (1-2 sentences): the dominant photography mood. Be specific about subject, lighting, and tone.
    Example (Chinese): "Communal round tables with shared dishes, lantern-warm lighting, steam rising off bowls, intimate dim ambiance — never bright commercial product shots."
    Example (Italian): "Hand-tossed dough on flour-dusted wood, golden hour light through restaurant windows, vineyards at dusk, simple white plates with vivid pasta colors."

• layout_signature (1-2 sentences): the LAYOUT idiom this brand should commit to.
    Example (Chinese fine dining): "Asymmetric balance with generous negative space (the 'ma' principle), vertical reading rhythm, off-center hero composition, restrained content density."
    Example (Italian trattoria): "Confident left-aligned hero, hand-drawn accents in margins, slightly imperfect spacing that reads warm/handcrafted, blackboard-style menu typography."

• cultural_palette_emphasis (1 sentence): which 2-3 colors should dominate the visual experience and why.
    Example: "Red lacquer (primary) + gold leaf (accent) + jade green (secondary) — symbolizing prosperity and ceremonial luxury in Chinese fine dining tradition."

• typography_voice (1-2 sentences): what the TYPE should FEEL like, beyond just font names.
    Example (Chinese): "Brush-stroke serifs evoking calligraphy in headings, paired with confident clean sans for body. Eyebrow tags use uppercase tracking-widest for ceremonial precision."
    Example (Italian trattoria): "Warm humanist serifs for headings (Cormorant or Crimson Pro), italic accent words for menu names, hand-drawn script eyebrows."

• section_flavors: per-section concrete cultural notes Claude should apply when generating that section. Each is 1 sentence describing how to flavor that specific section type.
    hero:         "...specific hero treatment for this brand..."
    menu:         "...how the menu should feel culturally..."
    gallery:      "...what the gallery should evoke..."
    story / philosophy / value_prop / testimonials / reservations: same pattern.
  Skip section types not relevant to this brand's archetype.

• section_anatomies: per-section STRUCTURAL anatomy spec — ONE PARAGRAPH per section, MAX 25 words / 150 characters. Describe WHERE things go, what shapes, what scale tokens, and which decorative cues land where. Format like a tight developer note. NEVER repeat sentences or pad with rationale. Each anatomy MUST include:
    1. Outer <section> structural rules (overflow-hidden when decorative bleeds present, min-h, padding tokens).
    2. Composition: column count / grid shape / asymmetry / where copy and media land.
    3. Concrete decorative integration: which 1-2 motifs / textures / iconography_anchors from above appear, and exactly where (eyebrow ornament, divider, image-frame border, hero overlay, card edge, etc.).
    4. Typography scale (e.g. text-5xl md:text-6xl lg:text-7xl on h1) tied to typography_voice.
    5. Photography treatment when applicable (overlay, duotone, mask, etc.).
  Per-section guidance:
    hero:         dominant pattern (full-bleed photo + overlay / oversized headline + product image / asymmetric split / etc.) chosen from research, not a generic SaaS centered hero.
    menu:         photo cards vs editorial dotted-leader vs categorized rows — pick what fits cultural_intensity + cuisine. Include category-header treatment + price-line shape.
    gallery:      grid shape (asymmetric 12-col, bento mosaic, marquee scroll) + tile aspect rotation + caption treatment.
    testimonials: card style (glass-on-photo / marquee / big-quote-portrait) + star treatment + avatar shape.
    features:     icon-grid vs split-image-bullets vs numbered-stepper vs editorial-numbered-list — pick from layout_research.
    story:        narrative split (asymmetric image-left / quote-dominant / timeline) — pick what fits the brand's voice.
    press:        FLAT logo strip + pull-quote (NEVER overlapping rotated cards).
    cta / process / stats / faq / pricing / locations / reservation / contact / newsletter: include only if the section appears in this brief; otherwise skip.
    header:       (top-bar archetype) sticky-bar | transparent-pill | centered-logo | mega-menu | side-rail. Pick what fits the brand category. Specify scroll behavior.
    footer:       (footer archetype) mega-columns | minimalist-row | cta-band | centered-stack. Pick what fits brand voice. Specify column structure + social row.
  Skip section types not relevant to this brand. Each anatomy is COMPLETE in itself — Claude reads it as the spec without seeing the others.

• cultural_intensity: ONE word — "subtle" or "bold". DEFAULT IS "bold". Pick "subtle" only when the category genuinely requires visual restraint.
    "bold"   (DEFAULT) = cultural textures take real real-estate (rice-paper or fabric texture behind sections, calligraphy/script-flavored display type for headlines, full-bleed cultural patterns or decorative motifs as section dividers, signature color used confidently across multiple surfaces — not just in accents). Page reads UNMISTAKABLY of its category and culture, not "generic SaaS template with brand name swapped in." This is the right answer for restaurants, cafes, hotels, resorts, spas, fashion, retail, beauty, fitness, wellness, lifestyle, hospitality, food/beverage, travel, cultural institutions, creative agencies, studios, music, entertainment, religious/ceremonial, weddings, events, real estate (luxury/boutique), language schools, education with cultural identity, and any brand whose appeal is sensory/aesthetic/cultural rather than purely functional.
    "subtle" = cultural cues sit in accent positions only (a small motif under a headline, a single textural detail). Modern restraint dominates. Pick this ONLY for: enterprise SaaS, B2B tools, fintech, healthcare/clinics, legal/law firms, accounting/audit, insurance, funeral services, government/civic, security/compliance, and similar categories where the audience expects visual restraint and "trustworthy/conservative" reads as a feature.
  Brand's tone is {tone} and personality is {personality}. If the category isn't on the "subtle" list above, pick "bold" — being timid with cultural identity produces a generic page that fails to feel like the brand. The whole point of grounding research in real reference sites and visual DNA is to make THIS brand look like ITSELF, not like every other landing page.

OUTPUT BUDGET — be COMPACT but section_anatomies needs room. Whole JSON should fit in ~6000 chars. Concretely:
  • Each string field (photography_style, layout_signature, cultural_palette_emphasis, typography_voice): 1-2 sentences, MAX ~200 chars each.
  • decorative_motifs: 3-5 items. Each item ≤80 chars.
  • signature_textures: 2-3 items. Each item ≤80 chars.
  • iconography_anchors: 4-6 items. Each item is a SHORT noun (≤25 chars: "lantern", "tea cup", "olive branch") — no descriptions.
  • section_flavors: 1 sentence per section type, MAX ~150 chars each. Skip section types not relevant.
  • section_anatomies: 20-25 words PER section, ~150 chars MAX each. 5-7 entries total. THREE MANDATORY KEYS — `hero`, `header`, `footer`. Pick footer variant: minimalist-row | mega-columns | cta-band | centered-stack. Other 2-4 entries from: menu, gallery, story, testimonials, features, pricing, cta, value_prop.

ANTI-RUNAWAY RULES (CRITICAL):
- NEVER repeat the same sentence or paraphrase twice in any anatomy.
- NEVER pad with phrases like "The section is designed to...", "The overall effect...", "This creates...".
- If you find yourself writing more than 150 chars on one anatomy, STOP that anatomy NOW and move to the next.
- The JSON closing brace `}}` MUST appear within 2500 tokens. If you're approaching the limit, OUTPUT THE CLOSING BRACE IMMEDIATELY.
HARD LIMIT: entire JSON must fit in 2500 tokens. Compact > complete > verbose.

OUTPUT: just the JSON. No markdown wrapper. Every string concrete and actionable — no abstract design jargon, no SaaS-flavored boilerplate."""


_DOMAIN_EXTRACT_PROMPT = """You are distilling 4 grounded research dumps about a {category} business in {geo_specifics} into a structured JSON object.

Audience: {audience_primary}

Below are the FOUR research dumps. They cite real URLs — keep that grounding. Extract the highest-signal fields into the response schema.

===BUSINESS_RESEARCH===
{business}

===AUDIENCE_RESEARCH===
{audience}

===REGIONAL_RESEARCH===
{regional}

===COMPETITIVE_RESEARCH===
{competitive}

EXTRACT into JSON matching the schema:

• industry_terms (10 items): real jargon / technical terms used by professionals in {category}. Pull from BUSINESS_RESEARCH ===INDUSTRY_TERMINOLOGY===. Strings only — just the term, no definition.

• audience_phrases (8-10 items): VERBATIM quotes from AUDIENCE_RESEARCH ===AUDIENCE_LANGUAGE===. Real things customers said. Keep the quote text only — drop the source attribution. These will be injected into copy generation as voice anchors, so STRONGLY PREFER positive intent/desire phrases ("looking for somewhere that…", "love when a place…", "always wanted…", "what makes it special is…") over complaints or negative reviews. Skip pure complaints unless they reveal an unmet need that frames a positive promise. Aim for ≥80% positive/aspirational tone.
  HARD RELEVANCE FILTER: phrases must match {category} / {geo_specifics} / {audience_primary}. Drop quotes about unrelated cuisines, venues, or cities even if they are from a high-quality local source. For example, a Korean BBQ brief must NOT keep Italian, Mediterranean, bakery, cocktail-only, or waterfront-generic restaurant quotes unless the quote explicitly applies to Korean BBQ / grilling / premium dining.

• regional_touchpoints (5 items): names of local landmarks / neighborhoods / events / institutions from REGIONAL_RESEARCH ===CULTURAL_TOUCHPOINTS===. Each entry: name + 1-line context.

• competitor_section_orders (5-8 entries): the section_order each competitor uses on their homepage, pulled from COMPETITIVE_RESEARCH ===COMPETITOR_INVENTORY===. Each entry is an array of section type strings in order, lowercase, snake_case (e.g. ["hero", "story", "menu_highlights", "reservations", "press", "footer"]).

• top_competitors (5 entries): from COMPETITIVE_RESEARCH ===COMPETITOR_INVENTORY===. Each: {{name, url (cleaned, no markdown), positioning (1 sentence), tier ("budget"|"mid"|"premium")}}.
  PRIORITY ORDER: first choose direct {category}/{subcategory} competitors in {geo_specifics}; then adjacent direct competitors in nearby NYC neighborhoods; only then use broader premium restaurants as aspirational comparables. At least 3 of 5 should be direct category/subcategory matches when the research contains them. Do not fill all 5 with generic premium restaurants if direct competitors exist.

• white_space (3-5 items): angles competitors aren't taking, from COMPETITIVE_RESEARCH ===WHITE_SPACE===. One short sentence each.

OUTPUT: just the JSON. No markdown wrapper."""


_DESIGN_EXTRACT_PROMPT = """You are distilling 4 grounded design research dumps for a {category} business with {personality} personality into a structured JSON object.

Tone: {tone}
Region: {geo_specifics}

Below are the FOUR research dumps. Make ONE design decision per field — synthesize across all 4 dumps, don't just copy one.

===VISUAL_RESEARCH===
{visual}

===TYPOGRAPHY_RESEARCH===
{typography}

===COLOR_RESEARCH===
{color}

===LAYOUT_RESEARCH===
{layout}

EXTRACT into JSON matching the schema:

• chosen_typography: pick ONE pairing from TYPOGRAPHY_RESEARCH ===TYPEFACE_PAIRINGS=== that best fits {personality} + {tone}. heading_font and body_font MUST be exact Google Fonts family names. rationale: 1 sentence on why this fits.

• chosen_palette: pick ONE palette from COLOR_RESEARCH ===RECOMMENDED_PALETTES=== using these
  selection rules (in priority order):
    1. The palette's PRIMARY saturation must be ≥ 45%. Reject any palette with primary saturation
       below 40% — those produce bland SaaS output and are the #1 complaint mode.
    2. Background must be a TINTED brand-tuned neutral (any non-zero saturation is fine), NOT pure
       white "0 0% 100%" or pure black "0 0% 0%". For moody categories (luxury, fine dining, premium
       audio, fragrance, fashion), PREFER a dark background palette (background L < 25%).
    3. Among palettes that pass 1 + 2, pick the one whose PRIMARY hue is most brand-distinctive for
       THIS {category} + {personality} + {tone} — reference the Mood and "Brand distinctiveness"
       lines that the research wrote per palette.
    4. WCAG AA ratio for foreground-on-background should pass; if forced to choose between a
       distinctive palette that fails WCAG and a bland one that passes, prefer the distinctive one
       and we'll adjust the foreground in design tokens.
  All 8 slots required as HSL strings in format "H S% L%" (no commas, no hsl() wrapper, no hex).
  Example: "30 35% 45%". Include the palette's descriptive name.

• dominant_paradigm: ONE word/phrase from VISUAL_RESEARCH ===DOMINANT_PARADIGMS=== that the chosen design language commits to. Examples: editorial-warm, brutalist-minimal, expressive-maximalist, bento-modular, glassmorphism-futuristic, hand-crafted-illustrated.

• hero_pattern: pick ONE pattern from LAYOUT_RESEARCH ===HERO_PATTERNS=== where "Fits our brief?" was yes. Use the pattern's exact name. rationale: 1 sentence.

• per_section_approaches: for any of these section types that appear in LAYOUT_RESEARCH ===PER_REQUIRED_SECTION_PATTERNS===, pick the recommended approach and put a 3-8 word phrase describing it (e.g. menu: "photo-card-grid with 6 dishes", gallery: "asymmetric 12-col bento mosaic"). Skip section types not covered by the research.

• reference_urls: 5-8 cleanest URLs cited across all 4 dumps (real domains only — strip any markdown / parens / brackets). Prioritize sites mentioned in multiple dumps. If {geo_specifics} is a specific city/region (not "United States" or "international"), prefer URLs from businesses physically operating in that region — drop URLs that are clearly from other countries or distant regions even if cited.

OUTPUT: just the JSON. No markdown wrapper."""


# ── Public entry points ──────────────────────────────────────────────

async def extract_research_signals(
    intent: dict,
    domain_research: dict,
    design_research: dict,
    *,
    timeout_s: float = 90.0,
    purpose_data: dict | None = None,
) -> dict[str, Any]:
    """Two parallel Flash calls → structured signal dict.

    Auth handled by gemini_post via Vertex ADC. Returns
    ``{"domain": {...}, "design": {...}, "_meta": {...}}``. On any failure,
    returns empty signal dicts so caller can no-op the enrichment cleanly.
    """
    audience = intent.get("target_audience") or {}
    personality = intent.get("brand_personality") or []
    fmt = {
        "category":         (intent.get("business_category") or "general business").strip(),
        "subcategory":      (intent.get("business_subcategory") or "").strip() or "general",
        "geo_specifics":    (intent.get("geographic_specifics") or "United States").strip(),
        "audience_primary": (audience.get("primary") or "general consumers").strip(),
        "personality":      ", ".join(personality) if personality else "modern, clear, trustworthy",
        "tone":             (intent.get("tone") or "friendly").strip(),
    }

    domain_prompt = _DOMAIN_EXTRACT_PROMPT.format(
        **{k: fmt[k] for k in ("category", "subcategory", "geo_specifics", "audience_primary", "personality", "tone")},
        business=_clip(domain_research.get("business", {}).get("text", ""), 8000),
        audience=_clip(domain_research.get("audience", {}).get("text", ""), 8000),
        regional=_clip(domain_research.get("regional", {}).get("text", ""), 6000),
        competitive=_clip(domain_research.get("competitive", {}).get("text", ""), 8000),
    )

    design_prompt = _DESIGN_EXTRACT_PROMPT.format(
        **{k: fmt[k] for k in ("category", "geo_specifics", "audience_primary", "personality", "tone")},
        visual=_clip(design_research.get("visual", {}).get("text", ""), 8000),
        typography=_clip(design_research.get("typography", {}).get("text", ""), 6000),
        color=_clip(design_research.get("color", {}).get("text", ""), 6000),
        layout=_clip(design_research.get("layout", {}).get("text", ""), 8000),
    )

    visual_dna_prompt = _VISUAL_DNA_EXTRACT_PROMPT.format(
        **fmt,
        visual=_clip(design_research.get("visual", {}).get("text", ""), 8000),
        typography=_clip(design_research.get("typography", {}).get("text", ""), 6000),
        color=_clip(design_research.get("color", {}).get("text", ""), 6000),
        layout=_clip(design_research.get("layout", {}).get("text", ""), 8000),
    )

    # ── visual_dna call config ────────────────────────────────────────
    # The visual_dna distill is the load-bearing call for cultural
    # specificity in section codegen — when it fails, every section
    # falls back to the generic _FALLBACK_SKELETONS floor and the
    # output reads "generic SaaS template with brand name swapped in."
    #
    # The previous configuration (max_tokens=32768, shared timeout_s,
    # running concurrently with domain+design in a single gather) was
    # silently timing out at 120s on real grounded inputs — verified
    # with a paired end-to-end test where 2/2 brands returned empty
    # visual_dna while domain+design extracts succeeded.
    #
    # Three changes restore reliability:
    #   1. max_tokens back to 16384 — known-good ceiling that fits the
    #      schema's ~6000-char target with plenty of headroom; 32768
    #      stretches generation time disproportionately on Flash.
    #   2. Dedicated 180s timeout — visual_dna is 4x the output size of
    #      domain/design signals and a busier schema.
    #   3. Sequential execution — run domain + design (light, fast) in
    #      parallel first, then run visual_dna alone so it doesn't
    #      compete with the lighter calls for the Vertex per-project
    #      concurrency window.
    _VISUAL_DNA_TIMEOUT_S = 180.0
    # Bumped 4000 → 6000. The schema has 22 section_anatomies fields each
    # capped at 250 chars, plus 6 other string fields. With a long prompt
    # (research dumps embedded) Pro's reasoning tokens eat into the response
    # budget and the JSON truncates mid-string — observed in the LogiFleet
    # run as "Unterminated string at char 1088, SALVAGED 5 keys". 6000 gives
    # the model headroom even on the largest brief.
    _VISUAL_DNA_MAX_TOKENS = 6000
    # Pin visual_dna to gemini-2.5-pro. Pro is ~2× slower than Flash
    # (~30-45s vs ~15-20s) but follows brevity constraints reliably.
    # Flash exhibited a runaway-generation pattern on abstract briefs
    # (SaaS, fintech, dev tools): it would write 4-5 anatomies fine, then
    # explode into a 6000+ char "philosophical pad" on a later anatomy
    # (value_prop, pricing, how_it_works), busting the JSON parser and
    # leaving only the first anatomy recoverable. Pro respects the
    # 20-25 word / 150-char anatomy limit and outputs all 5-7 anatomies
    # cleanly. The ~20s latency cost is worth it — visual_dna is the
    # single most important research artifact downstream.
    _VISUAL_DNA_MODEL = "gemini-2.5-pro"

    async def _visual_dna_call() -> str:
        return await structured_distill(
            visual_dna_prompt, _VISUAL_DNA_TIMEOUT_S,
            label="visual_dna",
            response_schema=_VISUAL_DNA_SCHEMA,
            max_tokens=_VISUAL_DNA_MAX_TOKENS,
            model=_VISUAL_DNA_MODEL,
        )

    # Phase 1: light signal extracts run concurrently.
    # design_signals bumped 4096→8192 because the upgraded chosen_palette
    # prompt (per-category hue guide + composition recipe + mood/distinctiveness
    # rationale per palette) plus a longer per_section_approaches list
    # routinely produces ~5k tokens of output before the JSON closes — at 4096
    # it was truncating mid-string and the salvage path lost chosen_palette
    # entirely, falling the brief back to _default_palette().
    domain_raw, design_raw = await asyncio.gather(
        structured_distill(
            domain_prompt, timeout_s,
            label="domain_signals", response_schema=_DOMAIN_SIGNALS_SCHEMA, max_tokens=4096,
        ),
        structured_distill(
            design_prompt, timeout_s,
            label="design_signals", response_schema=_DESIGN_SIGNALS_SCHEMA, max_tokens=8192,
        ),
    )

    # Phase 2: visual_dna runs alone with a generous timeout. It's the
    # heaviest call by far (schema with 15+ section_anatomies, each 80-150
    # words of grounded prose) so it gets its own quota window.
    visual_dna_raw = await _visual_dna_call()

    domain = _parse_json(domain_raw, label="domain_signals") or {}
    design = _parse_json(design_raw, label="design_signals") or {}
    visual_dna = _parse_json(visual_dna_raw, label="visual_dna") or {}

    # Persist the raw visual_dna response unconditionally — when extraction
    # silently returns `{}` (truncated response, schema mismatch) we need
    # the raw text on disk to diagnose. Tiny disk cost, big debug payoff.
    try:
        with open("/tmp/landing_visual_dna_raw.txt", "w") as fh:
            fh.write(visual_dna_raw or "(empty)")
    except Exception:
        pass

    # One-retry fallback: trigger when EITHER the whole visual_dna parsed
    # to empty (Gemini call failed) OR section_anatomies came back empty
    # (we need those for codegen). Previously only the second condition
    # was checked, so total-failure cases never retried.
    needs_retry = (not visual_dna) or (not (visual_dna.get("section_anatomies") or {}))
    if needs_retry:
        reason = "parse failed" if not visual_dna else "section_anatomies empty"
        try:
            logger.info("extract_research_signals: visual_dna %s — retrying once", reason)
            visual_dna_retry_raw = await _visual_dna_call()
            try:
                with open("/tmp/landing_visual_dna_raw_retry.txt", "w") as fh:
                    fh.write(visual_dna_retry_raw or "(empty)")
            except Exception:
                pass
            visual_dna_retry = _parse_json(visual_dna_retry_raw, label="visual_dna_retry") or {}
            # Prefer the retry only if it improves on the first attempt:
            # either we had nothing before, or the retry has anatomies and
            # the first didn't.
            if visual_dna_retry and (
                not visual_dna
                or (visual_dna_retry.get("section_anatomies") and not visual_dna.get("section_anatomies"))
            ):
                visual_dna = visual_dna_retry
                logger.info("extract_research_signals: retry succeeded — using retry result")
            else:
                logger.warning(
                    "extract_research_signals: retry didn't improve result (retry_keys=%d retry_anatomies=%d) — keeping first attempt",
                    len(visual_dna_retry),
                    len((visual_dna_retry.get("section_anatomies") or {})),
                )
        except Exception as exc:
            logger.warning("extract_research_signals: visual_dna retry failed (%s) — using first attempt", exc)

    # Targeted gap-fill: gemini-3-flash-preview reliably produces 5-7 anatomies
    # but routinely skips `footer` (and occasionally `header`) because they're
    # not explicit section types in the brief. Asking again at the top of the
    # prompt didn't help — added language pushed the model into MAX_TOKENS.
    # Instead, when those universal layout keys are missing, make a SMALL
    # follow-up call that only asks for them. Tiny prompt, tiny output,
    # high success rate.
    anatomies_so_far = (visual_dna.get("section_anatomies") or {})
    missing_layout_keys = [k for k in ("header", "footer") if k not in anatomies_so_far]
    if anatomies_so_far and missing_layout_keys:
        try:
            logger.info(
                "extract_research_signals: backfilling missing layout anatomies: %s",
                missing_layout_keys,
            )
            backfill_prompt = (
                f"You previously distilled a visual_dna brief for an upscale {fmt.get('category', '')} "
                f"brand in {fmt.get('geo_specifics', '')} (personality: {fmt.get('personality', '')}). "
                f"You produced anatomies for: {sorted(anatomies_so_far.keys())}. "
                f"Now write ONLY the missing layout anatomies: {missing_layout_keys}. "
                "Each anatomy: 30-50 words MAXIMUM. Outer container rules, composition, decorative accent. "
                "For footer, pick one variant explicitly: minimalist-row | mega-columns | cta-band | "
                "centered-stack — match the brand's voice. Output JSON only: "
                '{ "header": "...", "footer": "..." } — include only the keys you were asked for.'
            )
            backfill_schema = {
                "type": "OBJECT",
                "properties": {
                    "header": {"type": "STRING"},
                    "footer": {"type": "STRING"},
                },
            }
            backfill_raw = await structured_distill(
                backfill_prompt, timeout_s,
                label="visual_dna_backfill",
                response_schema=backfill_schema,
                max_tokens=1024,
                model="gemini-2.5-flash",
            )
            backfill = _parse_json(backfill_raw, label="visual_dna_backfill") or {}
            merged_count = 0
            for k in missing_layout_keys:
                v = (backfill.get(k) or "").strip()
                if isinstance(v, str) and len(v) >= 40:
                    anatomies_so_far[k] = v
                    merged_count += 1
            if merged_count:
                visual_dna["section_anatomies"] = anatomies_so_far
                logger.info(
                    "extract_research_signals: backfill merged %d/%d layout keys",
                    merged_count, len(missing_layout_keys),
                )
            else:
                logger.warning(
                    "extract_research_signals: backfill returned no usable anatomies for %s",
                    missing_layout_keys,
                )
        except Exception as exc:
            logger.warning("extract_research_signals: backfill failed (%s) — falling back to variant picker", exc)

    # Final deterministic repair for universal layout anatomies. Gemini can
    # still return an empty backfill despite a valid visual_dna object. Header
    # and footer are load-bearing for layout codegen, so don't leave the
    # research artifact incomplete: synthesize compact anatomies from the
    # visual DNA instead of forcing every downstream consumer to rediscover
    # the fallback.
    if visual_dna:
        _ensure_layout_anatomies(visual_dna, fmt)
        _sanitize_visual_dna_anatomies(visual_dna)

    # Persist for diagnostics
    try:
        with open("/tmp/landing_signals.json", "w") as fh:
            json.dump(
                {"domain": domain, "design": design, "visual_dna": visual_dna},
                fh, indent=2, ensure_ascii=False,
            )
    except Exception:
        pass

    # Loud signal when the whole visual_dna is still empty after retry — this
    # is the bug that produces same-looking sections across projects, so we
    # want it screaming in the logs, not buried in an info line.
    if not visual_dna:
        logger.error(
            "extract_research_signals: visual_dna EMPTY after retry — codegen will fall back to generic skeletons. "
            "Check /tmp/landing_visual_dna_raw*.txt for the raw response."
        )

    anatomies_count = len((visual_dna.get("section_anatomies") or {}))
    # Dedicated success/failure marker for visual_dna — load-bearing for
    # cultural specificity. When this logs FAIL the generated site will
    # fall back to generic skeletons; grep for this in prod telemetry.
    if visual_dna and anatomies_count > 0:
        logger.info(
            "extract_research_signals: visual_dna OK — anatomies=%d intensity=%s motifs=%d textures=%d icons=%d",
            anatomies_count,
            (visual_dna.get("cultural_intensity") or "?"),
            len(visual_dna.get("decorative_motifs") or []),
            len(visual_dna.get("signature_textures") or []),
            len(visual_dna.get("iconography_anchors") or []),
        )
    else:
        logger.error(
            "extract_research_signals: visual_dna FAIL — keys=%d anatomies=%d "
            "(generated page will be GENERIC; check Vertex quota / timeout / max_tokens)",
            len(visual_dna), anatomies_count,
        )
    logger.info(
        "extract_research_signals: ok — domain_keys=%d design_keys=%d visual_dna_keys=%d anatomies=%d intensity=%s",
        len(domain), len(design), len(visual_dna), anatomies_count,
        (visual_dna.get("cultural_intensity") or "?"),
    )
    return {
        "domain": domain,
        "design": design,
        "visual_dna": visual_dna,
        "_meta": {
            "domain_extracted":     bool(domain),
            "design_extracted":     bool(design),
            "visual_dna_extracted": bool(visual_dna),
        },
    }


def enrich_brief_with_signals(brief: dict, signals: dict) -> dict:
    """Merge research signals into a legacy Brief shape.

    Returns the same brief dict (mutated). Override priority:
      • palette         ← signals.design.chosen_palette (8 HSL slots)
      • typography      ← signals.design.chosen_typography (Google Fonts)
      • motif           ← derived from signals.design.dominant_paradigm
      • design_system   ← unchanged (still produced by legacy brief)
      • hero archetype  ← signals.design.hero_pattern (if matches allowed set)
      • per-section     ← signals.design.per_section_approaches (best-effort)
      • voice_phrases   ← signals.domain.audience_phrases (NEW field)
      • regional_refs   ← signals.domain.regional_touchpoints (NEW field)
      • industry_terms  ← signals.domain.industry_terms (NEW field)
      • white_space     ← signals.domain.white_space (NEW field)
      • references      ← signals.domain.top_competitors merged with existing

    Codegen reads voice_phrases / regional_refs / industry_terms when
    present (Step 6 wiring). For now they ride along untouched.
    """
    domain = (signals or {}).get("domain") or {}
    design = (signals or {}).get("design") or {}

    # Palette override — only if all 8 HSL slots present
    palette = design.get("chosen_palette") or {}
    palette_slots = ["primary", "secondary", "accent", "background", "foreground", "muted", "border", "card"]
    if all(palette.get(slot) for slot in palette_slots):
        brief["palette"] = {slot: palette[slot] for slot in palette_slots}
        brief.setdefault("_research", {})["palette_name"] = palette.get("name", "")
        _log_picked_palette(palette, source="gemini_research")
    elif palette:
        # Partial palette returned — log what was missing so we can spot
        # research-output drift in the wild. The brief.palette stays unset
        # here and the downstream merge in landing_brief.py backfills from
        # _default_palette() — which is now a warm editorial neutral, not
        # the old SaaS blue.
        missing = [s for s in palette_slots if not palette.get(s)]
        logger.warning(
            "palette: research returned partial palette (name=%r), missing slots: %s — "
            "falling back to defaults for those slots",
            palette.get("name", "?"), missing,
        )

    # Typography override
    typo = design.get("chosen_typography") or {}
    if typo.get("heading_font") and typo.get("body_font"):
        brief["typography"] = {
            "heading_font": typo["heading_font"],
            "body_font":    typo["body_font"],
            "scale":        brief.get("typography", {}).get("scale", "balanced"),
        }
        brief.setdefault("_research", {})["typography_rationale"] = typo.get("rationale", "")

    # Paradigm telemetry only. We INTENTIONALLY do not overwrite brief.motif
    # here: motif is paired with brief.personality.vibe_keywords on the plan
    # card, and the two come from the same Gemini distill call. Overriding
    # motif from a different research track produces an incoherent design
    # line ("utilitarian motif · premium, approachable, modern"). The
    # paradigm string is still kept under _research for codegen context.
    paradigm = (design.get("dominant_paradigm") or "").strip().lower()
    if paradigm:
        brief.setdefault("_research", {})["paradigm"] = paradigm

    # Hero pattern — kept as a metadata breadcrumb only.
    # Section structure is now driven by visual_dna.section_anatomies (Gemini-
    # written from grounded research). The legacy hero `archetype` field is
    # no longer load-bearing for codegen, but research-derived rationale is
    # still useful in _research for debugging and downstream consumers.
    hero = design.get("hero_pattern") or {}
    if hero.get("rationale"):
        for s in brief.get("sections") or []:
            if (s.get("type") or "").lower() == "hero":
                s.setdefault("_research", {})["hero_rationale"] = hero.get("rationale", "")
                if hero.get("name"):
                    s.setdefault("_research", {})["hero_pattern_name"] = hero["name"]
                break

    # Per-section approaches — soft overrides via _research breadcrumb;
    # codegen will read these as guidance when present.
    approaches = design.get("per_section_approaches") or {}
    if approaches:
        for s in brief.get("sections") or []:
            t = (s.get("type") or "").lower()
            note = approaches.get(t)
            if note:
                s.setdefault("_research", {})["approach_hint"] = note

    # NEW research-only fields. Codegen reads them as additional context.
    if domain.get("audience_phrases"):
        brief["voice_phrases"] = list(domain["audience_phrases"])[:10]
    if domain.get("regional_touchpoints"):
        brief["regional_refs"] = list(domain["regional_touchpoints"])[:5]
    if domain.get("industry_terms"):
        brief["industry_terms"] = list(domain["industry_terms"])[:10]
    if domain.get("white_space"):
        brief["white_space"] = list(domain["white_space"])[:5]

    # Visual DNA — concrete, culturally-specific visual cues. The codegen
    # prompt reads brief["visual_dna"] as the PRIMARY visual directive
    # (replacing the abstract motion/accent_shape/surface enums as the lead
    # design signal). Empty when extraction failed; codegen falls back to
    # the legacy enums-only path.
    visual_dna = signals.get("visual_dna") or {}
    if visual_dna:
        brief["visual_dna"] = visual_dna

    # Merge top_competitors into references (legacy brief.references)
    top = domain.get("top_competitors") or []
    if top:
        existing = list(brief.get("references") or [])
        existing_urls = {(r.get("url") or "").rstrip("/") for r in existing}
        for c in top:
            url = (c.get("url") or "").rstrip("/")
            if not url or url in existing_urls:
                continue
            existing.append({
                "name": c.get("name") or "",
                "url":  url,
                "why":  c.get("positioning") or "",
            })
            existing_urls.add(url)
        brief["references"] = existing[:8]

    brief.setdefault("_research", {})["enriched"] = True
    return brief


# ── Helpers ──────────────────────────────────────────────────────────

def _clip(text: str, max_chars: int) -> str:
    if not text:
        return "(none)"
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n…[truncated]"


def _ensure_layout_anatomies(visual_dna: dict, fmt: dict[str, str]) -> None:
    """Guarantee universal header/footer anatomy keys exist.

    These two keys are not always present in Gemini's compact JSON even though
    layout codegen needs them. The fallback is intentionally research-flavored:
    it references motifs, palette emphasis, typography voice, and category so
    Claude still gets brand-specific guidance instead of a generic footer.
    """
    anatomies = visual_dna.setdefault("section_anatomies", {})
    if not isinstance(anatomies, dict):
        anatomies = {}
        visual_dna["section_anatomies"] = anatomies

    motifs = [m for m in (visual_dna.get("decorative_motifs") or []) if isinstance(m, str)]
    motif_a = motifs[0] if motifs else "a small brand-specific accent mark"
    motif_b = motifs[1] if len(motifs) > 1 else motif_a
    palette = (visual_dna.get("cultural_palette_emphasis") or "primary + accent colors").strip()
    type_voice = (visual_dna.get("typography_voice") or "heading font for wordmark, clean sans for navigation").strip()
    category = (fmt.get("category") or "brand").strip()
    category_l = category.lower()
    geo = (fmt.get("geo_specifics") or "").strip()
    info_heavy_categories = (
        "education", "school", "academy", "language", "tutoring", "course",
        "coaching", "clinic", "healthcare", "fitness", "wellness",
        "real estate", "nonprofit", "community",
    )
    refined_categories = (
        "restaurant", "hotel", "resort", "hospitality", "spa", "studio",
        "gallery", "portfolio", "fashion", "luxury",
    )

    if not isinstance(anatomies.get("header"), str) or len((anatomies.get("header") or "").strip()) < 40:
        if any(c in category_l for c in info_heavy_categories):
            anatomies["header"] = (
                "Utility-split header archetype. Desktop has a slim top row for business_info details from landing.brand "
                "(email, phone, city or hours) and a main 64px row with brand left, concise nav center, rounded CTA right. "
                f"Brand mark uses {type_voice}; {motif_a} appears as a tiny divider or logo accent. "
                "Mobile collapses utility details into a full-width drawer. Header must stay solid and readable on scroll."
            )
        elif any(c in category_l for c in refined_categories):
            anatomies["header"] = (
                "Centered-logo editorial header archetype. Desktop uses left nav group, centered wordmark, and right nav/CTA group "
                "inside an 80px translucent background that becomes solid on scroll. "
                f"Wordmark uses {type_voice}; {motif_a} appears as a restrained logo accent. "
                f"CTA is a rounded token-driven button using {palette}. Mobile collapses to brand left and hamburger right."
            )
        else:
            anatomies["header"] = (
                "Sticky top-bar archetype with a 64px desktop height and solid readable surface after scroll. "
                f"Brand mark uses {type_voice}; navigation is a short centered row with hover underline or soft pill treatment. "
                f"Use {motif_a} as a tiny wordmark or divider accent, never as a large decoration. "
                f"CTA sits right as a rounded token-driven button using {palette}. Mobile collapses to a full-width drawer with the same nav and CTA."
            )

    if not isinstance(anatomies.get("footer"), str) or len((anatomies.get("footer") or "").strip()) < 40:
        anatomies["footer"] = (
            "Mega-columns footer tailored to the business context. Outer footer uses a dark or card surface with clear contrast, "
            "then a container grid of brand story, Explore links from landing.footer.links, Visit/contact details from "
            f"landing.brand.business_info, and a final reservation/newsletter CTA for this {category} in {geo}. "
            f"Integrate {motif_b} as a restrained divider or social-row accent. Bottom strip shows copyright plus small legal links. "
            "All links, social handles, and business info must read from landing.json at runtime."
        )


def _sanitize_visual_dna_anatomies(visual_dna: dict) -> None:
    """Remove anatomy phrases that contradict downstream hard UI rules."""
    anatomies = visual_dna.get("section_anatomies") or {}
    if not isinstance(anatomies, dict):
        return
    replacements = {
        "with no border-radius": "with token-driven rounded corners",
        "no border-radius": "token-driven rounded corners",
        "rounded-none": "rounded-md",
        "without border-radius": "with token-driven rounded corners",
    }
    for key, value in list(anatomies.items()):
        if not isinstance(value, str):
            continue
        cleaned = value
        for needle, repl in replacements.items():
            cleaned = cleaned.replace(needle, repl)
        anatomies[key] = cleaned


def _parse_json(raw: str, *, label: str) -> dict | None:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        # Salvage attempt for responses truncated mid-string (max_tokens hit).
        # Walk back to the last well-formed object/array boundary and close
        # any open brackets so we get partial signal instead of dropping
        # everything. Better partial visual_dna than nothing.
        salvaged = _salvage_truncated_json(raw)
        if salvaged is not None:
            logger.warning(
                "extract_research_signals %s: JSON parse failed (%s); SALVAGED %d keys",
                label, exc, len(salvaged) if isinstance(salvaged, dict) else 0,
            )
            return salvaged
        logger.warning("extract_research_signals %s: JSON parse failed (%s) — head: %s",
                       label, exc, raw[:200])
        return None


def _salvage_truncated_json(raw: str) -> dict | None:
    """Recover partial JSON from a response truncated mid-string.

    Two-pass strategy:
      1. Find the deepest safe truncation point AT ANY DEPTH where we're
         outside a string and at the end of a complete key/value pair.
         Close all open braces/brackets up to depth 0.
      2. Try to parse the reconstructed candidate.

    This recovers anatomies/items that ARE complete inside a parent object
    whose final entry was runaway-truncated mid-string. The previous
    depth-1-only approach lost everything inside ``section_anatomies`` when
    one anatomy ran away.
    """
    if not raw or "{" not in raw:
        return None

    in_string = False
    escape = False
    stack: list[str] = []  # tracks "{" or "[" for each open container
    # last_safe_at_depth[d] = (position-just-after-complete-entry, stack-snapshot)
    # for depth d. We pick the deepest non-zero depth that has a safe point.
    last_safe: list[tuple[int, list[str]]] = []

    for i, ch in enumerate(raw):
        if escape:
            escape = False
            continue
        if in_string:
            if ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
                # End-of-string at depth>0 — record this as a potential safe
                # point (after closing quote). Only valid when the string was
                # a *value* (i.e. preceded by ':') not a *key*. We detect by
                # scanning back: if last non-whitespace char before the
                # matching opening quote was ':', it's a value.
                if stack:
                    # Find opening quote of this string by scanning back
                    j = i - 1
                    while j >= 0 and raw[j] != '"':
                        j -= 1
                    # Walk further back past whitespace to find the char
                    # before this string token
                    k = j - 1
                    while k >= 0 and raw[k] in " \t\n\r":
                        k -= 1
                    if k >= 0 and raw[k] == ":":
                        # This was a value string — record safe point
                        last_safe.append((i + 1, stack.copy()))
            continue
        if ch == '"':
            in_string = True
            continue
        if ch in "{[":
            stack.append(ch)
        elif ch in "}]":
            if stack:
                stack.pop()
            if stack:  # still inside some container
                last_safe.append((i + 1, stack.copy()))

    if not last_safe:
        return None

    # Try from deepest-recorded safe point backwards
    for pos, stack_snap in reversed(last_safe):
        candidate = raw[:pos].rstrip()
        # Drop trailing comma if any
        if candidate.endswith(","):
            candidate = candidate[:-1]
        # Close every open container in stack_snap (in reverse order)
        for opener in reversed(stack_snap):
            candidate += "}" if opener == "{" else "]"
        try:
            result = json.loads(candidate)
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            continue
    return None


def _empty_result() -> dict[str, Any]:
    return {
        "domain": {},
        "design": {},
        "_meta": {"domain_extracted": False, "design_extracted": False},
    }


# ── Multi-page voice extraction ──────────────────────────────────────
# The legacy multi-page generator runs a single ``gemini_deep_research``
# call producing one big blob with ===HEADER=== sections. It does NOT
# produce the structured 4-domain + 4-design dumps the landing pipeline
# uses. To bring research-grounded voice priming to multi-page sites
# without doubling research cost, we run ONE Flash call that distills
# voice signals out of the legacy blob — then format them as a
# ===VOICE_GUIDANCE=== block appended to the research so every phase
# prompt (which already consumes the blob) sees them automatically.

_MULTIPAGE_VOICE_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "required": ["voice_phrases", "industry_terms", "regional_refs", "white_space"],
    "properties": {
        "voice_phrases": {
            "type": "ARRAY",
            "minItems": 6,
            "maxItems": 10,
            "items": {"type": "STRING"},
        },
        "industry_terms": {
            "type": "ARRAY",
            "minItems": 6,
            "maxItems": 12,
            "items": {"type": "STRING"},
        },
        "regional_refs": {
            "type": "ARRAY",
            "maxItems": 4,
            "items": {"type": "STRING"},
        },
        "white_space": {
            "type": "ARRAY",
            "minItems": 2,
            "maxItems": 4,
            "items": {"type": "STRING"},
        },
    },
}


_MULTIPAGE_VOICE_PROMPT = """You are extracting VOICE GUIDANCE for a multi-page {layout_archetype} project in the {domain} domain.

A code generator will produce many React pages and components. We want every page to write copy that mirrors how real people in this domain actually talk, naming concrete anchors when they fit.

Distill ONLY voice / positioning signals from the research below. Do NOT repeat features, layouts, or design tokens — those are already covered elsewhere.

RESEARCH:
{research}

Rules:
• voice_phrases (6–10): short quotes (≤ 12 words) that sound like REAL audience language for this domain — aspirational/positive intent ("looking for somewhere that…", "love when a place…"). Prefer verbatim audience quotes if the research contains them; otherwise infer plausible phrasing from copy_tone + cultural_atmosphere. No marketing fluff. No clichés ("best in class").
• industry_terms (6–12): specialty vocabulary natives of this domain use casually — things a customer would say without thinking. Examples: coffee shop → "single-origin", "pour-over", "cold brew"; fashion → "drape", "silhouette", "capsule".
• regional_refs (0–4): concrete named anchors (places, suppliers, events, neighborhoods, local figures) real businesses in this domain reference for credibility. Skip if the research is geographically generic.
• white_space (2–4): positioning angles competitors aren't taking — useful for differentiating value props and About copy.

Return ONLY valid JSON matching the schema."""


async def extract_multipage_voice_signals(
    research_blob: str,
    classification: dict,
    *,
    timeout_s: float = 60.0,
) -> dict[str, Any]:
    """One Flash call → 4 voice signals for the multi-page generator.

    Auth handled by gemini_post via Vertex ADC. Returns
    ``{voice_phrases, industry_terms, regional_refs, white_space}`` or an
    empty dict on failure (caller should no-op the injection).
    """
    if not research_blob or len(research_blob) < 500:
        logger.info(
            "extract_multipage_voice_signals: skip (blob=%d)",
            len(research_blob or ""),
        )
        return {}

    prompt = _MULTIPAGE_VOICE_PROMPT.format(
        layout_archetype=classification.get("layout_archetype") or "website",
        domain=classification.get("domain") or "general",
        research=_clip(research_blob, 18000),
    )

    raw = await structured_distill(
        prompt, timeout_s,
        label="multipage_voice",
        response_schema=_MULTIPAGE_VOICE_SCHEMA,
        max_tokens=2048,
    )
    parsed = _parse_json(raw, label="multipage_voice") or {}
    if not parsed:
        return {}

    logger.info(
        "extract_multipage_voice_signals: ok — voice=%d terms=%d regional=%d ws=%d",
        len(parsed.get("voice_phrases") or []),
        len(parsed.get("industry_terms") or []),
        len(parsed.get("regional_refs") or []),
        len(parsed.get("white_space") or []),
    )
    return parsed


def format_voice_guidance_block(signals: dict) -> str:
    """Render the extracted signals as a ===VOICE_GUIDANCE=== block.

    Returns empty string when there is nothing to inject — the caller
    should leave the research blob untouched in that case.
    """
    voice_phrases = list(signals.get("voice_phrases") or [])[:10]
    industry_terms = list(signals.get("industry_terms") or [])[:12]
    regional_refs = list(signals.get("regional_refs") or [])[:4]
    white_space = list(signals.get("white_space") or [])[:4]

    if not (voice_phrases or industry_terms or regional_refs or white_space):
        return ""

    lines: list[str] = [
        "",
        "===VOICE_GUIDANCE===",
        "Use these signals when writing copy across ALL pages and components.",
        "This is voice priming, not a checklist — skip any signal that does not",
        "fit a particular section's role. Never sacrifice clarity to shoehorn",
        "a phrase. Do NOT paste voice_phrases verbatim into headlines unless",
        "they fit naturally; mirror their register and cadence.",
        "",
    ]
    if voice_phrases:
        lines.append("AUDIENCE LANGUAGE (real customer phrasing — mirror the register):")
        for p in voice_phrases:
            lines.append(f"  - \"{p}\"")
        lines.append("")
    if industry_terms:
        lines.append("INDUSTRY VOCABULARY (weave 1-2 into body copy where they fit):")
        lines.append(f"  {', '.join(industry_terms)}")
        lines.append("")
    if regional_refs:
        lines.append("REGIONAL ANCHORS (reference 1 by name in About / Locations / Proof sections):")
        for r in regional_refs:
            lines.append(f"  - {r}")
        lines.append("")
    if white_space:
        lines.append("DIFFERENTIATION ANGLES (lean on these in value props / About):")
        for w in white_space:
            lines.append(f"  - {w}")
        lines.append("")
    return "\n".join(lines)
