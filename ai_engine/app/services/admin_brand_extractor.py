"""Lightweight brand-signal extractor for standalone admin panels.

Background
----------
The website pipeline runs a heavyweight Stage 3 (``extract_research_signals``
in ``landing_research_extract.py``) that produces a full ``visual_dna`` dict —
section_anatomies, decorative_motifs, live_ui_recipe, typography_voice,
cultural_intensity, cultural_palette_emphasis, palette, photography_style …
ten or so fields, each measured against the website pages it'll drive.

That's overkill for an admin panel. Admins are tools, not editorial
experiences — they don't need section_anatomies (they have one anatomy:
table → form), and they don't need live_ui_recipe motion signatures.
What they DO need is enough brand context to feel different from one
another: a medical clinic admin should not look identical to a creative
agency admin, even though both have leads + contacts + activities.

This module is the "just enough" extractor. One Gemini Flash call, six
fields out, ~$0.005 per admin generation.

For LINKED admins (parent_project_id set), the admin pipeline inherits
the parent's visual_dna directly and never calls this extractor.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from app.services.landing_gemini import structured_distill

logger = logging.getLogger(__name__)


# Picked to match the data_model_planner default (gemini-2.5-flash). It's
# cheap, stable, and we don't need Pro reasoning for a 6-field extraction.
_GEMINI_MODEL = "gemini-2.5-flash"
_TIMEOUT_S = 30.0

# Fallback fixture used when Gemini errors, returns non-JSON, or returns
# something invalid. Matches the shape callers expect so downstream stages
# don't have to defend against partial dicts.
_DEFAULT_SIGNALS: dict[str, Any] = {
    "primary_color":      "#0f172a",
    "typography_voice":   "professional",
    "cultural_intensity": "calm",
    "layout_density":     "comfortable",
    "accent_motif":       "geometric",
}

_VALID_TYPOGRAPHY = {"professional", "friendly", "minimal", "editorial",
                     "playful", "technical", "soft"}
_VALID_INTENSITY  = {"calm", "energetic", "editorial"}
_VALID_DENSITY    = {"compact", "comfortable", "spacious"}
_VALID_MOTIF      = {"geometric", "organic", "industrial", "minimal",
                     "editorial", "playful", "classical"}

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


_SYSTEM_PROMPT = """\
You are extracting visual identity signals for an admin panel UI.
Admin panels are functional tools — they should feel professional but
match the brand context of their domain. A medical clinic admin should
feel different from a creative agency admin.

Return concise, opinionated signals. Don't over-describe. Each field
gets 1-3 words max except primary_color which is a hex code.
"""


def _build_user_prompt(
    *,
    brand_name: str,
    industry: str,
    audience: str,
    purpose: str,
) -> str:
    return f"""\
Based on this admin tool's context, extract visual identity signals.

Tool name: {brand_name}
Industry: {industry}
Audience: {audience}
Purpose: {purpose}

Return JSON only, with this exact shape:
{{
  "primary_color": "#hex",
  "typography_voice": "one word (professional | friendly | minimal | editorial | playful | technical | soft)",
  "cultural_intensity": "calm | energetic | editorial",
  "layout_density": "compact | comfortable | spacious",
  "accent_motif": "one word (geometric | organic | industrial | minimal | editorial | playful | classical)"
}}
"""


def _coerce_signals(raw: dict[str, Any]) -> dict[str, Any]:
    """Validate + normalize Gemini's output against our closed-set vocabs.

    Anything Gemini returns that doesn't match the allowed values falls
    back to the default for that field. The shape of the output dict is
    guaranteed regardless of how rough Gemini's response was.
    """
    out: dict[str, Any] = dict(_DEFAULT_SIGNALS)

    color = (raw.get("primary_color") or "").strip()
    if _HEX_RE.match(color):
        out["primary_color"] = color.lower()

    voice = (raw.get("typography_voice") or "").strip().lower()
    if voice in _VALID_TYPOGRAPHY:
        out["typography_voice"] = voice

    intensity = (raw.get("cultural_intensity") or "").strip().lower()
    if intensity in _VALID_INTENSITY:
        out["cultural_intensity"] = intensity

    density = (raw.get("layout_density") or "").strip().lower()
    if density in _VALID_DENSITY:
        out["layout_density"] = density

    motif = (raw.get("accent_motif") or "").strip().lower()
    if motif in _VALID_MOTIF:
        out["accent_motif"] = motif

    return out


async def extract_admin_brand_signals(
    *,
    intent: dict[str, Any],
    purpose_data: dict[str, Any],
    gemini_key: str = "",
) -> dict[str, Any]:
    """Light-weight Gemini Flash extraction of brand signals for
    standalone admin generation.

    Returns a 6-field dict — ``brand_name`` plus the 5 visual signals
    that admin prompts consume. Always returns a valid dict (defaults
    on extractor failure) so callers never need to guard against None.

    ~$0.005 per call. For linked admins (parent_project_id set), the
    admin pipeline calls ``pipeline_tenant.resolve_tenant_for_project``
    instead and inherits the parent's visual_dna verbatim — skipping
    this extractor entirely.
    """
    _ = gemini_key  # honoured upstream by post_gemini's env-based auth

    brand_name = (
        (intent.get("brand") or {}).get("name")
        or intent.get("business_category")
        or "Admin"
    )
    industry = (purpose_data or {}).get("industry") or "general"
    audience = (purpose_data or {}).get("target_audience") or "internal users"
    purpose = (
        (purpose_data or {}).get("primary_purpose")
        or intent.get("primary_purpose")
        or "operations"
    )

    prompt = _SYSTEM_PROMPT + "\n\n" + _build_user_prompt(
        brand_name=brand_name,
        industry=industry,
        audience=audience,
        purpose=purpose,
    )

    try:
        raw = await structured_distill(
            prompt=prompt,
            timeout_s=_TIMEOUT_S,
            label="admin_brand_extractor",
            response_schema=None,  # mirror data_model_planner: parse + coerce
            max_tokens=1024,
            temperature=0.3,
            model=_GEMINI_MODEL,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "admin_brand_extractor: Gemini call failed (%s) — using defaults", exc,
        )
        return {"brand_name": brand_name, **_DEFAULT_SIGNALS}

    parsed = _safe_parse(raw)
    if not parsed:
        logger.warning(
            "admin_brand_extractor: could not parse response — using defaults",
        )
        return {"brand_name": brand_name, **_DEFAULT_SIGNALS}

    signals = _coerce_signals(parsed)
    logger.info(
        "admin_brand_extractor: brand=%r color=%s voice=%s intensity=%s "
        "density=%s motif=%s",
        brand_name, signals["primary_color"], signals["typography_voice"],
        signals["cultural_intensity"], signals["layout_density"],
        signals["accent_motif"],
    )
    return {"brand_name": brand_name, **signals}


def _safe_parse(raw: str) -> dict[str, Any] | None:
    """Best-effort JSON parse. Gemini in JSON mode usually returns clean
    output, but we still strip code fences / surrounding prose just in
    case the model deviates."""
    import json

    if not isinstance(raw, str):
        return None
    s = raw.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```\s*$", "", s)
    # Sometimes Gemini wraps the dict in a single-key envelope.
    try:
        obj = json.loads(s)
    except Exception:
        return None
    if isinstance(obj, dict):
        return obj
    return None
