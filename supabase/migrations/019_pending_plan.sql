-- Migration: Add pending_plan to chat_sessions so plans survive ai_engine restarts.
--
-- Background
-- ----------
-- The plan-confirmation gate kept plans in an in-memory dict keyed by
-- chat_session_id. Acceptable for websocket reconnects, but lost on
-- ai_engine restart — and a slow user (or one who walked away for a
-- while) would come back to a wiped pending plan even though the UI
-- still showed the card.
--
-- What changes
-- ------------
-- A JSONB column holds the most-recent emitted plan envelope for each
-- chat session. The ai_engine writes it when the plan is sent and
-- clears it on confirm / reject / timeout / error. Old rows stay NULL.
ALTER TABLE chat_sessions
    ADD COLUMN IF NOT EXISTS pending_plan JSONB;
