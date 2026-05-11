"""Purpose-specific research calls.

Triggered when ``analyze_intent`` detects a primary_purpose that needs
specialized facts the generic Pro+google_search call won't surface
(pay rates, application form fields, candidate pain points, etc.).

Each call is FAIL-SOFT — returns "" on any failure so the pipeline keeps
flowing without the augmented block. The caller appends the returned
text directly to the research blob.

Currently implemented:
  • run_recruitment_research — for primary_purpose == "hiring".

Add new purposes by writing one async function that returns a markdown
text block prefixed with a ``===PURPOSE_RESEARCH=== begin`` header and
ending with ``===PURPOSE_RESEARCH=== end``, then dispatch in
``maybe_run_purpose_research``.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


# ── Recruitment research ──────────────────────────────────────────────

_RECRUITMENT_PROMPT = """You are a recruiting strategist researching job-market reality for a hiring landing page.

CONTEXT:
- Industry / domain: {industry}
- Geographic scope: {geography}
- Named roles the user wants to hire: {named_roles}
- Urgency signals from prompt: {urgency_signals}

GOAL: produce a CODE-READY recruitment research block. Cite real sources; avoid generic platitudes.

Return your output in this EXACT structure (literal section markers):

===RECRUITMENT_RESEARCH===

## PAY_RATES
For each named role, provide concrete numeric ranges in the {geography} market:
- typical pay structure (CPM / hourly / weekly / salary / OO percentage)
- low / median / high range
- sign-on bonuses if common (cite at least one real company offering them)
- 1 source URL per claim

## COMPETITOR_POSTINGS
Find 3-5 REAL hiring pages or job postings (in or near the user's industry).
For each:
- Company name
- URL (must be real, not invented)
- Pay offered
- Top 2-3 benefits highlighted
- What makes the posting feel premium vs commodity (specific copy patterns)

## CANDIDATE_PAIN_POINTS
What candidates in this role complain about most (Reddit, Glassdoor, forums).
Top 5 pain points, ranked by frequency. Cite at least one source URL.

## WHAT_CANDIDATES_WANT
Top 5 things candidates prioritize when choosing an employer. Be specific:
- "home time" not "work-life balance"
- "modern equipment" not "good tools"
- "consistent miles" not "good schedule"

## INDUSTRY_TERMINOLOGY
Vocabulary the page should use (and pronounce correctly):
- Role abbreviations / qualifications (CDL-A, OTR, regional, OO, dry van, reefer, etc.)
- Compensation lingo (CPM, percentage, salary, mileage, accessorial)
- Equipment / route / shift terms specific to the role

## APPLICATION_FORM_BEST_PRACTICES
Specific to {named_roles}:
- Required fields (with input types)
- One-step vs multi-step
- 2-3 questions specific to this role (e.g. CDL class for drivers, portfolio link for designers)
- One drop-off-reduction tactic worth applying

===END_RECRUITMENT_RESEARCH===

RULES:
- Every numeric claim must have a source URL.
- If no concrete data is found for a section, write "no recent public data found" — do NOT invent numbers.
- No marketing fluff. No "competitive pay" without a number.
- Keep total under 1800 words. Specificity > comprehensiveness.
"""


async def run_recruitment_research(
    *,
    intent: dict[str, Any],
    classification: dict[str, Any],
    websocket: Any = None,
    timeout_s: float = 120.0,
) -> str:
    """Run grounded research for a hiring page. Returns markdown block or "".

    Uses Gemini Pro + google_search via landing_gemini.grounded_research,
    which authenticates through Vertex ADC inside gemini_post.
    """
    try:
        from app.services.landing_gemini import grounded_research
    except Exception as exc:
        logger.warning("recruitment_research: import failed — %s", exc)
        return ""

    industry = (
        intent.get("business_category")
        or classification.get("domain")
        or "general"
    )
    geo_scope = intent.get("geographic_scope") or "national"
    geo_specifics = intent.get("geographic_specifics") or ""
    geography = f"{geo_specifics or geo_scope}".strip() or "United States"

    named_roles = intent.get("named_roles") or []
    if not named_roles:
        # No specific roles surfaced — generic research is good enough; skip
        # the targeted call rather than waste a Pro grounded call on noise.
        logger.info("recruitment_research: skipping — no named_roles in intent")
        return ""

    urgency = intent.get("urgency_signals") or []

    prompt = _RECRUITMENT_PROMPT.format(
        industry=industry,
        geography=geography,
        named_roles=", ".join(named_roles[:6]) or "general workforce",
        urgency_signals=", ".join(urgency[:4]) or "none specified",
    )

    if websocket is not None:
        try:
            await websocket.send_json({
                "type": "progress",
                "message": f"🧑‍💼 Recruitment research — {len(named_roles)} role(s)",
            })
        except Exception:
            pass

    try:
        text, sources, _urls = await grounded_research(
            prompt,
            timeout_s=timeout_s,
            label="recruitment",
            websocket=websocket,
            max_tokens=4096,
            thinking_budget=1024,
            temperature=0.3,
        )
    except Exception as exc:
        logger.warning("recruitment_research call failed (non-fatal): %s", exc)
        return ""

    if not text:
        logger.info("recruitment_research: empty text returned")
        return ""

    # Defensive: if the model emitted the markers as plain ===RECRUITMENT_RESEARCH===
    # the block is already self-delimited. If not, wrap it ourselves so the
    # downstream extractor (research-section parser) can find it.
    block = text.strip()
    if "===RECRUITMENT_RESEARCH===" not in block:
        block = "===RECRUITMENT_RESEARCH===\n" + block + "\n===END_RECRUITMENT_RESEARCH==="

    logger.info(
        "recruitment_research: ok — %d chars, %d sources, %d roles",
        len(block), sources, len(named_roles),
    )
    return block


# ── Purpose directive (Claude prompt block) ──────────────────────────

_DIRECTIVES: dict[str, dict[str, Any]] = {
    "hiring": {
        "headline": "RECRUITMENT — this is a hiring page, not a company marketing page.",
        "rules": [
            "Hero leads with the job opportunity (named role + key benefit), NOT the company story.",
            "Pay rates appear above the fold OR in section 2. Use real numbers from ===RECRUITMENT_RESEARCH===; never write \"competitive pay\" without a number.",
            "Application form is MANDATORY. Required fields: Full name, Email, Phone, role-appropriate qualification (e.g. CDL class for drivers, portfolio link for designers), Years of experience, Preferred role (SELECT element), Available start date, Optional message. Submit text uses an action verb: \"Apply Now\", \"Start Application\".",
            "The \"Preferred role\" SELECT element MUST be populated with each NAMED ROLE from this directive as an <option>. Do NOT leave the select empty with only a placeholder. If only one named role exists, render it as a single pre-selected option (still inside <select>). Example: <select name=\"role\" required><option value=\"\">Select a position…</option><option value=\"cdl-a-driver\">CDL-A Driver</option><option value=\"owner-operator\">Owner Operator</option></select>",
            "Open positions section lists the SPECIFIC named roles from intent — each row showing title, pay rate, top requirements, route/shift type.",
            "Testimonials are from EMPLOYEES (work environment, equipment, home time, management), NOT from clients/customers.",
            "Use the industry vocabulary surfaced in ===RECRUITMENT_RESEARCH=== (CDL-A, OTR, CPM, dry van, etc. — when they exist for this role). A reader in this trade should recognise the language as insider.",
            "Talk TO candidates, not ABOUT the company. Wrong: \"Acme is committed to driver safety.\" Right: \"Your safety comes first — modern equipment, no pressure to skip rest stops.\"",
            "IMAGES: every visual section (hero, culture, day_in_life, testimonials, open_roles cards) MUST render images. Use the URLs in `section.images` when present. The hero specifically MUST render its image as a background or split-image — never a text-only hero on a hiring page (drivers respond to visuals of trucks, equipment, the road).",
        ],
        "forbidden": [
            "Company technology platform sections (unless the tech directly helps the candidate).",
            "B2B / client case studies / pricing plans.",
            "Investor or fundraising blocks.",
            "\"Why choose us\" framings — replace with \"Why drivers stay\" / \"Why our team stays\".",
        ],
        "primary_cta": "Apply Now",
        "secondary_cta": "View Open Positions",
        "tone": "direct, opportunity-focused, human",
    },
    "lead_generation": {
        "headline": "LEAD GENERATION — every section funnels toward a quote/contact form.",
        "rules": [
            "Hero leads with a problem-and-proof pair (the buyer's outcome + a credible signal: client logo / metric / award).",
            "A quote/contact form is MANDATORY (in-page, not just a /contact route). Fields: Name, Company, Email, Phone, Project type, Estimated budget or timeline, Message.",
            "Trust signals (client logos, metrics, certifications) appear within the first 1.5 viewports.",
            "Case studies / testimonials cite real outcomes with a number (\"reduced churn by 18%\"), not adjectives.",
            "Pricing or estimate framing — even if exact prices aren't public, give buyers a sense of range or process.",
        ],
        "forbidden": [
            "Job listings, application forms, employee benefits.",
            "Generic \"about our journey\" timelines unless they end in a credibility signal.",
        ],
        "primary_cta": "Get a Quote",
        "secondary_cta": "See Our Work",
        "tone": "professional, trustworthy, results-focused",
    },
    "ecommerce": {
        "headline": "E-COMMERCE — the page sells products. Every section nudges toward purchase.",
        "rules": [
            "Hero leads with a product, lifestyle shot, or featured collection — not a brand essay.",
            "Featured products grid appears within the first scroll. Each card shows price, image, name, primary CTA (\"Shop Now\" / \"View Details\").",
            "Trust signals (free shipping, returns policy, payment icons) sit near a CTA, not in the footer alone.",
            "Newsletter capture appears once (footer or pre-checkout exit-intent), never as a hero blocker.",
        ],
        "forbidden": [
            "Job postings, employee benefits, B2B case studies framed for procurement teams.",
        ],
        "primary_cta": "Shop Now",
        "secondary_cta": "Browse Collection",
        "tone": "vivid, confident, desire-building",
    },
    "booking": {
        "headline": "BOOKING — the page exists to fill a calendar. Every section reduces friction to schedule.",
        "rules": [
            "Hero pairs the offering with an immediate scheduling CTA (\"Book a table\" / \"Reserve a session\").",
            "A booking widget or form is in-page, not just on a separate route.",
            "Show availability cues (today's hours, next open slot) when the data is realistic.",
            "Service/menu list appears with prices or duration — buyers want to know before they click Book.",
        ],
        "forbidden": [
            "Generic \"about us\" novels above the fold; trim brand story to a single short paragraph below the booking module.",
        ],
        "primary_cta": "Book Now",
        "secondary_cta": "See Availability",
        "tone": "warm, frictionless, confident",
    },
}


def format_purpose_directive_block(intent: dict[str, Any]) -> str:
    """Return the ===PURPOSE_DIRECTIVE=== block for Claude, or "" if none.

    The block tells Claude what KIND of page this is at the goal level
    (hiring vs lead-gen vs ecommerce vs booking) so the generated sections
    serve the actual user goal — not a generic marketing template.
    """
    purpose = (intent or {}).get("primary_purpose") or ""
    spec = _DIRECTIVES.get(purpose)
    if not spec:
        return ""

    named_roles = list(intent.get("named_roles") or [])[:8]
    urgency = list(intent.get("urgency_signals") or [])[:4]

    lines = [
        "",
        "===PURPOSE_DIRECTIVE===",
        f"PRIMARY PURPOSE: {purpose.upper()}",
        spec["headline"],
        "",
        f"PRIMARY CTA:   {spec['primary_cta']}",
        f"SECONDARY CTA: {spec['secondary_cta']}",
        f"TONE:          {spec['tone']}",
    ]
    if named_roles:
        lines.append(f"NAMED ROLES:   {', '.join(named_roles)}")
    if urgency:
        lines.append(f"URGENCY:       {', '.join(urgency)}")

    lines.append("")
    lines.append("RULES:")
    for r in spec["rules"]:
        lines.append(f"  - {r}")

    lines.append("")
    lines.append("FORBIDDEN:")
    for f in spec["forbidden"]:
        lines.append(f"  - {f}")

    lines.append("===END_PURPOSE_DIRECTIVE===")
    lines.append("")
    return "\n".join(lines)


# ── Dispatch ──────────────────────────────────────────────────────────

async def maybe_run_purpose_research(
    *,
    intent: dict[str, Any],
    classification: dict[str, Any],
    websocket: Any = None,
) -> str:
    """Dispatch to the right purpose-specific research call. Returns "" if none.

    Add new purposes as elif branches here. Each branch should return a
    self-delimited markdown block (or "" on failure / no-op).
    """
    purpose = (intent or {}).get("primary_purpose") or ""

    if purpose == "hiring":
        return await run_recruitment_research(
            intent=intent,
            classification=classification,
            websocket=websocket,
        )

    # Future: add lead_generation / ecommerce / booking purpose-specific
    # calls when their generic-research outputs prove insufficient.
    return ""
