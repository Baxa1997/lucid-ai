"""Purpose classification — Stage 0.5 of the website pipeline.

Runs between Stage 0 (clarity agent) and Stage 1 (intent distillation).
Tells the downstream pipeline what the site is FOR — not just what
industry it belongs to. A "logistics company hiring CDL drivers" and a
"logistics company offering freight services" are both `logistics`
industry, but their pages, copy, CTAs, and section types diverge sharply.

One Gemini Flash call, structured output. Safe fallback (brand_awareness,
confidence=0) on any error so the pipeline never blocks.
"""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


_PRIMARY_PURPOSES = [
    "brand_awareness",
    "lead_generation",
    "recruitment",
    "product_showcase",
    "ecommerce",
    "appointment_booking",
    "event_registration",
    "education",
    "fundraising",
    "community",
]

_TARGET_AUDIENCES = [
    "b2b_buyers",
    "b2c_consumers",
    "job_seekers",
    "existing_customers",
    "investors",
    "partners",
]


_PURPOSE_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "required": [
        "primary_purpose", "industry", "target_audience",
        "named_roles", "urgency_signals", "confidence", "reasoning",
    ],
    "properties": {
        "primary_purpose": {"type": "STRING"},
        "industry":        {"type": "STRING", "maxLength": 60},
        "target_audience": {"type": "STRING"},
        "named_roles":     {"type": "ARRAY", "items": {"type": "STRING", "maxLength": 60}},
        "urgency_signals": {"type": "ARRAY", "items": {"type": "STRING", "maxLength": 80}},
        "confidence":      {"type": "INTEGER"},
        "reasoning":       {"type": "STRING", "maxLength": 280},
    },
}


_PROMPT = """You classify the PURPOSE of a website project. Your output drives page selection, copy tone, and CTAs downstream — be precise.

USER PROMPT:
{prompt}

CLARITY ANSWERS (from intake agent, may be empty):
{clarity_answers}

YOUR TASK
Return a JSON object with these fields:

1. primary_purpose — pick exactly ONE of:
   - brand_awareness        — pure presence / story / "we exist" sites
   - lead_generation        — capture inquiries from prospects (most B2B, clinics asking "get more patients")
   - recruitment            — hire people (the page recruits candidates, not customers)
   - product_showcase       — present a product/portfolio without checkout
   - ecommerce              — sell products online with cart/checkout
   - appointment_booking    — book a time slot (cleaning, haircut, consultation, dental visit)
   - event_registration     — register for a specific event/conference/workshop
   - education              — teach a topic (courses, schools, tutorials)
   - fundraising            — raise money (nonprofit donations, crowdfund)
   - community              — gather people around a shared interest (forum, club, association)

2. industry — short noun phrase (e.g. "logistics", "italian restaurant", "vintage cameras retail",
   "dental clinic", "marketing agency"). Free-form, but specific.

3. target_audience — pick exactly ONE of:
   - b2b_buyers, b2c_consumers, job_seekers, existing_customers, investors, partners

4. named_roles — list of specific job roles named in the prompt (e.g. ["CDL drivers", "warehouse associates"]).
   Empty list if no roles named. Only populate when primary_purpose=recruitment OR a role is literally named.

5. urgency_signals — short phrases capturing urgency/scale signals
   (e.g. "hiring 50 drivers this quarter", "grand opening next month", "now booking",
    "raising series A"). Empty list if none.

6. confidence — integer 0-100. How sure are you of primary_purpose?
   - 90-100: explicit signal ("hiring CDL drivers", "online shop", "book online")
   - 70-89:  strong implication ("dental clinic to get more patients" → lead_generation)
   - 50-69:  weighing two purposes
   - 0-49:   genuinely ambiguous — pick best guess

7. reasoning — one sentence explaining the choice (≤280 chars).

═══ CRITICAL RULES (override defaults) ═══
- "hiring" + ANY role (CDL driver, nurse, engineer, etc.) → primary_purpose=recruitment,
  target_audience=job_seekers. Even if the company is famous for selling/doing
  something else — the PAGE recruits candidates.
- Named job roles in the prompt → recruitment, always. The named role goes into
  named_roles. (Job roles look like: "drivers", "developers", "nurses", "cooks",
  "engineers", "associates", "managers", "technicians".)
- "shop", "store", "buy", "sell", "checkout", "cart" + products → ecommerce
- "book", "schedule", "appointment", "reserve a time", "now booking" → appointment_booking
  (NOTE: "reservations" for a restaurant ALSO maps here, but a restaurant site whose
   primary goal is *getting diners through the door / brand presence* is brand_awareness —
   only pick appointment_booking when booking IS the primary action of the site.)
- "get more patients/clients/customers/leads", "grow our pipeline" → lead_generation
- "showcase", "portfolio", "our work" without checkout → product_showcase
- Generic descriptive prompts about a local business with no explicit goal
  ("Italian restaurant in Brooklyn", "yoga studio in Austin") → brand_awareness

═══ EDGE CASES ═══
- "dental clinic" alone (no goal) → brand_awareness
- "dental clinic to get more patients" → lead_generation
- "dental clinic, book online" → appointment_booking
- "logistics company" alone → brand_awareness, industry=logistics
- "logistics company hiring CDL drivers" → recruitment, named_roles=["CDL drivers"]
- "restaurant" alone → brand_awareness
- "restaurant taking online reservations" → appointment_booking
- "online shop for vintage cameras" → ecommerce
- "vintage camera blog" → education (or community if forum-shaped)

Return ONLY the JSON. No markdown wrapper. No prose.
"""


_FALLBACK: dict[str, Any] = {
    "primary_purpose": "brand_awareness",
    "industry":        "general",
    "target_audience": "b2c_consumers",
    "named_roles":     [],
    "urgency_signals": [],
    "confidence":      0,
    "reasoning":       "Fallback — classifier unavailable or returned invalid output.",
}


async def classify_purpose(
    user_prompt: str,
    clarity_answers: dict | None = None,
    gemini_key: str = "",
    *,
    timeout_s: float = 25.0,
) -> dict[str, Any]:
    """Classify the website's purpose. Returns a dict — never raises.

    The ``gemini_key`` parameter is accepted for API symmetry with other
    services but is unused — Gemini auth is via Vertex ADC inside
    ``gemini_post``. Pass "" if you don't have one.
    """
    from app.services.landing_gemini import structured_distill

    if not user_prompt or not user_prompt.strip():
        return dict(_FALLBACK)

    clarity_str = (
        json.dumps(clarity_answers, ensure_ascii=False)
        if clarity_answers else "(none)"
    )
    prompt = _PROMPT.format(
        prompt=user_prompt.strip()[:2000],
        clarity_answers=clarity_str[:1000],
    )

    try:
        raw = await structured_distill(
            prompt,
            timeout_s,
            label="purpose_classify",
            response_schema=_PURPOSE_SCHEMA,
            max_tokens=600,
            model="gemini-3.5-flash",
        )
        data = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(data, dict):
            logger.warning("purpose_classifier: model returned non-dict — fallback")
            return dict(_FALLBACK)
        return _normalize(data)
    except Exception as exc:
        logger.warning("purpose_classifier: failed (%s) — fallback", exc)
        return dict(_FALLBACK)


def _normalize(data: dict[str, Any]) -> dict[str, Any]:
    """Coerce model output into the canonical shape. Always returns all fields."""
    primary = str(data.get("primary_purpose", "")).strip().lower()
    if primary not in _PRIMARY_PURPOSES:
        logger.info("purpose_classifier: unknown primary_purpose %r — coercing to brand_awareness", primary)
        primary = "brand_awareness"

    audience = str(data.get("target_audience", "")).strip().lower()
    if audience not in _TARGET_AUDIENCES:
        audience = "b2c_consumers"

    industry = str(data.get("industry") or "general").strip()[:60] or "general"

    named_roles = data.get("named_roles") or []
    if isinstance(named_roles, list):
        named_roles = [str(r).strip() for r in named_roles if str(r).strip()][:10]
    else:
        named_roles = []

    urgency = data.get("urgency_signals") or []
    if isinstance(urgency, list):
        urgency = [str(u).strip() for u in urgency if str(u).strip()][:10]
    else:
        urgency = []

    try:
        confidence = int(data.get("confidence", 0))
    except (TypeError, ValueError):
        confidence = 0
    confidence = max(0, min(100, confidence))

    reasoning = str(data.get("reasoning") or "")[:280]

    return {
        "primary_purpose": primary,
        "industry":        industry,
        "target_audience": audience,
        "named_roles":     named_roles,
        "urgency_signals": urgency,
        "confidence":      confidence,
        "reasoning":       reasoning,
    }
