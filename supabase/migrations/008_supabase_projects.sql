-- ─────────────────────────────────────────────────────────────────────────────
--  Migration: 008_supabase_projects
--  Dedicated table for tracking provisioned Supabase projects.
--  Encrypted credentials (anon key, service key, db password) are stored
--  as AES-256-CBC hex pairs (enc + iv). Never expose service_key to frontend.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS supabase_projects (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    project_id          TEXT,                               -- platform project reference
    project_slug        TEXT NOT NULL,
    supabase_ref        TEXT NOT NULL,                      -- the xxxx in xxxx.supabase.co
    supabase_url        TEXT NOT NULL,
    -- Encrypted credentials (enc + iv hex pairs)
    anon_key_enc        TEXT,
    anon_key_iv         TEXT,
    service_key_enc     TEXT,
    service_key_iv      TEXT,
    db_password_enc     TEXT,
    db_password_iv      TEXT,
    region              TEXT DEFAULT 'us-east-1',
    status              TEXT NOT NULL DEFAULT 'PROVISIONING'
                        CHECK (status IN ('PROVISIONING','ACTIVE','PAUSED','DELETED')),
    auth_providers      JSONB DEFAULT '[]'::jsonb,          -- configured (enabled) providers
    suggested_providers JSONB DEFAULT '[]'::jsonb,          -- suggested but not yet enabled
    gitlab_project_id   INTEGER,                            -- GitLab project ID for CI/CD
    last_ping_at        TIMESTAMPTZ,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW(),
    deleted_at          TIMESTAMPTZ,

    UNIQUE (user_id, project_id)
);

CREATE INDEX IF NOT EXISTS ix_supabase_projects_user
    ON supabase_projects (user_id);

CREATE INDEX IF NOT EXISTS ix_supabase_projects_status
    ON supabase_projects (status) WHERE status = 'ACTIVE';

CREATE INDEX IF NOT EXISTS ix_supabase_projects_ref
    ON supabase_projects (supabase_ref);

ALTER TABLE supabase_projects ENABLE ROW LEVEL SECURITY;

CREATE POLICY "sb_projects_select_own" ON supabase_projects FOR SELECT
    USING (user_id = auth.uid());

CREATE POLICY "sb_projects_insert_own" ON supabase_projects FOR INSERT
    WITH CHECK (user_id = auth.uid());

CREATE POLICY "sb_projects_update_own" ON supabase_projects FOR UPDATE
    USING      (user_id = auth.uid())
    WITH CHECK (user_id = auth.uid());

CREATE POLICY "sb_projects_delete_own" ON supabase_projects FOR DELETE
    USING (user_id = auth.uid());

CREATE OR REPLACE TRIGGER supabase_projects_updated_at
    BEFORE UPDATE ON supabase_projects
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
