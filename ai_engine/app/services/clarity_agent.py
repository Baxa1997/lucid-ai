"""Gemini-powered clarity check for new project prompts.

Asks up to 3 dynamic questions to understand the real project — what to build,
who it's for, where it is — so research and routing are accurate.
Uses the existing clarification_needed / [LUCID_CLARIFY::key=value] infrastructure.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

_MAX_ROUNDS = 3

# Valid question keys — snake_case identifiers that map to [LUCID_CLARIFY::key=value]
# markers stacked on the task. Any key not in this set is rejected (fail-safe).
_VALID_KEYS = {
    "project_type",    # landing page / full website / web app / e-commerce
    "location",        # city or country for physical businesses
    "audience",        # target demographic when it flips the visual style
    "niche",           # sub-category when parent is too generic (restaurant → Italian?)
    "style",           # brand tone when it would flip the design (luxury vs playful)
}

# Short physical-business prompts — Gemini is inconsistent on ≤5 word prompts.
# This fast-path guarantees a location question before the API call.
# "clinic" excluded — audience (luxury vs budget) matters more than location there.
_PHYSICAL_KEYWORDS = {
    "gym", "spa", "salon", "café", "cafe", "bakery", "barbershop", "bar",
    "pub", "club", "nightclub", "shop", "store", "boutique", "studio",
    "pharmacy", "hotel", "hostel", "resort", "restaurant", "diner",
    "bistro", "brasserie", "pizzeria", "trattoria", "tavern", "laundry",
    "cleaners", "carwash", "garage", "florist", "gallery", "museum",
    "theater", "theatre", "cinema", "library", "church", "mosque",
    "temple", "school", "academy", "tailor", "jeweler", "jeweller",
    "optician", "dentist", "vet",
}

_LOCATION_PREPOSITIONS = {"in", "at", "near", "around", "based", "located"}

_PHYSICAL_LOCATION_OPTIONS = [
    {"id": "united_states",  "label": "United States"},
    {"id": "united_kingdom", "label": "United Kingdom"},
    {"id": "western_europe", "label": "Western Europe"},
    {"id": "other",          "label": "Other"},
]


def _fast_location_check(task: str) -> dict | None:
    """Return a location question for short physical-business prompts.

    Only fires on ≤5-word prompts with no location preposition already present.
    """
    words = task.lower().split()
    if any(w.strip(".,!?") in _LOCATION_PREPOSITIONS for w in words):
        return None
    if len(words) > 5:
        return None
    for w in words:
        if w.strip(".,!?'\"") in _PHYSICAL_KEYWORDS:
            return {
                "key": "location",
                "text": f"Where is your {task.lower()} located?",
                "options": _PHYSICAL_LOCATION_OPTIONS,
            }
    return None


_SYSTEM_PROMPT = """\
You are a smart project intake agent for an AI web design platform.

A user described a project. Your job: ask the ONE most important question that would significantly improve the quality of this project — better visual research, better routing, better output.

══ QUESTION PRIORITY ══
Ask the first item below that is still unclear:

1. PROJECT SCOPE (key="project_type") — ask ONLY if it is genuinely unclear whether the user wants:
   - A single marketing/landing page
   - A full multi-page website
   - A web app or dashboard
   - An e-commerce store with products/cart
   Do NOT ask this for clear SaaS tools, digital products, or when user said "landing page" / "website".

2. LOCATION (key="location") — ask when the business is physical (shop, café, studio, gym, clinic, hotel, restaurant, bar, salon) AND no city/country is mentioned.
   → Options: specific countries, NOT continents. Always include "Other".
   → Pick the 4 most relevant countries for this business type.

3. BUSINESS NICHE (key="niche") — ask when the category is too generic for visual research.
   → "restaurant" alone → what cuisine? Italian, Asian, American BBQ, Fine dining?
   → "studio" alone → photography? yoga? music? tattoo?
   → "agency" alone → marketing? design? talent? law?

4. TARGET AUDIENCE (key="audience") — ask when the demographic would completely flip the visual style.
   → "clinic" → luxury private vs budget public
   → "fitness app" → audience is already clear (fitness people), DON'T ask
   → Keep options specific and visual: "Young professionals", "Families", "Seniors", "Luxury clients"

5. STYLE DIRECTION (key="style") — ask ONLY as a last resort when nothing else is unclear but brand tone is completely unknown.
   → Options: "Luxury / Premium", "Modern / Minimal", "Bold / Playful", "Classic / Traditional"

══ NEVER ASK ABOUT ══
- Colors, fonts, specific content, or features
- Things clearly stated or strongly implied by the prompt
- A second question on the same topic already answered

══ ALREADY CLARIFIED ══
{already_clarified}

══ ROUNDS ══
Used {rounds_used} of {max_rounds}. If rounds >= {max_rounds} OR prompt has enough context → return {{"clear": true}}.

══ PROMPT ══
{task}

Return JSON only — no prose:
{{"clear": true}}
OR
{{"clear": false, "question": {{"key": "<one of: project_type|location|niche|audience|style>", "text": "<question, max 12 words>", "options": [{{"id": "<snake_id>", "label": "<2-5 word label>"}}]}}}}
2–4 options maximum.
"""


async def check_prompt_clarity(
    task: str,
    already_clarified: dict,
    timeout_s: float = 12.0,
) -> Optional[dict]:
    """Return a question dict if clarification needed, else None.

    Returned dict: {key, text, options: [{id, label}]}.
    Returns None when prompt is clear or on any error (fail-open).
    """
    from app.services.landing_gemini import structured_distill
    from knowledge.loader import extract_clarify_context, force_archetype_from_task

    rounds_used = len(already_clarified)
    if rounds_used >= _MAX_ROUNDS:
        return None

    # Strip internal markers before showing to Gemini
    _, clean_task = force_archetype_from_task(task)
    _, clean_task = extract_clarify_context(clean_task)
    clean_task = clean_task.strip()

    if not clean_task:
        return None

    # Fast path: short physical-business prompts always need location first
    if rounds_used == 0 or "location" not in already_clarified:
        _fast = _fast_location_check(clean_task)
        if _fast:
            logger.info("clarity_agent: fast-path location for %r", clean_task)
            return _fast

    prompt = _SYSTEM_PROMPT.format(
        rounds_used=rounds_used,
        max_rounds=_MAX_ROUNDS,
        already_clarified=already_clarified if already_clarified else "none",
        task=clean_task,
    )

    response_schema = {
        "type": "object",
        "properties": {
            "clear": {"type": "boolean"},
            "question": {
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "text": {"type": "string"},
                    "options": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "label": {"type": "string"},
                            },
                            "required": ["id", "label"],
                        },
                    },
                },
                "required": ["key", "text", "options"],
            },
        },
        "required": ["clear"],
    }

    try:
        raw = await structured_distill(
            prompt,
            timeout_s,
            label="clarity_check",
            response_schema=response_schema,
            max_tokens=300,
            model="gemini-2.5-flash",
        )
        result = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(result, dict):
            return None
        if result.get("clear"):
            return None
        q = result.get("question")
        if not isinstance(q, dict):
            return None
        if not q.get("key") or not q.get("text") or not isinstance(q.get("options"), list):
            return None
        if len(q["options"]) < 2:
            return None

        # Validate key — must be snake_case and in allowed set
        key = re.sub(r"[^a-z0-9_]", "", q["key"].lower().strip())
        if key not in _VALID_KEYS:
            logger.info("clarity_agent: suppressed unknown key=%r", key)
            return None

        # Don't re-ask a key already answered
        if key in already_clarified:
            logger.info("clarity_agent: suppressed already-answered key=%r", key)
            return None

        q["key"] = key
        logger.info("clarity_agent: round %d — asking about %r", rounds_used + 1, key)
        return q

    except Exception as exc:
        logger.warning("clarity_agent: check failed (%s) — passing through", exc)
        return None
