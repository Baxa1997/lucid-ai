#!/usr/bin/env bash
# ───────────────────────────────────────────────────────────
# Phase 2.3.C JS-runtime smoke — host orchestrator.
#
# Flow:
#   1. Container Python: provision project, seed tenant, write the
#      generated foundation files + test.mjs into /app/tmp_js_smoke.
#   2. Host Node: npm install + run test.mjs against the live RPC.
#   3. Container Python: drop tenant schema + delete chat_sessions row.
#
# Why hybrid: the docker container is Python-only (no node), the host
# Node we have on the developer machine has no Python supabase-py. So
# we split the smoke across both, communicating via:
#   • the /app/tmp_js_smoke directory (bind-mounted to
#     ai_engine/tmp_js_smoke on the host),
#   • a one-line JSON state blob on stdout of the setup script.
#
# Cost: ~$0.05 Gemini (one planner + one seeder). Mutates Supabase —
# creates + drops one chat_session + one tenant schema. Cleans up on
# both success and failure.
# ───────────────────────────────────────────────────────────
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SMOKE_DIR="${REPO_ROOT}/ai_engine/tmp_js_smoke"
CONTAINER="lucid-ai-ai_engine-1"

cleanup() {
  local pid="${1:-}"
  if [[ -n "$pid" ]]; then
    echo "→ cleanup (project_id=${pid}) …" >&2
    docker exec -e OPENHANDS_SUPPRESS_BANNER=1 "$CONTAINER" python -c "
import asyncio, sys
sys.path.insert(0, '/app')
from app.supabase_client import managed_admin_client
async def main():
    async with managed_admin_client() as c:
        try:
            await c.rpc('drop_tenant_schema', {'p_project_id': '${pid}'}).execute()
        except Exception as e:
            print(f'   drop_tenant: {e}', file=sys.stderr)
        try:
            await c.table('chat_sessions').delete().eq('id', '${pid}').execute()
        except Exception as e:
            print(f'   delete chat_session: {e}', file=sys.stderr)
asyncio.run(main())
" >/dev/null 2>&1 || true
  fi
  rm -rf "$SMOKE_DIR"
}

# Catch unexpected exits and clean up if we already know a project id.
PID=""
trap 'cleanup "$PID"' EXIT

echo "=== Phase 2.3.C JS-runtime smoke ==="
rm -rf "$SMOKE_DIR"

# ── 1. setup phase (docker python) ─────────────────────────────────
echo "→ setup (provision + seed + write files) …"
STATE_JSON="$(docker exec -e OPENHANDS_SUPPRESS_BANNER=1 -e LOGURU_LEVEL=ERROR \
  "$CONTAINER" python scripts/smoke_js_runtime_setup.py)"
PID="$(echo "$STATE_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["project_id"])')"
TENANT="$(echo "$STATE_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["tenant_schema"])')"
FIRST_TABLE="$(echo "$STATE_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["first_table"])')"
SEEDED="$(echo "$STATE_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["rows_seeded"])')"

echo "  project_id=${PID}"
echo "  tenant=${TENANT}"
echo "  seeded ${SEEDED} rows; will fetch '${FIRST_TABLE}' from JS"

if [[ ! -d "$SMOKE_DIR" ]]; then
  echo "✗ setup did not create ${SMOKE_DIR}" >&2
  exit 1
fi
if [[ ! -f "$SMOKE_DIR/test.mjs" || ! -f "$SMOKE_DIR/src/lib/db.js" ]]; then
  echo "✗ setup did not write expected files" >&2
  ls -R "$SMOKE_DIR" >&2
  exit 1
fi

# ── 2. JS phase (host node) ─────────────────────────────────────────
echo "→ npm install (host) …"
( cd "$SMOKE_DIR" && npm install --silent --no-progress --no-audit --no-fund )

echo "→ node test.mjs ${FIRST_TABLE} (host) …"
# `--env-file` populates process.env BEFORE any module runs, which is
# essential because ESM `import` statements are hoisted: a `dotenv`
# call inside test.mjs would run too late, after supabase.js has
# already read (missing) env vars at top-level.
JS_OUTPUT="$( cd "$SMOKE_DIR" && node --env-file=.env.local test.mjs "$FIRST_TABLE" )"
echo "  → ${JS_OUTPUT}"

# Sanity-check the JS output: ok=true and row_count > 0.
OK_FLAG="$(echo "$JS_OUTPUT" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("ok"))')"
ROW_COUNT="$(echo "$JS_OUTPUT" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("row_count"))')"
if [[ "$OK_FLAG" != "True" || "$ROW_COUNT" == "0" || -z "$ROW_COUNT" ]]; then
  echo "✗ JS smoke returned unexpected payload" >&2
  exit 2
fi

echo "=== JS-runtime SMOKE PASSED — fetched ${ROW_COUNT} rows from JS via generated db.js ==="
exit 0
