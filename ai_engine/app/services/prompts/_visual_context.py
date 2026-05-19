"""Shared VISUAL CONTEXT block for admin CRUD prompts.

Each of the three admin prompts (list / create / edit) injects this
block into the user message and the same enforcement clause into the
system message. Centralizing here keeps the wording consistent so we
don't accidentally drift the rules per prompt.

The block reads from `admin_plan["branding"]`, which carries the
visual_dna subset that `admin_plan.build_admin_plan` exposed. Fields
that come back None fall back to safe defaults — admins predate
migration 029 may have None for everything except brand_name +
primary_color, and we don't want the prompts to spell out missing
values to Claude as "None" / "null".
"""
from __future__ import annotations

from typing import Any


# Defaults match admin_brand_extractor._DEFAULT_SIGNALS so a missing
# field reads as "professional / calm / comfortable" rather than
# leaving Claude to invent a tone.
_DEFAULTS = {
    "primary_color":      "#0f172a",
    "typography_voice":   "professional",
    "cultural_intensity": "calm",
    "layout_density":     "comfortable",
    "accent_motif":       "geometric",
}


def _pick(branding: dict[str, Any], key: str) -> str:
    val = branding.get(key)
    return str(val) if val else _DEFAULTS[key]


def build_visual_context_block(admin_plan: dict[str, Any]) -> str:
    """Return the VISUAL CONTEXT user-prompt block for admin codegen.

    The brand_name is mandatory; the other 5 fields fall back to
    professional/calm/comfortable when absent. The trailing guidance
    sentences are what teaches Claude how to APPLY the dials — without
    them Claude tends to ignore them.
    """
    branding   = (admin_plan or {}).get("branding") or {}
    brand_name = branding.get("brand_name") or "Admin"

    return (
        "VISUAL CONTEXT (apply throughout):\n"
        f"- Brand: {brand_name}\n"
        f"- Primary color: {_pick(branding, 'primary_color')}\n"
        f"- Typography voice: {_pick(branding, 'typography_voice')}\n"
        f"- Layout density: {_pick(branding, 'layout_density')}\n"
        f"- Cultural intensity: {_pick(branding, 'cultural_intensity')}\n"
        f"- Accent motif: {_pick(branding, 'accent_motif')}\n"
        "\n"
        "Apply these to spacing, color emphasis, button styling, and\n"
        "typography weight. A 'compact' layout has tight padding; a\n"
        "'spacious' one breathes. 'Energetic' intensity uses brighter\n"
        "accents; 'editorial' uses serif accents for headings.\n"
    )


# System-prompt enforcement clause — every admin prompt appends this so
# Claude knows to actually USE the context, not just read past it.
VISUAL_CONTEXT_RULE = (
    "Each generated component MUST reflect the VISUAL CONTEXT in at "
    "least 2 visible ways (e.g., density via padding, intensity via "
    "color saturation, voice via font weights, motif via icon style)."
)
