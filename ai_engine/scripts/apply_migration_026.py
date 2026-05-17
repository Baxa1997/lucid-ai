"""Apply migration 026 to the configured Supabase project.

Idempotent: the migration itself uses `CREATE OR REPLACE FUNCTION`
everywhere, so re-running is safe and necessary after any in-place
edit to `026_set_tenant_row.sql`.

Why a script instead of pasting into the Dashboard SQL Editor:
the migration has 5 distinct CREATE FUNCTION blocks. Pasting works
but is error-prone — easy to miss one. This script splits the file
into PL/pgSQL-safe chunks, runs each via the existing service-role
`execute_ddl` RPC (migration 024), and prints a per-chunk status.

Usage:
    docker exec -e OPENHANDS_SUPPRESS_BANNER=1 \\
        lucid-ai-ai_engine-1 python scripts/apply_migration_026.py

Verification (after):
    SELECT routine_name FROM information_schema.routines
    WHERE routine_schema = 'public'
      AND routine_name IN ('set_tenant_row',
                            'update_tenant_row',
                            'delete_tenant_row');
    → expected 3 rows.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Shared with apply_migration_027.py — see app/services/sql_splitter.py.
from app.services.sql_splitter import split_sql  # noqa: E402


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


# When this script runs inside the ai_engine docker container, the
# repo's `supabase/migrations/` directory is NOT bind-mounted (only
# `./ai_engine:/app` is). So we accept the SQL via stdin instead of
# trying to resolve a host-side path. The shell wrapper below pipes
# the file in for us.
#
# Fallback: if stdin is empty AND we're running on the host (not in
# the container), look for the file at the expected repo location.
_HOST_FILE = (
    Path(__file__).resolve().parents[2]
    / "supabase" / "migrations" / "026_set_tenant_row.sql"
)


def _read_migration() -> str:
    """Read SQL from stdin if piped, else from the host-side path."""
    if not sys.stdin.isatty():
        data = sys.stdin.read()
        if data.strip():
            return data
    if _HOST_FILE.is_file():
        return _HOST_FILE.read_text(encoding="utf-8")
    print(
        "ERROR: no migration on stdin and "
        f"{_HOST_FILE} not found.\n"
        "Pipe the file in: `cat ... | docker exec -i ... python "
        "scripts/apply_migration_026.py`",
        file=sys.stderr,
    )
    sys.exit(2)


# Splitter lives in app.services.sql_splitter — see the import above.


async def main() -> int:
    sql = _read_migration()
    statements = split_sql(sql)
    print(f"→ migration: 026_set_tenant_row.sql ({len(sql)} chars)")
    print(f"→ {len(statements)} top-level statement(s) to apply")

    from app.supabase_client import managed_admin_client
    async with managed_admin_client() as c:
        for idx, stmt in enumerate(statements, start=1):
            # Show a one-line preview so a failure in stmt N is easy to
            # locate in the source file.
            preview = " ".join(stmt.split())[:80]
            print(f"   [{idx:>2}/{len(statements)}] {preview} …")
            try:
                await c.rpc("execute_ddl", {"p_sql": stmt}).execute()
            except Exception as exc:
                print(f"   ✗ statement {idx} failed: {exc}", file=sys.stderr)
                print(f"   statement:\n{stmt}", file=sys.stderr)
                return 3

    # Verification — confirm each target function is callable. We
    # can't query information_schema through PostgREST (it isn't in
    # the exposed schemas list), so probe each RPC instead. Service-
    # role calls have NULL auth.uid(), which triggers `access_denied`
    # — that response proves the function exists AND its auth check
    # ran. Anything else (a "function does not exist" PostgREST 404,
    # for example) means the apply step missed a function.
    from uuid import uuid4
    probes = {
        "set_tenant_row":    {
            "p_project_id": str(uuid4()),
            "p_table_name": "smoke_probe",
            "p_payload":    {},
        },
        "update_tenant_row": {
            "p_project_id": str(uuid4()),
            "p_table_name": "smoke_probe",
            "p_row_id":     str(uuid4()),
            "p_payload":    {},
        },
        "delete_tenant_row": {
            "p_project_id": str(uuid4()),
            "p_table_name": "smoke_probe",
            "p_row_id":     str(uuid4()),
        },
    }

    bad = []
    async with managed_admin_client() as c:
        for fn, args in probes.items():
            try:
                await c.rpc(fn, args).execute()
                # We DON'T expect this to succeed — service-role has
                # no auth.uid(). If it does succeed, that's also a
                # signal the function is wired (returns gracefully).
            except Exception as exc:
                msg = str(exc).lower()
                # The auth-check raises 'access_denied'. Any other
                # error class means the function probably doesn't
                # exist or is mis-wired.
                if "access_denied" in msg or "insufficient_privilege" in msg:
                    continue
                # Postgres signals "no such function" via 42883; the
                # PostgREST wrapper carries that through.
                if "42883" in msg or "does not exist" in msg:
                    bad.append(fn)
                # Anything else (e.g. the table validation kicking in
                # because we use a snake_case probe) means the
                # function ran — accept it.
                continue

    if bad:
        print(f"✗ verification: these RPCs appear missing: {bad}",
              file=sys.stderr)
        return 4

    print("✓ migration 026 applied; all three write RPCs reachable via PostgREST")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
