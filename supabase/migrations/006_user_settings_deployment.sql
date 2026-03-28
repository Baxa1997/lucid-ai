-- ─────────────────────────────────────────────────────────────────────────────
--  Migration: 006_user_settings_deployment
--  Adds deployment configuration columns to user_settings.
-- ─────────────────────────────────────────────────────────────────────────────

ALTER TABLE user_settings
  ADD COLUMN IF NOT EXISTS gitlab_host        TEXT DEFAULT '',
  ADD COLUMN IF NOT EXISTS gitlab_group       TEXT DEFAULT '',
  ADD COLUMN IF NOT EXISTS gitlab_token_enc   TEXT,
  ADD COLUMN IF NOT EXISTS gitlab_token_iv    TEXT,
  ADD COLUMN IF NOT EXISTS ops_repo_url       TEXT DEFAULT '',
  ADD COLUMN IF NOT EXISTS ops_repo_branch    TEXT DEFAULT 'main',
  ADD COLUMN IF NOT EXISTS vercel_token_enc   TEXT,
  ADD COLUMN IF NOT EXISTS vercel_token_iv    TEXT,
  ADD COLUMN IF NOT EXISTS vercel_team_id     TEXT DEFAULT '',
  ADD COLUMN IF NOT EXISTS k8s_namespace      TEXT DEFAULT 'frontend-prod',
  ADD COLUMN IF NOT EXISTS k8s_domain         TEXT DEFAULT '',
  ADD COLUMN IF NOT EXISTS k8s_tls_secret     TEXT DEFAULT '',
  ADD COLUMN IF NOT EXISTS registry_url       TEXT DEFAULT '';
