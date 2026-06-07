"""Stage 4.5 — Gemini-driven DataModel planning.

Given the website plan (pages + sections) plus the upstream research
artifacts (intent, purpose_data, visual_dna), decide:

  • Which sections become Supabase collections (tables that the
    forthcoming admin pipeline will CRUD)?
  • Which sections stay in `src/content/*.json` singletons (one-off
    copy that ships with the build)?

The output is a validated `DataModel` (Step 1.2). Step 2.2 will wire
this into `website_pipeline.run_website_pipeline` between Stage 4
(plan) and Stage 5 (foundation); Step 2.3 will change codegen to read
from Supabase for collection-backed sections.

Why Gemini Flash
----------------
Pure rules-based mapping (section.type ∈ {"menu"} → menu_items table)
gets ~70% of cases right but falls over on long-tail variation:
"specials" really is menu_items, "menu_categories" is its own table,
some "gallery" sections are actually team_members. Gemini Flash with
a tight prompt handles those judgment calls with one round-trip; the
fallback path (validation fails twice) emits an empty DataModel and
the site falls back to all-JSON content — degrade gracefully, never
abort the pipeline.

Cost
----
~3K input + ~1.5K output tokens. At Gemini Flash pricing
($0.075/1M in, $0.30/1M out) that's ~$0.0007 per call. Spec quoted
$0.002 as a safer upper bound — close enough; we log the estimate but
don't bill here.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Literal, Optional

from pydantic import ValidationError

from app.services.data_model import (
    DataModel,
    validate_data_model,
)
from app.services.landing_gemini import structured_distill

logger = logging.getLogger(__name__)


# ── Constants ─────────────────────────────────────────────────────────

# Columns the tenant_sql_generator adds automatically. If Gemini emits
# any of these despite the prompt instruction, we filter them out
# before validation (rather than retrying for what's a cosmetic emitter
# mistake). The hard ValueError in tenant_sql_generator._table_ddl is
# still the last line of defense.
_RESERVED_FIELD_NAMES = frozenset({"id", "created_at", "updated_at"})


# Rough cost estimate per call. Used in INFO logging so operators can
# spot a runaway pipeline without wiring real billing here.
_COST_INPUT_PER_1M_USD = 0.075
_COST_OUTPUT_PER_1M_USD = 0.30


# Gemini timeout — large enough for cold-start + Pro thinking
# (8K thinking budget can take 60-90s end-to-end), tight enough not
# to hang the pipeline indefinitely.
_GEMINI_TIMEOUT_S = 120.0


# ── Response schema ───────────────────────────────────────────────────
# Gemini's responseSchema is OpenAPI-shaped (UPPER_CASE primitive
# types). We keep this loose — Gemini will still match the DataModel's
# Pydantic shape downstream, and overly strict schemas trigger Gemini's
# "fail with empty response" mode. The Pydantic + validate_data_model
# pass catches anything the schema misses.
_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "required": ["tables", "singletons"],
    "properties": {
        "tables": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "required": ["name", "singular_label", "plural_label",
                             "description", "fields"],
                "properties": {
                    "name":           {"type": "STRING"},
                    "singular_label": {"type": "STRING"},
                    "plural_label":   {"type": "STRING"},
                    "description":    {"type": "STRING"},
                    "fields": {
                        "type": "ARRAY",
                        "items": {
                            "type": "OBJECT",
                            "required": ["name", "type"],
                            "properties": {
                                "name":        {"type": "STRING"},
                                "type":        {"type": "STRING"},
                                "required":    {"type": "BOOLEAN"},
                                "default":     {"type": "STRING"},
                                "max_length":  {"type": "INTEGER"},
                                "description": {"type": "STRING"},
                                "enum_values": {
                                    "type": "ARRAY",
                                    "items": {"type": "STRING"},
                                },
                            },
                        },
                    },
                    "indexes": {
                        "type": "ARRAY",
                        "items": {"type": "STRING"},
                    },
                    "public_read": {"type": "BOOLEAN"},
                },
            },
        },
        "singletons": {
            "type": "OBJECT",
            # Free-form — singleton shapes vary. Gemini emits whatever
            # makes sense per project (hero/about/contact/footer/etc.).
        },
    },
}


# ── Prompt ────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are a database designer for AI-generated marketing websites.
Your job is to decide which content needs to live in a database
(collections that grow and are edited over time) vs which content
stays as static JSON (singletons that rarely change).

RULES FOR INCLUDING A TABLE:
- Content has multiple rows (3+ entities of the same kind)
- Content will be edited regularly after initial generation
- Content has structured fields (not just one paragraph of prose)

RULES FOR KEEPING AS JSON SINGLETON:
- Content is one-of-a-kind (one mission statement, one hero copy)
- Content rarely changes
- Content is mostly unstructured prose

COMMON COLLECTIONS (add to tables when present):
- menu_items (restaurants, cafes, food businesses)
- products (ecommerce, retail)
- testimonials (most service businesses)
- gallery_images (photographers, restaurants, venues)
- team_members (agencies, clinics, firms with multiple staff)
- blog_posts (content sites, marketing blogs)
- events (event venues, schools, conferences)
- services (when there are 4+ distinct services with structured pricing)
- locations (multi-location businesses)
- faqs (when there are 5+ structured Q&As)

COMMON SINGLETONS (keep as JSON, NOT a table):
- Hero section (title, subtitle, CTA)
- About / company story
- Mission / values
- Contact info (one address, phone, email, hours)
- Footer content
- Single-service descriptions (when there's only 1-3 services)
"""


def _build_user_prompt(
    website_plan: dict,
    intent: dict,
    purpose_data: dict,
) -> str:
    """Render the per-project prompt with all upstream context inlined."""
    plan_json = json.dumps(website_plan, ensure_ascii=False, indent=2)

    # Intent + purpose may be partially populated (e.g. analyze_intent
    # timed out and returned defaults). Use .get() defaults so the
    # prompt is well-formed even when fields are missing.
    industry        = (intent.get("business_category")
                       or purpose_data.get("industry") or "general")
    geographic      = (intent.get("geographic_specifics")
                       or intent.get("geographic_scope") or "unspecified")
    tone            = intent.get("tone") or "neutral"
    primary_purpose = purpose_data.get("primary_purpose") or "brand_awareness"
    target_audience = purpose_data.get("target_audience") or "general"

    return f"""\
Design a data model for this website.

WEBSITE PLAN:
{plan_json}

BUSINESS CONTEXT:
- Industry: {industry}
- Primary purpose: {primary_purpose}
- Target audience: {target_audience}
- Geographic scope: {geographic}
- Tone: {tone}

For each section in the plan, decide:
- Does this need a table (collection)? What columns?
- Or does this stay as a JSON singleton?

Output a JSON object matching this schema:
{{
  "tables": [
    {{
      "name": "snake_case_plural_name",
      "singular_label": "Human Readable Singular",
      "plural_label": "Human Readable Plural",
      "description": "1 sentence what this table holds",
      "fields": [
        {{
          "name": "snake_case_column",
          "type": "text|number|integer|boolean|date|datetime|json|image_url|email|phone|url",
          "required": true,
          "default": null,
          "max_length": null,
          "description": "1 sentence what this column is for",
          "enum_values": null
        }}
      ],
      "indexes": ["field_name_1", "field_name_2"],
      "public_read": true
    }}
  ],
  "singletons": {{
    "hero": {{"title": "string", "subtitle": "string", "cta_text": "string"}}
  }}
}}

CRITICAL FIELD RULES:
- DO NOT include 'id', 'created_at', or 'updated_at' fields — these are added automatically
- All field names must be snake_case
- All table names must be snake_case plural
- For prices, use type 'number' (decimal) or 'integer' for cents
- For URLs to images, use 'image_url' (gets validation)
- For email addresses, use 'email' (gets validation)
- public_read should be true for collections shown publicly on the site
- public_read should be false only for admin-only data (rare in websites)

SINGLETONS ARE MANDATORY when the plan contains the corresponding section.
For every major fixed-copy section you see in the plan, emit a singleton
key with its main fields. Typical singletons that appear in nearly every
website: hero, about, contact, footer. Also include any section that's
clearly one-of-a-kind copy (pricing tiers for a SaaS, comparison table,
features list, story/about, FAQ if it's fixed and short). Use snake_case
keys.

A correct singletons block for a SaaS landing might look like:
"singletons": {{
  "hero":     {{"title": "string", "subtitle": "string", "cta_text": "string", "cta_url": "string"}},
  "features": {{"items": "array of {{title, description, icon}}"}},
  "pricing":  {{"tiers": "array of {{name, price, features}}"}},
  "faq":      {{"items": "array of {{question, answer}}"}},
  "footer":   {{"copyright": "string", "links": "array of {{label, url}}"}}
}}

NEVER return "singletons": {{}} unless the plan has zero fixed-copy
sections (almost never the case — at minimum a hero is fixed copy).

Return ONLY the JSON. No markdown fences, no commentary.
"""


# ── Main entry point ──────────────────────────────────────────────────

_GEMINI_MODEL_BY_VARIANT: dict[str, str] = {
    "flash":    "gemini-3.5-flash",        # default production model
    "pro":      "gemini-2.5-pro",          # current Pro tier
    "flash-3":  "gemini-3-flash-preview",  # Gemini 3 family — cheaper sibling
    "pro-3.1":  "gemini-3.1-pro-preview",  # Gemini 3 family — reasoning tier
}


async def plan_data_model(
    website_plan: dict,
    intent: dict,
    purpose_data: dict,
    visual_dna: Optional[dict] = None,
    *,
    gemini_key: str = "",
    project_id: str = "",
    model_variant: Literal["flash", "pro", "flash-3", "pro-3.1"] = "pro-3.1",
) -> DataModel:
    """Return a validated `DataModel` for this website's data layer.

    The optional `visual_dna` is accepted for API stability — the
    current prompt doesn't reference design tokens, but a future
    revision might (e.g. "minimalist" sites get fewer tables). Pass
    None when unavailable.

    `model_variant` chooses the Gemini SKU. Default is `"pro-3.1"`
    (gemini-3.1-pro-preview) — picked from a 7-fixture 3-way bake-off
    where Pro 3.1 was the only variant that consistently caught the
    subtle "this will grow over time" tables (subscribers,
    project_images, contact_inquiries, faqs) that the others missed.
    At ~$0.025 per project on a one-shot per-project call the cost is
    negligible.
      • `"flash"`    → `gemini-3.5-flash` (~$0.0003/call, last-gen)
      • `"flash-3"`  → `gemini-3-flash-preview` (~$0.003/call, fast)
      • `"pro"`      → `gemini-2.5-pro` (last-gen Pro, ~2× slower than 3.1)
      • `"pro-3.1"`  → `gemini-3.1-pro-preview` (default; production)

    Failures degrade gracefully: two Gemini attempts, then an empty
    DataModel. This keeps the website pipeline operational on a Gemini
    outage; the generated site uses all-JSON content (no editable
    backend layer) until the project owner re-runs.

    `project_id` is used for logging only.
    """
    _ = visual_dna  # reserved for future use

    if model_variant not in _GEMINI_MODEL_BY_VARIANT:
        raise ValueError(
            f"unknown model_variant={model_variant!r}; "
            f"expected one of {list(_GEMINI_MODEL_BY_VARIANT)}"
        )
    gemini_model = _GEMINI_MODEL_BY_VARIANT[model_variant]

    logger.info(
        "data_model_planner: planning started for project %s "
        "(pages=%d, industry=%r, purpose=%r, variant=%r)",
        project_id or "<no-id>",
        len(website_plan.get("pages") or []),
        intent.get("business_category") or "?",
        purpose_data.get("primary_purpose") or "?",
        model_variant,
    )

    user_prompt = _build_user_prompt(website_plan, intent, purpose_data)
    full_prompt = _SYSTEM_PROMPT + "\n\n" + user_prompt

    model = await _call_gemini_with_validation(
        prompt=full_prompt,
        gemini_key=gemini_key,
        gemini_model=gemini_model,
        attempt=1,
    )

    if model is None:
        logger.warning(
            "data_model_planner: falling back to empty DataModel for project %s",
            project_id or "<no-id>",
        )
        return DataModel(version="1.0", tables=[], singletons={})

    table_names = [t.name for t in model.tables]
    singleton_keys = sorted(model.singletons.keys())
    logger.info(
        "data_model_planner: final model — tables=%s, singletons=%s",
        table_names, singleton_keys,
    )
    return model


# ── Gemini call + parse + retry ───────────────────────────────────────

async def _call_gemini_with_validation(
    *,
    prompt: str,
    gemini_key: str,
    gemini_model: str,
    attempt: int,
) -> Optional[DataModel]:
    """Single Gemini call → parse → validate. Retries up to attempt==2.

    Returns the validated `DataModel` on success, or None after the
    second attempt fails. The caller (`plan_data_model`) is responsible
    for the empty-DataModel fallback.

    Retry payload includes the error message from the failed parse so
    Gemini can self-correct on the second pass.
    """
    label = f"data_model_planner_attempt_{attempt}"

    # Pro models (2.5-pro, 3.1-pro-preview) blew through the previous
    # 1024-token thinking cap on complex fixtures (portfolio,
    # multi_location_gym), returning empty bodies with
    # finishReason=MAX_TOKENS. Give Pro 8K thinking + 24K output room
    # so reasoning never starves the structured-output budget. Flash
    # variants don't think (budget=0 by default) so they're unaffected;
    # the bigger maxOutputTokens is harmless because Flash rarely emits
    # more than ~2K tokens.
    is_pro = "pro" in gemini_model.lower()
    _thinking_budget = 8192 if is_pro else None  # None → auto-pick

    try:
        # NOTE: deliberately no response_schema here. With a schema, Gemini
        # Flash short-circuits to the trivially-valid `{"tables": [],
        # "singletons": {}}` whenever the prompt has any ambiguity — even
        # rich plans with obvious hero/about/contact singletons. The
        # downstream parse+validate cycle is the real guardrail, and
        # dropping the schema lets Gemini follow the prompt's narrative
        # instructions properly.
        raw = await structured_distill(
            prompt=prompt,
            timeout_s=_GEMINI_TIMEOUT_S,
            label=label,
            response_schema=None,
            max_tokens=32768,
            temperature=0.2,
            model=gemini_model,
            thinking_budget=_thinking_budget,
        )
    except Exception as exc:  # noqa: BLE001 — surface any provider error
        logger.warning(
            "data_model_planner: Gemini call (attempt %d) raised: %s",
            attempt, exc,
        )
        if attempt >= 2:
            return None
        return await _call_gemini_with_validation(
            prompt=prompt + f"\n\nThe previous request failed with: {exc!r}. "
                            "Return corrected JSON only.",
            gemini_key=gemini_key,
            gemini_model=gemini_model,
            attempt=attempt + 1,
        )

    _log_cost_estimate(prompt, raw, attempt)

    parse_error = _try_parse_to_model(raw)
    if isinstance(parse_error, DataModel):
        # Filtering must happen BEFORE validation: Gemini sometimes
        # emits reserved field names despite the prompt. Strip them
        # rather than burn a retry on a cosmetic mistake.
        cleaned = _filter_reserved_fields(parse_error)
        errors = validate_data_model(cleaned)
        if not errors:
            logger.info(
                "data_model_planner: validation passed on attempt %d "
                "(tables=%d, singletons=%d)",
                attempt, len(cleaned.tables), len(cleaned.singletons),
            )
            return cleaned

        if attempt >= 2:
            logger.warning(
                "data_model_planner: validation failed after %d attempts: %s",
                attempt, errors,
            )
            return None
        return await _call_gemini_with_validation(
            prompt=prompt + (
                f"\n\nYour previous response failed validation:\n"
                + "\n".join(f"  - {e}" for e in errors)
                + "\n\nReturn corrected JSON only."
            ),
            gemini_key=gemini_key,
            gemini_model=gemini_model,
            attempt=attempt + 1,
        )

    # parse_error is the exception message
    if attempt >= 2:
        logger.warning(
            "data_model_planner: parse failed after %d attempts: %s",
            attempt, parse_error,
        )
        return None
    return await _call_gemini_with_validation(
        prompt=prompt + (
            f"\n\nYour previous response had errors:\n{parse_error}\n\n"
            "Return corrected JSON only."
        ),
        gemini_key=gemini_key,
        gemini_model=gemini_model,
        attempt=attempt + 1,
    )


def _try_parse_to_model(raw: str) -> "DataModel | str":
    """Parse raw Gemini output into a DataModel.

    Returns a `DataModel` on success, or the string-form error message
    on JSON or Pydantic failure. The caller dispatches on type to
    decide whether to retry.

    Strips a leading ```json … ``` fence when Gemini ignores the
    "no markdown fences" instruction — frequent enough that not
    handling it would waste retries.
    """
    text = (raw or "").strip()
    if text.startswith("```"):
        # Drop leading fence + optional `json` tag
        text = text.split("\n", 1)[1] if "\n" in text else text
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
        text = text.strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        return f"JSON parse error: {exc}"

    try:
        return DataModel.model_validate(parsed)
    except ValidationError as exc:
        return f"Pydantic validation error: {exc}"


def _filter_reserved_fields(model: DataModel) -> DataModel:
    """Drop reserved column names that Gemini emitted anyway.

    The tenant_sql_generator adds id/created_at/updated_at automatically
    and refuses to emit a CREATE TABLE for a model that includes them.
    Filtering here makes the planner resilient to Gemini drift without
    masking real bugs — every drop is logged at WARNING."""
    cleaned_tables = []
    dropped: list[tuple[str, str]] = []
    for table in model.tables:
        kept_fields = []
        for field in table.fields:
            if field.name in _RESERVED_FIELD_NAMES:
                dropped.append((table.name, field.name))
                continue
            kept_fields.append(field)
        # Rebuild via model_copy so other table attributes survive.
        cleaned_tables.append(table.model_copy(update={"fields": kept_fields}))
    if dropped:
        logger.warning(
            "data_model_planner: stripped reserved field names from Gemini "
            "output: %s",
            dropped,
        )
    return model.model_copy(update={"tables": cleaned_tables})


def _log_cost_estimate(prompt: str, response: str, attempt: int) -> None:
    """Best-effort cost estimate. 4 chars ≈ 1 token rule-of-thumb."""
    in_tokens = max(1, len(prompt) // 4)
    out_tokens = max(1, len(response) // 4)
    cost_usd = (
        in_tokens * _COST_INPUT_PER_1M_USD / 1_000_000
        + out_tokens * _COST_OUTPUT_PER_1M_USD / 1_000_000
    )
    logger.info(
        "data_model_planner: gemini call (attempt %d) — ~%d in / ~%d out tokens, ~$%.5f",
        attempt, in_tokens, out_tokens, cost_usd,
    )


# Re-export for tests / external callers that want the reserved-name
# set without importing _RESERVED_FIELD_NAMES directly.
RESERVED_FIELD_NAMES = _RESERVED_FIELD_NAMES
