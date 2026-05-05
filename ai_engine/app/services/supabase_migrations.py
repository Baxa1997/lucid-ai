"""Apply SQL migrations to a customer's Supabase project via the Mgmt API.

Covers two operations the rest of the pipeline needs:

  apply_per_project_migrations(ref) — run every file in
      supabase/migrations/per_project/*.sql against the named customer
      project. Idempotent (every migration uses CREATE … IF NOT EXISTS,
      DROP POLICY IF EXISTS, etc.) so re-running is a no-op.

  run_sql(ref, sql, *, label) — single-statement helper used by step-3+
      seeding code that has to insert rows.

Endpoint: POST https://api.supabase.com/v1/projects/{ref}/database/query
The Mgmt API runs the body as a single SQL string; multiple statements
are allowed but they share one transaction. Errors return 4xx with the
Postgres message in the body.

Pure async I/O via httpx. All failures raise SupabaseMgmtError so the
generation pipeline's existing error handling can act on them.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import httpx

from app.services.supabase_mgmt import SupabaseMgmtError, _headers, _API_BASE

logger = logging.getLogger(__name__)

# supabase/migrations/per_project/ relative to repo root. Resolved at module
# load time so callers can't accidentally point us at the wrong folder.
_REPO_ROOT = Path(__file__).resolve().parents[3]  # ai_engine/app/services -> ai_engine -> repo
_PER_PROJECT_MIGRATIONS_DIR = _REPO_ROOT / "supabase" / "migrations" / "per_project"


def _list_migration_files() -> list[Path]:
    """Return every per-project migration file, sorted by filename so the
    numeric prefix dictates execution order (001_, 002_, …)."""
    if not _PER_PROJECT_MIGRATIONS_DIR.is_dir():
        raise SupabaseMgmtError(
            f"Per-project migrations directory not found: {_PER_PROJECT_MIGRATIONS_DIR}",
            context={"path": str(_PER_PROJECT_MIGRATIONS_DIR)},
        )
    return sorted(_PER_PROJECT_MIGRATIONS_DIR.glob("*.sql"))


async def run_sql(
    ref: str,
    sql: str,
    *,
    label: str | None = None,
    timeout: float = 120.0,
) -> Any:
    """Execute one SQL string against a customer project's database. Returns
    whatever the Mgmt API echoed back (usually a list of rows for SELECTs,
    None / empty for DDL).

    `label` is a human tag used purely for logging — not required for
    correctness, but makes "which migration failed" trivial to spot in
    structured logs.
    """
    if not sql or not sql.strip():
        raise SupabaseMgmtError("run_sql: empty SQL", context={"ref": ref, "label": label})
    url = f"{_API_BASE}/v1/projects/{ref}/database/query"
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            resp = await client.post(url, headers=_headers(), json={"query": sql})
        except httpx.HTTPError as exc:
            raise SupabaseMgmtError(
                f"Network error executing SQL on {ref}: {exc}",
                context={"ref": ref, "label": label, "exc": str(exc)},
            ) from exc

    if resp.status_code not in (200, 201):
        body_excerpt = (resp.text or "")[:600]
        raise SupabaseMgmtError(
            f"SQL execution failed on {ref} ({label or 'unlabeled'}): "
            f"HTTP {resp.status_code}: {body_excerpt}",
            context={
                "ref": ref,
                "label": label,
                "status": resp.status_code,
                "body": body_excerpt,
                "sql_excerpt": sql[:200],
            },
        )

    if not resp.content:
        return None
    try:
        return resp.json()
    except ValueError:
        # 200 with non-JSON body (e.g. empty DDL response) — treat as success.
        return None


async def apply_per_project_migrations(ref: str) -> list[str]:
    """Apply every migration in supabase/migrations/per_project/ to the
    given customer project, in lexical filename order. Returns the list
    of applied migration names so the caller can include them in
    progress events.

    Each file is run as one Mgmt API call → one Postgres transaction. If
    a file fails mid-way, that file's statements roll back, but earlier
    files have already committed. That's acceptable because every migration
    is written to be idempotent — re-running picks up where it stopped.
    """
    files = _list_migration_files()
    if not files:
        logger.warning("No per-project migrations found in %s", _PER_PROJECT_MIGRATIONS_DIR)
        return []

    applied: list[str] = []
    for path in files:
        sql = path.read_text(encoding="utf-8")
        logger.info("Applying migration %s to project %s (%d bytes)", path.name, ref, len(sql))
        await run_sql(ref, sql, label=path.name)
        applied.append(path.name)
        logger.info("Applied %s to %s", path.name, ref)
    return applied
