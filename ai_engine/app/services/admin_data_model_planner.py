"""Admin-aware Stage 4.5 planner — operational entities, not marketing copy.

Sibling to `data_model_planner.py` but tuned for the opposite use case:
"what entities does this user need to MANAGE in their internal tool?"
rather than "what content does this marketing site need to display?"

Why a separate module instead of a flag on data_model_planner
-------------------------------------------------------------
Step 3.3's dry run with the website planner against an admin prompt
produced `updates`, `resources`, `faqs` — the planner's anchor
("marketing collections") steered Gemini toward content tables even
when given operational language. The system prompt is the single
biggest influence on Gemini's category choice, so the cleanest
intervention is a sibling module with a different system prompt.

Differences from the website planner
------------------------------------
  • Default `public_read=False` for every table (admin data is private)
  • Singletons section should be empty {} or admin_branding only
  • Domain examples are operational (CRM, helpdesk, restaurant ops)
  • Field patterns are CRUD-oriented (status, notes, slug references)
  • Returns parent's data_model unchanged when linked (no re-planning)

Cost
----
~3K input + ~1.5K output tokens, gemini-3.1-pro-preview. ~$0.04 per
call. Same one-shot-per-project pattern as the website planner.
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

# Same reserved-name set the website planner enforces. tenant_sql_generator
# adds these as system columns; any Gemini-emitted duplicates get stripped
# before validation rather than burning a retry.
_RESERVED_FIELD_NAMES = frozenset({"id", "created_at", "updated_at"})

# Cost-estimate constants — Gemini Pro 3.1 pricing.
_COST_INPUT_PER_1M_USD = 1.25
_COST_OUTPUT_PER_1M_USD = 10.0

# Gemini timeout — Pro 3.1 thinking can take 60-90s end-to-end.
_GEMINI_TIMEOUT_S = 120.0


# ── Prompt ────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are a database designer for INTERNAL ADMIN PANELS — operational
tools that businesses use to manage their data.

You are NOT designing a marketing website. You are NOT picking
sections like 'hero' or 'features' or 'testimonials'. Those are
marketing concepts and have no place here.

Your job: given a description of an internal tool, return the
database tables that the tool's users need to manage.

ADMIN MENTAL MODEL:
- Users log into the admin to do their daily work
- They view lists of entities, create new ones, edit existing
  ones, delete obsolete ones
- Common admin entities: customers, leads, contacts, products,
  orders, invoices, tickets, appointments, bookings, employees,
  transactions, inventory items, projects, tasks, requests
- Each entity is a row in a table — multiple of them, with
  structured fields

RULES FOR INCLUDING A TABLE:
- User will manage MANY of these (10+ rows over time)
- Has structured data (fields, not free-form prose)
- User performs CRUD operations on it as part of their work

RULES FOR NOT INCLUDING:
- Marketing copy (admins don't market — they operate)
- One-time configuration (settings go in app config, not tables)
- Anything that's "just one row" (admins don't manage singletons)

For admin panels, the 'singletons' section should be EMPTY or
contain only system-level config keys like 'admin_branding'.

COMMON ADMIN DOMAINS AND THEIR ENTITIES:
- CRM → contacts, leads, deals, activities, companies
- Restaurant ops → reservations, orders, customers, menu_items, staff
- Logistics → shipments, drivers, vehicles, routes, deliveries
- Real estate → properties, leads, viewings, agents, tenants
- Property management → properties, tenants, payments, maintenance_requests
- Inventory → items, suppliers, purchase_orders, stock_movements
- Bookings/appointments → appointments, customers, services, staff
- Helpdesk → tickets, customers, agents, knowledge_articles
- HR → employees, departments, leave_requests, performance_reviews
- Project management → projects, tasks, team_members, milestones
- Sales → opportunities, contacts, quotes, invoices
- Healthcare clinic → patients, appointments, treatments, prescriptions
- Education → students, courses, enrollments, grades, instructors
"""


def _build_user_prompt(intent: dict, purpose_data: dict) -> str:
    """Render the per-project prompt with the user's actual description
    and the upstream classifier signals inlined."""
    original_prompt  = intent.get("original_prompt") or ""
    category         = intent.get("business_category") or "?"
    subcategory      = intent.get("business_subcategory") or "?"
    industry         = (purpose_data.get("industry")
                        or intent.get("business_category") or "?")
    primary_purpose  = purpose_data.get("primary_purpose") or "operational"

    return f"""\
Design the database for this internal admin tool.

USER'S DESCRIPTION:
{original_prompt}

BUSINESS CONTEXT:
- Tool category: {category}
- Subcategory: {subcategory}
- Industry: {industry}
- Tool purpose: {primary_purpose}

What entities does the user need to MANAGE in this tool?

For each entity, identify the fields that the admin user will
create/edit/view. Think practically: what does someone using this
tool need to record about a customer / lead / order / appointment /
etc.?

COMMON FIELD PATTERNS:
- Person entities (customers, contacts, leads, employees):
  name, email, phone, status, notes
- Transaction entities (orders, invoices, payments):
  date, amount, status, customer_slug, items (json)
- Resource entities (products, properties, vehicles):
  name, description, price/value, status, image_url
- Event entities (appointments, viewings, deliveries):
  scheduled_at, duration_minutes, status, customer_slug, notes
- Communication entities (tickets, activities, messages):
  subject, body, created_by, status, related_to_slug

FIELD TYPES AVAILABLE:
text, number, integer, boolean, date, datetime, json, image_url,
email, phone, url

CRITICAL OUTPUT RULES:
- DO NOT include 'id', 'created_at', or 'updated_at' fields (added
  automatically)
- All field/table names are snake_case
- All tables MUST have public_read=false (admin data is private)
- Singletons section should be empty {{}} unless there's a system-level
  config like a logo URL
- For relationships, use *_slug fields (no foreign keys in v1)
- Most admin tools have 3-6 entities — don't artificially expand

Return ONLY this JSON structure:
{{
  "tables": [
    {{
      "name": "snake_case_plural",
      "singular_label": "Human Singular",
      "plural_label": "Human Plural",
      "description": "1 sentence on what this table holds",
      "fields": [
        {{
          "name": "snake_case_column",
          "type": "text|number|integer|boolean|date|datetime|json|image_url|email|phone|url",
          "required": true,
          "description": "1 sentence on what this column is for"
        }}
      ],
      "indexes": ["field_name"],
      "public_read": false
    }}
  ],
  "singletons": {{}}
}}

Return ONLY the JSON. No markdown fences, no commentary.
"""


# ── Main entry point ──────────────────────────────────────────────────

_GEMINI_MODEL_BY_VARIANT: dict[str, str] = {
    "flash":    "gemini-2.5-flash",
    "pro":      "gemini-2.5-pro",
    "flash-3":  "gemini-3-flash-preview",
    "pro-3.1":  "gemini-3.1-pro-preview",
}


async def plan_admin_data_model(
    intent: dict,
    purpose_data: dict,
    *,
    gemini_key: str = "",
    parent_data_model: Optional[DataModel] = None,
    project_id: str = "",
    model_variant: Literal["flash", "pro", "flash-3", "pro-3.1"] = "pro-3.1",
) -> DataModel:
    """Return a validated `DataModel` optimised for admin/CRUD use.

    Linked admins: when `parent_data_model` is provided, returns it
    unchanged. Admin panels with a `parent_project_id` inherit the
    parent's schema verbatim — re-planning would risk drifting tables
    out of sync with the parent's tenant.

    Standalone admins: asks Gemini "what entities does this tool's
    user manage?" with an admin-specific system prompt. Defensive
    post-processing forces `public_read=False` on every emitted table
    in case the model ignores the prompt.

    Failure model: two attempts, then an empty DataModel. The admin
    pipeline treats an empty DataModel as a hard failure (no useful
    admin without operating data) — see Decision 3 in Step 3.3.
    """
    # Linked-admin short-circuit.
    if parent_data_model is not None:
        logger.info(
            "admin_data_model_planner: linked admin — reusing parent data_model "
            "for project %s (tables=%d, singletons=%d)",
            project_id or "<no-id>",
            len(parent_data_model.tables),
            len(parent_data_model.singletons),
        )
        return parent_data_model

    if model_variant not in _GEMINI_MODEL_BY_VARIANT:
        raise ValueError(
            f"unknown model_variant={model_variant!r}; "
            f"expected one of {list(_GEMINI_MODEL_BY_VARIANT)}"
        )
    gemini_model = _GEMINI_MODEL_BY_VARIANT[model_variant]

    logger.info(
        "admin_data_model_planner: planning started for project %s "
        "(category=%r, purpose=%r, variant=%r)",
        project_id or "<no-id>",
        intent.get("business_category") or "?",
        purpose_data.get("primary_purpose") or "?",
        model_variant,
    )

    user_prompt = _build_user_prompt(intent, purpose_data)
    full_prompt = _SYSTEM_PROMPT + "\n\n" + user_prompt

    model = await _call_gemini_with_validation(
        prompt=full_prompt,
        gemini_key=gemini_key,
        gemini_model=gemini_model,
        attempt=1,
    )

    if model is None:
        logger.warning(
            "admin_data_model_planner: falling back to empty DataModel "
            "for project %s",
            project_id or "<no-id>",
        )
        return DataModel(version="1.0", tables=[], singletons={})

    # Defensive: force public_read=False even if Gemini ignored the
    # prompt instruction. Admin data is never anon-readable.
    coerced_tables = []
    forced_count = 0
    for table in model.tables:
        if table.public_read:
            forced_count += 1
            coerced_tables.append(table.model_copy(update={"public_read": False}))
        else:
            coerced_tables.append(table)
    if forced_count:
        logger.warning(
            "admin_data_model_planner: coerced public_read=False on %d "
            "table(s) — Gemini ignored the admin-private instruction",
            forced_count,
        )
    model = model.model_copy(update={"tables": coerced_tables})

    table_names = [t.name for t in model.tables]
    singleton_keys = sorted(model.singletons.keys())
    logger.info(
        "admin_data_model_planner: final model — tables=%s, singletons=%s",
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

    Same flow as data_model_planner._call_gemini_with_validation —
    duplicated rather than shared because the retry-prompt template
    references the admin context. Keeping them separate also means a
    future tweak to one path can't accidentally regress the other.
    """
    label = f"admin_data_model_planner_attempt_{attempt}"

    # Same Pro thinking-budget rationale as the website planner: Pro
    # 3.1's 1024-token default starves the structured-output budget
    # on complex prompts. 8K thinking + 24K output keeps reasoning
    # and answer separately funded.
    is_pro = "pro" in gemini_model.lower()
    _thinking_budget = 8192 if is_pro else None

    try:
        # No response_schema — same reason as data_model_planner: a
        # schema short-circuits Gemini into returning the trivially-
        # valid {"tables":[],"singletons":{}} when the prompt has any
        # ambiguity. Parse-then-validate is the real guardrail.
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
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "admin_data_model_planner: Gemini call (attempt %d) raised: %s",
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
        cleaned = _filter_reserved_fields(parse_error)
        errors = validate_data_model(cleaned)
        if not errors:
            logger.info(
                "admin_data_model_planner: validation passed on attempt %d "
                "(tables=%d, singletons=%d)",
                attempt, len(cleaned.tables), len(cleaned.singletons),
            )
            return cleaned

        if attempt >= 2:
            logger.warning(
                "admin_data_model_planner: validation failed after %d attempts: %s",
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

    if attempt >= 2:
        logger.warning(
            "admin_data_model_planner: parse failed after %d attempts: %s",
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
    on JSON or Pydantic failure. Strips ```json ... ``` fences.
    """
    text = (raw or "").strip()
    if text.startswith("```"):
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
    """Drop reserved column names emitted despite the prompt."""
    cleaned_tables = []
    dropped: list[tuple[str, str]] = []
    for table in model.tables:
        kept_fields = []
        for field in table.fields:
            if field.name in _RESERVED_FIELD_NAMES:
                dropped.append((table.name, field.name))
                continue
            kept_fields.append(field)
        cleaned_tables.append(table.model_copy(update={"fields": kept_fields}))
    if dropped:
        logger.warning(
            "admin_data_model_planner: stripped reserved field names from "
            "Gemini output: %s",
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
        "admin_data_model_planner: gemini call (attempt %d) — "
        "~%d in / ~%d out tokens, ~$%.5f",
        attempt, in_tokens, out_tokens, cost_usd,
    )


# Re-export for tests / external callers.
RESERVED_FIELD_NAMES = _RESERVED_FIELD_NAMES
