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

Use these throughout all tests. Copy-paste this block into your terminal:

```bash
BASE=http://localhost:8000
USER_A="test-user-alice"
USER_B="test-user-bob"
```

**Dev mode (no INTERNAL_API_KEY):** Use `X-User-ID` header directly.

**Production mode (INTERNAL_API_KEY set):** Add the internal key header:

```bash
INTERNAL_KEY="lucid_internal_k8s_2f9a7e3c1d4b6e8a0f2c5d7e9b1a3c5d"
# Then add to every curl: -H "X-Internal-Key: $INTERNAL_KEY"
```

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

### 2.2 X-User-ID with valid internal key → 200

```bash
curl -s -H "X-User-ID: $USER_A" \
  $BASE/api/v1/sessions | jq
```

**Expected:** `HTTP 200` with `{"sessions": []}` (empty list).

In dev mode (no INTERNAL_API_KEY), this works but the server logs a warning.

### 2.3 X-User-ID with wrong internal key → 401 (production only)

Only test this if `INTERNAL_API_KEY` is set:

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
  -d '{"task": "Build a hello world app"}' | jq -r '.sessionId')
echo "Session A: $SESSION_A"
```

### 3.2 Create session for User B

```bash
SESSION_B=$(curl -s -X POST $BASE/api/v1/sessions \
  -H "Content-Type: application/json" \
  -H "X-User-ID: $USER_B" \
  -d '{"task": "Fix the login bug"}' | jq -r '.sessionId')
echo "Session B: $SESSION_B"
```

### 3.3 Create session with git repo

```bash
curl -s -X POST $BASE/api/v1/sessions \
  -H "Content-Type: application/json" \
  -H "X-User-ID: $USER_A" \
  -d '{
    "task": "Fix the authentication bug in login.js",
    "repoUrl": "https://github.com/your-user/your-repo",
    "gitToken": "ghp_your_github_token",
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
curl -s -H "X-User-ID: $USER_A" \
  $BASE/api/v1/sessions | jq
```

**Expected:** Only sessions created by User A.

### 4.2 User B sees only their sessions

```bash
curl -s -H "X-User-ID: $USER_B" \
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
curl -s -X DELETE -H "X-User-ID: $USER_A" \
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
  -X DELETE -H "X-User-ID: $USER_A" \
  $BASE/api/v1/sessions/$SESSION_B
```

**Expected:** `HTTP 403` with `"Not authorized to stop this session."`.

### 5.3 Stop non-existent session → 404

```bash
curl -s -w "\nHTTP %{http_code}\n" \
  -X DELETE -H "X-User-ID: $USER_A" \
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

First, generate a test JWT (requires Node.js):

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

Now connect with the token:

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

You can then send follow-ups:
```json
{"type": "message", "content": "Now add unit tests"}
```

Or stop:
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

**Expected:** Same behavior as 6.2 — authenticated via handshake token.

---

## Test 7 — Chat History

Chat sessions are automatically created when a WebSocket session starts (from Test 6). Now query them.

### 7.1 List chats for User A

```bash
curl -s -H "X-User-ID: $USER_A" \
  "$BASE/api/v1/chats?limit=10&offset=0" | jq
```

**Expected:**
```json
{
  "chats": [
    {
      "id": "uuid-...",
      "agentSessionId": "...",
      "projectId": "test-project",
      "title": "Create a hello world Python script",
      "modelProvider": "anthropic",
      "isActive": false,
      "createdAt": "...",
      "updatedAt": "..."
    }
  ]
}
```

Save the chat ID:
```bash
CHAT_ID=$(curl -s -H "X-User-ID: $USER_A" \
  "$BASE/api/v1/chats" | jq -r '.chats[0].id')
echo "Chat ID: $CHAT_ID"
```

### 7.2 Get chat with messages

```bash
curl -s -H "X-User-ID: $USER_A" \
  $BASE/api/v1/chats/$CHAT_ID | jq
```

**Expected:** Chat object with a `messages` array containing the initial task and agent responses.

### 7.3 User B cannot see User A's chats

```bash
curl -s -w "\nHTTP %{http_code}\n" \
  -H "X-User-ID: $USER_B" \
  $BASE/api/v1/chats/$CHAT_ID
```

**Expected:** `HTTP 404` — User B's request is scoped to their own chats.

### 7.4 Rename a chat

```bash
curl -s -X PATCH $BASE/api/v1/chats/$CHAT_ID \
  -H "Content-Type: application/json" \
  -H "X-User-ID: $USER_A" \
  -d '{"title": "My Hello World Project"}' | jq
```

**Expected:**
```json
{ "status": "updated", "id": "...", "title": "My Hello World Project" }
```

### 7.5 Delete a chat

```bash
curl -s -X DELETE -H "X-User-ID: $USER_A" \
  $BASE/api/v1/chats/$CHAT_ID | jq
```

**Expected:**
```json
{ "status": "deleted", "id": "..." }
```

Verify it's gone:
```bash
curl -s -w "\nHTTP %{http_code}\n" \
  -H "X-User-ID: $USER_A" \
  $BASE/api/v1/chats/$CHAT_ID
```

**Expected:** `HTTP 404`.

---

## Test 8 — File Explorer (requires active session)

These endpoints work on sessions that are still alive (not stopped). Create a fresh session first:

```bash
SESSION_FILES=$(curl -s -X POST $BASE/api/v1/sessions \
  -H "Content-Type: application/json" \
  -H "X-User-ID: $USER_A" \
  -d '{"task": "Test file explorer"}' | jq -r '.sessionId')
echo "Session: $SESSION_FILES"
```

### 8.1 List files in workspace

```bash
curl -s -H "X-User-ID: $USER_A" \
  "$BASE/api/v1/files/list?session_id=$SESSION_FILES" | jq
```

**Expected:** A `tree` array showing the workspace file structure. May be empty for a fresh mock session or populated if Docker created a workspace.

### 8.2 Read a file

```bash
curl -s -H "X-User-ID: $USER_A" \
  "$BASE/api/v1/files/read?session_id=$SESSION_FILES&path=/workspace/hello.py" | jq
```

**Expected:** `{"content": "..."}` if file exists, or `HTTP 404` if not.

### 8.3 Path traversal blocked

```bash
curl -s -w "\nHTTP %{http_code}\n" \
  -H "X-User-ID: $USER_A" \
  "$BASE/api/v1/files/read?session_id=$SESSION_FILES&path=../../etc/passwd"
```

**Expected:** `HTTP 400` with `"Path traversal not allowed."`.

### 8.4 User B cannot access User A's files

```bash
curl -s -w "\nHTTP %{http_code}\n" \
  -H "X-User-ID: $USER_B" \
  "$BASE/api/v1/files/list?session_id=$SESSION_FILES"
```

**Expected:** `HTTP 403` with `"Not authorized to access this session."`.

---

## Test 9 — Docker Sandbox Verification

Check if Docker sandboxing is working (requires Docker daemon accessible).

### 9.1 Verify Docker is detected

```bash
curl -s $BASE/ | jq '.docker_available'
```

**Expected:** `true`.

### 9.2 Create session and check sandbox count

```bash
# Before
curl -s $BASE/ | jq '.active_sandboxes'

# Create session with SDK available (or check Docker containers manually)
docker ps --filter "label=lucid.managed=true"
```

**Expected:** Each active session with Docker should have a corresponding container.

### 9.3 Verify container isolation

```bash
# List all Lucid sandbox containers
docker ps --filter "label=lucid.managed=true" --format "table {{.Names}}\t{{.Status}}\t{{.Labels}}"
```

Each container should have a unique `lucid.session_id` label.

### 9.4 Stop session and verify cleanup

```bash
curl -s -X DELETE -H "X-User-ID: $USER_A" \
  $BASE/api/v1/sessions/$SESSION_FILES | jq

# Check containers are gone
docker ps --filter "label=lucid.managed=true"
```

**Expected:** The container for the stopped session is removed.

---

## Test 10 — Multi-User Isolation (Full Flow)

This is the end-to-end test that verifies multiple users can work simultaneously without seeing each other's data.

### Step 1: Create sessions for both users

```bash
# User A
SESSION_A2=$(curl -s -X POST $BASE/api/v1/sessions \
  -H "Content-Type: application/json" \
  -H "X-User-ID: $USER_A" \
  -d '{"task": "Fix bug in auth module"}' | jq -r '.sessionId')

# User B
SESSION_B2=$(curl -s -X POST $BASE/api/v1/sessions \
  -H "Content-Type: application/json" \
  -H "X-User-ID: $USER_B" \
  -d '{"task": "Add dark mode support"}' | jq -r '.sessionId')

echo "A: $SESSION_A2"
echo "B: $SESSION_B2"
```

### Step 2: Verify each user only sees their own

```bash
echo "=== User A sessions ==="
curl -s -H "X-User-ID: $USER_A" $BASE/api/v1/sessions | jq '.sessions[].task'

echo "=== User B sessions ==="
curl -s -H "X-User-ID: $USER_B" $BASE/api/v1/sessions | jq '.sessions[].task'
```

**Expected:**
- User A sees `"Fix bug in auth module"` only
- User B sees `"Add dark mode support"` only

### Step 3: Cross-access blocked

```bash
# User A tries to stop User B's session
curl -s -w "\nHTTP %{http_code}\n" \
  -X DELETE -H "X-User-ID: $USER_A" \
  $BASE/api/v1/sessions/$SESSION_B2

# User B tries to read User A's files
curl -s -w "\nHTTP %{http_code}\n" \
  -H "X-User-ID: $USER_B" \
  "$BASE/api/v1/files/list?session_id=$SESSION_A2"
```

**Expected:** Both return `403 Forbidden`.

### Step 4: Clean up

```bash
curl -s -X DELETE -H "X-User-ID: $USER_A" $BASE/api/v1/sessions/$SESSION_A2 | jq
curl -s -X DELETE -H "X-User-ID: $USER_B" $BASE/api/v1/sessions/$SESSION_B2 | jq
```

---

## Test 11 — Error Cases

### 11.1 Missing required field (task)

```bash
curl -s -w "\nHTTP %{http_code}\n" \
  -X POST $BASE/api/v1/sessions \
  -H "Content-Type: application/json" \
  -H "X-User-ID: $USER_A" \
  -d '{}'
```

**Expected:** `HTTP 422` — validation error for missing `task` field.

### 11.2 Invalid model provider

```bash
curl -s -w "\nHTTP %{http_code}\n" \
  -X POST $BASE/api/v1/sessions \
  -H "Content-Type: application/json" \
  -H "X-User-ID: $USER_A" \
  -d '{"task": "test", "model_provider": "openai"}'
```

**Expected:** `HTTP 400` or `HTTP 500` (depends on whether SDK is available — in mock mode this may succeed since LLM is not actually called).

### 11.3 Chat pagination

```bash
# Page 1
curl -s -H "X-User-ID: $USER_A" "$BASE/api/v1/chats?limit=2&offset=0" | jq '.chats | length'

# Page 2
curl -s -H "X-User-ID: $USER_A" "$BASE/api/v1/chats?limit=2&offset=2" | jq '.chats | length'
```

### 11.4 Rename with missing title

```bash
curl -s -w "\nHTTP %{http_code}\n" \
  -X PATCH $BASE/api/v1/chats/some-chat-id \
  -H "Content-Type: application/json" \
  -H "X-User-ID: $USER_A" \
  -d '{}'
```

**Expected:** `HTTP 400` with `"Missing 'title'"`.

---

## Endpoint Reference

| # | Method | Path | Auth | Test |
|---|--------|------|------|------|
| 1 | `GET` | `/` | No | Test 1.1 |
| 2 | `GET` | `/health` | No | Test 1.2 |
| 3 | `POST` | `/api/v1/sessions` | Yes | Test 3 |
| 4 | `GET` | `/api/v1/sessions` | Yes | Test 4 |
| 5 | `DELETE` | `/api/v1/sessions/{id}` | Yes | Test 5 |
| 6 | `GET` | `/api/v1/chats` | Yes | Test 7.1 |
| 7 | `GET` | `/api/v1/chats/{id}` | Yes | Test 7.2 |
| 8 | `DELETE` | `/api/v1/chats/{id}` | Yes | Test 7.5 |
| 9 | `PATCH` | `/api/v1/chats/{id}` | Yes | Test 7.4 |
| 10 | `GET` | `/api/v1/files/list` | Yes | Test 8.1 |
| 11 | `GET` | `/api/v1/files/read` | Yes | Test 8.2 |
| 12 | `WS` | `/api/v1/ws` | Yes | Test 6 |
