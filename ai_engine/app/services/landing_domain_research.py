"""Landing-page Stage 2 — Domain Research (4 parallel grounded calls).

Pulls SPECIFIC, GROUNDED facts from the live web so downstream
synthesis (Stage 4) and codegen (Stage 6+) can produce copy that feels
real instead of AI-generic.

Four parallel ``gemini-2.5-pro + google_search`` calls:
  1. BUSINESS    — industry terminology, KPIs, pain points, proof
  2. AUDIENCE    — language, demographics, psychographic, buying behavior
  3. REGIONAL    — local terms, cultural touchpoints, local competitors
  4. COMPETITIVE — competitor inventory, common patterns, white space

Each call emits markdown with embedded URL citations. We log
``grounding_source_count`` per call as a quality signal — calls with 0
sources mean Gemini answered from training memory, which is the failure
mode the spec explicitly tries to prevent.

We also defend against a second failure mode: the model's tool-use loop
sometimes regurgitates raw search-snippet text instead of researching
(observed: 30k chars of "Brooklyn Magazine | Food, Arts, Culture..."
repeated 100x). ``_looks_degenerate`` catches that and zeroes the call
out so synthesis isn't poisoned by junk.

Output is consumed by Stage 4 (content strategy synthesis), which will
extract structured facts from the markdown.

Public entry point: ``run_domain_research``.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from app.services.landing_gemini import grounded_research, looks_degenerate
from app.services.pipeline.constants import _FALLBACK_GEMINI_KEY

logger = logging.getLogger(__name__)


# ── Prompts ───────────────────────────────────────────────────────────

_BUSINESS_RESEARCH_PROMPT = """You are conducting deep business research for a {category} ({subcategory}) operating in {geo_specifics}.

⚠️ SEARCH-FIRST: Run google_search at least 3 times BEFORE writing anything. Cite real URLs from search results — never invent or recall a URL. If you cannot find 7 distinct sources, expand your search queries until you do.

Recommended initial searches:
  1. "{category} industry terminology" OR "{category} jargon glossary"
  2. "{category} customer reviews complaints" OR "{category} reddit"
  3. "{category} certifications awards"

Find REAL, SPECIFIC details. Do NOT generalize. Return markdown with these sections:

===INDUSTRY_TERMINOLOGY===
List 10-15 terms (jargon, acronyms, technical vocabulary) used by professionals in this category.
Each line: <term> — <one-line definition> [source URL]

===KEY_METRICS===
5-8 industry-standard KPIs that customers in this category care about.
Each line: <metric> — <typical benchmark or range> [source URL]

===PAIN_POINTS===
5-7 specific frustrations real customers voice. Quote directly from forums, reviews, industry publications.
Each line: <pain point> — "<verbatim quote>" — <source URL>

===DIFFERENTIATION===
5-7 things top-tier businesses in this category do that average ones don't. Concrete features, not abstract qualities.
Each line: <differentiator> — <real example business name> [source URL]

===PROOF_ELEMENTS===
5-7 credentials, certifications, association memberships, or awards customers in this category trust.
Each line: <name of certification/award> — <issuing body> [source URL]

===PRICING_NORMS===
How businesses in this category typically structure offers. Include 3-5 real price points or service tiers.
Each line: <tier or package> — <price range> [source URL]

===SEASONAL_FACTORS===
2-4 buying seasons or industry cycles that affect messaging timing.
Each line: <season/cycle> — <effect on demand> [source URL]

===REFERENCES===
Plain list of every URL cited above (deduped). Minimum 7 distinct domains.
"""


_AUDIENCE_RESEARCH_PROMPT = """You are researching the target audience for a {category} business.

Target: {audience_primary}
Psychographic: {audience_psychographic}

⚠️ SEARCH-FIRST: Run google_search at least 3 times BEFORE writing. Prioritize first-person customer voice — Reddit threads, Yelp/Google reviews, industry forums, social discussions. NEVER invent a quote. Every quote must come from a real source you can cite.

Recommended initial searches:
  1. "{category} reddit customers" OR "{audience_primary} reddit"
  2. "{category} customer reviews" OR "{category} testimonials"
  3. "what {audience_primary} look for in {category}"

Return markdown with these sections:

===AUDIENCE_LANGUAGE===
15-20 phrases this audience actually uses. Mix:
  • Phrases describing their PROBLEMS ("I just want X without Y")
  • Phrases describing what they WANT ("looking for somewhere that...")
  • Words that BUILD trust vs words that TURN THEM OFF
Format each line EXACTLY like: "the actual quote" — context (URL)
Do NOT wrap quotes in tags or brackets. Just put the quote in double quotes, an em dash, the context, then the URL in parens.
Example: "I just want a place where I can hear myself think." — Reddit, r/AskNYC (https://reddit.com/r/AskNYC/...)

===DEMOGRAPHICS===
Specific not generic. Each as one short bullet with source:
  • Age range — <specific band> [source URL]
  • Income — <bracket> [source URL]
  • Occupation — <patterns> [source URL]
  • Family — <status patterns> [source URL]
  • Education — <level> [source URL]
  • Geographic concentration — <regions/cities> [source URL]

===PSYCHOGRAPHIC===
  • Core values (5-7 specific values, not platitudes)
  • Top 3 anxieties / fears they bring to this purchase
  • Top 3 aspirations / goals
  • Decision-making style (analytical | emotional | social-proof-driven | mixed)
  • Information sources they trust (specific publications, podcasts, communities)

===BUYING_BEHAVIOR===
  • Research path (how they discover → compare → decide)
  • Decision cycle length (hours | days | weeks | months)
  • Other people involved in the decision
  • Common objections (3-5)
  • What closes the sale (3-5 closers)

===COMMUNICATION_PREFERENCES===
  • Formal vs casual? Technical vs accessible? Visual vs text? Long-form vs scannable?
  • Each preference with a one-line rationale + source URL

===TRIGGER_MOMENTS===
3-5 life events, business situations, or external triggers that create demand.
Each line: <trigger> — <why it drives a purchase> [source URL]

===REFERENCES===
Plain list of every URL cited above (deduped). Minimum 5 distinct domains, prioritizing user-voice sources.
"""


_REGIONAL_RESEARCH_PROMPT = """You are researching how to make a {category} business feel ROOTED in {geo_specifics} — not generic.

⚠️ DO NOT dump search snippets. DO NOT list news headlines repeatedly. WRITE in your own words, citing a real URL after each claim.

⚠️ SEARCH-FIRST: Run google_search 2-3 times. Then SYNTHESIZE — don't paste raw results.

Recommended searches (run AT LEAST these two):
  1. "best {category} {geo_specifics}"
  2. "{geo_specifics} neighborhood guide" OR "{geo_specifics} local culture"

Return markdown with EXACTLY these 4 sections, in this order. Every line must end with a real URL in parens.

===CULTURAL_TOUCHPOINTS===
5-7 references locals in {geo_specifics} would recognize that a {category} could authentically nod to: neighborhoods, landmarks, communities, recurring events, pride points.
Format: <touchpoint name> — <one-sentence context> (URL)
Example: Smith Street — long-established Italian-American spine of Carroll Gardens, still home to old-school red-sauce holdouts (https://www.brownstoner.com/...)

===LOCAL_COMPETITORS===
5-8 REAL businesses in this category operating in {geo_specifics}. ONE entry per business, formatted as:
• <name> — <url>
  Positioning: <one sentence: what they emphasize>
  Tier: <budget | mid | premium>

===LOCAL_VOICE===
How businesses in {geo_specifics} present themselves. 3 short bullets, each with a real URL:
• Visual conventions: <3-5 adjectives observed across local sites> (URL of one example)
• Tonal conventions: <3-5 adjectives> (URL of one example)
• Shared local values they emphasize: <3-4 values> (URL of one example)

===REGIONAL_PRODUCTS===
4-6 things locally sourced, locally made, or regionally specialized that a {category} could authentically reference.
Format: <product/ingredient/material> — <local supplier, neighborhood, or origin> (URL)

===REFERENCES===
Plain deduped list of every URL cited above. Minimum 5 local-leaning sources (Eater Brooklyn / Brownstoner / The Infatuation NY / Brooklyn Magazine / individual restaurant or business sites in the area / local NYT or Brooklyn Paper articles, etc.).

HARD RULES:
- NEVER repeat the same sentence or headline more than once. Each line is unique.
- NEVER paste a search-result snippet verbatim. Rewrite in your own analytical voice.
- If the audience is sparse for {geo_specifics}, expand to "{geo_specifics} surrounding area" rather than padding with duplicates.
"""


_COMPETITIVE_RESEARCH_PROMPT = """You are conducting competitive research for a {category} business targeting {audience_primary}.

⚠️ SEARCH-FIRST: Run google_search at least 4 times. Find 8-10 REAL competitor websites, visit them, analyze them. Do NOT use famous brands you remember — find real current competitors via search.

Recommended initial searches:
  1. "best {category} websites 2025"
  2. "top {category} {geo_specifics}" (if regional)
  3. "{category} brand examples awwwards" OR "{category} site inspiration"
  4. specific search drawn from the prompt itself.

Return markdown with these sections:

===COMPETITOR_INVENTORY===
8-10 entries. Each entry on its own block:
  • Name — URL
  • Tagline (verbatim from their site)
  • Positioning (1 sentence: what they emphasize)
  • Visual style (3-5 specific adjectives)
  • Key sections their homepage has (in order: hero → ... → footer)
  • Pricing tier (budget | mid | premium)
  • Unique angle / differentiator

===COMMON_PATTERNS===
What 70%+ of competitors do. Bullet each:
  • Sections that appear on most sites
  • Recurring messaging themes (3-5)
  • Visual conventions (3-5)
  • CTAs and conversion patterns (3-5)

===WHITE_SPACE===
What's MISSING in the market — angles competitors aren't taking. 5-7 specific opportunities.
Each line: <opportunity> — <why no one is doing it / why it would work>

===BEST_IN_CLASS===
Top 2-3 competitors. For each:
  • Name — URL
  • Why they stand out (3-5 specific observations)
  • What we should EMULATE (not copy)

===ANTI_PATTERNS===
Common mistakes in this category. 5-7 things to avoid.
Each line: <anti-pattern> — <real example URL where you spotted it>

===REFERENCES===
Plain list of every URL cited (deduped). Minimum 8 distinct competitor domains analyzed.
"""


# ── Public entry point ────────────────────────────────────────────────

async def run_domain_research(
    intent: dict,
    *,
    gemini_key: str | None = None,
    websocket: Any = None,
    timeout_s: float = 240.0,
) -> dict[str, Any]:
    """Stage 2 — run 4 parallel grounded research calls.

    Returns a dict shaped:
      {
        "business":    {"text": str, "sources": int, "urls": [...]},
        "audience":    {"text": str, "sources": int, "urls": [...]},
        "regional":    {"text": str, "sources": int, "urls": [...]},
        "competitive": {"text": str, "sources": int, "urls": [...]},
        "_summary": {
          "total_sources": int,
          "calls_grounded": int,  # how many had >0 sources
          "calls_succeeded": int, # how many returned non-empty text
        },
      }

    Empty dict on missing API key — caller decides how to recover.
    """
    key = (gemini_key or _FALLBACK_GEMINI_KEY or os.environ.get("GOOGLE_API_KEY", "")).strip()
    if not key:
        logger.warning("run_domain_research: no Gemini key — skipping research")
        return _empty_result()

    fmt_args = _format_args(intent)

    if websocket is not None:
        try:
            await websocket.send_json({
                "type": "progress",
                "message": "🔎 Running 4 parallel domain research calls (business / audience / regional / competitive)...",
            })
        except Exception:
            pass

    business_prompt    = _BUSINESS_RESEARCH_PROMPT.format(**fmt_args)
    audience_prompt    = _AUDIENCE_RESEARCH_PROMPT.format(**fmt_args)
    regional_prompt    = _REGIONAL_RESEARCH_PROMPT.format(**fmt_args)
    competitive_prompt = _COMPETITIVE_RESEARCH_PROMPT.format(**fmt_args)

    business, audience, regional, competitive = await asyncio.gather(
        grounded_research(business_prompt,    key, timeout_s, label="business_research",    websocket=websocket),
        grounded_research(audience_prompt,    key, timeout_s, label="audience_research",    websocket=websocket),
        grounded_research(regional_prompt,    key, timeout_s, label="regional_research",    websocket=websocket),
        grounded_research(competitive_prompt, key, timeout_s, label="competitive_research", websocket=websocket),
    )

    result = {
        "business":    _wrap(business,    label="business_research"),
        "audience":    _wrap(audience,    label="audience_research"),
        "regional":    _wrap(regional,    label="regional_research"),
        "competitive": _wrap(competitive, label="competitive_research"),
    }
    result["_summary"] = _summarize(result)

    s = result["_summary"]
    logger.info(
        "run_domain_research: ok — calls_succeeded=%d/4 calls_grounded=%d/4 degenerate=%d/4 total_sources=%d",
        s["calls_succeeded"], s["calls_grounded"], s["calls_degenerate"], s["total_sources"],
    )
    return result


# ── Helpers ───────────────────────────────────────────────────────────

def _format_args(intent: dict) -> dict[str, str]:
    """Pull the prompt-injection fields from intent with safe defaults."""
    audience = intent.get("target_audience") or {}
    return {
        "category":             (intent.get("business_category") or "general business").strip(),
        "subcategory":          (intent.get("business_subcategory") or "").strip() or "general",
        "geo_specifics":        (intent.get("geographic_specifics") or "United States").strip(),
        "audience_primary":     (audience.get("primary") or "general consumers").strip(),
        "audience_psychographic": (audience.get("psychographic") or "values clarity and authenticity").strip(),
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
        "business":    dict(blank),
        "audience":    dict(blank),
        "regional":    dict(blank),
        "competitive": dict(blank),
        "_summary": {
            "total_sources": 0,
            "calls_grounded": 0,
            "calls_succeeded": 0,
            "calls_degenerate": 0,
        },
    }


def _summarize(result: dict[str, Any]) -> dict[str, int]:
    keys = ("business", "audience", "regional", "competitive")
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
