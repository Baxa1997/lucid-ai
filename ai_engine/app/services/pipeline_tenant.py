"""Shared tenant provisioning and seed data helpers used by website
and admin pipelines.

These functions are pipeline-agnostic. They take a project_id and
DataModel, then provision tenant schemas, generate DDL, apply it,
and seed sample data. They don't know or care whether the caller
is generating a website or an admin panel.

Provisioning policy:
  • If `chat_sessions.parent_project_id` is set, this is a linked
    project. Don't provision a new tenant_schema — caller should
    resolve the parent's tenant_schema and use that.
  • If `parent_project_id` is NULL, this is a standalone project.
    Provision a fresh tenant_schema.

Idempotency:
  • Both `provision_tenant_for_project` and `seed_tenant_for_project`
    can be called multiple times safely.
  • `provision_tenant_for_project` checks for an existing
    `tenant_schema` before creating, and the generated SQL is
    `IF NOT EXISTS` / `DROP IF EXISTS` throughout — so a partial
    failure followed by a re-run finishes cleanly.
  • `seed_tenant_for_project` does NOT currently skip already-seeded
    tables; re-running it appends new Gemini-generated rows. Callers
    that need exactly-once seeding should gate it themselves
    (e.g. cache the seed result against the project_id).
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Optional

from app.services.data_model import DataModel

logger = logging.getLogger(__name__)


# ── Shared regex (module-level so callers can import it too) ──────────
# A real UUID, case-insensitive. `provision_tenant_for_project` keys
# off this to refuse provisioning for placeholder project_ids like
# `_session_none_` that have no chat_sessions row to attach to.
UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


# ── Feature flags ─────────────────────────────────────────────────────

def tenant_provision_enabled() -> bool:
    """Stage 4.6 provisioning toggle (default ON). Set
    TENANT_PROVISION_ENABLED=0 to skip the schema-create + SQL-apply
    step. Useful when iterating on prompts and you don't want each run
    to mutate the Supabase project."""
    raw = os.environ.get("TENANT_PROVISION_ENABLED", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def tenant_seed_enabled() -> bool:
    """Stage 4.7 seed-generation toggle (default ON). Set
    TENANT_SEED_ENABLED=0 to skip the Gemini seed call + INSERTs.
    Disabling this leaves provisioned tables empty — useful when only
    the schema shape matters."""
    raw = os.environ.get("TENANT_SEED_ENABLED", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


# ── Internal: minimal websocket helper ────────────────────────────────
# Duplicated from website_pipeline._send rather than imported to keep
# this module free of any pipeline-specific cross-imports. Both
# implementations are tiny no-op-on-None wrappers around send_json;
# changing one without changing the other is fine — neither carries
# behaviour worth synchronizing.

async def _send(websocket: Any, kind: str, message: str) -> None:
    if websocket is None:
        return
    try:
        await websocket.send_json({"type": kind, "message": message})
    except Exception:
        pass


# ── provision_tenant_for_project ──────────────────────────────────────

async def provision_tenant_for_project(
    *,
    data_model: Optional[DataModel],
    project_id: str,
    websocket: Any,
) -> Optional[str]:
    """Stage 4.6 — lazy tenant provisioning.

    Creates the per-project Postgres schema (one-shot) and applies the
    planner's CREATE TABLE / RLS / index / trigger DDL. Returns the
    tenant schema name on success, or None when skipped or failed.

    Skipped (returns None) when ANY of:
      • The flag `TENANT_PROVISION_ENABLED` is off.
      • data_model is None or has no tables (nothing to provision for).
      • project_id is not a UUID — there's no chat_sessions row to
        associate the schema with (e.g. `_session_none_` dev runs).

    Failure path: any error during provision/apply is caught, logged,
    and surfaced as a websocket warning. The function returns None and
    the rest of the pipeline keeps running with JSON-only content.

    Idempotency: reads `chat_sessions.tenant_schema` first; if already
    populated, skips the RPC call and re-applies the generated SQL
    (which is fully IF NOT EXISTS / DROP IF EXISTS — safe to re-run).
    """
    # Entry log — makes it obvious in production whether the stage
    # was even reached. Pair with the SKIPPED warnings below.
    logger.info(
        "[%s] Stage 4.6 ENTRY: project_id type=%s, "
        "data_model_tables=%d, has_uuid=%s",
        project_id,
        type(project_id).__name__,
        len(getattr(data_model, "tables", []) or []) if data_model else -1,
        bool(UUID_RE.match(project_id)),
    )

    if not tenant_provision_enabled():
        logger.warning(
            "[%s] Stage 4.6 SKIPPED: reason=TENANT_PROVISION_ENABLED=0 "
            "(this means generated site will use JSON only — no live Supabase data layer)",
            project_id,
        )
        return None
    if data_model is None or not getattr(data_model, "tables", None):
        logger.warning(
            "[%s] Stage 4.6 SKIPPED: reason=empty_or_missing_data_model "
            "(planner emitted no tables; site will use JSON only)",
            project_id,
        )
        return None
    if not UUID_RE.match(project_id):
        logger.warning(
            "[%s] Stage 4.6 SKIPPED: reason=project_id_not_uuid "
            "(no chat_sessions row to anchor tenant_schema — site will use JSON only)",
            project_id,
        )
        return None

    await _send(websocket, "progress",
                "🔐 Stage 4.6/6 — Provisioning tenant schema + applying SQL…")

    # Lazy imports keep import-time of this module cheap and let
    # tests patch these symbols on the module easily.
    from app.supabase_client import managed_admin_client
    from app.services.tenant_sql_generator import (
        apply_tenant_sql,
        generate_tenant_sql,
        validate_generated_sql,
    )

    try:
        async with managed_admin_client() as admin:
            # 0. Persist the planner's DataModel onto chat_sessions so
            #    the public.get_tenant_collection RPC (migration 025)
            #    can authorize callers by checking the `public_read`
            #    flag per table. Done as part of provisioning rather
            #    than Stage 4.5 so we only persist when we're actually
            #    going to spin up a tenant for it.
            await (
                admin.table("chat_sessions")
                .update({"data_model": data_model.model_dump(mode="json")})
                .eq("id", project_id)
                .execute()
            )

            # 1. Has this project already been provisioned?
            existing = await (
                admin.table("chat_sessions")
                .select("tenant_schema")
                .eq("id", project_id)
                .limit(1)
                .execute()
            )
            rows = existing.data or []
            if rows and rows[0].get("tenant_schema"):
                tenant_schema = rows[0]["tenant_schema"]
                logger.info(
                    "pipeline_tenant: tenant_schema already provisioned (%s) — re-applying SQL",
                    tenant_schema,
                )
            else:
                rpc_res = await admin.rpc(
                    "provision_tenant_schema",
                    {"p_project_id": project_id},
                ).execute()
                tenant_schema = rpc_res.data
                logger.info(
                    "pipeline_tenant: tenant_schema provisioned — %s",
                    tenant_schema,
                )

            # 2. Generate + static-validate the DDL before sending to Postgres.
            sql = generate_tenant_sql(data_model, tenant_schema, project_id)
            sql_errs = validate_generated_sql(sql)
            if sql_errs:
                raise RuntimeError(
                    f"validate_generated_sql produced {len(sql_errs)} error(s): "
                    + "; ".join(sql_errs)
                )

            # 3. Apply each statement via the execute_ddl RPC.
            apply_res = await apply_tenant_sql(sql, admin)
            if not apply_res["success"]:
                raise RuntimeError(
                    f"apply_tenant_sql failed after {apply_res['statements_executed']} "
                    f"statement(s): {apply_res['error']}"
                )

            logger.info(
                "pipeline_tenant: tenant SQL applied — schema=%s tables=%d statements=%d",
                tenant_schema, len(data_model.tables),
                apply_res["statements_executed"],
            )
            await _send(
                websocket, "progress",
                f"🔐 Tenant schema {tenant_schema} ready "
                f"({apply_res['statements_executed']} DDL statements)",
            )
            return tenant_schema
    except Exception as exc:  # noqa: BLE001 — never surface DB errors to caller
        logger.error(
            "pipeline_tenant: tenant provisioning failed — %s", exc,
            exc_info=True,
        )
        await _send(
            websocket, "warning",
            f"⚠️ Tenant provisioning failed ({exc}). "
            "Continuing with JSON-only content.",
        )
        return None


# ── seed_tenant_for_project ───────────────────────────────────────────

async def seed_tenant_for_project(
    *,
    data_model: Optional[DataModel],
    tenant_schema: Optional[str],
    website_plan: dict,
    intent: dict,
    purpose_data: dict,
    gemini_key: str,
    project_id: str,
    websocket: Any,
) -> Optional[dict]:
    """Stage 4.7 — Gemini-generated seed rows inserted into tenant tables.

    Returns the `apply_seed_data` result dict on success, or None when
    skipped or failed.

    Skipped (returns None) when ANY of:
      • Flag `TENANT_SEED_ENABLED` is off.
      • data_model is None / has no tables.
      • tenant_schema is None — without a provisioned schema there's
        nothing to insert into. This makes the seed step a strict
        downstream of Stage 4.6 (provision must have succeeded).

    Non-fatal: any error logs + warns over the websocket; the rest of
    the pipeline keeps running with empty tenant tables. Phase 2.3.C
    codegen will fall back to JSON content for tables with zero rows.

    Note on the `website_plan` argument name: this is the prompt-context
    blob the seeder uses to generate domain-appropriate rows. The admin
    pipeline passes its admin "plan" (entities + purpose) in the same
    slot — the parameter name is historical, not semantic.
    """
    # Entry log — same pattern as Stage 4.6.
    logger.info(
        "[%s] Stage 4.7 ENTRY: tenant_schema=%r, data_model_tables=%d",
        project_id,
        tenant_schema,
        len(getattr(data_model, "tables", []) or []) if data_model else -1,
    )

    if not tenant_seed_enabled():
        logger.warning(
            "[%s] Stage 4.7 SKIPPED: reason=TENANT_SEED_ENABLED=0 "
            "(tables provisioned but empty — generated site will fall back to JSON)",
            project_id,
        )
        return None
    if data_model is None or not getattr(data_model, "tables", None):
        logger.warning(
            "[%s] Stage 4.7 SKIPPED: reason=empty_or_missing_data_model "
            "(nothing to seed)",
            project_id,
        )
        return None
    if not tenant_schema:
        logger.warning(
            "[%s] Stage 4.7 SKIPPED: reason=no_tenant_schema "
            "(Stage 4.6 either skipped or failed — no schema to insert into)",
            project_id,
        )
        return None

    await _send(websocket, "progress",
                "🌱 Stage 4.7/6 — Generating + inserting seed data…")

    from app.supabase_client import managed_admin_client
    from app.services.seed_tenant_data import apply_seed_data, plan_seed_data
    from app.services.image_binding import search_unsplash

    try:
        seed_data = await plan_seed_data(
            data_model=data_model,
            website_plan=website_plan,
            intent=intent,
            purpose_data=purpose_data,
            gemini_key=gemini_key,
            project_id=project_id,
        )
    except Exception as exc:
        logger.error(
            "pipeline_tenant: seed plan failed — %s", exc, exc_info=True,
        )
        await _send(
            websocket, "warning",
            f"⚠️ Seed generation failed ({exc}). Tables will be empty.",
        )
        return None

    if not seed_data:
        # plan_seed_data returns {} on Gemini failure; treat as a soft
        # miss rather than an error so the pipeline keeps going.
        await _send(
            websocket, "warning",
            "⚠️ Seed generation returned no rows — tables will be empty.",
        )
        return None

    try:
        async with managed_admin_client() as admin:
            result = await apply_seed_data(
                seed_data=seed_data,
                data_model=data_model,
                tenant_schema=tenant_schema,
                admin_client=admin,
                image_search=search_unsplash,
            )
    except Exception as exc:
        logger.error(
            "pipeline_tenant: seed apply threw — %s", exc, exc_info=True,
        )
        await _send(
            websocket, "warning",
            f"⚠️ Seed insert threw ({exc}). Some tables may be partially seeded.",
        )
        return None

    if not result["success"]:
        logger.error(
            "pipeline_tenant: seed apply failed on table %r — %s",
            result["failed_table"], result["error"],
        )
        await _send(
            websocket, "warning",
            f"⚠️ Seed insert failed on {result['failed_table']} "
            f"after {result['tables_inserted']} table(s): {result['error']}",
        )
        return result

    logger.info(
        "pipeline_tenant: seed applied — tables=%d rows=%d",
        result["tables_inserted"], result["rows_inserted"],
    )
    await _send(
        websocket, "progress",
        f"🌱 Seed data: {result['rows_inserted']} rows across "
        f"{result['tables_inserted']} table(s)",
    )
    return result


# ── resolve_tenant_for_project ────────────────────────────────────────

async def resolve_tenant_for_project(
    project_id: str,
    admin_client: Any,
) -> Optional[tuple[str, DataModel]]:
    """For a given project_id, returns (tenant_schema, data_model).

    If project has parent_project_id set: returns parent's values
    (linked-project case — the admin pipeline writes into the website
    project's tenant schema rather than its own).

    If project has its own tenant_schema: returns those.

    If neither: returns None — the project has no backend layer to
    write into. Caller decides how to handle (admin pipeline would
    log + warn).

    Used by admin_pipeline to determine where to write tenant data.
    Pure read — no provisioning side-effects.
    """
    # Fetch the project's tenancy metadata in one read.
    res = await (
        admin_client.table("chat_sessions")
        .select("id, parent_project_id, tenant_schema, data_model")
        .eq("id", project_id)
        .limit(1)
        .execute()
    )
    rows = res.data or []
    if not rows:
        logger.warning(
            "resolve_tenant_for_project: chat_sessions row not found for %s",
            project_id,
        )
        return None

    project = rows[0]

    # Linked-project case: resolve through the parent.
    parent_id = project.get("parent_project_id")
    if parent_id:
        parent_res = await (
            admin_client.table("chat_sessions")
            .select("tenant_schema, data_model")
            .eq("id", parent_id)
            .limit(1)
            .execute()
        )
        parent_rows = parent_res.data or []
        if not parent_rows:
            logger.error(
                "[%s] parent_project_id %s not found — cannot resolve tenant",
                project_id, parent_id,
            )
            return None

        parent           = parent_rows[0]
        tenant_schema    = parent.get("tenant_schema")
        data_model_dict  = parent.get("data_model")
        if not tenant_schema or not data_model_dict:
            return None
        return tenant_schema, DataModel.model_validate(data_model_dict)

    # Standalone: return the project's own values.
    tenant_schema   = project.get("tenant_schema")
    data_model_dict = project.get("data_model")
    if not tenant_schema or not data_model_dict:
        return None
    return tenant_schema, DataModel.model_validate(data_model_dict)
