# Invite-to-Project — Production Deployment Guide

This document covers the one-time work required to ship the invite feature
to production: applying the migration, setting environment variables,
restarting services, smoke-testing the API, walking through the e2e flow,
and rolling back if anything goes wrong.

The feature was developed in three passes (invites, workspace-data wiring,
member file access). This guide bundles everything needed to ship pass #1.

---

## 0. Pre-flight verification (already passed)

I read the four critical files before writing this doc:

| File                                               | Verified                                                                                                                                                                                  |
| -------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `supabase/migrations/021_project_invites.sql`      | Valid PG syntax; RLS enabled; partial-unique index on `(project_id, lower(invitee_email)) WHERE status='pending'`; SECURITY DEFINER `revoke_project_invite` granted only to service_role. |
| `ai_engine/app/routers/invites.py`                 | All 7 endpoints present (POST invite, GET pending-for-me, POST accept, GET project invites, DELETE invite, GET project members, DELETE member).                                           |
| `frontend/src/components/members/InviteDialog.jsx` | All 5 `fetch` calls use relative `/api/...` paths — no hardcoded `localhost` or absolute URLs. The proxy URL is set via `PYTHON_BACKEND_URL` in `lib/gatekeeper.js`.                      |
| `frontend/src/app/accept-invite/page.js`           | Handles 404, 410, 403, 409, network failure, and the unauthenticated-redirect-to-login case (with `returnTo` round-trip).                                                                 |

---

## 1. Environment variables

### 1a. `ai_engine/.env` (and `docker-compose.yml`)

| Variable               | Example                                  | Where to source                                        | Breaks if missing                                                                                                                                     |
| ---------------------- | ---------------------------------------- | ------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| `SUPABASE_URL`         | `https://abcd1234.supabase.co`           | Supabase Dashboard → Settings → API → Project URL      | All DB calls fail; engine refuses to start unless `SUPABASE_ANON_KEY` is also set.                                                                    |
| `SUPABASE_ANON_KEY`    | `eyJhbGciOiJIUzI1...`                    | Dashboard → Settings → API → Publishable key           | RLS-scoped reads (e.g. `list_members`) fall back to admin client or 500.                                                                              |
| `SUPABASE_SERVICE_KEY` | `eyJhbGciOiJIUzI1...` (different key)    | Dashboard → Settings → API → Secret key                | **All invite create/accept/revoke writes fail.** Sending invite emails (`admin.invite_user_by_email`) fails.                                          |
| `SUPABASE_JWT_SECRET`  | base64 string                            | Dashboard → Settings → API → JWT Settings → JWT Secret | Frontend Bearer JWTs decoded _without_ signature check (dev fallback). RLS-tagged queries will not resolve `auth.uid()` correctly.                    |
| `INTERNAL_API_KEY`     | any 32+ char string, must match frontend | shared secret (you generate)                           | Engine refuses to start (see `internal_api_key_required` validator).                                                                                  |
| `APP_URL`              | `https://app.lucid.ai`                   | the public frontend root                               | The invite email's `redirect_to` falls back to the first `ALLOWED_ORIGINS` entry — usually `http://localhost:3000`. **Users will get a broken link.** |
| `ALLOWED_ORIGINS`      | `https://app.lucid.ai,https://lucid.ai`  | comma-separated public frontend origins                | CORS preflight fails on every API call. Browsers will see `Access-Control-Allow-Origin` errors.                                                       |
| `ENCRYPTION_KEY`       | 32-char shared secret                    | shared with frontend (existing)                        | Unrelated to invites but required for engine startup.                                                                                                 |
| `ANTHROPIC_API_KEY`    | `sk-ant-...`                             | Anthropic console                                      | Required for engine startup; not used by the invite path.                                                                                             |

### 1b. `frontend/.env.local`

| Variable                        | Example                        | Where to source          | Breaks if missing                                                                                                                                |
| ------------------------------- | ------------------------------ | ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| `PYTHON_BACKEND_URL`            | `https://api.lucid.ai`         | the public ai_engine URL | Defaults to `http://localhost:8000`. **All `/api/*` proxies 503 in production.**                                                                 |
| `INTERNAL_API_KEY`              | (must equal ai_engine's value) | shared secret            | Server-to-server X-Internal-Key calls return 401. The Bearer-JWT path still works for browser-driven flows, so users may not notice immediately. |
| `NEXT_PUBLIC_SUPABASE_URL`      | `https://abcd1234.supabase.co` | Dashboard → API          | Browser-side auth helpers (`getSupabaseBrowserClient`) fail to initialize.                                                                       |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | `eyJhbGciOi...`                | Dashboard → API          | Same as above.                                                                                                                                   |

> **Rule of thumb**: `INTERNAL_API_KEY`, `ENCRYPTION_KEY`, and `SUPABASE_JWT_SECRET` must be **identical** in `ai_engine` and `frontend`. Mismatched values manifest as confusing 401s and "Row not found" errors with no clear cause.

### 1c. Supabase Dashboard (one-time UI config)

| Setting                      | Where                                              | Required value                                                                                                                                                                                                                               |
| ---------------------------- | -------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Google OAuth provider        | Authentication → Providers → Google                | Enabled, with client_id + client_secret from Google Cloud Console. The OAuth callback URL must be `https://<project-ref>.supabase.co/auth/v1/callback`.                                                                                      |
| Site URL                     | Authentication → URL Configuration → Site URL      | `https://app.lucid.ai` (your public frontend). Used as the default `redirect_to` when none is provided.                                                                                                                                      |
| Additional Redirect URLs     | Authentication → URL Configuration → Redirect URLs | Must include **`https://app.lucid.ai/accept-invite`** and `https://app.lucid.ai/auth/callback`. Without `/accept-invite` on this allowlist, Supabase will refuse the `redirect_to` we pass and the invite email will land on a generic page. |
| SMTP (recommended)           | Authentication → Settings → SMTP Settings          | Custom SMTP (SendGrid, Postmark, Resend, AWS SES). Without it, Supabase uses the built-in sender which is **heavily rate-limited (~4 emails/hour) and frequently flagged as spam**.                                                          |
| Email template — Invite user | Authentication → Email Templates → Invite user     | Default template works. Optionally customize subject ("You've been invited to {{ .Data.project_name }} on Lucid") and body. The accept-invite URL is auto-injected as `{{ .ConfirmationURL }}`.                                              |

---

## 2. Manual deployment checklist

Follow these steps in order. Each step has a verification command — do not move forward until it passes.

### Step 1 — Apply the migration

In Supabase Dashboard → SQL Editor → New query, paste the **entire contents** of `supabase/migrations/021_project_invites.sql`, then Run.

**Verification query** (run in SQL Editor immediately after):

```sql
-- Should return one row.
SELECT table_name FROM information_schema.tables
 WHERE table_schema = 'public' AND table_name = 'project_invites';

-- Should return 3 rows (the two partial indexes + the unique-token index).
SELECT indexname FROM pg_indexes
 WHERE schemaname = 'public' AND tablename = 'project_invites';

-- Should return 2 rows (invites_select_owner, invites_select_invitee).
SELECT polname FROM pg_policy WHERE polrelid = 'public.project_invites'::regclass;

-- Should return one row showing role=service_role with EXECUTE on the helper.
SELECT grantee, privilege_type FROM information_schema.routine_privileges
 WHERE routine_name = 'revoke_project_invite';
```

If any of these returns the wrong count, **stop**. Inspect the migration log; do not proceed.

### Step 2 — Set environment variables

Update `ai_engine` deployment env (Render/Fly/your-host UI) with the values from Section 1a, especially:

- `APP_URL` — **must** be set to your public frontend root, or invite emails will link to localhost.
- `SUPABASE_SERVICE_KEY` — must be the _Secret key_, not the publishable one.

Update `frontend` deployment env with the values from Section 1b.

Update Supabase Dashboard with Section 1c.

### Step 3 — Restart services

```bash
# ai_engine
docker-compose restart ai_engine
# or your hosted-platform restart command

# frontend
# Vercel/Netlify: trigger a fresh deploy after changing env vars (env-var
# changes do NOT propagate to existing builds — a redeploy is required).
```

Watch the ai_engine logs on boot. You should see:

```
Lucid AI Engine starting …
🔷 VERTEX AI ACTIVE | project=... | location=... | ADC=✅ found
Session reaper started ...
```

If you see `INTERNAL_API_KEY is required` — your env var is missing. If you see CORS errors in the browser console after starting — your `ALLOWED_ORIGINS` is wrong.

### Step 4 — API smoke test

From a terminal with a valid Supabase JWT in `$JWT` (grab one from devtools after signing in):

```bash
# Should return 200 + {"invites": []} for a freshly-deployed system.
curl -i -H "Authorization: Bearer $JWT" \
  https://api.lucid.ai/api/v1/invites/me

# 404 with an invalid project_id is correct.
curl -i -H "Authorization: Bearer $JWT" \
  https://api.lucid.ai/api/v1/projects/00000000-0000-0000-0000-000000000000/invites
```

Pass criteria: both return JSON (not HTML error pages), the first is 200, the second is 404 or 403 (depending on whether the project exists).

### Step 5 — Manual e2e (see Section 3 below)

Walk the two-user flow end-to-end as described.

### Step 6 — Rollback procedure

If anything is broken after Steps 1–5, recovery is **safe to roll back** because the migration is purely additive and the feature is gated by missing UI in the legacy workspace screen (existing users see no change).

**Code rollback**: redeploy the previous commit on both `ai_engine` and `frontend`. Existing data in `project_invites` is preserved — no DB downtime.

**Full feature backout** (if you also want the table gone):

```sql
-- Run in Supabase SQL Editor. Order matters: drop the function first
-- because the table drop will leave it dangling.
DROP FUNCTION IF EXISTS public.revoke_project_invite(UUID);
DROP TABLE   IF EXISTS public.project_invites CASCADE;
```

You do **not** need to revert migration 020 (`project_members`) — it was shipped earlier and is depended on by other code paths.

---

## 3. Manual e2e test script

You will need:

- **Alice** — a regular browser, signed in to Lucid (Google OAuth).
- **Bob** — an incognito window, NOT yet signed in. Have his real email accessible (Gmail tab open in another window).

### Step 1 — Alice creates / opens a project

1. As Alice, go to `https://app.lucid.ai`.
2. Sign in with Google.
3. Click **New Project** (or open an existing one).
4. Wait for the workspace page to load. The header should show the project title (not "Untitled project" placeholder).
5. **Screenshot**: header showing real title. Save as `docs/screenshots/01-alice-workspace.png`.

### Step 2 — Alice opens the Share dialog

6. Click the **Share** button in the header (blue, right of the action buttons).
7. The dialog opens. You should see:
   - Title: "Invite collaborators to <Project Title>"
   - Email input
   - "Members (1)" section with Alice listed as Owner
   - No pending invites yet.
8. **Screenshot**: `docs/screenshots/02-share-dialog-open.png`.

### Step 3 — Alice invites Bob

9. Type Bob's real email into the input.
10. Click **Send invite**.
11. Expected outcomes:
    - Green success banner: "Invite sent to bob@example.com."
    - The form clears.
    - A new row appears under "Pending invitations (1)" — Bob's email + "Pending" badge + revoke (trash) icon.
12. **Screenshot**: `docs/screenshots/03-invite-sent.png`.

### Step 4 — Verify the invite email

13. In Supabase Dashboard → Authentication → Logs (or your SMTP provider's dashboard), confirm the email was queued.
14. Switch to Bob's email tab. Wait up to 60s for the email to arrive (custom SMTP) or up to several minutes (default Supabase).
15. **The email body should contain a link** to `https://app.lucid.ai/accept-invite?token=...` (32 hex chars after the `=`).
16. **Screenshot**: `docs/screenshots/04-invite-email-received.png`.

### Step 5 — Bob accepts the invite

17. In an **incognito window**, paste the link from the email.
18. You land on `/accept-invite?token=...` with the "Accepting invite…" spinner.
19. Because Bob isn't signed in, the page should automatically redirect to `/login?returnTo=%2Faccept-invite%3Ftoken%3D...`.
20. Sign in as Bob (Google OAuth, using the **same email the invite was sent to**).
21. After OAuth completes, Bob is redirected back to `/accept-invite?token=...`.
22. The page now shows "Accepting invite…", then switches to "Opening project…", then redirects to `/workspace/<project_id>`.
23. Bob now sees the same workspace Alice does — same title, same chat messages.
24. **Screenshot**: `docs/screenshots/05-bob-in-workspace.png`.

### Step 6 — Verify state in DB

```sql
-- Should show status='accepted' with accepted_at populated.
SELECT id, invitee_email, status, accepted_at
  FROM public.project_invites
 ORDER BY created_at DESC LIMIT 1;

-- Should show 2 rows: alice (owner), bob (editor).
SELECT user_id, role FROM public.project_members
 WHERE project_id = '<the project id>';
```

### Step 7 — Negative tests

- **Wrong email**: Have Alice invite a third email. Sign in as Bob (not the invited email) and try to accept that link. Expect the red error screen: _"This invite was sent to a different email address."_
- **Re-accept**: With Bob already in the project, click the same invite link again. Expect _"This invite has already been accepted."_ (409).
- **Revoke**: As Alice, in the Share dialog, click the trash icon on Bob's invite (after re-inviting another email). Verify it disappears from "Pending invitations". Then try to accept that link from incognito — expect _"This invite has expired or been revoked."_ (410).

### Step 8 — Alice removes Bob

25. In the Share dialog, hover Bob's row in Members. The remove (trash) button appears.
26. Click it. Bob's row should disappear.
27. As Bob (still in incognito), refresh the project page. He should now see the 403 screen: _"You don't have access to this project."_

---

## 4. Known issues / things to watch in production

| Issue                                                                                                | Symptom                                                                                                                                                                                                                                                                                                                         | Mitigation                                                                                                                                                                                                            |
| ---------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Supabase built-in SMTP is rate-limited and flagged as spam**                                       | Invite emails delayed by minutes or land in Gmail Spam. Owners see "Invite sent" but recipient never receives it.                                                                                                                                                                                                               | Configure custom SMTP in Supabase before broad rollout (SendGrid, Postmark, Resend, AWS SES). The 4-emails-per-hour cap on the default sender will become limiting quickly.                                           |
| **`APP_URL` missing → invite link points to `http://localhost:3000`**                                | Recipients click and get DNS failure / connection refused.                                                                                                                                                                                                                                                                      | Set `APP_URL` explicitly in production env. The fallback to `ALLOWED_ORIGINS[0]` is only safe if the first entry is your real public URL.                                                                             |
| **`/accept-invite` not in Supabase's Redirect URL allowlist**                                        | Supabase refuses the `redirect_to` parameter; email link goes to default Site URL instead. User signs in but isn't redirected to invite acceptance — they land on the dashboard with no idea what happened.                                                                                                                     | Add `https://app.lucid.ai/accept-invite` to                                                                                                                                                                           |
| Authentication → URL Configuration → Redirect URLs. Test the full flow end-to-end after this change. |
| **JWT expires mid-flow during invite acceptance**                                                    | User signs in → Supabase token issued → user clicks accept after >1 hour → token expired → 401 on POST `/api/invites/{token}/accept`.                                                                                                                                                                                           | Frontend auto-refreshes the session before calling the accept endpoint. If the refresh fails (rare), the user sees a network error and refreshing the page re-runs auth. Acceptable for v1.                           |
| **Cookie-domain mismatch breaks `requireAuth()`**                                                    | After Bob signs in via OAuth, the `/accept-invite` page calls `supabase.auth.getUser()` and gets `null` despite a valid session existing on a different subdomain.                                                                                                                                                              | Ensure `NEXT_PUBLIC_SUPABASE_URL` is the same canonical host in both the OAuth callback page and the accept-invite page. If you use `app.lucid.ai` for one and `www.lucid.ai` for the other, cookies won't be shared. |
| **CORS preflight failures with `APP_URL` distinct from `ALLOWED_ORIGINS`**                           | Browser console shows `No 'Access-Control-Allow-Origin' header`.                                                                                                                                                                                                                                                                | Make sure `APP_URL` is one of the comma-separated values in `ALLOWED_ORIGINS`. The `app_url` property in config.py builds the accept-invite URL; `ALLOWED_ORIGINS` controls CORS — they must agree.                   |
| **Email-casing edge case at OAuth**                                                                  | Some OAuth providers normalize emails differently; if the JWT email comes back as `Bob@Example.com` and the invite was stored as `bob@example.com`, the case-insensitive comparison handles it. **But** if Supabase strips the email from the JWT (some configurations), the service falls back to `public.users.email` lookup. | The implementation already handles this (`_resolve_user_email` in `invites.py`). Watch the logs for `Authenticated user has no email on file` 400s — that's the failure mode.                                         |
| **Re-inviting a previously-revoked email**                                                           | Should work — the partial unique index only blocks pending duplicates. If it 409s, the previous invite was _accepted_ (not revoked), meaning the user is already a member.                                                                                                                                                      | Inspect with `SELECT status FROM project_invites WHERE invitee_email = ... ORDER BY created_at DESC` and explain to the owner that the user is already on the project.                                                |
| **The Next.js routes use `[id]` for both DELETE-by-invite-id and POST-by-token**                     | Path collision risk if Next.js routing changes in a future framework version. Documented in the route handlers.                                                                                                                                                                                                                 | No action needed today. If routing breaks, split into `/api/invites/[id]` and `/api/invites/by-token/[token]`.                                                                                                        |
| **`/files/list` and `/files/export` are now member-readable**                                        | A side-effect of the recent member-access fix in `_resolve_workspace`. Not strictly an invite-feature issue, but bundled because it ships at the same time.                                                                                                                                                                     | Verify the desired access model is "members see everything" before broad rollout. If you want owner-only listing, revert the helper change in `ai_engine/app/routers/files.py`.                                       |

---

## 5. Post-deployment monitoring

Watch these for the first 48 hours:

- `ai_engine` logs for `Supabase invite_user_by_email failed for ...` warnings. A spike means SMTP rate-limited.
- `ai_engine` logs for `Supabase error in create_invite: code=23505 ...` — should be near-zero. A spike means duplicate-invite UX is leaking through.
- Supabase Dashboard → Database → `project_invites` row count should grow monotonically. Status distribution should skew pending → accepted; high `expired` count without `accepted` count suggests emails aren't landing.
- Auth → Logs → confirm `invite_user_by_email` calls succeed with HTTP 2xx.

---

_Last updated: 2026-05-15._
