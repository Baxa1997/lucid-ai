-- Migration: Add generation_complete flag to chat_sessions
-- Used to detect whether project generation completed successfully,
-- so re-entering a conversation doesn't restart the pipeline from scratch.
ALTER TABLE chat_sessions
    ADD COLUMN IF NOT EXISTS generation_complete BOOLEAN NOT NULL DEFAULT FALSE;
