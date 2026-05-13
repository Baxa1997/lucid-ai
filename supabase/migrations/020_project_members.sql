-- ───────────────────────────────────────────────────────────
--  020_project_members.sql
--
--  Adds project-level membership so multiple users can collaborate on
--  the same project (chat session). Step 1 of the collaboration feature.
--
--  Model
--  -----
--  • A "project" is a chat_sessions row (chat_sessions.id is the
--    canonical project key).
--  • Membership lives in project_members(project_id, user_id, role).
--  • Roles in v1: 'owner' | 'editor'. 'owner' has destructive rights
--    (rename, delete, manage members); 'editor' can view chat history
--    and send tasks.
--
--  What changes for RLS
--  --------------------
--  • chat_sessions SELECT: any member of the project.
--  • chat_sessions INSERT/UPDATE/DELETE: owner only (unchanged semantics —
--    user_id = auth.uid() is preserved). Renaming / deleting the project
--    stays an owner action in v1.
--  • chat_messages SELECT/INSERT: any member of the project (via session).
--    Delete is still blocked at the policy layer (see 015).
--
--  Other tables (project_credentials, deployments, exports, subscriptions,
--  audit_log) intentionally stay owner-only — those carry billing and
--  secret material. Loosening them is a separate decision.
--
--  Billing attribution: usage events should be recorded against the
--  PROJECT OWNER, not the acting user. The ai_engine reads
--  chat_sessions.user_id when attributing token spend, so the change here
--  does not affect billing automatically (good — owner pays by default).
-- ───────────────────────────────────────────────────────────

-- ── project_members ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.project_members (
    project_id   UUID NOT NULL REFERENCES public.chat_sessions(id) ON DELETE CASCADE,
    user_id      UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    role         VARCHAR(20) NOT NULL DEFAULT 'editor',
    added_by     UUID REFERENCES public.users(id) ON DELETE SET NULL,
    added_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (project_id, user_id),
    CONSTRAINT project_members_role_check CHECK (role IN ('owner', 'editor'))
);

CREATE INDEX IF NOT EXISTS ix_project_members_user_id
    ON public.project_members (user_id);

ALTER TABLE public.project_members ENABLE ROW LEVEL SECURITY;

-- A member of a project can SEE everyone on that project (so the UI can
-- render an avatar stack / members panel without leaking other projects).
-- Write operations on project_members go through the ai_engine using the
-- service role; no user-facing INSERT/UPDATE/DELETE policy is created.
CREATE POLICY "members_select" ON public.project_members FOR SELECT
    USING (
        project_id IN (
            SELECT project_id FROM public.project_members WHERE user_id = auth.uid()
        )
    );


-- ── Helper: is_project_member ─────────────────────────────────────────────
-- SECURITY DEFINER so it can read project_members from inside other tables'
-- RLS policies without recursing into project_members's own SELECT policy.
-- Marked STABLE so the planner can cache the result within a single query.
CREATE OR REPLACE FUNCTION public.is_project_member(
    p_project_id UUID,
    p_user_id    UUID
) RETURNS BOOLEAN
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = public
AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.project_members
        WHERE project_id = p_project_id AND user_id = p_user_id
    );
$$;

-- Lock down EXECUTE: the function should only be callable from RLS contexts
-- (postgres role) and the service role. Authenticated callers don't need
-- direct access — they hit it transitively via the policies below.
REVOKE EXECUTE ON FUNCTION public.is_project_member(UUID, UUID) FROM PUBLIC;
GRANT  EXECUTE ON FUNCTION public.is_project_member(UUID, UUID) TO authenticated, service_role;


-- ── Backfill: every existing chat_session gets an owner member row ────────
-- ON CONFLICT DO NOTHING is defensive in case this migration is re-run.
INSERT INTO public.project_members (project_id, user_id, role, added_by, added_at)
SELECT id, user_id, 'owner', user_id, created_at
FROM   public.chat_sessions
ON CONFLICT (project_id, user_id) DO NOTHING;


-- ── Trigger: auto-add the creator as owner on every new chat_session ──────
CREATE OR REPLACE FUNCTION public.add_creator_as_project_owner()
RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    INSERT INTO public.project_members (project_id, user_id, role, added_by)
    VALUES (NEW.id, NEW.user_id, 'owner', NEW.user_id)
    ON CONFLICT (project_id, user_id) DO NOTHING;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS chat_sessions_add_owner ON public.chat_sessions;
CREATE TRIGGER chat_sessions_add_owner
    AFTER INSERT ON public.chat_sessions
    FOR EACH ROW EXECUTE FUNCTION public.add_creator_as_project_owner();


-- ── Rewrite RLS on chat_sessions: SELECT becomes member-aware ─────────────
-- INSERT/UPDATE/DELETE keep their owner-only semantics. UPDATE/DELETE still
-- require user_id = auth.uid() so rename / delete remain owner actions.
DROP POLICY IF EXISTS "sessions_select" ON public.chat_sessions;
CREATE POLICY "sessions_select" ON public.chat_sessions FOR SELECT
    USING (public.is_project_member(id, auth.uid()));


-- ── Rewrite RLS on chat_messages: SELECT/INSERT become member-aware ───────
-- These previously checked user_id directly on chat_sessions; now they
-- check membership so editors can read the chat history and send messages.
DROP POLICY IF EXISTS "messages_select" ON public.chat_messages;
CREATE POLICY "messages_select" ON public.chat_messages FOR SELECT
    USING (public.is_project_member(session_id, auth.uid()));

DROP POLICY IF EXISTS "messages_insert" ON public.chat_messages;
CREATE POLICY "messages_insert" ON public.chat_messages FOR INSERT
    WITH CHECK (public.is_project_member(session_id, auth.uid()));

-- DELETE policy stays absent — message deletion is service-role only
-- (per migration 015). CASCADE delete from chat_sessions still works.
