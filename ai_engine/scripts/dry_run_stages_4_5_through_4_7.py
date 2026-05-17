"""Pre-Claude dry run for Phase 2.3 Stages 4.5 → 4.7.

Exercises everything the chat UI would invoke up to (but NOT including)
Stage 6 — the Claude page generation that costs Anthropic credits.
After the run, performs 6 verifications against live Postgres + the
generated workspace, and reports a pass/fail per check.

Use this before paying Claude to verify a real chat run: a green dry
run means the pre-Claude pipeline is fully wired and the seeded data
+ Supabase plumbing are correct.

Verifications:
  (a) chat_sessions.data_model populated
  (b) chat_sessions.tenant_schema set
  (c) Tenant schema exists in Postgres
  (d) Tables exist inside the tenant schema
  (e) Tables have seed rows
  (f) Workspace has src/lib/supabase.js, src/lib/db.js, .env.local

Cost: $0 Claude + ~$0.03 Gemini (one planner call + one seeder call).
Mutates Supabase — creates + drops one chat_session + one tenant schema.
Cleans up on both success and failure.

Usage:
    docker exec -e OPENHANDS_SUPPRESS_BANNER=1 -e LOGURU_LEVEL=INFO \\
        lucid-ai-ai_engine-1 python scripts/dry_run_stages_4_5_through_4_7.py
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _load_env_file(path: str) -> None:
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env_file(str(Path(__file__).resolve().parents[2] / ".env"))


from app.services.data_model_planner import plan_data_model  # noqa: E402
from app.services.website_pipeline import (  # noqa: E402
    _build_foundation_files,
    _provision_tenant_for_project,
    _seed_tenant_for_project,
)


FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "website_plans"


# ── Setup / cleanup helpers ──────────────────────────────────────────

async def _pick_existing_user_id() -> str:
    from app.supabase_client import managed_admin_client
    async with managed_admin_client() as c:
        res = await c.table("users").select("id").limit(1).execute()
        rows = res.data or []
        if not rows:
            raise SystemExit("ERROR: no users in public.users — cannot attach test row")
        return rows[0]["id"]


async def _create_chat_session(title: str) -> str:
    """Insert a chat_sessions row and return its UUID. This mirrors what
    ChatService.create_session does for a real chat UI flow."""
    from app.supabase_client import managed_admin_client
    user_id = await _pick_existing_user_id()
    async with managed_admin_client() as c:
        res = await (
            c.table("chat_sessions")
            .insert({"user_id": user_id, "title": title})
            .execute()
        )
        return res.data[0]["id"]


async def _delete_chat_session(project_id: str) -> None:
    from app.supabase_client import managed_admin_client
    try:
        async with managed_admin_client() as c:
            await (
                c.table("chat_sessions")
                .delete().eq("id", project_id)
                .execute()
            )
    except Exception as exc:
        print(f"   cleanup: chat_session delete threw — {exc}")


async def _drop_tenant(project_id: str) -> None:
    from app.supabase_client import managed_admin_client
    try:
        async with managed_admin_client() as c:
            await c.rpc(
                "drop_tenant_schema", {"p_project_id": project_id},
            ).execute()
    except Exception as exc:
        print(f"   cleanup: drop_tenant threw — {exc}")


# ── Verification helpers ─────────────────────────────────────────────

async def _fetch_chat_session_row(project_id: str) -> dict | None:
    from app.supabase_client import managed_admin_client
    async with managed_admin_client() as c:
        res = await (
            c.table("chat_sessions")
            .select("id, tenant_schema, data_model")
            .eq("id", project_id).limit(1)
            .execute()
        )
        rows = res.data or []
        return rows[0] if rows else None


async def _fetch_via_rpc(project_id: str, table_name: str) -> list | None:
    """Call public.get_tenant_collection for `table_name`. Returns the
    list of rows on success, or None if the RPC raises (which means
    the table doesn't exist OR isn't public_read OR no chat_sessions
    row was found).

    This is the same path the generated website uses, so it doubles
    as runtime smoke + diagnostic.

    Why not query information_schema directly: PostgREST only exposes
    the schemas in its `db_schemas` config (public, graphql_public by
    default). information_schema isn't whitelisted, so a
    `.schema('information_schema').table('tables')` call returns
    PGRST106 "Invalid schema". Using the public RPC sidesteps that.
    """
    from app.supabase_client import managed_admin_client
    async with managed_admin_client() as c:
        try:
            res = await c.rpc(
                "get_tenant_collection",
                {"p_project_id": project_id, "p_table_name": table_name},
            ).execute()
        except Exception:
            return None
        data = res.data
        return data if isinstance(data, list) else None


# ── Verifications ────────────────────────────────────────────────────

def _emit(check_id: str, label: str, ok: bool, detail: str = "") -> None:
    sym = "✓" if ok else "✗"
    print(f"   {sym}  ({check_id}) {label}" + (f" — {detail}" if detail else ""))


async def _verify_all(
    project_id: str,
    tenant_schema: str,
    workspace_path: str,
    table_names: list[str],
) -> list[tuple[str, bool, str]]:
    """Returns [(check_id, passed, detail), ...] for the 6 checks (a)-(f)."""
    results: list[tuple[str, bool, str]] = []

    # (a) chat_sessions.data_model populated
    row = await _fetch_chat_session_row(project_id)
    dm_ok = bool(row) and bool(row.get("data_model")) and isinstance(
        row["data_model"], dict
    ) and bool(row["data_model"].get("tables"))
    results.append((
        "a", dm_ok,
        f"data_model has {len(row['data_model'].get('tables', []))} tables"
        if dm_ok else "data_model missing or empty",
    ))

    # (b) chat_sessions.tenant_schema set
    schema_set = bool(row) and bool(row.get("tenant_schema"))
    results.append((
        "b", schema_set,
        f"tenant_schema={row.get('tenant_schema')}" if schema_set else "tenant_schema is NULL",
    ))

    # Fetch every expected table once via the public RPC. That single
    # call answers (c), (d), and (e) in one round trip per table:
    #
    #   • If the RPC raises → schema or RPC misconfigured (fails c).
    #   • If it returns a list → the table exists in the tenant schema
    #     (contributes to d) and we count rows (contributes to e).
    #
    # We can't directly query information_schema via PostgREST (it
    # only exposes the schemas in its `db_schemas` config, which is
    # public + graphql_public by default). The RPC's existence
    # implicitly proves the schema does too.
    fetched: dict[str, list | None] = {}
    for tname in table_names:
        fetched[tname] = await _fetch_via_rpc(project_id, tname)

    # (c) Tenant schema exists in Postgres — proven if at least one
    # RPC call succeeded (didn't raise) AND chat_sessions.tenant_schema
    # was set. The RPC routes through `format(...%I)` on the persisted
    # schema name, so a successful call means the schema is real.
    any_rpc_ok = any(v is not None for v in fetched.values())
    results.append((
        "c", schema_set and any_rpc_ok,
        f"persisted={row.get('tenant_schema')}, "
        f"RPC reachable for {sum(1 for v in fetched.values() if v is not None)}/"
        f"{len(table_names)} tables",
    ))

    # (d) Expected tables exist inside the tenant schema — every
    # expected table's RPC call returned a list (even empty).
    missing_tables = [t for t, v in fetched.items() if v is None]
    results.append((
        "d", not missing_tables,
        f"all {len(table_names)} expected tables responded"
        if not missing_tables else
        f"missing/unreachable: {missing_tables}",
    ))

    # (e) Tables have seed rows.
    per_table = [(t, len(v) if v is not None else 0) for t, v in fetched.items()]
    total_rows = sum(n for _, n in per_table)
    results.append((
        "e", total_rows > 0,
        f"rows per table: {per_table} (total {total_rows})",
    ))

    # (f) Workspace has src/lib/supabase.js, src/lib/db.js, .env.local
    required = [
        ".env.local",
        "src/lib/supabase.js",
        "src/lib/db.js",
    ]
    missing_files = [
        rel for rel in required
        if not os.path.isfile(os.path.join(workspace_path, rel))
    ]
    results.append((
        "f", not missing_files,
        "all 3 Supabase plumbing files present" if not missing_files
        else f"missing: {missing_files}",
    ))

    return results


# ── Main ──────────────────────────────────────────────────────────────

async def main() -> int:
    fixture = "restaurant_plan.json"
    plan = json.loads((FIXTURES / fixture).read_text())
    intent = {
        "business_category":     "restaurant",
        "geographic_specifics":  "Brooklyn, NY",
        "tone":                  "warm",
    }
    purpose_data = {
        "primary_purpose":  "brand_awareness",
        "target_audience":  "b2c_consumers",
        "industry":         "restaurant",
    }
    visual_dna = {"cultural_intensity": "warm"}
    gemini_key = os.environ.get("GOOGLE_API_KEY", "")

    print("=== DRY RUN — Stages 4.5 → 4.7 (no Claude) ===\n")
    print(f"Fixture: {fixture}\n")

    project_id = await _create_chat_session(title="[DRY RUN 4.5-4.7] delete me")
    print(f"✓ chat_session created: {project_id}")

    workspace = tempfile.mkdtemp(prefix="lucid_dry_run_")
    tenant_schema: str | None = None
    table_names: list[str] = []
    exit_code = 0

    try:
        # Stage 4.5 — planner
        print("\n→ Stage 4.5 (planner) …")
        data_model = await plan_data_model(
            website_plan=plan, intent=intent, purpose_data=purpose_data,
            visual_dna=visual_dna,
            gemini_key=gemini_key, project_id=project_id,
        )
        if not data_model.tables:
            print("✗ planner returned empty DataModel — aborting (no point in 4.6/4.7)")
            return 1
        table_names = [t.name for t in data_model.tables]
        print(f"  tables: {table_names}")

        # Stage 4.6 — provision + DDL
        print("\n→ Stage 4.6 (provision + DDL) …")
        tenant_schema = await _provision_tenant_for_project(
            data_model=data_model, project_id=project_id, websocket=None,
        )
        if not tenant_schema:
            print("✗ provisioning returned None — aborting (no point in 4.7)")
            return 2
        print(f"  tenant_schema: {tenant_schema}")

        # Stage 4.7 — seed
        print("\n→ Stage 4.7 (seed) …")
        seed = await _seed_tenant_for_project(
            data_model=data_model, tenant_schema=tenant_schema,
            website_plan=plan, intent=intent, purpose_data=purpose_data,
            gemini_key=gemini_key, project_id=project_id, websocket=None,
        )
        if seed is None or not seed["success"]:
            print(f"  ⚠ seed step failed: {seed}")
            # Don't abort — still want to run the (a)-(d), (f) checks.
        else:
            print(f"  rows_inserted: {seed['rows_inserted']}")

        # Foundation builder — Stage 5's first step. We invoke it
        # directly into a tempdir to verify the Supabase plumbing
        # files are emitted (check f). The real pipeline writes
        # these to the project workspace as part of Stage 5.
        print("\n→ Foundation builder (Stage 5 partial — files only) …")
        files = _build_foundation_files(
            plan=plan, visual_dna=visual_dna, design_signal={},
            data_model=data_model, tenant_schema=tenant_schema,
            project_id=project_id,
        )
        for rel, content in files.items():
            abs_p = os.path.join(workspace, rel)
            os.makedirs(os.path.dirname(abs_p) or workspace, exist_ok=True)
            with open(abs_p, "w", encoding="utf-8") as fh:
                fh.write(content)
        print(f"  {len(files)} foundation files written to {workspace}")

        # ── 6 verifications ────────────────────────────────────────
        print("\n=== VERIFICATIONS ===")
        checks = await _verify_all(
            project_id=project_id,
            tenant_schema=tenant_schema,
            workspace_path=workspace,
            table_names=table_names,
        )
        for cid, ok, detail in checks:
            label = {
                "a": "chat_sessions.data_model populated",
                "b": "chat_sessions.tenant_schema set",
                "c": "Tenant schema exists in Postgres",
                "d": "Expected tables exist in tenant schema",
                "e": "Tables have seed rows (via public RPC)",
                "f": "Workspace has supabase.js, db.js, .env.local",
            }[cid]
            _emit(cid, label, ok, detail)
            if not ok:
                exit_code = 10

        all_passed = exit_code == 0
        print()
        print("=== DRY RUN " + ("PASSED" if all_passed else "FAILED") + " ===")
        return exit_code

    finally:
        print("\n→ cleanup …")
        if tenant_schema:
            await _drop_tenant(project_id)
            print(f"   dropped tenant_schema {tenant_schema}")
        await _delete_chat_session(project_id)
        print(f"   deleted chat_session {project_id}")
        try:
            shutil.rmtree(workspace)
            print(f"   removed temp workspace {workspace}")
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
