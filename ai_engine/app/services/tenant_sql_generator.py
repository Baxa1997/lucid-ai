"""Deterministic SQL generator: DataModel → tenant-schema DDL.

No LLM. No state. Pure functions you can reason about by reading the
spec. The output of `generate_tenant_sql(model, schema, project_id)` is
a complete script that, when executed against an already-provisioned
tenant schema, materializes every table, index, trigger, RLS policy,
and grant the model implies.

Contract assumptions (enforced upstream)
----------------------------------------
* `tenant_schema` is a snake_case identifier already created by
  `provision_tenant_schema` (migration 023). Trusted — not re-quoted.
* `project_id` is a real UUID present in `chat_sessions.id`. Trusted —
  embedded as a literal string in RLS policies.
* `model` passed `validate_data_model()` upstream. Names are snake_case,
  no duplicates, types are in the closed set — so identifiers can be
  interpolated without re-validation here.
* Migration 024 has been applied (provides `public.set_updated_at()`
  and `public.execute_ddl()`).

What this module does NOT do
----------------------------
* Generate DROP statements (use `drop_tenant_schema` RPC).
* Emit foreign keys (deferred to v1.1 — use `*_slug` columns for soft
  references in the meantime; see DATA_MODEL_FORMAT.md).
* Seed data — that's a separate pipeline step.
* Accept arbitrary SQL fragments from callers — every interpolation is
  either a snake_case identifier from a validated DataModel, an enum
  value (single-quote-escaped), or a UUID literal.
"""
from __future__ import annotations

import datetime as _dt
import re
from typing import Optional

from app.services.data_model import (
    DataModel,
    FieldDefinition,
    TableDefinition,
)


# ── Reserved columns ──────────────────────────────────────────────────
# Every generated table gets these for free. Emitters MUST NOT include
# them in their DataModel — that's a bug that should surface early.
RESERVED_COLUMN_NAMES = frozenset({"id", "created_at", "updated_at"})


# ── Type mapping ──────────────────────────────────────────────────────
# Closed set. Adding a type requires touching both `data_model.FieldType`
# and this dict. Stays as a module-level constant so tests can introspect
# it directly.
_TYPE_MAPPING: dict[str, str] = {
    "text":      "TEXT",
    "number":    "NUMERIC(12,2)",
    "integer":   "BIGINT",
    "boolean":   "BOOLEAN",
    "date":      "DATE",
    "datetime":  "TIMESTAMPTZ",
    "json":      "JSONB",
    "image_url": "TEXT",
    "email":     "TEXT",
    "phone":     "TEXT",
    "url":       "TEXT",
}


def field_to_sql_type(field: FieldDefinition) -> str:
    """Return the SQL column type for a field.

    Raises KeyError on unknown types — defensive, shouldn't happen with
    a Pydantic-validated FieldDefinition. The error message names the
    bad type so a stack trace is enough to debug an emitter regression.
    """
    try:
        return _TYPE_MAPPING[field.type]
    except KeyError:
        raise KeyError(
            f"field_to_sql_type: unknown FieldDefinition.type={field.type!r} "
            "(extend _TYPE_MAPPING and data_model.FieldType together)"
        )


# ── Check constraints ─────────────────────────────────────────────────

# Permissive URL pattern — allows https://, http://, /relative, data:.
# Wide on purpose: pipelines may emit data-URIs for inline SVG icons,
# and relative URLs for in-site links. Tightening this is a v1.1 concern.
_URL_REGEX = "'^(https?://|/|data:)'"

# Loose email pattern — not RFC strict. Catches obvious mistakes
# (missing @, missing dot, whitespace inside) without rejecting valid
# odd-but-real addresses. RFC-strict validation belongs in the app
# layer; the DB CHECK is a defense-in-depth net.
_EMAIL_REGEX = "'^[^@[:space:]]+@[^@[:space:]]+\\.[^@[:space:]]+$'"

# Types where `length(...)` makes sense for max_length enforcement.
_TEXTUAL_TYPES = frozenset({"text", "image_url", "email", "phone", "url"})


def _escape_sql_literal(value: str) -> str:
    """Postgres single-quote-string escaping: ' → ''."""
    return value.replace("'", "''")


def field_check_constraint(field: FieldDefinition, table_name: str) -> Optional[str]:
    """Return a CHECK clause for the field, or None when there's nothing
    to enforce.

    Precedence (only one constraint is emitted per field):
      1. enum_values  — closed set membership
      2. URL pattern  — for image_url / url
      3. Email pattern — for email
      4. Length cap   — for textual types with max_length

    Numeric / boolean / json types fall through to None.

    `table_name` is currently unused. It's accepted for API stability —
    a future v1.1 may emit per-table constraint names like
    `CHECK (...) /* {table_name}_email_format */` for easier debugging.
    """
    del table_name  # reserved for future use

    if field.enum_values:
        escaped = ", ".join(
            f"'{_escape_sql_literal(v)}'" for v in field.enum_values
        )
        return f"CHECK ({field.name} IS NULL OR {field.name} IN ({escaped}))"

    if field.type in ("image_url", "url"):
        return f"CHECK ({field.name} IS NULL OR {field.name} ~ {_URL_REGEX})"

    if field.type == "email":
        return f"CHECK ({field.name} IS NULL OR {field.name} ~* {_EMAIL_REGEX})"

    if field.max_length is not None and field.type in _TEXTUAL_TYPES:
        return (
            f"CHECK ({field.name} IS NULL OR length({field.name}) "
            f"<= {int(field.max_length)})"
        )

    return None


# ── Column DDL ────────────────────────────────────────────────────────

# Forms a `field.default` string can take that are safe to emit
# verbatim into a SQL `DEFAULT <expr>` clause. Anything outside this
# set gets auto-quoted as a string literal — see
# `_default_to_sql_expr` for the rationale.
_NUMERIC_DEFAULT_RE  = re.compile(r"^-?\d+(\.\d+)?$")
_FUNCTION_CALL_RE    = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\s*\(.*\)$", re.DOTALL)
_BARE_SQL_KEYWORDS   = frozenset({
    "TRUE", "FALSE", "NULL",
    "CURRENT_DATE", "CURRENT_TIME", "CURRENT_TIMESTAMP",
    "LOCALTIME", "LOCALTIMESTAMP",
})


def _default_to_sql_expr(field: FieldDefinition) -> str:
    """Render `field.default` as a Postgres-safe DEFAULT expression.

    Gemini sometimes emits bare identifiers like ``"default": "pending"``
    for string-type fields, expecting them to be string literals. The
    SQL parser instead reads ``DEFAULT pending`` as a column reference
    (Postgres error 0A000: "cannot use column reference in DEFAULT
    expression"). Auto-quote those without breaking the legitimate
    forms — numbers, booleans, NULL, quoted strings, function calls,
    and keyword constants.

    Caller must have already checked that `field.default is not None`.
    """
    raw = field.default
    assert raw is not None  # caller responsibility

    stripped = raw.strip()
    if not stripped:
        # Empty string after strip → emit empty string literal, not
        # raw `DEFAULT ` which is a syntax error.
        return "''"

    # Already-quoted string literal — keep as-is (also catches
    # quoted-string-with-cast like "'foo'::text").
    if stripped.startswith("'"):
        return stripped

    # Numeric.
    if _NUMERIC_DEFAULT_RE.match(stripped):
        return stripped

    # Boolean / NULL / current_* keyword constants.
    if stripped.upper() in _BARE_SQL_KEYWORDS:
        return stripped

    # Function call: identifier followed by parentheses.
    if _FUNCTION_CALL_RE.match(stripped):
        return stripped

    # Fall-through: assume Gemini meant a string literal. Quote and
    # SQL-escape it.
    escaped = stripped.replace("'", "''")
    return f"'{escaped}'"


def column_to_ddl(field: FieldDefinition, table_name: str) -> str:
    """Render one column line for a CREATE TABLE.

    The parts compose in a stable order: name → type → NOT NULL → DEFAULT
    → CHECK. Reordering breaks no test, but stability makes diffs of
    regenerated SQL readable.

    Defaults are normalized via `_default_to_sql_expr` so that bare
    identifiers (e.g. Gemini emits `"default": "pending"` instead of
    `"'pending'"`) get auto-quoted into valid string literals.
    """
    parts: list[str] = [field.name, field_to_sql_type(field)]

    if field.required:
        parts.append("NOT NULL")

    if field.default is not None:
        parts.append(f"DEFAULT {_default_to_sql_expr(field)}")

    check = field_check_constraint(field, table_name)
    if check:
        parts.append(check)

    return " ".join(parts)


# ── RLS policies ──────────────────────────────────────────────────────

def rls_policies_for_table(
    table_name: str,
    tenant_schema: str,
    project_id: str,
    public_read: bool,
) -> str:
    """Emit RLS setup + policies for one table.

    Policies emitted:
      • ENABLE ROW LEVEL SECURITY on the table.
      • `<table>_public_read` SELECT policy (only when public_read=True).
      • `<table>_member_insert` / `_member_update` / `_member_delete` —
        gated on `EXISTS (SELECT 1 FROM project_members ...)` for the
        owning project.

    Each CREATE POLICY is preceded by DROP POLICY IF EXISTS so the whole
    script is idempotent across re-runs.
    """
    qualified = f"{tenant_schema}.{table_name}"

    # `member_check` is the body of the project_members EXISTS clause.
    # Built once and reused across INSERT/UPDATE/DELETE policies.
    member_check = (
        f"EXISTS (\n"
        f"      SELECT 1 FROM public.project_members\n"
        f"       WHERE project_id = '{project_id}'::uuid\n"
        f"         AND user_id = auth.uid()\n"
        f"    )"
    )

    parts: list[str] = [
        f"ALTER TABLE {qualified} ENABLE ROW LEVEL SECURITY;",
    ]

    if public_read:
        parts.append(
            f"DROP POLICY IF EXISTS {table_name}_public_read ON {qualified};\n"
            f"CREATE POLICY {table_name}_public_read\n"
            f"    ON {qualified}\n"
            f"    FOR SELECT\n"
            f"    USING (true);"
        )

    parts.append(
        f"DROP POLICY IF EXISTS {table_name}_member_insert ON {qualified};\n"
        f"CREATE POLICY {table_name}_member_insert\n"
        f"    ON {qualified}\n"
        f"    FOR INSERT\n"
        f"    WITH CHECK ({member_check});"
    )
    parts.append(
        f"DROP POLICY IF EXISTS {table_name}_member_update ON {qualified};\n"
        f"CREATE POLICY {table_name}_member_update\n"
        f"    ON {qualified}\n"
        f"    FOR UPDATE\n"
        f"    USING ({member_check});"
    )
    parts.append(
        f"DROP POLICY IF EXISTS {table_name}_member_delete ON {qualified};\n"
        f"CREATE POLICY {table_name}_member_delete\n"
        f"    ON {qualified}\n"
        f"    FOR DELETE\n"
        f"    USING ({member_check});"
    )

    return "\n\n".join(parts)


# ── Per-table DDL helpers ─────────────────────────────────────────────

def _table_ddl(table: TableDefinition, tenant_schema: str) -> str:
    """CREATE TABLE statement with all columns + reserved system columns."""
    # Reject emitter bugs where a DataModel field collides with reserved
    # columns (id, created_at, updated_at). Surfacing this loudly is
    # better than silently dropping or overwriting.
    for field in table.fields:
        if field.name in RESERVED_COLUMN_NAMES:
            raise ValueError(
                f"table {table.name!r}: field {field.name!r} collides with a "
                f"reserved system column ({sorted(RESERVED_COLUMN_NAMES)})"
            )

    qualified = f"{tenant_schema}.{table.name}"

    column_lines = [
        "    id UUID PRIMARY KEY DEFAULT gen_random_uuid()",
        "    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
        "    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
    ]
    for field in table.fields:
        column_lines.append("    " + column_to_ddl(field, table.name))

    body = ",\n".join(column_lines)
    return (
        f"CREATE TABLE IF NOT EXISTS {qualified} (\n"
        f"{body}\n"
        f");"
    )


def _table_indexes(table: TableDefinition, tenant_schema: str) -> list[str]:
    """One CREATE INDEX statement per declared index."""
    stmts: list[str] = []
    for field_name in table.indexes:
        idx_name = f"ix_{table.name}_{field_name}"
        stmts.append(
            f"CREATE INDEX IF NOT EXISTS {idx_name} "
            f"ON {tenant_schema}.{table.name} ({field_name});"
        )
    return stmts


def _table_updated_at_trigger(table: TableDefinition, tenant_schema: str) -> str:
    """Trigger that calls public.set_updated_at() on every UPDATE.

    DROP THEN CREATE because Postgres doesn't have CREATE TRIGGER IF NOT
    EXISTS until very recent versions. DROP IF EXISTS is the idiomatic
    idempotency guard."""
    qualified = f"{tenant_schema}.{table.name}"
    trigger_name = f"{table.name}_set_updated_at"
    return (
        f"DROP TRIGGER IF EXISTS {trigger_name} ON {qualified};\n"
        f"CREATE TRIGGER {trigger_name}\n"
        f"    BEFORE UPDATE ON {qualified}\n"
        f"    FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();"
    )


# ── Main entry point ──────────────────────────────────────────────────

def generate_tenant_sql(
    model: DataModel,
    tenant_schema: str,
    project_id: str,
) -> str:
    """Render the complete tenant DDL script.

    Sections, in order:
      1. Comment header (auto-generated banner + project_id + UTC timestamp)
      2. set search_path to tenant_schema (so unqualified refs work)
      3. Per table:
           a. CREATE TABLE IF NOT EXISTS
           b. CREATE INDEX IF NOT EXISTS (one per declared index)
           c. updated_at trigger
           d. ENABLE ROW LEVEL SECURITY
           e. RLS policies (public_read if applicable, member writes)
      4. Schema-level GRANTs to anon + authenticated.

    `model.singletons` is intentionally ignored — singletons live in
    `src/content/*.json` (build-time bundled), not in Postgres.

    `table.relationships` is intentionally ignored — see module docstring.
    """
    now_iso = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    header = (
        f"-- ───────────────────────────────────────────────────────────\n"
        f"-- AUTO-GENERATED by tenant_sql_generator. Do not edit by hand.\n"
        f"-- project_id  : {project_id}\n"
        f"-- tenant_schema: {tenant_schema}\n"
        f"-- generated_at : {now_iso}\n"
        f"-- model_version: {model.version}\n"
        f"-- tables       : {len(model.tables)}\n"
        f"-- ───────────────────────────────────────────────────────────\n"
    )

    blocks: list[str] = [header]
    blocks.append(f"SET search_path TO {tenant_schema}, public;")

    for table in model.tables:
        blocks.append(f"-- ── {table.name} ────────────────────────────────")
        blocks.append(_table_ddl(table, tenant_schema))

        for idx_stmt in _table_indexes(table, tenant_schema):
            blocks.append(idx_stmt)

        blocks.append(_table_updated_at_trigger(table, tenant_schema))
        blocks.append(
            rls_policies_for_table(
                table_name=table.name,
                tenant_schema=tenant_schema,
                project_id=project_id,
                public_read=table.public_read,
            )
        )

    # Schema-level grants. Per-table grants are unnecessary because the
    # provisioner already configured default privileges; this is
    # belt-and-braces for the existing tables we just created.
    blocks.append(f"-- ── schema-wide grants ────────────────────────────")
    blocks.append(
        f"GRANT USAGE ON SCHEMA {tenant_schema} TO authenticated, anon;\n"
        f"GRANT ALL ON ALL TABLES IN SCHEMA {tenant_schema} TO authenticated;\n"
        f"GRANT SELECT ON ALL TABLES IN SCHEMA {tenant_schema} TO anon;\n"
        f"GRANT ALL ON ALL SEQUENCES IN SCHEMA {tenant_schema} TO authenticated;"
    )

    return "\n\n".join(blocks) + "\n"


# ── Validation ────────────────────────────────────────────────────────

# Patterns we never want to see in generated SQL. Each entry is a
# compiled regex + a human-readable label so callers get an actionable
# error message instead of "found something bad".
_DANGEROUS_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bDROP\s+DATABASE\b", re.IGNORECASE), "DROP DATABASE"),
    (re.compile(r"\bDROP\s+SCHEMA\b",   re.IGNORECASE), "DROP SCHEMA"),
    (re.compile(r"\bTRUNCATE\b",        re.IGNORECASE), "TRUNCATE"),
    (re.compile(r"\bGRANT\b[^;]*\bTO\s+public\b", re.IGNORECASE), "GRANT … TO public"),
    (re.compile(r"\bDISABLE\s+ROW\s+LEVEL\s+SECURITY\b", re.IGNORECASE), "DISABLE ROW LEVEL SECURITY"),
]


def validate_generated_sql(sql: str) -> list[str]:
    """Cheap sanity checks before sending the SQL to the database.

    These are tripwires for catastrophic bugs in the generator itself,
    not RFC validation of Postgres syntax (Postgres will do that). The
    goal is to short-circuit a regression where, say, a refactor stops
    emitting RLS or accidentally introduces a DROP.

    Returns a list of error messages; empty means the SQL passed.
    """
    issues: list[str] = []

    if "CREATE TABLE" not in sql.upper():
        # Tolerate empty models (no tables → no CREATE TABLE). Detect
        # this explicitly so the all-empty case isn't flagged.
        if "-- tables       : 0" not in sql:
            issues.append("missing CREATE TABLE statement")

    if "ENABLE ROW LEVEL SECURITY" not in sql.upper():
        if "-- tables       : 0" not in sql:
            issues.append("missing ENABLE ROW LEVEL SECURITY")

    # Parenthesis balance — counts only — doesn't catch nesting bugs,
    # but does catch the classic "forgot to close a paren" generator bug.
    if sql.count("(") != sql.count(")"):
        issues.append(
            f"unbalanced parentheses: {sql.count('(')} '(' vs {sql.count(')')} ')'"
        )

    # Single-quote balance. Doubled-up '' inside strings counts as zero
    # net, which is what Postgres also sees, so this works for properly
    # escaped literals. Any odd count indicates a missing closing quote.
    quote_count = sql.count("'")
    if quote_count % 2 != 0:
        issues.append(f"unbalanced single quotes (count={quote_count})")

    for pattern, label in _DANGEROUS_PATTERNS:
        if pattern.search(sql):
            issues.append(f"dangerous SQL pattern detected: {label}")

    return issues


# ── Apply ─────────────────────────────────────────────────────────────

def split_sql_statements(sql: str) -> list[str]:
    """Split a SQL script into individual statements.

    Splits on `;` followed by a newline or end-of-string. Strips
    whitespace and skips empty / comment-only fragments.

    This module's generated SQL contains no dollar-quoted blocks (the
    one PL/pgSQL function — `public.set_updated_at` — lives in
    migration 024, not in the per-tenant output), so a naive split is
    safe. If that ever changes, this function needs to grow $$-aware.
    """
    statements: list[str] = []
    for raw in re.split(r";\s*(?:\n|$)", sql):
        stmt = raw.strip()
        if not stmt:
            continue
        # Drop fragments that are entirely a comment line. Each kept
        # statement is suffixed with `;` so postgrest sees a complete
        # statement.
        non_comment = "\n".join(
            ln for ln in stmt.splitlines()
            if ln.strip() and not ln.strip().startswith("--")
        ).strip()
        if not non_comment:
            continue
        statements.append(non_comment + ";")
    return statements


async def apply_tenant_sql(sql: str, admin_client) -> dict:
    """Run generated DDL against the database via `public.execute_ddl`.

    `admin_client` must be a service-role supabase-py AsyncClient
    (typically obtained via `managed_admin_client()`). The RPC is
    GRANT-restricted to service_role, so the anon-keyed client cannot
    reach it even if a caller mis-wires.

    Returns:
        {
          "success":             bool,
          "error":               str | None,
          "failed_statement":    str | None,  # the SQL that errored
          "statements_executed": int,         # count of successful steps
        }

    Stops at the first error rather than continuing — half-applied DDL
    is harder to reason about than a clean failure. The migration is
    designed to be idempotent so re-running after a fix is safe.
    """
    statements = split_sql_statements(sql)
    executed = 0

    for stmt in statements:
        try:
            await admin_client.rpc("execute_ddl", {"p_sql": stmt}).execute()
        except Exception as exc:  # noqa: BLE001 — surfaces postgrest/asyncpg/Network errors uniformly
            return {
                "success": False,
                "error": str(exc),
                "failed_statement": stmt,
                "statements_executed": executed,
            }
        executed += 1

    return {
        "success": True,
        "error": None,
        "failed_statement": None,
        "statements_executed": executed,
    }
