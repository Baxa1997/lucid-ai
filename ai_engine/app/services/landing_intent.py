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
        "clarity_level",
        "clarification_questions",
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
        # Named roles the user mentioned (drivers, designers, chefs, etc.).
        # Empty for non-hiring purposes. Used by the recruitment research
        # call and the PURPOSE_DIRECTIVE prompt block.
        "named_roles": {
            "type": "ARRAY",
            "items": {"type": "STRING"},
        },
        # Urgency cues in the prompt ("now hiring", "immediately", "ASAP").
        # Surfaced to Claude to drive copy ("Apply Now" vs "Join Our Team").
        "urgency_signals": {
            "type": "ARRAY",
            "items": {"type": "STRING"},
        },
        # Clarity self-assessment. "high" = the prompt is concrete and
        # actionable on its own. "medium" = some assumptions made, page
        # will still be reasonable. "low" = the prompt is too vague /
        # contradictory / a string of unrelated words and Gemini cannot
        # produce a meaningful page without user input.
        "clarity_level": {"type": "STRING"},
        # Up to 3 questions the model wants to ask the user before
        # research begins. Empty when clarity_level=high. The pipeline
        # surfaces these as clarification UI; the user's answers are
        # appended to the prompt context on the next pass.
        "clarification_questions": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "required": ["key", "question", "options"],
                "properties": {
                    "key":       {"type": "STRING"},
                    "question":  {"type": "STRING"},
                    "options": {
                        "type": "ARRAY",
                        "items": {
                            "type": "OBJECT",
                            "required": ["id", "label"],
                            "properties": {
                                "id":    {"type": "STRING"},
                                "label": {"type": "STRING"},
                                "hint":  {"type": "STRING"},
                            },
                        },
                    },
                },
            },
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
9. NAMED ROLES. If primary_purpose is "hiring", extract every job title the user named (e.g. "CDL-A drivers", "owner-operators", "line cooks", "senior backend engineers"). Preserve the exact wording the user used. Empty array when not hiring.
10. URGENCY SIGNALS. Extract any urgency phrases verbatim from the prompt ("now hiring", "immediately", "ASAP", "this week"). Empty array when none. These drive the CTA tone.
11. PURPOSE PRIORITY. Named job roles → primary_purpose=hiring (regardless of industry). "get a quote" / "request pricing" / "contact sales" → lead_generation. The PURPOSE is what the user wants visitors to DO; industry is secondary.

12. CLARITY SELF-ASSESSMENT (clarity_level). Set to:
     • "high"   — the prompt names a concrete business + purpose; you can build a meaningful page without asking anything.
     • "medium" — you filled gaps with reasonable defaults (assumed audience, geography, sub-segment) but the page will still be coherent.
     • "low"    — the prompt is too vague to act on: random word salad, contradictory signals, generic categories with multiple equally-valid interpretations that produce very different pages (e.g. "house renting agency" — could be rentals-only OR rentals+sales+management; "fitness app" — workouts vs nutrition vs community).
   Be honest. Defaulting to "high" on vague prompts produces generic boilerplate.

13. CLARIFICATION QUESTIONS (clarification_questions). When clarity_level is "low", produce 1–3 questions that, if answered, would unlock a great page. Each question:
     • key:       short snake_case identifier (e.g. "rental_focus", "audience_segment", "primary_offering")
     • question:  one sentence, plain language. NEVER yes/no.
     • options:   2–4 mutually-exclusive choices, each with id (snake_case) + label (5–10 words) + optional hint (one sentence on what changes).
   Pick questions whose answers would PIVOT the page (different sections, different copy, different research direction) — not cosmetic preferences. When clarity_level is "high" or "medium", return an empty array.
   GOOD question for "house renting agency":
     {{key:"rental_focus", question:"What does this agency primarily handle?",
       options:[
         {{id:"rentals_only",   label:"Rental listings only — tenants searching for places"}},
         {{id:"rentals_and_management", label:"Rentals + property management for landlords"}},
         {{id:"rentals_and_sales", label:"Both rentals and home sales"}}]}}
   BAD question (cosmetic): "What color palette would you prefer?" — this never blocks page generation.

ENUMS — these fields MUST use one of these exact values:
  primary_purpose: {primary_purposes}
  geographic_scope: {geographic_scopes}
  tone: {tones}
  language: ISO 639-1 lowercase code, e.g. "en", "es", "fr"
  clarity_level: high | medium | low

OUTPUT: just the JSON object. No prose around it.
"""


# ── Public entry point ────────────────────────────────────────────────

async def _emit_fallback_warning(websocket, reason: str) -> None:
    """Tell the user — via the workspace chat — that Gemini wasn't reachable.

    Without this, when Gemini fails (bad ADC, quota, transient outage) the
    pipeline silently uses ``_fallback_intent`` and the user has no clue
    why their carefully-worded prompt produced a generic placeholder page.
    """
    if websocket is None:
        return
    try:
        await websocket.send_json({
            "type": "warning",
            "code": "FALLBACK_INTENT",
            "message": (
                f"⚠️ Intent analysis unavailable ({reason}). "
                "Falling back to a generic intent — page may miss domain-specific sections. "
                "Check ai_engine logs."
            ),
        })
    except Exception:
        pass


async def analyze_intent(
    description: str,
    classification: dict | None = None,
    *,
    websocket: Any = None,
    timeout_s: float = 60.0,
) -> dict[str, Any]:
    """Stage 1 — disambiguate prompt into a structured intent object.

    Returns a dict matching ``_INTENT_SCHEMA`` with all required fields
    populated. Falls back to ``_fallback_intent`` on any failure so
    downstream stages never see None. Auth is handled inside ``gemini_post``
    via Vertex ADC — callers do not pass keys.
    """
    classification = classification or {}
    domain = (classification.get("domain") or "general").strip()
    description = (description or "").strip()

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

    raw = await _structured_intent_call(prompt, timeout_s)
    if not raw:
        logger.warning("analyze_intent: Gemini returned empty — fallback")
        await _emit_fallback_warning(websocket, "Gemini intent call returned empty")
        return _fallback_intent(description, domain)

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("analyze_intent: JSON parse failed (%s) — fallback. Head: %s",
                       exc, raw[:200])
        await _emit_fallback_warning(websocket, f"Intent JSON parse failed: {str(exc)[:80]}")
        return _fallback_intent(description, domain)

    intent = _normalize_intent(parsed, description, domain)
    logger.info(
        "analyze_intent: ok — category=%r purpose=%s scope=%s tone=%s sections=%d flags=%d clarity=%s questions=%d",
        intent["business_category"],
        intent["primary_purpose"],
        intent["geographic_scope"],
        intent["tone"],
        len(intent["must_have_sections"]),
        len(intent["ambiguity_flags"]),
        intent["clarity_level"],
        len(intent["clarification_questions"]),
    )
    return intent


# ── Gemini call ───────────────────────────────────────────────────────

async def _structured_intent_call(prompt: str, timeout_s: float) -> str:
    """Flash + JSON output, no tools."""
    from app.services.gemini_http import gemini_post

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

    status, data, _ = await gemini_post(
        model=_INTENT_MODEL,
        payload=payload,
        timeout_s=timeout_s,
        label="intent",
    )
    if status != 200 or data is None:
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

    # Named roles + urgency signals — additive fields used by the
    # recruitment research call and the PURPOSE_DIRECTIVE prompt block.
    # Trimmed to <=8 each so a runaway model doesn't bloat the brief.
    out["named_roles"] = [
        r.strip() for r in (raw.get("named_roles") or [])
        if isinstance(r, str) and r.strip()
    ][:8]
    out["urgency_signals"] = [
        u.strip() for u in (raw.get("urgency_signals") or [])
        if isinstance(u, str) and u.strip()
    ][:6]

    # Clarity self-assessment + clarification questions. Coerce to one of
    # the three allowed values; anything unknown falls back to "medium" so
    # the pipeline never blocks on a missing field.
    clarity = (raw.get("clarity_level") or "").strip().lower()
    out["clarity_level"] = clarity if clarity in ("high", "medium", "low") else "medium"

    raw_qs = raw.get("clarification_questions") or []
    norm_qs: list[dict[str, Any]] = []
    for q in raw_qs[:3]:
        if not isinstance(q, dict):
            continue
        key = (q.get("key") or "").strip().lower().replace(" ", "_")
        question = (q.get("question") or "").strip()
        if not key or not question:
            continue
        opts_raw = q.get("options") or []
        opts: list[dict[str, str]] = []
        for o in opts_raw[:4]:
            if not isinstance(o, dict):
                continue
            oid = (o.get("id") or "").strip().lower().replace(" ", "_")
            label = (o.get("label") or "").strip()
            if not oid or not label:
                continue
            entry: dict[str, str] = {"id": oid, "label": label}
            hint = (o.get("hint") or "").strip()
            if hint:
                entry["hint"] = hint
            opts.append(entry)
        if len(opts) < 2:
            continue
        norm_qs.append({"key": key, "question": question, "options": opts})
    out["clarification_questions"] = norm_qs

    # If the model said clarity=low but supplied no usable questions,
    # demote to medium so the pipeline doesn't deadlock.
    if out["clarity_level"] == "low" and not out["clarification_questions"]:
        out["clarity_level"] = "medium"

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
        "named_roles":          [],
        "urgency_signals":      [],
        "clarity_level":        "medium",
        "clarification_questions": [],
    }
