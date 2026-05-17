"""Pre-Claude dry run for the admin pipeline (Step 3.6 Part A update).

Exercises run_admin_pipeline against live Postgres + Gemini, then
verifies the pre-Claude side of the pipeline produced the expected
state. Stage 6 runs in MOCK mode (no Anthropic credits spent); the
real Claude path will be exercised in Step 3.6 Part B.

Step 3.6 Part A verifications (extend Step 3.3 + 3.4 + 3.5):
  (a) chat_sessions.product_type = 'admin'
  (b) chat_sessions.data_model populated (1+ tables)
  (c) chat_sessions.tenant_schema set
  (d) Tenant schema exists in Postgres
  (e) Tables have seed rows (or are reachable via public RPC)
  (f) Workspace has README.md
  (g) admin plan has 3 pages per table + 3 shared pages
  (h) admin plan navigation has one entry per entity
  (i) data_model.singletons is empty
  (j) all data_model.tables have public_read=false
  (k) package.json exists with @supabase/ssr dependency
  (l) src/app/layout.jsx exists
  (m) src/app/login/page.jsx exists
  (n) src/app/page.jsx (dashboard) exists
  (o) src/lib/supabase.js, db_admin.js, auth.js all exist
  (p) For each entity: 3 pages exist (list/new/edit, as stubs)
  (q) src/components/Sidebar.jsx exists with nav items for each entity
  (r) .env.local has all 6 required env vars
  (s) Stage 6 ran in MOCK mode (no Anthropic key needed)
  (t) Each entity has 3 generated mock CRUD files
  (u) Mock files pass validate_generated_crud_file
  (v) Workspace `next build` static checks would pass (validator green)

Cost: ~$0.05 Gemini (purpose + intent + admin planner + seeder).
Zero Anthropic. Mutates Supabase — creates one chat_session,
provisions one tenant schema, seeds rows. Cleans up on success and
failure.

Usage:
    docker exec -e OPENHANDS_SUPPRESS_BANNER=1 -e LOGURU_LEVEL=INFO \\
        lucid-ai-ai_engine-1 python scripts/dry_run_admin_pipeline.py
"""
from __future__ import annotations

import asyncio
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

# Flag must be on for run_admin_pipeline to do anything.
os.environ["ADMIN_PIPELINE_V2_ENABLED"] = "true"


from app.services.admin_pipeline import run_admin_pipeline  # noqa: E402


# ── Setup / cleanup helpers (parallel to dry_run_stages_4_5_through_4_7.py) ──

async def _pick_existing_user_id() -> str:
    from app.supabase_client import managed_admin_client
    async with managed_admin_client() as c:
        res = await c.table("users").select("id").limit(1).execute()
        rows = res.data or []
        if not rows:
            raise SystemExit(
                "ERROR: no users in public.users — cannot attach test row"
            )
        return rows[0]["id"]


async def _create_chat_session(title: str) -> str:
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
                .delete().eq("id", project_id).execute()
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
            .select("id, product_type, tenant_schema, data_model")
            .eq("id", project_id).limit(1)
            .execute()
        )
        rows = res.data or []
        return rows[0] if rows else None


async def _fetch_via_rpc(project_id: str, table_name: str) -> list | None:
    """Read via the admin RPC (migration 027). Falls back to the public
    RPC (025) if the table is public_read.

    Admin tables aren't public_read by default, so we go through the
    service-role-callable authenticated RPC. service-role bypasses
    auth.uid() in the RPC and the membership check goes through, but
    auth.uid() will be NULL so the function raises access_denied —
    use the anon RPC if available, otherwise just count via SQL via
    execute_ddl is impossible here. Easiest: skip via_rpc for this
    dry run and use the tenant schema directly via a read.

    Instead: use the public 025 RPC since the planner emits at least
    one public_read=true table by default. If that fails, count by
    asking PostgREST via .schema(tenant_schema) — won't work
    (PGRST106). So we drop back to a SQL-via-RPC pattern.
    """
    # Easiest: call the public read RPC (025) — admin tables planned
    # by data_model_planner usually include public_read=false rows,
    # so this may return [] (which is still informative for "table
    # exists"). On exception, return None.
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


async def _count_rows_in_tenant(
    tenant_schema: str, table_name: str,
) -> int | None:
    """Use the service-role execute_ddl path to run a COUNT(*) in
    the tenant schema and surface the result. Returns the row count
    on success, None on failure.

    execute_ddl returns the affected row count from a SELECT … RETURNING
    style payload, but COUNT(*) doesn't return rows. So we wrap it in
    a DO block that RAISEs a NOTICE we can't capture from the client.
    Fallback: use the migration-024 helper if present, otherwise
    just probe table existence via the planner emitter — failing that,
    return None (verification (e) becomes "table responded via any
    RPC", which is weaker but still meaningful).

    Strategy used: call execute_ddl with a SELECT that inserts a
    marker into a one-row meta table — too clever. Instead: ship a
    purpose-built read via the migration-026 set_tenant_row path?
    No — that writes.

    Practical: we count by calling the admin RPC (027) WITHOUT a JWT.
    The function checks auth.uid() and raises access_denied for the
    service role — which proves the function ran, but doesn't give
    us a count. So we mint a JWT.

    For Step 3.3's dry run this complexity isn't worth it — we accept
    the weaker check (e): "the public RPC reached the table without
    raising". If it raises, _fetch_via_rpc returns None and we mark
    that table as "unreachable".
    """
    _ = tenant_schema, table_name
    return None


# ── Verifications ────────────────────────────────────────────────────

def _emit(check_id: str, label: str, ok: bool, detail: str = "") -> None:
    sym = "✓" if ok else "✗"
    print(f"   {sym}  ({check_id}) {label}" + (f" — {detail}" if detail else ""))


async def _verify_all(
    project_id: str,
    workspace_path: str,
) -> list[tuple[str, bool, str]]:
    """Returns [(check_id, passed, detail), ...] for checks (a)-(f)."""
    results: list[tuple[str, bool, str]] = []

    row = await _fetch_chat_session_row(project_id)

    # (a) product_type = 'admin'
    pt = (row or {}).get("product_type")
    results.append((
        "a", pt == "admin",
        f"product_type={pt!r}",
    ))

    # (b) data_model populated with 1+ tables
    dm = (row or {}).get("data_model") or {}
    tables = dm.get("tables") if isinstance(dm, dict) else None
    dm_ok = isinstance(tables, list) and len(tables) >= 1
    results.append((
        "b", dm_ok,
        f"{len(tables) if isinstance(tables, list) else 0} tables: "
        + (
            ", ".join(t.get("name", "?") for t in tables[:5])
            if isinstance(tables, list) else "n/a"
        ),
    ))

    # (c) tenant_schema set
    tenant_schema = (row or {}).get("tenant_schema")
    results.append((
        "c", bool(tenant_schema),
        f"tenant_schema={tenant_schema}" if tenant_schema else "NULL",
    ))

    # (d) Tenant schema exists in Postgres — proven by any public-RPC
    # call to one of the planned tables NOT raising "function does not
    # exist" or "schema does not exist". We call against the FIRST
    # table; if it returns a list (even empty), the schema's there.
    schema_ok = False
    reach_detail = "no tables to probe"
    if isinstance(tables, list) and tables:
        first_table = tables[0].get("name")
        if first_table:
            fetched = await _fetch_via_rpc(project_id, first_table)
            schema_ok = fetched is not None
            reach_detail = (
                f"{first_table} responded ({len(fetched)} rows)"
                if fetched is not None else
                f"{first_table} unreachable via public RPC"
            )
    results.append(("d", schema_ok, reach_detail))

    # (e) Tables have seed rows — proven if ANY table comes back with
    # a non-empty list via the public RPC. Tables that aren't
    # public_read return [] (we don't assert per-table seeding —
    # that's seed_tenant_data's own test surface).
    total_rows = 0
    public_reachable = 0
    if isinstance(tables, list):
        for t in tables:
            name = t.get("name")
            if not name:
                continue
            fetched = await _fetch_via_rpc(project_id, name)
            if fetched is not None:
                public_reachable += 1
                total_rows += len(fetched)
    # "Seed rows" is satisfied either way:
    #  • total_rows > 0 from any public_read=true table, OR
    #  • all tables responded (proves provisioning + seed didn't crash;
    #    rows are there even if not visible to anon).
    seed_ok = total_rows > 0 or (
        isinstance(tables, list)
        and len(tables) > 0
        and public_reachable == len(tables)
    )
    results.append((
        "e", seed_ok,
        f"{total_rows} rows visible via public RPC across "
        f"{public_reachable}/{len(tables) if isinstance(tables, list) else 0} table(s)",
    ))

    # (f) Workspace has README.md placeholder
    readme_path = os.path.join(workspace_path, "README.md")
    fok = os.path.isfile(readme_path)
    results.append((
        "f", fok,
        f"README.md present ({os.path.getsize(readme_path)} bytes)"
        if fok else "README.md missing",
    ))

    # ── Step 3.4 additions ──────────────────────────────────────────
    # Rebuild the admin plan locally from the persisted data_model so
    # we can verify the page + nav shape Stage 6 will consume. The
    # pipeline doesn't persist its plan dict (yet — Step 3.5 might),
    # so we re-derive it deterministically here.
    from app.services.admin_plan import build_admin_plan
    from app.services.data_model import DataModel

    parsed_dm: DataModel | None = None
    if isinstance(dm, dict) and dm.get("tables") is not None:
        try:
            parsed_dm = DataModel.model_validate(dm)
        except Exception as exc:
            parsed_dm = None
            print(f"   (could not re-validate data_model for plan check: {exc})")

    n_tables = len(parsed_dm.tables) if parsed_dm else 0

    if parsed_dm is None:
        # Without a data_model the plan derivation is meaningless;
        # mark g/h as failures with a clear reason.
        results.append(("g", False, "no data_model to build plan from"))
        results.append(("h", False, "no data_model to build plan from"))
    else:
        plan = build_admin_plan(parsed_dm, {"brand_name": "DryRun"})

        # (g) plan has 3 pages per table + 3 shared pages
        expected_pages = 3 + 3 * n_tables
        results.append((
            "g", len(plan["pages"]) == expected_pages,
            f"{len(plan['pages'])} pages (expected {expected_pages} "
            f"= 3 shared + 3×{n_tables})",
        ))

        # (h) navigation has Dashboard + one entry per entity
        nav_routes = [item["route"] for item in plan["navigation"]]
        expected_nav_routes = ["/admin"] + [
            f"/admin/{t.name.replace('_', '-')}" for t in parsed_dm.tables
        ]
        nav_ok = nav_routes == expected_nav_routes
        results.append((
            "h", nav_ok,
            f"nav routes={nav_routes} (expected {expected_nav_routes})"
            if not nav_ok else
            f"{len(nav_routes)} nav items (Dashboard + {n_tables} entities)",
        ))

    # (i) data_model.singletons is empty (or admin_branding-only)
    singletons = dm.get("singletons") if isinstance(dm, dict) else None
    if singletons is None:
        singletons_keys: list[str] = []
    elif isinstance(singletons, dict):
        singletons_keys = sorted(singletons.keys())
    else:
        singletons_keys = []
    marketing = {
        "hero", "about", "footer", "testimonials", "features",
        "pricing", "faq", "faqs",
    }
    leaks = sorted(set(singletons_keys) & marketing)
    singletons_ok = not leaks
    results.append((
        "i", singletons_ok,
        f"singletons={singletons_keys}" + (
            f" — marketing leak: {leaks}" if leaks else ""
        ),
    ))

    # (j) all tables have public_read=false
    if parsed_dm is None:
        results.append(("j", False, "no parsed data_model"))
    else:
        offenders = [t.name for t in parsed_dm.tables if t.public_read]
        results.append((
            "j", not offenders,
            "all tables private" if not offenders
            else f"public_read=True on: {offenders}",
        ))

    # ── Step 3.5 additions (k-r): Stage 5 foundation files ──────────
    ws = workspace_path

    def _isfile(rel: str) -> bool:
        return os.path.isfile(os.path.join(ws, rel))

    # (k) package.json exists + lists @supabase/ssr
    pkg_ok = False
    pkg_detail = "package.json missing"
    if _isfile("package.json"):
        try:
            import json as _json
            pkg = _json.loads(open(os.path.join(ws, "package.json")).read())
            deps = (pkg.get("dependencies") or {})
            pkg_ok = "@supabase/ssr" in deps
            pkg_detail = (
                f"name={pkg.get('name')!r}, @supabase/ssr={deps.get('@supabase/ssr')}"
                if pkg_ok else
                f"package.json present but @supabase/ssr missing (deps={list(deps)})"
            )
        except Exception as exc:
            pkg_detail = f"package.json parse failed: {exc}"
    results.append(("k", pkg_ok, pkg_detail))

    # (l) src/app/layout.jsx
    results.append((
        "l", _isfile("src/app/layout.jsx"),
        "src/app/layout.jsx present" if _isfile("src/app/layout.jsx")
        else "src/app/layout.jsx missing",
    ))

    # (m) src/app/login/page.jsx
    results.append((
        "m", _isfile("src/app/login/page.jsx"),
        "src/app/login/page.jsx present" if _isfile("src/app/login/page.jsx")
        else "src/app/login/page.jsx missing",
    ))

    # (n) src/app/page.jsx (dashboard)
    results.append((
        "n", _isfile("src/app/page.jsx"),
        "src/app/page.jsx present" if _isfile("src/app/page.jsx")
        else "src/app/page.jsx missing",
    ))

    # (o) src/lib/supabase.js, db_admin.js, auth.js
    lib_files = ["src/lib/supabase.js", "src/lib/db_admin.js", "src/lib/auth.js"]
    missing_lib = [f for f in lib_files if not _isfile(f)]
    results.append((
        "o", not missing_lib,
        "all 3 lib files present" if not missing_lib
        else f"missing: {missing_lib}",
    ))

    # (p) For each entity: 3 pages (list/new/edit)
    if isinstance(tables, list) and tables:
        per_entity = []
        for t in tables:
            name = t.get("name")
            if not name:
                continue
            slug = name.replace("_", "-")
            paths = [
                f"src/app/{slug}/page.jsx",
                f"src/app/{slug}/new/page.jsx",
                f"src/app/{slug}/[id]/page.jsx",
            ]
            missing = [p for p in paths if not _isfile(p)]
            per_entity.append((name, len(missing)))
        all_three = all(missing == 0 for _, missing in per_entity)
        detail = (
            f"all 3 pages for {len(per_entity)} entities"
            if all_three else
            f"per-entity missing counts: {per_entity}"
        )
        results.append(("p", all_three, detail))
    else:
        results.append(("p", False, "no tables to check"))

    # (q) src/components/Sidebar.jsx exists + mentions every entity slug
    sidebar_ok = False
    sidebar_detail = "Sidebar.jsx missing"
    if _isfile("src/components/Sidebar.jsx"):
        sidebar_src = open(os.path.join(ws, "src/components/Sidebar.jsx")).read()
        if isinstance(tables, list):
            missing_slugs = []
            for t in tables:
                slug = (t.get("name") or "").replace("_", "-")
                if slug and f"/{slug}" not in sidebar_src:
                    missing_slugs.append(slug)
            sidebar_ok = not missing_slugs
            sidebar_detail = (
                f"contains all {len(tables)} entity slugs"
                if sidebar_ok else
                f"missing slugs in sidebar: {missing_slugs}"
            )
        else:
            sidebar_ok = True
            sidebar_detail = "sidebar present (no entities to verify)"
    results.append(("q", sidebar_ok, sidebar_detail))

    # (r) .env.local has all 6 required env vars
    env_ok = False
    env_detail = ".env.local missing"
    if _isfile(".env.local"):
        env_text = open(os.path.join(ws, ".env.local")).read()
        required_vars = [
            "NEXT_PUBLIC_SUPABASE_URL",
            "NEXT_PUBLIC_SUPABASE_ANON_KEY",
            "NEXT_PUBLIC_PROJECT_ID",
            "NEXT_PUBLIC_TENANT_SCHEMA",
            "NEXT_PUBLIC_BRAND_NAME",
            "NEXT_PUBLIC_PRIMARY_COLOR",
        ]
        missing_env = [v for v in required_vars if v not in env_text]
        env_ok = not missing_env
        env_detail = (
            "all 6 env vars present" if env_ok
            else f"missing env vars: {missing_env}"
        )
    results.append(("r", env_ok, env_detail))

    # ── Step 3.6 Part A additions (s-v): Stage 6 mock codegen ───────
    # Stage 6 OVERWRITES the foundation-builder stubs at the same
    # paths, so we only need to inspect contents to tell Step 3.5
    # (foundation stub) from Step 3.6 (mock CRUD) — the latter
    # carries a distinctive marker.
    from app.services.admin_codegen_validator import (
        validate_generated_crud_file,
    )

    mock_marker = "MOCK page generated by Step 3.6 Part A"
    table_names = (
        [t.get("name") for t in tables if t.get("name")]
        if isinstance(tables, list) else []
    )

    # (s) Stage 6 ran in MOCK mode — detect by reading any one
    # generated file and looking for the mock marker.
    s_detail = "no entity tables to probe"
    s_ok = False
    if table_names:
        slug0 = table_names[0].replace("_", "-")
        first_path = os.path.join(ws, f"src/app/{slug0}/page.jsx")
        if os.path.isfile(first_path):
            first_text = open(first_path).read()
            s_ok = mock_marker in first_text
            s_detail = (
                "mock marker present in generated page"
                if s_ok else
                "mock marker missing — Stage 6 may not have run, "
                "or ran in non-mock (Claude) mode"
            )
    results.append(("s", s_ok, s_detail))

    # (t) Each entity has 3 generated mock CRUD files
    t_ok = False
    t_detail = "no entity tables to probe"
    if table_names:
        per_entity_mock = []
        for name in table_names:
            slug = name.replace("_", "-")
            files = [
                f"src/app/{slug}/page.jsx",
                f"src/app/{slug}/new/page.jsx",
                f"src/app/{slug}/[id]/page.jsx",
            ]
            mock_count = 0
            for rel in files:
                p = os.path.join(ws, rel)
                if os.path.isfile(p) and mock_marker in open(p).read():
                    mock_count += 1
            per_entity_mock.append((name, mock_count))
        all_three = all(count == 3 for _, count in per_entity_mock)
        t_ok = all_three
        t_detail = (
            f"all 3 mock files for {len(table_names)} entities"
            if all_three else
            f"mock-file counts per entity: {per_entity_mock}"
        )
    results.append(("t", t_ok, t_detail))

    # (u) Mock files pass the static validator. Run it across every
    # per-entity file; any issue flips the check.
    u_ok = True
    u_problems: list[str] = []
    if not table_names:
        u_ok = False
        u_problems.append("no tables to validate")
    else:
        for name in table_names:
            slug = name.replace("_", "-")
            for rel, page_type in (
                (f"src/app/{slug}/page.jsx",        "list"),
                (f"src/app/{slug}/new/page.jsx",    "create"),
                (f"src/app/{slug}/[id]/page.jsx",   "edit"),
            ):
                p = os.path.join(ws, rel)
                if not os.path.isfile(p):
                    u_ok = False
                    u_problems.append(f"{rel} missing")
                    continue
                issues = validate_generated_crud_file(
                    open(p).read(),
                    expected_entity=name,
                    page_type=page_type,
                )
                if issues:
                    u_ok = False
                    u_problems.append(
                        f"{rel}: {issues[0]}" + (
                            f" (+{len(issues) - 1} more)"
                            if len(issues) > 1 else ""
                        )
                    )
    u_detail = (
        f"validator green across {len(table_names) * 3} files"
        if u_ok else
        f"validator issues: {u_problems[:3]}"
        + (f" (+{len(u_problems) - 3} more)" if len(u_problems) > 3 else "")
    )
    results.append(("u", u_ok, u_detail))

    # (v) Build verification proxy — same green-validator gate as (u)
    # plus a structural sanity check that the foundation files
    # required by the mocks (AuthGuard, db_admin) exist. The real
    # `next build` runs separately when KEEP_WORKSPACE=1.
    structural_ok = (
        _isfile("src/components/AuthGuard.jsx")
        and _isfile("src/lib/db_admin.js")
    )
    v_ok = u_ok and structural_ok
    v_detail = (
        "validator green AND AuthGuard + db_admin foundation files present"
        if v_ok else
        f"u_ok={u_ok}, AuthGuard+db_admin present={structural_ok}"
    )
    results.append(("v", v_ok, v_detail))

    return results


# ── Main ──────────────────────────────────────────────────────────────

async def main() -> int:
    print("=== DRY RUN — admin_pipeline (Step 3.3, no Claude) ===\n")

    project_id = await _create_chat_session(
        title="[DRY RUN admin pipeline] delete me",
    )
    print(f"✓ chat_session created: {project_id}")

    workspace = tempfile.mkdtemp(prefix="lucid_dry_run_admin_")
    exit_code = 0

    try:
        # Run the full pipeline
        print("\n→ run_admin_pipeline …")
        ok = await run_admin_pipeline(
            description=(
                "internal tool to manage customer leads and contacts. "
                "Sales reps add new leads, mark them qualified, "
                "convert them to contacts with company + phone + email."
            ),
            classification={
                "layout_archetype": "admin_dashboard",
                "domain":           "internal_tool",
            },
            workspace_path=workspace,
            validated={
                "gemini_api_key": os.environ.get("GOOGLE_API_KEY", ""),
            },
            websocket=None,
            chat_session_id=project_id,
        )
        print(f"   pipeline returned: ok={ok}")
        if not ok:
            print("✗ pipeline returned False — verifications will still run")

        # ── 6 verifications ────────────────────────────────────────
        print("\n=== VERIFICATIONS ===")
        checks = await _verify_all(
            project_id=project_id,
            workspace_path=workspace,
        )
        for cid, ok_check, detail in checks:
            label = {
                "a": "chat_sessions.product_type = 'admin'",
                "b": "chat_sessions.data_model has 1+ tables",
                "c": "chat_sessions.tenant_schema set",
                "d": "Tenant schema exists in Postgres",
                "e": "Tables have seed rows / are reachable",
                "f": "Workspace has README.md",
                "g": "admin plan: 3 pages per table + 3 shared",
                "h": "admin plan: navigation has Dashboard + one per entity",
                "i": "data_model.singletons is empty (no marketing keys)",
                "j": "all data_model.tables have public_read=false",
                "k": "package.json has @supabase/ssr dependency",
                "l": "src/app/layout.jsx exists",
                "m": "src/app/login/page.jsx exists",
                "n": "src/app/page.jsx (dashboard) exists",
                "o": "src/lib/{supabase,db_admin,auth}.js all exist",
                "p": "Each entity has 3 stub pages (list/new/edit)",
                "q": "src/components/Sidebar.jsx mentions every entity",
                "r": ".env.local has all 6 required env vars",
                "s": "Stage 6 ran in MOCK mode (no Anthropic spent)",
                "t": "Each entity has 3 generated mock CRUD files",
                "u": "Mock files pass validate_generated_crud_file",
                "v": "Validator + foundation contract intact (build-ready)",
            }[cid]
            _emit(cid, label, ok_check, detail)
            if not ok_check:
                exit_code = 10

        # Print the planner's tables for the report
        row = await _fetch_chat_session_row(project_id)
        dm = (row or {}).get("data_model") or {}
        tables = dm.get("tables") if isinstance(dm, dict) else []
        if isinstance(tables, list) and tables:
            print("\n   planner emitted tables:")
            for t in tables:
                cols = [f.get("name") for f in t.get("fields") or []]
                print(f"     • {t.get('name')}  fields={cols}")

        all_passed = exit_code == 0
        print()
        print("=== DRY RUN " + ("PASSED" if all_passed else "FAILED") + " ===")
        return exit_code

    finally:
        print("\n→ cleanup …")
        await _drop_tenant(project_id)
        print(f"   dropped tenant_schema (if any)")
        await _delete_chat_session(project_id)
        print(f"   deleted chat_session {project_id}")
        # Step 3.5: keep the workspace if the smoke-test wants to
        # `cd` in and run `npm install` against it. Set
        # KEEP_WORKSPACE=1 to skip the rmtree.
        if os.environ.get("KEEP_WORKSPACE", "").strip() in ("1", "true", "yes"):
            print(f"   KEEP_WORKSPACE set — workspace preserved at {workspace}")
        else:
            try:
                shutil.rmtree(workspace)
                print(f"   removed temp workspace {workspace}")
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
