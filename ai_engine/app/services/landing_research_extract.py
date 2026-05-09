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
from app.services.pipeline.constants import _FALLBACK_GEMINI_KEY

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
    gemini_key: str | None = None,
    timeout_s: float = 90.0,
) -> dict[str, Any]:
    """Two parallel Flash calls → structured signal dict.

    Returns ``{"domain": {...}, "design": {...}, "_meta": {...}}``.
    On any failure, returns empty signal dicts so caller can no-op the
    enrichment cleanly.
    """
    key = (gemini_key or _FALLBACK_GEMINI_KEY or os.environ.get("GOOGLE_API_KEY", "")).strip()
    if not key:
        logger.warning("extract_research_signals: no Gemini key — empty signals")
        return _empty_result()

    audience = intent.get("target_audience") or {}
    personality = intent.get("brand_personality") or []
    fmt = {
        "category":         (intent.get("business_category") or "general business").strip(),
        "geo_specifics":    (intent.get("geographic_specifics") or "United States").strip(),
        "audience_primary": (audience.get("primary") or "general consumers").strip(),
        "personality":      ", ".join(personality) if personality else "modern, clear, trustworthy",
        "tone":             (intent.get("tone") or "friendly").strip(),
    }

    domain_prompt = _DOMAIN_EXTRACT_PROMPT.format(
        **fmt,
        business=_clip(domain_research.get("business", {}).get("text", ""), 8000),
        audience=_clip(domain_research.get("audience", {}).get("text", ""), 8000),
        regional=_clip(domain_research.get("regional", {}).get("text", ""), 6000),
        competitive=_clip(domain_research.get("competitive", {}).get("text", ""), 8000),
    )

    design_prompt = _DESIGN_EXTRACT_PROMPT.format(
        **fmt,
        visual=_clip(design_research.get("visual", {}).get("text", ""), 8000),
        typography=_clip(design_research.get("typography", {}).get("text", ""), 6000),
        color=_clip(design_research.get("color", {}).get("text", ""), 6000),
        layout=_clip(design_research.get("layout", {}).get("text", ""), 8000),
    )

    domain_raw, design_raw = await asyncio.gather(
        structured_distill(
            domain_prompt, key, timeout_s,
            label="domain_signals", response_schema=_DOMAIN_SIGNALS_SCHEMA, max_tokens=4096,
        ),
        structured_distill(
            design_prompt, key, timeout_s,
            label="design_signals", response_schema=_DESIGN_SIGNALS_SCHEMA, max_tokens=4096,
        ),
    )

    domain = _parse_json(domain_raw, label="domain_signals") or {}
    design = _parse_json(design_raw, label="design_signals") or {}

    # Persist for diagnostics
    try:
        with open("/tmp/landing_signals.json", "w") as fh:
            json.dump({"domain": domain, "design": design}, fh, indent=2, ensure_ascii=False)
    except Exception:
        pass

    logger.info(
        "extract_research_signals: ok — domain_keys=%d design_keys=%d",
        len(domain), len(design),
    )
    return {
        "domain": domain,
        "design": design,
        "_meta": {
            "domain_extracted": bool(domain),
            "design_extracted": bool(design),
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

    # Motif from paradigm — keep as one short word for legacy compat
    paradigm = (design.get("dominant_paradigm") or "").strip().lower()
    if paradigm:
        brief["motif"] = paradigm.split("-")[0] or brief.get("motif", "minimal")
        brief.setdefault("_research", {})["paradigm"] = paradigm

    # Hero archetype — substring match on the research-named pattern
    # against our allowed enum set. First match wins.
    hero_arch_allowed = [
        "full-bleed-overlay", "oversized-watermark", "asymmetric-split",
        "type-wrapping-product", "video-mask", "card-stack",
    ]
    hero = design.get("hero_pattern") or {}
    hero_name_raw = (hero.get("name") or "").strip().lower()
    # Normalize separators so "Asymmetric Split with strong imagery" → "asymmetric-split-..."
    hero_name = hero_name_raw.replace(" ", "-").replace("_", "-")
    # Extra fuzzy aliases for phrasings that don't contain our slug literally
    if "video" in hero_name and "background" in hero_name:
        hero_name = "video-mask"
    elif "minimalist" in hero_name and ("typograph" in hero_name or "type" in hero_name):
        hero_name = "oversized-watermark"
    elif "centered" in hero_name and ("cta" in hero_name or "headline" in hero_name):
        hero_name = "full-bleed-overlay"

    matched = next((a for a in hero_arch_allowed if a in hero_name), None)
    if matched:
        for s in brief.get("sections") or []:
            if (s.get("type") or "").lower() == "hero":
                s["archetype"] = matched
                s.setdefault("_research", {})["hero_rationale"] = hero.get("rationale", "")
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
        logger.warning("extract_research_signals %s: JSON parse failed (%s) — head: %s",
                       label, exc, raw[:200])
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
    gemini_key: str | None = None,
    timeout_s: float = 60.0,
) -> dict[str, Any]:
    """One Flash call → 4 voice signals for the multi-page generator.

    Returns ``{voice_phrases, industry_terms, regional_refs, white_space}``
    or an empty dict on failure (caller should no-op the injection).
    """
    key = (gemini_key or _FALLBACK_GEMINI_KEY or os.environ.get("GOOGLE_API_KEY", "")).strip()
    if not key or not research_blob or len(research_blob) < 500:
        logger.info(
            "extract_multipage_voice_signals: skip (key=%s blob=%d)",
            bool(key), len(research_blob or ""),
        )
        return {}

    prompt = _MULTIPAGE_VOICE_PROMPT.format(
        layout_archetype=classification.get("layout_archetype") or "website",
        domain=classification.get("domain") or "general",
        research=_clip(research_blob, 18000),
    )

    raw = await structured_distill(
        prompt, key, timeout_s,
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
