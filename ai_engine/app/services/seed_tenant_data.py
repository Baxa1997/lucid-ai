"""Stage 4.7 — Gemini-driven tenant data seeding.

Given the validated `DataModel` (from Stage 4.5) plus the upstream
artifacts (plan + intent + purpose_data), generate plausible initial
rows for every collection in `data_model.tables` and insert them into
the freshly-provisioned tenant schema (Stage 4.6).

Why generated, not lorem-ipsum
-------------------------------
The whole reason these are collections — not singletons — is that they
hold *domain-specific* repeated content (menu items, products, team
members, blog posts). Static lorem-ipsum would make the rendered site
useless for showing the user what their data layer looks like. Gemini
already knows the project's industry, audience, tone, and visual DNA
from upstream stages, so we let it draft realistic rows.

Architecture
------------
Two surfaces:

  • `plan_seed_data(...)`   — calls Gemini once; returns
      ``dict[table_name -> list[dict]]``. Image fields are emitted as
      Unsplash search queries (e.g. "warm italian dining room") so we
      don't bake URLs into the LLM call.

  • `apply_seed_data(...)`  — resolves image-query strings to real
      Unsplash URLs via the existing `image_binding.search_unsplash`,
      then builds escaped INSERT statements and runs them through the
      `public.execute_ddl` RPC. Per-statement transactions; safe to
      retry because the seeder runs once and tables are empty
      beforehand.

Failure mode
------------
Same degrade-gracefully contract as Stage 4.5/4.6: any exception is
caught at the pipeline integration point; the empty-tables state is
the natural fallback (the codegen layer in Phase 2.3.C will fall back
to JSON for empty tables).
"""
from __future__ import annotations

import json
import logging
from typing import Any, Literal, Optional

from pydantic import ValidationError

from app.services.data_model import DataModel, FieldDefinition
from app.services.landing_gemini import structured_distill

logger = logging.getLogger(__name__)


# Gemini model — match the data_model_planner default so seeded data is
# as carefully reasoned as the schema it's filling. Cost is similar.
_GEMINI_MODEL_DEFAULT = "gemini-3.1-pro-preview"


# Output cap. Seed JSON for ~6 tables × ~8 rows × ~10 fields is ~10K
# tokens. Bumping past the 8K default keeps Pro from running out of
# output budget mid-row, which would corrupt the JSON.
_MAX_OUTPUT_TOKENS = 32768

# Pro models need a generous thinking budget — same rationale as in
# data_model_planner. The seeder reasons about each table individually,
# so it benefits more than the planner from extra thinking room.
_THINKING_BUDGET = 8192

# Seeder Gemini timeout — generous because Pro thinking can take 60-90s
# on multi-table fixtures.
_GEMINI_TIMEOUT_S = 180.0


# Image-field types that should be resolved to Unsplash URLs at apply
# time. Other field types are inserted verbatim.
_IMAGE_FIELD_TYPES = frozenset({"image_url"})


# Rows per table guidance to Gemini. Hard ceiling enforced at parse
# time so a runaway LLM emission can't blow up an insert.
_MIN_ROWS_PER_TABLE = 3
_MAX_ROWS_PER_TABLE = 12


# ── Prompt ────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are a content writer seeding a freshly-built marketing website's
database with realistic initial rows.

For each table in the data model, generate REALISTIC, DOMAIN-SPECIFIC
seed rows. The rows should look like what a real business in this
industry would have on day one — not lorem ipsum, not generic
placeholders.

RULES:
- Generate between 4 and 10 rows per table. Use judgment: a restaurant
  has ~8 menu items, a portfolio has ~6 projects, a blog has ~5 posts.
- Field values must match each field's `type`:
    text     → free-form short string
    number   → decimal (e.g. 12.50 for prices in dollars)
    integer  → whole number (e.g. 1295 for prices in cents)
    boolean  → true or false (native JSON bool)
    date     → "YYYY-MM-DD"
    datetime → "YYYY-MM-DD HH:MM:SS"
    email    → real-looking email
    phone    → "+1-555-XXX-XXXX" style
    url      → full https:// URL
    image_url→ an Unsplash search query (NOT a URL!), 3-6 words
               describing the image, e.g. "warm cozy italian dining room"
    json     → nested JSON value
- Respect `required` (must not be null) and `enum_values` (must be one of).
- Respect `max_length` — keep text fields under the cap.
- Make rows internally consistent: a "menu_items" table with category
  field should mix starters/mains/desserts; a "team_members" table
  should have varied titles, not all "CEO".
- Use the BUSINESS CONTEXT to pick names, prices, locations, and tone
  appropriately.
- DO NOT include `id`, `created_at`, `updated_at` — those are filled
  in by Postgres.
"""


def _serialize_table(table) -> dict:
    """Compact JSON-friendly view of a table for the Gemini prompt."""
    return {
        "name":           table.name,
        "singular_label": table.singular_label,
        "plural_label":   table.plural_label,
        "description":    table.description,
        "fields": [
            {
                "name":        f.name,
                "type":        f.type,
                "required":    f.required,
                "max_length":  f.max_length,
                "enum_values": f.enum_values,
                "description": f.description,
            }
            for f in table.fields
        ],
    }


def _build_user_prompt(
    data_model: DataModel,
    website_plan: dict,
    intent: dict,
    purpose_data: dict,
) -> str:
    tables_json = json.dumps(
        [_serialize_table(t) for t in data_model.tables],
        ensure_ascii=False, indent=2,
    )

    industry        = (intent.get("business_category")
                       or purpose_data.get("industry") or "general")
    geographic      = (intent.get("geographic_specifics")
                       or intent.get("geographic_scope") or "unspecified")
    tone            = intent.get("tone") or "neutral"
    primary_purpose = purpose_data.get("primary_purpose") or "brand_awareness"
    target_audience = purpose_data.get("target_audience") or "general"
    brand_name      = (website_plan.get("brand") or {}).get("name") or "the business"

    return f"""\
Generate seed rows for the data model below.

BRAND: {brand_name}
BUSINESS CONTEXT:
- Industry: {industry}
- Primary purpose: {primary_purpose}
- Target audience: {target_audience}
- Geographic scope: {geographic}
- Tone: {tone}

TABLES TO SEED:
{tables_json}

Output a JSON object where each key is a table name and each value is
an array of rows. Each row is an object whose keys are the table's
field names (excluding id/created_at/updated_at).

Example shape (illustrative — your output must match the actual schema
above):
{{
  "menu_items": [
    {{"name": "Carbonara", "description": "...", "price_cents": 1850,
      "category": "main", "is_available": true,
      "photo_url": "rustic italian pasta carbonara dim lighting"}}
  ],
  "team_members": [
    {{"full_name": "Marco Bianchi", "title": "Head Chef",
      "bio": "...", "photo_url": "italian chef portrait kitchen"}}
  ]
}}

Return ONLY the JSON. No markdown fences, no commentary.
"""


# ── Main entry point ──────────────────────────────────────────────────

async def plan_seed_data(
    data_model: DataModel,
    website_plan: dict,
    intent: dict,
    purpose_data: dict,
    *,
    gemini_key: str = "",
    project_id: str = "",
    model: str = _GEMINI_MODEL_DEFAULT,
) -> dict[str, list[dict]]:
    """Return ``{table_name: [row, row, ...]}`` for every table in the model.

    On Gemini failure or parse failure, returns ``{}`` — the caller
    should treat an empty dict as "no seed data, continue with empty
    tables" (Phase 2.3.C codegen will fall back to JSON for empty
    tables anyway).

    `project_id` is used for logging only.
    """
    if not data_model.tables:
        logger.info(
            "seed_tenant_data: data_model has no tables — skipping seed for project %s",
            project_id or "<no-id>",
        )
        return {}

    user_prompt = _build_user_prompt(
        data_model, website_plan, intent, purpose_data,
    )
    full_prompt = _SYSTEM_PROMPT + "\n\n" + user_prompt

    label = f"seed_tenant_data_{project_id or 'noid'}"
    try:
        raw = await structured_distill(
            prompt=full_prompt,
            timeout_s=_GEMINI_TIMEOUT_S,
            label=label,
            response_schema=None,
            max_tokens=_MAX_OUTPUT_TOKENS,
            temperature=0.3,
            model=model,
            thinking_budget=_THINKING_BUDGET,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "seed_tenant_data: Gemini call failed (project=%s): %s",
            project_id or "<no-id>", exc,
        )
        return {}

    parsed = _try_parse_seed_blob(raw)
    if not isinstance(parsed, dict):
        logger.warning(
            "seed_tenant_data: parse failed (project=%s): %s",
            project_id or "<no-id>", parsed,
        )
        return {}

    cleaned = _validate_and_clean(parsed, data_model)
    logger.info(
        "seed_tenant_data: generated rows — %s",
        {k: len(v) for k, v in cleaned.items()},
    )
    return cleaned


def _try_parse_seed_blob(raw: str) -> "dict | str":
    """Parse Gemini output, tolerating a stray markdown fence."""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
        text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        return f"JSON parse error: {exc}"


def _validate_and_clean(
    seed_blob: dict, data_model: DataModel,
) -> dict[str, list[dict]]:
    """Filter the Gemini output down to known tables/fields and clamp row counts.

    The aim is to be permissive about what Gemini emits — drop unknown
    keys (table or field), clamp row counts to a safe upper bound,
    keep the rest. Validation that *would* break the INSERT (e.g.
    wrong-type values, missing required fields) is handled at the
    apply step where the SQL builder has full type context.
    """
    known_tables = {t.name: t for t in data_model.tables}
    cleaned: dict[str, list[dict]] = {}

    for table_name, rows in seed_blob.items():
        if table_name not in known_tables:
            logger.info(
                "seed_tenant_data: dropping unknown table %r from seed output",
                table_name,
            )
            continue
        if not isinstance(rows, list):
            logger.warning(
                "seed_tenant_data: rows for %r is not a list (%s); skipping",
                table_name, type(rows).__name__,
            )
            continue

        known_field_names = {f.name for f in known_tables[table_name].fields}
        kept_rows: list[dict] = []
        for row in rows[:_MAX_ROWS_PER_TABLE]:
            if not isinstance(row, dict):
                continue
            kept_rows.append({
                k: v for k, v in row.items() if k in known_field_names
            })
        cleaned[table_name] = kept_rows

    return cleaned


# ── Apply (resolve images, build INSERTs, execute) ────────────────────

async def apply_seed_data(
    seed_data: dict[str, list[dict]],
    data_model: DataModel,
    tenant_schema: str,
    admin_client,
    *,
    image_search,  # async (query, count) -> [{"url": ...}, ...]
) -> dict:
    """Insert seed rows into the tenant schema.

    `image_search` is injected (not imported) so tests can substitute a
    deterministic fake. In production, pass
    `app.services.image_binding.search_unsplash`.

    Returns::

        {
          "success":          bool,
          "tables_inserted":  int,
          "rows_inserted":    int,
          "failed_table":     str | None,
          "error":            str | None,
        }
    """
    tables_inserted = 0
    rows_inserted = 0

    by_name = {t.name: t for t in data_model.tables}

    for table_name, rows in seed_data.items():
        if not rows:
            continue
        table = by_name.get(table_name)
        if table is None:
            # Already filtered upstream; defensive.
            continue

        image_field_names = [
            f.name for f in table.fields if f.type in _IMAGE_FIELD_TYPES
        ]
        if image_field_names:
            await _resolve_image_fields(
                rows, image_field_names, image_search,
            )

        try:
            sql = build_insert_sql(table, tenant_schema, rows)
        except Exception as exc:  # noqa: BLE001
            return {
                "success":         False,
                "tables_inserted": tables_inserted,
                "rows_inserted":   rows_inserted,
                "failed_table":    table_name,
                "error":           f"build_insert_sql failed: {exc}",
            }

        try:
            await admin_client.rpc(
                "execute_ddl", {"p_sql": sql},
            ).execute()
        except Exception as exc:  # noqa: BLE001 — surface postgrest errors
            return {
                "success":         False,
                "tables_inserted": tables_inserted,
                "rows_inserted":   rows_inserted,
                "failed_table":    table_name,
                "error":           f"INSERT failed: {exc}",
            }
        tables_inserted += 1
        rows_inserted += len(rows)

    return {
        "success":         True,
        "tables_inserted": tables_inserted,
        "rows_inserted":   rows_inserted,
        "failed_table":    None,
        "error":           None,
    }


async def _resolve_image_fields(
    rows: list[dict],
    image_field_names: list[str],
    image_search,
) -> None:
    """Replace Unsplash-search-query strings with real URLs in-place.

    Walks the rows; for each image field whose current value looks like
    a search query (any non-empty string that isn't already an HTTPS
    URL), calls ``image_search(query, count=1)`` and replaces the value
    with the first returned URL. On lookup failure we leave the
    original string in place — the INSERT will still succeed because
    `image_url` is just TEXT at the SQL layer, but the rendered site
    will show whatever string came back. Logged at WARNING so it's
    diagnosable.
    """
    for row in rows:
        for fname in image_field_names:
            val = row.get(fname)
            if not isinstance(val, str) or not val.strip():
                continue
            if val.startswith("https://") or val.startswith("http://"):
                # Already a URL; leave alone.
                continue
            try:
                results = await image_search(val, count=1)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "seed_tenant_data: image_search threw for %r — %s",
                    val, exc,
                )
                continue
            if results and isinstance(results, list):
                first = results[0]
                if isinstance(first, dict) and first.get("url"):
                    row[fname] = first["url"]


# ── SQL building ──────────────────────────────────────────────────────

def build_insert_sql(table, tenant_schema: str, rows: list[dict]) -> str:
    """Render an `INSERT INTO tenant_xxx.<table> (...) VALUES (...), ...;`.

    Skips columns that no row in the batch provides — keeps the column
    list aligned with the values list. Each value is escaped per-type
    so Postgres parses it correctly. Strings get single-quote doubling
    (`'` → `''`); numbers / bools / NULL pass through.

    Raises ValueError on empty input — caller is expected to guard.
    """
    if not rows:
        raise ValueError("build_insert_sql: rows is empty")

    # Field lookup by name + the column set we'll actually emit (only
    # columns that any row provides).
    field_by_name = {f.name: f for f in table.fields}
    columns_used: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for col in row.keys():
            if col not in seen and col in field_by_name:
                seen.add(col)
                columns_used.append(col)

    if not columns_used:
        raise ValueError(
            f"build_insert_sql: no usable columns in {table.name!r} rows"
        )

    cols_csv = ", ".join(columns_used)
    values_clauses: list[str] = []
    for row in rows:
        rendered = ", ".join(
            _format_sql_value(row.get(col), field_by_name[col])
            for col in columns_used
        )
        values_clauses.append(f"({rendered})")

    qualified = f"{tenant_schema}.{table.name}"
    return (
        f"INSERT INTO {qualified} ({cols_csv}) VALUES\n  "
        + ",\n  ".join(values_clauses)
        + ";"
    )


def _format_sql_value(value: Any, field: FieldDefinition) -> str:
    """Render `value` as a SQL literal appropriate for `field.type`.

    Strings get single-quote doubling. Booleans render as TRUE/FALSE.
    None always → NULL (the DDL's NOT NULL constraint will catch
    missing required fields rather than silently inserting empty
    strings).
    """
    if value is None:
        return "NULL"

    ftype = field.type
    if ftype == "boolean":
        # Tolerant of "true"/"True"/1 emissions
        if isinstance(value, bool):
            return "TRUE" if value else "FALSE"
        if isinstance(value, (int, float)):
            return "TRUE" if value else "FALSE"
        if isinstance(value, str):
            return "TRUE" if value.strip().lower() in ("true", "1", "yes") else "FALSE"
        return "FALSE"

    if ftype in ("integer", "number"):
        if isinstance(value, (int, float)):
            return str(value)
        # Coerce strings — drop currency / separators if Gemini emits them.
        try:
            cleaned = (
                str(value).strip()
                .replace("$", "").replace(",", "").replace(" ", "")
            )
            return str(float(cleaned)) if ftype == "number" else str(int(float(cleaned)))
        except (ValueError, TypeError):
            return "NULL"

    if ftype == "json":
        try:
            return "'" + json.dumps(value, ensure_ascii=False).replace("'", "''") + "'::jsonb"
        except (TypeError, ValueError):
            return "NULL"

    # Everything else (text / email / phone / url / image_url / date /
    # datetime / enum) is a string literal.
    s = str(value).replace("'", "''")
    return f"'{s}'"


# Re-export for tests
RESERVED_ROW_FIELDS = frozenset({"id", "created_at", "updated_at"})
