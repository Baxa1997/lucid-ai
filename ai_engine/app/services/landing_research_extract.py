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
from typing import Any

from app.services.landing_gemini import structured_distill

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
    "required": [
        "decorative_motifs",
        "signature_textures",
        "iconography_anchors",
        "photography_style",
        "layout_signature",
        "cultural_palette_emphasis",
        "typography_voice",
        "section_flavors",
        "section_anatomies",
        "cultural_intensity",
    ],
    "properties": {
        "decorative_motifs": {
            "type": "ARRAY",
            "items": {"type": "STRING"},
        },
        "signature_textures": {
            "type": "ARRAY",
            "items": {"type": "STRING"},
        },
        "iconography_anchors": {
            "type": "ARRAY",
            "items": {"type": "STRING"},
        },
        "photography_style":         {"type": "STRING"},
        "layout_signature":          {"type": "STRING"},
        "cultural_palette_emphasis": {"type": "STRING"},
        "typography_voice":          {"type": "STRING"},
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
        "section_anatomies": {
            "type": "OBJECT",
            "properties": {
                "hero":         {"type": "STRING"},
                "menu":         {"type": "STRING"},
                "gallery":      {"type": "STRING"},
                "story":        {"type": "STRING"},
                "philosophy":   {"type": "STRING"},
                "testimonials": {"type": "STRING"},
                "value_prop":   {"type": "STRING"},
                "features":     {"type": "STRING"},
                "process":      {"type": "STRING"},
                "how_it_works": {"type": "STRING"},
                "press":        {"type": "STRING"},
                "team":         {"type": "STRING"},
                "pricing":      {"type": "STRING"},
                "faq":          {"type": "STRING"},
                "cta":          {"type": "STRING"},
                "stats":        {"type": "STRING"},
                "locations":    {"type": "STRING"},
                "reservation":  {"type": "STRING"},
                "contact":      {"type": "STRING"},
                "newsletter":   {"type": "STRING"},
                "header":       {"type": "STRING"},
                "footer":       {"type": "STRING"},
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

• section_anatomies: per-section STRUCTURAL anatomy spec (the actual layout blueprint Claude implements). Each entry is 80–150 words describing WHERE things go, what shapes, what scale tokens, and which decorative cues from this VISUAL DNA land where. Format like a developer-facing design spec. Anchor to the visual_research / layout_research above — pull from real reference patterns the research surfaced. Each anatomy MUST include:
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

• cultural_intensity: ONE word — "subtle" or "bold".
    "subtle"  = cultural cues sit in accent positions only (a small red seal-stamp, a lantern silhouette as a divider). Modern restraint stays. Page reads upscale-modern with cultural FLAVOR.
    "bold"    = larger cultural textures (rice-paper over hero, calligraphy-stroke headers, full-bleed cultural patterns), more 'wow', less restraint. Page reads unmistakably traditional.
  Pick based on the brand's tone ({tone}) and personality ({personality}). Restrained, refined, minimalist, sophisticated → subtle. Festive, traditional, immersive, theatrical, expressive → bold. When in doubt, pick subtle.

OUTPUT BUDGET — be COMPACT but section_anatomies needs room. Whole JSON should fit in ~6000 chars. Concretely:
  • Each string field (photography_style, layout_signature, cultural_palette_emphasis, typography_voice): 1-2 sentences, MAX ~200 chars each.
  • decorative_motifs: 3-5 items. Each item ≤80 chars.
  • signature_textures: 2-3 items. Each item ≤80 chars.
  • iconography_anchors: 4-6 items. Each item is a SHORT noun (≤25 chars: "lantern", "tea cup", "olive branch") — no descriptions.
  • section_flavors: 1 sentence per section type, MAX ~150 chars each. Skip section types not relevant.
  • section_anatomies: 80-150 words PER section, ~700-1200 chars each. ONLY include section types relevant to this brief — fewer richer entries beats many thin ones. Aim for 5-8 entries total (always include hero + header + footer; the rest match what the brand needs).
Stop early. Do not pad with extra commentary, examples, or rationale prose. Concrete and tight beats expansive every time.

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

• regional_touchpoints (5 items): names of local landmarks / neighborhoods / events / institutions from REGIONAL_RESEARCH ===CULTURAL_TOUCHPOINTS===. Each entry: name + 1-line context.

• competitor_section_orders (5-8 entries): the section_order each competitor uses on their homepage, pulled from COMPETITIVE_RESEARCH ===COMPETITOR_INVENTORY===. Each entry is an array of section type strings in order, lowercase, snake_case (e.g. ["hero", "story", "menu_highlights", "reservations", "press", "footer"]).

• top_competitors (5 entries): from COMPETITIVE_RESEARCH ===COMPETITOR_INVENTORY===. Each: {{name, url (cleaned, no markdown), positioning (1 sentence), tier ("budget"|"mid"|"premium")}}.

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

• chosen_palette: pick ONE palette from COLOR_RESEARCH ===RECOMMENDED_PALETTES=== that best fits the brief. All 8 slots required as HSL strings in format "H S% L%" (no commas, no hsl() wrapper, no hex). Example: "30 35% 45%". Include the palette's descriptive name.

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
        **{k: fmt[k] for k in ("category", "geo_specifics", "audience_primary", "personality", "tone")},
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

    async def _visual_dna_call() -> str:
        """Visual DNA call wrapped so we can retry once when section_anatomies
        comes back empty. The anatomies are now load-bearing for codegen — when
        they're missing every section falls back to the generic skeleton, so
        a single extra distill call (~5s, cheap Flash tokens) is worth it.
        """
        return await structured_distill(
            visual_dna_prompt, timeout_s,
            label="visual_dna", response_schema=_VISUAL_DNA_SCHEMA, max_tokens=16384,
        )

    domain_raw, design_raw, visual_dna_raw = await asyncio.gather(
        structured_distill(
            domain_prompt, timeout_s,
            label="domain_signals", response_schema=_DOMAIN_SIGNALS_SCHEMA, max_tokens=4096,
        ),
        structured_distill(
            design_prompt, timeout_s,
            label="design_signals", response_schema=_DESIGN_SIGNALS_SCHEMA, max_tokens=4096,
        ),
        _visual_dna_call(),
    )

    domain = _parse_json(domain_raw, label="domain_signals") or {}
    design = _parse_json(design_raw, label="design_signals") or {}
    visual_dna = _parse_json(visual_dna_raw, label="visual_dna") or {}

    # One-retry fallback: if section_anatomies is missing/empty, the codegen
    # falls back to generic skeletons — a 5s retry is cheap insurance.
    if not (visual_dna.get("section_anatomies") or {}):
        try:
            logger.info("extract_research_signals: visual_dna.section_anatomies empty — retrying once")
            visual_dna_retry_raw = await _visual_dna_call()
            visual_dna_retry = _parse_json(visual_dna_retry_raw, label="visual_dna_retry") or {}
            if visual_dna_retry.get("section_anatomies"):
                visual_dna = visual_dna_retry
        except Exception as exc:
            logger.warning("extract_research_signals: visual_dna retry failed (%s) — using first attempt", exc)

    # Persist for diagnostics
    try:
        with open("/tmp/landing_signals.json", "w") as fh:
            json.dump(
                {"domain": domain, "design": design, "visual_dna": visual_dna},
                fh, indent=2, ensure_ascii=False,
            )
    except Exception:
        pass

    anatomies_count = len((visual_dna.get("section_anatomies") or {}))
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

    Strategy: scan the raw text left-to-right tracking brace/bracket depth
    and string state. Remember the LAST byte position where we were at
    depth=1 (just inside the top-level object) and not inside a string.
    Truncate there, append `}`, parse. Returns None if salvage fails.
    """
    if not raw or "{" not in raw:
        return None
    depth = 0
    in_string = False
    escape = False
    last_safe = -1  # last position at depth 1 outside a string after a comma
    for i, ch in enumerate(raw):
        if escape:
            escape = False
            continue
        if in_string:
            if ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth == 1:
                last_safe = i + 1  # right after a closing bracket at depth 2
        elif ch == "," and depth == 1:
            last_safe = i  # before the comma
    if last_safe <= 0:
        return None
    candidate = raw[:last_safe].rstrip()
    if candidate.endswith(","):
        candidate = candidate[:-1]
    candidate += "}"
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
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
