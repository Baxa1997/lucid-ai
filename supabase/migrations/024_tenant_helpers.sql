-- ───────────────────────────────────────────────────────────
--  024_tenant_helpers.sql
--
--  Two helper functions in `public` that the tenant_sql_generator
--  needs at runtime:
--
--  1. public.set_updated_at()
--       Generic trigger function — referenced by every tenant
--       table's updated_at trigger. Lives in `public` so it's
--       shared across all tenants (one definition, many users)
--       rather than duplicated inside each tenant schema.
--
--  2. public.execute_ddl(p_sql TEXT)
--       Service-role-only escape hatch for running generated DDL
--       (CREATE TABLE, CREATE POLICY, …) through postgrest. The
--       postgrest REST API can't execute arbitrary SQL directly,
--       so the generator's `apply_tenant_sql()` calls this RPC
--       per statement. SECURITY DEFINER + GRANT to service_role
--       only — never expose to anon / authenticated.
--
--  Why this lives in its own migration
--  -----------------------------------
--  The provisioning RPCs (023) and the generated tenant DDL (this
--  step's runtime output) are conceptually independent. Keeping the
--  helpers in a separate file makes it obvious that the generator
--  module has a hard dependency on them.
-- ───────────────────────────────────────────────────────────


-- ── 1. public.set_updated_at() ────────────────────────────────────────
-- Generic trigger function. Every tenant table gets a BEFORE UPDATE
-- trigger that calls this — keeps `updated_at` honest without each
-- tenant schema needing its own copy of the function body.
CREATE OR REPLACE FUNCTION public.set_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

-- Trigger functions need to be callable by the role that owns the
-- table firing the trigger. The simplest path is to grant EXECUTE to
-- the standard roles — the function body has no side effects beyond
-- mutating the row Postgres is already about to write.
GRANT EXECUTE ON FUNCTION public.set_updated_at() TO authenticated, anon, service_role;


-- ── 2. public.execute_ddl(p_sql TEXT) ─────────────────────────────────
-- Server-side DDL executor for tenant generation. SECURITY DEFINER so
-- the elevated path runs as the function owner regardless of caller.
-- GRANTed to service_role only — the supabase-py admin client uses
-- this to apply generated CREATE TABLE / CREATE POLICY etc. There is
-- NO input validation: the caller (tenant_sql_generator.apply_tenant_sql)
-- is responsible for splitting and sanity-checking the SQL. Anon /
-- authenticated MUST never gain EXECUTE on this function.
--
-- Why this is acceptable despite the obvious "arbitrary SQL execution"
-- alarm bells: the only caller is server-to-server code with full
-- service-role credentials anyway. service_role can already do
-- anything; this function just makes that capability reachable via
-- postgrest's RPC mechanism instead of requiring a direct asyncpg
-- connection.
CREATE OR REPLACE FUNCTION public.execute_ddl(p_sql TEXT)
RETURNS VOID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
    EXECUTE p_sql;
END;
$$;

REVOKE ALL ON FUNCTION public.execute_ddl(TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.execute_ddl(TEXT) TO service_role;


-- ── Verification queries ──────────────────────────────────────────────
-- SELECT routine_name FROM information_schema.routines
-- WHERE routine_schema = 'public'
--   AND routine_name IN ('set_updated_at', 'execute_ddl')
-- ORDER BY routine_name;
-- (Expected: 2 rows)
