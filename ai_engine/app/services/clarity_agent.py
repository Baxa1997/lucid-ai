"""Gemini-powered project-intake agent for new project prompts.

Asks a short series of dynamic questions (up to _MAX_ROUNDS, one per round) to
build a RICH, design-ready brief — project type first, then the design-critical
facts for that type (goal, audience, key offer, pages, entities, style…) — so
research, routing, and generation are accurate.

ZERO hardcoded questions or options. Gemini decides:
  - what to ask next (the highest-impact missing fact)
  - what options to offer (contextually relevant per prompt)
  - when the brief is rich enough to stop

Uses the existing clarification_needed / [LUCID_CLARIFY::key=value] infrastructure.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

_MAX_ROUNDS = 5

_SYSTEM_PROMPT = """\
You are a smart project intake agent for an AI web design platform. Talk like a sharp designer scoping a project: ask dynamic, specific questions — ONE per round — to genuinely understand what the user wants, before generation starts.

══ GOAL ══
First nail the project CORE, then ask a few smart follow-ups that would change the design. Ask one question per round, phrased naturally for THIS prompt (not generic). Return {{"clear": true}} once the core is set and you understand the audience / style / key features (or when ROUNDS hits the max).
NEVER ask about pages, sections, navigation, or site structure — the platform generates those automatically.

══ OPEN QUESTIONS vs OPTIONS ══
Default to OPEN questions — set "options" to an empty list and let the user answer in their own words. That's how you truly understand them.
Provide 2–4 "options" ONLY when the answer is a small, well-defined choice:
  • project_type (uses the fixed ids below) — always options
  • a clear visual style direction, or a genuine either/or
For everything else (the field/business, audience, desired features, specifics) → ask an OPEN question with NO options. Do NOT invent option lists for open-ended things; a free-text answer is richer.

══ STEP 1 — NAIL THE PROJECT CORE (top priority) ══
The CORE = (1) the project type/structure AND (2) the field/domain it's dedicated to. The same type means nothing without the field — a landing page could be for a RESTAURANT, a SAAS product, a COMPANY PROFILE, or a PORTFOLIO, and each needs a totally different design. Ask whichever part is missing; skip whatever the prompt already states or implies.

(1) PROJECT TYPE / STRUCTURE — ask project_type (WITH options, fixed ids) only when ambiguous. TWO HARD OVERRIDES:
A. **Website + admin combo** — prompt has BOTH a customer-facing word ("website","site","landing","page","homepage") AND an internal-tool word ("admin","dashboard","panel","back office","manage","manager") → ask project_type with at least {{landing_page, full_website, admin_dashboard, marketing_with_admin}}.
B. **Business without depth** — prompt names a business but does NOT state scope ("landing","one-page","multi-page","full site","website","homepage") and has no admin word → ask project_type with at least {{landing_page, full_website}}.
Read type from prompt: "app"→web_app · "landing page"/"landing"→landing_page · "website"/"site"/"homepage"→full_website · "admin"/"dashboard"/"manage X"/"track Y"→admin in play · "store"/"shop"/"sell"→ecommerce · "portfolio"/"showcase"→portfolio · "blog"→blog.
PROJECT_TYPE OPTION IDS (use these EXACT ids; labels free-form): landing_page · full_website · admin_dashboard · marketing_with_admin · web_app · ecommerce · portfolio · blog.

(2) FIELD / DOMAIN — what the project is FOR. THIS IS THE MOST IMPORTANT QUESTION. If the prompt doesn't say (e.g. just "landing page", "a website", "build me a site", "make me a page"), ASK it as an OPEN question (key="field", NO options) — e.g. "What's this landing page for — what business, product, or person?" Let them describe it freely. If the field IS stated ("restaurant landing page","SaaS dashboard","photographer portfolio") → field is known, skip it.

══ STEP 2 — DYNAMIC FOLLOW-UPS (open questions, only if they change the design) ══
After the core is set, ask only these, one per round, only when MISSING and design-critical — all as OPEN questions (no options) unless noted:
- **audience** — who it's for (open). Ask when it would flip the design.
- **style** — visual look & feel. Open by default ("What look are you going for?"); you MAY offer 2–4 directions if that helps.
- **features** — specific things they want included (open) — booking, gallery, pricing, testimonials, menu, etc.
Plus **location** only if it's a physical business with no place stated. Nothing else.
DO NOT ask about pages, sections, layout, or navigation under any circumstance.

══ WHEN YOU DO USE OPTIONS ══
- 2–4 options, specific to THIS prompt; add "Other" if a free answer is plausible.
- location → specific COUNTRIES, never continents.
Otherwise leave "options" empty.

══ KEY NAMING ══
snake_case: project_type, field, audience, style, features, niche, location. Don't re-ask answered keys.

══ EXAMPLES (flow) ══
"landing page" → ASK field OPEN: "What's this landing page for?" (no options) → then audience OPEN → then style. Stop.
"restaurant landing page" → core known. ASK style → maybe features OPEN ("Any must-have features, like online booking or a menu?"). Stop.
"SaaS landing page for a CRM" → core known. ASK audience OPEN → key features OPEN. Stop.
"Italian restaurant in Brooklyn" → ASK project_type (rule B, WITH options); field known → ASK style or features OPEN. Stop.
"photographer portfolio" → core known. ASK style → maybe audience OPEN. Stop.
"build me a website" → ASK field OPEN first ("What's the website for?") → then style. Stop.

══ INPUT ══
ALREADY CLARIFIED: {already_clarified}
ROUNDS: {rounds_used} of {max_rounds}
PROMPT: {task}

If ROUNDS >= {max_rounds} → return {{"clear": true}}.
If the project core (type + field) is set and you understand audience/style/features — or further questions wouldn't change the design — return {{"clear": true}}.

Return JSON only:
{{"clear": true}}
OR open-ended (preferred for most questions):
{{"clear": false, "question": {{"key": "snake_case", "text": "max 14 words", "options": []}}}}
OR with choices (project_type / clear either-or only):
{{"clear": false, "question": {{"key": "snake_case", "text": "max 14 words", "options": [{{"id": "snake_id", "label": "2-5 word label"}}]}}}}
"""


async def check_prompt_clarity(
    task: str,
    already_clarified: dict,
    timeout_s: float = 22.0,
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
                # options is OPTIONAL — omit/empty for open-ended (free-text)
                # questions; include 2-4 only when the answer is a small fixed
                # choice (e.g. project_type).
                "required": ["key", "text"],
            },
        },
        "required": ["clear"],
    }

    try:
        # gemini-3.1-pro-preview gives noticeably better intent inference on
        # short / ambiguous prompts ("acca website", "agency") and produces
        # tighter, more relevant follow-up questions than 2.5-flash. The
        # latency cost (~1–2s extra) is worth it because clarification is
        # gated on a real Q&A — happens once at the start of a session.
        raw = await structured_distill(
            prompt,
            timeout_s,
            label="clarity_check",
            response_schema=response_schema,
            max_tokens=480,
            model="gemini-3.1-pro-preview",
        )
        result = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(result, dict):
            return None
        if result.get("clear"):
            return None
        q = result.get("question")
        if not isinstance(q, dict):
            return None
        if not q.get("key") or not q.get("text"):
            return None

        # Options are OPTIONAL. Empty/absent → open-ended free-text question
        # (the UI shows a text input). A lone option is meaningless, so drop
        # it to open-ended too; otherwise keep the 2-4 choices.
        opts = q.get("options")
        if not isinstance(opts, list):
            opts = []
        if len(opts) < 2:
            opts = []
        q["options"] = opts

        # Normalize key — snake_case only (no allowlist; trust Gemini)
        key = re.sub(r"[^a-z0-9_]", "", q["key"].lower().strip())
        if not key:
            return None

        # Don't re-ask a key already answered
        if key in already_clarified:
            logger.info("clarity_agent: suppressed already-answered key=%r", key)
            return None

        q["key"] = key
        logger.info("clarity_agent: round %d — asking %r (%s)",
                    rounds_used + 1, key,
                    f"{len(opts)} options" if opts else "open-ended")
        return q

    except Exception as exc:
        logger.warning("clarity_agent: check failed (%s) — passing through", exc)
        return None
