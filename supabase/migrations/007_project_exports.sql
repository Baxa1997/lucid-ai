-- ─────────────────────────────────────────────────────────────────────────────
--  Migration: 007_project_exports
--  Tracks code exports to external providers (GitHub, GitLab, Bitbucket).
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS project_exports (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    project_id    TEXT NOT NULL,
    provider      TEXT NOT NULL,                   -- 'github' | 'gitlab' | 'bitbucket'
    repo_url      TEXT,
    repo_name     TEXT,
    exported_at   TIMESTAMPTZ DEFAULT NOW(),
    updated_at    TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE (user_id, project_id, provider)
);

CREATE INDEX IF NOT EXISTS ix_project_exports_user
    ON project_exports (user_id);

ALTER TABLE project_exports ENABLE ROW LEVEL SECURITY;

CREATE POLICY "exports_select_own" ON project_exports FOR SELECT
    USING (user_id = auth.uid());

CREATE POLICY "exports_insert_own" ON project_exports FOR INSERT
    WITH CHECK (user_id = auth.uid());

CREATE POLICY "exports_update_own" ON project_exports FOR UPDATE
    USING      (user_id = auth.uid())
    WITH CHECK (user_id = auth.uid());
