-- ─────────────────────────────────────────────────────────────────────────────
--  Migration: 004_user_settings
--  Stores per-user LLM configuration (provider, model, encrypted API key).
--
--  Security model:
--    • RLS enabled — users can only read/write their own row.
--    • api_key is stored encrypted (handled at the application layer).
--    • One row per user (UNIQUE constraint on user_id).
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.user_settings (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    llm_provider    TEXT NOT NULL DEFAULT 'google',
    llm_model       TEXT NOT NULL DEFAULT 'gemini/gemini-3-flash-preview',
    api_key_enc     TEXT,          -- AES-GCM encrypted API key (base64)
    api_key_iv      TEXT,          -- AES-GCM IV (base64)
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT uq_user_settings_user_id UNIQUE (user_id)
);

CREATE INDEX IF NOT EXISTS ix_user_settings_user_id ON public.user_settings (user_id);

ALTER TABLE public.user_settings ENABLE ROW LEVEL SECURITY;

CREATE POLICY "user_settings_select_own" ON public.user_settings FOR SELECT
    USING (user_id = auth.uid());

CREATE POLICY "user_settings_insert_own" ON public.user_settings FOR INSERT
    WITH CHECK (user_id = auth.uid());

CREATE POLICY "user_settings_update_own" ON public.user_settings FOR UPDATE
    USING      (user_id = auth.uid())
    WITH CHECK (user_id = auth.uid());

CREATE POLICY "user_settings_delete_own" ON public.user_settings FOR DELETE
    USING (user_id = auth.uid());

CREATE OR REPLACE TRIGGER user_settings_updated_at
    BEFORE UPDATE ON public.user_settings
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
