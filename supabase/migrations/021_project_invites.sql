-- ───────────────────────────────────────────────────────────
--  021_project_invites.sql
--
--  Email-based invitations for project_members. An owner sends an invite
--  to an email address; Supabase Auth mails a magic link that lands on
--  the frontend's /accept-invite page; the accepting user calls the API
--  which inserts a row into project_members(role='editor').
--
--  Status lifecycle:
--      pending  →  accepted   (user clicked link + API insert succeeded)
--      pending  →  revoked    (owner cancelled before acceptance)
--      pending  →  expired    (lazy state — read by checking expires_at)
--
--  We keep accepted/revoked rows around for audit. Only one pending row
--  per (project_id, invitee_email) is enforced by a partial UNIQUE index
--  so re-inviting a previously-revoked email is allowed.
--
--  Permissions model (no role tier yet — every invitee becomes 'editor'):
--    • Owner: SELECT / INSERT / UPDATE invites on their projects
--    • Invitee: SELECT pending invites matching their JWT email
--    • All other writes happen via service-role from the ai_engine
-- ───────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.project_invites (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID NOT NULL REFERENCES public.chat_sessions(id) ON DELETE CASCADE,
    -- Note: references public.users (the profile row), not auth.users — matches
    -- the existing convention in project_members and chat_sessions.
    inviter_id      UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    invitee_email   TEXT NOT NULL,
    token           TEXT NOT NULL UNIQUE,
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'accepted', 'revoked', 'expired')),
    expires_at      TIMESTAMPTZ NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    accepted_at     TIMESTAMPTZ,
    revoked_at      TIMESTAMPTZ
);

-- Lookup index for the invitee-facing GET /api/invites/me query.
CREATE INDEX IF NOT EXISTS ix_project_invites_invitee_pending
    ON public.project_invites (invitee_email, status)
    WHERE status = 'pending';

-- Lookup index for the owner-facing GET /api/projects/{id}/invites query.
CREATE INDEX IF NOT EXISTS ix_project_invites_project_pending
    ON public.project_invites (project_id, status)
    WHERE status = 'pending';

-- One open invite per (project, email). Re-inviting after revoke is allowed
-- because the partial predicate ignores non-pending rows.
CREATE UNIQUE INDEX IF NOT EXISTS uq_project_invites_pending_per_email
    ON public.project_invites (project_id, lower(invitee_email))
    WHERE status = 'pending';

ALTER TABLE public.project_invites ENABLE ROW LEVEL SECURITY;

-- Owners can see / manage invites on their projects.
-- is_project_member is broader than "owner" (any editor is a member), so for
-- INSERT/UPDATE we additionally check the role via the project_members table.
CREATE POLICY "invites_select_owner" ON public.project_invites FOR SELECT
    USING (
        EXISTS (
            SELECT 1 FROM public.project_members pm
             WHERE pm.project_id = project_invites.project_id
               AND pm.user_id    = auth.uid()
               AND pm.role       = 'owner'
        )
    );

-- Invitee can see their own pending invites (lowercased compare so casing
-- of the email at invite-creation time doesn't block lookup).
CREATE POLICY "invites_select_invitee" ON public.project_invites FOR SELECT
    USING (
        status = 'pending'
        AND lower(invitee_email) = lower(auth.email())
    );

-- INSERT / UPDATE go through service-role (ai_engine) — no user-facing policy.
-- DELETE intentionally absent — invites are revoked (status update), never
-- hard-deleted, to preserve audit history.


-- ── Helper: revoke invite (used by RPC or service-role writes) ───────────
-- Marked SECURITY DEFINER so it can flip status without inheriting RLS, but
-- callable only by service_role (ai_engine). Authenticated users hit the
-- REST endpoint, which calls the service-role client.
CREATE OR REPLACE FUNCTION public.revoke_project_invite(p_invite_id UUID)
RETURNS BOOLEAN
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_count INTEGER;
BEGIN
    UPDATE public.project_invites
       SET status = 'revoked', revoked_at = NOW()
     WHERE id = p_invite_id AND status = 'pending';
    GET DIAGNOSTICS v_count = ROW_COUNT;
    RETURN v_count > 0;
END;
$$;

REVOKE EXECUTE ON FUNCTION public.revoke_project_invite(UUID) FROM PUBLIC;
GRANT  EXECUTE ON FUNCTION public.revoke_project_invite(UUID) TO service_role;
