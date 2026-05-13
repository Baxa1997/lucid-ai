"""Knowledge Loader — Layer 2 of the 3-Layer AI Architecture.

Reads pattern files, quality standards, and framework rules from the
local `knowledge/` directory and builds context blocks that are injected
into Claude's prompts.

Architecture:
  Layer 1 (MCP):   TEMPLATE_MANIFEST.md — what exists in the workspace
  Layer 2 (Skills): Knowledge files — HOW to build things correctly
  Layer 3 (Plugin): Layer 1 + Layer 2 bundled per-template

This module implements Layer 2.
"""
import os
import logging
from typing import Optional

logger = logging.getLogger("lucid.knowledge")

# Path to the knowledge directory — this file lives inside it.
_KNOWLEDGE_DIR = os.path.dirname(os.path.abspath(__file__))


def safe_gemini_text(response_json: dict) -> str:
    """Extract text from a Gemini generateContent response, tolerating every
    observed response shape.

    Gemini returns wildly different shapes depending on finish state:
    - Normal:   {candidates: [{content: {parts: [{text: "..."}]}}]}
    - String:   {candidates: [{content: "raw text"}]}          (rare)
    - Thinking: parts contain {thought: true, text: "..."}      → filtered out
    - Parts-as-strings: {content: {parts: ["text", ...]}}        (seen on some grounding paths)
    - Blocked:  {candidates: []} + {promptFeedback: {blockReason: "SAFETY"}}
    - Error:    {error: {message: "..."}}

    Raises RuntimeError for blocked/missing candidates (caller decides whether
    to fall back). Returns empty string if candidates exist but produced no
    usable text — caller should treat that as "retry or fail".
    """
    if not isinstance(response_json, dict):
        raise RuntimeError(
            f"Gemini response is not a dict: {type(response_json).__name__}"
        )

    candidates = response_json.get("candidates") or []
    if not isinstance(candidates, list) or not candidates:
        block_reason = (
            (response_json.get("promptFeedback") or {}).get("blockReason")
            or (response_json.get("error") or {}).get("message")
            or "unknown"
        )
        raise RuntimeError(f"Gemini returned no candidates (blockReason={block_reason})")

    candidate = candidates[0] if isinstance(candidates[0], dict) else {}
    if candidate.get("finishReason") == "SAFETY":
        raise RuntimeError("Gemini blocked response due to safety filter")

    content = candidate.get("content", {})

    if isinstance(content, str):
        return content

    if not isinstance(content, dict):
        return ""

    parts = content.get("parts")
    if not isinstance(parts, list):
        return ""

    chunks: list[str] = []
    for p in parts:
        if isinstance(p, dict):
            if p.get("thought"):
                continue
            txt = p.get("text")
            if isinstance(txt, str) and txt:
                chunks.append(txt)
        elif isinstance(p, str) and p:
            chunks.append(p)

    return "\n".join(chunks)


def _read_file(filepath: str, max_chars: int = 8000) -> str:
    """Read a file, returning empty string on failure."""
    try:
        if os.path.isfile(filepath):
            with open(filepath, "r", errors="replace") as f:
                content = f.read()
            return content[:max_chars]
    except Exception as e:
        logger.warning("Failed to read knowledge file %s: %s", filepath, e)
    return ""


def classify_project_type(task: str) -> str:
    """Fast static classifier — returns app_type string (legacy interface)."""
    result = _classify_static(task)
    return result["app_type"]


# ── Ambiguity detection ─────────────────────────────────────────────
# When a prompt mixes signals from two incompatible archetypes (e.g.
# "landing page" + cart/checkout), the keyword fast-path locks it into
# the wrong route. detect_classification_conflict surfaces these so the
# caller can ask the user before any expensive work runs.
_LANDING_SIGNALS = (
    "landing page", "landing-page", "one-pager", "one pager", "one-page",
    "single page", "single-page", "promo page", "marketing page",
    "scrollable page",
)
_ECOMMERCE_SIGNALS = (
    "cart", "checkout", "add to cart", "shopping cart", "online store",
    "online shop", "ecommerce", "e-commerce", "sell products",
    "product catalog", "buy products", "place orders",
)

# Strong recruitment signals — phrases that mean "this page recruits".
# Kept narrow to avoid false-positives on incidental mentions of jobs.
_HIRING_SIGNALS = (
    "now hiring", "we're hiring", "we are hiring", "join our team",
    "apply now", "apply today", "open positions", "open roles",
    "career opportunities", "recruitment page", "hiring page",
    "recruit drivers", "recruit operators", "recruiting drivers",
    "recruiting operators", "hire drivers", "hire operators",
)

# Brand/marketing signals — explicit framing that the page sells the
# COMPANY, not the jobs. Generic "company website" doesn't count
# (too common); we want phrases that explicitly say it's marketing.
_BRAND_MARKETING_SIGNALS = (
    "marketing page", "marketing site", "marketing website",
    "brand page", "brand site", "brand landing", "company brochure",
    "promotional page", "promotional site",
)


def detect_classification_conflict(task: str) -> Optional[dict]:
    """Detect prompts that mix incompatible archetype signals.

    Returns a clarification dict (question + options) when the user's
    prompt names one archetype but contains strong signals for another,
    or None if there is no conflict.
    """
    if not task:
        return None
    t = task.lower()

    has_landing = any(s in t for s in _LANDING_SIGNALS)
    has_ecom = any(s in t for s in _ECOMMERCE_SIGNALS)
    has_hiring = any(s in t for s in _HIRING_SIGNALS)
    has_brand = any(s in t for s in _BRAND_MARKETING_SIGNALS)

    if has_landing and has_ecom:
        return {
            "kind": "landing_vs_ecommerce",
            "question": (
                "It looks like you want an online store. Should I generate a "
                "full e-commerce website with cart and checkout, or just a "
                "marketing landing page?"
            ),
            "options": [
                {
                    "id": "ecommerce",
                    "label": "Full e-commerce website with cart & checkout",
                },
                {
                    "id": "single_page_landing",
                    "label": "Marketing landing page only",
                },
            ],
        }

    # Hiring vs brand-marketing — fires when a prompt explicitly frames
    # itself as marketing AND contains strong recruitment signals. Avoids
    # false-positives on prompts like "logistics company hiring drivers"
    # (no marketing-frame keyword → analyze_intent handles it directly).
    if has_hiring and has_brand:
        return {
            "kind": "hiring_vs_brand",
            "question": (
                "Should this page focus on RECRUITING (apply now, pay rates, "
                "open roles) or on MARKETING the company (services, story, "
                "client trust signals)?"
            ),
            "options": [
                {
                    "id": "single_page_landing",
                    "label": "Recruiting page — apply now, pay rates, open roles",
                    "purpose_hint": "hiring",
                },
                {
                    "id": "single_page_landing",
                    "label": "Marketing page — services, story, trust signals",
                    "purpose_hint": "brand_awareness",
                },
            ],
        }
    return None


# Internal marker used to lock the classifier to a user-chosen archetype
# after they answer a clarification question. Stripped by the project
# generator before the description flows into prompts.
ARCHETYPE_LOCK_PREFIX = "[LUCID_FORCE_ARCHETYPE::"


def force_archetype_from_task(task: str) -> tuple[Optional[str], str]:
    """Parse an archetype-lock marker out of a task string.

    The marker can sit at the start of the task or buried inside a
    larger ``enriched_task`` (the orchestrator may prepend conversation
    context before the raw task is consumed). Returns
    (locked_archetype, task_without_marker). When no marker is present,
    returns (None, task) unchanged.
    """
    import re as _re
    if not task or ARCHETYPE_LOCK_PREFIX not in task:
        return None, task or ""
    m = _re.search(r"\[LUCID_FORCE_ARCHETYPE::([a-z_]+)\]\s*", task)
    if not m:
        return None, task
    return m.group(1), task[: m.start()] + task[m.end():]


# Generic clarify-context marker. Prepended to the task by the WS layer
# whenever the user answers a Stage-0 clarification question. Multiple
# markers can stack (one per answered question) and survive across
# pipeline re-runs so the analyzer / brief sees the disambiguating
# context every pass without asking again.
#
# Shape: ``[LUCID_CLARIFY::<key>=<value>]`` where both halves are
# snake_case identifiers. Plain text (no JSON, no quotes) so it remains
# legible when the orchestrator splices it into prompts.
CLARIFY_MARKER_PREFIX = "[LUCID_CLARIFY::"


def extract_clarify_context(task: str) -> tuple[dict, str]:
    """Strip every ``[LUCID_CLARIFY::key=value]`` marker out of *task*.

    Returns ``(answers_dict, task_without_markers)``. ``answers_dict``
    maps ``key`` → ``value`` (last write wins on duplicate keys). When
    no markers are present, returns ``({}, task)``. The cleaned task is
    what flows downstream into prompts so the markers themselves never
    reach Gemini / Claude.
    """
    import re as _re
    if not task or CLARIFY_MARKER_PREFIX not in task:
        return {}, task or ""
    answers: dict[str, str] = {}
    pattern = _re.compile(r"\[LUCID_CLARIFY::([a-z][a-z0-9_]*)=([a-z0-9_\-]+)\]\s*")
    cleaned = pattern.sub(lambda m: answers.update({m.group(1): m.group(2)}) or "", task)
    return answers, cleaned


def format_clarify_marker(key: str, value: str) -> str:
    """Build a single ``[LUCID_CLARIFY::key=value]`` marker.

    Both halves are coerced to snake_case lowercase. Caller is responsible
    for prepending the marker to the task with a trailing space / newline.
    """
    safe_key = "".join(c if c.isalnum() else "_" for c in (key or "").strip().lower())
    safe_val = "".join(c if (c.isalnum() or c == "-") else "_" for c in (value or "").strip().lower())
    return f"[LUCID_CLARIFY::{safe_key}={safe_val}]"


def map_project_type_to_archetype(project_type_value: str) -> Optional[str]:
    """Map a clarity-agent project_type answer to a layout_archetype.

    Tolerant of variations (landing_page, single_page, one_pager, etc.).
    Returns None if no confident match — caller falls back to classifier.
    """
    if not project_type_value:
        return None
    v = project_type_value.lower().strip().replace("-", "_")
    # Order matters: more specific matches first
    if any(s in v for s in ("ecommerce", "e_commerce", "online_store", "online_shop", "shop", "store")):
        return "ecommerce"
    if any(s in v for s in ("landing", "one_page", "one_pager", "single_page", "promo")):
        return "single_page_landing"
    if "portfolio" in v:
        return "portfolio"
    if "blog" in v or "magazine" in v:
        return "blog"
    if "marketplace" in v:
        return "marketplace"
    if any(s in v for s in ("dashboard", "saas", "web_app", "webapp", "admin")):
        return "saas_dashboard"
    if any(s in v for s in ("full_website", "multi_page", "website", "site")):
        return "consumer_website"
    return None


# ╔══════════════════════════════════════════════════════════════╗
# ║  AI-Powered Classifier (Gemini Flash — 2 seconds)           ║
# ╚══════════════════════════════════════════════════════════════╝

VALID_APP_TYPES = [
    "admin_panel", "ecommerce", "blog", "saas_app", "social",
    "booking", "analytics", "documentation", "portfolio",
    "entertainment", "food_restaurant", "medical", "education",
    "fitness", "travel", "real_estate", "landing_page",
    # Extended consumer types
    "marketplace", "events", "finance", "fashion", "automotive",
]

# Layout archetypes — the structural decision that drives all generation routing.
# This maps from layout_archetype → structural metadata.
LAYOUT_ARCHETYPES = {
    "single_page_landing": {
        "is_single_page": True,
        "nav_style": "top_header",
        "has_admin_features": False,
        "has_sidebar": False,
        "app_type_hint": "landing_page",
    },
    "consumer_website": {
        "is_single_page": False,
        "nav_style": "top_header",
        "has_admin_features": False,
        "has_sidebar": False,
        "app_type_hint": "consumer",
    },
    "admin_dashboard": {
        "is_single_page": False,
        "nav_style": "sidebar_left",
        "has_admin_features": True,
        "has_sidebar": True,
        "app_type_hint": "admin_panel",
    },
    "crm": {
        "is_single_page": False,
        "nav_style": "sidebar_left",
        "has_admin_features": True,
        "has_sidebar": True,
        "app_type_hint": "admin_panel",
    },
    "tms": {
        "is_single_page": False,
        "nav_style": "sidebar_left",
        "has_admin_features": True,
        "has_sidebar": True,
        "app_type_hint": "admin_panel",
    },
    "saas_dashboard": {
        "is_single_page": False,
        "nav_style": "sidebar_left",
        "has_admin_features": False,
        "has_sidebar": True,
        "app_type_hint": "saas_app",
    },
    "ecommerce": {
        "is_single_page": False,
        "nav_style": "sidebar_left",
        "has_admin_features": True,
        "has_sidebar": True,
        "app_type_hint": "ecommerce",
    },
    "blog": {
        "is_single_page": False,
        "nav_style": "top_header",
        "has_admin_features": False,
        "has_sidebar": False,
        "app_type_hint": "blog",
    },
    "portfolio": {
        "is_single_page": False,
        "nav_style": "top_header",
        "has_admin_features": False,
        "has_sidebar": False,
        "app_type_hint": "portfolio",
    },
    "marketplace": {
        "is_single_page": False,
        "nav_style": "top_header",
        "has_admin_features": False,
        "has_sidebar": False,
        "app_type_hint": "marketplace",
    },
}


# Structural families — Gemini research is allowed to refine WITHIN a family
# (e.g. admin_dashboard → crm) but must NOT cross family boundaries.
_STRUCTURAL_FAMILIES: dict[str, set] = {
    "single":   {"single_page_landing"},
    "consumer": {"consumer_website", "portfolio", "blog", "marketplace"},
    "admin":    {"admin_dashboard", "crm", "tms", "saas_dashboard", "ecommerce"},
}

def get_structural_family(layout_archetype: str) -> str:
    """Return the structural family name for a layout_archetype."""
    for family, members in _STRUCTURAL_FAMILIES.items():
        if layout_archetype in members:
            return family
    return "consumer"


def _build_rich_classification(
    layout_archetype: str,
    domain: str,
    locked: bool = False,
) -> dict:
    """Build a rich classification dict from layout_archetype + domain.

    locked=True means the classification came from an explicit keyword match and
    must not be overridden by Gemini's research-time correction.
    """
    meta = LAYOUT_ARCHETYPES.get(layout_archetype, LAYOUT_ARCHETYPES["consumer_website"])
    # app_type: for admin-like types use "admin_panel", for landing use "landing_page",
    # for known consumer domains use domain string, otherwise use layout hint
    if meta["has_admin_features"] or meta["has_sidebar"]:
        app_type = "admin_panel"
    elif layout_archetype == "single_page_landing":
        app_type = "landing_page"
    elif domain in VALID_APP_TYPES:
        app_type = domain
    else:
        app_type = meta["app_type_hint"]

    return {
        "layout_archetype": layout_archetype,
        "domain": domain,
        "app_type": app_type,
        "is_single_page": meta["is_single_page"],
        "nav_style": meta["nav_style"],
        "has_admin_features": meta["has_admin_features"],
        "has_sidebar": meta["has_sidebar"],
        "classification_locked": locked,
    }


_DOMAIN_KW_MAP = [
    (["restaurant", "food", "cafe", "bakery", "catering", "menu", "chef"], "food_restaurant"),
    (["travel", "tourism", "hotel", "vacation", "destination", "tour"], "travel"),
    (["real estate", "property", "apartment", "rental", "listing", "agent", "broker"], "real_estate"),
    (["fitness", "gym", "workout", "yoga", "crossfit", "personal trainer"], "fitness"),
    (["booking", "reservation", "appointment", "scheduling"], "booking"),
    (["medical", "health", "clinic", "doctor", "dental", "pharmacy", "wellness"], "healthcare"),
    (["school", "university", "course", "learning", "academy", "e-learning"], "education"),
    (["entertainment", "movie", "cinema", "streaming", "music", "gaming"], "entertainment"),
    (["social", "community", "forum", "network", "social media"], "social"),
    (["finance", "banking", "accounting", "fintech", "investment", "insurance",
      "acca", "cpa", "cfa", "icaew", "cima", "chartered accountant", "aat", "chartered"], "finance"),
    (["fashion", "clothing", "apparel", "boutique"], "fashion"),
    (["automotive", "cars", "vehicles", "dealer", "auto"], "automotive"),
    (["events", "concert", "ticket", "conference", "meetup"], "events"),
    (["saas", "software", "startup", "platform", "app", "tool", "product"], "saas"),
]


def _detect_domain(text: str) -> str:
    """Return best-matching domain for a description, or 'general'."""
    for kws, dom in _DOMAIN_KW_MAP:
        if any(kw in text for kw in kws):
            return dom
    return "general"


def _classify_static(task: str) -> dict:
    """Static keyword-based fallback classifier. Returns rich classification dict."""
    import re as _re
    task_lower = (task or "").lower()

    # LUCID_CLARIFY::project_type — user explicitly answered → trust it
    _ctx, _ = extract_clarify_context(task or "")
    _pt = _ctx.get("project_type")
    if _pt:
        _m = map_project_type_to_archetype(_pt)
        if _m:
            return _build_rich_classification(_m, _detect_domain(task_lower), locked=True)

    # "landing page" OR standalone "landing" word → single_page_landing (HIGHEST priority).
    # Catches: "acca landing", "startup landing", "saas landing", "landing for X", etc.
    if "landing page" in task_lower or _re.search(r'\blanding\b', task_lower):
        return _build_rich_classification("single_page_landing", _detect_domain(task_lower), locked=True)

    # TMS / logistics / transport signals — before admin check
    tms_kw = [
        "tms", "transportation management", "transport management",
        "freight management", "logistics management",
        "fleet management", "fleet tracking", "dispatch system",
        "delivery management", "shipment tracking", "route optimization",
        "truck", "carrier", "cargo", "freight",
    ]
    if any(kw in task_lower for kw in tms_kw):
        return _build_rich_classification("tms", "logistics", locked=True)

    # CRM signals — before admin check
    crm_kw = [
        "crm", "customer relationship", "sales pipeline", "lead management",
        "deal management", "contact management", "sales tracker",
        "prospect", "opportunity management",
    ]
    if any(kw in task_lower for kw in crm_kw):
        return _build_rich_classification("crm", "sales", locked=True)

    # General admin / management signals
    admin_kw = [
        "admin", "dashboard", "management system", "management panel",
        "cms", "backoffice", "back office",
        "erp", "tms", "wms", "hris", "hrms", "pos",
        "inventory", "warehouse", "supply chain",
        "manage users", "manage products", "manage orders",
        "data table", "crud", "admin panel",
        "compliance", "inspection", "operations tool", "ops tool",
        "internal tool", "back-office",
    ]
    if any(kw in task_lower for kw in admin_kw):
        # Detect specific admin subtypes
        if any(kw in task_lower for kw in ["project management", "task", "sprint", "agile", "kanban board", "team workspace"]):
            return _build_rich_classification("saas_dashboard", "saas", locked=True)
        return _build_rich_classification("admin_dashboard", "general", locked=True)

    # SaaS / project management
    saas_kw = ["project management", "task manager", "team workspace", "sprint", "agile",
               "collaboration tool", "team productivity", "saas platform"]
    if any(kw in task_lower for kw in saas_kw):
        return _build_rich_classification("saas_dashboard", "saas", locked=True)

    # E-commerce
    ecom_kw = ["ecommerce", "e-commerce", "online shop", "online store",
               "sell products", "product catalog", "checkout", "cart"]
    if any(kw in task_lower for kw in ecom_kw):
        return _build_rich_classification("ecommerce", "retail", locked=True)

    # Blog / content
    blog_kw = ["blog", "articles", "magazine", "publication", "news site", "content platform"]
    if any(kw in task_lower for kw in blog_kw):
        return _build_rich_classification("blog", "content", locked=True)

    # Portfolio
    portfolio_kw = ["portfolio", "showcase", "personal site", "design agency",
                    "freelancer", "photographer", "creative studio"]
    if any(kw in task_lower for kw in portfolio_kw):
        return _build_rich_classification("portfolio", "creative", locked=True)

    # Marketplace
    marketplace_kw = ["marketplace", "listing platform", "buy and sell",
                      "two-sided", "buyers and sellers"]
    if any(kw in task_lower for kw in marketplace_kw):
        return _build_rich_classification("marketplace", "commerce", locked=True)

    # Domain detection for consumer websites — reuse the shared _domain_kw_map
    detected = _detect_domain(task_lower)
    if detected != "general":
        return _build_rich_classification("consumer_website", detected)

    # Default: treat as consumer website (multi-page public site)
    return _build_rich_classification("consumer_website", "general")


async def classify_project_type_ai(
    task: str,
    force_archetype: Optional[str] = None,
) -> dict:
    """AI-powered project classifier using Gemini Flash.

    Returns a rich classification dict:
    {
        "layout_archetype": "consumer_website" | "single_page_landing" | "admin_dashboard" | "crm" | "tms" | "saas_dashboard" | "ecommerce" | "blog" | "portfolio" | "marketplace",
        "domain": "finance" | "food_restaurant" | "logistics" | ...,
        "app_type": str,   # legacy compat — used as app_type string downstream
        "is_single_page": bool,
        "nav_style": "top_header" | "sidebar_left",
        "has_admin_features": bool,
        "has_sidebar": bool,
    }

    Falls back to static keyword classifier if Gemini is unavailable.

    When ``force_archetype`` is provided (e.g. user resolved a
    classification conflict via a clarification dialog), classification
    is short-circuited and the archetype is locked.
    """
    if force_archetype and force_archetype in LAYOUT_ARCHETYPES:
        logger.info("classify: forced archetype=%s (user clarification)", force_archetype)
        return _build_rich_classification(
            force_archetype, _detect_domain((task or "").lower()), locked=True
        )

    # ── LUCID_CLARIFY::project_type override ──
    # When the clarity agent asked "landing page or full website?" and the
    # user picked one, the answer rides on the task as a marker. Trust it
    # over keyword/Gemini classification — the user explicitly chose this.
    _clarify_ctx, _ = extract_clarify_context(task or "")
    _project_type = _clarify_ctx.get("project_type")
    if _project_type:
        _mapped = map_project_type_to_archetype(_project_type)
        if _mapped:
            logger.info("classify: user picked project_type=%s → archetype=%s",
                        _project_type, _mapped)
            return _build_rich_classification(
                _mapped, _detect_domain((task or "").lower()), locked=True
            )

    # Fast-path: landing / one-pager synonyms are unambiguous — skip Gemini to save time.
    # Lock the classification so neither Gemini research nor the schema validator can
    # escalate this to a multi-page consumer_website (which generated spurious
    # /about, /menu, /contact routes on a "coffee shop landing page" prompt).
    import re as _re2
    _t = task.lower()
    _landing_phrases = ("landing page", "one-page", "one page", "one pager", "one-pager",
                        "single page", "single-page", "scrollable page", "scroll-through page")
    if any(p in _t for p in _landing_phrases) or _re2.search(r'\blanding\b', _t):
        return _build_rich_classification("single_page_landing", _detect_domain(_t), locked=True)

    try:
        import httpx

        prompt = f"""Classify this project description into a LAYOUT ARCHETYPE and DOMAIN.

LAYOUT ARCHETYPES (choose ONE):
- single_page_landing: ONE scrollable page, anchor-link nav. Signals: "landing page", "one-page", "promo page", "product launch", "[brand/product] landing" (e.g. "acca landing", "saas landing", "startup landing")
- consumer_website: Multi-page public site, top header nav. Signals: "X website", "website for X", "[brand] website", business site
- admin_dashboard: Internal tool, sidebar nav, CRUD tables, KPIs. Signals: "management system", "admin panel", "ops tool", "back-office"
- crm: Customer pipeline, sidebar, kanban, contacts/deals. Signals: "CRM", "sales pipeline", "lead management", "customer tracker"
- tms: Transport/logistics, sidebar, shipments/fleet/routes. Signals: "TMS", "freight", "logistics", "fleet management", "dispatch", "delivery tracker"
- saas_dashboard: SaaS workspace, sidebar, project/task management. Signals: "project management", "task tracker", "team workspace", "collaboration tool"
- ecommerce: Online store, products/cart/orders. Signals: "shop", "store", "e-commerce", "sell products"
- blog: Content publishing, articles/authors/categories. Signals: "blog", "magazine", "news site", "content platform"
- portfolio: Personal/agency showcase. Signals: "portfolio", "personal site", "design agency", "freelancer"
- marketplace: Two-sided platform, buyers + sellers. Signals: "marketplace", "listing platform", "buy and sell"

DOMAIN (specific industry/niche):
finance | food_restaurant | logistics | fitness | healthcare | real_estate | education | automotive | fashion | entertainment | events | travel | social | saas | legal | construction | retail | hospitality | government | general

CRITICAL RULES:
- "landing page for X" OR "X landing" (any word + landing) → ALWAYS single_page_landing, domain = X's industry
- "X website" or "website for X" → consumer_website (use the domain of X)
- Abbreviations like "acca" (finance body), "saas", "b2b" should inform the domain, not the archetype
- Anything with "management", "system", "tracker", "ops", "admin", "panel" → admin type
- "TMS", "freight", "fleet", "dispatch", "logistics" → tms
- "CRM", "pipeline", "leads", "deals", "contacts" → crm
- "project management", "task manager", "team workspace" → saas_dashboard

Return ONLY valid JSON, no markdown:
{{"layout_archetype": "...", "domain": "..."}}

Description: "{task}"

JSON:"""

        from app.services.gemini_http import gemini_post

        status, data, _ = await gemini_post(
            model="gemini-2.5-flash",
            payload={"contents": [{"parts": [{"text": prompt}]}]},
            timeout_s=10.0,
            label="classifier",
        )

        if status == 200 and data is not None:
            result_text = safe_gemini_text(data)

            # Parse JSON response
            result_text = result_text.strip()
            if "```" in result_text:
                import re
                result_text = re.sub(r"```(?:json)?", "", result_text).strip().strip("`")

            import json
            parsed = json.loads(result_text)
            layout = parsed.get("layout_archetype", "").strip().lower()
            domain = parsed.get("domain", "general").strip().lower()

            if layout in LAYOUT_ARCHETYPES:
                logger.info("Gemini classified '%s' → layout=%s domain=%s", task[:60], layout, domain)
                return _build_rich_classification(layout, domain)

            logger.warning("Gemini returned unknown layout '%s', falling back", layout)

    except Exception as exc:
        logger.warning("Gemini classifier failed: %s, using static fallback", exc)

    return _classify_static(task)


def get_pattern_knowledge(project_type: str, layout_archetype: str = "") -> str:
    """Load the architecture pattern knowledge for the given project type.

    Returns a markdown string with code patterns that Claude should follow.
    Routed first by layout_archetype (specific), then by project_type (legacy).
    """
    # layout_archetype → pattern file (most specific)
    _archetype_pattern_map = {
        "crm":               "crm_saas.md",
        "saas_dashboard":    "crm_saas.md",
        "ecommerce":         "ecommerce.md",
        "admin_dashboard":   "admin_panel.md",
        "tms":               "admin_panel.md",
        "consumer_website":  "consumer_website.md",
        "portfolio":         "consumer_website.md",
        "marketplace":       "consumer_website.md",
        "blog":              "website_landing.md",
        "single_page_landing": "website_landing.md",
    }

    # Legacy project_type fallback
    _legacy_pattern_map = {
        "admin_panel":    "admin_panel.md",
        "ecommerce":      "ecommerce.md",
        "analytics":      "admin_panel.md",
        "saas_app":       "crm_saas.md",
        "landing_page":   "website_landing.md",
        "blog":           "website_landing.md",
        "documentation":  "website_landing.md",
        "portfolio":      "consumer_website.md",
        "social":         "admin_panel.md",
        "booking":        "consumer_website.md",
        "food_restaurant": "consumer_website.md",
        "healthcare":     "consumer_website.md",
        "real_estate":    "consumer_website.md",
        "fitness":        "consumer_website.md",
        "finance":        "consumer_website.md",
        "travel":         "consumer_website.md",
    }

    if layout_archetype:
        filename = _archetype_pattern_map.get(layout_archetype, "website_landing.md")
    else:
        filename = _legacy_pattern_map.get(project_type, "website_landing.md")

    filepath = os.path.join(_KNOWLEDGE_DIR, "patterns", filename)
    content = _read_file(filepath)

    if content:
        label = layout_archetype or project_type
        return (
            f"\n## ARCHITECTURE PATTERN: {label.upper().replace('_', ' ')}\n"
            f"Follow these exact code patterns when implementing components.\n"
            f"Copy the structure — adapt the content to the specific project.\n\n"
            f"{content}\n"
        )
    return ""


def get_quality_standards() -> str:
    """Load the quality standards knowledge.

    Returns a markdown string with coding standards that Claude must follow.
    """
    filepath = os.path.join(_KNOWLEDGE_DIR, "quality_standards.md")
    content = _read_file(filepath, max_chars=6000)

    if content:
        return f"\n## CODING STANDARDS — FOLLOW EXACTLY\n{content}\n"
    return ""


def get_ux_skill() -> str:
    """Load the UX/Frontend skill definition (SKILL.md).

    SKILL.md activates the 'Senior UX Engineer + Accessibility Expert' persona
    and provides design principles, component checklists, and anti-patterns.
    Injected at the TOP of CLAUDE.md so it has the highest influence on generation.
    """
    filepath = os.path.join(_KNOWLEDGE_DIR, "SKILL.md")
    content = _read_file(filepath, max_chars=10000)
    if content:
        return f"\n## UX SKILL — SENIOR UX ENGINEER PERSONA\n{content}\n"
    return ""


def get_framework_knowledge(stack: str) -> str:
    """Load framework-specific knowledge.

    Returns additional rules for the specific framework (Next.js, Vue, React).
    """
    _stk = (stack or "").lower()

    if "nextjs" in _stk or "next" in _stk:
        filename = "nextjs.md"
    elif "vue" in _stk:
        filename = "vue.md"
    else:
        filename = "react.md"

    filepath = os.path.join(_KNOWLEDGE_DIR, "frameworks", filename)
    content = _read_file(filepath, max_chars=4000)

    if content:
        return f"\n## FRAMEWORK KNOWLEDGE\n{content}\n"
    return ""


def build_knowledge_context(task: str, stack: str = "", layout_archetype: str = "") -> dict:
    """Build the complete knowledge context for a project.

    Returns a dict with:
      - project_type: str — classified project type
      - pattern_knowledge: str — architecture pattern markdown
      - quality_standards: str — coding standards markdown
      - framework_knowledge: str — framework-specific rules markdown
      - full_context: str — all three combined into one injection block

    Pass layout_archetype (from Gemini research) for precise pattern routing.
    """
    project_type = classify_project_type(task)
    logger.info("Knowledge loader: project_type=%s layout_archetype=%s stack=%s",
                project_type, layout_archetype, stack)

    ux_skill = get_ux_skill()
    patterns = get_pattern_knowledge(project_type, layout_archetype=layout_archetype)
    quality = get_quality_standards()
    framework = get_framework_knowledge(stack)

    full = ""
    # UX skill first — highest priority context
    if ux_skill:
        full += ux_skill
    if patterns:
        full += patterns
    if quality:
        full += quality
    if framework:
        full += framework

    return {
        "project_type": project_type,
        "ux_skill": ux_skill,
        "pattern_knowledge": patterns,
        "quality_standards": quality,
        "framework_knowledge": framework,
        "full_context": full,
    }


def generate_claude_md(task: str, stack: str, workspace_path: str) -> Optional[str]:
    """Generate a CLAUDE.md file in the workspace.

    Claude Code automatically reads CLAUDE.md from the working directory
    BEFORE any prompts. This is the most powerful injection point — it
    acts as Layer 3 (Plugin = MCP + Skills bundled together).

    Returns the path to the created file, or None on failure.
    """
    ctx = build_knowledge_context(task, stack)
    project_type = ctx["project_type"]

    # Read template manifest if available
    manifest_content = ""
    manifest_path = os.path.join(workspace_path, "TEMPLATE_MANIFEST.md")
    if os.path.isfile(manifest_path):
        manifest_content = _read_file(manifest_path, max_chars=5000)

    # Build CLAUDE.md content
    claude_md = f"""# Project: {task[:100]}
# Type: {project_type}
# Stack: {stack}

## YOU ARE
A SENIOR FRONTEND ENGINEER at a top design agency (like Vercel, Linear, or Stripe).
You build production-grade web applications that look like they belong on Dribbble or Awwwards.
The UI must be STUNNING — users pay for this product. Quality is everything.

## DESIGN PHILOSOPHY (non-negotiable)

### Template Transformation (CRITICAL)
- Section/page components (src/components/sections/*, src/pages/*): OVERHAUL completely with industry-specific content, layout, and design. Don't just change text — build the right UI for this project.
- Layout components (MainLayout, Sidebar, Header, AppSidebar, AppHeader, MarketingHeader, MarketingFooter): MODIFY DATA ONLY — update nav items, brand name, colors. DO NOT change their structure or create replacements.
- Router files (src/router/routes.*): ADD new page routes. DO NOT restructure the routing architecture.
- The template is your foundation — enhance section components aggressively, but preserve the layout architecture.

### Visual Excellence
- Every component must feel PREMIUM — not a tutorial project
- Use Tailwind classes for shadows (shadow-md, shadow-lg, shadow-xl), rounded corners (rounded-xl, rounded-2xl)
- Smooth transitions: transition-all duration-300, hover:scale-105, hover:-translate-y-1
- Cards hover: hover:shadow-xl hover:-translate-y-1 transition-all duration-300
- Buttons: bg-primary hover:bg-primary/90 rounded-full px-6 py-3
- Generous whitespace — py-16 py-20 py-24 for sections, p-6 p-8 for cards
- Typography: text-4xl md:text-5xl font-bold tracking-tight for headings

### Color Discipline (Tailwind + shadcn/ui)
- Use Tailwind semantic classes: bg-primary, text-foreground, bg-muted, bg-card, etc.
- NEVER use var(--color-primary) or var(--color-bg) — those DON'T EXIST
- NEVER use inline style={{}} for colors — always className with Tailwind
- NEVER use hardcoded hex (#fff, #000, #333) — use Tailwind semantic colors

### Content Quality
- Write REAL, compelling content — headlines that sell, descriptions that inform
- Feature cards: 6+ items with specific, descriptive text (3+ sentences each)
- Testimonials: realistic quotes with names, roles, and companies
- Data tables: 5-10 rows of realistic mock data with proper formatting
- NO "Lorem ipsum", NO "Sample text", NO placeholder content
- Numbers and stats should be realistic: "$2.4M revenue", "10,000+ users", "99.9% uptime"

### Layout & Spacing (Tailwind)
- Container: max-w-7xl mx-auto px-4 sm:px-6 lg:px-8
- Section padding: py-16 md:py-20 lg:py-24
- Card grids: grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6 gap-8
- Spacing: space-y-4 space-y-6 for vertical, gap-4 gap-6 gap-8 for grids

### Responsive (Tailwind mobile-first breakpoints)
- Default: mobile (single column, compact)
- sm: 640px+ (minor adjustments)
- md: 768px+ (two columns, medium spacing)
- lg: 1024px+ (full layout, 3-column grids)
- xl: 1280px+ (max-width containers)

## WORKSPACE RULES

### 'use client' Directive (MANDATORY for Next.js App Router)
**EVERY .jsx/.tsx file that uses ANY of these MUST start with 'use client' on line 1:**
- React hooks: useState, useEffect, useRef, useCallback, useMemo, useContext
- Event handlers: onClick, onChange, onSubmit, onKeyDown, etc.
- Browser APIs: window, document, localStorage, sessionStorage
- Third-party client libraries: framer-motion, react-hook-form, etc.

**If in doubt, ADD 'use client'. It never hurts, but MISSING it crashes the build.**

### Import Safety (CRITICAL — build MUST pass)
1. Before writing ANY import, verify the target file exists in the workspace
2. If a file doesn't exist, create it before importing
3. Use RELATIVE imports only (../components/X, ./sections/Y)
4. Never use @/ alias — it may not be configured
5. Icons: import {{ IconName }} from 'lucide-react'
6. **BANNED ICONS — lucide-react does NOT export these (will crash build):**
   Facebook, Instagram, Twitter, Linkedin, Youtube, Tiktok, Pinterest, Github (brand icon)
   For social media icons, create inline SVG components:
   ```
   const FacebookIcon = ({{ className }}) => (<svg className={{className}} viewBox="0 0 24 24" fill="currentColor"><path d="M24 12.073c0-6.627-5.373-12-12-12s-12 5.373-12 12c0 5.99 4.388 10.954 10.125 11.854v-8.385H7.078v-3.47h3.047V9.43c0-3.007 1.792-4.669 4.533-4.669 1.312 0 2.686.235 2.686.235v2.953H15.83c-1.491 0-1.956.925-1.956 1.874v2.25h3.328l-.532 3.47h-2.796v8.385C19.612 23.027 24 18.062 24 12.073z"/></svg>)
   ```

### Component Architecture
1. Named export AND default export: `export function Name() {{ }} export default Name`
2. Loading state (skeleton shimmer or spinner)
3. Empty state (illustration + message + CTA)
4. Tailwind classes only — never inline style={{}}, never hardcoded hex
5. Responsive — sm:, md:, lg:, xl: Tailwind breakpoints

### After Every File
Ask yourself: "Would npm run build pass right now?"
- Does the file start with 'use client' if it uses hooks or events? (MOST COMMON FAILURE)
- Are all imports pointing to existing files?
- Am I using className with Tailwind, not inline style={{}}?
- Am I importing any social brand icons from lucide-react? (BANNED — use inline SVG)


"""

    if manifest_content:
        claude_md += f"""## TEMPLATE MANIFEST — Available Components
{manifest_content}

"""

    # UX skill first — establishes the Senior UX Engineer persona (highest priority).
    # Pattern knowledge and quality standards follow; framework rules come last.
    claude_md += ctx.get("ux_skill", "")
    claude_md += ctx.get("pattern_knowledge", "")
    claude_md += "\n" + ctx.get("quality_standards", "")
    claude_md += ctx.get("framework_knowledge", "")

    # Write to workspace
    claude_md_path = os.path.join(workspace_path, "CLAUDE.md")
    try:
        with open(claude_md_path, "w") as f:
            f.write(claude_md)
        logger.info("Generated CLAUDE.md (%d bytes) at %s", len(claude_md), claude_md_path)
        return claude_md_path
    except Exception as e:
        logger.warning("Failed to write CLAUDE.md: %s", e)
        return None
