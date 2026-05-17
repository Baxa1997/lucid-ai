-- ───────────────────────────────────────────────────────────
--  025_get_tenant_collection.sql
--
--  Public RPC that lets a generated website fetch rows from
--  its own per-tenant Postgres schema through PostgREST.
--
--  Why this RPC exists
--  -------------------
--  Tenant schemas (`tenant_<id>`) are created dynamically per
--  project (migration 023). PostgREST only exposes schemas listed
--  in the static `db_schemas` config — there is no API to add a
--  schema at runtime. Without a workaround, generated sites can
--  not reach their own tables via the anon-keyed supabase-js
--  client.
--
--  This function is the workaround: a single PUBLIC entry point
--  that takes (project_id, table_name) and returns the rows as a
--  JSONB array. SECURITY DEFINER so it can read across schemas.
--  The function enforces public visibility by checking the table's
--  `public_read` flag from the project's stored `data_model` — we
--  refuse to expose admin-only tables even if a caller guesses the
--  name.
--
--  What this RPC does NOT do
--  -------------------------
--  • No write path. Inserts/updates/deletes still go through
--    `public.execute_ddl` (server-side only, service_role) or a
--    forthcoming admin-write RPC. This function is read-only.
--  • No pagination beyond a hard ceiling. Pagination is the
--    caller's job; this function caps the per-call row count so a
--    runaway query can't dump a million rows.
--  • No filtering / sorting. v1 returns the whole table; sorting
--    happens in the generated UI. A future migration can add
--    `p_order_by` / `p_filters` once the codegen layer needs them.
-- ───────────────────────────────────────────────────────────


-- Identifier safety: PostgreSQL's `format(... %I)` quotes any
-- identifier defensively, so even a malicious `p_table_name` like
-- `"; DROP TABLE x; --` becomes a quoted identifier and fails the
-- table-exists check rather than executing anything destructive.
-- We still bound `p_table_name` length and shape in the regex
-- below as defense-in-depth — every legitimate column name in
-- the data model is snake_case (validated by data_model.py).

CREATE OR REPLACE FUNCTION public.get_tenant_collection(
    p_project_id  UUID,
    p_table_name  TEXT,
    p_limit       INT  DEFAULT 200
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_schema     TEXT;
    v_data_model JSONB;
    v_is_public  BOOLEAN;
    v_clamped    INT;
    v_result     JSONB;
BEGIN
    -- Validate p_limit and clamp.
    IF p_limit IS NULL OR p_limit <= 0 THEN
        v_clamped := 50;
    ELSE
        v_clamped := LEAST(p_limit, 500);  -- never serve more than 500 rows per call
    END IF;

    -- p_table_name shape check (snake_case, ≤63 chars). Rejects
    -- garbage *before* we touch the DB.
    IF p_table_name IS NULL
       OR NOT (p_table_name ~ '^[a-z][a-z0-9_]{0,62}$')
    THEN
        RAISE EXCEPTION 'get_tenant_collection: invalid table name'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- Look up the tenant schema and the data_model in one read.
    SELECT tenant_schema, data_model
        INTO v_schema, v_data_model
    FROM public.chat_sessions
    WHERE id = p_project_id;

    -- Project gone or never provisioned → empty array (a missing
    -- project shouldn't be distinguishable from a missing table
    -- to anon callers; return [] for both).
    IF v_schema IS NULL THEN
        RETURN '[]'::JSONB;
    END IF;

    -- Confirm the requested table is `public_read` in the project's
    -- own data_model. Without this guard, anon could enumerate any
    -- table by guessing its name.
    SELECT bool_or(
        (t->>'name' = p_table_name)
        AND COALESCE((t->>'public_read')::BOOLEAN, FALSE)
    ) INTO v_is_public
    FROM jsonb_array_elements(
        COALESCE(v_data_model->'tables', '[]'::jsonb)
    ) AS t;

    IF NOT COALESCE(v_is_public, FALSE) THEN
        -- Table not in this project's data_model, or marked private.
        -- Return empty rather than raising — uniform "not found"
        -- behaviour for anon callers.
        RETURN '[]'::JSONB;
    END IF;

    -- All checks pass. Pull the rows.
    EXECUTE format(
        'SELECT COALESCE(jsonb_agg(row_to_json(t)::jsonb), ''[]''::jsonb)
         FROM (SELECT * FROM %I.%I LIMIT $1) t',
        v_schema, p_table_name
    )
    INTO v_result
    USING v_clamped;

    RETURN COALESCE(v_result, '[]'::JSONB);
END;
$$;

-- Default permissions deny PUBLIC; explicit grant to the standard
-- Supabase API roles. Service role inherits via *anything-to-anything*
-- defaults but we list it explicitly for symmetry / readability.
REVOKE ALL ON FUNCTION public.get_tenant_collection(UUID, TEXT, INT)
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.get_tenant_collection(UUID, TEXT, INT)
    TO anon, authenticated, service_role;


-- ── Verification queries ──────────────────────────────────────────────
-- After applying:
--
-- -- 1) Function exists
-- SELECT routine_name FROM information_schema.routines
-- WHERE routine_schema = 'public' AND routine_name = 'get_tenant_collection';
--   → 1 row
--
-- -- 2) Smoke against a known project (replace the UUID)
-- SELECT public.get_tenant_collection(
--   'YOUR-PROJECT-UUID-HERE'::uuid, 'menu_items'
-- );
--   → JSONB array of rows (or [] if no project / not public_read)
--
-- -- 3) Negative paths
-- SELECT public.get_tenant_collection(gen_random_uuid(), 'menu_items');
--   → []  (unknown project)
-- SELECT public.get_tenant_collection(
--   'YOUR-PROJECT-UUID-HERE'::uuid, 'NoSuchTable!'
-- );
--   → ERROR: invalid table name
