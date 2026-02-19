# Lucid AI Engine — Code Explanation

This document explains every concept, file, and design decision in the `ai_engine/` backend. Read this to understand what the code does and why.

---

## What is the AI Engine?

The AI Engine is a Python backend that powers the Lucid AI platform. When a user says "fix this bug", the AI Engine:

1. Creates an isolated Docker container for that user
2. Clones the user's GitHub/GitLab repo inside the container
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
│   ├── models.py                    # Database table definitions
│   ├── database.py                  # Database connection
│   ├── events.py                    # Event formatting + WebSocket streaming
│   ├── exceptions.py                # Custom error types
│   ├── routers/
│   │   ├── health.py                # GET / and GET /health
│   │   ├── sessions.py              # CRUD for agent sessions
│   │   ├── ws.py                    # WebSocket endpoint
│   │   ├── chat.py                  # Chat history endpoints
│   │   └── files.py                 # File explorer endpoints
│   └── services/
│       ├── sessions.py              # Session lifecycle logic
│       ├── docker_workspace.py      # Docker container management
│       ├── chat.py                  # Chat database operations
│       └── llm.py                   # LLM provider resolution
├── alembic/                         # Database migrations
├── alembic.ini                      # Alembic config
└── requirements.txt                 # Python dependencies
```

---

## Core Concepts

### What is a "Session"?

A **session** is a single agent work unit. When a user says "fix the login bug", that creates one session. The session contains:

- **session_id** — A UUID that identifies this work unit
- **user_id** — Who started it (for isolation)
- **task** — What the agent should do ("fix the login bug")
- **workspace** — Where the code lives (a Docker container or local directory)
- **conversation** — The OpenHands SDK object that talks to the AI
- **llm** — The language model powering the agent (Claude, Gemini, etc.)
- **event_buffer** — A queue of events the agent produces (actions, observations)
- **container_id** — The Docker container running this session's sandbox

Sessions are stored **in memory** (not in the database). They exist only while the agent is actively running. When the user disconnects or stops the session, it's destroyed and the Docker container is removed.

**File:** `app/services/sessions.py`

### What is a "Chat"?

A **chat** is the persistent record of a session. While sessions are temporary (in memory), chats are saved to PostgreSQL so users can review their history later.

A chat has:
- **ChatSession** — Metadata: who, when, what project, which model, is it still running
- **ChatMessage** — Individual messages: user messages, agent actions, agent observations

When a WebSocket session starts, a ChatSession is created in the database. As the agent works, its events are batched and saved as ChatMessages. When the session ends, the ChatSession is marked `is_active = false`.

**Files:** `app/models.py`, `app/services/chat.py`, `app/routers/chat.py`

### What is a "Workspace"?

A **workspace** is the isolated environment where the agent's code lives. It's where the agent runs commands, edits files, and executes tests.

There are three modes:

1. **Docker workspace** (production) — A real Docker container. The agent has a full Linux environment with git, node, python. Each session gets its own container. Containers are isolated from each other — User A's container cannot see User B's files.

2. **Local workspace** (fallback) — A directory on the host machine at `storage/{user_id}/{session_id}/`. Used when Docker is unavailable.

3. **No workspace** (mock mode) — When the OpenHands SDK isn't installed. The agent sends fake responses. Used for frontend development.

**File:** `app/services/docker_workspace.py`

### What is the "OpenHands SDK"?

[OpenHands](https://github.com/All-Hands-AI/OpenHands) is an open-source framework for building AI software agents. It provides:

- **Agent** — The AI that reads code, thinks, and acts
- **Conversation** — Manages the back-and-forth between user and agent
- **Workspace** — Abstraction over where code lives (local, Docker, remote)
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
- **Startup:** Checks if Docker daemon is accessible. Cleans up any orphaned containers from crashed previous runs. Logs warnings if SDK or LLM keys are missing.
- **Shutdown:** Destroys all active sessions (stops agent, removes Docker containers). Destroys any remaining containers. Closes the database connection pool.

**CORS middleware** — Allows the frontend (different origin) to call the API. Origins are restricted via the `ALLOWED_ORIGINS` env var (default: `http://localhost:3000`). In docker-compose this is set to `http://localhost:3000,http://frontend:3000`. Change it to your production domain before deploying.

**Router registration** — Attaches all endpoint groups: health, sessions, ws, chat, files.

### `app/config.py` — Configuration

Every environment variable is read once at import time. The `Settings` class groups them:

| Setting | What it does |
|---------|-------------|
| `ANTHROPIC_API_KEY` | Key for Claude models |
| `GOOGLE_API_KEY` | Key for Gemini models |
| `LLM_API_KEY` | Generic fallback key |
| `LLM_BASE_URL` | Custom LLM endpoint (for proxies) |
| `DEFAULT_PROVIDER` | Which model to use by default (`"anthropic"` or `"google"`) |
| `MAX_ITERATIONS` | How many steps the agent can take before stopping |
| `SANDBOX_IMAGE` | Docker image for agent containers |
| `WORKSPACE_MOUNT_PATH` | Path inside the container where code lives (`/workspace`) |
| `SESSION_SECRET` | Secret for signing/validating JWT tokens (must match frontend) |
| `DATABASE_URL` | PostgreSQL connection string |
| `WORKSPACE_BASE_PATH` | Local fallback workspace root |
| `SANDBOX_CONTAINER_PREFIX` | Prefix for Docker container names (`lucid-sandbox-`) |
| `SANDBOX_MEMORY_LIMIT` | Max RAM per container (`2g`) |
| `SANDBOX_CPU_LIMIT` | Max CPUs per container (`1.0`) |
| `DOCKER_NETWORK` | Optional Docker network for containers |
| `INTERNAL_API_KEY` | Secret for server-to-server auth between frontend and ai_engine |
| `ALLOWED_ORIGINS` | Comma-separated allowed CORS origins. Defaults to `http://localhost:3000`. Set to your frontend URL in production. |

**MODEL_CONFIGS** — Maps provider names to LiteLLM model strings. LiteLLM is a library that gives a unified API across all LLM providers.

**Magic numbers** — Constants like `WS_EVENT_MAX_CHARS = 2000` (max event size sent to browser), `DB_BATCH_SIZE = 20` (flush events to DB every 20), `CONVERSATION_TIMEOUT_SECONDS = 1800` (agent times out after 30 minutes).

### `app/auth.py` — Authentication

Three auth methods, checked in this order:

**1. JWT (Bearer token)** — The frontend signs a JWT with `SESSION_SECRET` containing `{ userId, projectId, sessionId }`. The AI engine validates the signature and extracts the user identity. This is the most secure method.

**2. X-User-ID header** — For server-to-server calls (Next.js → ai_engine). The frontend sends the user ID in a header. To prevent spoofing, the AI engine also checks `X-Internal-Key` against `INTERNAL_API_KEY`. If the key doesn't match, the request is rejected. Without `INTERNAL_API_KEY` configured (dev mode), it's accepted with a warning.

**3. No auth → 401** — If neither method works, the request is rejected.

**WebSocket auth** works differently: JWT comes via `?token=` query param or `token` field in the first WebSocket message. If neither provides a valid JWT, the connection is closed. There is no anonymous fallback — every WebSocket must be authenticated.

**`AuthenticatedUser`** — A lightweight object holding `user_id`, `project_id`, `session_id`. Created by the auth functions and passed to endpoint handlers.

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

### `app/models.py` — Database Tables

SQLAlchemy ORM models that map to PostgreSQL tables.

**`User`** — Read-only reference to the `users` table (owned by Prisma/frontend). The AI engine never writes to this table. It only exists here so `ChatSession` can have a foreign key relationship.

**`ChatSession`** — One row per agent session. Links to the user and stores metadata.

**`ChatMessage`** — One row per message. `role` is either `"user"` (human), `"assistant"` (agent), or `"system"`. `event_type` records what kind of agent action it was (e.g., `"CmdRunAction"`, `"ThinkAction"`).

Both tables have `ondelete="CASCADE"` — deleting a user deletes their chats, deleting a chat deletes its messages.

### `app/database.py` — Database Connection

Sets up the async database connection using SQLAlchemy's async engine.

- **`engine`** — The connection pool. `pool_pre_ping=True` checks connections are alive before using them (handles DB restarts).
- **`async_session`** — Factory that creates database sessions. Each session is a unit of work — you make queries, then commit or rollback.
- **`get_db()`** — FastAPI dependency. Routers use `db: AsyncSession = Depends(get_db)` to get a session that auto-closes when the request ends.
- **`dispose_engine()`** — Called on shutdown to close all connections cleanly.

The URL is automatically converted from `postgresql://` to `postgresql+asyncpg://` so it works with the async driver.

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

- **`GET /`** — Returns full system status: service name, version, whether OpenHands SDK is available, whether Docker is accessible, how many active sessions and sandboxes exist, which LLM model is configured.

- **`GET /health`** — Returns `{"status": "ok"}`. Used by load balancers and Docker health checks to verify the service is running.

### `app/routers/sessions.py`

CRUD for agent sessions. All endpoints require authentication.

- **`POST /api/v1/sessions`** — Creates a new agent session. Calls `create_session()` which either creates a Docker container (full mode), a local directory (local mode), or a mock object (mock mode). Returns the session ID.

- **`GET /api/v1/sessions`** — Lists the authenticated user's active sessions. Filters the in-memory store by `user_id` so users only see their own.

- **`DELETE /api/v1/sessions/{id}`** — Stops a session. Checks that the session belongs to the requesting user (403 if not). Destroys the Docker container and removes from the store.

### `app/routers/ws.py`

The WebSocket endpoint — the heart of the real-time agent experience.

**Connection lifecycle:**

1. **Accept** — Browser opens WebSocket connection
2. **Authenticate** — JWT from query param or handshake message. If neither is valid, connection is closed (no anonymous access).
3. **Receive config** — Client sends JSON with `task`, `repoUrl`, `gitToken`, etc.
4. **Create session** — Spins up Docker container, clones repo, configures git
5. **Create chat** — Saves a ChatSession record in PostgreSQL
6. **Start agent** — If SDK available: creates a Conversation, sends the task, starts streaming events. If not: runs mock loop with simulated events.
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

- **`GET /api/v1/files/list?session_id=X`** — Returns the file tree as nested JSON. Works by running `find` inside the Docker container (for Docker sessions) or `os.listdir` (for local sessions). Excludes `.git`, `node_modules`, `__pycache__`, etc.

- **`GET /api/v1/files/read?session_id=X&path=/workspace/file.py`** — Reads a file's content. Runs `cat` inside the Docker container or reads from the filesystem. Path traversal (`..`) is blocked.

**`should_refresh_file_tree()`** — Helper used by the event streamer. When the agent does something that might change the file tree (writes a file, runs `git clone`, installs packages), this returns `True` and the streamer sends an updated file tree to the frontend automatically.

---

## Services (Business Logic)

### `app/services/sessions.py`

The core session lifecycle.

**`SessionStore`** — An in-memory dictionary protected by an asyncio lock. Every method acquires the lock so concurrent requests don't see partial state. Methods: `add`, `get`, `pop`, `contains`, `list_all`, `count`, `snapshot_ids`.

Why in-memory and not in the database? Because sessions hold live Python objects (the OpenHands Conversation, Workspace, Agent). These can't be serialized to a database. The database stores the *history* (chats); memory stores the *live state* (sessions).

**`create_session()`** — The main factory function. Requires a non-empty `user_id` — raises `ValueError` immediately if missing. This prevents any edge case where sessions could be assigned to a shared `"anonymous"` owner (which would allow cross-user data access). Since all endpoints enforce authentication before calling `create_session()`, `user_id` is always a real value in practice.

1. **Mock mode** — If SDK not installed: creates a bare AgentSession with no workspace. The WebSocket handler will run the mock loop.
2. **Real mode with Docker** — If Docker available: creates a container via `docker_manager`, wraps it in an SDK Workspace, creates an Agent and Conversation with event callbacks.
3. **Real mode without Docker** — Creates a local directory, uses it as workspace.

The event callback (`on_event`) converts SDK events and pushes them into the session's `event_buffer` queue. The WebSocket streamer reads from this queue.

**`destroy_session()`** — Removes the session from the store, closes the Conversation, destroys the Docker container, and cleans up local files.

### `app/services/docker_workspace.py`

Manages Docker containers — one per session.

**`DockerSessionManager`** — Singleton that talks to the Docker daemon via the Python Docker SDK.

**Container creation:**
- Image: `SANDBOX_IMAGE` (default: `nikolaik/python-nodejs:python3.11-nodejs20`)
- Command: `sleep infinity` (keeps the container running)
- Memory limit: `SANDBOX_MEMORY_LIMIT` (default: 2GB)
- CPU limit: `SANDBOX_CPU_LIMIT` (default: 1 core)
- Labels: `lucid.managed=true`, `lucid.session_id=<UUID>` (for cleanup)
- Working directory: `/workspace`

**Git setup inside the container:**
1. Installs git if not present
2. Sets `git config --global user.name` and `user.email`
3. Configures `credential.helper store` and writes `~/.git-credentials`
4. Clones the repo with `--depth 1` (shallow clone for speed)

The credential store means the agent can later run `git push` without being prompted for a password.

**`exec_command()`** — Runs any command inside a session's container. Used by the file explorer, and could be used for future features like running tests.

**`cleanup_orphaned_containers()`** — On startup, finds all containers with `lucid.managed=true` label and removes them. This handles the case where the server crashed without cleaning up.

### `app/services/chat.py`

Database CRUD for chat sessions and messages. Every method is a `@staticmethod` that takes a `db: AsyncSession` — this makes it easy to test and keeps the service stateless.

All queries include `user_id` in the WHERE clause so users can only access their own data.

### `app/services/llm.py`

Resolves which language model to use.

1. Looks up the provider name in `MODEL_CONFIGS` (validates it's supported)
2. Resolves the API key: user-provided key → provider-specific env var → generic `LLM_API_KEY`
3. Creates an OpenHands `LLM` object with `SecretStr` (prevents the key from leaking in logs)
4. Adds Gemini safety settings if using Google (disables content filters for coding agents)

---

## Database & Migrations

### How the Two Databases Coexist

The frontend (Prisma) and ai_engine (SQLAlchemy + Alembic) share the **same PostgreSQL database**. But they own different tables:

- **Prisma owns:** `users`, `accounts`, `projects`, `integrations`, `agent_sessions`, `_prisma_migrations`
- **Alembic owns:** `chat_sessions`, `chat_messages`

The `alembic/env.py` file has a `PRISMA_TABLES` set that tells Alembic to ignore all Prisma-managed tables. This prevents conflicts — `alembic revision --autogenerate` won't try to create or modify Prisma's tables.

### Migration Flow

When the server starts (in Docker), it runs `alembic upgrade head` before starting uvicorn. This applies any pending migrations. The first migration (`001_add_chat_tables.py`) creates the `chat_sessions` and `chat_messages` tables with proper indexes and foreign keys.

To create a new migration:
```bash
cd ai_engine
alembic revision --autogenerate -m "description"
alembic upgrade head
```

---

## Security Model

### Authentication Chain

```
Browser → Next.js (Google OAuth) → JWT signed with SESSION_SECRET
                                          ↓
                    ┌─── REST: X-User-ID + X-Internal-Key headers
                    │         (Next.js → ai_engine server-to-server)
                    │
                    └─── WebSocket: ?token=JWT query parameter
                                    (browser → ai_engine direct)
                                          ↓
                              ai_engine validates JWT signature
                                          ↓
                              AuthenticatedUser(user_id=...)
                                          ↓
                              All queries filtered by user_id
```

### Data Isolation

Every database query includes `WHERE user_id = <authenticated_user>`:
- `ChatService.list_sessions(db, user.user_id, ...)`
- `ChatService.get_session(db, chat_id, user.user_id)`
- `ChatService.delete_session(db, session_id, user.user_id)`

Every in-memory operation checks ownership:
- `list_sessions` filters: `if s.user_id == user.user_id`
- `stop_session` checks: `if session.user_id != user.user_id: raise 403`
- `read_file` checks: `if session.user_id != user_id: raise 403`

### Container Isolation

Each Docker container is a separate process namespace. Container A cannot:
- Read Container B's files
- See Container B's processes
- Access Container B's network (unless on the same Docker network)

Containers have resource limits (memory, CPU) to prevent one user from consuming all resources.

---

## Three Degradation Modes

The system is designed to work in three configurations:

### Full Mode (Production)
- OpenHands SDK installed
- Docker daemon accessible
- Each session gets a real Docker container
- Agent reads, writes, and executes code
- Git push works

### Local Mode (No Docker)
- OpenHands SDK installed
- Docker NOT accessible (e.g., no Docker on the machine)
- Sessions use local directories (`storage/{user_id}/{session_id}/`)
- Agent works but without container isolation

### Mock Mode (Development)
- OpenHands SDK NOT installed
- No real agent execution
- WebSocket sends fake events that simulate an agent
- Useful for frontend development without the SDK

The mode is determined automatically at runtime:
```python
if not sdk.OPENHANDS_AVAILABLE:
    # Mock mode
elif docker_manager.is_docker_available():
    # Full mode (Docker)
else:
    # Local mode
```

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
              ├── every 20 events → _flush_batch() → PostgreSQL
              └── every 2 seconds → _flush_batch() → PostgreSQL
```

The queue has a max size of 1000. If the agent produces events faster than the WebSocket can send them, new events are silently dropped (`put_nowait` + `QueueFull` catch). This prevents memory issues if the browser is slow.

---

## Docker Compose Architecture

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│    frontend      │────→│    ai_engine     │────→│       db        │
│   (Next.js)      │     │   (FastAPI)      │     │  (PostgreSQL)   │
│   :3000          │     │   :8000          │     │   :5432         │
└─────────────────┘     └────────┬─────────┘     └─────────────────┘
                                 │
                        Docker Socket Mount
                        /var/run/docker.sock
                                 │
                    ┌────────────┼────────────┐
                    ↓            ↓            ↓
              ┌──────────┐ ┌──────────┐ ┌──────────┐
              │ sandbox-a │ │ sandbox-b │ │ sandbox-c │
              │ (user A)  │ │ (user B)  │ │ (user A)  │
              └──────────┘ └──────────┘ └──────────┘
```

The ai_engine creates sandbox containers by talking to the Docker daemon through the mounted socket. Each sandbox runs independently with its own filesystem, processes, and resource limits.

---

## Key Design Decisions

### Why in-memory sessions instead of database?

Agent sessions hold live Python objects (Conversation, Workspace, Agent) that can't be serialized. The database stores the history; memory stores the running state. If the server restarts, active sessions are lost — but the chat history survives.

### Why batch event writes?

The agent can produce dozens of events per second. Writing each one individually would create too many database round-trips. Batching (20 events or 2 seconds) reduces DB load while keeping persistence reasonably up-to-date.

### Why a separate Docker container per session?

Isolation. If two users run agents simultaneously, they shouldn't be able to see each other's code or interfere with each other's processes. Docker provides OS-level isolation. Container A literally cannot access Container B's filesystem.

### Why `sleep infinity` as the container command?

The container needs to stay running so we can `exec` commands into it. `sleep infinity` is a lightweight way to keep a container alive without consuming CPU. The agent runs commands via `container.exec_run()`, not as the main container process.

### Why the SDK facade pattern?

The OpenHands SDK isn't on PyPI yet. By wrapping all imports in `app/sdk.py` with a try/except, the entire application runs without it. Code checks `OPENHANDS_AVAILABLE` before calling any SDK function. This lets you develop the frontend, test the API, and work on infrastructure without needing the SDK installed.

### Why two auth methods (JWT + X-User-ID)?

JWT is the secure path for browser-to-backend communication. X-User-ID exists because the frontend's server-side API routes (Next.js) call the AI engine on behalf of the user — they already validated the user via NextAuth and just need to pass the identity along. The `INTERNAL_API_KEY` ensures only the real frontend can use this path.

### Why Alembic alongside Prisma?

Both the frontend and AI engine need database access, but they're written in different languages (JavaScript/Prisma and Python/SQLAlchemy). Rather than force one ORM on both, each service manages its own tables with its own migration tool. The `include_object` filter in `alembic/env.py` prevents them from stepping on each other.

---

## Frontend Proxy Routes (Next.js API)

The browser never talks to the ai_engine directly. All requests go through Next.js server-side API routes that add proper auth headers.

### Why proxy routes?

The ai_engine uses server-to-server auth (`X-User-ID` + `X-Internal-Key`). These headers must come from the trusted Next.js server — if the browser sent them directly, any user could spoof them. Each proxy route:

1. Verifies the user is logged in via NextAuth (returns 401 if not)
2. Adds `X-User-ID: <user's id>` and `X-Internal-Key: <shared secret>`
3. Forwards the request to the ai_engine
4. Returns the response to the browser

### Routes

| Route | Purpose |
|-------|---------|
| `GET /api/agent/token` | Issues a short-lived JWT (2h) signed with `SESSION_SECRET` for the current user. The workspace WebSocket uses this to authenticate. |
| `POST /api/agent/start` | Creates a new agent session. Decrypts the git token from Prisma, forwards all fields to ai_engine `/api/v1/sessions`. |
| `GET /api/chats` | Lists the user's chat history from ai_engine `/api/v1/chats`. Used by the conversations page. |
| `GET /api/files/read` | Reads a file from the agent's workspace via ai_engine `/api/v1/files/read`. Used by the file viewer panel. |

### WebSocket token flow

The workspace page authenticates the WebSocket by fetching a token from the proxy:

1. Page loads → `GET /api/agent/token` → receives a JWT
2. JWT passed to `useAgentSession({ token })`
3. Hook appends `?token=<JWT>` to the WebSocket URL
4. ai_engine validates the JWT signature → extracts `userId` → authenticates the session

The token has a 2-hour lifetime. The `SESSION_SECRET` is shared between Next.js (signs the JWT) and ai_engine (validates the JWT) via the same env var.

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
