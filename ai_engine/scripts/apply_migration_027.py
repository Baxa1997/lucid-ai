"""Apply migration 027 to the configured Supabase project.

Idempotent: the migration uses `CREATE OR REPLACE FUNCTION` so
re-running is safe (including after in-place edits).

Stdin-driven: this script runs inside the ai_engine docker container,
where the `supabase/` directory is NOT bind-mounted. Pipe the file
in instead:

    cat supabase/migrations/027_get_tenant_collection_authenticated.sql \\
      | docker exec -i lucid-ai-ai_engine-1 \\
          python scripts/apply_migration_027.py

After applying, this script also sends a `NOTIFY pgrst, 'reload
schema'` so PostgREST picks up the new RPC immediately (instead of
waiting for its periodic auto-reload).
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Shared with apply_migration_026.py.
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


_HOST_FILE = (
    Path(__file__).resolve().parents[2]
    / "supabase" / "migrations" / "027_get_tenant_collection_authenticated.sql"
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
        "scripts/apply_migration_027.py`",
        file=sys.stderr,
    )
    sys.exit(2)


async def main() -> int:
    sql = _read_migration()
    statements = split_sql(sql)
    print(f"→ migration: 027_get_tenant_collection_authenticated.sql ({len(sql)} chars)")
    print(f"→ {len(statements)} top-level statement(s) to apply")

    from app.supabase_client import managed_admin_client
    async with managed_admin_client() as c:
        for idx, stmt in enumerate(statements, start=1):
            preview = " ".join(stmt.split())[:80]
            print(f"   [{idx:>2}/{len(statements)}] {preview} …")
            try:
                await c.rpc("execute_ddl", {"p_sql": stmt}).execute()
            except Exception as exc:
                print(f"   ✗ statement {idx} failed: {exc}", file=sys.stderr)
                print(f"   statement:\n{stmt}", file=sys.stderr)
                return 3

    # Functional probe — call the RPC with a missing project and
    # bogus args; we expect a domain error ('project_not_found' or
    # 'invalid_*'), NOT "function does not exist". Service-role
    # has auth.uid() = NULL so we'd ordinarily get access_denied
    # first; but the cheap arg-shape guards fire BEFORE that, so a
    # bad direction is the easiest way to confirm the function ran.
    from uuid import uuid4
    async with managed_admin_client() as c:
        try:
            await c.rpc(
                "get_tenant_collection_authenticated",
                {
                    "p_project_id":      str(uuid4()),
                    "p_table_name":      "smoke_probe",
                    "p_order_direction": "neither",   # → invalid_order_direction
                },
            ).execute()
        except Exception as exc:
            msg = str(exc).lower()
            if "42883" in msg or "does not exist" in msg:
                print(f"✗ verification: RPC appears missing — {exc}",
                      file=sys.stderr)
                return 4
            # Anything else (e.g. invalid_order_direction) means the
            # function exists and ran. That's success.

        # Tell PostgREST to reload its in-memory schema cache so the
        # new RPC is callable through the REST API immediately. The
        # service-role admin client routes through PostgREST too, so
        # without this we'd see "function not found" from anon /
        # authenticated callers until the next auto-reload (~30s).
        try:
            await c.rpc("execute_ddl", {"p_sql": "NOTIFY pgrst, 'reload schema';"}).execute()
            print("→ PostgREST schema cache reload signal sent")
        except Exception as exc:
            print(f"⚠ schema-reload NOTIFY failed (non-fatal): {exc}",
                  file=sys.stderr)

    print("✓ migration 027 applied; get_tenant_collection_authenticated reachable")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
