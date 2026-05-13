"""Gemini-powered clarity check for new project prompts.

Analyzes whether a prompt has enough context to generate a high-quality result.
If not, returns one targeted question (max 3 rounds total across the session).
Uses the existing clarification_needed / [LUCID_CLARIFY::key=value] infrastructure.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_MAX_ROUNDS = 3

# Physical business keywords — single/short prompts naming these always need
# a location question because cultural context flips the entire visual style.
# Prompts with these words need location — but NOT audience questions first.
# "clinic" is excluded: audience (luxury vs budget) matters more than location.
_PHYSICAL_KEYWORDS = {
    "gym", "spa", "salon", "café", "cafe", "bakery", "barbershop", "bar",
    "pub", "club", "nightclub", "shop", "store", "boutique", "studio",
    "pharmacy", "hotel", "hostel", "resort", "restaurant",
    "diner", "bistro", "brasserie", "pizzeria", "trattoria", "tavern",
    "laundry", "cleaners", "carwash", "garage", "florist",
    "nursery", "gallery", "museum", "theater", "theatre", "cinema",
    "library", "church", "mosque", "temple", "school", "academy",
    "tailor", "jeweler", "jeweller", "optician", "dentist", "vet",
}

# Location prepositions — if found, the prompt likely already has a location
_LOCATION_PREPOSITIONS = {"in", "at", "near", "around", "based", "located"}

_PHYSICAL_LOCATION_OPTIONS = [
    {"id": "united_states", "label": "United States"},
    {"id": "united_kingdom", "label": "United Kingdom"},
    {"id": "western_europe", "label": "Western Europe"},
    {"id": "other",          "label": "Other"},
]

def _fast_location_check(task: str) -> dict | None:
    """Return a location question for short physical-business prompts.

    Gemini is inconsistent on 1-3 word prompts (e.g. 'gym', 'spa'). This
    pre-check catches them reliably before the API call.
    Skipped when the prompt already contains a location preposition.
    """
    words = task.lower().split()
    # Skip if prompt likely already contains a location ("restaurant in Tokyo")
    if any(w.strip(".,!?") in _LOCATION_PREPOSITIONS for w in words):
        return None
    # Only apply to short prompts (≤ 5 words) — longer ones Gemini handles fine
    if len(words) > 5:
        return None
    for w in words:
        w_clean = w.strip(".,!?'\"")
        if w_clean in _PHYSICAL_KEYWORDS:
            return {
                "key": "location",
                "text": f"Where is your {task.lower()} located?",
                "options": _PHYSICAL_LOCATION_OPTIONS,
            }
    return None

_SYSTEM_PROMPT = """\
You are a project-intake agent for an AI web design platform.

A user described a project they want built. Decide if ONE specific question would unlock significantly better visual results — or if you have enough to proceed.

═══ ONLY ASK ABOUT THESE TWO THINGS ═══

1. LOCATION — ask key="location" when the business is physical (shop, restaurant, café, studio, clinic, gym, hotel, bar) AND no city or country is mentioned.
   → Options must be specific countries/regions, not continents.
   → Examples: "Italy", "France", "Spain", "UK", "United States", "Japan", "South Korea", "Mexico", "Brazil", "Other"
   → Pick the 4 most likely options for that business type, always include "Other"

2. AUDIENCE — ask key="audience" ONLY when the target audience would flip the entire visual style AND it cannot be inferred.
   → Example: "clinic" alone — could be luxury private or budget public → ask
   → Example: "SaaS tool for developers" — audience is clear, don't ask
   → Options: max 4, short labels (3-5 words each)

═══ NEVER ASK ABOUT ═══
- Page type / project type (landing page vs full website) — the platform decides this automatically
- Colors, fonts, content, features
- Anything inferable from the prompt
- Industry or business type when it's explicit

═══ KEY NAMING RULES ═══
- Location questions: always key="location"
- Audience questions: always key="audience"

ROUNDS USED: {rounds_used} of {max_rounds}
ALREADY ANSWERED: {already_clarified}

If rounds >= {max_rounds} OR the prompt is clear enough → return {{"clear": true}}.

PROMPT:
{task}

Return JSON only:
{{"clear": true}}
OR
{{"clear": false, "question": {{"key": "location"|"audience", "text": "Question (max 12 words)", "options": [{{"id": "snake_id", "label": "Label"}}]}}}}
"""


async def check_prompt_clarity(
    task: str,
    already_clarified: dict,
    timeout_s: float = 12.0,
) -> Optional[dict]:
    """Return a question dict if clarification needed, else None.

    The returned dict has keys: key, text, options (list of {id, label}).
    Returns None when the prompt is clear enough to proceed, or on any error
    (fail-open so slow AI never blocks generation).
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

    # Fast path: short physical-business prompts always need location
    _fast = _fast_location_check(clean_task)
    if _fast:
        logger.info("clarity_agent: fast-path location question for %r", clean_task)
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
            max_tokens=256,
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
        # Normalize key — only "location" and "audience" are valid
        key = q.get("key", "").lower().strip()
        if key not in ("location", "audience"):
            logger.info("clarity_agent: suppressed question with key=%r — not location/audience", key)
            return None
        q["key"] = key
        logger.info("clarity_agent: round %d — asking about %r", rounds_used + 1, key)
        return q
    except Exception as exc:
        logger.warning("clarity_agent: check failed (%s) — passing through to pipeline", exc)
        return None
