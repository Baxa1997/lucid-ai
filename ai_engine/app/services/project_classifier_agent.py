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

_WORKFLOW_BY_ARCHETYPE = {
    "single_page_landing": "landing_generation",
    "consumer_website": "website_generation",
    "portfolio": "website_generation",
    "blog": "website_generation",
    "marketplace": "website_generation",
    "consumer_website_with_admin": "website_with_admin_generation",
    "admin_dashboard": "admin_generation",
    "crm": "admin_generation",
    "tms": "admin_generation",
    "saas_dashboard": "admin_generation",
    "ecommerce": "admin_generation",
}


def workflow_for_archetype(archetype: str) -> str:
    """Return the execution workflow selected for a resolved archetype."""
    return _WORKFLOW_BY_ARCHETYPE.get(archetype, "new_project_generation")


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
            model="gemini-3.5-flash",
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


async def route_new_project_with_gemini(description: str) -> dict[str, Any]:
    """Let Gemini Flash decide whether to clarify or start a workflow.

    A start decision must cite the exact prompt text that supplied both the
    project type and domain. This keeps the router agent-owned while preventing
    it from silently inventing a website, admin panel, or other missing scope.
    """
    from app.services.gemini_http import gemini_post
    from knowledge.loader import LAYOUT_ARCHETYPES, safe_gemini_text

    allowed = sorted(LAYOUT_ARCHETYPES.keys())
    prompt = f"""You are Lucid AI's prompt-identification agent.
Decide whether this description is sufficient to start building, and choose
the execution archetype when it is sufficient.

Description: {description}

A prompt is sufficient when BOTH are known:
1. project type/structure, such as landing page, website, app, dashboard, store
2. subject/domain, such as education center, coffee shop, photographer, CRM

Type + domain is enough to start. Do NOT require audience, colors, style,
location, page count, features, or content; downstream agents decide those.
Random words, gibberish, type-only, and domain-only prompts require clarification.

STRICT NO-GUESS RULES:
- Project type must be explicitly written in the description. Never infer
  "website", "app", "dashboard", or "admin" from a business/domain alone.
- Domain must be explicitly written in the description. Never invent one from
  a project type alone.
- Choose consumer_website_with_admin only when BOTH a public website and an
  admin/internal surface are explicitly requested.
- Choose an admin archetype only when an admin/dashboard/internal/management
  product surface is explicitly requested.
- For start_workflow, copy the exact phrases from Description that prove the
  project type and domain into project_type_evidence and domain_evidence.
- If either exact evidence phrase is missing, action MUST be clarify.

Examples:
- "landing page for education center" -> start_workflow, single_page_landing
- "education center" -> clarify project_type; do not assume website
- "landing page" -> clarify domain
- "website for a coffee shop" -> start_workflow, consumer_website
- "website and admin panel for a school" -> start_workflow,
  consumer_website_with_admin
- "hhhh" -> clarify intent

Allowed archetypes:
{json.dumps(allowed)}

Return ONLY JSON:
{{
  "action": "start_workflow" or "clarify",
  "archetype": "one allowed value, only for start_workflow",
  "project_type_evidence": "exact phrase copied from Description, only for start_workflow",
  "domain_evidence": "exact phrase copied from Description, only for start_workflow",
  "clarify_key": "project_type|domain|intent, only for clarify",
  "question": "short question, only for clarify",
  "reasoning": "short routing reason"
}}"""

    status, data, raw = await gemini_post(
        model="gemini-3.5-flash",
        payload={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0,
                "maxOutputTokens": 800,
                "responseMimeType": "application/json",
                "thinkingConfig": {"thinkingBudget": 0},
            },
        },
        timeout_s=15.0,
        label="new_project_prompt_router",
    )
    if status != 200 or data is None:
        raise RuntimeError(raw or f"HTTP {status}")

    parsed = json.loads(safe_gemini_text(data).strip())
    action = str(parsed.get("action") or "").strip()
    if action == "start_workflow":
        archetype = str(parsed.get("archetype") or "").strip()
        if archetype not in LAYOUT_ARCHETYPES:
            raise ValueError(f"Gemini returned unknown archetype: {archetype}")
        type_evidence = str(parsed.get("project_type_evidence") or "").strip()
        domain_evidence = str(parsed.get("domain_evidence") or "").strip()
        description_folded = description.casefold()
        has_type_evidence = bool(
            type_evidence and type_evidence.casefold() in description_folded
        )
        has_domain_evidence = bool(
            domain_evidence and domain_evidence.casefold() in description_folded
        )
        if not has_type_evidence or not has_domain_evidence:
            missing_key = "project_type" if not has_type_evidence else "domain"
            question = (
                "What kind of project should I build for this?"
                if missing_key == "project_type"
                else "What business, product, or topic is this project for?"
            )
            logger.warning(
                "Gemini start decision lacked prompt evidence "
                "(type=%r domain=%r); requesting %s",
                type_evidence,
                domain_evidence,
                missing_key,
            )
            return {
                "action": "clarify",
                "clarify_key": missing_key,
                "question": question,
                "reasoning": "The prompt did not explicitly state both project type and domain.",
            }
        return {
            "action": action,
            "archetype": archetype,
            "project_type_evidence": type_evidence,
            "domain_evidence": domain_evidence,
            "reasoning": str(parsed.get("reasoning") or "").strip(),
        }
    if action == "clarify":
        return {
            "action": action,
            "clarify_key": str(parsed.get("clarify_key") or "intent").strip(),
            "question": str(
                parsed.get("question")
                or "Could you describe what you would like to build?"
            ).strip(),
            "reasoning": str(parsed.get("reasoning") or "").strip(),
        }
    raise ValueError(f"Gemini returned invalid action: {action}")


async def classify_archetype_with_gemini(description: str) -> tuple[str, str]:
    """Use Gemini Flash to choose the new-project execution archetype.

    Static classification is retained only as outage recovery.
    """
    from app.services.gemini_http import gemini_post
    from knowledge.loader import LAYOUT_ARCHETYPES, safe_gemini_text

    allowed = sorted(LAYOUT_ARCHETYPES.keys())
    prompt = f"""You are Lucid AI's new-project workflow router.
Choose the single best layout archetype for this project description.

Description: {description}

Allowed archetypes:
{json.dumps(allowed)}

Return ONLY JSON:
{{"archetype": "one allowed value", "reasoning": "short routing reason"}}"""

    status, data, raw = await gemini_post(
        model="gemini-3.5-flash",
        payload={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0,
                "maxOutputTokens": 500,
                "responseMimeType": "application/json",
                "thinkingConfig": {"thinkingBudget": 0},
            },
        },
        timeout_s=15.0,
        label="project_workflow_router",
    )
    if status != 200 or data is None:
        raise RuntimeError(raw or f"HTTP {status}")

    parsed = json.loads(safe_gemini_text(data).strip())
    archetype = str(parsed.get("archetype") or "").strip()
    if archetype not in LAYOUT_ARCHETYPES:
        raise ValueError(f"Gemini returned unknown archetype: {archetype}")
    return archetype, str(parsed.get("reasoning") or "").strip()


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
    function again. The canonical Gemini router continues asking until it
    identifies enough explicit scope to select a workflow.

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
            decided_by="user_marker",
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
                decided_by="clarification_answer",
            )

    # Canonical path: Gemini Flash identifies whether this prompt should ask a
    # question or start a specific project workflow.
    try:
        route = await route_new_project_with_gemini(clean_task or task or "")
        if route.get("action") == "clarify":
            clarify_key = route.get("clarify_key", "intent")
            return {
                "status": "needs_clarification",
                "next_action": "clarify",
                "workflow_id": "project_clarification",
                "question": route.get("question", ""),
                "options": [],
                "clarify_key": clarify_key,
                "stage": _stage_for_key(clarify_key, answers),
                "answers": answers,
                "reasoning": route.get("reasoning", ""),
                "decided_by": "gemini_flash",
            }
        archetype = str(route.get("archetype") or "")
        if archetype:
            return await _build_resolved_async(
                archetype=archetype,
                answers=answers,
                description=clean_task,
                extract_entities=extract_entities,
                reasoning=(
                    f"Gemini Flash selected {archetype!r}: "
                    f"{route.get('reasoning', '')}"
                ),
                decided_by="gemini_flash",
            )
    except Exception as exc:
        logger.warning("Gemini new-project prompt router failed: %s — using fallback", exc)

    # No project_type yet → defer to clarity_agent for a Gemini-driven
    # question only when the canonical prompt router is unavailable.
    question = await check_prompt_clarity(
        task=clean_task,
        already_clarified=answers,
        timeout_s=12.0,
    )

    if question is not None:
        stage = _stage_for_key(question.get("key", ""), answers)
        return {
            "status": "needs_clarification",
            "next_action": "clarify",
            "workflow_id": "project_clarification",
            "question": question.get("text", ""),
            "options": question.get("options", []),
            "clarify_key": question.get("key", ""),
            "stage": stage,
            "answers": answers,
            "decided_by": "fallback_clarity_router",
        }

    # Clarity says "clear" but we still don't have a project_type. Gemini
    # Flash now makes the explicit workflow decision. The static classifier
    # is outage recovery only.
    from knowledge.loader import _classify_static  # type: ignore[attr-defined]
    try:
        fallback_archetype, route_reason = await classify_archetype_with_gemini(
            clean_task or task or "",
        )
        route_reason = f"Gemini Flash selected {fallback_archetype!r}: {route_reason}"
        decided_by = "gemini_flash_fallback"
    except Exception as exc:
        logger.warning("Gemini project workflow router failed: %s — using static fallback", exc)
        static = _classify_static(clean_task or task or "")
        fallback_archetype = static.get("layout_archetype", "consumer_website")
        route_reason = f"static outage fallback selected {fallback_archetype!r}"
        decided_by = "static_outage_fallback"

    return await _build_resolved_async(
        archetype=fallback_archetype,
        answers=answers,
        description=clean_task,
        extract_entities=extract_entities,
        reasoning=route_reason,
        decided_by=decided_by,
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
    decided_by: str = "agent",
    entities: Optional[list[str]] = None,
    entity_confidence: int = 0,
) -> dict[str, Any]:
    """Sync version — used when entity extraction is not needed."""
    needs_admin_followup = archetype == "consumer_website_with_admin"
    payload: dict[str, Any] = {
        "status": "resolved",
        "next_action": "start_workflow",
        "workflow_id": workflow_for_archetype(archetype),
        "archetype": archetype,
        "needs_admin_followup": needs_admin_followup,
        "answers": answers,
        "stage": STAGE_RESOLVED,
        "reasoning": reasoning,
        "decided_by": decided_by,
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
    decided_by: str = "agent",
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
        decided_by=decided_by,
        entities=entities,
        entity_confidence=confidence,
    )
