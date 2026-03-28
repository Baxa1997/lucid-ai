-- Add GitHub token columns to user_settings
-- Mirrors the existing gitlab_token_enc/iv and vercel_token_enc/iv pattern

ALTER TABLE user_settings
  ADD COLUMN IF NOT EXISTS github_token_enc text,
  ADD COLUMN IF NOT EXISTS github_token_iv  text;
