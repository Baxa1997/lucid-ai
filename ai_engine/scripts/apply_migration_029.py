"""Apply migration 029 (chat_sessions.visual_dna JSONB) to Supabase.

Idempotent (uses `ADD COLUMN IF NOT EXISTS`). Pipe in:

    cat supabase/migrations/029_chat_sessions_visual_dna.sql \\
      | docker exec -i lucid-ai-ai_engine-1 \\
          python scripts/apply_migration_029.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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
    / "supabase" / "migrations" / "029_chat_sessions_visual_dna.sql"
)


def _read_migration() -> str:
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
        "scripts/apply_migration_029.py`",
        file=sys.stderr,
    )
    sys.exit(2)


async def main() -> int:
    sql = _read_migration()
    statements = split_sql(sql)
    print(f"→ migration: 029_chat_sessions_visual_dna.sql ({len(sql)} chars)")
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

        # Verify the column landed by selecting it on a synthetic row.
        try:
            await c.table("chat_sessions").select("id, visual_dna").limit(1).execute()
        except Exception as exc:
            print(f"✗ verification: visual_dna column not readable — {exc}",
                  file=sys.stderr)
            return 4

        try:
            await c.rpc("execute_ddl", {"p_sql": "NOTIFY pgrst, 'reload schema';"}).execute()
            print("→ PostgREST schema cache reload signal sent")
        except Exception as exc:
            print(f"⚠ schema-reload NOTIFY failed (non-fatal): {exc}",
                  file=sys.stderr)

    print("✓ migration 029 applied; chat_sessions.visual_dna column live")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
