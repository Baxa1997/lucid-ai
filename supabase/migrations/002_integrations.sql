-- ─────────────────────────────────────────────────────────────────────────────
--  Migration: 002_integrations
--  Creates the integrations table and enables RLS.
--
--  PostgREST maps snake_case columns automatically.
--
--  NOTE: user_id is UUID and references users(id), so auth.uid() can be
--  compared directly without a cast.
-- ─────────────────────────────────────────────────────────────────────────────


-- ── integrations ──────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS integrations (
    id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider               TEXT NOT NULL,
    label                  TEXT,
    access_token_encrypted TEXT NOT NULL,
    iv                     TEXT NOT NULL,
    external_username      TEXT,
    scopes                 TEXT,
    created_at             TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS ix_integrations_user_provider
    ON integrations (user_id, provider);

CREATE INDEX IF NOT EXISTS ix_integrations_user_id
    ON integrations (user_id);

ALTER TABLE integrations ENABLE ROW LEVEL SECURITY;

CREATE POLICY "integrations_select" ON integrations FOR SELECT
    USING (user_id = auth.uid());

CREATE POLICY "integrations_insert" ON integrations FOR INSERT
    WITH CHECK (user_id = auth.uid());

CREATE POLICY "integrations_update" ON integrations FOR UPDATE
    USING      (user_id = auth.uid())
    WITH CHECK (user_id = auth.uid());

CREATE POLICY "integrations_delete" ON integrations FOR DELETE
    USING (user_id = auth.uid());
