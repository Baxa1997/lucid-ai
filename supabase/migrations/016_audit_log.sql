-- ───────────────────────────────────────────────────────────
--  016_audit_log.sql
--
--  Append-only audit trail of security-relevant actions.
--
--  Background
--  ----------
--  We need a forensic log answering "what did this user do, when, from
--  where" for incident response and support investigations. Examples of
--  events worth recording:
--    • integration.saved / integration.deleted  — PATs added or removed
--    • session.created                          — agent session started
--    • pr.created                               — PR/MR opened via our API
--    • task.started                             — user kicked off a run
--
--  Design
--  ------
--  • Append-only: users can INSERT their own rows (so the ai_engine writing
--    under a user JWT works) and SELECT their own rows (so support tools
--    can surface them in-app). UPDATE and DELETE are denied for everyone
--    except the service_role key (which bypasses RLS).
--  • metadata is jsonb so new event shapes don't need migrations.
--  • ip_addr / user_agent captured at insert time. Nullable — the
--    ai_engine sometimes writes server-to-server where there is no client
--    request context.
-- ───────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS audit_logs (
    id           BIGSERIAL PRIMARY KEY,
    user_id      UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    event_type   TEXT NOT NULL,
    event_key    TEXT,                      -- free-form target (repo URL, session id, integration provider, etc.)
    metadata     JSONB NOT NULL DEFAULT '{}'::jsonb,
    ip_addr      INET,
    user_agent   TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Hot paths: "show me this user's recent activity" and
-- "find everyone who did X in the last 24h".
CREATE INDEX IF NOT EXISTS audit_logs_user_created_idx
    ON audit_logs (user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS audit_logs_event_created_idx
    ON audit_logs (event_type, created_at DESC);

-- ── RLS ────────────────────────────────────────────────────────────────

ALTER TABLE audit_logs ENABLE ROW LEVEL SECURITY;

-- Insert: a user can record their own actions (ai_engine writes under the
-- user's JWT, so auth.uid() matches user_id). Service-role bypasses RLS.
DROP POLICY IF EXISTS "audit_logs_insert_self" ON audit_logs;
CREATE POLICY "audit_logs_insert_self"
    ON audit_logs FOR INSERT
    TO authenticated
    WITH CHECK (auth.uid() = user_id);

-- Select: a user can read only their own audit trail. Support staff with
-- the service_role key can read everyone's.
DROP POLICY IF EXISTS "audit_logs_select_self" ON audit_logs;
CREATE POLICY "audit_logs_select_self"
    ON audit_logs FOR SELECT
    TO authenticated
    USING (auth.uid() = user_id);

-- No UPDATE / DELETE policies → append-only for authenticated users.
-- service_role bypasses RLS for admin cleanup if ever needed.
