-- ─────────────────────────────────────────────────────────────────────────────
--  Migration: 004_project_credentials
--  Stores encrypted Supabase/provider credentials per user + project.
--  Service keys are AES-encrypted — never exposed to the frontend.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS project_credentials (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    project_id      TEXT,
    provider        TEXT NOT NULL DEFAULT 'supabase',
    supabase_ref    TEXT,
    supabase_url    TEXT,
    anon_key_enc    TEXT,
    anon_key_iv     TEXT,
    service_key_enc TEXT,
    service_key_iv  TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE (user_id, project_id, provider)
);

CREATE INDEX IF NOT EXISTS ix_project_credentials_user
    ON project_credentials (user_id);

ALTER TABLE project_credentials ENABLE ROW LEVEL SECURITY;

CREATE POLICY "creds_select_own" ON project_credentials FOR SELECT
    USING (user_id = auth.uid());

CREATE POLICY "creds_insert_own" ON project_credentials FOR INSERT
    WITH CHECK (user_id = auth.uid());

CREATE POLICY "creds_update_own" ON project_credentials FOR UPDATE
    USING      (user_id = auth.uid())
    WITH CHECK (user_id = auth.uid());

CREATE POLICY "creds_delete_own" ON project_credentials FOR DELETE
    USING (user_id = auth.uid());

CREATE OR REPLACE TRIGGER project_credentials_updated_at
    BEFORE UPDATE ON project_credentials
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
