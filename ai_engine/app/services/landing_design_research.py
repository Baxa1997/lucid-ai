"""Landing-page Stage 3 — Design Research (4 parallel grounded calls).

Grounds visual design choices in real, current best-in-class examples
rather than generic AI defaults. Independent of Stage 2 (domain
research); both stages can run in parallel from the same intent.

Four parallel ``gemini-2.5-pro + google_search`` calls:
  1. VISUAL     — reference sites, dominant paradigms, distinctive elements
  2. TYPOGRAPHY — Google-Fonts-available pairings, hierarchy, conventions
  3. COLOR      — palettes with HSL/hex + WCAG, regional/cultural notes
  4. LAYOUT     — section patterns, conversion placement, mobile behavior

Each call emits markdown with embedded URL citations. The
``looks_degenerate`` guard catches search-snippet bailouts.

Output is consumed by Stage 5 (design system synthesis), which fuses
the 4 markdown dumps into a unified design system spec.

Public entry point: ``run_design_research``.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from app.services.landing_gemini import grounded_research, looks_degenerate

logger = logging.getLogger(__name__)


# ── Prompts ───────────────────────────────────────────────────────────

_VISUAL_RESEARCH_PROMPT = """You are conducting visual design research for a {category} ({subcategory}) targeting {audience_primary}.

Brand personality: {personality}
Tone: {tone}
Region: {geo_specifics}

⚠️ SEARCH-FIRST: Run google_search at least 3 times BEFORE writing. Visit Awwwards, SiteInspire, Land-Book, OnePageLove, Lapa.ninja, and direct competitor sites. Cite real URLs from search results — never invent.

⚠️ DO NOT use generic design knowledge. Find SPECIFIC current examples.

Recommended initial searches:
  1. "best {category} websites 2025" OR "{category} site awwwards"
  2. "{category} design inspiration"
  3. "{category} brand identity examples"

Return markdown with EXACTLY these 5 sections:

===VISUAL_REFERENCE_SITES===
8-10 entries. ONE block per site, formatted as:
• <name> — <url>
  Style: <5-7 specific adjectives>
  What works: <3-4 concrete observations>
  What we'd adapt: <1-2 specific elements>

===DOMINANT_PARADIGMS===
3-5 paradigms commonly used in this category. For each:
• <paradigm name (editorial / brutalist / minimalist-swiss / maximalist-expressive / glassmorphism / hand-crafted-illustrated / bento-grid / etc.)> — <when it works> — <when it doesn't> — <one current example URL>

===DISTINCTIVE_ELEMENTS===
Patterns recurring across the references. 5-8 bullets, each with a real URL:
• Animation styles (none / subtle / expressive)
• Photography style (lifestyle / product / abstract / illustrated)
• Texture and material treatments
• Border, shadow, depth conventions
• Notable signature elements

===CATEGORY_VISUAL_CODES===
What signals "this is a {category} business" visually? 4-6 bullets:
• Visual cues the audience expects (with example URL)
• Color associations the audience expects
• Imagery types the audience trusts
• Visual elements to AVOID for category fit (with anti-example URL if possible)

===ASPIRATIONAL_VS_PRACTICAL===
3-4 bullets on the tension:
• What does this audience aspire to visually?
• What feels approachable vs intimidating?
• Where to lean aspirational vs grounded?

===REFERENCES===
Plain deduped list of every URL cited above. Minimum 8 distinct domains.

HARD RULES:
- Never repeat the same headline more than once.
- Never paste raw search-result snippets — write in analytical voice.
"""


_TYPOGRAPHY_RESEARCH_PROMPT = """You are researching typography for a {category} business with {personality} personality.

⚠️ SEARCH-FIRST: Run google_search 2-3 times. Visit Fonts In Use, Typewolf, Google Fonts trending, and direct competitor sites.

⚠️ All recommended fonts MUST be available on Google Fonts (or open-source equivalent named explicitly). No paid-only foundry fonts.

Recommended searches:
  1. "{category} typography examples"
  2. "fonts in use {category}" OR "typewolf {category}"
  3. "best Google Fonts for {category}"

Return markdown with EXACTLY these 4 sections:

===TYPEFACE_PAIRINGS===
5-7 pairings. ONE block per pairing:
• Pairing: <Display Font> + <Body Font>
  Display weights: <list>
  Body weights: <list>
  Used by: <real site name and URL>
  Personality: <one sentence>
  Best for: <use case in one phrase>
  On Google Fonts: <yes/no — if no, name closest substitute>

===CATEGORY_CONVENTIONS===
What types of typefaces fit {category}? 3-5 bullets, each with example URL:
• Serif vs sans-serif convention
• Script / display use cases (if any)
• Mono use cases (if any)
• Common stylistic anti-patterns

===HIERARCHY_PATTERNS===
4-5 bullets, observed from references:
• Heading size scales (e.g. "h1 at 56-72px on desktop") with example URL
• Line-height conventions (display vs body)
• Letter-spacing for display vs body vs caps
• Common hierarchy depth (h1 → h6 actually used or not)

===EXPRESSIVE_OPPORTUNITIES===
3-5 bullets:
• Where can typography be bold/distinctive?
• Mixed-weight or mixed-style techniques worth borrowing (with URL)
• Type-as-imagery moments (with URL)
• Accessibility floors (min body size, contrast, weights to avoid)

===REFERENCES===
Plain deduped list of every URL cited. Minimum 6 sources.

HARD RULES:
- Every pairing must list real Google Fonts family names (e.g. "Playfair Display", "Inter", "Cormorant Garamond").
- Never paste raw search-result snippets.
"""


_COLOR_RESEARCH_PROMPT = """You are researching color palettes for a {category} business with {personality} personality, targeting {audience_primary} in {geo_specifics}.

⚠️ SEARCH-FIRST: Run google_search 2-3 times. Visit Coolors, Adobe Color, Awwwards palette galleries, and real competitor sites. Cite real URLs.

⚠️ Every palette MUST include HSL + hex values. Every text-on-background combination MUST cite a WCAG AA pass/fail.

Recommended searches:
  1. "{category} color palette examples"
  2. "{category} brand colors" OR "{category} website colors"
  3. "color psychology {category}"

Return markdown with EXACTLY these 4 sections:

===CATEGORY_COLOR_ASSOCIATIONS===
4-6 bullets, each with a real URL:
• Conventional colors for {category} and why
• Colors that signal trust vs distrust
• Colors that energize vs calm this audience
• Cultural color considerations specific to {geo_specifics}

===RECOMMENDED_PALETTES===
3-4 distinct palettes. ONE block per palette:
• Palette name: <descriptive, e.g. "Warm Tuscan Earth">
  Primary:    HSL <H S% L%> / Hex <#xxxxxx>
  Secondary:  HSL <H S% L%> / Hex <#xxxxxx>
  Accent:     HSL <H S% L%> / Hex <#xxxxxx>
  Background: HSL <H S% L%> / Hex <#xxxxxx>
  Foreground: HSL <H S% L%> / Hex <#xxxxxx>
  Muted:      HSL <H S% L%> / Hex <#xxxxxx>
  Border:     HSL <H S% L%> / Hex <#xxxxxx>
  Card:       HSL <H S% L%> / Hex <#xxxxxx>
  Used by (real site): <name + URL>
  Personality: <one sentence>
  WCAG AA: foreground-on-background <PASS / FAIL with ratio>; primary-on-background <PASS / FAIL>

===DARK_MODE_ADAPTATION===
For each recommended palette: how does it translate to dark mode?
3-4 bullets per palette is fine; focus on which slots invert vs which stay.

===COMBINATIONS_TO_AVOID===
3-5 bullets:
• <slot vs slot combination> — <reason it fails> — <example URL where you spotted it>

===REFERENCES===
Plain deduped list of every URL cited. Minimum 5 sources.

HARD RULES:
- Every palette has 8 slots filled with both HSL and hex.
- Never paste raw search-result snippets.
- Use HSL format "H S% L%" — no commas, no hsl() wrapper.
"""


_LAYOUT_RESEARCH_PROMPT = """You are researching layout and structural design patterns for a {category} landing page.

Primary purpose: {primary_purpose}
Audience: {audience_primary}
Required sections: {must_have_sections}

⚠️ SEARCH-FIRST: Run google_search 2-3 times. Visit Awwwards, OnePageLove, Land-Book, and successful competitor sites. Every pattern must cite a real example URL.

Recommended searches:
  1. "best {category} landing page 2025"
  2. "{category} hero section examples"
  3. "{primary_purpose} landing page conversion"

Return markdown with EXACTLY these 5 sections:

===HERO_PATTERNS===
4-6 hero patterns common in this category. ONE block per pattern:
• Pattern: <name, e.g. "Centered headline + media right" / "Full-bleed video bg" / "Asymmetric split with CTA stack" / "Type-wrapping product" / "Card stack hero">
  When it works: <one sentence>
  Real example: <site name and URL>
  Conversion notes: <one observation>
  Fits our brief? <yes / no / maybe — one-line why>

===SECTION_RHYTHM===
How successful pages alternate dense vs spacious sections. 3-5 bullets:
• <observation> — <example URL with section-by-section breakdown>
Include where social proof typically lands and how monotony is broken.

===PER_REQUIRED_SECTION_PATTERNS===
For 5-6 of the most important must_have_sections, list 2-3 ways it's commonly designed.
Format:
• <section type>:
  - <approach 1> — <real example URL>
  - <approach 2> — <real example URL>
  - <approach 3> — <real example URL>
  Best for our brief: <pick one + one-line why>

===CONVERSION_PLACEMENT===
4-5 bullets, each with example URL:
• Where do CTAs appear in this category?
• How many CTAs per page is typical?
• What microcopy works best?
• Form length and field conventions
• Above-fold vs scroll-triggered CTAs

===MOBILE_PATTERNS===
3-5 bullets, each with example URL or mobile-specific observation:
• How do these layouts adapt at 375px?
• Mobile-specific considerations for this category
• Sticky elements / bottom-bar CTAs / collapsing nav patterns

===REFERENCES===
Plain deduped list of every URL cited. Minimum 12 unique URLs across all patterns.

HARD RULES:
- Every pattern cites a real example URL — no abstract patterns without grounding.
- Never repeat the same example URL more than 3 times across the document.
- Never paste raw search-result snippets.
"""


# ── Public entry point ────────────────────────────────────────────────

async def run_design_research(
    intent: dict,
    *,
    websocket: Any = None,
    timeout_s: float = 240.0,
    purpose_data: dict | None = None,
) -> dict[str, Any]:
    """Stage 3 — run 4 parallel grounded design research calls.

    Auth handled by gemini_post via Vertex ADC. Returns a dict shaped:
      {
        "visual":     {"text": str, "sources": int, "urls": [...]},
        "typography": {"text": str, "sources": int, "urls": [...]},
        "color":      {"text": str, "sources": int, "urls": [...]},
        "layout":     {"text": str, "sources": int, "urls": [...]},
        "_summary": {
          "total_sources": int,
          "calls_grounded": int,
          "calls_succeeded": int,
          "calls_degenerate": int,
        },
      }
    """
    fmt_args = _format_args(intent)

    if websocket is not None:
        try:
            await websocket.send_json({
                "type": "progress",
                "message": "🎨 Running 4 parallel design research calls (visual / typography / color / layout)...",
            })
        except Exception:
            pass

    visual_prompt     = _VISUAL_RESEARCH_PROMPT.format(**fmt_args)
    typography_prompt = _TYPOGRAPHY_RESEARCH_PROMPT.format(**fmt_args)
    color_prompt      = _COLOR_RESEARCH_PROMPT.format(**fmt_args)
    layout_prompt     = _LAYOUT_RESEARCH_PROMPT.format(**fmt_args)

    visual, typography, color, layout = await asyncio.gather(
        grounded_research(visual_prompt,     timeout_s, label="visual_research",     websocket=websocket),
        grounded_research(typography_prompt, timeout_s, label="typography_research", websocket=websocket),
        grounded_research(color_prompt,      timeout_s, label="color_research",      websocket=websocket),
        grounded_research(layout_prompt,     timeout_s, label="layout_research",     websocket=websocket),
    )

    result = {
        "visual":     _wrap(visual,     label="visual_research"),
        "typography": _wrap(typography, label="typography_research"),
        "color":      _wrap(color,      label="color_research"),
        "layout":     _wrap(layout,     label="layout_research"),
    }
    result["_summary"] = _summarize(result)

    s = result["_summary"]
    logger.info(
        "run_design_research: ok — calls_succeeded=%d/4 calls_grounded=%d/4 degenerate=%d/4 total_sources=%d",
        s["calls_succeeded"], s["calls_grounded"], s["calls_degenerate"], s["total_sources"],
    )
    return result


# ── Helpers ───────────────────────────────────────────────────────────

def _format_args(intent: dict) -> dict[str, str]:
    audience = intent.get("target_audience") or {}
    personality = intent.get("brand_personality") or []
    must_have = intent.get("must_have_sections") or []
    return {
        "category":             (intent.get("business_category") or "general business").strip(),
        "subcategory":          (intent.get("business_subcategory") or "").strip() or "general",
        "geo_specifics":        (intent.get("geographic_specifics") or "United States").strip(),
        "audience_primary":     (audience.get("primary") or "general consumers").strip(),
        "personality":          ", ".join(personality) if personality else "modern, clear, trustworthy",
        "tone":                 (intent.get("tone") or "friendly").strip(),
        "primary_purpose":      (intent.get("primary_purpose") or "lead_generation").strip(),
        "must_have_sections":   ", ".join(must_have) if must_have else "hero, value_prop, cta",
    }


def _wrap(triple: tuple[str, int, list[str]], *, label: str) -> dict[str, Any]:
    text, sources, urls = triple
    if text and looks_degenerate(text):
        logger.warning(
            "%s: degenerate output detected (repeated content / search-snippet dump) — zeroing call",
            label,
        )
        return {"text": "", "sources": 0, "urls": [], "degenerate": True}
    return {"text": text, "sources": sources, "urls": urls}


def _empty_result() -> dict[str, Any]:
    blank = {"text": "", "sources": 0, "urls": []}
    return {
        "visual":     dict(blank),
        "typography": dict(blank),
        "color":      dict(blank),
        "layout":     dict(blank),
        "_summary": {
            "total_sources": 0,
            "calls_grounded": 0,
            "calls_succeeded": 0,
            "calls_degenerate": 0,
        },
    }


def _summarize(result: dict[str, Any]) -> dict[str, int]:
    keys = ("visual", "typography", "color", "layout")
    total_sources = sum(result[k]["sources"] for k in keys)
    calls_grounded = sum(1 for k in keys if result[k]["sources"] > 0)
    calls_succeeded = sum(1 for k in keys if result[k]["text"])
    calls_degenerate = sum(1 for k in keys if result[k].get("degenerate"))
    return {
        "total_sources": total_sources,
        "calls_grounded": calls_grounded,
        "calls_succeeded": calls_succeeded,
        "calls_degenerate": calls_degenerate,
    }
