-- Migration: Add platform_repo_url to chat_sessions
-- When wizard projects are auto-created on the platform GitHub,
-- this column stores the repo URL so users can export later.

ALTER TABLE chat_sessions
    ADD COLUMN IF NOT EXISTS platform_repo_url TEXT;

-- User's own repo (set after Export to GitHub/GitLab/Bitbucket)
ALTER TABLE chat_sessions
    ADD COLUMN IF NOT EXISTS user_repo_url TEXT;

ALTER TABLE chat_sessions
    ADD COLUMN IF NOT EXISTS user_repo_provider TEXT;
