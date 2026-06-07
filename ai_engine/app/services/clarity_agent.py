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

# ── Deterministic bare-project-type guard ──────────────────────────────
# Gemini's clarity check sometimes mis-classifies inputs like "landing page",
# "dashboard", "build me a website" as CLEAR — but they are missing the
# field/domain (a landing page for a coffee shop looks nothing like one for
# a SaaS tool). We MUST ask what the project is FOR before generating.
# This deterministic pre-check runs BEFORE the Gemini call so the behavior
# is stable across model drift and prompt variants. Mirrored in
# step1_validate._looks_like_garbage so a second line of defense exists if
# clarity_agent is ever bypassed.

_TYPE_WORDS = {
    "landing", "page", "pages", "website", "websites", "site", "sites",
    "homepage", "webpage", "web",
    "app", "apps", "application", "applications",
    "dashboard", "dashboards", "admin", "panel", "panels",
    "portal", "platform",
    "portfolio", "portfolios",
    "blog", "blogs",
    "store", "shop", "ecommerce",
}

_SURFACE_WORDS = {
    # Public surfaces
    "landing", "onepage", "onepager", "website", "websites", "site", "sites",
    "homepage", "webpage", "portfolio", "blog", "store", "shop", "ecommerce",
    "marketplace",
    # Product/internal surfaces
    "dashboard", "dashboards", "panel", "panels", "portal",
    "platform", "app", "apps", "application", "applications",
    "cms", "crm", "tms", "erp", "internal", "public",
}

_ADMIN_SIGNALS = {
    "admin", "dashboard", "panel", "backoffice", "back-office", "back", "office",
    "manage", "management", "crud", "cms", "crm", "tms", "erp", "internal",
    "operator", "operations", "inventory",
}

_PUBLIC_SITE_SIGNALS = {
    "website", "site", "landing", "homepage", "page", "pages", "public",
    "marketing", "seo", "blog", "portfolio", "store", "shop", "ecommerce",
}

_BLUEPRINT_SIGNALS = {
    "blueprint", "brief", "spec", "specification", "requirements",
    "prd", "scope", "user", "users", "roles", "features", "modules",
    "entities", "schema", "database", "flows", "workflow", "wireframe",
    "apis", "api", "endpoints", "acceptance", "milestones",
}

_PROJECT_TYPE_OPTIONS = [
    {"id": "landing_page", "label": "Landing page"},
    {"id": "full_website", "label": "Full website"},
    {"id": "admin_dashboard", "label": "Admin panel"},
    {"id": "marketing_with_admin", "label": "Website + admin"},
    {"id": "web_app", "label": "Web app / SaaS"},
]

_FILLERS = {
    # Articles, pronouns, auxiliaries
    "a", "an", "the", "i", "me", "my", "mine", "we", "us", "our",
    "you", "your", "yours", "they", "them", "their", "he", "she", "it", "its",
    "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did",
    "can", "could", "would", "should", "will", "shall", "may", "might", "must",
    # Verbs of creation
    "make", "makes", "made", "build", "builds", "built", "building",
    "create", "creates", "created", "creating",
    "develop", "develops", "developed", "developing",
    "design", "designs", "designed", "designing",
    "want", "wants", "wanted", "need", "needs", "needed",
    "give", "gives", "gave", "get", "gets", "got",
    # Prepositions, conjunctions
    "for", "with", "without", "and", "or", "but", "so", "yet",
    "of", "to", "from", "in", "on", "at", "by", "about", "as",
    # Generic style adjectives (don't convey a field)
    "modern", "simple", "clean", "minimal", "minimalist", "responsive",
    "fast", "beautiful", "professional", "premium", "stylish", "fresh",
    "sleek", "elegant", "creative", "fancy", "bold", "nice", "good",
    "cool", "great", "best", "awesome", "amazing", "new", "old",
    # Generic colors (don't convey a field)
    "blue", "red", "green", "dark", "light", "black", "white", "yellow",
    "orange", "purple", "pink", "gray", "grey", "brown", "gold", "silver",
    # Size
    "small", "large", "big", "tiny", "huge",
    # Politeness, placeholders
    "please", "thanks", "thank", "kindly",
    "something", "anything", "everything", "stuff", "thing", "things",
    "some", "any", "all", "very", "really", "quite", "pretty",
    "just", "only", "even", "also", "too", "still",
    # Channels — too generic on their own to identify a field
    "online", "mobile", "desktop",
}


def _has_field_context(text: str) -> bool:
    """Return True if text contains a substantive word that could identify a field.

    A substantive word is any token ≥3 chars that is not a project-type word
    and not a generic filler/style/color word. E.g. 'coffee', 'restaurant',
    'fitness', 'crm', 'todo', 'bakery' all qualify; 'modern', 'a', 'the' don't.
    """
    tokens = re.findall(r"[a-z]+", (text or "").lower())
    for t in tokens:
        if len(t) < 3:
            continue
        if t in _TYPE_WORDS:
            continue
        if t in _FILLERS:
            continue
        return True
    return False


def _has_project_type_context(text: str) -> bool:
    """True when the prompt already states the intended product surface."""
    tokens = set(re.findall(r"[a-z]+", (text or "").lower()))
    # Do not treat technical-layer words such as "frontend" or "backend" as a
    # product surface. Blueprints often include those while still omitting the
    # routing decision: landing page vs full website vs admin panel vs both.
    if tokens & _SURFACE_WORDS:
        return True
    if _has_admin_surface_signal(text):
        return True
    lowered = (text or "").lower()
    route_count = len(re.findall(r"(?m)(?:^|[\s,])/[a-z0-9][a-z0-9_/-]*", lowered))
    if route_count >= 2:
        return True
    if re.search(r"\bpages?\s*:", lowered) or re.search(r"\broutes?\s*:", lowered):
        return True
    if re.search(r"\bsections?\s*:", lowered):
        return True
    return False


def _has_admin_surface_signal(text: str) -> bool:
    """True for an admin product surface, not merely an "admin" user role."""
    lowered = (text or "").lower()
    tokens = set(re.findall(r"[a-z-]+", lowered))
    strong_admin_terms = _ADMIN_SIGNALS - {"admin"}
    if tokens & strong_admin_terms:
        return True
    return bool(re.search(
        r"\badmin\s+(dashboard|panel|portal|console|app|site|page|pages|area|section|view|crud)\b",
        lowered,
    ))


def _looks_like_blueprint(text: str) -> bool:
    """Detect a pasted product brief/blueprint/spec, not a one-line prompt."""
    lowered = (text or "").lower()
    tokens = set(re.findall(r"[a-z]+", lowered))
    signal_count = len(tokens & _BLUEPRINT_SIGNALS)
    has_structure_lines = bool(re.search(
        r"(?mi)^\s*(overview|goal|features?|modules?|roles?|entities|pages?|routes?|database|schema|requirements?)\s*[:#-]",
        text or "",
    ))
    return signal_count >= 3 or (signal_count >= 1 and has_structure_lines)


def _project_type_question(text: str, *, blueprint: bool = False) -> dict:
    """Ask the surface/depth question with stable option ids."""
    lowered = (text or "").lower()
    has_admin = _has_admin_surface_signal(lowered)
    has_public = bool(set(re.findall(r"[a-z-]+", lowered)) & _PUBLIC_SITE_SIGNALS)
    if blueprint:
        question = (
            "This looks like a blueprint. What should Lucid build from it?"
        )
    elif has_admin and has_public:
        question = "Should this be public-facing, internal admin, or both?"
    else:
        question = "What kind of project should I build from this?"

    options = list(_PROJECT_TYPE_OPTIONS)
    if has_admin and not has_public:
        options = [
            {"id": "admin_dashboard", "label": "Admin panel"},
            {"id": "marketing_with_admin", "label": "Website + admin"},
            {"id": "full_website", "label": "Full website"},
            {"id": "web_app", "label": "Web app"},
        ]
    elif has_public and not has_admin:
        options = [
            {"id": "landing_page", "label": "Landing page"},
            {"id": "full_website", "label": "Full website"},
            {"id": "marketing_with_admin", "label": "Website + admin"},
            {"id": "web_app", "label": "Web app"},
        ]

    return {"key": "project_type", "text": question, "options": options}


def _detected_type_label(text: str) -> str:
    """Pick a friendly natural-language label for the detected project type."""
    t = (text or "").lower()
    if "landing" in t or "homepage" in t:
        return "landing page"
    if "dashboard" in t or "admin" in t or "panel" in t or "portal" in t:
        return "dashboard"
    if "portfolio" in t:
        return "portfolio"
    if "blog" in t:
        return "blog"
    if "store" in t or "shop" in t or "ecommerce" in t:
        return "store"
    if "app" in t or "application" in t:
        return "app"
    return "site"


def is_bare_project_type(text: str) -> bool:
    """True if the text mentions a project type word but lacks any field/domain context.

    Examples that return True (need clarification):
      'landing page', 'a website', 'build me a site', 'modern landing page',
      'dashboard', 'portfolio', 'blue landing page', 'I want an app'
    Examples that return False (field is present, OK to proceed):
      'coffee shop landing page', 'CRM dashboard', 'todo app',
      'fitness coach portfolio', 'restaurant website', 'blog about plants'
    """
    if not text:
        return False
    tokens = set(re.findall(r"[a-z]+", text.lower()))
    if not tokens & _TYPE_WORDS:
        return False
    return not _has_field_context(text)


def _bare_type_clarification(text: str) -> dict:
    """Build the standard 'what's this for?' clarify question for a bare type."""
    label = _detected_type_label(text)
    return {
        "key": "field",
        "text": f"Got it — what's this {label} for? Tell me about the business, product, or person.",
        "options": [],
    }


def _strip_lucid_project_header(text: str) -> str:
    """Return the user-facing prompt from a [LUCID_PROJECT] task envelope."""
    raw = (text or "").strip()
    if not raw.startswith("[LUCID_PROJECT]"):
        return raw
    if "\n\n" in raw:
        tail = raw.split("\n\n", 1)[1].strip()
        if tail:
            return tail
    m = re.search(r"description=([^|]+)", raw)
    return m.group(1).strip() if m else raw

_SYSTEM_PROMPT = """\
You are a smart project intake agent for an AI web design platform. Talk like a sharp designer scoping a project: ask dynamic, specific questions — ONE per round — to genuinely understand what the user wants, before generation starts.

══ STEP 0 — INCOHERENT-INPUT GUARD (CHECK BEFORE EVERY OTHER STEP) ══
Users can write in ANY language — handle them all (English, Russian, Spanish, Arabic, Chinese, Uzbek, Vietnamese, etc.). The OUTPUT question text must be written in the SAME language the user wrote in (mirror it).
If the prompt does NOT describe a coherent project intent — i.e. random words, lyrics, pseudo-sentences, keyboard mash, pasted unrelated text, or vague exclamations — DO NOT try to guess. Return a `field`-keyed open question asking what they want to build, in their language.
REJECT (these are incoherent, ask field OPEN):
  • "tree apple banana keyboard sunshine"   ← random English nouns
  • "дом стол окно компьютер"               ← random Russian nouns
  • "lorem ipsum dolor sit amet"            ← placeholder text
  • "asdf qwerty xcvbn"                     ← keyboard mash
  • "hello how are you"                     ← greeting, no project signal
  • A list of disconnected nouns in any language with no project-type or business word
ACCEPT (these are legitimate, even if terse — proceed to STEP 1):
  • "italian restaurant in florence"        ← field=italian restaurant, ask project_type
  • "сайт для кафе" (Russian: "website for café") ← field=café, ask project_type
  • "kino website"                          ← cinema/movie site; ask field for what kind
  • "yoga studio"                           ← field=yoga; ask project_type
  • "blog about plants"                     ← field=plants blog; ask style
RULE OF THUMB: if you cannot name in one short phrase what business/product/topic the project would be FOR after reading the prompt, treat it as incoherent — return a field question, do not assume an interpretation.

══ GOAL ══
After the STEP 0 guard passes, nail the project CORE, then ask a few smart follow-ups that would change the design. Ask one question per round, phrased naturally for THIS prompt (not generic). Return {{"clear": true}} once the core is set and you understand the audience / style / key features (or when ROUNDS hits the max).
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
    websocket=None,
) -> Optional[dict]:
    """Return a question dict if clarification needed, else None.

    Returned dict: {key, text, options: [{id, label}]}.
    Returns None when prompt is clear or on any error (fail-open).

    `websocket` is optional — when present, we emit `agent.status` events
    around the Gemini call so the FE can show "Understanding your
    prompt…" instead of a misleading preview-pipeline label.
    """
    from app.services.landing_gemini import structured_distill
    from app.services.agent_status import emit_agent_status
    from knowledge.loader import extract_clarify_context, force_archetype_from_task

    rounds_used = len(already_clarified)
    if rounds_used >= _MAX_ROUNDS:
        return None

    # Strip internal markers before showing to Gemini
    _, clean_task = force_archetype_from_task(task)
    _, clean_task = extract_clarify_context(clean_task)
    clean_task = _strip_lucid_project_header(clean_task)

    if not clean_task:
        return None

    # ── Deterministic pre-check ────────────────────────────────────────
    # If the task is a bare project type ("landing page", "a website",
    # "dashboard", "build me a site"), short-circuit and force a field
    # clarification. This guarantees stable behavior — we never reach the
    # Gemini call for inputs that empirically slip through (a 2025-05
    # regression where "landing page" alone was being marked clear).
    if "field" not in already_clarified and is_bare_project_type(clean_task):
        q = _bare_type_clarification(clean_task)
        logger.info(
            "clarity_agent: bare project type %r — asking %r (deterministic)",
            clean_task[:60], q["key"],
        )
        return q

    # Blueprint/spec mode: a pasted brief can be very detailed while still
    # omitting the most important routing decision: landing page vs full site
    # vs admin vs both. Ask before research so the expensive stages can fill
    # the right gaps instead of guessing.
    if (
        "project_type" not in already_clarified
        and _looks_like_blueprint(clean_task)
        and not _has_project_type_context(clean_task)
    ):
        q = _project_type_question(clean_task, blueprint=True)
        logger.info(
            "clarity_agent: blueprint without project_type — asking project_type",
        )
        return q

    # Domain-only prompt: "Italian restaurant in Brooklyn" is rich enough
    # for research, but not enough to know whether to build one page or a
    # multi-page website. Deterministically ask this before Gemini so the
    # first generation never picks the wrong surface.
    if (
        "project_type" not in already_clarified
        and "field" not in already_clarified
        and _has_field_context(clean_task)
        and not _has_project_type_context(clean_task)
    ):
        q = _project_type_question(clean_task)
        logger.info(
            "clarity_agent: domain-only prompt %r — asking project_type",
            clean_task[:80],
        )
        return q

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
        # Switched from gemini-3.1-pro-preview to gemini-3.5-flash. The 3.5
        # Flash release on Vertex is the latest stable Flash (faster than
        # 3-flash-preview, no schema-bug like 3-flash-preview has on JSON
        # outputs). The STEP-0 multilingual incoherence guard in
        # _SYSTEM_PROMPT carries the heavy lifting now, so the model's
        # marginal intent-inference advantage over Flash isn't worth the
        # latency. Verified available on Vertex AI publisher catalog
        # (us-central1, publishers/google/models/gemini-3.5-flash).
        await emit_agent_status(
            websocket,
            key="understanding_prompt",
            label="Understanding your prompt...",
            description="Checking what you'd like to build",
            state="active",
            source="gemini_flash",
            workflow_id="prompt_intake",
        )
        raw = await structured_distill(
            prompt,
            timeout_s,
            label="clarity_check",
            response_schema=response_schema,
            max_tokens=480,
            model="gemini-3.5-flash",
            # Drop to near-zero temperature so the STEP-0 incoherence guard
            # behaves deterministically across reruns. The default 0.2
            # produced the "sometimes proceeds, sometimes asks" flakiness
            # the user reported on identical random-word inputs.
            temperature=0.05,
        )
        result = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(result, dict):
            return None
        if result.get("clear"):
            # Safety net: even if Gemini says clear, override when the prompt
            # is still a bare project type (the pre-check above should have
            # caught this, but the model occasionally edits the task in ways
            # the heuristic doesn't see).
            if (
                "field" not in already_clarified
                and is_bare_project_type(clean_task)
            ):
                logger.info(
                    "clarity_agent: Gemini returned clear=True but %r is bare — overriding",
                    clean_task[:60],
                )
                await emit_agent_status(
                    websocket,
                    key="waiting_for_details",
                    label="Waiting for your answer",
                    description="The agent needs the project domain before generating",
                    state="waiting",
                    source="clarity_agent",
                    workflow_id="prompt_intake",
                )
                return _bare_type_clarification(clean_task)
            # Clear → pipeline takes over. Flip status to done so the FE
            # doesn't stick on "Understanding your prompt…" during the
            # gap before the first task_phase event fires.
            await emit_agent_status(
                websocket,
                key="understanding_done",
                label="",
                description="",
                state="done",
                source="clarity_agent",
                workflow_id="prompt_intake",
            )
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
        # Flip the FE status to "waiting" — the agent has done its work and
        # is now waiting for the user's answer. Without this the
        # understanding_prompt status sticks until the next backend event,
        # which makes the right panel look like it's still processing.
        await emit_agent_status(
            websocket,
            key="waiting_for_details",
            label="Waiting for your answer",
            description="The agent needs a bit more detail before generating",
            state="waiting",
            source="clarity_agent",
            workflow_id="prompt_intake",
        )
        return q

    except Exception as exc:
        logger.warning("clarity_agent: check failed (%s) — passing through", exc)
        # Failed Gemini call also clears the in-flight status so the FE
        # doesn't get stuck at "Understanding your prompt…".
        await emit_agent_status(
            websocket,
            key="understanding_done",
            label="",
            description="",
            state="done",
            source="clarity_agent",
            workflow_id="prompt_intake",
        )
        return None
