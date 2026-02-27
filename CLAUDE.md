# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Lucid AI is a chat-first AI coding assistant (think Devin / OpenHands). Users connect their GitHub or GitLab repos, then describe a task in chat. An agent clones the repo in an isolated Docker sandbox, writes code, pushes a branch, and opens a PR — all visible as streaming events in the chat. There is no code editor pane; everything happens through chat. The platform uses the OpenHands SDK for agent execution.

## Architecture

Two services + Supabase, orchestrated via `docker-compose.yml`:

1. **`frontend/`** — Next.js (App Router) with Tailwind CSS, Zustand state management
2. **`ai_engine/`** — FastAPI + OpenHands SDK V1 (4-package architecture). Primary AI backend with WebSocket agent communication, LiteLLM model routing, Docker-sandboxed execution
3. **Supabase** — Hosted PostgreSQL + Auth. Manages user identity (Google, GitHub, GitLab OAuth). The ai_engine connects via `supabase-py` async client — anon key + user JWT for RLS-enforced access, service_role key for server-to-server calls.

### Auth flow
- Users sign in via Supabase Auth (Google / GitHub / GitLab OAuth)
- Supabase issues a JWT signed with `SUPABASE_JWT_SECRET`
- Frontend passes the JWT to the ai_engine as `Authorization: Bearer <token>`
- ai_engine validates the JWT and uses it to authenticate Supabase DB calls (RLS enforced)
- Server-to-server calls (Next.js → ai_engine) use `X-User-ID` + `X-Internal-Key` headers instead; the ai_engine uses the service_role key (RLS bypassed, explicit `user_id` filters applied)

### Per-user isolation
No organization/multi-tenancy layer. Every resource belongs directly to a User. Git provider tokens (GitHub/GitLab PATs) are encrypted with AES-256-CBC before storage — the same key and algorithm is used by both the frontend (`frontend/src/lib/crypto.js`) and the ai_engine (`ai_engine/app/services/integrations.py`). The `ENCRYPTION_KEY` env var must be identical in both services.

## Common Commands

### AI Engine (Python)
```bash
cd ai_engine
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

### Docker (full stack)
```bash
docker-compose up              # ai_engine :8000
```

### Supabase (one-time setup)
Run each migration file in order via **Dashboard → SQL Editor → New query**:
- `supabase/migrations/001_initial_schema.sql`
- `supabase/migrations/002_integrations.sql`

## Key Files

- `ai_engine/app/__init__.py` — FastAPI app factory; registers all routers
- `ai_engine/app/config.py` — Pydantic BaseSettings; required: `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_JWT_SECRET`, `SUPABASE_SERVICE_KEY`, `ENCRYPTION_KEY`
- `ai_engine/app/supabase_client.py` — Three client helpers: `managed_client(user_jwt)` (anon key + JWT, RLS enforced), `managed_admin_client()` (service_role key, RLS bypassed), `db_client(user_jwt: str | None)` (dispatcher)
- `ai_engine/app/auth.py` — Supabase Auth JWT validation (HS256, `SUPABASE_JWT_SECRET`, `verify_aud: False`); Bearer path sets `raw_jwt=<token>`, X-Internal-Key path sets `raw_jwt=None` (triggers admin client in services)
- `ai_engine/app/routers/ws.py` — WebSocket endpoint; session lifecycle + event streaming
- `ai_engine/app/routers/chat.py` — Chat history CRUD (list, get, delete, rename)
- `ai_engine/app/routers/integrations.py` — PAT save/validate, repo listing, PR creation
- `ai_engine/app/services/integrations.py` — AES-256-CBC encrypt/decrypt + GitHub/GitLab API helpers
- `ai_engine/app/services/chat.py` — Chat session + message CRUD via Supabase; all methods take `user_jwt: str | None`
- `ai_engine/app/services/docker_workspace.py` — Per-session Docker sandbox: `create_sandbox()` (container + workspace bind-mount + resource limits), `destroy_container()`, orphan cleanup on startup
- `ai_engine/app/services/sessions.py` — In-memory session store + session lifecycle (create workspace dir, spin up sandbox, build LocalConversation)
- `supabase/migrations/001_initial_schema.sql` — Creates `users` (profile, references `auth.users`), `chat_sessions`, `chat_messages` with RLS. Includes `handle_new_user()` trigger that auto-creates a profile row on first OAuth sign-in.
- `supabase/migrations/002_integrations.sql` — Creates `integrations` table (snake_case columns, UUID FK to users) + RLS policies

## OAuth Sign-In Setup

Supabase Auth handles Google, GitHub, and GitLab OAuth. For each provider you need to:

1. Create an OAuth app in the provider's developer console
2. Set the **redirect/callback URL** to: `https://<project-ref>.supabase.co/auth/v1/callback`
3. Copy the client ID and secret into the Supabase Dashboard → Authentication → Providers

**Google** — https://console.cloud.google.com → APIs & Services → Credentials → Create OAuth 2.0 Client ID (Web application)

**GitHub** — https://github.com/settings/applications/new → set Authorization callback URL

**GitLab** — https://gitlab.com/-/user_settings/applications → set Redirect URI, scopes: `read_user openid email`
(For self-hosted GitLab, set the instance URL in Dashboard → Authentication → Providers → GitLab → GitLab URL)

After enabling providers in the Dashboard, also set:
- **Site URL** (Dashboard → Authentication → URL Configuration): your app's root URL
- **Additional redirect URLs**: any extra allowed redirect targets

## Environment Variables

**AI Engine** (`docker-compose.yml` or local `.env`):

| Variable | Source | Description |
|----------|--------|-------------|
| `SUPABASE_URL` | Dashboard → Settings → API | Project URL |
| `SUPABASE_ANON_KEY` | Dashboard → Settings → API → **Publishable key** (formerly anon) | Safe to use in browsers; RLS enforced |
| `SUPABASE_JWT_SECRET` | Dashboard → Settings → API → JWT Settings | Validates Supabase Auth JWTs |
| `SUPABASE_SERVICE_KEY` | Dashboard → Settings → API → **Secret key** (formerly service_role) | Server-only — never expose to clients; bypasses RLS |
| `ENCRYPTION_KEY` | shared secret | Must match frontend value |
| `INTERNAL_API_KEY` | shared secret | Must match frontend value |
| `ANTHROPIC_API_KEY` | Anthropic console | LLM key |
| `GOOGLE_API_KEY` | Google AI Studio | LLM key (Gemini) |
| `DEFAULT_MODEL_PROVIDER` | — | `anthropic` or `google` |
| `MAX_ITERATIONS` | — | Agent max steps (default 50) |
| `HOST_WORKSPACE_PATH` | `${PWD}/workspaces` | Host-side workspace root for Docker sandbox bind mounts (DinD); leave empty for local dev |

> `ENCRYPTION_KEY` and `INTERNAL_API_KEY` must be **identical** in both the frontend and ai_engine.
> `SUPABASE_JWT_SECRET` must match the JWT Secret shown in the Supabase Dashboard so `auth.uid()` resolves correctly in RLS policies.

## Conventions

- Frontend uses JavaScript (not TypeScript) with Next.js App Router (`src/app/` directory)
- CSS utility function `cn()` from `@/lib/utils` (clsx + tailwind-merge)
- UI icons from `lucide-react`
- OpenHands SDK imports are wrapped in try/catch so the app runs in dev mode without the SDK installed (mock sessions)
- Path alias `@/` maps to `frontend/src/`
- All ai_engine service methods use `user_jwt: str | None` — str triggers `managed_client` (RLS via anon key + JWT), None triggers `managed_admin_client` (service_role key, bypasses RLS)
- RLS policies use `auth.uid()` — the canonical Supabase function for getting the authenticated user's UUID
