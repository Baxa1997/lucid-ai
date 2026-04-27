"""backend_schema.py — generate a Supabase migration from project entities.

Why this exists
───────────────
For admin/CRM/dashboard projects, the spec.md describes Supabase tables but
the pipeline never emits actual SQL. Users get a beautiful frontend wired to
mock data with no path to a real backend. This module closes that gap: it
takes the structured ``project_schema["entities"]`` produced by
``project_schema.build_project_schema`` and asks Claude Sonnet to write a
production-ready Postgres migration with RLS policies, foreign keys, and
indexes.

Output goes to ``supabase/migrations/0001_init.sql`` in the workspace, plus
a short README explaining how to apply it. The frontend stays on mock data
in this generation — wiring it to real Supabase is a separate (bigger)
follow-up step we don't take yet.

FAIL-SOFT: every error path returns ``None`` so generation continues even
when the migration call fails. Migration is a nice-to-have, not blocking.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


_MODEL = "claude-sonnet-4-6"
_API_URL = "https://api.anthropic.com/v1/messages"
_MAX_TOKENS = 8000
_TIMEOUT = 90.0


# ── Claude tool schema ─────────────────────────────────────────────────

_MIGRATION_TOOL: dict = {
    "name": "emit_supabase_migration",
    "description": (
        "Emit a complete, runnable Postgres migration for Supabase. "
        "Include CREATE TABLE statements, RLS policies, foreign keys, "
        "indexes, and a final GRANT block."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "sql": {
                "type": "string",
                "description": (
                    "The complete SQL migration. Must be runnable as-is in "
                    "the Supabase SQL editor. No markdown fences, no prose."
                ),
            },
            "tables_summary": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Short list of table names + 1-line purpose, used in the "
                    "README. Example: ``contacts — sales contact records``."
                ),
            },
        },
        "required": ["sql", "tables_summary"],
    },
}


# ── Entity → prompt summary ────────────────────────────────────────────

def _summarize_entities(entities: list[dict]) -> str:
    """Render schema entities as compact prompt-ready text.

    Trims to the fields Claude needs to design the table: name, type,
    required, options. Drops UI-only fields (inList, inForm, placeholder)
    that don't matter for a database schema.
    """
    if not entities:
        return ""
    blocks: list[str] = []
    for ent in entities:
        name = ent.get("name") or "Entity"
        slug = ent.get("slug") or name.lower()
        fields = ent.get("fields") or []
        lines = [f"### {name} (table: {slug})"]
        for f in fields:
            fname = f.get("name", "")
            ftype = f.get("type", "text")
            req = " required" if f.get("required") else ""
            opts = f.get("options") or []
            opt_str = f" enum:[{','.join(str(o) for o in opts)}]" if opts else ""
            lines.append(f"- {fname}: {ftype}{req}{opt_str}")
        # A few sample rows give Claude column-shape grounding without
        # blowing up the prompt. Cap at 2 rows × 200 chars each.
        mock = ent.get("mockData") or []
        if mock:
            sample = mock[0]
            try:
                sample_json = json.dumps(sample)[:200]
                lines.append(f"  sample: {sample_json}")
            except Exception:
                pass
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


# ── Spec helper: pull the Data Model section if present ────────────────

def _extract_data_model_block(spec_text: str) -> str:
    """Return the body of the spec's ``## Data Model`` section, or "".

    The research/spec text may already contain a Supabase schema sketch
    written by Gemini. Feeding it to Claude alongside the entities lets
    Claude reconcile both views — entity-name drift in either source
    becomes obvious in the prompt.
    """
    if not spec_text:
        return ""
    lines = spec_text.split("\n")
    in_section = False
    body: list[str] = []
    for ln in lines:
        s = ln.strip()
        if s.startswith("## "):
            header = s.lstrip("#").strip().lower()
            if in_section:
                break
            if "data model" in header or "database" in header:
                in_section = True
                continue
        elif in_section:
            body.append(ln)
    text = "\n".join(body).strip()
    return text[:4000]  # hard cap — we already have the entity summary


# ── Main entry point ───────────────────────────────────────────────────

async def build_supabase_migration(
    *,
    description: str,
    project_schema: dict,
    spec_text: str,
    api_key: str,
    user_id: Optional[str] = None,
    websocket=None,
) -> Optional[dict]:
    """Generate a Supabase migration from entities + spec data model.

    Returns ``{"sql": str, "tables_summary": list[str]}`` on success, or
    ``None`` on any failure (no entities, Claude error, malformed output).
    Caller should treat None as "skip the schema-write step" and proceed.
    """
    entities = project_schema.get("entities") or []
    if not entities:
        logger.info("backend_schema: no entities in project_schema, skipping")
        return None

    entity_block = _summarize_entities(entities)
    data_model_block = _extract_data_model_block(spec_text)

    system = (
        "You are a senior database engineer who writes safe, idiomatic "
        "Postgres migrations for Supabase. You always include RLS policies, "
        "foreign keys with appropriate ON DELETE behavior, and indexes on "
        "join/filter columns. You never use VARCHAR (use TEXT). You never "
        "skip RLS. You never grant to anon for tables that hold user data."
    )

    user = f"""Generate a complete Supabase migration for this project.

PROJECT: {description[:300]}

ENTITIES (from project schema — these are the source of truth for tables):
{entity_block}

DATA MODEL HINTS (from product spec — use only to inform RLS + relationships;
do NOT add tables that aren't in the entities list above):
{data_model_block or "(no extra hints)"}

REQUIREMENTS — every table MUST have:
1. `id UUID PRIMARY KEY DEFAULT gen_random_uuid()`
2. `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`
3. `updated_at TIMESTAMPTZ NOT NULL DEFAULT now()` with a trigger that updates it
4. `ALTER TABLE ... ENABLE ROW LEVEL SECURITY;`
5. RLS policies for SELECT/INSERT/UPDATE/DELETE. For tables with an
   `owner_id UUID REFERENCES auth.users(id)`: use `auth.uid() = owner_id`.
   For shared/lookup tables (e.g. status_options, tags): use
   `auth.role() = 'authenticated'` for SELECT and admin-only for writes.
6. Foreign keys for any field whose name ends in `_id` and references
   another entity. Use `ON DELETE CASCADE` for child tables, `ON DELETE
   SET NULL` for optional refs.
7. CREATE INDEX on every foreign-key column and on any field used for
   filtering/sorting (status, created_at, owner_id).
8. Use proper Postgres types: TEXT, TIMESTAMPTZ, UUID, NUMERIC(12,2) for
   money, BOOLEAN, JSONB. Never VARCHAR or TIMESTAMP (without time zone).

START THE FILE WITH:
-- =====================================================================
-- Lucid AI — initial schema for {{ project name }}
-- Apply via: Supabase Dashboard → SQL Editor → paste → Run
-- =====================================================================

-- Helper trigger function for updated_at
CREATE OR REPLACE FUNCTION public.set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

END THE FILE WITH the GRANT block:
GRANT USAGE ON SCHEMA public TO authenticated;
GRANT ALL ON ALL TABLES IN SCHEMA public TO authenticated;
GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO authenticated;

Output via the emit_supabase_migration tool. The `sql` field must be the
COMPLETE file ready to paste into Supabase. The `tables_summary` field is
a short list for the README.
"""

    from app.services.llm_retry import (
        call_with_retry, classify_http_error, LLMPermanentError,
    )

    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }
    payload = {
        "model": _MODEL,
        "max_tokens": _MAX_TOKENS,
        "temperature": 0.2,  # SQL must be deterministic-leaning
        "system": system,
        "messages": [{"role": "user", "content": user}],
        "tools": [_MIGRATION_TOOL],
        "tool_choice": {"type": "tool", "name": "emit_supabase_migration"},
    }

    async def _do_call() -> dict:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(_API_URL, headers=headers, json=payload)
        if resp.status_code != 200:
            raise classify_http_error(resp.status_code, resp.text)
        return resp.json()

    try:
        data = await call_with_retry(_do_call, label="backend_schema", websocket=websocket)
    except LLMPermanentError as exc:
        logger.warning("backend_schema permanent failure: %s", exc)
        return None
    except Exception as exc:
        logger.warning("backend_schema failed after retries: %s", exc)
        return None

    # Meter token usage (fire-and-forget)
    try:
        from app.services.billing_meter import report_token_usage
        _u = data.get("usage") or {}
        report_token_usage(
            user_id,
            int(_u.get("input_tokens", 0) or 0),
            int(_u.get("output_tokens", 0) or 0),
            source="backend_schema",
        )
    except Exception:
        pass

    try:
        for block in data.get("content") or []:
            if block.get("type") == "tool_use" and block.get("name") == "emit_supabase_migration":
                result = block.get("input") or {}
                sql = (result.get("sql") or "").strip()
                if not sql or "CREATE TABLE" not in sql.upper():
                    logger.warning("backend_schema: tool returned empty/invalid SQL")
                    return None
                return result
    except Exception as exc:
        logger.warning("backend_schema response parse failed: %s", exc)
    return None


# ── File writer ────────────────────────────────────────────────────────

_README_TEMPLATE = """# Supabase backend

Lucid AI generated this Postgres schema from your product spec. The frontend
in this generation still uses `db.json` for mock data — apply the migration
to get a real backend, then swap the mock fetch calls for Supabase queries.

## Tables

{tables_section}

## How to apply

1. Go to your Supabase project → SQL Editor → New query
2. Paste the contents of `migrations/0001_init.sql`
3. Click Run. RLS is enabled — every authenticated user gets row-level access
   to their own records (any table with `owner_id`)
4. Add your Supabase URL + anon key to a `.env.local` file:
   ```
   NEXT_PUBLIC_SUPABASE_URL=https://YOUR-PROJECT.supabase.co
   NEXT_PUBLIC_SUPABASE_ANON_KEY=eyJ...
   ```
5. Install the client: `npm install @supabase/supabase-js`
6. Replace mock fetches in your code with real Supabase queries

## Future migrations

Add new files as `migrations/0002_*.sql`, `0003_*.sql`, etc. Run them in
order via the same SQL Editor flow, or use the Supabase CLI for automation.
"""


def write_supabase_migration(
    workspace_path: str,
    migration: dict,
) -> bool:
    """Write the migration SQL + README to the workspace.

    Returns True on success, False on any IO error. Never raises.
    """
    try:
        sql = migration.get("sql") or ""
        tables = migration.get("tables_summary") or []
        if not sql:
            return False

        ws = Path(workspace_path)
        mig_dir = ws / "supabase" / "migrations"
        mig_dir.mkdir(parents=True, exist_ok=True)

        sql_path = mig_dir / "0001_init.sql"
        sql_path.write_text(sql, encoding="utf-8")

        tables_section = (
            "\n".join(f"- `{t}`" for t in tables)
            if tables
            else "_See `migrations/0001_init.sql` for the full schema._"
        )
        readme_path = ws / "supabase" / "README.md"
        readme_path.write_text(
            _README_TEMPLATE.format(tables_section=tables_section),
            encoding="utf-8",
        )

        logger.info(
            "backend_schema: wrote %s (%d bytes) + README (%d tables)",
            sql_path.relative_to(ws),
            os.path.getsize(sql_path),
            len(tables),
        )
        return True
    except Exception as exc:
        logger.warning("backend_schema: write failed: %s", exc)
        return False
