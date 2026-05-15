"""Gemini-powered clarity check for new project prompts.

Asks up to 3 dynamic questions to understand the real project — what to build,
who it's for, where it is — so research and routing are accurate.

ZERO hardcoded questions or options. Gemini decides:
  - whether to ask anything at all
  - what to ask
  - what options to offer (contextually relevant per prompt)

Uses the existing clarification_needed / [LUCID_CLARIFY::key=value] infrastructure.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

_MAX_ROUNDS = 3

_SYSTEM_PROMPT = """\
You are a smart project intake agent for an AI web design platform. You decide the MINIMUM number of questions needed to generate a great result.

══ DEFAULT: clear=true ══
Bias toward passing through. Only ask when the answer would FUNDAMENTALLY change the design or routing. If you're unsure → return {{"clear": true}}.

══ TWO HARD OVERRIDES (always ask, regardless of bias) ══

A. **Website + admin combo** — if the prompt contains BOTH
   • a customer-facing site word: "website", "site", "landing", "page", "homepage"
   • AND an internal-tool word: "admin", "dashboard", "panel", "back office", "internal tool", "manage", "manager"
   → ALWAYS ask project_type FIRST with at least {{landing_page, full_website, admin_dashboard, marketing_with_admin}} as options. Never pass through.
   Examples that trigger: "restaurant website with admin panel", "law firm site with case management", "company website with employee portal".

B. **Business description without depth** — if the prompt describes a business
   ("X restaurant in Y", "Z yoga studio", "my dental clinic") but does NOT mention
   the depth/scope ("landing", "one-page", "one pager", "multi-page", "full site",
   "website", "homepage") AND does NOT contain an admin word from rule A,
   → ASK project_type with {{landing_page, full_website}} as the minimum options.
   Physical location specified alone is NOT enough to skip this.
   Examples that trigger: "Italian restaurant in Brooklyn", "yoga studio in Tashkent", "dentist in Berlin".
   Examples that do NOT trigger: "landing page for X" (depth stated), "full website for Y" (depth stated).

══ HOW TO READ THE PROMPT ══
Extract everything already stated or strongly implied BEFORE deciding:
- "app" → it's a web app, project_type is clear
- "landing page", "website", "page", "site" → project_type is clear
- "in <city/country>" → location is clear
- "Italian/French/Japanese/etc." restaurant → cuisine is clear
- "for <audience>" → audience is clear
- "luxury/premium/budget/casual" → style direction is clear
- Adjective + business (Italian café, yoga studio, dental clinic) → category often clear

If the prompt already has these → DO NOT ask about them again.

══ WHEN TO ASK ══
Only when something CRITICAL for design is missing and not inferable:
1. Physical business with NO location → ask "location" with country options specific to that business type
2. Business category too generic to design for (just "restaurant", just "shop", just "studio") AND nothing else specified → ask "niche"
3. Project scope ambiguous AND not implied (e.g. "company website" — could be one-pager or full multi-page) → ask "project_type"
4. Target audience would FLIP the design AND isn't implied (e.g. "clinic" — luxury private vs public) → ask "audience"

══ OPTIONS MUST BE CONTEXTUAL TO THE PROMPT ══
- Italian gelato missing location → ["Italy", "France", "Spain", "Other"]
- Japanese pottery missing location → ["Japan", "South Korea", "China", "Other"]
- yoga studio missing location → ["India", "United States", "Western Europe", "Other"]
- "restaurant" missing niche → ["Italian", "Asian fusion", "American BBQ", "Fine dining"]
- NEVER continental ("Europe", "Asia") — always specific countries
- 2–4 options, optionally "Other"

══ KEY NAMING ══
snake_case, descriptive. Common: project_type, location, niche, audience, style.
Don't ask about keys already in "Already clarified".

══ PROJECT_TYPE OPTION IDS ══
When asking project_type, options must use these EXACT ids (labels can vary):
- id="landing_page"            → single marketing/promo page
- id="full_website"            → multi-page public website (about, services, contact…)
- id="admin_dashboard"         → internal CRUD tool (manage entities, no public site)
- id="marketing_with_admin"    → BOTH public site + internal admin sharing data
- id="web_app"                 → SaaS app / dashboard for end-users
- id="ecommerce"               → online store with products/cart
- id="portfolio"               → personal/agency showcase
- id="blog"                    → content publishing site
The label shown to the user is free-form; only the id needs to match.

══ ASK product_type WHEN ══
The user mentioned wanting to "manage X" / "track Y" / "run a business with…"
without specifying whether they want a public site or an internal tool. Offer at
minimum {{landing_page, full_website, admin_dashboard, marketing_with_admin}}.
"manage bookings", "track inventory", "back-office tool" → admin_dashboard is in play.
"restaurant with admin panel", "shop and admin" → marketing_with_admin is in play.

══ EXAMPLES ══
"SaaS invoicing tool for freelancers" → clear=true (project type=app, audience=freelancers, both stated)
"AI writing assistant app" → clear=true (says "app", that's enough)
"fitness app" → clear=true (says "app")
"landing page for fitness coach" → clear=true (depth=landing stated explicitly)
"full website for Italian restaurant" → clear=true (depth=full website stated)
"Italian coffee shop in Florence" → ASK project_type (rule B: business without depth)
"gym in New York" → ASK project_type (rule B: business without depth)
"dentist in Berlin" → ASK project_type (rule B: business without depth)
"luxury skincare brand for women 40+" → ASK project_type (rule B: business without depth)
"restaurant website with admin panel" → ASK project_type (rule A: site + admin combo)
"company website with employee portal" → ASK project_type (rule A: site + admin combo)
"law firm site with case management" → ASK project_type (rule A: site + admin combo)
"coffee shop" → ASK location FIRST (physical, no city)
"company website" → ASK project_type (could be 1-page or full)
"clinic" → ASK audience (luxury vs budget flips entire design)
"restaurant" + already has location → ASK niche (cuisine matters for design)
"agency" → ASK project_type (could be portfolio site or landing page)

══ INPUT ══
ALREADY CLARIFIED: {already_clarified}
ROUNDS: {rounds_used} of {max_rounds}
PROMPT: {task}

If rounds >= {max_rounds} → return {{"clear": true}}.

Return JSON only:
{{"clear": true}}
OR
{{"clear": false, "question": {{"key": "snake_case", "text": "max 12 words", "options": [{{"id": "snake_id", "label": "2-5 word label"}}]}}}}
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
            max_tokens=320,
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

        # Normalize key — snake_case only (no allowlist; trust Gemini)
        key = re.sub(r"[^a-z0-9_]", "", q["key"].lower().strip())
        if not key:
            return None

        # Don't re-ask a key already answered
        if key in already_clarified:
            logger.info("clarity_agent: suppressed already-answered key=%r", key)
            return None

        q["key"] = key
        logger.info("clarity_agent: round %d — asking %r (%d options)",
                    rounds_used + 1, key, len(q["options"]))
        return q

    except Exception as exc:
        logger.warning("clarity_agent: check failed (%s) — passing through", exc)
        return None
