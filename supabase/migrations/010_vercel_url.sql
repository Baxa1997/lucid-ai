-- Migration: Add vercel_url to chat_sessions
-- Stores the Vercel preview URL so it persists across page reloads.

ALTER TABLE chat_sessions
    ADD COLUMN IF NOT EXISTS vercel_url TEXT;
