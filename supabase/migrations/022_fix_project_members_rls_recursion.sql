-- ───────────────────────────────────────────────────────────
--  022_fix_project_members_rls_recursion.sql
--
--  Fixes an infinite-recursion error in the project_members SELECT
--  policy shipped in 020. The original policy was:
--
--      USING (project_id IN (
--          SELECT project_id FROM public.project_members
--           WHERE user_id = auth.uid()
--      ))
--
--  Postgres detects the self-reference and aborts with:
--      ERROR: 42P17 infinite recursion detected in policy
--      for relation "project_members"
--
--  Migration 020 already declared an ``is_project_member`` helper as
--  SECURITY DEFINER specifically so RLS policies could call it without
--  re-entering the policy machinery. We rewrite the policy to use it.
--
--  Symptom before fix: every read on project_members returned 500;
--  the Invite dialog reported "Members (0)" for the actual owner,
--  and the email-invite form was hidden because the frontend's
--  isOwner derivation requires seeing the owner's own row.
-- ───────────────────────────────────────────────────────────

DROP POLICY IF EXISTS "members_select" ON public.project_members;

CREATE POLICY "members_select" ON public.project_members FOR SELECT
    USING (public.is_project_member(project_id, auth.uid()));
