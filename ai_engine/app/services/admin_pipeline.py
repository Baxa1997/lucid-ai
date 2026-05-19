"""Admin panel generation pipeline. Mirrors website_pipeline.py for
consistency.

Stages:
  0.5  Purpose classification    (Gemini)
  1    Intent analysis           (Gemini)
  2    Domain research           (Gemini, lighter than website —
                                  focused on entity discovery)
  3    Visual DNA / branding     (Gemini Pro, lighter than website)
  4    Plan: entities + views    (Gemini Flash)
  4.5  Data model planning       (reuse Phase 2 — same DataModel
                                  shape works for admin)
  4.6  Tenant provisioning       (resolve linked OR provision new)
  4.7  Seed data                 (reuse Phase 2)
  5    Foundation files          (deterministic — Step 3.5 will fill)
  6    CRUD codegen              (Claude — Step 3.6 will fill)
  7    Build verification        (reuse from website)

This module is structurally complete but stages 5-6 are stubs in
Step 3.3. Stage 5 lands in Step 3.5, Stage 6 in Step 3.6.

Linking model (admin vs website):
  • Standalone admin (parent_project_id NULL) → provisions its own
    tenant_schema + seed data via Phase 2 helpers.
  • Linked admin (parent_project_id set) → reads tenant_schema +
    data_model from the parent via `resolve_tenant_for_project`,
    skips Stages 4.5/4.6/4.7 entirely (data already exists).

Failure model:
  Returns False on any hard failure so the caller can fall through
  to legacy admin generation. Failures inside Stages 4.6/4.7 are
  non-fatal (matches website_pipeline) — they degrade the generated
  admin to a placeholder but don't break the pipeline.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Optional

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
    """Gate for Step 3.3 admin pipeline. Default OFF.

    Set ADMIN_PIPELINE_V2_ENABLED=true to route admin_dashboard / crm /
    tms / saas_dashboard archetypes through this pipeline. While off,
    the caller falls through to legacy admin generation in
    project_generator.py.
    """
    raw = os.environ.get("ADMIN_PIPELINE_V2_ENABLED", "").strip().lower()
    return raw in ("1", "true", "yes")


def should_route_to_admin_pipeline(layout_archetype: str) -> bool:
    """Single source of truth for project_generator's dispatcher.

    Returns True iff the feature flag is on AND the archetype is in
    `ADMIN_V2_ARCHETYPES`. Tests assert against this helper directly
    so routing decisions stay in one place.
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


async def _phase(websocket: Any, phase: int, title: str, desc: str, status: str) -> None:
    if websocket is None:
        return
    try:
        await websocket.send_json({
            "type": "task_phase",
            "phase": phase, "title": title,
            "description": desc, "status": status,
        })
    except Exception:
        pass


# ── Stage 3 stub: minimal "visual DNA" for admins ────────────────────

async def _resolve_admin_visual_dna(
    *,
    intent: dict[str, Any],
    purpose_data: dict[str, Any],
    parent_visual_dna: Optional[dict[str, Any]],
    gemini_key: str,
) -> dict[str, Any]:
    """Decide which visual_dna applies to this admin run.

    Linked admin (parent project's visual_dna is set): inherit it
    verbatim. The admin and its website share a single brand
    identity — the admin's UI is the website's logo + palette +
    voice, applied to a CRUD shell.

    Standalone admin (no parent, or parent has no visual_dna):
    extract a 6-field signal set via Gemini Flash.

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
        "_resolve_admin_visual_dna: standalone admin — extracting own brand signals",
    )
    from app.services.admin_brand_extractor import extract_admin_brand_signals
    return await extract_admin_brand_signals(
        intent=intent,
        purpose_data=purpose_data,
        gemini_key=gemini_key,
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

    # ── Feature flag check ──────────────────────────────────────────
    if not _admin_pipeline_v2_enabled():
        logger.warning(
            "[%s] Admin pipeline SKIPPED: reason=ADMIN_PIPELINE_V2_ENABLED is off",
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
    await _phase(websocket, 1, "Preparing workspace", "Workspace ready", "done")
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
        intent = await analyze_intent(clean_description, classification, timeout_s=60.0)
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

    # ── Stage 2: Research (LIGHTER than website) ────────────────────
    # Step 3.3 stub: empty result. Step 3.4 may build an
    # admin_entity_research.py if entity discovery beyond what the
    # data_model planner already gives us turns out to matter.
    logger.warning(
        "[%s] Admin Stage 2 SKIPPED: reason=step_3_3_stub "
        "(admin_entity_research not yet implemented — using planner output as the only entity signal)",
        project_id,
    )

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
    await _send(
        websocket, "progress",
        "Login and dashboard ready.",
    )

    # ── Stage 6: CRUD codegen (Step 3.6 Part A — mock-default) ──────
    # Per-entity Claude codegen for list / create / edit pages.
    # Default is MOCK mode — writes self-explanatory placeholders that
    # honour the AuthGuard + db_admin contract. Set
    # ADMIN_CODEGEN_MOCK=false (with a valid ANTHROPIC_API_KEY) once
    # Part B is ready to spend credits.
    from app.services.admin_codegen import (
        _EST_PAGE_COST_USD,
        generate_one_admin_page,
        pages_for_entity,
    )

    mock_codegen = (
        os.environ.get("ADMIN_CODEGEN_MOCK", "true").strip().lower()
        in ("1", "true", "yes")
    )
    logger.info(
        "[%s] Admin Stage 6 ENTRY: generating CRUD for %d entities (mock=%s)",
        project_id, len(data_model.tables), mock_codegen,
    )
    if mock_codegen:
        logger.warning(
            "[%s] Admin Stage 6 in MOCK mode — set ADMIN_CODEGEN_MOCK=false "
            "with a live ANTHROPIC_API_KEY to run real Claude codegen.",
            project_id,
        )
    await _send(
        websocket, "progress",
        "Generating pages…",
    )

    anthropic_key = (
        (validated or {}).get("anthropic_api_key")
        or os.environ.get("ANTHROPIC_API_KEY", "")
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
    try:
        concurrency = int(os.environ.get("ADMIN_CODEGEN_CONCURRENCY", "8"))
    except ValueError:
        concurrency = 8
    sem = asyncio.Semaphore(max(1, concurrency))

    # Track per-entity remaining count so we can emit "<entity> pages
    # ready" the moment ALL 3 pages for an entity have landed —
    # entities complete in arbitrary order under parallel execution.
    by_entity = {t.name: t for t in data_model.tables}
    remaining = {t.name: 3 for t in data_model.tables}

    async def _bounded_page(
        entity: TableDefinition,
        page_type: str,
        rel_path: str,
        prompt: dict,
    ) -> dict[str, Any]:
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
        return page_result

    all_tasks = []
    for entity in data_model.tables:
        for page_type, rel_path, prompt in pages_for_entity(entity, plan):
            all_tasks.append(_bounded_page(entity, page_type, rel_path, prompt))

    logger.info(
        "[%s] Admin Stage 6: launching %d parallel calls (concurrency=%d)",
        project_id, len(all_tasks), concurrency,
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
    logger.info(
        "[%s] Admin Stage 6 COMPLETE: files=%d, validator_errors=%d, "
        "cost=$%.4f (est $%.4f, mock=%s)",
        project_id, total_files, total_errors,
        total_actual, total_est, mock_codegen,
    )
    expected_files = len(data_model.tables) * 3
    if not mock_codegen and (total_files < expected_files or total_errors > 0):
        logger.error(
            "[%s] Admin Stage 6 FAILED: files=%d/%d validator_errors=%d",
            project_id, total_files, expected_files, total_errors,
        )
        await _send(
            websocket, "error",
            "The dashboard pages did not pass code validation. Please retry when credits are available.",
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
        await _send(websocket, "progress", "Checking dashboard build…")
        from app.services.build_validator import BuildValidator

        try:
            retries = int(os.environ.get("ADMIN_BUILD_FIX_RETRIES", "0"))
        except ValueError:
            retries = 0
        validator = BuildValidator(
            api_key=str(anthropic_key or ""),
            classification={
                **(classification or {}),
                "project_stack": "admin-react",
                "model_id": (classification or {}).get("model_id", "claude-sonnet-4-6"),
            },
            websocket=websocket,
            max_retries=max(0, retries),
        )
        build_result = await validator.validate_and_fix(workspace_path)
        try:
            setattr(websocket, "_build_ok", bool(build_result.get("success")))
        except Exception:
            pass
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
    return True
