-- -------------------------------------------------------------------
-- 029_chat_sessions_visual_dna.sql
--
-- Add `visual_dna` JSONB to chat_sessions. Website pipeline writes
-- the dict produced by Stage 3 (extract_research_signals) here so
-- linked admin projects can inherit their parent's visual identity
-- verbatim, and so standalone admins have a place to persist their
-- own brand_extractor output for future regenerations.
--
-- Nullable: old projects (created before this migration) will read
-- as NULL. The admin pipeline treats NULL as "fall back to standalone
-- brand extraction".
-- -------------------------------------------------------------------

ALTER TABLE public.chat_sessions
    ADD COLUMN IF NOT EXISTS visual_dna JSONB;

COMMENT ON COLUMN public.chat_sessions.visual_dna IS
    'Brand identity signals (palette, typography_voice, cultural_intensity, etc). '
    'Website pipeline writes the full dict from extract_research_signals; '
    'admin pipeline writes a 6-field subset from admin_brand_extractor.';
