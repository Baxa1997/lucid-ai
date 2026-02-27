# Lucid AI Engine — Code Explanation

This document explains every concept, file, and design decision in the `ai_engine/` backend. Read this to understand what the code does and why.

---

## What is the AI Engine?

The AI Engine is a Python backend that powers the Lucid AI platform. When a user says "fix this bug", the AI Engine:

1. Creates an isolated local workspace directory for that user
2. Clones the user's GitHub/GitLab repo into the workspace
3. Runs an AI agent (powered by OpenHands SDK) that reads code, writes fixes, runs commands
4. Streams everything back to the user's browser in real-time via WebSocket
5. The agent can commit and push changes back to the repo

Think of it as the brain behind a Devin-like AI coding assistant.

---

## How the Code is Organized

```
ai_engine/
├── main.py                          # Entry point
├── app/
│   ├── __init__.py                  # FastAPI app factory + startup/shutdown
│   ├── config.py                    # All settings and environment variables
│   ├── auth.py                      # JWT validation + user identity
│   ├── sdk.py                       # OpenHands SDK imports (with fallback)
│   ├── schemas.py                   # Request/response data shapes
│   ├── supabase_client.py           # Async Supabase client context managers
│   ├── events.py                    # Event formatting + WebSocket streaming
│   ├── exceptions.py                # Custom error types
│   ├── routers/
│   │   ├── health.py                # GET / and GET /health
│   │   ├── sessions.py              # CRUD for agent sessions
│   │   ├── ws.py                    # WebSocket endpoint
│   │   ├── chat.py                  # Chat history endpoints
│   │   ├── integrations.py          # PAT management + repo listing + PR creation
│   │   └── files.py                 # File explorer endpoints
│   └── services/
│       ├── sessions.py              # Session lifecycle logic
│       ├── docker_workspace.py      # Docker daemon health check + orphan cleanup
│       ├── chat.py                  # Chat database operations (Supabase)
│       ├── integrations.py          # Token encryption + GitHub/GitLab API
│       └── llm.py                   # LLM provider resolution
└── requirements.txt                 # Python dependencies
```

---

## Core Concepts

### What is a "Session"?

A **session** is a single agent work unit. When a user says "fix the login bug", that creates one session. The session contains:

- **session_id** — A UUID that identifies this work unit
- **user_id** — Who started it (for isolation)
- **task** — What the agent should do ("fix the login bug")
- **workspace** — A local directory path where the code lives (`storage/{user_id}/{session_id}/`)
- **conversation** — The OpenHands SDK object that talks to the AI
- **llm** — The language model powering the agent (Claude, Gemini, etc.)
- **event_buffer** — A queue of events the agent produces (actions, observations)

Sessions are stored **in memory** (not in the database). They exist only while the agent is actively running. When the user disconnects or stops the session, it's destroyed and the workspace directory is removed.

**File:** `app/services/sessions.py`

### What is a "Chat"?

A **chat** is the persistent record of a session. While sessions are temporary (in memory), chats are saved to Supabase so users can review their history later.

A chat has:
- **chat_sessions** row — Metadata: who, when, what project, which model, is it still running
- **chat_messages** rows — Individual messages: user messages, agent actions, agent observations

When a WebSocket session starts, a `chat_sessions` row is created in Supabase. As the agent works, its events are batched and saved as `chat_messages` rows. When the session ends, the row is marked `is_active = false`.

**Files:** `app/services/chat.py`, `app/routers/chat.py`, `supabase/migrations/001_initial_schema.sql`

### What is a "Workspace"?

A **workspace** is the isolated environment where the agent's code lives. It's where the agent runs commands, edits files, and executes tests.

There are two modes:

1. **Sandboxed workspace** (SDK installed) — A directory at `storage/{user_id}/{session_id}/` plus an isolated Docker container with the directory bind-mounted at `/workspace`. The container enforces memory and CPU limits per session. The OpenHands SDK `LocalConversation` executes agent commands against this workspace. When the SDK exports `DockerWorkspace`, all shell/bash commands run through `container.exec_run()` for full process isolation; until then they run on the ai_engine host while still sharing the workspace directory with the container. Docker unavailability falls back gracefully to local-only execution.

2. **Mock workspace** (no SDK) — When the OpenHands SDK isn't installed. The agent sends fake responses. Used for frontend development.

**File:** `app/services/sessions.py`

### What is the "OpenHands SDK"?

[OpenHands](https://github.com/All-Hands-AI/OpenHands) is an open-source framework for building AI software agents. It provides:

- **Agent** — The AI that reads code, thinks, and acts
- **Conversation** — Manages the back-and-forth between user and agent
- **LocalConversation** — Runs the agent against a local workspace directory
- **Tools** — File editor, terminal, task tracker that the agent can use
- **LLM** — Connection to language models (Claude, GPT, Gemini)

The SDK is **not yet published on PyPI**, so the app runs in mock mode without it. All SDK imports go through `app/sdk.py`, which catches ImportError and sets `OPENHANDS_AVAILABLE = False`.

**File:** `app/sdk.py`

---

## File-by-File Explanation

### `main.py`

The entry point. Just three lines that matter:

```python
from app import app
```

This imports the FastAPI application instance. When you run `uvicorn main:app`, uvicorn finds this `app` object and serves it.

The `if __name__ == "__main__"` block lets you also run it with `python main.py` during development.

### `app/__init__.py` — Application Factory

Creates the FastAPI app with all its configuration.

**Lifespan** — The `lifespan` function runs code on startup and shutdown:
- **Startup:** Checks if Docker daemon is accessible (for orphan cleanup). Cleans up any orphaned sandbox containers from crashed previous runs. Logs warnings if SDK or LLM keys are missing.
- **Shutdown:** Destroys all active sessions (stops agent, removes workspace directories). Cleans up any tracked Docker containers.

**CORS middleware** — Allows the frontend (different origin) to call the API. Origins are restricted via the `ALLOWED_ORIGINS` env var (default: `http://localhost:3000`). In docker-compose this is set to `http://localhost:3000,http://frontend:3000`. Change it to your production domain before deploying.

**Router registration** — Attaches all endpoint groups: health, sessions, ws, chat, files, integrations.

### `app/config.py` — Configuration

Every environment variable is read once at import time. The `Settings` class groups them:

| Setting | What it does |
|---------|-------------|
| `SUPABASE_URL` | Supabase project URL (e.g. `https://xxxx.supabase.co`) |
| `SUPABASE_ANON_KEY` | **Publishable key** (Dashboard → API) — used with user JWT for RLS-enforced calls. Formerly called the anon key. |
| `SUPABASE_JWT_SECRET` | Validates incoming Supabase Auth JWTs (HS256) |
| `SUPABASE_SERVICE_KEY` | **Secret key** (Dashboard → API) — bypasses RLS for server-to-server calls. Formerly called the service_role key. Never expose to clients. |
| `ENCRYPTION_KEY` | AES-256-CBC key for encrypting git provider tokens |
| `ANTHROPIC_API_KEY` | Key for Claude models |
| `GOOGLE_API_KEY` | Key for Gemini models |
| `LLM_API_KEY` | Generic fallback key |
| `LLM_BASE_URL` | Custom LLM endpoint (for proxies) |
| `DEFAULT_PROVIDER` | Which model to use by default (`"anthropic"` or `"google"`) |
| `MAX_ITERATIONS` | How many steps the agent can take before stopping |
| `SANDBOX_IMAGE` | Docker image for agent sandboxes (used by OpenHands SDK) |
| `WORKSPACE_MOUNT_PATH` | Path inside the sandbox where code lives (`/workspace`) |
| `WORKSPACE_BASE_PATH` | Local workspace root on the host (`./storage`) |
| `SANDBOX_CONTAINER_PREFIX` | Prefix for sandbox container names (`lucid-sandbox-`) |
| `SANDBOX_MEMORY_LIMIT` | Max RAM per sandbox (`2g`) |
| `SANDBOX_CPU_LIMIT` | Max CPUs per sandbox (`1.0`) |
| `DOCKER_NETWORK` | Optional Docker network for sandboxes |
| `INTERNAL_API_KEY` | Secret for server-to-server auth between frontend and ai_engine |
| `ALLOWED_ORIGINS` | Comma-separated allowed CORS origins. Defaults to `http://localhost:3000`. Set to your frontend URL in production. |

**MODEL_CONFIGS** — Maps provider names to LiteLLM model strings. LiteLLM is a library that gives a unified API across all LLM providers.

**Magic numbers** — Constants like `WS_EVENT_MAX_CHARS = 2000` (max event size sent to browser), `DB_BATCH_SIZE = 20` (flush events to DB every 20), `CONVERSATION_TIMEOUT_SECONDS = 1800` (agent times out after 30 minutes).

### `app/auth.py` — Authentication

Three auth methods, checked in this order:

**1. JWT (Bearer token)** — The frontend passes a Supabase Auth JWT (`Authorization: Bearer <token>`). The AI engine validates the signature with `SUPABASE_JWT_SECRET` (HS256) and extracts the user identity from the `sub` claim. The raw token is stored on `AuthenticatedUser.raw_jwt` so services can forward it to the Supabase client for RLS enforcement.

**2. X-User-ID header** — For server-to-server calls (Next.js → ai_engine). The frontend sends the user ID in a header. To prevent spoofing, the AI engine also checks `X-Internal-Key` against `INTERNAL_API_KEY` using constant-time comparison. `raw_jwt` is set to `None` on this path — services then use the admin (service_role) Supabase client which bypasses RLS while still filtering rows by `user_id` explicitly.

**3. No auth → 401** — If neither method works, the request is rejected.

**WebSocket auth** works differently: JWT comes via `?token=` query param or `token` field in the first WebSocket message. If neither provides a valid JWT, the connection is closed. There is no anonymous fallback — every WebSocket must be authenticated.

**`AuthenticatedUser`** — A lightweight object holding `user_id`, `project_id`, `session_id`, and `raw_jwt`. The `raw_jwt` field drives which Supabase client is used in service calls (`str` → `managed_client` with RLS; `None` → `managed_admin_client` bypassing RLS).

### `app/sdk.py` — OpenHands SDK Facade

This file imports all SDK symbols in a single `try/except ImportError` block. If the SDK isn't installed, every symbol is set to `None` and `OPENHANDS_AVAILABLE` is `False`.

Other files import from `app.sdk` — never directly from `openhands.*`. This means:
- The app starts even without the SDK installed
- There's one place to update if SDK imports change
- Code can check `OPENHANDS_AVAILABLE` before using any SDK feature

### `app/schemas.py` — Request/Response Models

Pydantic models that define the shape of API requests and responses. FastAPI uses these for:
- **Validation** — Rejects requests with missing/wrong fields
- **Serialization** — Converts Python objects to JSON
- **Documentation** — Auto-generates OpenAPI/Swagger docs

`InitSessionRequest` is the main input — everything needed to start an agent session:

| Field | Required | What it does |
|-------|----------|-------------|
| `task` | Yes | What the agent should do |
| `repoUrl` | No | Git repo to clone |
| `gitToken` | No | Token for private repo access |
| `branch` | No | Which branch to clone |
| `gitUserName` | No | Name for git commits |
| `gitUserEmail` | No | Email for git commits |
| `projectId` | No | Links session to a frontend project |
| `model_provider` | No | `"anthropic"` or `"google"` |
| `api_key` | No | User's own LLM API key |

### `app/supabase_client.py` — Supabase Client Helpers

Three async context manager helpers for accessing Supabase. Each creates a fresh client, uses it, then closes it cleanly.

- **`managed_client(user_jwt)`** — Anon key + user JWT → RLS enforced via PostgREST auth header. Creates a fresh `AsyncClient` per call to avoid JWT cross-contamination between concurrent requests. Use for all user-facing DB operations.
- **`managed_admin_client()`** — Service-role key → RLS bypassed. Use only for server-to-server calls (X-Internal-Key path) where no user JWT is available. Callers must filter by `user_id` explicitly.
- **`db_client(user_jwt: str | None)`** — Dispatcher: yields `managed_client` when `user_jwt` is a non-empty string, `managed_admin_client` when it is `None`. Services import this instead of the two lower-level helpers.

All tables (`users`, `chat_sessions`, `chat_messages`, `integrations`) are created by the migrations in `supabase/migrations/`.

### `app/events.py` — Event Formatting and Streaming

When the OpenHands agent does something (runs a command, edits a file, thinks), it produces an event object. This file converts those SDK events into JSON that the frontend understands.

**`format_sdk_event(event)`** — Takes an SDK event and returns a dict like:
```json
{
  "type": "agent_event",
  "event": "action",
  "eventType": "CmdRunAction",
  "content": "npm install express",
  "command": "npm install express",
  "timestamp": "2026-02-18T10:30:00+00:00"
}
```

Events are categorized by class name: `*Action` → action, `*Error` → error, everything else → observation.

**`stream_events_to_ws()`** — Background task that runs for the entire session. It:
1. Pulls events from the session's `event_buffer` queue
2. Sends each event to the WebSocket client
3. Checks if the event changed the file tree (triggers a file tree refresh)
4. Accumulates events for database persistence
5. Flushes to the database every 20 events or every 2 seconds (whichever comes first)
6. On shutdown, flushes any remaining events

This batching is important for performance. Writing every single event to the DB individually would create too many database round-trips.

### `app/exceptions.py` — Custom Errors

Four exception classes used throughout the codebase:

- **`SessionNotFoundError`** — The session ID doesn't exist in the in-memory store
- **`ProviderError`** — User requested an LLM provider that doesn't exist (not "anthropic" or "google")
- **`APIKeyMissingError`** — No API key found for the requested provider
- **`APIKeyInvalidError`** — The LLM provider rejected the key (401 from their API)

Routers catch these and convert them to appropriate HTTP status codes (400, 401, 404).

---

## Routers (API Endpoints)

### `app/routers/health.py`

Two endpoints, no authentication required:

- **`GET /`** — Returns full system status: service name, version, whether OpenHands SDK is available, whether Docker is accessible, how many active sessions exist, which LLM model is configured.

- **`GET /health`** — Returns `{"status": "ok"}`. Used by load balancers and Docker health checks to verify the service is running.

### `app/routers/sessions.py`

CRUD for agent sessions. All endpoints require authentication.

- **`POST /api/v1/sessions`** — Creates a new agent session. Calls `create_session()` which either creates a local workspace directory with an SDK `LocalConversation` (full mode) or a mock object (mock mode). Returns the session ID.

- **`GET /api/v1/sessions`** — Lists the authenticated user's active sessions. Filters the in-memory store by `user_id` so users only see their own.

- **`DELETE /api/v1/sessions/{id}`** — Stops a session. Checks that the session belongs to the requesting user (403 if not). Destroys the workspace directory and removes from the store.

### `app/routers/ws.py`

The WebSocket endpoint — the heart of the real-time agent experience.

**Connection lifecycle:**

1. **Accept** — Browser opens WebSocket connection
2. **Authenticate** — JWT from query param or handshake message. If neither is valid, connection is closed (no anonymous access).
3. **Receive config** — Client sends JSON with `task`, `repoUrl`, `gitToken`, etc.
4. **Create session** — Creates a local workspace directory, spins up a Docker sandbox container, and initialises the SDK agent
5. **Create chat** — Saves a ChatSession record in Supabase
6. **Start agent** — If SDK available: creates a `LocalConversation`, sends the task, starts streaming events. If not: runs mock loop with simulated events.
7. **Follow-up loop** — Client can send additional messages, stop commands, etc.
8. **Cleanup** — On disconnect or error: cancel streaming, destroy session, mark chat inactive

**Mock mode** — When the SDK isn't installed, the WebSocket sends a sequence of fake events that simulate an agent: thinking → creating directory → writing a file → running it → completing. This lets you develop the frontend without the SDK.

### `app/routers/chat.py`

CRUD for chat history. All endpoints filter by the authenticated user's ID.

- **`GET /api/v1/chats`** — Paginated list of past chats, newest first. Returns metadata only (no messages).
- **`GET /api/v1/chats/{id}`** — Single chat with all its messages, sorted by creation time.
- **`DELETE /api/v1/chats/{id}`** — Deletes a chat and all its messages (cascade).
- **`PATCH /api/v1/chats/{id}`** — Renames a chat (updates the title).

### `app/routers/files.py`

File explorer endpoints for viewing the agent's workspace.

- **`GET /api/v1/files/list?session_id=X`** — Returns the workspace file tree as nested JSON. Uses `os.listdir` to walk the local workspace directory. Excludes `.git`, `node_modules`, `__pycache__`, etc.

- **`GET /api/v1/files/read?session_id=X&path=/workspace/file.py`** — Reads a file's content from the local workspace. Path traversal (`..`) is blocked.

**`should_refresh_file_tree()`** — Helper used by the event streamer. When the agent does something that might change the file tree (writes a file, runs `git clone`, installs packages), this returns `True` and the streamer sends an updated file tree to the frontend automatically.

---

## Services (Business Logic)

### `app/services/sessions.py`

The core session lifecycle.

**`SessionStore`** — An in-memory dictionary protected by an asyncio lock. Every method acquires the lock so concurrent requests don't see partial state. Methods: `add`, `get`, `pop`, `contains`, `list_all`, `count`, `snapshot_ids`.

Why in-memory and not in the database? Because sessions hold live Python objects (the OpenHands Conversation, Agent). These can't be serialized to a database. The database stores the *history* (chats); memory stores the *live state* (sessions).

**`create_session()`** — The main factory function. Requires a non-empty `user_id` — raises `ValueError` immediately if missing. This prevents any edge case where sessions could be assigned to a shared `"anonymous"` owner (which would allow cross-user data access). Since all endpoints enforce authentication before calling `create_session()`, `user_id` is always a real value in practice.

1. **Mock mode** — If SDK not installed: creates a bare `AgentSession` with no workspace. The WebSocket handler will run the mock loop.
2. **Real mode** — Creates a local directory at `storage/{user_id}/{session_id}/`, spins up an isolated Docker sandbox container with the workspace bind-mounted (falling back gracefully if Docker is unavailable), creates an `LLM` and `Agent` via the SDK, then creates a `LocalConversation` (backed by `DockerWorkspace` if the SDK exports it, otherwise `LocalWorkspace`) with event callbacks. The `container_id` is stored on the session for lifecycle management.

The event callback (`on_event`) converts SDK events and pushes them into the session's `event_buffer` queue. The WebSocket streamer reads from this queue.

**`destroy_session()`** — Removes the session from the store, closes the Conversation, destroys the Docker sandbox container (if one was created), and removes the local workspace directory with `shutil.rmtree`.

### `app/services/docker_workspace.py`

Manages Docker daemon interactions for sandbox lifecycle.

**`DockerSessionManager`** — Singleton that talks to the Docker daemon via the Python Docker SDK.

- **`create_sandbox(session_id, user_id, workspace_dir)`** — Creates an isolated Docker container for one agent session. The container runs `sleep infinity` to stay alive so the agent can exec commands into it. The workspace directory is bind-mounted at `/workspace` (configurable via `WORKSPACE_MOUNT_PATH`). Labels `lucid.managed=true` and `lucid.session_id=<id>` are applied for lifecycle tracking and orphan cleanup. Memory and CPU limits are applied from `SANDBOX_MEMORY_LIMIT` and `SANDBOX_CPU_LIMIT`. Returns the container ID.
- **`destroy_container(container_id, session_id)`** — Stops and removes a specific sandbox container when its session ends.
- **`is_docker_available()`** — Called on startup to check if the Docker daemon is reachable.
- **`cleanup_orphaned_containers()`** — On startup, finds all containers with `lucid.managed=true` label and removes them. Handles crashes from previous runs.
- **`destroy_all()`** — Called on shutdown to destroy all tracked containers.

**DinD path resolution** — When the ai_engine runs inside Docker, the workspace directory exists at an internal path (`/app/storage/{user}/{session}`). The Docker daemon sits on the host and needs the *host-side* path for the bind mount. `HOST_WORKSPACE_PATH` maps the internal workspace root to the host-side root so the correct path is passed to `containers.run(volumes=...)`.

### `app/services/chat.py`

Database CRUD for chat sessions and messages via Supabase. Every method is a `@staticmethod` that takes `user_jwt: str | None`. Internally it uses `async with db_client(user_jwt) as client:` — `str` triggers `managed_client` (RLS enforced via anon key + JWT), `None` triggers `managed_admin_client` (service-role key, RLS bypassed, filters by `user_id` explicitly).

All queries include `user_id` in the `.eq()` filter so users can only access their own data.

Key methods:
- `create_session` / `list_sessions` / `get_session` / `delete_session` / `rename_session` — standard CRUD on `chat_sessions`
- `add_message` — single insert into `chat_messages`
- `add_messages(events, session_id)` — **batch insert** used by `_flush_batch` in `events.py`; sends all events in one Supabase call instead of N individual inserts
- `deactivate_session(session_id, user_id, user_jwt)` — sets `is_active = false`; called by `ws.py` on WebSocket disconnect. `user_id` is included as an explicit filter on the admin path (where RLS is bypassed)

### `app/services/llm.py`

Resolves which language model to use.

1. Looks up the provider name in `MODEL_CONFIGS` (validates it's supported)
2. Resolves the API key: user-provided key → provider-specific env var → generic `LLM_API_KEY`
3. Creates an OpenHands `LLM` object with `SecretStr` (prevents the key from leaking in logs)
4. Adds Gemini safety settings if using Google (disables content filters for coding agents)

---

## Database

### Supabase (hosted PostgreSQL)

All data lives in a single Supabase project. All tables are created by the migrations in `supabase/migrations/`:

| Migration | Tables created |
|-----------|---------------|
| `001_initial_schema.sql` | `users`, `chat_sessions`, `chat_messages` + RLS policies + `handle_new_user()` trigger |
| `002_integrations.sql` | Creates `integrations` table + RLS policies |

There are no Alembic migrations and no SQLAlchemy ORM. The ai_engine accesses Supabase through the `supabase-py` async client (PostgREST API). Schema changes are made by creating a new numbered migration file and running it in the Supabase SQL editor.

### Connection methods

- **ai_engine → Supabase (user JWT path):** `managed_client(user_jwt)` — anon key + user JWT, RLS enforced. Env vars: `SUPABASE_URL`, `SUPABASE_ANON_KEY`.
- **ai_engine → Supabase (server-to-server path):** `managed_admin_client()` — service_role key, RLS bypassed. Env vars: `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`.

### Column naming

All tables use snake_case (e.g. `user_id`, `access_token_encrypted`, `created_at`). The `integrations` table previously used camelCase (legacy from Prisma) but was normalised to snake_case in `002_integrations.sql`.

---

## Supabase Auth

### What Supabase Auth Does

Supabase Auth is a hosted authentication service that sits in front of your PostgreSQL database. It handles:

- **Identity** — creates and stores user accounts in a private `auth.users` table that your application code cannot write to directly
- **OAuth** — manages the full OAuth handshake with Google, GitHub, and GitLab on your behalf
- **Sessions** — issues signed JWTs that prove who a user is; these tokens are what every API call carries

Your application never touches passwords or OAuth tokens. Supabase handles all of that and hands you back a JWT.

---

### OAuth Sign-In Flow (Step by Step)

```
1. User clicks "Sign in with GitHub" in the browser
        │
        ▼
2. Frontend redirects to Supabase Auth:
   https://<project>.supabase.co/auth/v1/authorize?provider=github
        │
        ▼
3. Supabase redirects the user to GitHub's OAuth page
   (GitHub asks: "Allow Lucid AI to access your account?")
        │
        ▼
4. User approves → GitHub redirects back to:
   https://<project>.supabase.co/auth/v1/callback?code=<auth_code>
        │
        ▼
5. Supabase exchanges the auth code for a GitHub access token
   (server-to-server, never seen by your app or the browser)
        │
        ▼
6. Supabase fetches the user's profile from GitHub API:
   { login, name, email, avatar_url }
        │
        ▼
7. Supabase creates or updates a row in auth.users:
   { id: <UUID>, email: "...", raw_user_meta_data: { name, avatar_url, ... } }
        │
        ▼
8. Database trigger fires: handle_new_user()
   → Creates a row in public.users with name and avatar_url
   → ON CONFLICT (id) DO NOTHING (safe to re-run on repeat sign-ins)
        │
        ▼
9. Supabase issues a JWT and redirects the browser back to your app
   (the JWT is stored by the Supabase JS client in localStorage / a cookie)
```

The same flow happens for Google and GitLab — only the provider's metadata keys differ slightly, which is why `handle_new_user()` uses `COALESCE`:
- Google sends `full_name` (not `name`) and sometimes `picture` (not `avatar_url`)
- GitHub and GitLab both send `name` and `avatar_url`

---

### The JWT

Every signed-in user carries a JWT. Here is what it looks like decoded:

```json
{
  "sub": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "email": "user@example.com",
  "role": "authenticated",
  "aud": "authenticated",
  "iss": "https://<project>.supabase.co/auth/v1",
  "iat": 1740000000,
  "exp": 1740003600,
  "app_metadata": { "provider": "github" },
  "user_metadata": { "name": "Alice", "avatar_url": "https://..." }
}
```

Key fields:

| Field | What it is |
|-------|-----------|
| `sub` | The user's UUID — this is `auth.uid()` in RLS policies and `user_id` throughout the app |
| `role` | Always `"authenticated"` for logged-in users |
| `aud` | Always `"authenticated"` — Supabase uses a non-standard audience value |
| `exp` | Token expires 1 hour after issue by default |
| `app_metadata.provider` | Which OAuth provider was used (`github`, `google`, `gitlab`) |

The JWT is signed with **HS256** using the `SUPABASE_JWT_SECRET` (found in Dashboard → Settings → API → JWT Settings). This same secret is set as `SUPABASE_JWT_SECRET` in the ai_engine so it can verify the signature without calling Supabase's servers.

---

### Token Refresh

The Supabase JS client (in the frontend) automatically refreshes the JWT before it expires using a long-lived **refresh token** stored separately. The browser always has a valid access JWT as long as the session is active. The ai_engine never needs to refresh tokens — it only validates them.

---

### The `public.users` Profile Table

Supabase Auth owns `auth.users` — your application cannot write to it. So the project has a separate `public.users` table for application-level profile data:

```sql
CREATE TABLE public.users (
    id         UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    email      TEXT UNIQUE NOT NULL,
    name       TEXT,
    avatar_url TEXT,
    ...
);
```

`id` is a foreign key into `auth.users` — the same UUID that appears as `sub` in the JWT. The `ON DELETE CASCADE` means if the user deletes their account from Supabase Auth, the profile row is automatically removed too.

The row is created automatically by the `handle_new_user()` trigger:

```sql
CREATE FUNCTION public.handle_new_user()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO public.users (id, email, name, avatar_url)
    VALUES (
        NEW.id,
        NEW.email,
        COALESCE(NEW.raw_user_meta_data ->> 'full_name',
                 NEW.raw_user_meta_data ->> 'name'),
        COALESCE(NEW.raw_user_meta_data ->> 'avatar_url',
                 NEW.raw_user_meta_data ->> 'picture')
    )
    ON CONFLICT (id) DO NOTHING;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public;

CREATE TRIGGER on_auth_user_created
    AFTER INSERT ON auth.users
    FOR EACH ROW EXECUTE FUNCTION public.handle_new_user();
```

`SECURITY DEFINER` means the function runs with the privileges of its creator (superuser level) rather than the caller — this is required because the trigger fires in the `auth` schema context but needs to write to `public.users`.

`SET search_path = public` pins the schema so a malicious user cannot shadow `public.users` with a function in another schema.

---

### How RLS Uses the JWT

Row Level Security policies use `auth.uid()` — a Supabase function that reads the `sub` claim out of the JWT that was attached to the database connection:

```sql
-- Only the owner can read their own chat sessions
CREATE POLICY "sessions_select" ON chat_sessions FOR SELECT
    USING (user_id = auth.uid());
```

`auth.uid()` returns `NULL` if there is no JWT attached (unauthenticated connection), causing all policy checks to fail and returning zero rows instead of an error. This makes RLS a silent, automatic data isolation layer.

When the ai_engine calls Supabase with `managed_client(user_jwt)`, it sets the JWT on the PostgREST connection via:
```python
client.postgrest.auth(user_jwt)
```
PostgREST attaches this JWT to every SQL statement it sends, so `auth.uid()` resolves to the correct user UUID for every query.

---

### How the ai_engine Validates the JWT

The ai_engine never calls Supabase Auth to validate a token. It validates locally:

```python
jwt.decode(
    token,
    settings.SUPABASE_JWT_SECRET,   # same secret Supabase used to sign it
    algorithms=["HS256"],
    options={"verify_aud": False},   # skip audience check (Supabase uses non-standard "authenticated")
)
```

`verify_aud: False` is intentional — Supabase sets `aud: "authenticated"` which `python-jose` would reject by default (it expects a URL, not a plain string). Signature and expiry are still fully verified.

The `sub` claim becomes `user_id` inside the ai_engine. This is the UUID that links the JWT to the `public.users` row and all of their data.

---

### What SUPABASE_ANON_KEY Does

The Publishable key (formerly "anon key") is a **pre-signed JWT** with `role: "anon"`. When you attach a user JWT on top (via `client.postgrest.auth(user_jwt)`), PostgREST uses the user JWT for `auth.uid()` resolution while the Publishable key acts as the API credential. RLS policies then enforce data isolation per user.

The Secret key (formerly "service_role key") is a **pre-signed JWT** with `role: "service_role"`. It bypasses all RLS policies entirely — which is why it's only used in `managed_admin_client()` for the X-Internal-Key (server-to-server) path where no user JWT is available, and callers must filter by `user_id` explicitly.

---

## Security Model

### Authentication Chain

```
Browser → Supabase Auth (Google / GitHub / GitLab OAuth) → Supabase JWT
                                          ↓
                    ┌─── REST: X-User-ID + X-Internal-Key headers
                    │         (Next.js → ai_engine server-to-server)
                    │         raw_jwt = None → managed_admin_client (service_role)
                    │
                    └─── WebSocket / Bearer: Authorization: Bearer <JWT>
                                    (browser → ai_engine direct)
                                    raw_jwt = <token> → managed_client (anon key + JWT, RLS)
                                          ↓
                              ai_engine validates JWT with SUPABASE_JWT_SECRET
                                          ↓
                              AuthenticatedUser(user_id=..., raw_jwt=...)
                                          ↓
                              All queries filtered by user_id (+ RLS via JWT)
```

### Data Isolation

Every database query includes `WHERE user_id = <authenticated_user>`:
- `ChatService.list_sessions(user_jwt, user.user_id, ...)`
- `ChatService.get_session(user_jwt, chat_id, user.user_id)`
- `ChatService.delete_session(user_jwt, session_id, user.user_id)`

On the JWT path, Supabase RLS policies provide a second layer of isolation — the database itself rejects any query that tries to access another user's rows.

Every in-memory operation also checks ownership:
- `list_sessions` filters: `if s.user_id == user.user_id`
- `stop_session` checks: `if session.user_id != user.user_id: raise 403`
- `read_file` checks: `if session.user_id != user_id: raise 403`

---

## Two Operating Modes

The system is designed to work in two configurations:

### Full Mode (SDK installed)
- OpenHands SDK installed
- Sessions create a local workspace directory (`storage/{user_id}/{session_id}/`) **and** spin up an isolated Docker sandbox container with the workspace bind-mounted at `/workspace`
- Agent reads, writes, and executes code via `LocalConversation` (using `DockerWorkspace` when the SDK exports it, `LocalWorkspace` otherwise)
- Docker unavailability is non-fatal: session falls back to local-only execution with a warning
- Git push works

### Mock Mode (Development)
- OpenHands SDK NOT installed
- No real agent execution
- WebSocket sends fake events that simulate an agent
- Useful for frontend development without the SDK

The mode is determined automatically at runtime:
```python
if not sdk.OPENHANDS_AVAILABLE:
    # Mock mode — bare session, WebSocket runs fake event loop
else:
    # Full mode — LocalConversation with local directory workspace
```

Docker availability affects session creation: if Docker is unavailable, sessions fall back to local-only execution without a sandbox container, and a warning is logged. Mock mode is determined solely by SDK availability, not by Docker availability.

---

## Event Flow (How the Agent Talks to the Browser)

```
OpenHands Agent
    │ produces events (ThinkAction, CmdRunAction, FileWriteAction, ...)
    ↓
on_event callback (sessions.py)
    │ calls format_sdk_event() to convert to JSON dict
    ↓
session.event_buffer (asyncio.Queue, max 1000)
    │
    ↓
stream_events_to_ws() (events.py) — background asyncio task
    │
    ├──→ websocket.send_json(event_data) → browser
    │
    ├──→ if file tree changed: send updated tree → browser
    │
    └──→ accumulate in pending[] batch
              │
              ├── every 20 events → _flush_batch() → Supabase (batch insert)
              └── every 2 seconds → _flush_batch() → Supabase (batch insert)
```

The queue has a max size of 1000. If the agent produces events faster than the WebSocket can send them, new events are silently dropped (`put_nowait` + `QueueFull` catch). This prevents memory issues if the browser is slow.

---

## Docker Compose Architecture

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│    frontend      │     │    ai_engine     │     │    Supabase      │
│   (Next.js)      │     │   (FastAPI)      │     │  (hosted PG)    │
│   :3000          │     │   :8000          │     │   supabase.co    │
│                  │     │                  │     │                  │
│                  │     │  supabase-py ────┼────→│  users           │
│                  │     │  (SUPABASE_URL)  │     │  integrations    │
└─────────────────┘     │                  │     │  chat_sessions   │
                         │  supabase-py ────┼────→│  chat_messages   │
                         └────────┬─────────┘     └─────────────────┘
                                  │
                         Docker Socket Mount
                         /var/run/docker.sock
                         (sandbox creation + cleanup)
```

The Docker socket is mounted so the ai_engine can create per-session sandbox containers, clean up orphaned containers on startup, and check Docker health. There is no local `db` container — Postgres is hosted on Supabase.

---

## Key Design Decisions

### Why in-memory sessions instead of database?

Agent sessions hold live Python objects (Conversation, Agent) that can't be serialized. The database stores the history; memory stores the running state. If the server restarts, active sessions are lost — but the chat history survives.

### Why batch event writes?

The agent can produce dozens of events per second. Writing each one individually would create too many database round-trips. Batching (20 events or 2 seconds) reduces DB load while keeping persistence reasonably up-to-date.

### Why the SDK facade pattern?

The OpenHands SDK isn't on PyPI yet. By wrapping all imports in `app/sdk.py` with a try/except, the entire application runs without it. Code checks `OPENHANDS_AVAILABLE` before calling any SDK function. This lets you develop the frontend, test the API, and work on infrastructure without needing the SDK installed.

### Why two auth methods (JWT + X-User-ID)?

JWT is the secure path for browser-to-backend communication. X-User-ID exists because the frontend's server-side API routes (Next.js) call the AI engine on behalf of the user — they already validated the user via Supabase Auth and just need to pass the identity along. The `INTERNAL_API_KEY` ensures only the real frontend can use this path.

### Why two Supabase client modes (anon + service_role)?

The anon key + user JWT path enforces Row Level Security at the database level — Supabase itself rejects queries that access another user's rows. The service_role key bypasses RLS for server-to-server calls (X-Internal-Key path) where no user JWT is available. These calls enforce isolation explicitly with `user_id` filters instead.

### Why supabase-py instead of SQLAlchemy?

`supabase-py` uses PostgREST under the hood — a REST API over PostgreSQL — which means no connection pool to manage, no ORM migrations, and a simpler async interface. For the relatively simple CRUD operations the ai_engine needs (insert, select with filter, update, delete) the PostgREST query builder is cleaner than SQLAlchemy's async session management.

### Why Supabase instead of a local PostgreSQL container?

Supabase gives a persistent, hosted PostgreSQL instance with zero local Docker dependency. Developers don't need to manage a `db` container — they point `SUPABASE_URL` at the remote project and the stack works immediately. Supabase Auth also handles Google/GitHub/GitLab OAuth and JWT issuance out of the box.

---

## Frontend Proxy Routes (Next.js API)

The browser never talks to the ai_engine directly. All requests go through Next.js server-side API routes that add proper auth headers.

### Why proxy routes?

The ai_engine uses server-to-server auth (`X-User-ID` + `X-Internal-Key`). These headers must come from the trusted Next.js server — if the browser sent them directly, any user could spoof them. Each proxy route:

1. Verifies the user is logged in via Supabase Auth (returns 401 if not)
2. Adds `X-User-ID: <user's id>` and `X-Internal-Key: <shared secret>`
3. Forwards the request to the ai_engine
4. Returns the response to the browser

### Routes

| Route | Purpose |
|-------|---------|
| `GET /api/agent/token` | Issues a short-lived JWT for the current user. The workspace WebSocket uses this to authenticate. |
| `POST /api/agent/start` | Creates a new agent session. Decrypts the git token, forwards all fields to ai_engine `/api/v1/sessions`. |
| `GET /api/chats` | Lists the user's chat history from ai_engine `/api/v1/chats`. Used by the conversations page. |
| `GET /api/files/read` | Reads a file from the agent's workspace via ai_engine `/api/v1/files/read`. Used by the file viewer panel. |

### WebSocket token flow

The workspace page authenticates the WebSocket by fetching a token from the proxy:

1. Page loads → `GET /api/agent/token` → receives a JWT
2. JWT passed to `useAgentSession({ token })`
3. Hook appends `?token=<JWT>` to the WebSocket URL
4. ai_engine validates the JWT signature with `SUPABASE_JWT_SECRET` → extracts `userId` → authenticates the session

---

## Workspace Layout (Frontend)

The workspace is a **chat-first interface** — not an IDE. Everything the agent does streams into the chat as structured event cards. There is no separate code editor pane.

```
┌──────────────┬──────────────────────────────────────┬───────────────┐
│ FileExplorer │  Chat (center, flex-1)               │ Terminal      │
│   (240px)    │                                      │  (400px)      │
│              │  💭 Thought: "Checking auth.js..."   │               │
│ Read-only    │  $ npm install (+ output)            │ Agent command │
│ file tree,   │  📝 Edit: src/auth.js                │ output stream │
│ live from    │     +12 / -3  (inline diff)          │               │
│ agent        │  $ git commit && git push            │               │
│              │  ✅ PR opened → github.com/...       │               │
│              │                                      │               │
│              │  [User input textarea]               │               │
└──────────────┴──────────────────────────────────────┴───────────────┘
```

**Left — FileExplorer (read-only):** Shows the agent's workspace file tree, populated live from WebSocket `file_tree` events. Clicking a file shows its content but the user cannot edit it — the agent does all editing.

**Center — Chat:** The core of the product. All agent actions stream in as structured cards:
- 💭 **Thought** — Agent reasoning ("I need to look at the login module first")
- **`$` Command** — Terminal commands run by the agent, with their output
- **📝 File edit** — Inline unified diff showing exactly what changed (+/- lines)
- **✅ Status** — Session started, completed, PR created (with link)
- **User messages** — The user's task and follow-ups

**Right — Terminal:** Raw agent command output stream for debugging. Separate from the chat for cleaner UX.

**Token:** On mount, the page fetches `GET /api/agent/token` to get a JWT, which is passed to `useAgentSession`. This JWT authenticates the WebSocket connection to the ai_engine.
