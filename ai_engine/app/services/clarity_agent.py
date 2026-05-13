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

_SYSTEM_PROMPT = """\
You are a project-intake agent for an AI web design platform. A user just described a project they want built.

Your job: decide if the prompt has enough context to generate a great website/landing page, OR if one short targeted question would unlock significantly better results.

QUESTION RULES — only ask if the answer would change the design in a major way:
- Missing business location (physical shops, restaurants, studios need a city/country for cultural style)
- Ambiguous project type (could be a landing page OR a full multi-page site)
- Missing target audience when it would flip the visual approach entirely (luxury vs budget, B2B vs consumer)

NEVER ask about:
- Colors, fonts, or content details
- Anything clearly implied by the prompt
- Things you could assume reasonably from context

ROUNDS: {rounds_used} of {max_rounds} clarification rounds already used.
ALREADY ANSWERED: {already_clarified}

If rounds >= {max_rounds}, OR the prompt is specific enough to proceed, return {{"clear": true}}.

PROMPT TO ANALYZE:
{task}

Respond ONLY with JSON matching this exact shape:
{{
  "clear": true
}}
OR:
{{
  "clear": false,
  "question": {{
    "key": "snake_case_key",
    "text": "The question to show the user (max 15 words)",
    "options": [
      {{"id": "snake_case_id", "label": "Human-readable label"}},
      {{"id": "snake_case_id2", "label": "Label 2"}}
    ]
  }}
}}
Max 4 options. Keep option labels short (2-5 words).
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

    if not clean_task or len(clean_task) < 4:
        return None

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
        logger.info("clarity_agent: round %d — asking about %r", rounds_used + 1, q["key"])
        return q
    except Exception as exc:
        logger.warning("clarity_agent: check failed (%s) — passing through to pipeline", exc)
        return None
