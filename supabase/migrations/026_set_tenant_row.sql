-- ───────────────────────────────────────────────────────────
--  026_set_tenant_row.sql
--
--  Write-path RPCs for per-tenant data — the admin pipeline (Phase 3)
--  will call these to insert / update / delete rows in tenant tables.
--  Pairs with migration 025 which added the read-path RPC
--  `get_tenant_collection` for generated websites.
--
--  Three functions, all SECURITY DEFINER, all granted to
--  `authenticated` only (NOT anon). The auth check is:
--
--      auth.uid() must be in public.project_members for p_project_id
--
--  Public anon callers (generated websites) MUST NEVER write through
--  this RPC. Only the admin panel — backed by an authenticated user
--  session — should be able to mutate tenant data.
--
--  Why three functions instead of one polymorphic mutation
--  -------------------------------------------------------
--  • Postgres doesn't have a clean "upsert via jsonb" idiom that's
--    safe across all column types — distinct functions let us write
--    each one tightly.
--  • Different functions can have different return types: insert/update
--    return JSONB (the new/updated row), delete returns VOID.
--  • Future per-operation rate limiting / audit trails are easier when
--    the operation is named in the function signature.
--
--  Identifier safety
--  -----------------
--  p_table_name is checked against the project's stored data_model
--  BEFORE being used in dynamic SQL. format(... %I) double-quotes it
--  defensively anyway. p_payload keys are also restricted to columns
--  declared in the data_model — see _safe_columns_for() below.
--
--  Idempotency
--  -----------
--  These RPCs are write operations and are NOT idempotent. The caller
--  (admin panel) is responsible for de-duplication. set_tenant_row
--  always INSERTs a new row even if the payload matches an existing
--  one — there is no ON CONFLICT clause because the unique key
--  semantics are table-specific (most tables have only `id` as the
--  unique key, which is generated server-side).
-- ───────────────────────────────────────────────────────────


-- ── 0. Shared input shape check ───────────────────────────────────────
-- Same snake_case identifier guard as get_tenant_collection. Rejects
-- garbage before any dynamic SQL fires.
CREATE OR REPLACE FUNCTION public._validate_tenant_table_name(p_table_name TEXT)
RETURNS VOID
LANGUAGE plpgsql IMMUTABLE
AS $$
BEGIN
    IF p_table_name IS NULL
       OR NOT (p_table_name ~ '^[a-z][a-z0-9_]{0,62}$')
    THEN
        RAISE EXCEPTION 'invalid_table'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
END;
$$;


-- ── 1. Shared auth + table-resolution helper ──────────────────────────
-- Returns the tenant schema name on success, raising one of three
-- well-known errors on failure. Each write RPC calls this first so
-- the auth/validation logic lives in exactly one place.
CREATE OR REPLACE FUNCTION public._tenant_write_check(
    p_project_id UUID,
    p_table_name TEXT
) RETURNS TEXT
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_user       UUID;
    v_schema     TEXT;
    v_data_model JSONB;
    v_known      BOOLEAN;
BEGIN
    -- Lightweight shape guard first.
    PERFORM public._validate_tenant_table_name(p_table_name);

    -- Auth: must be a logged-in user.
    v_user := auth.uid();
    IF v_user IS NULL THEN
        RAISE EXCEPTION 'access_denied'
            USING ERRCODE = 'insufficient_privilege';
    END IF;

    -- Look up the project's tenant_schema + data_model.
    SELECT tenant_schema, data_model
        INTO v_schema, v_data_model
    FROM public.chat_sessions
    WHERE id = p_project_id;

    -- "Project not found" includes the case where the row exists but
    -- has no tenant_schema (legacy / unprovisioned project) — there's
    -- nothing for us to write into either way.
    IF v_schema IS NULL THEN
        RAISE EXCEPTION 'project_not_found'
            USING ERRCODE = 'no_data_found';
    END IF;

    -- Auth: must be a member of THIS project.
    IF NOT public.is_project_member(p_project_id, v_user) THEN
        RAISE EXCEPTION 'access_denied'
            USING ERRCODE = 'insufficient_privilege';
    END IF;

    -- Table must appear in the project's data_model. Without this
    -- guard a member could write to arbitrary tables in their tenant
    -- schema (e.g. an undeclared `_secrets`); the guard keeps the
    -- write surface to exactly what the planner approved.
    SELECT bool_or(t->>'name' = p_table_name) INTO v_known
    FROM jsonb_array_elements(
        COALESCE(v_data_model->'tables', '[]'::jsonb)
    ) AS t;

    IF NOT COALESCE(v_known, FALSE) THEN
        RAISE EXCEPTION 'invalid_table'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    RETURN v_schema;
END;
$$;

REVOKE ALL ON FUNCTION public._tenant_write_check(UUID, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public._tenant_write_check(UUID, TEXT)
    TO authenticated, service_role;


-- ── 2. Column-allowlist helper ────────────────────────────────────────
-- Returns the columns to actually write — intersection of:
--   • columns physically present in the tenant table
--   • keys present in p_payload
--   • NOT in the reserved set (id / created_at / updated_at — those
--     are auto-managed by Postgres and must never be overridden by
--     the caller).
--
-- Returns the column list in stable declaration order so the
-- generated INSERT and the SELECT side of the INSERT line up
-- positionally.
CREATE OR REPLACE FUNCTION public._tenant_safe_columns(
    p_schema     TEXT,
    p_table_name TEXT,
    p_payload    JSONB
) RETURNS TEXT[]
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_keys      TEXT[];
    v_safe_cols TEXT[];
BEGIN
    v_keys := ARRAY(SELECT jsonb_object_keys(COALESCE(p_payload, '{}'::jsonb)));

    SELECT array_agg(column_name ORDER BY ordinal_position)
        INTO v_safe_cols
    FROM information_schema.columns
    WHERE table_schema = p_schema
      AND table_name   = p_table_name
      AND column_name  = ANY(v_keys)
      AND column_name NOT IN ('id', 'created_at', 'updated_at');

    RETURN COALESCE(v_safe_cols, ARRAY[]::TEXT[]);
END;
$$;

REVOKE ALL ON FUNCTION public._tenant_safe_columns(TEXT, TEXT, JSONB) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public._tenant_safe_columns(TEXT, TEXT, JSONB)
    TO authenticated, service_role;


-- ── 3. INSERT — public.set_tenant_row ─────────────────────────────────
-- Inserts ONE row into the project's tenant table. p_payload's keys
-- are filtered down to columns that physically exist on the table
-- (anything extra is silently dropped — defensive against drift
-- between the data_model and the actual DDL). Reserved columns
-- (id/created_at/updated_at) are always rejected from the payload so
-- their server-side defaults (gen_random_uuid(), NOW()) fire as
-- expected.
--
-- Returns the inserted row as JSONB. Raises:
--   • access_denied      — not authenticated / not a member
--   • project_not_found  — project missing or unprovisioned
--   • invalid_table      — table not in this project's data_model
--   • invalid_payload    — payload had no columns the table accepts
--   • (any Postgres error from the actual INSERT propagates as-is —
--     NOT NULL violations, CHECK violations, etc.)
CREATE OR REPLACE FUNCTION public.set_tenant_row(
    p_project_id UUID,
    p_table_name TEXT,
    p_payload    JSONB
) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_schema    TEXT;
    v_cols      TEXT[];
    v_col_list  TEXT;
    v_result    JSONB;
BEGIN
    v_schema := public._tenant_write_check(p_project_id, p_table_name);
    v_cols   := public._tenant_safe_columns(v_schema, p_table_name, p_payload);

    IF cardinality(v_cols) = 0 THEN
        RAISE EXCEPTION 'invalid_payload: no writable columns'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- Quote each identifier individually then comma-join.
    SELECT string_agg(quote_ident(c), ', ')
        INTO v_col_list
    FROM unnest(v_cols) c;

    -- INSERT ... SELECT ... FROM jsonb_populate_record(NULL::<row_type>, $1)
    -- — restricting to the safe column list means server-side defaults
    -- still fire for id/created_at/updated_at.
    EXECUTE format(
        'WITH ins AS (
            INSERT INTO %I.%I (%s)
            SELECT %s
            FROM jsonb_populate_record(NULL::%I.%I, $1)
            RETURNING *
         )
         SELECT to_jsonb(ins.*) FROM ins',
        v_schema, p_table_name, v_col_list,
        v_col_list,
        v_schema, p_table_name
    )
    INTO v_result
    USING p_payload;

    RETURN v_result;
END;
$$;

REVOKE ALL ON FUNCTION public.set_tenant_row(UUID, TEXT, JSONB) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.set_tenant_row(UUID, TEXT, JSONB)
    TO authenticated, service_role;


-- ── 4. UPDATE — public.update_tenant_row ──────────────────────────────
-- Patches ONE row identified by id. Only fields present in p_payload
-- are touched; other columns keep their current value. Reserved
-- columns (id/created_at/updated_at) are filtered out — the
-- set_updated_at trigger handles updated_at, and id is the primary
-- key so changing it would be a bug.
--
-- Returns the updated row as JSONB. Raises:
--   • access_denied      — not authenticated / not a member
--   • project_not_found  — project missing or unprovisioned
--   • invalid_table      — table not in this project's data_model
--   • row_not_found      — no row with that id in this table
CREATE OR REPLACE FUNCTION public.update_tenant_row(
    p_project_id UUID,
    p_table_name TEXT,
    p_row_id     UUID,
    p_payload    JSONB
) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_schema    TEXT;
    v_cols      TEXT[];
    v_set_pairs TEXT;
    v_result    JSONB;
BEGIN
    v_schema := public._tenant_write_check(p_project_id, p_table_name);
    v_cols   := public._tenant_safe_columns(v_schema, p_table_name, p_payload);

    IF cardinality(v_cols) = 0 THEN
        -- No-op update: just return the current row if it exists,
        -- so the caller still gets "row_not_found" semantics when the
        -- target id doesn't exist.
        EXECUTE format(
            'SELECT to_jsonb(t.*) FROM %I.%I t WHERE t.id = $1',
            v_schema, p_table_name
        ) INTO v_result USING p_row_id;
        IF v_result IS NULL THEN
            RAISE EXCEPTION 'row_not_found'
                USING ERRCODE = 'no_data_found';
        END IF;
        RETURN v_result;
    END IF;

    -- SET (c1, c2, ...) = (SELECT c1, c2, ... FROM jsonb_populate_record(t, $2))
    -- The `t` argument seeds jsonb_populate_record with the EXISTING row;
    -- p_payload then overlays its keys on top, giving us "patch semantics".
    SELECT
        '(' || string_agg(quote_ident(c), ', ') || ')'
    INTO v_set_pairs
    FROM unnest(v_cols) c;

    EXECUTE format(
        'WITH upd AS (
            UPDATE %I.%I AS t
            SET %s = (
                SELECT %s
                FROM jsonb_populate_record(t, $2)
            )
            WHERE t.id = $1
            RETURNING *
         )
         SELECT to_jsonb(upd.*) FROM upd',
        v_schema, p_table_name,
        v_set_pairs,
        (SELECT string_agg(quote_ident(c), ', ') FROM unnest(v_cols) c)
    )
    INTO v_result
    USING p_row_id, p_payload;

    IF v_result IS NULL THEN
        RAISE EXCEPTION 'row_not_found'
            USING ERRCODE = 'no_data_found';
    END IF;

    RETURN v_result;
END;
$$;

REVOKE ALL ON FUNCTION public.update_tenant_row(UUID, TEXT, UUID, JSONB) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.update_tenant_row(UUID, TEXT, UUID, JSONB)
    TO authenticated, service_role;


-- ── 5. DELETE — public.delete_tenant_row ──────────────────────────────
-- Removes ONE row identified by id. Returns VOID — the caller already
-- knows the id they asked to delete. Raises `row_not_found` when no
-- row matched so the admin UI can render "already removed" feedback
-- rather than silently succeeding.
CREATE OR REPLACE FUNCTION public.delete_tenant_row(
    p_project_id UUID,
    p_table_name TEXT,
    p_row_id     UUID
) RETURNS VOID
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_schema  TEXT;
    v_deleted INT;
BEGIN
    v_schema := public._tenant_write_check(p_project_id, p_table_name);

    EXECUTE format(
        'DELETE FROM %I.%I WHERE id = $1',
        v_schema, p_table_name
    ) USING p_row_id;

    GET DIAGNOSTICS v_deleted = ROW_COUNT;

    IF v_deleted = 0 THEN
        RAISE EXCEPTION 'row_not_found'
            USING ERRCODE = 'no_data_found';
    END IF;
END;
$$;

REVOKE ALL ON FUNCTION public.delete_tenant_row(UUID, TEXT, UUID) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.delete_tenant_row(UUID, TEXT, UUID)
    TO authenticated, service_role;


-- ── 6. Verification queries ───────────────────────────────────────────
-- After applying:
--
-- -- All three write RPCs registered + auth-helper visible
-- SELECT routine_name FROM information_schema.routines
-- WHERE routine_schema = 'public'
--   AND routine_name IN (
--     'set_tenant_row', 'update_tenant_row', 'delete_tenant_row'
--   )
-- ORDER BY routine_name;
--   → 3 rows
--
-- -- Grants — should show authenticated + service_role only,
-- -- never anon or PUBLIC
-- SELECT grantee, privilege_type
-- FROM information_schema.routine_privileges
-- WHERE routine_schema = 'public'
--   AND routine_name = 'set_tenant_row'
-- ORDER BY grantee;
