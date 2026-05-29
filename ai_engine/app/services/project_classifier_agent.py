"""High-level project classifier agent — multi-turn resolution to an archetype.

Composes existing infrastructure:
  • ``clarity_agent.check_prompt_clarity``    → Gemini-driven question generation
  • ``LUCID_CLARIFY::key=value`` markers      → per-turn answer persistence
  • ``map_project_type_to_archetype``         → maps clarity answers → archetype

What this module adds on top:
  • ``extract_admin_entities``  — Gemini Flash call that infers the data
    entities a user will need to manage, given the business description.
    Used when the user resolves to an admin-flavoured project_type so the
    pipeline can pre-populate an entity confirmation step.
  • ``resolve_classification`` — single entry point that returns a
    ``needs_clarification`` payload or a fully resolved ``{archetype, …}``
    dict. The orchestrator calls this once per WebSocket turn; if status is
    ``needs_clarification`` it forwards the question to the user and waits
    for the next turn to call this function again with the new
    ``[LUCID_CLARIFY::...]`` marker glued onto the task.

Design notes
------------
The user's original spec described an explicit state machine
(INITIAL/ASK_PRODUCT_TYPE/ASK_SITE_DEPTH/ASK_ENTITIES/RESOLVED). The
existing ``clarity_agent`` is Gemini-driven rather than state-driven —
it decides at each round which question (if any) is the next-most-useful.
That's preserved here on purpose: hard-coded state machines age badly,
and Gemini already handles "did the user already say `landing page`?
skip that question" via the ``already_clarified`` mechanism.

The resolver still exposes a ``stage`` hint in its return value so a
caller (or test) can reason about what's pending. The hint is derived
from which clarify keys are filled in, not from a stored state variable.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ── Public types ──────────────────────────────────────────────────────

# Stage names that callers may reference for UI / logging. Not stored in
# the DB — derived from clarify markers at resolve time.
STAGE_INITIAL          = "INITIAL"
STAGE_ASK_PRODUCT_TYPE = "ASK_PRODUCT_TYPE"
STAGE_ASK_SITE_DEPTH   = "ASK_SITE_DEPTH"
STAGE_ASK_ENTITIES     = "ASK_ENTITIES"
STAGE_RESOLVED         = "RESOLVED"


# Admin-flavoured archetypes that should trigger entity confirmation.
_ADMIN_ARCHETYPES = {"admin_dashboard", "crm", "tms", "saas_dashboard", "ecommerce"}


# ── Entity extraction ─────────────────────────────────────────────────

_ENTITY_PROMPT = """\
You infer the *data entities* a business would need to manage in an
internal admin panel, given a short description.

Description: {description}

Return 3–7 entities. Each entity is a plural noun naming a thing the
operator inserts/edits/deletes daily. Common examples:
  • Restaurant      → dishes, categories, reservations, customers
  • E-commerce      → products, orders, customers, shipments
  • Property mgmt   → properties, tenants, leases, payments
  • SaaS / B2B      → users, subscriptions, invoices

Rules:
  • snake_case, plural, lowercase
  • Drop generic boilerplate (settings, audit_log, notifications) unless
    the prompt explicitly mentions them
  • Prefer entities the operator owns. Skip purely public-facing ones
    (blog_posts, testimonials) unless the business is content-first

Return ONLY this JSON:
{{"entities": ["...", "..."], "confidence": 0-100}}
"""


async def extract_admin_entities(
    description: str,
    *,
    timeout_s: float = 12.0,
) -> dict[str, Any]:
    """Infer the entities a user will need to manage in an admin panel.

    Returns ``{"entities": [...], "confidence": int}``. Falls back to an
    empty list with confidence=0 on any failure so the caller can prompt
    the user to fill them in manually.
    """
    description = (description or "").strip()
    if not description:
        return {"entities": [], "confidence": 0}

    try:
        from app.services.landing_gemini import structured_distill

        response_schema = {
            "type": "object",
            "properties": {
                "entities": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "confidence": {"type": "integer"},
            },
            "required": ["entities"],
        }

        raw = await structured_distill(
            _ENTITY_PROMPT.format(description=description[:500]),
            timeout_s,
            label="admin_entities",
            response_schema=response_schema,
            max_tokens=300,
            model="gemini-2.5-flash",
        )
        result = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(result, dict):
            return {"entities": [], "confidence": 0}

        # Normalize: snake_case, plural-ish, dedupe, cap at 7
        raw_ents = result.get("entities") or []
        seen: set[str] = set()
        cleaned: list[str] = []
        for ent in raw_ents:
            if not isinstance(ent, str):
                continue
            slug = re.sub(r"[^a-z0-9_]", "_", ent.lower().strip())
            slug = re.sub(r"_+", "_", slug).strip("_")
            if not slug or slug in seen:
                continue
            seen.add(slug)
            cleaned.append(slug)
            if len(cleaned) >= 7:
                break

        conf = int(result.get("confidence", 0) or 0)
        conf = max(0, min(100, conf))
        logger.info("extract_admin_entities: %d entities, confidence=%d", len(cleaned), conf)
        return {"entities": cleaned, "confidence": conf}

    except Exception as exc:
        logger.warning("extract_admin_entities failed: %s", exc)
        return {"entities": [], "confidence": 0}


# ── Resolver ──────────────────────────────────────────────────────────

# Clarify-key → resolved archetype shortcut. Used when the user has
# already explicitly answered a question (so we don't re-run Gemini just
# to map "landing_page" → "single_page_landing").
_PRODUCT_TYPE_KEY = "project_type"


async def resolve_classification(
    task: str,
    *,
    gemini_key: str = "",
    extract_entities: bool = True,
) -> dict[str, Any]:
    """Return either a clarification request or a resolved archetype.

    Single entry point for the orchestrator. Idempotent — call it once
    per turn. When the user answers, the orchestrator prepends a new
    ``[LUCID_CLARIFY::key=value]`` marker to the task and calls this
    function again; resolution converges within ``_MAX_ROUNDS`` (3).

    Args:
        task: Raw user prompt, possibly containing one or more
            ``[LUCID_CLARIFY::...]`` markers from prior turns.
        gemini_key: Unused (kept for API compatibility; auth is handled
            inside ``gemini_post`` via Vertex ADC).
        extract_entities: When True (default), the resolver will run
            entity extraction the first time it resolves to an
            admin-flavoured archetype. Disable in tests if you want to
            assert resolution behavior without firing the extractor.

    Returns:
        Either::

            {
              "status": "needs_clarification",
              "question": "...",
              "options": [{"id": "...", "label": "..."}, ...],
              "clarify_key": "project_type",
              "stage": "ASK_PRODUCT_TYPE" | "ASK_SITE_DEPTH" | ...,
            }

        or::

            {
              "status": "resolved",
              "archetype": "single_page_landing" | "consumer_website" |
                           "admin_dashboard" | "consumer_website_with_admin",
              "needs_admin_followup": bool,
              "entities": [...],            # only for admin/both
              "entity_confidence": int,     # only when entities present
              "answers": {...},             # parsed clarify markers
              "stage": "RESOLVED",
              "reasoning": "...",
            }
    """
    from knowledge.loader import (
        extract_clarify_context, force_archetype_from_task,
        map_project_type_to_archetype,
    )
    from app.services.clarity_agent import (
        _strip_lucid_project_header,
        check_prompt_clarity,
    )

    # Strip force-archetype marker first (highest priority) so a user
    # who already picked a mode via the UI toggle bypasses all
    # clarification.
    forced_archetype, task_after_force = force_archetype_from_task(task)
    if forced_archetype:
        return _build_resolved(
            archetype=forced_archetype,
            answers={"force_archetype": forced_archetype},
            description=_strip_lucid_project_header(task_after_force).strip(),
            extract_entities=extract_entities,
            reasoning="archetype forced by UI marker",
        )

    answers, clean_task = extract_clarify_context(task_after_force)
    clean_task = _strip_lucid_project_header(clean_task).strip()

    # If the user's prior answers already pin down a project_type, map it
    # and short-circuit — no need to ask Gemini again.
    pt = answers.get(_PRODUCT_TYPE_KEY)
    if pt:
        archetype = map_project_type_to_archetype(pt)
        if archetype:
            return await _build_resolved_async(
                archetype=archetype,
                answers=answers,
                description=clean_task,
                extract_entities=extract_entities,
                reasoning=f"user answered project_type={pt!r}",
            )

    # No project_type yet → defer to clarity_agent for a Gemini-driven
    # question. The agent returns ``None`` when the prompt is already
    # clear (e.g. "landing page for X"), in which case we still want to
    # convert that clarity into an archetype — fall back to the keyword
    # detector + Gemini classifier inside the *static* classify path.
    question = await check_prompt_clarity(
        task=clean_task,
        already_clarified=answers,
        timeout_s=12.0,
    )

    if question is not None:
        stage = _stage_for_key(question.get("key", ""), answers)
        return {
            "status": "needs_clarification",
            "question": question.get("text", ""),
            "options": question.get("options", []),
            "clarify_key": question.get("key", ""),
            "stage": stage,
            "answers": answers,
        }

    # Clarity says "clear" but we still don't have a project_type. Use
    # the existing static classifier as a fallback so the resolver always
    # returns a real archetype.
    from knowledge.loader import _classify_static  # type: ignore[attr-defined]
    static = _classify_static(clean_task or task or "")
    fallback_archetype = static.get("layout_archetype", "consumer_website")

    return await _build_resolved_async(
        archetype=fallback_archetype,
        answers=answers,
        description=clean_task,
        extract_entities=extract_entities,
        reasoning=f"clarity passed-through; static classifier picked {fallback_archetype!r}",
    )


# ── Internal helpers ──────────────────────────────────────────────────

def _stage_for_key(clarify_key: str, prior_answers: dict) -> str:
    """Map a clarify_key → the spec's named stage, for UI/logs only."""
    if clarify_key == _PRODUCT_TYPE_KEY:
        return STAGE_ASK_PRODUCT_TYPE
    if clarify_key in ("site_depth", "page_count"):
        return STAGE_ASK_SITE_DEPTH
    if clarify_key in ("entities", "entity_confirmation"):
        return STAGE_ASK_ENTITIES
    if not prior_answers:
        return STAGE_INITIAL
    return STAGE_ASK_PRODUCT_TYPE  # generic ask


def _build_resolved(
    *,
    archetype: str,
    answers: dict,
    description: str,
    extract_entities: bool,
    reasoning: str,
    entities: Optional[list[str]] = None,
    entity_confidence: int = 0,
) -> dict[str, Any]:
    """Sync version — used when entity extraction is not needed."""
    needs_admin_followup = archetype == "consumer_website_with_admin"
    payload: dict[str, Any] = {
        "status": "resolved",
        "archetype": archetype,
        "needs_admin_followup": needs_admin_followup,
        "answers": answers,
        "stage": STAGE_RESOLVED,
        "reasoning": reasoning,
    }
    if entities is not None:
        payload["entities"] = entities
        payload["entity_confidence"] = entity_confidence
    return payload


async def _build_resolved_async(
    *,
    archetype: str,
    answers: dict,
    description: str,
    extract_entities: bool,
    reasoning: str,
) -> dict[str, Any]:
    """Build a resolved payload, firing entity extraction for admin paths."""
    entities: Optional[list[str]] = None
    confidence = 0
    if (
        extract_entities
        and (archetype in _ADMIN_ARCHETYPES or archetype == "consumer_website_with_admin")
        and description
    ):
        ent_result = await extract_admin_entities(description)
        entities = ent_result.get("entities") or []
        confidence = int(ent_result.get("confidence", 0))

    return _build_resolved(
        archetype=archetype,
        answers=answers,
        description=description,
        extract_entities=extract_entities,
        reasoning=reasoning,
        entities=entities,
        entity_confidence=confidence,
    )
