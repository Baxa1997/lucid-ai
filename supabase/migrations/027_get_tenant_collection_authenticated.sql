-- ───────────────────────────────────────────────────────────
--  027_get_tenant_collection_authenticated.sql
--
--  Admin-only read path for per-tenant data. Pairs with:
--    025 — public.get_tenant_collection   (anon reads, public_read tables only)
--    026 — public.set/update/delete_tenant_row (authenticated writes)
--
--  Why a separate function instead of extending 025
--  -------------------------------------------------
--  025 deliberately returns [] for any table that isn't
--  `public_read=true` — so the generated public website can't even
--  enumerate the existence of internal tables. Admins need the
--  opposite: read EVERY table in the project's data_model, including
--  ones marked private. Different security model → different RPC.
--
--  Linking semantics (parent_project_id)
--  -------------------------------------
--  An admin panel is provisioned as its own chat_sessions row with
--  `parent_project_id` pointing at the website project it manages.
--  Phase 3.1's `resolve_tenant_for_project` resolves that to the
--  parent's tenant_schema; this RPC mirrors that resolution.
--
--  IMPORTANT: the membership check is performed against the
--  CALLING `p_project_id`, never against the parent. An admin panel
--  has its own `project_members` list — separate from the website
--  it manages. A user invited only to the admin can read tenant
--  data; a user invited only to the website cannot. This is by
--  design — websites are publishing surfaces, admins are operating
--  surfaces, and they have independent permission boundaries.
--
--  What this RPC does NOT do (v1)
--  ------------------------------
--  • No WHERE-clause / filter support. Pagination + ORDER BY only.
--    Adding filters means sanitizing operator + value types; out
--    of scope for v1.
--  • No JOINs. The data_model has no relationships in v1 anyway
--    (see DATA_MODEL_FORMAT.md). When relationships ship in v2 we
--    can add a `get_tenant_collection_with_joins` sibling.
--  • No multi-table fetch in one call. Generated admin pages
--    issue one RPC per table — the per-call cost is negligible
--    and the per-table cache invalidation story is much simpler.
-- ───────────────────────────────────────────────────────────


CREATE OR REPLACE FUNCTION public.get_tenant_collection_authenticated(
    p_project_id      UUID,
    p_table_name      TEXT,
    p_order_by        TEXT  DEFAULT 'created_at',
    p_order_direction TEXT  DEFAULT 'desc',
    p_limit           INT   DEFAULT 100,
    p_offset          INT   DEFAULT 0
)
RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_user          UUID;
    v_parent_id     UUID;
    v_tenant_schema TEXT;
    v_data_model    JSONB;
    v_known         BOOLEAN;
    v_direction     TEXT;
    v_result        JSONB;
BEGIN
    -- ── Argument shape guards (cheap; fail before any DB read) ──

    IF p_order_direction IS NULL
       OR lower(p_order_direction) NOT IN ('asc', 'desc')
    THEN
        RAISE EXCEPTION 'invalid_order_direction'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    v_direction := lower(p_order_direction);

    -- Same identifier shape used by tenant_sql_generator + data_model
    -- validator. Anything outside it can't be a real column name, so
    -- we reject without even reading the table's metadata.
    IF p_order_by IS NULL
       OR NOT (p_order_by ~ '^[a-z_][a-z0-9_]*$')
    THEN
        RAISE EXCEPTION 'invalid_order_column'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- Same regex on the requested table name. data_model validation
    -- below is the real authorization, but failing this regex first
    -- means a malicious caller can't waste a chat_sessions read with
    -- a junk identifier.
    IF p_table_name IS NULL
       OR NOT (p_table_name ~ '^[a-z][a-z0-9_]{0,62}$')
    THEN
        RAISE EXCEPTION 'invalid_table'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    IF p_limit IS NULL OR p_limit < 1 OR p_limit > 1000 THEN
        RAISE EXCEPTION 'invalid_limit'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    IF p_offset IS NULL OR p_offset < 0 THEN
        RAISE EXCEPTION 'invalid_offset'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- ── Auth: caller must be a logged-in user ─────────────────────
    v_user := auth.uid();
    IF v_user IS NULL THEN
        RAISE EXCEPTION 'access_denied'
            USING ERRCODE = 'insufficient_privilege';
    END IF;

    -- ── Look up the project + its parent link ─────────────────────
    SELECT parent_project_id, tenant_schema, data_model
        INTO v_parent_id, v_tenant_schema, v_data_model
    FROM public.chat_sessions
    WHERE id = p_project_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'project_not_found'
            USING ERRCODE = 'no_data_found';
    END IF;

    -- ── Auth: membership is checked against THE ADMIN'S row, ───────
    -- never against the parent. The admin panel's own
    -- project_members table is the source of truth — see the file
    -- header for the design rationale.
    IF NOT public.is_project_member(p_project_id, v_user) THEN
        RAISE EXCEPTION 'access_denied'
            USING ERRCODE = 'insufficient_privilege';
    END IF;

    -- ── Resolve effective tenant_schema + data_model ──────────────
    -- If linked, both come from the parent. If standalone, both come
    -- from this row. A linked admin with NO parent_project_id row, or
    -- a parent without tenant_schema/data_model, surfaces `no_tenant`
    -- so the caller can render a "not yet provisioned" state rather
    -- than crash on a dynamic-SQL NULL.
    IF v_parent_id IS NOT NULL THEN
        SELECT tenant_schema, data_model
            INTO v_tenant_schema, v_data_model
        FROM public.chat_sessions
        WHERE id = v_parent_id;
        -- A linked-to-nothing project is a data integrity bug; treat
        -- as "no tenant available" rather than letting it through.
        IF NOT FOUND THEN
            RAISE EXCEPTION 'no_tenant'
                USING ERRCODE = 'no_data_found';
        END IF;
    END IF;

    IF v_tenant_schema IS NULL THEN
        RAISE EXCEPTION 'no_tenant'
            USING ERRCODE = 'no_data_found';
    END IF;

    -- ── Confirm the requested table is in the (effective) data_model.
    -- This is the per-project allowlist that prevents a member from
    -- reading random tables that happen to exist in the tenant
    -- schema but aren't part of the project's declared model.
    SELECT bool_or(t->>'name' = p_table_name) INTO v_known
    FROM jsonb_array_elements(
        COALESCE(v_data_model->'tables', '[]'::jsonb)
    ) AS t;

    IF NOT COALESCE(v_known, FALSE) THEN
        RAISE EXCEPTION 'invalid_table'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- ── Run the dynamic SELECT ────────────────────────────────────
    -- format(... %I) double-quotes every identifier; order_by + table
    -- are already shape-validated above. Direction is interpolated
    -- via %s after lower-casing + allow-list — there is no path for
    -- an attacker-controlled string to reach this layer untouched.
    EXECUTE format(
        'SELECT COALESCE(jsonb_agg(row_to_json(t)::jsonb), ''[]''::jsonb)
         FROM (
             SELECT * FROM %I.%I
             ORDER BY %I %s
             LIMIT $1 OFFSET $2
         ) t',
        v_tenant_schema, p_table_name, p_order_by, v_direction
    )
    INTO v_result
    USING p_limit, p_offset;

    RETURN COALESCE(v_result, '[]'::JSONB);
END;
$$;

REVOKE ALL ON FUNCTION public.get_tenant_collection_authenticated(
    UUID, TEXT, TEXT, TEXT, INT, INT
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.get_tenant_collection_authenticated(
    UUID, TEXT, TEXT, TEXT, INT, INT
) TO authenticated, service_role;


-- ── Verification queries ──────────────────────────────────────────────
-- After applying:
--
-- -- 1) Function present
-- SELECT routine_schema, routine_name, security_type
-- FROM information_schema.routines
-- WHERE routine_name = 'get_tenant_collection_authenticated';
--   → 1 row, security_type=DEFINER
--
-- -- 2) Grants — authenticated + service_role only, never anon
-- SELECT grantee, privilege_type
-- FROM information_schema.routine_privileges
-- WHERE routine_name = 'get_tenant_collection_authenticated'
-- ORDER BY grantee;
--   → 'authenticated' + 'service_role' (no 'anon', no 'PUBLIC')
--
-- -- 3) Reload PostgREST schema cache so the RPC is callable
--      immediately rather than after the next auto-reload.
-- NOTIFY pgrst, 'reload schema';
