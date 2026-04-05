-- ─────────────────────────────────────────────────────────────────────────────
--  Migration: 013_godaddy_integrations
--  Multi-account GoDaddy DNS integration per user.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS godaddy_accounts (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id         UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  label           TEXT NOT NULL DEFAULT '',
  domain          TEXT NOT NULL DEFAULT '',
  record_type     TEXT NOT NULL DEFAULT 'A',
  target          TEXT NOT NULL DEFAULT '',
  api_key_enc     TEXT,
  api_key_iv      TEXT,
  api_secret_enc  TEXT,
  api_secret_iv   TEXT,
  created_at      TIMESTAMPTZ DEFAULT now(),
  updated_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_godaddy_accounts_user ON godaddy_accounts(user_id);

-- Row Level Security
ALTER TABLE godaddy_accounts ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can manage their own GoDaddy accounts"
  ON godaddy_accounts
  FOR ALL
  USING (auth.uid() = user_id)
  WITH CHECK (auth.uid() = user_id);
