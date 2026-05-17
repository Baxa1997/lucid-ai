-- ───────────────────────────────────────────────────────────
--  023_project_tenancy.sql
--
--  Foundation for per-project data storage. Adds metadata to
--  chat_sessions so generated products (websites, admin panels)
--  can each own a Postgres schema for their own runtime data —
--  menu_items, products, testimonials, blog posts, etc.
--
--  Numbering note
--  --------------
--  Originally drafted as 022_project_tenancy. Renumbered to 023
--  because 022_fix_project_members_rls_recursion.sql already
--  exists from the invite/membership work.
--
--  Why now, before any pipeline change
--  -----------------------------------
--  Both the website pipeline and the (forthcoming) admin pipeline
--  will write to the same metadata shape (parent_project_id,
--  product_type, tenant_schema, data_model). Shipping the contract
--  first lets the two pipelines develop independently against a
--  stable foundation, and the future "Generate admin for this
--  website" feature becomes a thin UX layer — just set
--  parent_project_id + reuse tenant_schema — instead of a
--  re-architecture.
--
--  What this migration does NOT do
--  -------------------------------
--  • No tenant tables created yet (that's a later step; tables
--    are created at runtime by the pipeline via DDL inside the
--    tenant schema).
--  • No pipeline code changes.
--  • No new RLS on chat_sessions (already has membership-based RLS
--    from migration 020).
--  • tenant_schema stays NULLABLE — legacy projects continue to
--    work; only newly-generated projects get a tenant schema.
-- ───────────────────────────────────────────────────────────

-- ── 1. Columns on chat_sessions ───────────────────────────────────────
-- parent_project_id           — admin panels point at their website
-- product_type                — what was generated
-- tenant_schema               — Postgres schema for this project's data
-- data_model                  — JSONB description of entities + fields
-- supabase_credentials_encrypted — reserved for future per-project DBs

ALTER TABLE public.chat_sessions
    ADD COLUMN IF NOT EXISTS parent_project_id UUID
        REFERENCES public.chat_sessions(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS product_type TEXT NOT NULL DEFAULT 'website',
    ADD COLUMN IF NOT EXISTS tenant_schema TEXT,
    ADD COLUMN IF NOT EXISTS data_model JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS supabase_credentials_encrypted TEXT;

-- product_type values come from a closed set. Use a CHECK rather than
-- an enum so the migration is reversible and adding new values later
-- is just an ALTER TABLE ... DROP/ADD constraint, no enum dance.
DO $$
BEGIN
    ALTER TABLE public.chat_sessions
        ADD CONSTRAINT chat_sessions_product_type_check
        CHECK (product_type IN ('website', 'admin', 'landing'));
EXCEPTION WHEN duplicate_object THEN
    NULL;  -- already added on a prior run
END $$;


-- ── 2. Indexes ────────────────────────────────────────────────────────
-- parent_project_id is NULL for the vast majority of rows (every
-- standalone website / landing). Partial index keeps the index tiny
-- and the lookup fast for "find all admins for website X".
CREATE INDEX IF NOT EXISTS ix_chat_sessions_parent_project_id
    ON public.chat_sessions (parent_project_id)
    WHERE parent_project_id IS NOT NULL;

-- product_type drives "list my websites" / "list my admin panels" tabs
-- in the dashboard. Full index — almost every row has a value.
CREATE INDEX IF NOT EXISTS ix_chat_sessions_product_type
    ON public.chat_sessions (product_type);

-- tenant_schema is a logical identity for the data home. Uniqueness
-- gated on NOT NULL so legacy rows (NULL) don't conflict with each
-- other under a regular UNIQUE constraint.
CREATE UNIQUE INDEX IF NOT EXISTS ix_chat_sessions_tenant_schema_unique
    ON public.chat_sessions (tenant_schema)
    WHERE tenant_schema IS NOT NULL;


-- ── 3. RPC: provision_tenant_schema(project_id) ───────────────────────
-- Creates a Postgres schema for a project's runtime data and stores
-- the name on the chat_sessions row.
--
-- SECURITY DEFINER because:
--   • CREATE SCHEMA / GRANT USAGE / ALTER DEFAULT PRIVILEGES are
--     superuser-ish operations the calling role (service_role via
--     supabase-py) doesn't carry directly.
--   • We REVOKE PUBLIC and only GRANT EXECUTE to service_role so the
--     elevated path stays gated behind server-to-server calls. End
--     users CANNOT call this from a browser session.
--
-- Idempotency: this function intentionally REFUSES to re-provision a
-- project that already has tenant_schema set. The pipeline calls this
-- exactly once per project (at generation time); a second call means
-- something is wrong — either a bug or a manual retry that risks data
-- loss. Surface the error rather than silently re-running.
CREATE OR REPLACE FUNCTION public.provision_tenant_schema(p_project_id UUID)
RETURNS TEXT
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_schema_name TEXT;
    v_existing TEXT;
    v_found BOOLEAN;
BEGIN
    -- Verify project exists AND fetch current tenant_schema in one read.
    SELECT (tenant_schema), TRUE
        INTO v_existing, v_found
    FROM public.chat_sessions
    WHERE id = p_project_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'provision_tenant_schema: project not found: %', p_project_id
            USING ERRCODE = 'no_data_found';
    END IF;

    IF v_existing IS NOT NULL THEN
        RAISE EXCEPTION 'provision_tenant_schema: project % already has tenant_schema %', p_project_id, v_existing
            USING ERRCODE = 'unique_violation';
    END IF;

    -- Schema name: 12 hex chars from the UUID = 48 bits entropy.
    -- Same UUID always produces the same name (deterministic).
    v_schema_name := 'tenant_' || substr(replace(p_project_id::text, '-', ''), 1, 12);

    -- Create the schema and grant baseline access to the standard roles.
    -- Format-with-%I quotes the identifier safely; even though the name
    -- comes from a deterministic transform, defense-in-depth costs nothing.
    EXECUTE format('CREATE SCHEMA IF NOT EXISTS %I', v_schema_name);
    EXECUTE format(
        'GRANT USAGE ON SCHEMA %I TO anon, authenticated, service_role',
        v_schema_name
    );

    -- Default privileges so future tables (created later by the pipeline)
    -- auto-grant the standard role set. Without this every CREATE TABLE
    -- inside the tenant schema would need its own GRANT.
    EXECUTE format(
        'ALTER DEFAULT PRIVILEGES IN SCHEMA %I GRANT SELECT ON TABLES TO anon, authenticated',
        v_schema_name
    );
    EXECUTE format(
        'ALTER DEFAULT PRIVILEGES IN SCHEMA %I GRANT ALL ON TABLES TO service_role',
        v_schema_name
    );

    -- Persist the name on the row so the pipeline + admin lookups can
    -- find it.
    UPDATE public.chat_sessions
        SET tenant_schema = v_schema_name
    WHERE id = p_project_id;

    RETURN v_schema_name;
END;
$$;

REVOKE ALL ON FUNCTION public.provision_tenant_schema(UUID) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.provision_tenant_schema(UUID) TO service_role;


-- ── 4. RPC: drop_tenant_schema(project_id) ────────────────────────────
-- Cleanup. Drops the schema CASCADE (removes all tables in it) and
-- clears the column. Used at project-delete time and during test
-- cleanup. Service-role only.
--
-- Returns the dropped schema name, or empty string if there was
-- nothing to drop (so callers can distinguish "cleaned up" from
-- "no-op"). Raises if the project doesn't exist — that's a real bug.
CREATE OR REPLACE FUNCTION public.drop_tenant_schema(p_project_id UUID)
RETURNS TEXT
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_existing TEXT;
BEGIN
    SELECT tenant_schema INTO v_existing
    FROM public.chat_sessions
    WHERE id = p_project_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'drop_tenant_schema: project not found: %', p_project_id
            USING ERRCODE = 'no_data_found';
    END IF;

    IF v_existing IS NULL THEN
        RETURN '';
    END IF;

    EXECUTE format('DROP SCHEMA IF EXISTS %I CASCADE', v_existing);

    UPDATE public.chat_sessions
        SET tenant_schema = NULL
    WHERE id = p_project_id;

    RETURN v_existing;
END;
$$;

REVOKE ALL ON FUNCTION public.drop_tenant_schema(UUID) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.drop_tenant_schema(UUID) TO service_role;


-- ── 5. Verification queries (run in SQL Editor after applying) ────────
-- These are documented here so the operator can paste them into the
-- Supabase SQL Editor to confirm the migration landed cleanly.
--
-- Expected: 4 columns ------------------------------------------------------
-- SELECT column_name
-- FROM information_schema.columns
-- WHERE table_schema = 'public'
--   AND table_name = 'chat_sessions'
--   AND column_name IN (
--     'parent_project_id', 'product_type', 'tenant_schema', 'data_model'
--   )
-- ORDER BY column_name;
--
-- Expected: 2 functions ----------------------------------------------------
-- SELECT routine_name
-- FROM information_schema.routines
-- WHERE routine_schema = 'public'
--   AND routine_name IN ('provision_tenant_schema', 'drop_tenant_schema')
-- ORDER BY routine_name;
