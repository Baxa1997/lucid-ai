# Lucid AI — Running Guide & API Reference

## Services

| Service | Tech | Port |
|---------|------|------|
| **frontend** | Next.js 14, NextAuth v5, Prisma, Tailwind, Zustand | 3000 |
| **ai_engine** | FastAPI, OpenHands SDK V1, SQLAlchemy, Alembic | 8000 |
| **db** | PostgreSQL 15 | 5432 |

---

## Running

### Full Stack (Docker)

```bash
docker-compose up
```

Starts all three services. Alembic migrations run automatically on ai_engine boot.

### Database Only

```bash
docker-compose up -d db
```

PostgreSQL at `localhost:5432` — user: `lucid_user`, password: `lucid_password`, db: `lucid_db`.

### AI Engine (dev)

```bash
cd ai_engine
pip install -r requirements.txt
alembic upgrade head                     # run migrations
uvicorn main:app --reload --port 8000    # start dev server
```

### Frontend (dev)

```bash
cd frontend
npm install
npx prisma generate
npx prisma db push
npm run dev
```

---

## Environment Variables (AI Engine)

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DATABASE_URL` | Yes | `postgresql+asyncpg://lucid_user:lucid_password@localhost:5432/lucid_db` | PostgreSQL connection |
| `SESSION_SECRET` | Yes | `change_me_in_prod` | JWT signing secret (must match frontend) |
| `INTERNAL_API_KEY` | Recommended | — | When set, `X-User-ID` header only trusted with matching `X-Internal-Key` |
| `WORKSPACE_BASE_PATH` | No | `./storage` | Per-user workspace root |
| `ANTHROPIC_API_KEY` | No* | — | Anthropic API key |
| `GOOGLE_API_KEY` | No* | — | Google Gemini API key |
| `LLM_API_KEY` | No | — | Generic fallback LLM key |
| `DEFAULT_MODEL_PROVIDER` | No | `anthropic` | `"anthropic"` or `"google"` |
| `MAX_ITERATIONS` | No | `50` | Agent max iterations |
| `CONVERSATION_TIMEOUT` | No | `1800` | Agent timeout in seconds |
| `PORT` | No | `8000` | API server port |
| `SANDBOX_CONTAINER_PREFIX` | No | `lucid-sandbox-` | Docker container name prefix |
| `SANDBOX_MEMORY_LIMIT` | No | `2g` | Memory limit per sandbox container |
| `SANDBOX_CPU_LIMIT` | No | `1.0` | CPU limit per sandbox container |
| `DOCKER_NETWORK` | No | — | Docker network for sandbox containers |

\* At least one LLM key required for real agent execution. Without it, runs in mock mode.

---

## Authentication

All `/api/v1/*` endpoints require authentication. Two methods:

**1. Bearer JWT** (preferred)
```
Authorization: Bearer <jwt-token>
```
JWT signed with `SESSION_SECRET` (HS256). Payload: `{ "userId", "projectId", "sessionId" }`.

**2. X-User-ID header** (server-to-server only)
```
X-User-ID: <user-id>
X-Internal-Key: <INTERNAL_API_KEY>
```
When `INTERNAL_API_KEY` is set, the `X-Internal-Key` header **must** match or the request is rejected with 401. This prevents external callers from impersonating users. Without `INTERNAL_API_KEY` (dev mode), X-User-ID is accepted with a warning.

**WebSocket** authenticates via `?token=<JWT>` query param or `token` field in the first message. **Anonymous connections are rejected** — a valid JWT is required.

---

## API Endpoints

Base URL: `http://localhost:8000`

All versioned endpoints use the `/api/v1` prefix.

---

### Health (no auth)

#### `GET /`

System status.

```bash
curl http://localhost:8000/
```

```json
{
  "service": "Lucid AI Engine",
  "version": "1.0.0",
  "status": "healthy",
  "openhands_available": false,
  "docker_available": true,
  "active_sandboxes": 0,
  "active_sessions": 0,
  "llm_model": "anthropic/claude-3-5-sonnet-20241022"
}
```

#### `GET /health`

Minimal health check.

```bash
curl http://localhost:8000/health
```

```json
{ "status": "ok" }
```

---

### Agent Sessions — `/api/v1/sessions`

#### `POST /api/v1/sessions`

Create a new agent session.

```bash
curl -X POST http://localhost:8000/api/v1/sessions \
  -H "Content-Type: application/json" \
  -H "X-User-ID: test-user-123" \
  -d '{
    "task": "Create a REST API with Express.js",
    "repoUrl": "https://github.com/user/repo",
    "gitToken": "ghp_xxxx",
    "projectId": "proj-123",
    "model_provider": "anthropic",
    "api_key": "sk-ant-xxxx"
  }'
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `task` | string | Yes | Task for the agent |
| `repoUrl` | string | No | Git repo URL to clone |
| `gitToken` | string | No | Git access token |
| `gitUserName` | string | No | Git user name for commits |
| `gitUserEmail` | string | No | Git user email for commits |
| `projectId` | string | No | Project identifier |
| `model_provider` | string | No | `"anthropic"` or `"google"` |
| `api_key` | string | No | User's own LLM API key |

**Response (200):**
```json
{
  "status": "ready",
  "sessionId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "message": "Agent session initialized. Connect via WebSocket to start."
}
```

Without OpenHands SDK: `"status": "mock"`.

**Errors:** `400` (invalid provider/key), `401` (bad LLM key), `500` (creation failed)

#### `GET /api/v1/sessions`

List the authenticated user's active (in-memory) agent sessions.

```bash
curl -H "X-User-ID: test-user-123" http://localhost:8000/api/v1/sessions
```

```json
{
  "sessions": [
    {
      "sessionId": "a1b2c3d4-...",
      "userId": "test-user-123",
      "task": "Create a REST API with Express.js",
      "isAlive": true,
      "createdAt": "2026-02-17T10:30:00+00:00"
    }
  ]
}
```

#### `DELETE /api/v1/sessions/{session_id}`

Stop and destroy an agent session. Only the session owner can do this.

```bash
curl -X DELETE -H "X-User-ID: test-user-123" \
  http://localhost:8000/api/v1/sessions/a1b2c3d4-...
```

```json
{
  "status": "stopped",
  "sessionId": "a1b2c3d4-...",
  "message": "Session stopped and resources cleaned up."
}
```

**Errors:** `403` (not the owner), `404` (not found)

---

### Chat History — `/api/v1/chats`

All endpoints scoped to the authenticated user.

#### `GET /api/v1/chats`

Paginated list of chats, newest first.

```bash
curl -H "X-User-ID: test-user-123" \
  "http://localhost:8000/api/v1/chats?limit=10&offset=0"
```

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `limit` | int | 50 | Results per page (1-100) |
| `offset` | int | 0 | Skip N results |

```json
{
  "chats": [
    {
      "id": "uuid-...",
      "agentSessionId": "a1b2c3d4-...",
      "projectId": "proj-123",
      "title": "Create a REST API with Express.js",
      "modelProvider": "anthropic",
      "isActive": false,
      "createdAt": "2026-02-17T10:30:00+00:00",
      "updatedAt": "2026-02-17T11:00:00+00:00"
    }
  ]
}
```

#### `GET /api/v1/chats/{chat_id}`

Get a chat with all messages.

```bash
curl -H "X-User-ID: test-user-123" \
  http://localhost:8000/api/v1/chats/uuid-...
```

```json
{
  "id": "uuid-...",
  "agentSessionId": "a1b2c3d4-...",
  "title": "Create a REST API with Express.js",
  "modelProvider": "anthropic",
  "isActive": false,
  "createdAt": "2026-02-17T10:30:00+00:00",
  "updatedAt": "2026-02-17T11:00:00+00:00",
  "messages": [
    {
      "id": "msg-uuid-...",
      "role": "user",
      "content": "Create a REST API with Express.js",
      "eventType": "InitialTask",
      "metadataJson": null,
      "createdAt": "2026-02-17T10:30:01+00:00"
    },
    {
      "id": "msg-uuid-...",
      "role": "assistant",
      "content": "Analyzing task...",
      "eventType": "ThinkAction",
      "metadataJson": null,
      "createdAt": "2026-02-17T10:30:02+00:00"
    }
  ]
}
```

**Errors:** `404` (not found or belongs to another user)

#### `DELETE /api/v1/chats/{chat_id}`

Delete a chat and all its messages.

```bash
curl -X DELETE -H "X-User-ID: test-user-123" \
  http://localhost:8000/api/v1/chats/uuid-...
```

```json
{ "status": "deleted", "id": "uuid-..." }
```

#### `PATCH /api/v1/chats/{chat_id}`

Rename a chat.

```bash
curl -X PATCH http://localhost:8000/api/v1/chats/uuid-... \
  -H "Content-Type: application/json" \
  -H "X-User-ID: test-user-123" \
  -d '{ "title": "Express API Project" }'
```

```json
{ "status": "updated", "id": "uuid-...", "title": "Express API Project" }
```

**Errors:** `400` (missing title), `404`

---

### Files — `/api/v1/files`

#### `GET /api/v1/files/list?session_id={id}`

List all files in the agent's workspace as a recursive tree.

```bash
curl -H "X-User-ID: test-user-123" \
  "http://localhost:8000/api/v1/files/list?session_id=a1b2c3d4-..."
```

```json
{
  "tree": [
    {
      "name": "repo",
      "type": "folder",
      "path": "/workspace/repo",
      "children": [
        { "name": "package.json", "type": "file", "path": "/workspace/repo/package.json" },
        { "name": "src", "type": "folder", "path": "/workspace/repo/src", "children": [...] }
      ]
    }
  ]
}
```

#### `GET /api/v1/files/read?session_id={id}&path={path}`

Read a file from the agent's workspace.

```bash
curl -H "X-User-ID: test-user-123" \
  "http://localhost:8000/api/v1/files/read?session_id=a1b2c3d4-...&path=/workspace/repo/package.json"
```

```json
{ "content": "{\n  \"name\": \"my-app\",\n  ..." }
```

**Errors:** `400` (path traversal), `403` (not session owner), `404` (file/session not found)

---

### WebSocket — `/api/v1/ws`

Real-time agent communication.

#### Connection

```
ws://localhost:8000/api/v1/ws?token=<JWT>
```

#### Protocol

```
Client                              Server
  │                                    │
  ├── connect /api/v1/ws?token= ────►│ accept + authenticate
  │                                    │
  ├── { task, repoUrl, ... } ────────►│ create session
  │                                    │
  │◄── { type: status, initializing } ─┤
  │◄── { type: status, ready }  ───────┤
  │◄── { type: agent_event, ... } ─────┤ streams events
  │◄── { type: status, completed } ────┤
  │                                    │
  ├── { type: message, content } ─────►│ follow-up
  │◄── { type: agent_event, ... } ─────┤
  │◄── { type: status, completed } ────┤
  │                                    │
  ├── { type: stop } ────────────────►│ cleanup
  │◄── { type: status, stopping } ─────┤
```

#### Client → Server

**Initial config** (within 30s):
```json
{
  "task": "Fix the login bug",
  "repoUrl": "https://github.com/user/repo",
  "token": "ghp_xxxx",
  "gitUserName": "John Doe",
  "gitUserEmail": "john@example.com",
  "projectId": "clxyz123",
  "modelProvider": "anthropic",
  "apiKey": "sk-ant-xxxx"
}
```

**Follow-up:** `{ "type": "message", "content": "Now add unit tests" }`

**Stop:** `{ "type": "stop", "content": "any" }`

#### Server → Client

**Status:**
```json
{
  "type": "status",
  "status": "initializing | ready | mock_mode | completed | stopping",
  "sessionId": "UUID",
  "message": "..."
}
```

**Agent event:**
```json
{
  "type": "agent_event",
  "event": "action | observation | error | state",
  "eventType": "ThinkAction | CmdRunAction | CmdOutputObservation | ...",
  "content": "string (max 2000 chars)",
  "timestamp": "ISO-8601",
  "command": "optional",
  "exitCode": 0,
  "path": "optional",
  "thought": "optional (max 1000 chars)"
}
```

**Error:** `{ "type": "error", "message": "..." }`

#### Chat Persistence

When an authenticated user connects:
1. `ChatSession` record created in PostgreSQL
2. Initial task saved as `ChatMessage` (role: `"user"`)
3. Agent events saved as `ChatMessage` (role: `"assistant"`)
4. Follow-up messages saved as `ChatMessage` (role: `"user"`)
5. On disconnect, `is_active` set to `false`

Retrieve later via `GET /api/v1/chats/{id}`.

---

## Endpoint Summary

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/` | No | System status |
| `GET` | `/health` | No | Health check |
| `POST` | `/api/v1/sessions` | Yes | Create agent session |
| `GET` | `/api/v1/sessions` | Yes | List active sessions |
| `DELETE` | `/api/v1/sessions/{id}` | Yes | Stop agent session |
| `GET` | `/api/v1/chats` | Yes | List chat history |
| `GET` | `/api/v1/chats/{id}` | Yes | Get chat with messages |
| `DELETE` | `/api/v1/chats/{id}` | Yes | Delete chat |
| `PATCH` | `/api/v1/chats/{id}` | Yes | Rename chat |
| `GET` | `/api/v1/files/list` | Yes | List workspace files |
| `GET` | `/api/v1/files/read` | Yes | Read workspace file |
| `WS` | `/api/v1/ws` | Yes | Agent WebSocket |

---

## Database

### Migrations

```bash
cd ai_engine
alembic upgrade head                              # apply all
alembic current                                   # check version
alembic revision --autogenerate -m "description"  # new migration
```

### Tables (owned by ai_engine)

**`chat_sessions`**

| Column | Type | Notes |
|--------|------|-------|
| `id` | varchar | PK, UUID |
| `user_id` | varchar | FK → `users.id` |
| `agent_session_id` | varchar | Runtime session UUID |
| `project_id` | varchar | nullable |
| `title` | varchar(255) | From first message |
| `model_provider` | varchar(50) | |
| `is_active` | boolean | |
| `created_at` | timestamptz | |
| `updated_at` | timestamptz | |

**`chat_messages`**

| Column | Type | Notes |
|--------|------|-------|
| `id` | varchar | PK, UUID |
| `session_id` | varchar | FK → `chat_sessions.id` |
| `role` | varchar(20) | `"user"`, `"assistant"`, `"system"` |
| `content` | text | |
| `event_type` | varchar(100) | e.g. `"ThinkAction"`, `"CmdRunAction"` |
| `metadata_json` | text | Optional JSON |
| `created_at` | timestamptz | |

Prisma-managed tables (`users`, `accounts`, `organizations`, etc.) are excluded from Alembic migrations.

---

## Workspace Isolation

Each agent session gets its own isolated Docker container (per-session sandboxing):

1. **Full mode** (SDK + Docker available): Per-session Docker containers with real agent execution, git push support
2. **Local mode** (SDK available, Docker unavailable): Local workspace directories as fallback
3. **Mock mode** (SDK not installed): Simulated agent responses for development

### Docker Sandbox

When Docker is available, each session creates a container from `SANDBOX_IMAGE` with:
- Isolated `/workspace` directory
- Git credentials configured for push (if provided)
- Memory/CPU limits (`SANDBOX_MEMORY_LIMIT`, `SANDBOX_CPU_LIMIT`)
- Automatic cleanup on session destroy

### Git Push Flow

1. User provides `repoUrl`, `gitToken`, `gitUserName`, `gitUserEmail` when starting a session
2. Agent's Docker container has the repo cloned and git configured
3. Agent can run: `git checkout -b fix/bug-123`, make changes, `git add .`, `git commit -m "Fix bug"`, `git push origin fix/bug-123`

### Local Fallback

When Docker is unavailable, workspaces are stored as local directories:

```
storage/
├── user-id-1/
│   ├── session-uuid-a/
│   └── session-uuid-b/
├── user-id-2/
│   └── session-uuid-c/
```

Controlled by `WORKSPACE_BASE_PATH` env var.

---

## Quick Test

```bash
# 1. Health (no auth)
curl http://localhost:8000/health

# 2. System status (no auth)
curl http://localhost:8000/

# 3. Unauthenticated → 401
curl http://localhost:8000/api/v1/chats

# 4. List chats (empty)
curl -H "X-User-ID: test-user" http://localhost:8000/api/v1/chats

# 5. Create agent session (mock mode)
curl -X POST http://localhost:8000/api/v1/sessions \
  -H "Content-Type: application/json" \
  -H "X-User-ID: test-user" \
  -d '{ "task": "Build a hello world app" }'

# 6. List active sessions
curl -H "X-User-ID: test-user" http://localhost:8000/api/v1/sessions

# 7. Stop session
curl -X DELETE -H "X-User-ID: test-user" \
  http://localhost:8000/api/v1/sessions/<sessionId>

# 8. WebSocket (use wscat)
npx wscat -c "ws://localhost:8000/api/v1/ws"
> { "task": "Hello agent", "userId": "test-user" }
```
