# Lucid AI — Manual Test Guide

This file walks through every backend API endpoint you can test yourself, in order. Each test builds on the previous one.

---

## Prerequisites

### 1. Start the services

```bash
docker-compose up
```

This starts: **frontend** (:3000), **ai_engine** (:8000), **postgres** (:5432).

Wait until you see: `Lucid AI Engine starting …` in the logs.

### 2. Install test tools

```bash
# wscat — for WebSocket testing
npm install -g wscat

# jq — for pretty-printing JSON (optional)
brew install jq
```

### 3. Set up shell variables

Copy-paste this block into your terminal once. All tests below use these variables.

```bash
BASE=http://localhost:8000
USER_A="00000000-0000-0000-0000-000000000001"
USER_B="00000000-0000-0000-0000-000000000002"
INTERNAL_KEY="lucid_internal_k8s_2f9a7e3c1d4b6e8a0f2c5d7e9b1a3c5d"

# Replace with a real GitHub/GitLab PAT for integration tests (Test 13)
GITHUB_TOKEN="ghp_your_token_here"
GITLAB_TOKEN="glpat_your_token_here"
```

`INTERNAL_KEY` matches `INTERNAL_API_KEY` in `docker-compose.yml`. Every curl that uses `X-User-ID` must also include `X-Internal-Key` when running against docker-compose (which has `INTERNAL_API_KEY` set by default).

> **Dev mode only (no `INTERNAL_API_KEY` set):** `X-User-ID` works without the key — server logs a warning. Not applicable when using docker-compose.

---

## Test 1 — Health Checks (no auth)

These endpoints have **no authentication**. They should always work.

### 1.1 System status

```bash
curl -s $BASE/ | jq
```

**Expected:**
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

**What to check:**
- `docker_available` should be `true` if Docker socket is mounted
- `openhands_available` will be `false` until SDK is installed (that's OK — mock mode)
- `active_sessions` should be `0` on fresh start

### 1.2 Minimal health check

```bash
curl -s $BASE/health | jq
```

**Expected:**
```json
{ "status": "ok" }
```

---

## Test 2 — Authentication

Verify that endpoints reject unauthenticated requests.

### 2.1 No auth → 401

```bash
curl -s -w "\nHTTP %{http_code}\n" $BASE/api/v1/sessions
```

**Expected:** `HTTP 401` with `"Authentication required"`.

### 2.2 Valid auth → 200

```bash
curl -s \
  -H "X-User-ID: $USER_A" \
  -H "X-Internal-Key: $INTERNAL_KEY" \
  $BASE/api/v1/sessions | jq
```

**Expected:** `HTTP 200` with `{"sessions": []}`.

### 2.3 Wrong internal key → 401

```bash
curl -s -w "\nHTTP %{http_code}\n" \
  -H "X-User-ID: $USER_A" \
  -H "X-Internal-Key: wrong-key" \
  $BASE/api/v1/sessions
```

**Expected:** `HTTP 401` with `"Invalid internal API key"`.

---

## Test 3 — Create Agent Sessions

### 3.1 Create session for User A (minimal)

```bash
curl -s -X POST $BASE/api/v1/sessions \
  -H "Content-Type: application/json" \
  -H "X-User-ID: $USER_A" \
  -H "X-Internal-Key: $INTERNAL_KEY" \
  -d '{
    "task": "Build a hello world Express.js app"
  }' | jq
```

**Expected:**
```json
{
  "status": "mock",
  "sessionId": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "message": "Mock session created — OpenHands SDK not installed. ..."
}
```

Save the session ID:
```bash
SESSION_A=$(curl -s -X POST $BASE/api/v1/sessions \
  -H "Content-Type: application/json" \
  -H "X-User-ID: $USER_A" \
  -H "X-Internal-Key: $INTERNAL_KEY" \
  -d '{"task": "Build a hello world app"}' | jq -r '.sessionId')
echo "Session A: $SESSION_A"
```

### 3.2 Create session for User B

```bash
SESSION_B=$(curl -s -X POST $BASE/api/v1/sessions \
  -H "Content-Type: application/json" \
  -H "X-User-ID: $USER_B" \
  -H "X-Internal-Key: $INTERNAL_KEY" \
  -d '{"task": "Fix the login bug"}' | jq -r '.sessionId')
echo "Session B: $SESSION_B"
```

### 3.3 Create session with git repo

```bash
curl -s -X POST $BASE/api/v1/sessions \
  -H "Content-Type: application/json" \
  -H "X-User-ID: $USER_A" \
  -H "X-Internal-Key: $INTERNAL_KEY" \
  -d '{
    "task": "Fix the authentication bug in login.js",
    "repoUrl": "https://github.com/your-user/your-repo",
    "gitToken": "ghp_your_github_pat",
    "branch": "main",
    "gitUserName": "Alice",
    "gitUserEmail": "alice@example.com",
    "model_provider": "anthropic",
    "api_key": "sk-ant-your-key"
  }' | jq
```

**What this does:**
- Creates a Docker container for this session
- Clones the repo inside the container
- Configures git credentials for push
- If Docker is unavailable, falls back to local workspace

---

## Test 4 — List Sessions (User Isolation)

### 4.1 User A sees only their sessions

```bash
curl -s \
  -H "X-User-ID: $USER_A" \
  -H "X-Internal-Key: $INTERNAL_KEY" \
  $BASE/api/v1/sessions | jq
```

**Expected:** Only sessions created by User A.

### 4.2 User B sees only their sessions

```bash
curl -s \
  -H "X-User-ID: $USER_B" \
  -H "X-Internal-Key: $INTERNAL_KEY" \
  $BASE/api/v1/sessions | jq
```

**Expected:** Only sessions created by User B. User A's sessions are NOT visible.

### 4.3 Verify system status shows total

```bash
curl -s $BASE/ | jq '.active_sessions'
```

**Expected:** Total count of ALL sessions (A + B).

---

## Test 5 — Stop Sessions (Ownership Check)

### 5.1 User A stops their own session → 200

```bash
curl -s -X DELETE \
  -H "X-User-ID: $USER_A" \
  -H "X-Internal-Key: $INTERNAL_KEY" \
  $BASE/api/v1/sessions/$SESSION_A | jq
```

**Expected:**
```json
{
  "status": "stopped",
  "sessionId": "...",
  "message": "Session stopped and resources cleaned up."
}
```

### 5.2 User A tries to stop User B's session → 403

```bash
curl -s -w "\nHTTP %{http_code}\n" \
  -X DELETE \
  -H "X-User-ID: $USER_A" \
  -H "X-Internal-Key: $INTERNAL_KEY" \
  $BASE/api/v1/sessions/$SESSION_B
```

**Expected:** `HTTP 403` with `"Not authorized to stop this session."`.

### 5.3 Stop non-existent session → 404

```bash
curl -s -w "\nHTTP %{http_code}\n" \
  -X DELETE \
  -H "X-User-ID: $USER_A" \
  -H "X-Internal-Key: $INTERNAL_KEY" \
  $BASE/api/v1/sessions/fake-session-id
```

**Expected:** `HTTP 404`.

---

## Test 6 — WebSocket Agent Communication

### 6.1 Connect without auth → rejected

```bash
wscat -c "ws://localhost:8000/api/v1/ws"
```

Once connected, send:
```json
{"task": "Hello agent"}
```

**Expected:** Server sends error `"Authentication required. Provide a valid JWT token."` and closes with code 4010.

### 6.2 Connect with JWT token

Generate a test JWT (requires Node.js):

```bash
TOKEN=$(node -e "
  const jwt = require('jsonwebtoken');
  const secret = '6977c6e899b6846aee73922de6ac653ddf41e5074cb904816eb2d3c5f5cd9325';
  const token = jwt.sign(
    { userId: '$USER_A', projectId: 'test-project' },
    secret,
    { expiresIn: '1h' }
  );
  console.log(token);
")
echo "Token: $TOKEN"
```

> **Note:** You need `jsonwebtoken` installed: `npm install jsonwebtoken`

Connect with the token:

```bash
wscat -c "ws://localhost:8000/api/v1/ws?token=$TOKEN"
```

Once connected, send the initial config:
```json
{"task": "Create a hello world Python script", "modelProvider": "anthropic"}
```

**Expected sequence of messages from server:**
1. `{"type": "status", "status": "initializing", ...}`
2. `{"type": "status", "status": "mock_mode", "sessionId": "...", ...}` (if SDK not installed)
3. Several `{"type": "agent_event", ...}` mock events
4. `{"type": "status", "status": "completed", ...}`

Send a follow-up:
```json
{"type": "message", "content": "Now add unit tests"}
```

Stop the agent:
```json
{"type": "stop", "content": "done"}
```

### 6.3 Connect with token in handshake message (alternative)

```bash
wscat -c "ws://localhost:8000/api/v1/ws"
```

Send token in the first message:
```json
{"task": "Build a REST API", "token": "<paste-your-JWT-here>"}
```

**Expected:** Same behavior as 6.2.

---

## Test 7 — Chat History

Chat sessions are created automatically when a WebSocket session starts (Test 6). Now query them.

### 7.1 List chats for User A

```bash
curl -s \
  -H "X-User-ID: $USER_A" \
  -H "X-Internal-Key: $INTERNAL_KEY" \
  "$BASE/api/v1/chats?limit=10&offset=0" | jq
```

Save the chat ID:
```bash
CHAT_ID=$(curl -s \
  -H "X-User-ID: $USER_A" \
  -H "X-Internal-Key: $INTERNAL_KEY" \
  "$BASE/api/v1/chats" | jq -r '.chats[0].id')
echo "Chat ID: $CHAT_ID"
```

### 7.2 Get chat with messages

```bash
curl -s \
  -H "X-User-ID: $USER_A" \
  -H "X-Internal-Key: $INTERNAL_KEY" \
  $BASE/api/v1/chats/$CHAT_ID | jq
```

**Expected:** Chat object with a `messages` array.

### 7.3 User B cannot see User A's chats → 404

```bash
curl -s -w "\nHTTP %{http_code}\n" \
  -H "X-User-ID: $USER_B" \
  -H "X-Internal-Key: $INTERNAL_KEY" \
  $BASE/api/v1/chats/$CHAT_ID
```

**Expected:** `HTTP 404`.

### 7.4 Rename a chat

```bash
curl -s -X PATCH $BASE/api/v1/chats/$CHAT_ID \
  -H "Content-Type: application/json" \
  -H "X-User-ID: $USER_A" \
  -H "X-Internal-Key: $INTERNAL_KEY" \
  -d '{"title": "My Hello World Project"}' | jq
```

**Expected:** `{ "status": "updated", "id": "...", "title": "My Hello World Project" }`

### 7.5 Delete a chat

```bash
curl -s -X DELETE \
  -H "X-User-ID: $USER_A" \
  -H "X-Internal-Key: $INTERNAL_KEY" \
  $BASE/api/v1/chats/$CHAT_ID | jq
```

**Expected:** `{ "status": "deleted", "id": "..." }`

Verify it's gone:
```bash
curl -s -w "\nHTTP %{http_code}\n" \
  -H "X-User-ID: $USER_A" \
  -H "X-Internal-Key: $INTERNAL_KEY" \
  $BASE/api/v1/chats/$CHAT_ID
```

**Expected:** `HTTP 404`.

---

## Test 8 — File Explorer (requires active session)

Create a fresh session first:

```bash
SESSION_FILES=$(curl -s -X POST $BASE/api/v1/sessions \
  -H "Content-Type: application/json" \
  -H "X-User-ID: $USER_A" \
  -H "X-Internal-Key: $INTERNAL_KEY" \
  -d '{"task": "Test file explorer"}' | jq -r '.sessionId')
echo "Session: $SESSION_FILES"
```

### 8.1 List files in workspace

```bash
curl -s \
  -H "X-User-ID: $USER_A" \
  -H "X-Internal-Key: $INTERNAL_KEY" \
  "$BASE/api/v1/files/list?session_id=$SESSION_FILES" | jq
```

**Expected:** A `tree` array showing the workspace file structure.

### 8.2 Read a file

```bash
curl -s \
  -H "X-User-ID: $USER_A" \
  -H "X-Internal-Key: $INTERNAL_KEY" \
  "$BASE/api/v1/files/read?session_id=$SESSION_FILES&path=/workspace/hello.py" | jq
```

**Expected:** `{"content": "..."}` if file exists, or `HTTP 404` if not.

### 8.3 Path traversal blocked → 400

```bash
curl -s -w "\nHTTP %{http_code}\n" \
  -H "X-User-ID: $USER_A" \
  -H "X-Internal-Key: $INTERNAL_KEY" \
  "$BASE/api/v1/files/read?session_id=$SESSION_FILES&path=../../etc/passwd"
```

**Expected:** `HTTP 400` with `"Path traversal not allowed."`.

### 8.4 User B cannot access User A's files → 403

```bash
curl -s -w "\nHTTP %{http_code}\n" \
  -H "X-User-ID: $USER_B" \
  -H "X-Internal-Key: $INTERNAL_KEY" \
  "$BASE/api/v1/files/list?session_id=$SESSION_FILES"
```

**Expected:** `HTTP 403`.

---

## Test 9 — Docker Sandbox Verification

### 9.1 Verify Docker is detected

```bash
curl -s $BASE/ | jq '.docker_available'
```

**Expected:** `true`.

### 9.2 Check sandbox count

```bash
curl -s $BASE/ | jq '.active_sandboxes'
docker ps --filter "label=lucid.managed=true"
```

### 9.3 Verify container isolation

```bash
docker ps --filter "label=lucid.managed=true" --format "table {{.Names}}\t{{.Status}}\t{{.Labels}}"
```

Each container should have a unique `lucid.session_id` label.

### 9.4 Stop session and verify cleanup

```bash
curl -s -X DELETE \
  -H "X-User-ID: $USER_A" \
  -H "X-Internal-Key: $INTERNAL_KEY" \
  $BASE/api/v1/sessions/$SESSION_FILES | jq

docker ps --filter "label=lucid.managed=true"
```

**Expected:** The container for the stopped session is removed.

---

## Test 10 — Multi-User Isolation (Full Flow)

### Step 1: Create sessions for both users

```bash
SESSION_A2=$(curl -s -X POST $BASE/api/v1/sessions \
  -H "Content-Type: application/json" \
  -H "X-User-ID: $USER_A" \
  -H "X-Internal-Key: $INTERNAL_KEY" \
  -d '{"task": "Fix bug in auth module"}' | jq -r '.sessionId')

SESSION_B2=$(curl -s -X POST $BASE/api/v1/sessions \
  -H "Content-Type: application/json" \
  -H "X-User-ID: $USER_B" \
  -H "X-Internal-Key: $INTERNAL_KEY" \
  -d '{"task": "Add dark mode support"}' | jq -r '.sessionId')

echo "A: $SESSION_A2"
echo "B: $SESSION_B2"
```

### Step 2: Each user only sees their own

```bash
echo "=== User A sessions ==="
curl -s -H "X-User-ID: $USER_A" -H "X-Internal-Key: $INTERNAL_KEY" \
  $BASE/api/v1/sessions | jq '.sessions[].task'

echo "=== User B sessions ==="
curl -s -H "X-User-ID: $USER_B" -H "X-Internal-Key: $INTERNAL_KEY" \
  $BASE/api/v1/sessions | jq '.sessions[].task'
```

### Step 3: Cross-access blocked → 403

```bash
curl -s -w "\nHTTP %{http_code}\n" \
  -X DELETE \
  -H "X-User-ID: $USER_A" \
  -H "X-Internal-Key: $INTERNAL_KEY" \
  $BASE/api/v1/sessions/$SESSION_B2

curl -s -w "\nHTTP %{http_code}\n" \
  -H "X-User-ID: $USER_B" \
  -H "X-Internal-Key: $INTERNAL_KEY" \
  "$BASE/api/v1/files/list?session_id=$SESSION_A2"
```

**Expected:** Both return `403 Forbidden`.

### Step 4: Clean up

```bash
curl -s -X DELETE \
  -H "X-User-ID: $USER_A" -H "X-Internal-Key: $INTERNAL_KEY" \
  $BASE/api/v1/sessions/$SESSION_A2 | jq

curl -s -X DELETE \
  -H "X-User-ID: $USER_B" -H "X-Internal-Key: $INTERNAL_KEY" \
  $BASE/api/v1/sessions/$SESSION_B2 | jq
```

---

## Test 11 — Error Cases

### 11.1 Missing required field (task) → 422

```bash
curl -s -w "\nHTTP %{http_code}\n" \
  -X POST $BASE/api/v1/sessions \
  -H "Content-Type: application/json" \
  -H "X-User-ID: $USER_A" \
  -H "X-Internal-Key: $INTERNAL_KEY" \
  -d '{}'
```

**Expected:** `HTTP 422`.

### 11.2 Invalid model provider

```bash
curl -s -w "\nHTTP %{http_code}\n" \
  -X POST $BASE/api/v1/sessions \
  -H "Content-Type: application/json" \
  -H "X-User-ID: $USER_A" \
  -H "X-Internal-Key: $INTERNAL_KEY" \
  -d '{"task": "test", "model_provider": "openai"}'
```

**Expected:** `HTTP 400` or `HTTP 500` (mock mode may succeed since LLM is not called).

### 11.3 Chat pagination

```bash
curl -s -H "X-User-ID: $USER_A" -H "X-Internal-Key: $INTERNAL_KEY" \
  "$BASE/api/v1/chats?limit=2&offset=0" | jq '.chats | length'

curl -s -H "X-User-ID: $USER_A" -H "X-Internal-Key: $INTERNAL_KEY" \
  "$BASE/api/v1/chats?limit=2&offset=2" | jq '.chats | length'
```

### 11.4 Rename with missing title → 400

```bash
curl -s -w "\nHTTP %{http_code}\n" \
  -X PATCH $BASE/api/v1/chats/some-chat-id \
  -H "Content-Type: application/json" \
  -H "X-User-ID: $USER_A" \
  -H "X-Internal-Key: $INTERNAL_KEY" \
  -d '{}'
```

**Expected:** `HTTP 400` with `"Missing 'title'"`.

---

## Test 12 — Frontend Proxy Routes

These routes run inside Next.js (port 3000). Test from a browser console while logged in to `http://localhost:3000`.

### 12.1 Get WebSocket JWT token

```js
const res = await fetch('/api/agent/token');
const { token } = await res.json();
console.log('JWT:', token);
// Use: wscat -c "ws://localhost:8000/api/v1/ws?token=<token>"
```

**Expected:** `{ "token": "<JWT>" }` — a signed 2-hour token for the current user.

### 12.2 List chats via proxy

```js
const res = await fetch('/api/chats?limit=10&offset=0');
const data = await res.json();
console.log(data.chats);
```

**Expected:** Same as `GET /api/v1/chats` — auth headers added server-side by Next.js.

### 12.3 Read a workspace file via proxy

```js
const res = await fetch('/api/files/read?session_id=<SESSION_ID>&path=/workspace/hello.py');
const { content } = await res.json();
console.log(content);
```

**Expected:** `{ "content": "..." }` if file exists. `401` if not logged in, `403` if wrong user.

---

---

## Test 13 — Git Integrations (PAT save, repo listing, PR creation)

**Requires a real GitHub or GitLab PAT.** Set `GITHUB_TOKEN` / `GITLAB_TOKEN` in your shell variables.

> **Note:** The `ENCRYPTION_KEY` env var must be set in both the ai_engine container and the frontend container with the **same value** — the ai_engine encrypts with it and the frontend decrypts with it.

### 13.1 Save a GitHub PAT (validates token against GitHub API)

```bash
curl -s -X POST $BASE/api/v1/integrations \
  -H "Content-Type: application/json" \
  -H "X-User-ID: $USER_A" \
  -H "X-Internal-Key: $INTERNAL_KEY" \
  -d "{\"provider\": \"github\", \"token\": \"$GITHUB_TOKEN\"}" | jq
```

**Expected:**
```json
{
  "status": "connected",
  "provider": "github",
  "username": "your-github-username",
  "message": "Connected as your-github-username"
}
```

### 13.2 Save a GitLab PAT

```bash
curl -s -X POST $BASE/api/v1/integrations \
  -H "Content-Type: application/json" \
  -H "X-User-ID: $USER_A" \
  -H "X-Internal-Key: $INTERNAL_KEY" \
  -d "{\"provider\": \"gitlab\", \"token\": \"$GITLAB_TOKEN\"}" | jq
```

For self-hosted GitLab, add `"gitlabUrl": "https://gitlab.example.com"` to the body.

### 13.3 Invalid token → 422

```bash
curl -s -w "\nHTTP %{http_code}\n" \
  -X POST $BASE/api/v1/integrations \
  -H "Content-Type: application/json" \
  -H "X-User-ID: $USER_A" \
  -H "X-Internal-Key: $INTERNAL_KEY" \
  -d '{"provider": "github", "token": "bad_token"}'
```

**Expected:** `HTTP 422` with `"Token validation failed"`.

### 13.4 List connected integrations

```bash
curl -s \
  -H "X-User-ID: $USER_A" \
  -H "X-Internal-Key: $INTERNAL_KEY" \
  $BASE/api/v1/integrations | jq
```

**Expected:** Array with one entry per connected provider. Tokens are never returned.

### 13.5 List repos (B2)

```bash
curl -s \
  -H "X-User-ID: $USER_A" \
  -H "X-Internal-Key: $INTERNAL_KEY" \
  $BASE/api/v1/integrations/github/repos | jq '.repos[0:3]'
```

**Expected:** Array of repos with `name`, `fullName`, `cloneUrl`, `defaultBranch`, `private`.

### 13.6 List repos with no token saved → 404

```bash
curl -s -w "\nHTTP %{http_code}\n" \
  -H "X-User-ID: $USER_B" \
  -H "X-Internal-Key: $INTERNAL_KEY" \
  $BASE/api/v1/integrations/github/repos
```

**Expected:** `HTTP 404` with `"No github token saved"`.

### 13.7 Create a PR (B3)

After the agent pushes a branch, call this to open the PR:

```bash
curl -s -X POST $BASE/api/v1/integrations/github/pr \
  -H "Content-Type: application/json" \
  -H "X-User-ID: $USER_A" \
  -H "X-Internal-Key: $INTERNAL_KEY" \
  -d '{
    "repoUrl": "https://github.com/your-user/your-repo",
    "branch": "fix/auth-bug",
    "baseBranch": "main",
    "title": "Fix authentication bug",
    "body": "Agent fixed the auth module as requested."
  }' | jq
```

**Expected:**
```json
{
  "status": "created",
  "provider": "github",
  "prUrl": "https://github.com/your-user/your-repo/pull/42",
  "prNumber": 42,
  "title": "Fix authentication bug",
  "state": "open"
}
```

### 13.8 Unsupported provider → 400

```bash
curl -s -w "\nHTTP %{http_code}\n" \
  -X POST $BASE/api/v1/integrations \
  -H "Content-Type: application/json" \
  -H "X-User-ID: $USER_A" \
  -H "X-Internal-Key: $INTERNAL_KEY" \
  -d '{"provider": "bitbucket", "token": "abc"}'
```

**Expected:** `HTTP 400` with `"Unsupported provider"`.

### 13.9 Remove an integration

```bash
curl -s -X DELETE \
  -H "X-User-ID: $USER_A" \
  -H "X-Internal-Key: $INTERNAL_KEY" \
  $BASE/api/v1/integrations/github | jq
```

**Expected:** `{"status": "disconnected", "provider": "github"}`.

---

## Endpoint Reference

| # | Method | Path | Auth | Test |
|---|--------|------|------|------|
| 1 | `GET` | `/` | No | Test 1.1 |
| 2 | `GET` | `/health` | No | Test 1.2 |
| 3 | `POST` | `/api/v1/sessions` | X-User-ID + X-Internal-Key | Test 3 |
| 4 | `GET` | `/api/v1/sessions` | X-User-ID + X-Internal-Key | Test 4 |
| 5 | `DELETE` | `/api/v1/sessions/{id}` | X-User-ID + X-Internal-Key | Test 5 |
| 6 | `GET` | `/api/v1/chats` | X-User-ID + X-Internal-Key | Test 7.1 |
| 7 | `GET` | `/api/v1/chats/{id}` | X-User-ID + X-Internal-Key | Test 7.2 |
| 8 | `DELETE` | `/api/v1/chats/{id}` | X-User-ID + X-Internal-Key | Test 7.5 |
| 9 | `PATCH` | `/api/v1/chats/{id}` | X-User-ID + X-Internal-Key | Test 7.4 |
| 10 | `GET` | `/api/v1/files/list` | X-User-ID + X-Internal-Key | Test 8.1 |
| 11 | `GET` | `/api/v1/files/read` | X-User-ID + X-Internal-Key | Test 8.2 |
| 12 | `WS` | `/api/v1/ws` | JWT (`?token=`) | Test 6 |
| 13 | `POST` | `/api/v1/integrations` | X-User-ID + X-Internal-Key | Test 13.1 |
| 14 | `GET` | `/api/v1/integrations` | X-User-ID + X-Internal-Key | Test 13.4 |
| 15 | `DELETE` | `/api/v1/integrations/{provider}` | X-User-ID + X-Internal-Key | Test 13.9 |
| 16 | `GET` | `/api/v1/integrations/{provider}/repos` | X-User-ID + X-Internal-Key | Test 13.5 |
| 17 | `POST` | `/api/v1/integrations/{provider}/pr` | X-User-ID + X-Internal-Key | Test 13.7 |
| 18 | `POST` | `/api/agent/start` *(Next.js)* | Session cookie | — |
| 19 | `GET` | `/api/agent/token` *(Next.js)* | Session cookie | Test 12.1 |
| 20 | `GET` | `/api/chats` *(Next.js)* | Session cookie | Test 12.2 |
| 21 | `GET` | `/api/files/read` *(Next.js)* | Session cookie | Test 12.3 |
