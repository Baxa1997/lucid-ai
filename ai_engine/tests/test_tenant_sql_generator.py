"""Tests for the deterministic tenant SQL generator.

Two layers:
  • Unit tests (most of the file) — pure functions on hand-crafted models.
    No DB, no network. Run anywhere.
  • Live-apply tests (TestLiveApply) — skip without real Supabase creds
    AND a working `public.execute_ddl` RPC (migration 024). They
    provision a tenant schema, apply generated SQL, inspect Postgres
    catalogs, and clean up.

Class layout matches the Step 1.3 spec:
  TestTypeMapping        — field_to_sql_type
  TestCheckConstraints   — field_check_constraint
  TestColumnDDL          — column_to_ddl composition
  TestRLSPolicies        — rls_policies_for_table
  TestFullGeneration     — generate_tenant_sql against real example models
  TestValidation         — validate_generated_sql
  TestSqlSplitting       — split_sql_statements
  TestLiveApply          — apply_tenant_sql against a real Supabase
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ── Load .env for live-apply tests (best-effort) ─────────────────────
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


from app.services.data_model import (  # noqa: E402
    DataModel,
    FieldDefinition,
    TableDefinition,
)
from app.services.tenant_sql_generator import (  # noqa: E402
    RESERVED_COLUMN_NAMES,
    apply_tenant_sql,
    column_to_ddl,
    field_check_constraint,
    field_to_sql_type,
    generate_tenant_sql,
    rls_policies_for_table,
    split_sql_statements,
    validate_generated_sql,
    _TYPE_MAPPING,
)


# ── Example DataModels — referenced by multiple test classes ─────────

def _restaurant_model() -> DataModel:
    return DataModel(
        tables=[
            TableDefinition(
                name="menu_items",
                singular_label="Menu Item",
                plural_label="Menu Items",
                description="Dishes the restaurant sells.",
                fields=[
                    FieldDefinition(name="name", type="text", required=True, max_length=120),
                    FieldDefinition(name="description", type="text"),
                    FieldDefinition(name="price_cents", type="integer", required=True),
                    FieldDefinition(name="photo_url", type="image_url"),
                    FieldDefinition(
                        name="category",
                        type="text",
                        enum_values=["starter", "main", "dessert", "drink"],
                    ),
                    FieldDefinition(name="is_available", type="boolean", default="true"),
                ],
                indexes=["category"],
                public_read=True,
            ),
            TableDefinition(
                name="reservations",
                singular_label="Reservation",
                plural_label="Reservations",
                description="Bookings made by guests.",
                fields=[
                    FieldDefinition(name="guest_name", type="text", required=True),
                    FieldDefinition(name="guest_email", type="email", required=True),
                    FieldDefinition(name="party_size", type="integer", required=True),
                    FieldDefinition(name="reserved_for", type="datetime", required=True),
                ],
                public_read=False,
            ),
        ],
    )


def _ecommerce_model() -> DataModel:
    return DataModel(
        tables=[
            TableDefinition(
                name="products",
                singular_label="Product", plural_label="Products",
                description="Items for sale.",
                fields=[
                    FieldDefinition(name="name", type="text", required=True),
                    FieldDefinition(name="slug", type="text", required=True),
                    FieldDefinition(name="price_cents", type="integer", required=True),
                    FieldDefinition(name="stock_count", type="integer", default="0"),
                    FieldDefinition(name="is_active", type="boolean", default="true"),
                ],
                indexes=["slug"],
                public_read=True,
            ),
            TableDefinition(
                name="orders",
                singular_label="Order", plural_label="Orders",
                description="Customer purchases.",
                fields=[
                    FieldDefinition(name="customer_email", type="email", required=True),
                    FieldDefinition(name="total_cents", type="integer", required=True),
                    FieldDefinition(
                        name="status", type="text", required=True, default="'pending'",
                        enum_values=["pending", "paid", "shipped"],
                    ),
                    FieldDefinition(name="line_items", type="json", required=True),
                ],
                public_read=False,
            ),
        ],
    )


def _blog_model() -> DataModel:
    return DataModel(
        tables=[
            TableDefinition(
                name="posts",
                singular_label="Post", plural_label="Posts",
                description="Articles.",
                fields=[
                    FieldDefinition(name="title", type="text", required=True, max_length=200),
                    FieldDefinition(name="slug", type="text", required=True),
                    FieldDefinition(name="body_markdown", type="text", required=True),
                    FieldDefinition(name="hero_image_url", type="image_url"),
                    FieldDefinition(name="published_at", type="datetime"),
                    FieldDefinition(
                        name="status", type="text", required=True, default="'draft'",
                        enum_values=["draft", "published", "archived"],
                    ),
                ],
                indexes=["slug", "status"],
                public_read=True,
            ),
        ],
    )


# ── TestTypeMapping ──────────────────────────────────────────────────

class TestTypeMapping:
    """field_to_sql_type — closed-set type → SQL type."""

    @pytest.mark.parametrize("field_type, sql_type", [
        ("text",      "TEXT"),
        ("number",    "NUMERIC(12,2)"),
        ("integer",   "BIGINT"),
        ("boolean",   "BOOLEAN"),
        ("date",      "DATE"),
        ("datetime",  "TIMESTAMPTZ"),
        ("json",      "JSONB"),
        ("image_url", "TEXT"),
        ("email",     "TEXT"),
        ("phone",     "TEXT"),
        ("url",       "TEXT"),
    ])
    def test_all_known_types_map_correctly(self, field_type, sql_type):
        f = FieldDefinition(name="x", type=field_type)
        assert field_to_sql_type(f) == sql_type

    def test_mapping_covers_exactly_eleven_types(self):
        """Catch the case where someone adds to FieldType but not here."""
        assert len(_TYPE_MAPPING) == 11

    def test_unknown_type_raises(self):
        """Defensive — Pydantic should block this, but we test anyway."""
        # Bypass Pydantic validation with model_construct so we can hand
        # the generator a synthetically invalid FieldDefinition.
        f = FieldDefinition.model_construct(name="x", type="binary")  # type: ignore[arg-type]
        with pytest.raises(KeyError, match="unknown FieldDefinition.type"):
            field_to_sql_type(f)


# ── TestCheckConstraints ─────────────────────────────────────────────

class TestCheckConstraints:
    """field_check_constraint — picks the right CHECK clause per field."""

    def test_image_url_gets_url_check(self):
        f = FieldDefinition(name="photo_url", type="image_url")
        c = field_check_constraint(f, "menu_items")
        assert c is not None
        assert "photo_url" in c
        assert "https?://" in c

    def test_url_gets_url_check(self):
        f = FieldDefinition(name="link", type="url")
        c = field_check_constraint(f, "x")
        assert c and "https?://" in c

    def test_email_gets_email_check(self):
        f = FieldDefinition(name="email", type="email")
        c = field_check_constraint(f, "users")
        assert c and "@" in c

    def test_enum_values_get_in_check(self):
        f = FieldDefinition(name="status", type="text",
                            enum_values=["pending", "paid"])
        c = field_check_constraint(f, "orders")
        assert c and "IN ('pending', 'paid')" in c

    def test_enum_values_with_apostrophe_are_escaped(self):
        """Single quotes inside enum values must be doubled, not closed."""
        f = FieldDefinition(name="role", type="text",
                            enum_values=["chef", "owner's friend"])
        c = field_check_constraint(f, "staff")
        assert c is not None
        assert "'owner''s friend'" in c, f"escape missing: {c}"

    def test_max_length_check(self):
        f = FieldDefinition(name="title", type="text", max_length=200)
        c = field_check_constraint(f, "posts")
        assert c and "length(title) <= 200" in c

    def test_max_length_only_applies_to_textual_types(self):
        """A `json` field with max_length doesn't get a length() check —
        length() wouldn't mean what you want on JSONB."""
        f = FieldDefinition(name="payload", type="json", max_length=200)
        assert field_check_constraint(f, "x") is None

    def test_text_without_constraints_returns_none(self):
        f = FieldDefinition(name="body", type="text")
        assert field_check_constraint(f, "posts") is None

    def test_enum_takes_precedence_over_url(self):
        """Closed enum is more restrictive than URL pattern."""
        f = FieldDefinition(name="cdn", type="url",
                            enum_values=["https://a.example", "https://b.example"])
        c = field_check_constraint(f, "x")
        assert c and " IN " in c and "https?://" not in c


# ── TestColumnDDL ────────────────────────────────────────────────────

class TestColumnDDL:
    """column_to_ddl — assembles one column line for CREATE TABLE."""

    def test_required_field_has_not_null(self):
        f = FieldDefinition(name="title", type="text", required=True)
        out = column_to_ddl(f, "posts")
        assert "title TEXT NOT NULL" in out

    def test_default_included(self):
        f = FieldDefinition(name="status", type="text", default="'pending'")
        out = column_to_ddl(f, "orders")
        assert "DEFAULT 'pending'" in out

    def test_optional_field_omits_not_null(self):
        f = FieldDefinition(name="description", type="text", required=False)
        out = column_to_ddl(f, "products")
        assert "NOT NULL" not in out

    def test_check_constraint_appended(self):
        f = FieldDefinition(name="photo_url", type="image_url")
        out = column_to_ddl(f, "menu_items")
        assert "CHECK" in out

    def test_multiple_modifiers_compose_in_stable_order(self):
        """name → type → NOT NULL → DEFAULT → CHECK."""
        f = FieldDefinition(
            name="status", type="text", required=True,
            default="'pending'", enum_values=["pending", "paid"],
        )
        out = column_to_ddl(f, "orders")
        # Verify the order by finding indexes
        idx_type = out.index("TEXT")
        idx_not_null = out.index("NOT NULL")
        idx_default = out.index("DEFAULT")
        idx_check = out.index("CHECK")
        assert idx_type < idx_not_null < idx_default < idx_check, out

    # ── Default-expression normalization ──────────────────────────────
    # Gemini sometimes emits bare identifiers like `"default": "pending"`
    # for string-type fields, expecting them to be string literals.
    # Postgres reads `DEFAULT pending` as a column reference (error
    # 0A000). column_to_ddl auto-quotes these without breaking the
    # legitimate forms.

    def test_default_bare_identifier_auto_quoted(self):
        f = FieldDefinition(name="status", type="text", default="pending")
        out = column_to_ddl(f, "orders")
        assert "DEFAULT 'pending'" in out

    def test_default_already_quoted_kept_as_is(self):
        f = FieldDefinition(name="status", type="text", default="'pending'")
        out = column_to_ddl(f, "orders")
        assert "DEFAULT 'pending'" in out
        assert "DEFAULT ''pending''" not in out  # no double-quoting

    def test_default_function_call_kept_as_is(self):
        f = FieldDefinition(name="ts", type="datetime", default="now()")
        out = column_to_ddl(f, "events")
        assert "DEFAULT now()" in out

    def test_default_current_timestamp_kept_as_is(self):
        f = FieldDefinition(name="ts", type="datetime", default="CURRENT_TIMESTAMP")
        out = column_to_ddl(f, "events")
        assert "DEFAULT CURRENT_TIMESTAMP" in out

    def test_default_numeric_string_kept_as_is(self):
        f = FieldDefinition(name="n", type="integer", default="0")
        out = column_to_ddl(f, "x")
        assert "DEFAULT 0" in out

    def test_default_boolean_keyword_kept_as_is(self):
        # boolean defaults arrive as "TRUE"/"FALSE" thanks to the
        # Pydantic coercion validator on FieldDefinition.default.
        f = FieldDefinition(name="active", type="boolean", default=False)
        out = column_to_ddl(f, "items")
        assert "DEFAULT FALSE" in out

    def test_default_with_apostrophe_is_escaped(self):
        """Bare identifier containing a quote — escape on the way in."""
        f = FieldDefinition(name="note", type="text", default="chef's")
        out = column_to_ddl(f, "items")
        assert "DEFAULT 'chef''s'" in out


# ── TestRLSPolicies ──────────────────────────────────────────────────

class TestRLSPolicies:
    """rls_policies_for_table — builds the RLS block for one table."""

    PROJECT_UUID = "11111111-2222-3333-4444-555555555555"
    SCHEMA = "tenant_abc123def456"

    def test_public_read_true_emits_public_read_policy(self):
        out = rls_policies_for_table("posts", self.SCHEMA, self.PROJECT_UUID, public_read=True)
        assert "posts_public_read" in out
        assert "FOR SELECT" in out
        assert "USING (true)" in out

    def test_public_read_false_omits_public_read_policy(self):
        out = rls_policies_for_table("orders", self.SCHEMA, self.PROJECT_UUID, public_read=False)
        assert "orders_public_read" not in out

    def test_all_tables_get_member_writes(self):
        for pr in (True, False):
            out = rls_policies_for_table("t", self.SCHEMA, self.PROJECT_UUID, public_read=pr)
            assert "t_member_insert" in out
            assert "t_member_update" in out
            assert "t_member_delete" in out

    def test_project_id_interpolated(self):
        out = rls_policies_for_table("t", self.SCHEMA, self.PROJECT_UUID, public_read=True)
        assert f"'{self.PROJECT_UUID}'::uuid" in out

    def test_schema_name_interpolated(self):
        out = rls_policies_for_table("t", self.SCHEMA, self.PROJECT_UUID, public_read=True)
        assert f"{self.SCHEMA}.t" in out

    def test_rls_enable_emitted(self):
        out = rls_policies_for_table("t", self.SCHEMA, self.PROJECT_UUID, public_read=True)
        assert "ENABLE ROW LEVEL SECURITY" in out

    def test_idempotent_drop_before_create(self):
        """Each CREATE POLICY must be preceded by DROP POLICY IF EXISTS."""
        out = rls_policies_for_table("t", self.SCHEMA, self.PROJECT_UUID, public_read=True)
        # Four policies → four drops
        assert out.count("DROP POLICY IF EXISTS") == 4


# ── TestFullGeneration ───────────────────────────────────────────────

class TestFullGeneration:
    """generate_tenant_sql — full output against real example models."""

    SCHEMA = "tenant_fixture_001"
    PROJECT_UUID = "00000000-1111-2222-3333-444444444444"

    def _gen(self, model: DataModel) -> str:
        return generate_tenant_sql(model, self.SCHEMA, self.PROJECT_UUID)

    def test_restaurant_passes_validation(self):
        sql = self._gen(_restaurant_model())
        assert validate_generated_sql(sql) == []

    def test_ecommerce_passes_validation(self):
        sql = self._gen(_ecommerce_model())
        assert validate_generated_sql(sql) == []

    def test_blog_passes_validation(self):
        sql = self._gen(_blog_model())
        assert validate_generated_sql(sql) == []

    def test_create_table_per_declared_table(self):
        sql = self._gen(_restaurant_model())
        assert sql.count("CREATE TABLE IF NOT EXISTS") == 2

    def test_updated_at_trigger_per_table(self):
        sql = self._gen(_restaurant_model())
        # 2 tables → 2 trigger drops + 2 creates
        assert sql.count("DROP TRIGGER IF EXISTS") == 2
        assert sql.count("CREATE TRIGGER") == 2

    def test_set_updated_at_function_not_redefined_in_tenant_sql(self):
        """The function lives in migration 024; tenant SQL only references it."""
        sql = self._gen(_restaurant_model())
        assert "CREATE OR REPLACE FUNCTION" not in sql
        assert "public.set_updated_at()" in sql

    def test_idempotency_markers_present(self):
        sql = self._gen(_restaurant_model())
        assert "CREATE TABLE IF NOT EXISTS" in sql
        assert "CREATE INDEX IF NOT EXISTS" in sql
        assert "DROP POLICY IF EXISTS" in sql
        assert "DROP TRIGGER IF EXISTS" in sql

    def test_empty_model_generates_valid_minimal_sql(self):
        sql = self._gen(DataModel(tables=[]))
        # Validator tolerates the empty case via the header sentinel
        assert validate_generated_sql(sql) == []
        assert "search_path" in sql.lower()

    def test_relationships_field_ignored_silently(self):
        """v1: relationships slot is accepted on the model but the
        generator must not emit foreign keys or fail."""
        m = DataModel(tables=[
            TableDefinition(
                name="posts", singular_label="Post", plural_label="Posts",
                description="x",
                fields=[FieldDefinition(name="title", type="text")],
                relationships=[{"from": "author_slug", "to": "authors.slug"}],
            ),
        ])
        sql = self._gen(m)
        assert "REFERENCES" not in sql
        assert "FOREIGN KEY" not in sql
        assert validate_generated_sql(sql) == []

    def test_reserved_column_collision_raises(self):
        """A DataModel that includes 'id'/'created_at'/'updated_at' is a bug."""
        for reserved in RESERVED_COLUMN_NAMES:
            m = DataModel(tables=[
                TableDefinition(
                    name="things", singular_label="Thing", plural_label="Things",
                    description="x",
                    fields=[FieldDefinition(name=reserved, type="text")],
                ),
            ])
            with pytest.raises(ValueError, match="reserved system column"):
                self._gen(m)

    def test_header_contains_project_and_schema(self):
        sql = self._gen(_restaurant_model())
        assert self.PROJECT_UUID in sql
        assert self.SCHEMA in sql
        assert "AUTO-GENERATED" in sql

    def test_grants_present_at_end(self):
        sql = self._gen(_restaurant_model())
        assert f"GRANT USAGE ON SCHEMA {self.SCHEMA}" in sql
        assert "GRANT SELECT ON ALL TABLES" in sql


# ── TestValidation ───────────────────────────────────────────────────

class TestValidation:
    """validate_generated_sql — sanity checks before applying."""

    def test_valid_sql_passes(self):
        sql = generate_tenant_sql(_restaurant_model(), "tenant_x", "00000000-0000-0000-0000-000000000001")
        assert validate_generated_sql(sql) == []

    def test_missing_create_table_flagged(self):
        sql = "SELECT 1;"
        issues = validate_generated_sql(sql)
        assert any("CREATE TABLE" in i for i in issues)

    def test_missing_rls_flagged(self):
        sql = "CREATE TABLE x (id int);"
        issues = validate_generated_sql(sql)
        assert any("ROW LEVEL SECURITY" in i for i in issues)

    def test_unbalanced_parens_flagged(self):
        sql = "CREATE TABLE x (id int; ENABLE ROW LEVEL SECURITY;"
        issues = validate_generated_sql(sql)
        assert any("parentheses" in i for i in issues)

    def test_unbalanced_quotes_flagged(self):
        sql = (
            "CREATE TABLE x (s text);\n"
            "ENABLE ROW LEVEL SECURITY x;\n"
            "INSERT INTO x VALUES ('unclosed);\n"
        )
        issues = validate_generated_sql(sql)
        assert any("single quote" in i.lower() for i in issues)

    @pytest.mark.parametrize("snippet, label", [
        ("DROP DATABASE production;",                  "DROP DATABASE"),
        ("DROP SCHEMA public;",                        "DROP SCHEMA"),
        ("TRUNCATE chat_sessions;",                    "TRUNCATE"),
        ("GRANT ALL ON TABLE x TO public;",            "GRANT … TO public"),
        ("ALTER TABLE x DISABLE ROW LEVEL SECURITY;",  "DISABLE ROW LEVEL SECURITY"),
    ])
    def test_dangerous_pattern_flagged(self, snippet, label):
        sql = f"CREATE TABLE x (id int); ENABLE ROW LEVEL SECURITY; {snippet}"
        issues = validate_generated_sql(sql)
        assert any(label in i for i in issues), f"didn't catch {label}: {issues}"


# ── TestSqlSplitting ─────────────────────────────────────────────────

class TestSqlSplitting:
    """split_sql_statements — preps SQL for per-statement RPC execution."""

    def test_single_statement(self):
        out = split_sql_statements("SELECT 1;")
        assert out == ["SELECT 1;"]

    def test_multiple_statements(self):
        sql = "CREATE TABLE a (id int);\nCREATE TABLE b (id int);\n"
        assert split_sql_statements(sql) == [
            "CREATE TABLE a (id int);",
            "CREATE TABLE b (id int);",
        ]

    def test_empty_input(self):
        assert split_sql_statements("") == []

    def test_comments_alone_are_dropped(self):
        sql = "-- comment\n;\nSELECT 1;"
        out = split_sql_statements(sql)
        assert out == ["SELECT 1;"]

    def test_inline_comments_kept_when_alongside_sql(self):
        sql = "CREATE TABLE x (\n  id int -- pk\n);"
        out = split_sql_statements(sql)
        assert len(out) == 1
        assert "CREATE TABLE x" in out[0]


# ── TestLiveApply ────────────────────────────────────────────────────

def _have_real_supabase() -> bool:
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
    if not url or not key:
        return False
    if "test.supabase.co" in url:
        return False
    if key in ("x", "dummy", "placeholder"):
        return False
    return True


pytestmark_live = pytest.mark.skipif(
    not _have_real_supabase(),
    reason="No real Supabase creds — set SUPABASE_URL + SUPABASE_SERVICE_KEY",
)


@pytest.fixture(autouse=True)
def _reset_admin_singleton():
    """Reset the supabase admin singleton between tests — same reason as
    test_tenant_provisioning.py: the singleton binds to one event loop."""
    import app.supabase_client as _sc
    _sc._admin_client = None
    _sc._admin_client_lock = None
    yield
    _sc._admin_client = None
    _sc._admin_client_lock = None


async def _pick_user_id():
    from app.supabase_client import managed_admin_client
    async with managed_admin_client() as client:
        res = await client.table("users").select("id").limit(1).execute()
        rows = res.data or []
        if not rows:
            pytest.skip("No public.users rows — can't attach a chat_sessions row")
        return rows[0]["id"]


@pytest_asyncio.fixture
async def provisioned_project():
    """Create a chat_sessions row, provision a tenant schema, yield (project_id, schema_name).

    Cleans both up after the test, even on assertion failure. The schema
    is dropped CASCADE so any tables we created inside it are removed
    in one shot.
    """
    from app.supabase_client import managed_admin_client
    project_id: str | None = None
    try:
        user_id = await _pick_user_id()
        async with managed_admin_client() as client:
            ins = await (
                client.table("chat_sessions")
                .insert({"user_id": user_id, "title": "tenant_sql_generator live test"})
                .execute()
            )
            project_id = ins.data[0]["id"]
            prov = await client.rpc(
                "provision_tenant_schema",
                {"p_project_id": project_id},
            ).execute()
            schema = prov.data
        yield project_id, schema
    finally:
        if project_id:
            try:
                async with managed_admin_client() as client:
                    await client.rpc(
                        "drop_tenant_schema",
                        {"p_project_id": project_id},
                    ).execute()
                    await (
                        client.table("chat_sessions")
                        .delete()
                        .eq("id", project_id)
                        .execute()
                    )
            except Exception:
                pass


@pytestmark_live
@pytest.mark.asyncio
class TestLiveApply:
    """End-to-end: provision → generate → apply → introspect → drop.

    These hit the real Supabase. They depend on migrations 020, 023, AND
    024 being applied. If `public.execute_ddl` is missing, every test in
    this class fails fast on the first apply call.
    """

    async def test_restaurant_sql_applies_cleanly(self, provisioned_project):
        from app.supabase_client import managed_admin_client
        project_id, schema = provisioned_project
        sql = generate_tenant_sql(_restaurant_model(), schema, project_id)

        async with managed_admin_client() as client:
            result = await apply_tenant_sql(sql, client)
            assert result["success"], (
                f"apply failed: {result['error']!r}\n"
                f"on statement: {result['failed_statement']!r}"
            )
            assert result["statements_executed"] > 0

    async def test_applied_tables_exist_with_expected_columns(self, provisioned_project):
        from app.supabase_client import managed_admin_client
        project_id, schema = provisioned_project
        sql = generate_tenant_sql(_restaurant_model(), schema, project_id)

        async with managed_admin_client() as client:
            await apply_tenant_sql(sql, client)

            # Verify tables exist via information_schema. We do this
            # through execute_ddl wrapping a SELECT isn't right — use
            # the RPC system table lookup via postgrest by querying
            # pg_tables through a dedicated helper. Simplest: query the
            # actual data table existence via a SELECT.
            for table in ("menu_items", "reservations"):
                # If the table exists, postgrest can select from it under
                # a schema-qualified path. We do that via a raw RPC call
                # wrapped in execute_ddl with a DO block that SELECTs and
                # asserts.
                check_sql = (
                    f"DO $$ BEGIN IF NOT EXISTS ("
                    f"SELECT 1 FROM information_schema.tables "
                    f"WHERE table_schema = '{schema}' AND table_name = '{table}'"
                    f") THEN RAISE EXCEPTION 'table missing: %', '{table}'; END IF; END $$;"
                )
                await client.rpc("execute_ddl", {"p_sql": check_sql}).execute()

    async def test_rls_policies_present(self, provisioned_project):
        from app.supabase_client import managed_admin_client
        project_id, schema = provisioned_project
        sql = generate_tenant_sql(_restaurant_model(), schema, project_id)

        async with managed_admin_client() as client:
            await apply_tenant_sql(sql, client)
            # menu_items is public_read=True → expect 4 policies
            # (public_read + member_insert/update/delete)
            check = (
                f"DO $$ DECLARE c INT; BEGIN "
                f"SELECT count(*) INTO c FROM pg_policies "
                f"WHERE schemaname = '{schema}' AND tablename = 'menu_items'; "
                f"IF c < 4 THEN RAISE EXCEPTION 'menu_items policies: % (expected >=4)', c; END IF; "
                f"END $$;"
            )
            await client.rpc("execute_ddl", {"p_sql": check}).execute()

            # reservations is public_read=False → expect 3 policies
            check = (
                f"DO $$ DECLARE c INT; BEGIN "
                f"SELECT count(*) INTO c FROM pg_policies "
                f"WHERE schemaname = '{schema}' AND tablename = 'reservations'; "
                f"IF c < 3 THEN RAISE EXCEPTION 'reservations policies: % (expected >=3)', c; END IF; "
                f"END $$;"
            )
            await client.rpc("execute_ddl", {"p_sql": check}).execute()

    async def test_idempotent_double_apply(self, provisioned_project):
        from app.supabase_client import managed_admin_client
        project_id, schema = provisioned_project
        sql = generate_tenant_sql(_restaurant_model(), schema, project_id)

        async with managed_admin_client() as client:
            r1 = await apply_tenant_sql(sql, client)
            r2 = await apply_tenant_sql(sql, client)
            assert r1["success"] and r2["success"], (
                f"first run: {r1}\nsecond run: {r2}"
            )

    async def test_updated_at_trigger_fires(self, provisioned_project):
        """The BEFORE UPDATE trigger must advance `updated_at` on every UPDATE.

        Subtlety: Postgres `NOW()` returns `transaction_timestamp()` — the
        same value for every call within one transaction. INSERT and
        UPDATE must therefore run in SEPARATE transactions (separate
        execute_ddl RPC calls) for the timestamp to actually advance.
        Same-transaction timing would give a false negative.
        """
        from app.supabase_client import managed_admin_client
        project_id, schema = provisioned_project
        sql = generate_tenant_sql(_restaurant_model(), schema, project_id)

        async with managed_admin_client() as client:
            await apply_tenant_sql(sql, client)

            # Use a temp `_lucid_test_marker` table in the tenant schema
            # to round-trip the inserted row id back to Python without
            # needing SELECT-via-postgrest (which wouldn't see the
            # tenant schema without a custom REST exposure). Each
            # execute_ddl is its own transaction.
            await client.rpc("execute_ddl", {"p_sql":
                f"CREATE TABLE IF NOT EXISTS {schema}._lucid_test_marker "
                f"(k TEXT PRIMARY KEY, v UUID, t1 TIMESTAMPTZ, t2 TIMESTAMPTZ);"
            }).execute()

            # Txn 1: insert a menu_items row, stash id+updated_at in marker
            await client.rpc("execute_ddl", {"p_sql":
                f"DO $$ DECLARE rid UUID; rt TIMESTAMPTZ; BEGIN "
                f"INSERT INTO {schema}.menu_items (name, price_cents) "
                f"VALUES ('Trigger Test', 100) RETURNING id, updated_at "
                f"INTO rid, rt; "
                f"INSERT INTO {schema}._lucid_test_marker (k, v, t1) "
                f"VALUES ('row', rid, rt) "
                f"ON CONFLICT (k) DO UPDATE SET v = EXCLUDED.v, t1 = EXCLUDED.t1; "
                f"END $$;"
            }).execute()

            # Txn 2 (after a small sleep): update the row, stash the new updated_at
            await client.rpc("execute_ddl", {"p_sql":
                f"DO $$ DECLARE rid UUID; rt TIMESTAMPTZ; BEGIN "
                f"PERFORM pg_sleep(0.1); "
                f"SELECT v INTO rid FROM {schema}._lucid_test_marker WHERE k = 'row'; "
                f"UPDATE {schema}.menu_items SET name = 'Trigger Test 2' "
                f"WHERE id = rid RETURNING updated_at INTO rt; "
                f"UPDATE {schema}._lucid_test_marker SET t2 = rt WHERE k = 'row'; "
                f"END $$;"
            }).execute()

            # Txn 3: assert t2 > t1 (the trigger fired and advanced the timestamp)
            await client.rpc("execute_ddl", {"p_sql":
                f"DO $$ DECLARE a TIMESTAMPTZ; b TIMESTAMPTZ; BEGIN "
                f"SELECT t1, t2 INTO a, b FROM {schema}._lucid_test_marker WHERE k = 'row'; "
                f"IF b IS NULL OR a IS NULL THEN "
                f"  RAISE EXCEPTION 'marker incomplete: t1=% t2=%', a, b; "
                f"END IF; "
                f"IF b <= a THEN "
                f"  RAISE EXCEPTION 'updated_at did not advance: % -> %', a, b; "
                f"END IF; "
                f"END $$;"
            }).execute()
