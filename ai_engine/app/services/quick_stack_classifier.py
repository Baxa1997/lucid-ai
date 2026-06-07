"""Quick stack classifier — runs BEFORE template clone.

Triggered when the user picks "Choose for me" in the wizard (stack=auto
or empty). Returns a stack key the `_TEMPLATE_REGISTRY` understands so
the right template repo gets cloned upfront — not a generic local
skeleton + last-minute overwrite by the admin pipeline.

Two-tier design:
  1. Fast keyword pre-filter (zero LLM cost) — catches unambiguous
     cases like "CRM for sales" or "restaurant landing page". When
     this fires we skip Gemini entirely.
  2. Gemini Flash structured-output classifier (~$0.001, ~1-2s) —
     only fires when keywords are ambiguous. Three-class output:
     nextjs | react | vue.

Failure modes are silent: keyword fallback first, then a conservative
``nextjs`` default. The caller (step1_validate) ALWAYS gets a string
back — never an exception, never None.
"""
from __future__ import annotations

import logging
import re
from typing import Literal

logger = logging.getLogger(__name__)


StackKey = Literal["nextjs", "react", "vue"]


# ── Layer 1: Keyword pre-filter (zero LLM cost) ──────────────────────

# Phrases that UNAMBIGUOUSLY identify an admin/internal tool. When any
# of these hit, we route straight to "react" (which maps to the
# react-admin template in _TEMPLATE_REGISTRY). Order matters — more
# specific phrases checked first to avoid false matches on "panel" etc.
_ADMIN_PATTERNS = (
    r"\bcrm\b",
    r"\btms\b",
    r"\berp\b",
    r"\bcms\b",
    r"\badmin\s+(?:panel|dashboard|tool|interface|portal|system)\b",
    r"\binternal\s+(?:tool|app|dashboard|admin)\b",
    r"\bback[\s-]?office\b",
    r"\bsaas\s+dashboard\b",
    r"\boperations?\s+dashboard\b",
    r"\bmanagement\s+(?:system|panel|dashboard|tool|software)\b",
    r"\binventory\s+management\b",
    r"\blead\s+management\b",
    r"\bcustomer\s+management\b",
    r"\bdispatch\s+system\b",
    r"\bfleet\s+management\b",
    r"\b(?:hr|hiring|payroll|asset|warehouse|case|ticket)\s+management\b",
)
_ADMIN_RE = re.compile("|".join(_ADMIN_PATTERNS), re.IGNORECASE)

# Phrases that UNAMBIGUOUSLY identify a marketing website / landing /
# multi-page content site. Routes to "nextjs" (mapped to
# nextjs-website template).
_WEBSITE_PATTERNS = (
    r"\blanding\s+page\b",
    r"\bmarketing\s+(?:website|site|page)\b",
    r"\b(?:company|brand|product)\s+website\b",
    r"\bportfolio\s+(?:website|site)\b",
    r"\b(?:restaurant|cafe|bakery|salon|gym|spa)\s+website\b",
    r"\bsingle[\s-]?page\s+(?:website|site)\b",
    r"\bone[\s-]?pager\b",
)
_WEBSITE_RE = re.compile("|".join(_WEBSITE_PATTERNS), re.IGNORECASE)

# Phrases that suggest Vue specifically (rare, but respect the user's
# explicit framework call-out).
_VUE_RE = re.compile(r"\bvue(?:\.?js)?\b", re.IGNORECASE)


def _keyword_classify(description: str) -> StackKey | None:
    """Return a stack key when keywords are decisive, otherwise None.

    Vue check comes first because "Vue admin panel" should resolve to
    vue, not react. Admin patterns are checked before website patterns
    because "admin website" should route to react-admin (the admin
    intent dominates).
    """
    if not description:
        return None
    if _VUE_RE.search(description):
        return "vue"
    if _ADMIN_RE.search(description):
        return "react"
    if _WEBSITE_RE.search(description):
        return "nextjs"
    return None


# ── Layer 2: Gemini Flash classifier (only on keyword miss) ──────────

_CLASSIFIER_PROMPT = """You classify a project description into ONE technical category for template selection.

DESCRIPTION:
{description}

Pick exactly one category:

  nextjs  — Marketing website, landing page, portfolio, brand site, blog,
            documentation, content/SEO-focused multi-page sites.
            (Examples: restaurant website, agency portfolio, SaaS landing,
            personal blog, news magazine, real-estate listing site that's
            primarily public-facing.)

  react   — Admin panel, dashboard, CRM, ERP, CMS, internal tool,
            back-office system, SaaS dashboard for authenticated users,
            inventory/lead/HR management software, anything CRUD-heavy
            for internal operators.
            (Examples: lead tracker, hospital admin, restaurant POS,
            warehouse management, fleet dispatch, school grading system.)

  vue     — ONLY when the user explicitly asks for Vue or Nuxt. Otherwise
            default to one of the above.

Return JSON:
{{ "stack": "nextjs" | "react" | "vue", "reasoning": "one short sentence" }}
"""


async def _gemini_classify(description: str, timeout_s: float) -> StackKey | None:
    """Call Gemini Flash with structured output. Returns None on any failure.

    Imports `structured_distill` lazily so this module is cheap to import
    in test paths that mock the classifier away.
    """
    try:
        from app.services.landing_gemini import structured_distill
    except Exception as exc:
        logger.warning("quick_stack_classifier: gemini import failed — %s", exc)
        return None

    prompt = _CLASSIFIER_PROMPT.format(description=description.strip()[:2000])

    schema = {
        "type": "OBJECT",
        "required": ["stack"],
        "properties": {
            "stack":     {"type": "STRING", "enum": ["nextjs", "react", "vue"]},
            "reasoning": {"type": "STRING"},
        },
    }

    try:
        raw = await structured_distill(
            prompt,
            timeout_s,
            label="quick_stack_classifier",
            response_schema=schema,
            max_tokens=256,
            temperature=0.0,
            model="gemini-3.5-flash",
        )
    except Exception as exc:
        logger.warning("quick_stack_classifier: gemini call failed — %s", exc)
        return None

    if not isinstance(raw, str) or not raw.strip():
        return None

    try:
        import json
        parsed = json.loads(raw)
    except Exception:
        logger.warning("quick_stack_classifier: gemini returned non-JSON — %r", raw[:120])
        return None

    stack = (parsed.get("stack") or "").strip().lower()
    if stack not in ("nextjs", "react", "vue"):
        logger.warning("quick_stack_classifier: gemini returned unknown stack %r", stack)
        return None

    reasoning = parsed.get("reasoning") or ""
    logger.info(
        "quick_stack_classifier: gemini picked %r — %s",
        stack, reasoning[:120],
    )
    return stack  # type: ignore[return-value]


# ── Public entry ─────────────────────────────────────────────────────

async def classify_stack(description: str, *, timeout_s: float = 8.0) -> StackKey:
    """Pick the best stack for `description`. Always returns a string.

    Pipeline:
      1. Keyword pre-filter (zero cost). When decisive → return.
      2. Gemini Flash classifier. When it returns a valid value → return.
      3. Conservative default: "nextjs".

    The wider system stays safe even when this returns the default
    because the admin pipeline (Stage 6) overwrites the workspace for
    admin archetypes regardless of which template was cloned.
    """
    description = (description or "").strip()
    if not description:
        logger.info("quick_stack_classifier: empty description — defaulting to nextjs")
        return "nextjs"

    kw_result = _keyword_classify(description)
    if kw_result is not None:
        logger.info(
            "quick_stack_classifier: keyword pre-filter resolved %r → %s",
            description[:60], kw_result,
        )
        return kw_result

    gemini_result = await _gemini_classify(description, timeout_s)
    if gemini_result is not None:
        return gemini_result

    logger.info(
        "quick_stack_classifier: no classifier signal — defaulting to nextjs for %r",
        description[:60],
    )
    return "nextjs"
