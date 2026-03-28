-- ─────────────────────────────────────────────────────────────────────────────
--  Migration: 005_project_deployments
--  Tracks deployed projects: repo URL, deploy URL, method, GitLab project ID.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS project_deployments (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id           UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    project_id        TEXT,
    repo_url          TEXT,
    deploy_url        TEXT,
    deploy_method     TEXT,                      -- 'vercel' | 'gitlab-ci'
    gitlab_project_id INTEGER,
    status            TEXT DEFAULT 'deployed',   -- 'deployed' | 'exported' | 'archived'
    deployed_at       TIMESTAMPTZ DEFAULT NOW(),
    updated_at        TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE (user_id, project_id)
);

CREATE INDEX IF NOT EXISTS ix_project_deployments_user
    ON project_deployments (user_id);

ALTER TABLE project_deployments ENABLE ROW LEVEL SECURITY;

CREATE POLICY "deploys_select_own" ON project_deployments FOR SELECT
    USING (user_id = auth.uid());

CREATE POLICY "deploys_insert_own" ON project_deployments FOR INSERT
    WITH CHECK (user_id = auth.uid());

CREATE POLICY "deploys_update_own" ON project_deployments FOR UPDATE
    USING      (user_id = auth.uid())
    WITH CHECK (user_id = auth.uid());

CREATE POLICY "deploys_delete_own" ON project_deployments FOR DELETE
    USING (user_id = auth.uid());

CREATE OR REPLACE TRIGGER project_deployments_updated_at
    BEFORE UPDATE ON project_deployments
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
