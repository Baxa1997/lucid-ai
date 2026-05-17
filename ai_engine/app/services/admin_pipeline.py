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


# ── Defaults ─────────────────────────────────────────────────────────
# Stage 3 stubs to these until we build admin-specific visual research
# in Step 3.4. Slate-900 is a neutral dark default that works for most
# internal tools — admins lean toward calm/professional over branded.
_DEFAULT_PRIMARY_COLOR = "#0f172a"  # slate-900


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

def _stub_visual_dna_for_admin(intent: dict[str, Any]) -> dict[str, Any]:
    """Step 3.3 placeholder for full visual_dna extraction.

    Admin panels need only brand_name + primary_color + (optional)
    logo_url to drive layout — Step 3.4 will swap this for real
    research if differentiation matters. For now we synthesize from
    intent so downstream stages keep getting the dict shape they
    expect.
    """
    brand_name = (
        (intent.get("brand") or {}).get("name")
        or intent.get("business_category")
        or "Admin"
    )
    return {
        "brand_name":    brand_name,
        "primary_color": _DEFAULT_PRIMARY_COLOR,
        "logo_url":      None,
        # Kept for plumbing compatibility with website_plan, which
        # reads cultural_intensity + layout_signature. Admin defaults
        # match shadcn/ui's neutral look.
        "cultural_intensity": "calm",
        "layout_signature":   "sidebar dashboard with table views",
    }


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
    await _send(websocket, "progress", "🎯 Stage 0.5 — Classifying admin purpose…")

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
        await _send(websocket, "error", f"❌ Purpose classification failed: {exc}")
        return False

    logger.info(
        "[%s] Admin Stage 0.5 COMPLETE: purpose=%s (%d%%), industry=%r",
        project_id, purpose_data["primary_purpose"],
        purpose_data["confidence"], purpose_data["industry"],
    )

    # ── Stage 1: Intent analysis ────────────────────────────────────
    logger.info("[%s] Admin Stage 1 ENTRY: intent analysis", project_id)
    await _send(websocket, "progress", "🧠 Stage 1 — Analyzing intent…")

    from app.services.landing_intent import analyze_intent
    try:
        intent = await analyze_intent(clean_description, classification, timeout_s=60.0)
    except Exception as exc:
        logger.error(
            "[%s] Admin Stage 1 FAILED — %s", project_id, exc, exc_info=True,
        )
        await _send(websocket, "error", f"❌ Intent analysis failed: {exc}")
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

    # ── Stage 3: Visual DNA (LIGHTER than website) ──────────────────
    # Step 3.3 stub: minimal defaults from intent. Admin styling is
    # mostly standard template (per Q2 decision in spec); a full
    # palette/typography research call is overkill.
    logger.info("[%s] Admin Stage 3 ENTRY: visual DNA (stub)", project_id)
    visual_dna = _stub_visual_dna_for_admin(intent)
    logger.info(
        "[%s] Admin Stage 3 COMPLETE: brand=%r primary_color=%s",
        project_id, visual_dna["brand_name"], visual_dna["primary_color"],
    )

    # ── Stage 4: Plan — derived from data_model after 4.5 ───────────
    # The website pipeline calls build_website_plan BEFORE the data
    # model planner. For admins we flip the order: the data model is
    # the source of truth, and "pages" are just entity routes derived
    # from it. We build the plan AFTER Stage 4.5 below.

    # ── Linked-admin path: resolve parent's tenant, skip 4.5/4.6/4.7 ─
    # If parent_project_id is set on this chat_sessions row, the admin
    # reads from the parent's tenant — there's nothing to provision or
    # seed because the parent already did. We still need a plan; we
    # derive it from the parent's data_model.
    from app.supabase_client import managed_admin_client
    from app.services.pipeline_tenant import (
        resolve_tenant_for_project,
        provision_tenant_for_project,
        seed_tenant_for_project,
    )

    is_linked = False
    linked_data_model = None
    linked_tenant_schema: Optional[str] = None
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
                            "[%s] Admin Stage 4.6 FAILED: linked admin's "
                            "parent (%s) has no tenant_schema or data_model",
                            project_id, parent_id,
                        )
                        await _send(
                            websocket, "error",
                            "❌ Linked admin's parent project is not provisioned yet.",
                        )
                        return False
                    linked_tenant_schema, linked_data_model = resolved
                    logger.info(
                        "[%s] Admin Stage 4.6 COMPLETE (linked): "
                        "parent=%s tenant_schema=%s tables=%d",
                        project_id, parent_id, linked_tenant_schema,
                        len(linked_data_model.tables),
                    )
        except Exception as exc:
            logger.error(
                "[%s] Admin linked-resolution threw — %s",
                project_id, exc, exc_info=True,
            )
            await _send(
                websocket, "error",
                f"❌ Could not resolve admin's parent project: {exc}",
            )
            return False

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
        "🗂️  Stage 4.5 — Planning data model (entities to manage)…",
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
            f"❌ Data model planning failed: {exc}",
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
            "❌ Planner produced 0 entity tables for this admin.",
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
        f"🗂️  Data model: {len(data_model.tables)} entities "
        f"({', '.join(t.name for t in data_model.tables)})",
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
                "❌ Tenant provisioning failed for this admin.",
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
    # Deterministic file emission — package.json, layouts, auth, lib,
    # components, dashboard, stub CRUD pages. No LLM here. Step 3.6
    # will replace the stub CRUD pages with real list / create / edit
    # bodies via Claude.
    logger.info("[%s] Admin Stage 5 ENTRY: foundation builder", project_id)
    await _send(
        websocket, "progress",
        "🛠️  Stage 5 — Building admin foundation files…",
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
            f"❌ Foundation builder failed: {exc}",
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
        f"🛠️  Foundation: {len(foundation_result['files_written'])} files "
        f"({len(data_model.tables)} entities scaffolded)",
    )

    # ── Stage 6: CRUD codegen (Step 3.6 Part A — mock-default) ──────
    # Per-entity Claude codegen for list / create / edit pages.
    # Default is MOCK mode — writes self-explanatory placeholders that
    # honour the AuthGuard + db_admin contract. Set
    # ADMIN_CODEGEN_MOCK=false (with a valid ANTHROPIC_API_KEY) once
    # Part B is ready to spend credits.
    from app.services.admin_codegen import generate_entity_crud

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
        f"⚡ Stage 6 — Generating CRUD ({'mock' if mock_codegen else 'Claude'})…",
    )

    anthropic_key = (
        (validated or {}).get("anthropic_api_key")
        or os.environ.get("ANTHROPIC_API_KEY", "")
    )

    codegen_results: list[dict] = []
    for entity in data_model.tables:
        try:
            result = await generate_entity_crud(
                entity=entity,
                data_model=data_model,
                admin_plan=plan,
                workspace_path=workspace_path,
                anthropic_key=anthropic_key,
                mock=mock_codegen,
            )
        except Exception as exc:
            logger.error(
                "[%s] Admin Stage 6 entity %s threw — %s",
                project_id, entity.name, exc, exc_info=True,
            )
            continue
        codegen_results.append(result)
        if result["validation_errors"]:
            logger.warning(
                "[%s] Admin Stage 6 entity %s: %d validation issue(s)",
                project_id, entity.name, len(result["validation_errors"]),
            )

    total_actual = sum(r["claude_actual_cost"]   for r in codegen_results)
    total_est    = sum(r["claude_cost_estimate"] for r in codegen_results)
    total_files  = sum(len(r["files_written"])   for r in codegen_results)
    total_errors = sum(len(r["validation_errors"]) for r in codegen_results)
    logger.info(
        "[%s] Admin Stage 6 COMPLETE: files=%d, validator_errors=%d, "
        "cost=$%.4f (est $%.4f, mock=%s)",
        project_id, total_files, total_errors,
        total_actual, total_est, mock_codegen,
    )

    # ── Stage 7: Build verification (still deferred) ────────────────
    # The mock + real Claude paths produce JSX that the next `npm run
    # build` will validate; until we shell out to Node from Python
    # this stage stays informational.
    logger.info(
        "[%s] Admin Stage 7 SKIPPED: reason=needs_node_runtime "
        "(npm run build is the canonical verifier — see dry_run script)",
        project_id,
    )

    await _send(
        websocket, "progress",
        f"✅ Admin generated — {len(data_model.tables)} entities, "
        f"{total_files} CRUD files{' (mock)' if mock_codegen else ''}, "
        f"tenant_schema={tenant_schema}",
    )
    return True
