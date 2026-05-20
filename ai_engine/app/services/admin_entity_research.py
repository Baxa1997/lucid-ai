"""Admin pipeline Stage 2 — grounded research for entity + operations signals.

Mirrors the website pipeline's `landing_domain_research` pattern but
tuned for admin/internal-tool generation. The website cares about
brand voice + audience psychographics + visual references; the admin
cares about:

  • What entities does this domain actually track? (industry vocab)
  • What status enums / workflows are real? (lead.status, deal.stage)
  • What KPIs do operators in this domain measure?
  • What screens are standard for this tool class?

Two parallel ``gemini-2.5-pro + google_search`` calls:
  1. ENTITY_RESEARCH    — entities, fields, relationships, status enums
  2. OPERATIONS_RESEARCH — KPIs, workflows, dashboard surfaces

The output is markdown with `===HEADER===` blocks. Downstream
consumers (admin_data_model_planner, admin_brand_extractor,
admin_plan) extract structured hints — they don't need to parse the
whole blob, just look at the section that matters to them.

Cost: ~$0.10-0.20 per admin generation (~5-8k output tokens × 2 calls,
gemini-2.5-pro). Cached at the pipeline level so re-runs of the same
project are free.

Public entry point: ``run_admin_entity_research``.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.services.landing_gemini import grounded_research, looks_degenerate

logger = logging.getLogger(__name__)


# ── Prompts ───────────────────────────────────────────────────────────

_ENTITY_RESEARCH_PROMPT = """You are designing the data model for an admin/internal tool in the {category} space ({subcategory}).

Tool purpose: {primary_purpose}
Geographic context: {geo_specifics}
Audience (who uses this admin daily): {audience}

⚠️ SEARCH-FIRST: Run google_search at least 3 times BEFORE writing. Cite real sources — actual SaaS products' help docs, ERP/CRM vendor pages, industry forums. Never invent. If you can't find 5 distinct sources, expand your searches.

Recommended initial searches:
  1. "{category} CRM entities" OR "{category} ERP data model"
  2. "{category} software workflow status" OR "{category} pipeline stages"
  3. "{category} admin dashboard fields" OR "{subcategory} management software"

Return markdown with these sections — be SPECIFIC to {category}, never generic:

===CORE_ENTITIES===
List 4-8 entities (database tables) that any serious admin tool in this domain MUST track.
Each line: <entity_name_snake_case> — <plural label> — <one-line purpose> [source URL]
Example for real_estate: properties — Properties — Listings managed by agents [source]

===ENTITY_FIELDS===
For each CORE entity above, list its 5-10 most important fields with realistic types.
Format:
  <entity_name>:
    - <field_name> (<type>) — <purpose>
    - ...
Types from: text, number, integer, boolean, date, datetime, image_url, email, phone, url, json
Mark fields that are typically required with [required].

===STATUS_ENUMS===
For each entity that has a workflow/state, list the typical enum values.
Format:
  <entity_name>.<field_name>: <value1> | <value2> | <value3>
Example: leads.status: new | contacted | qualified | proposal | won | lost

===RELATIONSHIPS===
List the entity-to-entity links that matter operationally.
Each line: <child_entity>.<fk_field> → <parent_entity>  — <meaning>
Example: deals.company_id → companies — Every deal is anchored to one company

===INDUSTRY_TERMINOLOGY===
8-12 domain-specific terms that the UI should use INSTEAD of generic words.
Each line: <preferred term> — <generic alternative it replaces> [source URL]
Example for fitness: "Member" — instead of "Customer" [source]

===REFERENCES===
Plain list of every URL cited above (deduped). Minimum 5 distinct domains.
"""


_OPERATIONS_RESEARCH_PROMPT = """You are researching the operational surfaces for an admin tool in the {category} space ({subcategory}).

Tool purpose: {primary_purpose}
Audience: {audience}

⚠️ SEARCH-FIRST: Run google_search at least 3 times BEFORE writing. Sources should be real SaaS product tours, dashboard screenshots, industry KPI articles. Never invent.

Recommended initial searches:
  1. "{category} dashboard KPI" OR "{category} metrics tracked"
  2. "{category} admin screens" OR "{subcategory} reporting"
  3. "{category} workflow automation" OR "{category} pipeline management"

Return markdown with these sections:

===DASHBOARD_KPIS===
6-10 KPIs that an operator in this domain wants on the home dashboard. Be specific — "Active leads this week" not "Engagement".
Each line: <KPI name> — <how it's computed (one phrase)> — <chart type: counter | line | bar | donut | table> [source URL]

===WORKFLOW_STAGES===
2-4 multi-step workflows specific to this domain that the admin should support.
Format:
  <workflow name>: <stage 1> → <stage 2> → <stage 3> → ...
Example for real_estate: Listing lifecycle: draft → active → under_contract → sold

===COMMON_SCREENS===
List 6-10 screens that operators in this domain expect.
Each line: <screen name> — <one-line purpose> — <primary entity it shows>
Mark required-for-MVP with [core] vs nice-to-have [advanced].

===AUTOMATIONS===
3-5 background tasks / scheduled jobs / triggers that mature tools in this space ship.
Each line: <trigger> → <action> [source URL]

===VISUAL_REFERENCES===
Name 3-5 real SaaS admin products that operators in this domain use today. Describe their UI density and color palette in one phrase each.
Each line: <product name> — <ui density: compact | comfortable | spacious> — <palette vibe> [source URL]

===REFERENCES===
Plain list of every URL cited above (deduped). Minimum 5 distinct domains.
"""


# ── Public entry ─────────────────────────────────────────────────────

async def run_admin_entity_research(
    intent: dict[str, Any],
    *,
    purpose_data: dict[str, Any] | None = None,
    timeout_s: float = 120.0,
    websocket: Any = None,
) -> dict[str, Any]:
    """Run admin Stage 2 research. Returns a dict, never raises.

    Output shape:
      {
        "entity_research":     "<markdown>",     # always set, "" on failure
        "operations_research": "<markdown>",     # always set, "" on failure
        "entity_sources":      <int>,            # grounding source count
        "operations_sources":  <int>,
        "all_urls":            [<url>, ...],     # deduped citations
      }

    The two calls run in parallel via `asyncio.gather`. Per-call failures
    degrade gracefully — the surviving call's output still flows
    downstream, the failed side is empty.
    """
    purpose_data = purpose_data or {}
    category = (intent.get("business_category") or "general").strip()
    subcategory = (intent.get("business_subcategory") or "general").strip()
    geo_specifics = (intent.get("geographic_specifics") or "global").strip()
    primary_purpose = (
        (purpose_data.get("primary_purpose") or intent.get("primary_purpose") or "internal operations")
        .strip()
    )
    audience_primary = (
        (intent.get("target_audience") or {}).get("primary")
        or purpose_data.get("target_audience")
        or "internal team members"
    )

    fmt = {
        "category": category,
        "subcategory": subcategory,
        "geo_specifics": geo_specifics,
        "primary_purpose": primary_purpose,
        "audience": audience_primary,
    }

    entity_prompt = _ENTITY_RESEARCH_PROMPT.format(**fmt)
    operations_prompt = _OPERATIONS_RESEARCH_PROMPT.format(**fmt)

    logger.info(
        "admin_entity_research: starting parallel research — category=%r subcategory=%r",
        category, subcategory,
    )

    # Explicit progress so the user sees BOTH research streams firing
    # at once — the website pipeline shows similar per-call messages
    # and admin felt silent without them.
    if websocket is not None:
        try:
            await websocket.send_json({
                "type": "progress",
                "message": (
                    f"🔬 Researching {category} admin tools in parallel: "
                    "entity vocab + operations KPIs (2 grounded Gemini calls)…"
                ),
            })
        except Exception:
            pass

    # Two parallel calls. `return_exceptions=True` so one failing
    # doesn't kill the other — the pipeline can still proceed with
    # partial signal.
    results = await asyncio.gather(
        grounded_research(
            entity_prompt, timeout_s,
            label="admin_entities",
            websocket=websocket,
            max_tokens=6144,
        ),
        grounded_research(
            operations_prompt, timeout_s,
            label="admin_operations",
            websocket=websocket,
            max_tokens=6144,
        ),
        return_exceptions=True,
    )

    entity_text, entity_sources, entity_urls = _unpack(results[0], "admin_entities")
    operations_text, operations_sources, operations_urls = _unpack(
        results[1], "admin_operations",
    )

    # Defend against tool-loop degenerate outputs (raw search snippets
    # repeated 100x). Treats them as failure so downstream synthesis
    # isn't poisoned by junk.
    if entity_text and looks_degenerate(entity_text):
        logger.warning(
            "admin_entity_research: ENTITY output looks degenerate — zeroing out",
        )
        entity_text = ""
        entity_sources = 0
        entity_urls = []
    if operations_text and looks_degenerate(operations_text):
        logger.warning(
            "admin_entity_research: OPERATIONS output looks degenerate — zeroing out",
        )
        operations_text = ""
        operations_sources = 0
        operations_urls = []

    all_urls = sorted(set(entity_urls + operations_urls))

    logger.info(
        "admin_entity_research: done — entity=%d chars (%d sources), "
        "operations=%d chars (%d sources), unique_urls=%d",
        len(entity_text), entity_sources,
        len(operations_text), operations_sources,
        len(all_urls),
    )

    return {
        "entity_research":     entity_text,
        "operations_research": operations_text,
        "entity_sources":      entity_sources,
        "operations_sources":  operations_sources,
        "all_urls":            all_urls,
    }


# ── Internal helper ──────────────────────────────────────────────────

def _unpack(result: Any, label: str) -> tuple[str, int, list[str]]:
    """Normalize a `gather(..., return_exceptions=True)` slot.

    Returns ``("", 0, [])`` for any failure mode — exception, None
    result, or unexpected shape. Logs the exception for debugging.
    """
    if isinstance(result, BaseException):
        logger.error("admin_entity_research %s: raised — %s", label, result)
        return ("", 0, [])
    if not isinstance(result, tuple) or len(result) != 3:
        logger.warning(
            "admin_entity_research %s: unexpected shape %r — coercing to empty",
            label, type(result).__name__,
        )
        return ("", 0, [])
    text, sources, urls = result
    return (text or "", int(sources or 0), list(urls or []))
