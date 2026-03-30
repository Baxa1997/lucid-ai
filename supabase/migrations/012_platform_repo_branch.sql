-- Migration: Add platform_repo_branch to chat_sessions
-- When the AI generates code in new_project_mode, it pushes to a new
-- branch (lucid-gen-YYYYMMDD-HHMMSS) instead of main. This column
-- stores that branch name so the frontend can show a direct link.

ALTER TABLE chat_sessions
    ADD COLUMN IF NOT EXISTS platform_repo_branch TEXT;

-- Index for fast lookups
CREATE INDEX IF NOT EXISTS idx_chat_sessions_platform_repo_branch
    ON chat_sessions (platform_repo_branch)
    WHERE platform_repo_branch IS NOT NULL;
