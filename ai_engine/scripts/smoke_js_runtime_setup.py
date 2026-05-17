"""Phase 2.3.C JS-runtime smoke — setup phase.

Runs inside the ai_engine docker container. Provisions a project,
seeds it, builds the foundation files (including the new Supabase
plumbing) into `/app/tmp_js_smoke`, then drops a `test.mjs` +
`package.json` into the same directory so the host can run `node`
against them.

Prints ONE LINE of JSON to stdout — the orchestrator (shell script
on the host) parses it to get the project_id for cleanup. All
diagnostic output goes to stderr.

The companion cleanup script (or shell wrapper) is responsible for
dropping the tenant schema + deleting the chat_sessions row.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
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


from app.config import settings  # noqa: E402
from app.services.data_model_planner import plan_data_model  # noqa: E402
from app.services.website_pipeline import (  # noqa: E402
    _build_foundation_files,
    _provision_tenant_for_project,
    _seed_tenant_for_project,
)


FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "website_plans"
OUT_DIR  = Path("/app") / "tmp_js_smoke"


# Run a Node script that loads the generated `src/lib/supabase.js`
# and `src/lib/db.js` and calls `getCollection`. Env vars are
# loaded by Node's `--env-file=.env.local` flag — see test_run.sh.
# We can't use `dotenv` from inside the script: ESM hoists all
# `import` statements to before any code runs, which means the
# top-level env-var check inside `supabase.js` fires BEFORE any
# `dotenv.config()` call could populate process.env.
_TEST_MJS = '''\
import { getCollection } from "./src/lib/db.js";

const tableName = process.argv[2];
if (!tableName) {
  console.error("usage: node test.mjs <table_name>");
  process.exit(2);
}

try {
  const rows = await getCollection(tableName);
  if (!Array.isArray(rows)) {
    console.error("✗ getCollection did not return an array:", rows);
    process.exit(3);
  }
  console.log(JSON.stringify({
    ok:        true,
    table:     tableName,
    row_count: rows.length,
    sample:    rows[0] ?? null,
  }));
  process.exit(0);
} catch (e) {
  console.error("✗ getCollection threw:", e);
  process.exit(4);
}
'''


_PACKAGE_JSON = json.dumps({
    "name":    "lucid-js-smoke",
    "private": True,
    "type":    "module",
    "dependencies": {
        "@supabase/supabase-js": "^2.45.0",
    },
}, indent=2) + "\n"


async def _create_chat_session(title: str) -> str:
    from app.supabase_client import managed_admin_client
    async with managed_admin_client() as c:
        users = await c.table("users").select("id").limit(1).execute()
        if not users.data:
            sys.exit("ERROR: no users in public.users")
        user_id = users.data[0]["id"]
        res = await (
            c.table("chat_sessions")
            .insert({"user_id": user_id, "title": title})
            .execute()
        )
        return res.data[0]["id"]


async def main() -> int:
    plan = json.loads((FIXTURES / "restaurant_plan.json").read_text())
    intent       = {"business_category": "restaurant",
                    "geographic_specifics": "Brooklyn, NY", "tone": "warm"}
    purpose_data = {"primary_purpose": "brand_awareness",
                    "target_audience": "b2c_consumers",
                    "industry": "restaurant"}
    visual_dna   = {"cultural_intensity": "warm"}
    gemini_key   = os.environ.get("GOOGLE_API_KEY", "")

    print("→ creating chat_session …", file=sys.stderr)
    project_id = await _create_chat_session("[SMOKE JS-RUNTIME] delete me")
    print(f"  project_id={project_id}", file=sys.stderr)

    print("→ Stage 4.5 planner …", file=sys.stderr)
    data_model = await plan_data_model(
        website_plan=plan, intent=intent, purpose_data=purpose_data,
        gemini_key=gemini_key, project_id=project_id,
    )
    if not data_model.tables:
        sys.exit("ERROR: planner returned empty DataModel")
    table_names = [t.name for t in data_model.tables]

    print("→ Stage 4.6 provisioning …", file=sys.stderr)
    tenant_schema = await _provision_tenant_for_project(
        data_model=data_model, project_id=project_id, websocket=None,
    )
    if not tenant_schema:
        sys.exit("ERROR: provisioning returned None")

    print("→ Stage 4.7 seeding …", file=sys.stderr)
    seed = await _seed_tenant_for_project(
        data_model=data_model, tenant_schema=tenant_schema,
        website_plan=plan, intent=intent, purpose_data=purpose_data,
        gemini_key=gemini_key, project_id=project_id, websocket=None,
    )
    if seed is None or not seed["success"]:
        sys.exit(f"ERROR: seed step failed: {seed}")

    # Build the foundation files and persist them to a host-visible dir.
    print("→ writing foundation files + test rig to /app/tmp_js_smoke …",
          file=sys.stderr)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    files = _build_foundation_files(
        plan=plan, visual_dna=visual_dna, design_signal={},
        data_model=data_model, tenant_schema=tenant_schema,
        project_id=project_id,
    )
    for rel, content in files.items():
        abs_p = OUT_DIR / rel
        abs_p.parent.mkdir(parents=True, exist_ok=True)
        abs_p.write_text(content, encoding="utf-8")

    # JS test rig
    (OUT_DIR / "package.json").write_text(_PACKAGE_JSON, encoding="utf-8")
    (OUT_DIR / "test.mjs").write_text(_TEST_MJS, encoding="utf-8")

    # State for the orchestrator + cleanup.
    state = {
        "project_id":    project_id,
        "tenant_schema": tenant_schema,
        "tables":        table_names,
        "first_table":   table_names[0],
        "rows_seeded":   seed["rows_inserted"],
    }
    print(json.dumps(state))   # stdout — the orchestrator's only consumed line
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
