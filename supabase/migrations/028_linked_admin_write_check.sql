-- -------------------------------------------------------------------
-- 028_linked_admin_write_check.sql
--
-- Patch migration 026's write auth helper so linked admin projects
-- can write into their parent website tenant while membership remains
-- checked against the admin project's own chat_sessions row.
--
-- Read path migration 027 already has this behavior for
-- get_tenant_collection_authenticated. This migration makes
-- set/update/delete_tenant_row use the same project resolution model.
-- -------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public._tenant_write_check(
    p_project_id UUID,
    p_table_name TEXT
) RETURNS TEXT
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_user              UUID;
    v_parent_project_id UUID;
    v_schema            TEXT;
    v_data_model        JSONB;
    v_known             BOOLEAN;
BEGIN
    -- Lightweight shape guard first.
    PERFORM public._validate_tenant_table_name(p_table_name);

    -- Auth: must be a logged-in user.
    v_user := auth.uid();
    IF v_user IS NULL THEN
        RAISE EXCEPTION 'access_denied'
            USING ERRCODE = 'insufficient_privilege';
    END IF;

    -- Auth is checked against the CALLING project. For a linked
    -- admin, that is the admin panel row, not the website row.
    IF NOT public.is_project_member(p_project_id, v_user) THEN
        RAISE EXCEPTION 'access_denied'
            USING ERRCODE = 'insufficient_privilege';
    END IF;

    SELECT parent_project_id, tenant_schema, data_model
        INTO v_parent_project_id, v_schema, v_data_model
    FROM public.chat_sessions
    WHERE id = p_project_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'project_not_found'
            USING ERRCODE = 'no_data_found';
    END IF;

    -- Linked admins store membership and product metadata on their
    -- own row, but tenant_schema/data_model live on the parent row.
    IF v_parent_project_id IS NOT NULL THEN
        SELECT tenant_schema, data_model
            INTO v_schema, v_data_model
        FROM public.chat_sessions
        WHERE id = v_parent_project_id;
    END IF;

    -- "Project not found" includes linked-to-missing and unprovisioned
    -- projects: there is no tenant schema to write into either way.
    IF v_schema IS NULL THEN
        RAISE EXCEPTION 'project_not_found'
            USING ERRCODE = 'no_data_found';
    END IF;

    -- Table must appear in the effective data_model. Without this
    -- guard a member could write to arbitrary tables in the tenant
    -- schema.
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
