"""Landing-page Stage 1 — Intent Analysis & Disambiguation.

Takes the raw user prompt + a coarse domain classification and produces a
structured intent object that downstream stages (domain/design research,
content strategy, design system) consume. The goal is to decide WHAT we
are building before we research anything: business category, audience,
geography, primary purpose, must-have sections, tone, ambiguity flags.

This is a single Gemini Flash call with structured-JSON output. No web
grounding — we are interpreting the user's input, not researching the
world. Total wall time ~3-8s.

The output schema follows ``Stage 1`` of the landing pipeline spec. It
must be valid even when Gemini fails: ``_fallback_intent`` returns
sensible defaults derived from prompt + classification so downstream
stages always have something to read.

Public entry point: ``analyze_intent``.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx

from app.services.pipeline.constants import _FALLBACK_GEMINI_KEY

# Flash is plenty for an interpretation task — no tools, just JSON.
_INTENT_MODEL = os.environ.get("LANDING_INTENT_MODEL", "gemini-2.5-flash")

logger = logging.getLogger(__name__)


# ── Allowed enum values (also enforced post-hoc in _normalize_intent) ──

_PRIMARY_PURPOSES = [
    "lead_generation",
    "ecommerce",
    "hiring",
    "brand_awareness",
    "booking",
    "signup",
]

_GEOGRAPHIC_SCOPES = ["hyperlocal", "regional", "national", "international"]

_TONES = [
    "formal",
    "friendly",
    "authoritative",
    "playful",
    "technical",
    "warm",
    "bold",
    "luxurious",
]

# Purpose → required sections (spec: "hard-code purpose-to-section mappings").
# These are merged with whatever the model emits so a hiring page always has
# an application form, a booking page always has a scheduler, etc.
_PURPOSE_REQUIRED_SECTIONS: dict[str, list[str]] = {
    "lead_generation": ["hero", "value_prop", "trust_signals", "contact_form"],
    "ecommerce":       ["hero", "products", "trust_signals", "testimonials"],
    "hiring":          ["hero", "culture", "open_roles", "application_form"],
    "brand_awareness": ["hero", "story", "gallery", "press"],
    "booking":         ["hero", "services", "booking_form", "testimonials"],
    "signup":          ["hero", "value_prop", "features", "signup_form"],
}


# ── Structured output schema (Gemini responseSchema) ──────────────────

_INTENT_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "required": [
        "business_category",
        "business_subcategory",
        "primary_purpose",
        "target_audience",
        "geographic_scope",
        "geographic_specifics",
        "brand_personality",
        "must_have_sections",
        "tone",
        "language",
        "ambiguity_flags",
    ],
    "properties": {
        "business_category":    {"type": "STRING"},
        "business_subcategory": {"type": "STRING"},
        "primary_purpose":      {"type": "STRING"},
        "secondary_purposes": {
            "type": "ARRAY",
            "items": {"type": "STRING"},
        },
        "target_audience": {
            "type": "OBJECT",
            "required": ["primary", "psychographic"],
            "properties": {
                "primary":       {"type": "STRING"},
                "secondary":     {"type": "STRING"},
                "psychographic": {"type": "STRING"},
            },
        },
        "geographic_scope":     {"type": "STRING"},
        "geographic_specifics": {"type": "STRING"},
        "brand_personality": {
            "type": "ARRAY",
            "items": {"type": "STRING"},
        },
        "must_have_sections": {
            "type": "ARRAY",
            "items": {"type": "STRING"},
        },
        "tone":     {"type": "STRING"},
        "language": {"type": "STRING"},
        "ambiguity_flags": {
            "type": "ARRAY",
            "items": {"type": "STRING"},
        },
    },
}


# ── Prompt ────────────────────────────────────────────────────────────

_INTENT_PROMPT = """You are an intent analyst for a landing-page generator. The user has described what they want; your job is to disambiguate it into a structured brief BEFORE any research happens.

USER PROMPT: "{description}"
COARSE DOMAIN (already classified): {domain}

Return a single JSON object matching the response schema. Apply these rules:

1. SPECIFICITY. Resolve vague prompts to the MOST COMMON variant of the category. "restaurant" → casual dining, not fast food. "agricultural" → farm-to-table marketplace unless prompt says equipment / cooperative / chemical etc.
2. INFER AGGRESSIVELY when the prompt is short (< 10 words). Don't punt with "unknown" — pick the most likely interpretation and add an ambiguity_flag noting it.
3. GEOGRAPHY. If unspecified, infer from language and cultural cues. Default to English-speaking markets with broad appeal. For "Brooklyn restaurant" set geographic_scope=hyperlocal, geographic_specifics="Brooklyn, NY". For generic prompts use national / "United States".
4. PURPOSE. If unclear: B2B → lead_generation. B2C product/service → brand_awareness. SaaS → signup. Restaurant/spa/salon → booking. Job postings → hiring. Online store → ecommerce.
5. AUDIENCE. Be specific. NOT "everyone" / "all ages". Real demographic: age band, occupation pattern, motivations.
6. PERSONALITY. 3-5 single-word traits that match the category and audience. Example for fine-dining: ["refined", "warm", "rooted", "confident"]. NOT generic words like "modern" / "professional".
7. MUST-HAVE SECTIONS. Pick from this canonical list, derived from the primary purpose:
     hero, value_prop, features, products, services, menu, gallery, story, testimonials, trust_signals, press, stats, faq, pricing, comparison, how_it_works, process, team, culture, open_roles, locations, hours, contact, contact_form, booking_form, application_form, signup_form, newsletter, cta
   Order matters — list the order they should appear in the page.
8. AMBIGUITY FLAGS. List EVERY assumption you made because the prompt was unclear. Examples: "business_subcategory_assumed", "geography_assumed", "audience_assumed", "purpose_assumed".

ENUMS — these fields MUST use one of these exact values:
  primary_purpose: {primary_purposes}
  geographic_scope: {geographic_scopes}
  tone: {tones}
  language: ISO 639-1 lowercase code, e.g. "en", "es", "fr"

OUTPUT: just the JSON object. No prose around it.
"""


# ── Public entry point ────────────────────────────────────────────────

async def analyze_intent(
    description: str,
    classification: dict | None = None,
    *,
    gemini_key: str | None = None,
    websocket: Any = None,
    timeout_s: float = 60.0,
) -> dict[str, Any]:
    """Stage 1 — disambiguate prompt into a structured intent object.

    Returns a dict matching ``_INTENT_SCHEMA`` with all required fields
    populated. Falls back to ``_fallback_intent`` on any failure so
    downstream stages never see None.
    """
    classification = classification or {}
    domain = (classification.get("domain") or "general").strip()
    description = (description or "").strip()

    key = (gemini_key or _FALLBACK_GEMINI_KEY or os.environ.get("GOOGLE_API_KEY", "")).strip()
    if not key:
        logger.warning("analyze_intent: no Gemini key — returning fallback intent")
        return _fallback_intent(description, domain)

    if websocket is not None:
        try:
            await websocket.send_json({
                "type": "progress",
                "message": "🎯 Analyzing intent and disambiguating...",
            })
        except Exception:
            pass

    prompt = _INTENT_PROMPT.format(
        description=description,
        domain=domain,
        primary_purposes=_PRIMARY_PURPOSES,
        geographic_scopes=_GEOGRAPHIC_SCOPES,
        tones=_TONES,
    )

    raw = await _structured_intent_call(prompt, key, timeout_s)
    if not raw:
        logger.warning("analyze_intent: Gemini returned empty — fallback")
        return _fallback_intent(description, domain)

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("analyze_intent: JSON parse failed (%s) — fallback. Head: %s",
                       exc, raw[:200])
        return _fallback_intent(description, domain)

    intent = _normalize_intent(parsed, description, domain)
    logger.info(
        "analyze_intent: ok — category=%r purpose=%s scope=%s tone=%s sections=%d flags=%d",
        intent["business_category"],
        intent["primary_purpose"],
        intent["geographic_scope"],
        intent["tone"],
        len(intent["must_have_sections"]),
        len(intent["ambiguity_flags"]),
    )
    return intent


# ── Gemini call ───────────────────────────────────────────────────────

async def _structured_intent_call(prompt: str, key: str, timeout_s: float) -> str:
    """Flash + JSON output, no tools."""
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.3,
            "maxOutputTokens": 2048,
            "responseMimeType": "application/json",
            "responseSchema": _INTENT_SCHEMA,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{_INTENT_MODEL}:generateContent?key={key}"
    )

    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.post(url, json=payload)
    except httpx.TimeoutException:
        logger.warning("gemini intent: timeout after %ss", timeout_s)
        return ""
    except Exception as exc:
        logger.warning("gemini intent: transport error — %s", exc)
        return ""

    if resp.status_code != 200:
        logger.warning("gemini intent: HTTP %d — %s", resp.status_code, resp.text[:300])
        return ""

    try:
        data = resp.json()
    except Exception:
        logger.warning("gemini intent: non-JSON response")
        return ""

    # Token billing
    try:
        from app.services.billing_meter import report_token_usage
        u = data.get("usageMetadata") or {}
        _in = int(u.get("promptTokenCount", 0) or 0)
        _out = int(u.get("candidatesTokenCount", 0) or 0) + int(u.get("thoughtsTokenCount", 0) or 0)
        if _in or _out:
            report_token_usage(None, _in, _out, source="gemini_landing_intent")
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

    # Persist for diagnostics
    try:
        with open("/tmp/landing_intent.txt", "w") as fh:
            fh.write(text)
    except Exception:
        pass

    if text:
        logger.info("gemini intent: %d chars", len(text))
    return text


# ── Normalization & fallback ──────────────────────────────────────────

def _normalize_intent(raw: dict, description: str, domain: str) -> dict:
    """Coerce model output to the schema, fill missing fields, enforce enums."""
    out: dict[str, Any] = {}
    fb = _fallback_intent(description, domain)

    # Required scalar strings
    out["business_category"] = (raw.get("business_category") or fb["business_category"]).strip()
    out["business_subcategory"] = (raw.get("business_subcategory") or fb["business_subcategory"]).strip()

    # Enum coercion — fall back to first allowed value if model ignored the list.
    purpose = (raw.get("primary_purpose") or "").strip().lower()
    out["primary_purpose"] = purpose if purpose in _PRIMARY_PURPOSES else fb["primary_purpose"]

    out["secondary_purposes"] = [
        p.strip().lower() for p in (raw.get("secondary_purposes") or [])
        if isinstance(p, str) and p.strip().lower() in _PRIMARY_PURPOSES
        and p.strip().lower() != out["primary_purpose"]
    ]

    # Audience
    aud = raw.get("target_audience") or {}
    out["target_audience"] = {
        "primary":       (aud.get("primary") or fb["target_audience"]["primary"]).strip(),
        "secondary":     (aud.get("secondary") or "").strip(),
        "psychographic": (aud.get("psychographic") or fb["target_audience"]["psychographic"]).strip(),
    }

    scope = (raw.get("geographic_scope") or "").strip().lower()
    out["geographic_scope"] = scope if scope in _GEOGRAPHIC_SCOPES else fb["geographic_scope"]
    out["geographic_specifics"] = (raw.get("geographic_specifics") or fb["geographic_specifics"]).strip()

    # Personality — clamp to 3-5 single-word lowercase traits
    traits = [
        t.strip().lower() for t in (raw.get("brand_personality") or [])
        if isinstance(t, str) and t.strip()
    ]
    if len(traits) < 3:
        for t in fb["brand_personality"]:
            if t not in traits:
                traits.append(t)
            if len(traits) >= 3:
                break
    out["brand_personality"] = traits[:5]

    # Must-have sections — merge model output with purpose-required sections,
    # preserving model's order where possible. Required sections that were
    # dropped get appended (so spec rule "hiring needs application_form" is
    # always honored).
    model_sections = [
        s.strip().lower() for s in (raw.get("must_have_sections") or [])
        if isinstance(s, str) and s.strip()
    ]
    required = _PURPOSE_REQUIRED_SECTIONS.get(out["primary_purpose"], [])
    merged: list[str] = []
    for s in model_sections:
        if s not in merged:
            merged.append(s)
    for s in required:
        if s not in merged:
            merged.append(s)
    if not merged:
        merged = list(required) or ["hero", "value_prop", "cta"]
    out["must_have_sections"] = merged

    tone = (raw.get("tone") or "").strip().lower()
    out["tone"] = tone if tone in _TONES else fb["tone"]

    lang = (raw.get("language") or "en").strip().lower()
    # ISO 639-1 is 2 letters; keep first 2 to be safe.
    out["language"] = (lang[:2] if lang else "en") or "en"

    flags = [
        f.strip() for f in (raw.get("ambiguity_flags") or [])
        if isinstance(f, str) and f.strip()
    ]
    out["ambiguity_flags"] = flags

    return out


def _fallback_intent(description: str, domain: str) -> dict:
    """Minimal valid intent when Gemini is unavailable.

    Uses only the prompt + coarse domain. Marks every assumption with a
    flag so callers know everything is best-effort.
    """
    desc = (description or "").lower()
    dom = (domain or "general").lower()

    # Crude purpose inference from domain hints
    if any(k in dom or k in desc for k in ("saas", "platform", "tool", "app")):
        purpose = "signup"
    elif any(k in dom or k in desc for k in ("restaurant", "salon", "spa", "clinic", "studio")):
        purpose = "booking"
    elif any(k in dom or k in desc for k in ("shop", "store", "ecommerce", "marketplace")):
        purpose = "ecommerce"
    elif any(k in dom or k in desc for k in ("hiring", "careers", "jobs", "recruit")):
        purpose = "hiring"
    else:
        purpose = "lead_generation"

    # Crude geo inference — look for known city/region tokens
    geo_specifics = ""
    geo_scope = "national"
    for hint, scope in (
        ("brooklyn", "hyperlocal"), ("manhattan", "hyperlocal"),
        ("nyc", "hyperlocal"), ("new york", "hyperlocal"),
        ("london", "hyperlocal"), ("paris", "hyperlocal"),
        ("tokyo", "hyperlocal"), ("berlin", "hyperlocal"),
    ):
        if hint in desc:
            geo_specifics = hint.title()
            geo_scope = scope
            break
    if not geo_specifics:
        geo_specifics = "United States"

    return {
        "business_category":    dom if dom != "general" else "general business",
        "business_subcategory": "",
        "primary_purpose":      purpose,
        "secondary_purposes":   [],
        "target_audience": {
            "primary":       "general consumers, 25-55",
            "secondary":     "",
            "psychographic": "values clarity, authenticity, and ease",
        },
        "geographic_scope":     geo_scope,
        "geographic_specifics": geo_specifics,
        "brand_personality":    ["modern", "trustworthy", "clear"],
        "must_have_sections":   _PURPOSE_REQUIRED_SECTIONS.get(purpose, ["hero", "value_prop", "cta"]),
        "tone":                 "friendly",
        "language":             "en",
        "ambiguity_flags":      ["fallback_intent_used"],
    }
