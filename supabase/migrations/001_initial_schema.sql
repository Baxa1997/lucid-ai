-- ─────────────────────────────────────────────────────────────────────────────
--  Migration: 001_initial_schema
--  Creates all Supabase-native tables with indexes, triggers, and RLS.
--
--  Table ownership:
--    • users          → profile table; Supabase Auth owns identity in auth.users
--    • chat_sessions  → ai_engine owned
--    • chat_messages  → ai_engine owned
--    • integrations   → created by 002_integrations.sql
--
--  Security model:
--    • RLS is enabled on every table — users can only access their own rows.
--    • Policies use auth.uid() which Supabase resolves from the JWT automatically.
--    • The Supabase JWT Secret (Dashboard → Settings → API → JWT Settings) must
--      equal the SUPABASE_JWT_SECRET env var in the ai_engine.
-- ─────────────────────────────────────────────────────────────────────────────


-- ── Shared trigger function ───────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$ BEGIN NEW.updated_at = NOW(); RETURN NEW; END; $$ LANGUAGE plpgsql;


-- ── users ─────────────────────────────────────────────────────────────────────
-- Public profile table. Supabase Auth manages identity in auth.users.
-- A trigger auto-creates this row whenever a new user signs up via OAuth.

CREATE TABLE IF NOT EXISTS public.users (
    id          UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    email       TEXT UNIQUE NOT NULL,
    name        TEXT,
    avatar_url  TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_users_email ON users (email);

ALTER TABLE users ENABLE ROW LEVEL SECURITY;

CREATE POLICY "users_select_own" ON users FOR SELECT
    USING (id = auth.uid());

CREATE POLICY "users_update_own" ON users FOR UPDATE
    USING      (id = auth.uid())
    WITH CHECK (id = auth.uid());

CREATE OR REPLACE TRIGGER users_updated_at
    BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- Auto-populate users profile on first sign-in via Supabase Auth.
-- Each OAuth provider stores metadata under slightly different keys:
--   Google  → full_name (display name), avatar_url or picture
--   GitHub  → name, avatar_url
--   GitLab  → name, avatar_url
-- COALESCE picks the first non-null value so all three providers work.
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO public.users (id, email, name, avatar_url)
    VALUES (
        NEW.id,
        NEW.email,
        COALESCE(
            NEW.raw_user_meta_data ->> 'full_name',
            NEW.raw_user_meta_data ->> 'name'
        ),
        COALESCE(
            NEW.raw_user_meta_data ->> 'avatar_url',
            NEW.raw_user_meta_data ->> 'picture'
        )
    )
    ON CONFLICT (id) DO NOTHING;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public;

CREATE OR REPLACE TRIGGER on_auth_user_created
    AFTER INSERT ON auth.users
    FOR EACH ROW EXECUTE FUNCTION public.handle_new_user();


-- ── chat_sessions ─────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS chat_sessions (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    agent_session_id TEXT,
    project_id       TEXT,
    title            VARCHAR(255),
    model_provider   VARCHAR(50),
    is_active        BOOLEAN DEFAULT TRUE,
    created_at       TIMESTAMPTZ DEFAULT NOW(),
    updated_at       TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_chat_sessions_user_id ON chat_sessions (user_id);

ALTER TABLE chat_sessions ENABLE ROW LEVEL SECURITY;

CREATE POLICY "sessions_select" ON chat_sessions FOR SELECT
    USING (user_id = auth.uid());

CREATE POLICY "sessions_insert" ON chat_sessions FOR INSERT
    WITH CHECK (user_id = auth.uid());

CREATE POLICY "sessions_update" ON chat_sessions FOR UPDATE
    USING      (user_id = auth.uid())
    WITH CHECK (user_id = auth.uid());

CREATE POLICY "sessions_delete" ON chat_sessions FOR DELETE
    USING (user_id = auth.uid());

CREATE OR REPLACE TRIGGER chat_sessions_updated_at
    BEFORE UPDATE ON chat_sessions
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();


-- ── chat_messages ─────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS chat_messages (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id    UUID NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    role          VARCHAR(20) NOT NULL,
    content       TEXT NOT NULL,
    event_type    VARCHAR(100),
    metadata_json JSONB,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_chat_messages_session_id ON chat_messages (session_id);

ALTER TABLE chat_messages ENABLE ROW LEVEL SECURITY;

-- Messages are append-only — no UPDATE policy is intentional.
-- Once written, a message is never modified; only inserted or deleted.
-- Messages inherit access control from their parent session.
CREATE POLICY "messages_select" ON chat_messages FOR SELECT
    USING (
        session_id IN (
            SELECT id FROM chat_sessions WHERE user_id = auth.uid()
        )
    );

CREATE POLICY "messages_insert" ON chat_messages FOR INSERT
    WITH CHECK (
        session_id IN (
            SELECT id FROM chat_sessions WHERE user_id = auth.uid()
        )
    );

CREATE POLICY "messages_delete" ON chat_messages FOR DELETE
    USING (
        session_id IN (
            SELECT id FROM chat_sessions WHERE user_id = auth.uid()
        )
    );
