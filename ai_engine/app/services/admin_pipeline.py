"""Admin panel generation pipeline. Mirrors website_pipeline.py for
consistency.

Stages:
  0.5  Purpose classification    (Gemini)
  1    Intent analysis           (Gemini)
  2    Research                  STUB — admin_entity_research not built;
                                  planner output is the only entity signal
  3    Visual DNA / branding     (Gemini Flash via admin_brand_extractor
                                  for standalone; parent inheritance for
                                  linked admins)
  4    Admin plan                (deterministic from data_model:
                                  3 pages/entity + 3 shared pages,
                                  navigation built from entity list)
  4.5  Data model planning       (shared admin_data_model_planner —
                                  same DataModel shape as website)
  4.6  Tenant provisioning       (resolve linked OR provision new
                                  per-project schema + DDL apply)
  4.7  Seed data                 (Gemini Pro Preview → realistic rows
                                  for every collection; INSERTed)
  5    Foundation files          WIRED — calls build_admin_foundation:
                                  package.json, Vite config, AuthGuard,
                                  Login, Dashboard, Layout, supabase.js,
                                  db_admin.js, auth.js, .env.local, README
  6    CRUD codegen              WIRED — generate_one_admin_page runs
                                  N×3 (entity × {list, create, edit})
                                  in parallel under a Semaphore.
                                  Defaults to real Claude codegen when an
                                  Anthropic key is available; otherwise
                                  falls back to contract-honoring
                                  placeholders. Output goes through
                                  admin_codegen_validator before write.
  7    Build verification        WIRED — BuildValidator runs a Vite build
                                  by default in real-codegen mode; mock
                                  mode skips unless ADMIN_BUILD_VALIDATE=true

Linking model (admin vs website):
  • Standalone admin (parent_project_id NULL) → provisions its own
    tenant_schema + seed data via Phase 2 helpers.
  • Linked admin (parent_project_id set) → reads tenant_schema +
    data_model from the parent via `resolve_tenant_for_project`,
    skips Stages 4.5/4.6/4.7 entirely (data already exists).

Feature flag:
  ADMIN_PIPELINE_V2_ENABLED defaults ON for admin_dashboard / crm / tms /
  saas_dashboard archetypes. Set ADMIN_PIPELINE_V2_ENABLED=0 to force the
  caller to fall through to legacy admin generation in project_generator.py.

Failure model:
  Returns False on any hard failure so the caller can fall through
  to legacy admin generation. Failures inside Stages 4.6/4.7 are
  non-fatal (matches website_pipeline) — they degrade the generated
  admin to a placeholder but don't break the pipeline. Stage 6 with
  mock=false treats validator errors and missing files as fatal;
  mock=true is always treated as success.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Optional

from app.services.generation_build import run_generation_build_check
from app.services.generation_contract import GenerationResult

logger = logging.getLogger(__name__)


# ── Feature flag + routing ───────────────────────────────────────────

# Archetypes the admin pipeline v2 will handle when the flag is on.
# 'ecommerce' is intentionally NOT in this set — it has a more complex
# legacy path we'll migrate in a later step.
ADMIN_V2_ARCHETYPES = frozenset({
    "admin_dashboard",
    "crm",
    "tms",
    "saas_dashboard",
})


def _admin_pipeline_v2_enabled() -> bool:
    """Gate for Step 3.3 admin pipeline. Default ON.

    Set ADMIN_PIPELINE_V2_ENABLED=0 to fall through to legacy admin
    generation in project_generator.py.
    """
    raw = os.environ.get("ADMIN_PIPELINE_V2_ENABLED", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def should_route_to_admin_pipeline(layout_archetype: str) -> bool:
    """Single source of truth for project_generator's dispatcher.

    Returns True iff the pipeline is enabled AND the archetype is in
    `ADMIN_V2_ARCHETYPES`.
    """
    return (
        _admin_pipeline_v2_enabled()
        and (layout_archetype or "").strip() in ADMIN_V2_ARCHETYPES
    )


# ── Internal: websocket helpers (mirror website_pipeline) ────────────

async def _send(websocket: Any, kind: str, message: str) -> None:
    if websocket is None:
        return
    try:
        await websocket.send_json({"type": kind, "message": message})
    except Exception:
        pass


async def _phase(
    websocket: Any,
    phase: int,
    title: str,
    desc: str,
    status: str,
    *,
    mode: str = "admin_generation",
) -> None:
    """Emit a structured phase event.

    ``mode`` (Phase 2 Step 3): "new" for fresh admin-panel generation,
    "edit" for follow-up changes. Default "new" — admin pipeline is
    currently only invoked from new-project flows. The frontend reads
    this to choose mode-aware status copy without re-deriving from
    sessionStorage flags.
    """
    from app.services.agent_status import emit_task_phase
    await emit_task_phase(
        websocket,
        phase=phase,
        title=title,
        description=desc,
        status=status,
        mode=mode,
    )


# ── Stage 3 stub: minimal "visual DNA" for admins ────────────────────

def _build_admin_plan_data(
    *,
    brand_name: str,
    data_model: Any,
    admin_plan: dict[str, Any],
    visual_dna: dict[str, Any],
    admin_research: dict[str, Any] | None,
    intent: dict[str, Any],
) -> dict[str, Any]:
    """Assemble the plan_data dict the frontend's PlanBubble renders.

    Mirrors the shape produced by project_generator._plan_data so the
    same React component can render both website plans and admin plans
    without branching. Fields:
      • intro                 — bold-prefixed brand statement
      • description           — short summary including research sources
      • entities              — list of {name, desc, count} per table
      • pages_nested          — per-entity {name, route, sections} groups
      • design                — visual_dna summary line
      • requiresConfirmation  — False for admin (we don't have a gate yet)
    """
    category = (intent.get("business_category") or "internal tool").strip()
    tables = list(getattr(data_model, "tables", None) or [])
    entity_count = len(tables)
    from app.services.plan_extras import (
        compact_count,
        summarize_grounded_research,
        summary_chip,
    )
    research_meta = summarize_grounded_research(admin_research=admin_research)

    # Research summary line — mention the sources so the user sees
    # research actually happened. Falls back gracefully if research
    # was skipped or returned empty.
    research_summary = ""
    if admin_research:
        e_sources = int(admin_research.get("entity_sources") or 0)
        o_sources = int(admin_research.get("operations_sources") or 0)
        if e_sources or o_sources:
            research_summary = (
                f" Grounded in {e_sources + o_sources} web sources "
                f"from real {category} admin tools."
            )

    plural_entities = ", ".join(
        (t.plural_label or t.name) for t in tables[:6]
    ) if tables else "no entities yet"
    description = (
        f"A {category} admin dashboard managing **{entity_count}** "
        f"entit{'y' if entity_count == 1 else 'ies'}: "
        f"{plural_entities}.{research_summary}"
    )

    # Entity list — one row per table with field-count hint.
    entities_list: list[dict[str, str]] = []
    for t in tables:
        field_count = len(getattr(t, "fields", None) or [])
        entities_list.append({
            "name": t.plural_label or t.name,
            "desc": (
                (getattr(t, "description", "") or "").strip()
                or f"{field_count} fields · CRUD list/create/edit"
            )[:140],
        })

    # Pages_nested: group admin_plan pages by their entity. The admin_plan
    # emits 3 pages per entity (List/New/Edit) plus a Dashboard, Login,
    # Layout. We render entity groups + a standalone Dashboard group.
    pages_nested: list[dict[str, Any]] = []

    # Dashboard first
    pages_nested.append({
        "name":     "Dashboard",
        "route":    "/",
        "purpose":  f"Home overview with KPIs across all {entity_count} entities.",
        "sections": [
            {"type": "kpi_cards", "headline": "Top-line metrics"},
            {"type": "recent_activity", "headline": "Latest changes"},
        ],
    })

    # Then one group per entity
    for t in tables:
        slug = (t.name or "").replace("_", "-")
        label_plural = t.plural_label or t.name
        label_singular = t.singular_label or t.name
        pages_nested.append({
            "name":     label_plural,
            "route":    f"/{slug}",
            "purpose":  (getattr(t, "description", "") or "").strip()[:200],
            "sections": [
                {"type": "list_view",   "headline": f"All {label_plural}",
                 "details": "Searchable, sortable table with row actions"},
                {"type": "create_view", "headline": f"New {label_singular}",
                 "details": "Validated form for adding records"},
                {"type": "edit_view",   "headline": f"Edit {label_singular}",
                 "details": "Update an existing row, with delete action"},
            ],
        })

    # Design summary line
    voice = visual_dna.get("typography_voice", "professional")
    intensity = visual_dna.get("cultural_intensity", "calm")
    density = visual_dna.get("layout_density", "comfortable")
    color = visual_dna.get("primary_color", "")
    design_parts = [f"{voice} voice", f"{intensity} mood", f"{density} density"]
    if color:
        design_parts.insert(0, f"primary {color}")
    design_line = " · ".join(design_parts)

    return {
        "intro": (
            f"I'll build **{brand_name}** — a "
            f"**{category}** admin dashboard. Here's my plan:"
        ),
        "description": description,
        "planSummary": [
            summary_chip("Type", "Admin panel"),
            summary_chip("Scope", compact_count("", entity_count, "data model")),
            summary_chip("Research", f"{research_meta['confidence']} · {research_meta['sources']} sources"),
        ],
        "research": research_meta,
        "entities":    entities_list,
        "pages_nested": pages_nested,
        # Flat `pages` mirrors pages_nested for downstream code that
        # only reads the flat shape (history records, older renderers).
        "pages": [
            {"name": p["name"], "desc": p.get("purpose") or ""}
            for p in pages_nested
        ],
        "design": design_line,
        "buildSteps": [
            "Create auth, dashboard shell, navigation, and data helpers",
            "Generate list, create, and edit pages for every data model",
            "Validate generated CRUD pages and run the dashboard build when enabled",
        ],
        "assumptions": [
            "Operators need searchable CRUD screens before advanced analytics",
            "Data model names should use the terminology found in research",
        ],
        "requiresConfirmation": True,
    }


async def _resolve_admin_visual_dna(
    *,
    intent: dict[str, Any],
    purpose_data: dict[str, Any],
    parent_visual_dna: Optional[dict[str, Any]],
    gemini_key: str,
    admin_research: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Decide which visual_dna applies to this admin run.

    Linked admin (parent project's visual_dna is set): inherit it
    verbatim. The admin and its website share a single brand
    identity — the admin's UI is the website's logo + palette +
    voice, applied to a CRUD shell.

    Standalone admin (no parent, or parent has no visual_dna):
    extract a 6-field signal set via Gemini Flash. When
    `admin_research` is provided (output of `run_admin_entity_research`),
    the extractor uses the VISUAL_REFERENCES + INDUSTRY_TERMINOLOGY
    blocks to ground signals against real admin products in this
    space rather than generic "professional" defaults.

    Always returns a dict with at minimum brand_name + primary_color.
    On Gemini failure for standalone admins, returns the
    extractor's default fixture so downstream stages keep working.
    """
    if parent_visual_dna:
        logger.info(
            "_resolve_admin_visual_dna: inheriting parent visual_dna "
            "(keys=%d, intensity=%s)",
            len(parent_visual_dna),
            parent_visual_dna.get("cultural_intensity"),
        )
        return parent_visual_dna

    logger.info(
        "_resolve_admin_visual_dna: standalone admin — extracting own brand signals (research=%s)",
        "yes" if (admin_research and admin_research.get("operations_research")) else "no",
    )
    from app.services.admin_brand_extractor import extract_admin_brand_signals
    return await extract_admin_brand_signals(
        intent=intent,
        purpose_data=purpose_data,
        gemini_key=gemini_key,
        admin_research=admin_research,
    )


# ── Entry: run_admin_pipeline ────────────────────────────────────────

async def run_admin_pipeline(
    *,
    description: str,
    classification: dict[str, Any],
    workspace_path: str,
    validated: dict[str, Any],
    websocket: Any = None,
    chat_session_id: str = "",
) -> bool:
    """Run the admin panel pipeline. Returns True on success.

    Mirrors `run_website_pipeline`'s API so the project_generator
    dispatcher can route to either based on layout_archetype.
    """
    project_id = chat_session_id or "_session_none_"
    generation = GenerationResult(pipeline="admin")

    # ── Feature flag check ──────────────────────────────────────────
    if not _admin_pipeline_v2_enabled():
        logger.warning(
            "[%s] Admin pipeline SKIPPED: reason=ADMIN_PIPELINE_V2_ENABLED disabled",
            project_id,
        )
        return False

    logger.info(
        "[%s] Admin pipeline ENTRY: workspace=%s, archetype=%s",
        project_id, workspace_path,
        (classification or {}).get("layout_archetype"),
    )

    gemini_key = (
        (validated or {}).get("gemini_api_key")
        or os.environ.get("GOOGLE_API_KEY", "")
    )

    # Persist product_type=admin as the very first chat_sessions mutation
    # so the row reflects "this is an admin project" even if a later
    # stage fails. Best-effort; a real UUID is required.
    from app.services.pipeline_tenant import UUID_RE
    if UUID_RE.match(project_id):
        try:
            from app.supabase_client import managed_admin_client
            async with managed_admin_client() as admin:
                await (
                    admin.table("chat_sessions")
                    .update({"product_type": "admin"})
                    .eq("id", project_id)
                    .execute()
                )
        except Exception as exc:
            logger.warning(
                "[%s] product_type=admin write failed (non-fatal) — %s",
                project_id, exc,
            )

    # ── Stage 0.5: Purpose classification ───────────────────────────
    logger.info("[%s] Admin Stage 0.5 ENTRY: purpose classification", project_id)
    # Phase 1 title kept in sync with orchestrator.py — see landing_pipeline.py
    # for the rationale (Phase 1 + Phase 2 used to both say "Preparing workspace").
    await _phase(websocket, 1, "Validating inputs", "Inputs validated", "done")
    await _send(websocket, "progress", "Understanding what you want to build…")

    from app.services.purpose_classifier import classify_purpose
    from knowledge.loader import extract_clarify_context

    clarity_answers, clean_description = extract_clarify_context(description)

    try:
        purpose_data = await classify_purpose(
            user_prompt=clean_description,
            clarity_answers=clarity_answers or {},
            gemini_key=gemini_key,
        )
    except Exception as exc:
        logger.error(
            "[%s] Admin Stage 0.5 FAILED — %s", project_id, exc, exc_info=True,
        )
        await _send(websocket, "error", "Couldn't understand your request — please try a more specific description.")
        return False

    logger.info(
        "[%s] Admin Stage 0.5 COMPLETE: purpose=%s (%d%%), industry=%r",
        project_id, purpose_data["primary_purpose"],
        purpose_data["confidence"], purpose_data["industry"],
    )

    # ── Stage 1: Intent analysis ────────────────────────────────────
    logger.info("[%s] Admin Stage 1 ENTRY: intent analysis", project_id)
    await _send(websocket, "progress", "Studying your requirements…")

    from app.services.landing_intent import analyze_intent
    try:
        intent = await analyze_intent(
            clean_description, classification,
            websocket=websocket, timeout_s=60.0,
        )
    except Exception as exc:
        logger.error(
            "[%s] Admin Stage 1 FAILED — %s", project_id, exc, exc_info=True,
        )
        await _send(websocket, "error", "Couldn't read your request — please try again.")
        return False

    logger.info(
        "[%s] Admin Stage 1 COMPLETE: category=%s personality=%s",
        project_id, intent.get("business_category"),
        intent.get("brand_personality"),
    )

    # ── Stage 2: Grounded research (entities + operations) ──────────
    # Two parallel Gemini Pro + google_search calls that produce
    # industry-grounded signals for downstream stages:
    #   • entity_research     → vocab + status enums + relationships
    #                            (consumed by admin_data_model_planner)
    #   • operations_research → KPIs + workflows + visual references
    #                            (consumed by admin_brand_extractor +
    #                             admin_plan dashboard surface)
    # Cached per-project via pipeline_cache so re-runs are free.
    # Skippable via ADMIN_RESEARCH_ENABLED=false for cheap dry runs.
    await _send(websocket, "progress", "Researching your industry…")

    from app.services.admin_entity_research import run_admin_entity_research
    from app.services.pipeline_cache import pipeline_cache
    from app.services.research_quality import (
        admin_research_is_strong,
        admin_research_score,
    )

    admin_research: dict[str, Any] = {
        "entity_research": "", "operations_research": "",
        "entity_sources": 0, "operations_sources": 0, "all_urls": [],
    }
    research_enabled = (
        os.environ.get("ADMIN_RESEARCH_ENABLED", "true").strip().lower()
        in ("1", "true", "yes")
    )
    if not research_enabled:
        logger.info(
            "[%s] Admin Stage 2 SKIPPED: reason=ADMIN_RESEARCH_ENABLED=false",
            project_id,
        )
    else:
        cached_research = pipeline_cache.get(
            project_id, "admin_research", intent, purpose_data,
        )
        if cached_research is not None:
            admin_research = cached_research
            logger.info(
                "[%s] Admin Stage 2 CACHE HIT: entity=%d chars operations=%d chars",
                project_id,
                len(admin_research.get("entity_research") or ""),
                len(admin_research.get("operations_research") or ""),
            )
        else:
            try:
                admin_research = await run_admin_entity_research(
                    intent,
                    purpose_data=purpose_data,
                    timeout_s=120.0,
                    websocket=websocket,
                )
                pipeline_cache.set(
                    project_id, "admin_research", admin_research,
                    intent, purpose_data,
                )
                logger.info(
                    "[%s] Admin Stage 2 COMPLETE: entity=%d chars (%d sources), "
                    "operations=%d chars (%d sources)",
                    project_id,
                    len(admin_research["entity_research"]),
                    admin_research["entity_sources"],
                    len(admin_research["operations_research"]),
                    admin_research["operations_sources"],
                )
            except Exception as exc:
                # Non-fatal: research is enrichment, not gate. Empty
                # research means downstream falls back to the original
                # planner-only behavior.
                logger.warning(
                    "[%s] Admin Stage 2 FAILED (non-fatal): %s — continuing without research",
                    project_id, exc, exc_info=True,
                )
        if not admin_research_is_strong(admin_research):
            old_score = admin_research_score(admin_research)
            await _send(websocket, "progress", "Improving dashboard research coverage…")
            try:
                retried_research = await run_admin_entity_research(
                    intent,
                    purpose_data=purpose_data,
                    timeout_s=120.0,
                    websocket=websocket,
                )
                if admin_research_score(retried_research) >= old_score:
                    admin_research = retried_research
                    pipeline_cache.set(
                        project_id, "admin_research", admin_research,
                        intent, purpose_data,
                    )
                    logger.info(
                        "[%s] Admin Stage 2 retry accepted: entity_sources=%d operations_sources=%d",
                        project_id,
                        int(admin_research.get("entity_sources") or 0),
                        int(admin_research.get("operations_sources") or 0),
                    )
                else:
                    logger.warning(
                        "[%s] Admin Stage 2 retry did not improve research score",
                        project_id,
                    )
            except Exception as exc:
                logger.warning(
                    "[%s] Admin Stage 2 retry failed (non-fatal): %s",
                    project_id, exc,
                )
        try:
            from app.services.plan_extras import summarize_grounded_research
            _research_meta = summarize_grounded_research(admin_research=admin_research)
            await _send(
                websocket,
                "progress",
                f"Research ready — {_research_meta['confidence'].lower()} confidence, "
                f"{_research_meta['sources']} sources.",
            )
        except Exception:
            pass

    # ── Linked-admin detection (early) ──────────────────────────────
    # We need to know is_linked BEFORE Stage 3 so that visual_dna can
    # inherit the parent's identity verbatim instead of paying for a
    # standalone brand extraction. The downstream Stages 4.5/4.6/4.7
    # also branch on is_linked, but the parent fetch is idempotent and
    # this single early read covers all of them.
    from app.supabase_client import managed_admin_client
    from app.services.pipeline_tenant import (
        resolve_tenant_for_project,
        provision_tenant_for_project,
        seed_tenant_for_project,
    )

    is_linked = False
    linked_data_model = None
    linked_tenant_schema: Optional[str] = None
    linked_visual_dna: Optional[dict] = None
    if UUID_RE.match(project_id):
        try:
            async with managed_admin_client() as admin:
                row_res = await (
                    admin.table("chat_sessions")
                    .select("parent_project_id")
                    .eq("id", project_id)
                    .limit(1)
                    .execute()
                )
                rows = row_res.data or []
                parent_id = rows[0].get("parent_project_id") if rows else None
                if parent_id:
                    is_linked = True
                    resolved = await resolve_tenant_for_project(project_id, admin)
                    if not resolved:
                        logger.error(
                            "[%s] Admin linked-resolution FAILED: parent "
                            "(%s) has no tenant_schema or data_model",
                            project_id, parent_id,
                        )
                        await _send(
                            websocket, "error",
                            "The linked website isn't ready yet — please try again in a moment.",
                        )
                        return False
                    linked_tenant_schema, linked_data_model, linked_visual_dna = resolved
                    logger.info(
                        "[%s] Admin linked-resolution COMPLETE: "
                        "parent=%s tenant_schema=%s tables=%d visual_dna=%s",
                        project_id, parent_id, linked_tenant_schema,
                        len(linked_data_model.tables),
                        "inherited" if linked_visual_dna else "none",
                    )
        except Exception as exc:
            logger.error(
                "[%s] Admin linked-resolution threw — %s",
                project_id, exc, exc_info=True,
            )
            await _send(
                websocket, "error",
                "Couldn't find the linked website.",
            )
            return False

    # ── Stage 3: Visual DNA ─────────────────────────────────────────
    # Linked admins inherit the parent's full visual_dna (palette,
    # typography_voice, cultural_intensity, decorative_motifs,
    # section_anatomies — though admin prompts only consume the first 4).
    # Standalone admins get a 6-field Flash extraction (~$0.005).
    logger.info("[%s] Admin Stage 3 ENTRY: visual DNA", project_id)
    visual_dna = await _resolve_admin_visual_dna(
        intent=intent,
        purpose_data=purpose_data,
        parent_visual_dna=linked_visual_dna,
        gemini_key=gemini_key,
        admin_research=admin_research,
    )
    logger.info(
        "[%s] Admin Stage 3 COMPLETE: brand=%r primary_color=%s "
        "voice=%s intensity=%s density=%s (linked=%s)",
        project_id,
        visual_dna.get("brand_name"),
        visual_dna.get("primary_color"),
        visual_dna.get("typography_voice"),
        visual_dna.get("cultural_intensity"),
        visual_dna.get("layout_density"),
        is_linked,
    )

    # Persist standalone admin's visual_dna so re-runs and downstream
    # queries see the same identity. Skip for linked admins — they
    # already inherit from the parent's persisted row.
    if visual_dna and not is_linked and UUID_RE.match(project_id):
        try:
            async with managed_admin_client() as _admin:
                await (
                    _admin.table("chat_sessions")
                    .update({"visual_dna": visual_dna})
                    .eq("id", project_id)
                    .execute()
                )
            logger.info(
                "[%s] Admin Stage 3: persisted visual_dna (%d keys)",
                project_id, len(visual_dna),
            )
        except Exception as exc:
            logger.warning(
                "[%s] Admin Stage 3: visual_dna persist failed (non-fatal) — %s",
                project_id, exc,
            )

    # ── Stage 4: Plan — derived from data_model after 4.5 ───────────
    # The website pipeline calls build_website_plan BEFORE the data
    # model planner. For admins we flip the order: the data model is
    # the source of truth, and "pages" are just entity routes derived
    # from it. We build the plan AFTER Stage 4.5 below.

    # ── Stage 4.5: Data model (admin-aware) ─────────────────────────
    # Standalone admins call the admin-specific planner (Step 3.4),
    # which asks Gemini "what entities does this user manage?" rather
    # than the website planner's "what content does this site display?".
    # Linked admins inherit the parent's data_model verbatim — the
    # admin-specific planner short-circuits when parent_data_model is
    # passed.
    logger.info("[%s] Admin Stage 4.5 ENTRY: admin data model planner", project_id)
    await _send(
        websocket, "progress",
        "Designing your data structure…",
    )
    from app.services.admin_data_model_planner import plan_admin_data_model

    try:
        data_model = await plan_admin_data_model(
            intent=intent,
            purpose_data=purpose_data,
            gemini_key=gemini_key,
            parent_data_model=linked_data_model,  # None for standalone
            project_id=project_id,
            admin_research=admin_research,
        )
    except Exception as exc:
        logger.error(
            "[%s] Admin Stage 4.5 FAILED — %s",
            project_id, exc, exc_info=True,
        )
        await _send(
            websocket, "error",
            "Couldn't design your data — please try a more specific description.",
        )
        return False

    if not data_model or not data_model.tables:
        logger.error(
            "[%s] Admin Stage 4.5 produced no tables — admin pipeline "
            "cannot continue",
            project_id,
        )
        await _send(
            websocket, "error",
            "Couldn't figure out what to manage — try describing it differently.",
        )
        return False

    logger.info(
        "[%s] Admin Stage 4.5 COMPLETE: tables=%d singletons=%d "
        "(linked=%s)",
        project_id, len(data_model.tables), len(data_model.singletons),
        is_linked,
    )
    await _send(
        websocket, "progress",
        f"Will manage: {', '.join(t.name for t in data_model.tables)}",
    )

    # ── Stage 4 (deferred): build admin plan from data_model ────────
    # Deterministic — no LLM. For each table emit list/new/edit pages,
    # plus the shared login/layout/dashboard trio.
    from app.services.admin_plan import build_admin_plan
    plan = build_admin_plan(data_model, visual_dna)
    logger.info(
        "[%s] Admin Stage 4 COMPLETE: brand=%r pages=%d nav=%d",
        project_id, plan["brand"]["name"],
        len(plan["pages"]), len(plan["navigation"]),
    )

    # ── Stage 4 UI: emit the plan card so the user sees what we'll build ─
    # Same shape as the website pipeline's plan card — frontend's
    # PlanBubble renders the same component for both. Best-effort emit;
    # the confirmation gate below ONLY engages when the emit succeeded.
    _plan_emitted_ok = False
    try:
        plan_data = _build_admin_plan_data(
            brand_name=visual_dna.get("brand_name", "Admin"),
            data_model=data_model,
            admin_plan=plan,
            visual_dna=visual_dna,
            admin_research=admin_research,
            intent=intent,
        )
        await websocket.send_json({
            "type": "chat_message",
            "role": "agent",
            "messageType": "plan",
            "planData": plan_data,
        })
        _plan_emitted_ok = True
        logger.info(
            "[%s] Admin Stage 4 UI: plan card emitted (entities=%d pages_nested=%d)",
            project_id, len(plan_data.get("entities", [])),
            len(plan_data.get("pages_nested", [])),
        )
        # Persist so a ws reconnect during the confirmation window can
        # re-emit the same envelope — mirrors website pipeline behavior.
        try:
            from app.services.project_generator import save_persisted_plan
            await save_persisted_plan(project_id, plan_data, task=description)
        except Exception as _persist_exc:
            logger.warning(
                "[%s] Admin Stage 4 UI: plan persist failed (non-fatal) — %s",
                project_id, _persist_exc,
            )
    except Exception as _plan_emit_exc:
        logger.warning(
            "[%s] Admin Stage 4 UI: plan card emit failed (non-fatal) — %s",
            project_id, _plan_emit_exc, exc_info=True,
        )

    # ── Stage 4 Gate: wait for user to confirm or reject the plan ──
    # Mirrors project_generator's pattern. Uses the same confirmation
    # registry so ws.py routes the user's button click here. Gate runs
    # ONLY when the plan card actually went out — otherwise we'd block
    # forever on a future the user can never resolve.
    if _plan_emitted_ok:
        from app.services.project_generator import (
            _confirmation_key,
            register_plan_confirmation,
            clear_persisted_plan,
            PLAN_CONFIRM_TIMEOUT_SECONDS,
            pending_plan_confirmations,
        )

        _gate_key = _confirmation_key(websocket, project_id)
        try:
            await websocket.send_json({
                "type": "plan_awaiting_confirmation",
                "message": "Review your plan above and click 'Looks Good' to start building.",
            })
        except Exception as _await_send_err:
            logger.warning(
                "[%s] Admin Stage 4 Gate: plan_awaiting_confirmation send failed — %s",
                project_id, _await_send_err,
            )

        _plan_future = register_plan_confirmation(_gate_key)
        try:
            _confirmation = await asyncio.wait_for(
                _plan_future, timeout=PLAN_CONFIRM_TIMEOUT_SECONDS,
            )
            if not _confirmation.get("confirmed", True):
                _correction = _confirmation.get("correction", "")
                if _correction:
                    await _send(
                        websocket, "progress",
                        f"🔄 Got it — adjusting: {_correction[:60]}…",
                    )
                    logger.info(
                        "[%s] Admin plan rejected — correction=%r",
                        project_id, _correction[:120],
                    )
                    try:
                        setattr(websocket, "_plan_correction", _correction)
                    except Exception:
                        pass
                    await clear_persisted_plan(project_id)
                    return False
                logger.info(
                    "[%s] Admin plan rejected without correction — aborting",
                    project_id,
                )
                await _send(
                    websocket, "warning",
                    "❌ Plan rejected — generation aborted.",
                )
                await clear_persisted_plan(project_id)
                return False
            logger.info(
                "[%s] Admin plan confirmed by user — proceeding to provisioning",
                project_id,
            )
            await _send(
                websocket, "progress",
                "✅ Plan confirmed — building your dashboard…",
            )
            await clear_persisted_plan(project_id)
        except asyncio.TimeoutError:
            logger.info(
                "[%s] Admin plan confirmation timed out after %ds — aborting",
                project_id, PLAN_CONFIRM_TIMEOUT_SECONDS,
            )
            pending_plan_confirmations.pop(_gate_key, None)
            await clear_persisted_plan(project_id)
            try:
                await _send(
                    websocket, "warning",
                    "⏱️ Plan expired after 30 minutes — send your message again "
                    "to rebuild (research is cached, so it'll be quick).",
                )
            except Exception:
                pass
            return False
        except Exception as _conf_err:
            logger.warning(
                "[%s] Admin plan confirmation aborted: %s",
                project_id, _conf_err, exc_info=True,
            )
            pending_plan_confirmations.pop(_gate_key, None)
            await clear_persisted_plan(project_id)
            try:
                await _send(
                    websocket, "warning",
                    "❌ Plan confirmation failed — generation aborted. Please retry.",
                )
            except Exception:
                pass
            return False
    else:
        logger.warning(
            "[%s] Admin Stage 4 Gate: skipping — plan was never emitted",
            project_id,
        )

    # ── Stage 4.6: Tenant provisioning ──────────────────────────────
    # Standalone: provision via Phase 2 helper. Linked: already done
    # above (linked_tenant_schema is set; nothing to do here).
    tenant_schema = linked_tenant_schema
    if not is_linked:
        tenant_schema = await provision_tenant_for_project(
            data_model=data_model,
            project_id=project_id,
            websocket=websocket,
        )
        if not tenant_schema:
            # Provisioning is non-fatal at the Phase 2 layer, but for
            # the admin we treat a missing tenant as a hard failure —
            # there's no useful admin without a backend to manage.
            logger.error(
                "[%s] Admin Stage 4.6 FAILED: tenant_schema is None",
                project_id,
            )
            await _send(
                websocket, "error",
                "Couldn't set up your database — please try again.",
            )
            return False
        logger.info(
            "[%s] Admin Stage 4.6 COMPLETE (standalone): tenant_schema=%s",
            project_id, tenant_schema,
        )

    # ── Stage 4.7: Seed data ────────────────────────────────────────
    # Standalone: seed via Phase 2 helper. Linked: parent already
    # seeded these tables; re-running would duplicate rows.
    if not is_linked:
        await seed_tenant_for_project(
            data_model=data_model,
            tenant_schema=tenant_schema,
            website_plan=plan,
            intent=intent,
            purpose_data=purpose_data,
            gemini_key=gemini_key,
            project_id=project_id,
            websocket=websocket,
        )
        logger.info("[%s] Admin Stage 4.7 COMPLETE (standalone)", project_id)
    else:
        logger.info(
            "[%s] Admin Stage 4.7 SKIPPED: reason=linked_admin_reuses_parent_seed",
            project_id,
        )

    # ── Stage 5: Foundation files (Step 3.5) ────────────────────────
    # Deterministic React/Vite file emission — package.json, Vite
    # config, auth, lib, components, dashboard, and stub CRUD pages.
    # No LLM here. Step 3.6 will replace the stub CRUD pages with
    # real list / create / edit bodies via Claude.
    logger.info("[%s] Admin Stage 5 ENTRY: foundation builder", project_id)
    await _send(
        websocket, "progress",
        "Setting up your dashboard…",
    )
    from app.services.admin_foundation_builder import build_admin_foundation
    from app.config import settings

    try:
        foundation_result = build_admin_foundation(
            workspace_path=workspace_path,
            data_model=data_model,
            admin_plan=plan,
            tenant_schema=tenant_schema or "",
            project_id=project_id,
            supabase_url=settings.SUPABASE_URL,
            supabase_anon_key=settings.SUPABASE_ANON_KEY,
        )
    except Exception as exc:
        logger.error(
            "[%s] Admin Stage 5 FAILED — %s",
            project_id, exc, exc_info=True,
        )
        await _send(
            websocket, "error",
            "Couldn't build the project files — please try again.",
        )
        return False

    logger.info(
        "[%s] Admin Stage 5 COMPLETE: %d files written "
        "(seeded=%d empty=%d)",
        project_id,
        len(foundation_result["files_written"]),
        len(foundation_result["tables_with_seed_data"]),
        len(foundation_result["tables_empty"]),
    )
    generation.add_files(foundation_result.get("files_written") or [])
    await _send(
        websocket, "progress",
        "Login and dashboard ready.",
    )

    # ── Stage 6: CRUD codegen ───────────────────────────────────────
    # Per-entity Claude codegen for list / create / edit pages.
    # Default is real codegen when an Anthropic key exists; otherwise we
    # fall back to self-explanatory placeholders that honour the AuthGuard
    # + db_admin contract. ADMIN_CODEGEN_MOCK explicitly overrides this.
    from app.services.admin_codegen import (
        _EST_PAGE_COST_USD,
        generate_one_admin_page,
        pages_for_entity,
    )

    anthropic_key = (
        (validated or {}).get("anthropic_api_key")
        or os.environ.get("ANTHROPIC_API_KEY", "")
    )
    mock_raw = os.environ.get("ADMIN_CODEGEN_MOCK")
    if mock_raw is None or not mock_raw.strip():
        mock_codegen = not bool(str(anthropic_key).strip())
    else:
        mock_codegen = mock_raw.strip().lower() in ("1", "true", "yes", "on")
    logger.info(
        "[%s] Admin Stage 6 ENTRY: generating CRUD for %d entities (mock=%s)",
        project_id, len(data_model.tables), mock_codegen,
    )
    if mock_codegen:
        logger.warning(
            "[%s] Admin Stage 6 in MOCK mode — set ADMIN_CODEGEN_MOCK=false "
            "and provide a live ANTHROPIC_API_KEY to force real Claude codegen.",
            project_id,
        )
    await _send(
        websocket, "progress",
        "Generating pages…",
    )

    if not mock_codegen and not str(anthropic_key).strip():
        logger.error(
            "[%s] Admin Stage 6 FAILED: ADMIN_CODEGEN_MOCK=false but no "
            "Anthropic API key is available",
            project_id,
        )
        await _send(
            websocket, "error",
            "Real dashboard coding is enabled, but no Anthropic key is available.",
        )
        return False

    # ── Flat N×3 parallel codegen ────────────────────────────────────
    # Mirrors website_orchestrator's pattern: one Semaphore, one
    # asyncio.gather, all (entity, page_type) pairs in flight under a
    # bounded concurrency. Lets users with N=4 entities finish in ~one
    # page's wall time instead of N×3.
    # Default lowered 8→4 after the website pipeline hit the same Anthropic
    # TPM ceiling at concurrency=8 (empty streams, no tool_use output).
    # Admin runs 3 pages per entity × N entities → at 5+ entities the parallel
    # count balloons fast, so cap the per-batch in-flight count at 4.
    try:
        concurrency = int(os.environ.get("ADMIN_CODEGEN_CONCURRENCY", "4"))
    except ValueError:
        concurrency = 4
    sem = asyncio.Semaphore(max(1, concurrency))

    # Track per-entity remaining count so we can emit "<entity> pages
    # ready" the moment ALL 3 pages for an entity have landed —
    # entities complete in arbitrary order under parallel execution.
    by_entity = {t.name: t for t in data_model.tables}
    remaining = {t.name: 3 for t in data_model.tables}

    # Per-page completion narration so the FE's narrator advances on
    # every finished CRUD page, not just on the final aggregate.
    from app.services.agent_status import emit_agent_status as _emit_admin_status
    _admin_completed = 0
    _admin_lock = asyncio.Lock()
    _admin_total = len(data_model.tables) * 3

    async def _bounded_page(
        entity: TableDefinition,
        page_type: str,
        rel_path: str,
        prompt: dict,
    ) -> dict[str, Any]:
        nonlocal _admin_completed
        async with sem:
            page_result = await generate_one_admin_page(
                entity=entity,
                page_type=page_type,
                rel_path=rel_path,
                prompt=prompt,
                admin_plan=plan,
                workspace_path=workspace_path,
                anthropic_key=anthropic_key,
                mock=mock_codegen,
            )
        # Smooth chat motion — emit per-entity progress when an
        # entity's third page completes (any order is fine).
        remaining[entity.name] -= 1
        if remaining[entity.name] == 0:
            label = entity.plural_label or entity.name
            await _send(
                websocket, "progress",
                f"{label} pages ready.",
            )
        async with _admin_lock:
            _admin_completed += 1
            done_now = _admin_completed
        await _emit_admin_status(
            websocket,
            key="writing_admin_pages",
            label=f"Writing admin pages… {done_now}/{_admin_total}",
            description=f"{entity.plural_label or entity.name} · {page_type}",
            state="active",
            source="admin_pipeline",
        )
        return page_result

    all_tasks = []
    planned_per_entity: dict[str, int] = {name: 0 for name in by_entity}
    for entity in data_model.tables:
        for page_type, rel_path, prompt in pages_for_entity(entity, plan):
            all_tasks.append(_bounded_page(entity, page_type, rel_path, prompt))
            planned_per_entity[entity.name] += 1

    logger.info(
        "[%s] Admin Stage 6: launching %d parallel calls (concurrency=%d)",
        project_id, len(all_tasks), concurrency,
    )
    await _emit_admin_status(
        websocket,
        key="writing_admin_pages",
        label=f"Writing admin pages… 0/{_admin_total}",
        description=f"Generating CRUD pages for {len(data_model.tables)} entities",
        state="active",
        source="admin_pipeline",
    )
    page_results = await asyncio.gather(*all_tasks, return_exceptions=True)

    # Group page results back per-entity for the post-stage gate.
    codegen_results: dict[str, dict[str, Any]] = {
        name: {"files_written": [], "validation_errors": [], "page_cost": 0.0}
        for name in by_entity
    }
    for r in page_results:
        if isinstance(r, Exception):
            logger.error(
                "[%s] Admin Stage 6 task raised — %s",
                project_id, r, exc_info=r,
            )
            continue
        bucket = codegen_results[r["entity"]]
        bucket["page_cost"] += r["page_cost"]
        if r["written"]:
            bucket["files_written"].append(r["rel_path"])
        if r["issues"]:
            bucket["validation_errors"].append(
                {"file": r["rel_path"], "issues": r["issues"]}
            )

    total_actual = sum(b["page_cost"]                    for b in codegen_results.values())
    total_est    = len(all_tasks) * _EST_PAGE_COST_USD
    total_files  = sum(len(b["files_written"])           for b in codegen_results.values())
    total_errors = sum(len(b["validation_errors"])       for b in codegen_results.values())
    for bucket in codegen_results.values():
        generation.add_files(bucket["files_written"])
    generation.metadata.update({
        "crud_files_written": total_files,
        "crud_validation_errors": total_errors,
        "mock_codegen": mock_codegen,
    })
    logger.info(
        "[%s] Admin Stage 6 COMPLETE: files=%d, validator_errors=%d, "
        "cost=$%.4f (est $%.4f, mock=%s)",
        project_id, total_files, total_errors,
        total_actual, total_est, mock_codegen,
    )
    # ── Artifact-coverage audit (per-entity) ────────────────────────
    # The aggregate file count below can mask per-entity gaps (entity A
    # writing 4 files while entity B wrote 2 still sums to "complete").
    # Audit each entity against ITS planned page count and report the
    # incomplete ones by name — structured event + chat warning + metadata.
    def _entity_label(name: str) -> str:
        ent = by_entity.get(name)
        return str(getattr(ent, "plural_label", "") or getattr(ent, "name", "") or name)

    incomplete_entities = [
        name for name, bucket in codegen_results.items()
        if len(bucket["files_written"]) < planned_per_entity.get(name, 0)
    ]
    try:
        from app.services.generation_audit import report_artifact_coverage
        await report_artifact_coverage(
            pipeline="admin",
            websocket=websocket,
            planned=[_entity_label(n) for n in codegen_results],
            missing=[_entity_label(n) for n in incomplete_entities],
            artifact_label="entity",
            generation=generation,
            recovery_hint=(
                "Ask me to finish the incomplete sections and I'll regenerate just those."
            ),
        )
    except Exception as exc:
        logger.warning(
            "[%s] Admin artifact audit failed (non-fatal): %s", project_id, exc,
        )

    expected_files = len(data_model.tables) * 3
    if not mock_codegen and (total_files < expected_files or total_errors > 0):
        _missing_names = ", ".join(_entity_label(n) for n in incomplete_entities[:8])
        logger.error(
            "[%s] Admin Stage 6 FAILED: files=%d/%d validator_errors=%d incomplete=%s",
            project_id, total_files, expected_files, total_errors,
            incomplete_entities,
        )
        await _send(
            websocket, "error",
            (
                f"The dashboard pages for {_missing_names} did not generate cleanly. "
                "Please retry when credits are available."
                if _missing_names else
                "The dashboard pages did not pass code validation. "
                "Please retry when credits are available."
            ),
        )
        return False

    # ── Stage 7: Build verification ─────────────────────────────────
    # Real codegen gets a no-token Vite build by default. Mock mode
    # skips by default so unit tests and cheap dry runs stay fast, but
    # operators can force it with ADMIN_BUILD_VALIDATE=true.
    build_default = "false" if mock_codegen else "true"
    build_validate = (
        os.environ.get("ADMIN_BUILD_VALIDATE", build_default).strip().lower()
        in ("1", "true", "yes", "on")
    )
    if build_validate:
        try:
            retries = int(os.environ.get("ADMIN_BUILD_FIX_RETRIES", "0"))
        except ValueError:
            retries = 0
        build_result = await run_generation_build_check(
            pipeline="admin_pipeline",
            workspace_path=workspace_path,
            api_key=str(anthropic_key or ""),
            classification={
                **(classification or {}),
                "project_stack": "admin-react",
                "model_id": (classification or {}).get("model_id", "claude-sonnet-4-6"),
            },
            websocket=websocket,
            max_retries=max(0, retries),
            send=lambda kind, message: _send(websocket, kind, message),
            progress_message="Checking dashboard build...",
            failure_message="",
            generation=generation,
        )
        if not build_result.get("success"):
            logger.error(
                "[%s] Admin Stage 7 FAILED: %s",
                project_id, build_result.get("errors", "")[:1000],
            )
            await _send(
                websocket, "error",
                "The dashboard code was generated, but the Vite build failed.",
            )
            return False
        logger.info("[%s] Admin Stage 7 COMPLETE: Vite build passed", project_id)
    else:
        logger.info(
            "[%s] Admin Stage 7 SKIPPED: mock=%s ADMIN_BUILD_VALIDATE=%s",
            project_id, mock_codegen, os.environ.get("ADMIN_BUILD_VALIDATE"),
        )

    await _send(
        websocket, "progress",
        "Your dashboard is ready!",
    )
    generation.ok = True
    try:
        setattr(websocket, "_generation_result", generation.as_dict())
    except Exception:
        pass
    return True
