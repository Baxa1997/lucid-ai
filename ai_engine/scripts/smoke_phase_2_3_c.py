"""End-to-end smoke for Phase 2.3.C — verifies that:

  • Migration 025's `get_tenant_collection` RPC actually returns
    the seeded rows we wrote in Stage 4.7.
  • `_build_foundation_files` emits valid Supabase plumbing
    (.env.local, src/lib/supabase.js, src/lib/db.js) with the real
    env var values and project_id baked in.

This script piggybacks on the Phase 2.3.A+B smoke. It runs Stages
4.5 → 4.6 → 4.7, then makes a real RPC call as anon, then dumps the
foundation files into a temp workspace and inspects them.

Stage 6 (Claude code generation) is NOT exercised — that costs real
Anthropic money and the unit tests already prove the prompt is built
correctly. Verify Claude's behavior by running the full pipeline
through the UI when you're ready.

Cost: ~$0.05 Gemini (one planner + one seeder call). Mutates the
Supabase project — creates one chat_sessions row + one tenant schema,
deletes both at the end.

Usage:
    docker exec -e OPENHANDS_SUPPRESS_BANNER=1 -e LOGURU_LEVEL=INFO \\
        lucid-ai-ai_engine-1 python scripts/smoke_phase_2_3_c.py
"""
from __future__ import annotations

import asyncio
import json
import os
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
from app.services.pipeline_tenant import (  # noqa: E402
    provision_tenant_for_project,
    seed_tenant_for_project,
)
from app.services.website_pipeline import _build_foundation_files  # noqa: E402


FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "website_plans"


async def _pick_existing_user_id() -> str:
    from app.supabase_client import managed_admin_client
    async with managed_admin_client() as client:
        res = await client.table("users").select("id").limit(1).execute()
        rows = res.data or []
        if not rows:
            raise SystemExit("ERROR: no users in public.users — can't attach test row")
        return rows[0]["id"]


async def _create_chat_session(title: str) -> str:
    from app.supabase_client import managed_admin_client
    user_id = await _pick_existing_user_id()
    async with managed_admin_client() as client:
        res = await (
            client.table("chat_sessions")
            .insert({"user_id": user_id, "title": title})
            .execute()
        )
        return res.data[0]["id"]


async def _delete_chat_session(project_id: str) -> None:
    from app.supabase_client import managed_admin_client
    try:
        async with managed_admin_client() as client:
            await (
                client.table("chat_sessions")
                .delete()
                .eq("id", project_id)
                .execute()
            )
    except Exception as exc:
        print(f"   cleanup: chat_session delete threw — {exc}")


async def _drop_tenant(project_id: str) -> None:
    from app.supabase_client import managed_admin_client
    try:
        async with managed_admin_client() as client:
            await client.rpc(
                "drop_tenant_schema", {"p_project_id": project_id},
            ).execute()
    except Exception as exc:
        print(f"   cleanup: drop_tenant threw — {exc}")


async def _call_rpc_as_anon(project_id: str, table_name: str) -> list:
    """Hit get_tenant_collection through an anon-keyed client — the
    same path the generated website will use at runtime."""
    from app.supabase_client import _create_client
    from app.config import settings

    client = await _create_client(
        settings.SUPABASE_URL, settings.SUPABASE_ANON_KEY,
    )
    try:
        res = await client.rpc(
            "get_tenant_collection",
            {"p_project_id": project_id, "p_table_name": table_name},
        ).execute()
        return res.data or []
    finally:
        # Best-effort close. _create_client doesn't expose a clean
        # close in older supabase-py versions; ignore failures.
        try:
            await client.postgrest.aclose()
        except Exception:
            pass


def _write_files(files: dict[str, str], root: str) -> None:
    for rel, content in files.items():
        abs_path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(abs_path) or root, exist_ok=True)
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(content)


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

    print("=== Smoke Phase 2.3.C ===\n")
    print(f"Fixture: {fixture}\n")

    project_id = await _create_chat_session(title="[SMOKE 2.3.C] delete me")
    print(f"✓ chat_session created: {project_id}")

    tenant_schema: str | None = None
    try:
        # 1. Plan → DataModel
        print("\n→ Stage 4.5 (planner) …")
        gemini_key = os.environ.get("GOOGLE_API_KEY", "")
        data_model = await plan_data_model(
            website_plan=plan, intent=intent, purpose_data=purpose_data,
            gemini_key=gemini_key, project_id=project_id,
        )
        if not data_model.tables:
            print("✗ planner returned empty DataModel — aborting")
            return 1
        table_names = [t.name for t in data_model.tables]
        print(f"✓ tables: {table_names}")

        # 2. Provision + apply DDL
        print("\n→ Stage 4.6 (provision + DDL) …")
        tenant_schema = await provision_tenant_for_project(
            data_model=data_model, project_id=project_id, websocket=None,
        )
        if not tenant_schema:
            print("✗ provisioning failed — aborting")
            return 2
        print(f"✓ tenant_schema: {tenant_schema}")

        # 3. Seed
        print("\n→ Stage 4.7 (seed) …")
        seed = await seed_tenant_for_project(
            data_model=data_model, tenant_schema=tenant_schema,
            website_plan=plan, intent=intent, purpose_data=purpose_data,
            gemini_key=gemini_key, project_id=project_id, websocket=None,
        )
        if seed is None or not seed["success"]:
            print(f"✗ seed step failed: {seed}")
            return 3
        print(
            f"✓ seed: {seed['rows_inserted']} rows across "
            f"{seed['tables_inserted']} tables"
        )

        # 4. **C.1 verification — anon-keyed call to get_tenant_collection.**
        # This is the path the generated website actually uses.
        print("\n→ C.1: anon call to public.get_tenant_collection …")
        first_table = table_names[0]
        rows = await _call_rpc_as_anon(project_id, first_table)
        if not isinstance(rows, list):
            print(f"✗ RPC returned non-list: {rows!r}")
            return 4
        print(f"✓ anon RPC returned {len(rows)} rows from {first_table}")
        if rows:
            # Show a tiny preview so we can eyeball the shape.
            sample = {k: v for k, v in list(rows[0].items())[:4]}
            print(f"   sample row: {sample}")

        # 5. Negative path — table not in data_model must return [].
        ghost_rows = await _call_rpc_as_anon(project_id, "definitely_not_a_table")
        if ghost_rows != []:
            print(f"✗ ghost-table call should return [], got {ghost_rows!r}")
            return 5
        print("✓ unknown table → [] (no enumeration)")

        # 6. **C.2 verification — foundation files emit correctly.**
        print("\n→ C.2: build foundation files into a temp workspace …")
        with tempfile.TemporaryDirectory() as tmp:
            files = _build_foundation_files(
                plan=plan, visual_dna=visual_dna, design_signal={},
                data_model=data_model, tenant_schema=tenant_schema,
                project_id=project_id,
            )
            _write_files(files, tmp)
            for required in (".env.local",
                             "src/lib/supabase.js",
                             "src/lib/db.js"):
                p = os.path.join(tmp, required)
                if not os.path.isfile(p):
                    print(f"✗ expected file missing: {required}")
                    return 6
                with open(p, encoding="utf-8") as f:
                    content = f.read()
                if required == ".env.local":
                    assert project_id in content, "project_id missing"
                    assert "NEXT_PUBLIC_SUPABASE_URL=" in content
                    assert "NEXT_PUBLIC_SUPABASE_ANON_KEY=" in content
                if required == "src/lib/db.js":
                    assert "get_tenant_collection" in content
                    assert "getCollection" in content
                    # Real table names should appear in the documentation comment
                    for n in table_names:
                        assert n in content, f"db.js missing {n}"
            print(f"✓ {len(files)} foundation files emitted; .env.local + db.js look right")

        print("\n=== SMOKE PASSED ===")
        return 0

    finally:
        print("\n→ cleanup …")
        if tenant_schema:
            await _drop_tenant(project_id)
        await _delete_chat_session(project_id)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
