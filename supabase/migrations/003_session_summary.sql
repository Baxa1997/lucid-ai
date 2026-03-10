-- Migration: Add summary and last_task columns to chat_sessions
-- These columns store a digest of the most recent task and its result.
-- Used by the context replay system to quickly rebuild agent memory
-- on future sessions without loading all individual messages.

ALTER TABLE chat_sessions
    ADD COLUMN IF NOT EXISTS summary   TEXT DEFAULT '',
    ADD COLUMN IF NOT EXISTS last_task  TEXT DEFAULT '';

-- Index on project_id for faster context replay lookups
CREATE INDEX IF NOT EXISTS ix_chat_sessions_project_id
    ON chat_sessions (project_id);
