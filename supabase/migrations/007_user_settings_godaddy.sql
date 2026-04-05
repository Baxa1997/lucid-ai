-- ─────────────────────────────────────────────────────────────────────────────
--  Migration: 007_user_settings_godaddy
--  Adds GoDaddy API configuration and GitLab invite username to user_settings.
-- ─────────────────────────────────────────────────────────────────────────────

ALTER TABLE user_settings
  ADD COLUMN IF NOT EXISTS godaddy_domain         TEXT DEFAULT '',
  ADD COLUMN IF NOT EXISTS godaddy_record_type    TEXT DEFAULT 'A',
  ADD COLUMN IF NOT EXISTS godaddy_target         TEXT DEFAULT '',
  ADD COLUMN IF NOT EXISTS godaddy_api_key_enc    TEXT,
  ADD COLUMN IF NOT EXISTS godaddy_api_key_iv     TEXT,
  ADD COLUMN IF NOT EXISTS godaddy_api_secret_enc TEXT,
  ADD COLUMN IF NOT EXISTS godaddy_api_secret_iv  TEXT,
  ADD COLUMN IF NOT EXISTS gitlab_invite_username TEXT DEFAULT 'udevs';
