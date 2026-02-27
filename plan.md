# Lucid AI — Roadmap

## Product Vision

A chat interface like OpenHands/Devin — not an IDE. Everything happens in the chat. The user describes a task, and the agent's actions stream into the chat as structured events: thoughts, terminal commands and their output, file edits shown as inline diffs. The user watches the agent work in real-time, can send follow-up messages, and gets a PR link at the end.

**There is no code editor pane.** Code changes are visible as diff blocks inside the chat stream, exactly like OpenHands.

**Core loop:**
```
User connects GitHub/GitLab → describes task in chat
→ Agent clones repo in Docker sandbox
→ Agent actions stream into chat:
    💭 Thought: "I'll look at the auth module first..."
    $ git clone ... / npm install ... (+ output)
    📝 Edit: src/auth.js  (+12 / -3 lines, shown as diff)
    $ npm test (+ output)
    $ git commit -m "Fix auth bug" && git push
→ PR opened automatically
→ PR link appears in chat
```

---

## Current State — Full Picture

### Backend API (ai_engine — port 8000)

Every endpoint is behind `X-User-ID` + `X-Internal-Key` headers (or a JWT for WebSocket). The frontend proxy (`gatekeeper.js`) adds these automatically.

| Endpoint | Status | Notes |
|----------|--------|-------|
| `GET /` | ✅ Working | System health — Docker status, SDK status, active sessions |
| `GET /health` | ✅ Working | Minimal `{"status":"ok"}` for load balancers |
| `POST /api/v1/sessions` | ✅ Working | Create Docker-sandboxed agent session; returns sessionId |
| `GET /api/v1/sessions` | ✅ Working | List active in-memory sessions for caller |
| `DELETE /api/v1/sessions/{id}` | ✅ Working | Stop + clean up session and Docker container |
| `WS /api/v1/ws` | ✅ Working | Real-time agent communication; JWT auth via `?token=` |
| `GET /api/v1/chats` | ✅ Working | Paginated chat session list from Supabase |
| `GET /api/v1/chats/{id}` | ✅ Working | Full chat with all messages |
| `DELETE /api/v1/chats/{id}` | ✅ Working | Delete chat + cascade messages |
| `PATCH /api/v1/chats/{id}` | ✅ Working | Rename chat |
| `GET /api/v1/files/list` | ✅ Working | Live workspace file tree (Docker or local) |
| `GET /api/v1/files/read` | ✅ Working | Read a file from the agent's workspace |
| `POST /api/v1/integrations` | ✅ Working | Save/update GitHub or GitLab PAT — validates against API, encrypts with AES-256-CBC |
| `GET /api/v1/integrations` | ✅ Working | List connected providers; tokens never returned |
| `DELETE /api/v1/integrations/{provider}` | ✅ Working | Disconnect GitHub or GitLab |
| `GET /api/v1/integrations/{provider}/repos` | ✅ Working | List repos via stored PAT (paginated, all pages) |
| `POST /api/v1/integrations/{provider}/pr` | ✅ Working | Open GitHub PR or GitLab MR using stored PAT |
| Session resume after refresh | ❌ Not built | Sessions are in-memory; browser refresh = session lost |
| Token-by-token streaming | ❌ Not built | Events are batched (every 20 or every 2 s); no SSE/chunked streaming |

**What the backend can do right now:** Start an agent, clone a repo, run code in Docker, stream events, save/query chat history, manage git tokens, list repos, create PRs. The core loop is complete at the API level.

**What the backend cannot do yet:** Resume a session after the connection drops, or stream tokens as they are generated (agent outputs arrive in chunks, not word-by-word).

---

### Frontend (Next.js — port 3000)

| Feature | Status | Notes |
|---------|--------|-------|
| Auth — dev email login | ✅ Working | Any email creates a user automatically (dev credentials provider) |
| Auth — Google OAuth | ⚠️ Config needed | Works once `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` are set in `.env` |
| Conversations list page | ✅ Working | Shows past chat sessions from DB |
| Chat UI — send message, receive events | ✅ Working | WebSocket connected; basic message display |
| Read-only file explorer | ✅ Working | Live workspace tree from agent; updates on file changes |
| Terminal output panel | ✅ Working | Agent command output visible |
| Git token settings page | ❌ Not built | No UI to paste a GitHub/GitLab PAT — must call API directly |
| Repo picker in new session flow | ❌ Not built | User cannot pick a repo from a dropdown; must type the URL manually or hardcode it |
| Inline agent events as structured cards | ❌ Not built | Agent events (thoughts, commands, diffs) arrive via WebSocket but are likely displayed as raw/flat text, not as styled thought bubbles / command blocks / diff cards |
| Stop agent button | ❌ Not built | No button to interrupt the agent mid-task |
| PR link card in chat | ❌ Not built | No card appears when agent creates a PR |
| Notion connect / task picker | ❌ Not built | Phase 2 — not started |

---

### What you can do end-to-end right now

✅ **Fully testable via curl / wscat (no frontend needed):**
- Connect a GitHub/GitLab PAT → list your repos → start an agent session on a repo → watch mock events stream → create a PR

⚠️ **Partially works in the browser:**
- Log in, view past conversations, see the chat UI and file explorer
- Cannot connect GitHub/GitLab from the UI (no settings page)
- Cannot pick a repo from a dropdown (no repo picker)
- Events appear in chat but without structured formatting

❌ **Core loop is broken from the browser because:**
1. There is no settings page to paste a PAT — users have no way to connect GitHub/GitLab from the UI
2. New session flow has no repo picker — user can't select which repo to work on
3. Agent events in chat show as flat messages, not as styled thought/command/diff cards

**In short:** The backend is feature-complete for Phase 1. The frontend is missing the three pieces that expose those backend features to a real user: token settings (F3), repo picker (F4), and structured event rendering (F5).

---

### 🗑️ Removed from scope

These were planned but don't belong in a chat product:
- Monaco code editor as a separate pane — not an IDE; code is visible as inline diffs in chat
- File write/edit by user — agent does all editing
- Separate git status panel — agent commits/pushes autonomously
- Commit & push UI — agent does this automatically
- GitHub/GitLab OAuth flow — replaced with PAT input (OpenHands style)
- Organization/team logic — single-user, removed from codebase and DB schema

---

## Phase 1 — Full Agent Cycle (Priority)

Get the complete loop working: connect repo → chat → agent codes → push → PR.

### How GitHub/GitLab connection works (OpenHands style)

No OAuth. User pastes a **Personal Access Token (PAT)** directly — copied from GitHub/GitLab → Settings → Developer tokens. Token is encrypted (AES-256-CBC) and stored in the `integrations` table. This is the same approach OpenHands uses.

**GitHub PAT scopes needed:** `repo`, `workflow`
**GitLab PAT scopes needed:** `api`, `read_repository`, `write_repository`

### Backend

| # | Task | Priority | Description |
|---|------|----------|-------------|
| B1 | ✅ **Save/update git token** | P0 | `POST /api/v1/integrations` — validates PAT against provider API, encrypts with AES-256-CBC (matching frontend key), upserts into `integrations` table. `GET` lists, `DELETE /{provider}` removes. |
| B2 | ✅ **List user repos** | P0 | `GET /api/v1/integrations/{provider}/repos` — decrypts stored token, calls GitHub/GitLab API, returns repos list. Self-hosted GitLab URL encoded in scopes field. |
| B3 | ✅ **Auto PR creation** | P0 | `POST /api/v1/integrations/{provider}/pr` — opens GitHub PR or GitLab MR using stored PAT. Frontend calls this after agent pushes a branch. Returns PR URL. |
| B4 | **Session resume** | P1 | Reconnect WebSocket to an existing session after browser refresh. Session state (messages, files) already in DB — just re-attach. Currently a refresh loses the session. |
| B5 | **Token-by-token streaming** | P1 | Stream agent output as it's generated, not in batches. User sees agent "thinking" in real-time. |
| B6 | **Remove org tables from DB** | P1 | Run `prisma db push` against Supabase to drop `organizations` and `memberships` tables (schema already updated). |

### Frontend

| # | Task | Priority | Description |
|---|------|----------|-------------|
| F1 | ✅ **Chat + file explorer + terminal** | P0 | Done. |
| F2 | ✅ **Conversations list** | P0 | Done. |
| F3 | **Git token settings page** | P0 | Settings page with two fields: "GitHub Token" and "GitLab Token". User pastes PAT, hits Save. Shows masked token + connected username if valid. Clear button to remove. Same UX as OpenHands settings. |
| F4 | **Repo picker in new session flow** | P0 | When starting a new session, user types or picks a repo URL. If a token is saved, repos are listed in a dropdown (from B2). Pre-fills repoUrl + passes token to agent. |
| F5 | **Inline agent events in chat** | P0 | Render agent WebSocket events as structured chat messages: thought bubbles, command blocks with output, file edit diffs (unified diff format), status updates. This is the core of the OpenHands-like UX. |
| F6 | **Stop agent button** | P1 | Button in chat to stop the agent mid-task. |
| F7 | **PR link in chat** | P1 | When agent creates a PR, a card appears in chat with the PR link, branch name, and title. |

---

## Phase 2 — Notion Integration

After Phase 1 is complete and the full cycle works reliably.

User connects Notion, picks a task from a database, Lucid runs the agent, pushes code, opens a PR, and writes the PR link back to the Notion task.

### Backend

| # | Task | Priority | Description |
|---|------|----------|-------------|
| N1 | **Notion OAuth** | P0 | OAuth 2.0 flow to connect Notion workspace. Store encrypted Notion access token per user in DB. |
| N2 | **List Notion databases** | P0 | `GET /api/notion/databases` — returns databases the user has access to. |
| N3 | **List Notion tasks** | P0 | `GET /api/notion/tasks?database_id=X` — fetches pages with title, status, assignee. |
| N4 | **Get Notion task detail** | P0 | `GET /api/notion/tasks/{page_id}` — full page content used as the agent's task prompt. |
| N5 | **Write back to Notion** | P1 | Update task status to "In Progress" when agent starts. Add PR URL and set status to "In Review" when done. |
| N6 | **Store Notion config** | P1 | Save database ID and field mappings (status field, PR field) per user so they don't reconfigure each time. |

### Frontend

| # | Task | Priority | Description |
|---|------|----------|-------------|
| NF1 | **Notion connect page** | P0 | Settings → "Connect Notion". OAuth flow. Shows connected workspace on success. |
| NF2 | **Task picker** | P0 | "Start from Notion" → pick database → pick task → auto-fills agent prompt and starts session. |
| NF3 | **Notion task card in workspace** | P1 | Small card showing the linked Notion task title, status, and link — visible during the session. |
| NF4 | **Auto-update banner** | P1 | After PR is created, show "Notion updated — PR link added" with link to Notion page. |

### Notion flow

```
User → Settings → Connect Notion (OAuth)
     → "New Session" → "From Notion" → picks database → picks task
     → Agent starts, Notion task status → "In Progress"
     → Agent clones repo, writes code, pushes branch
     → PR created automatically via GitHub/GitLab API
     → Notion task status → "In Review", PR URL added
     → User sees PR link + Notion task link in chat
```

---

## Phase 3 — Polish & Production

| # | Task | Priority | Description |
|---|------|----------|-------------|
| P1 | **Rate limiting** | P2 | Limit sessions per user, requests per minute. |
| P2 | **Container auto-cleanup** | P2 | Destroy idle containers after 30 min. |
| P3 | **Cost tracking** | P2 | Track LLM token usage per session. Expose via `/api/v1/usage`. |
| P4 | **Multi-model routing** | P3 | Choose model per session or let agent pick based on task complexity. |
| P5 | **Redis session store** | P3 | Replace in-memory store for multi-instance deployment. |
| P6 | **GitHub webhook trigger** | P3 | Auto-trigger agent when a new issue is opened or a comment asks for it. |

---

## Build Order

```
Done:    B1 + B2 + B3        ✅ Save GitHub/GitLab PAT + repo listing + auto PR
Now:     F3 + F4              → Token settings page + repo picker in new session flow
Then:    B4 + B5 + F5 + F6   → Session resume + streaming + inline events + stop button
         B6                   → Run prisma db push (drop org tables)
         F7                   → PR link card in chat
Later:   Phase 2 (Notion)     → N1→N6, NF1→NF4
Last:    Phase 3 (Polish)      → Rate limiting, cost tracking, cleanup
```

After "Now" (F3 + F4) you have the complete working cycle:
- User connects GitHub → picks repo → describes task in chat
- Agent clones repo, writes code, commits, pushes
- PR opened automatically — user gets the link

---

## Architecture (Target State)

```
┌─────────────────────────────────────────────────────────────────┐
│  GitHub / GitLab              Notion (Phase 2)                   │
│  PAT · Repos · PRs            Databases · Tasks · Write-back    │
└────────────────┬──────────────────────────┬─────────────────────┘
                 │ PAT + API                │ OAuth + API
┌────────────────▼──────────────────────────▼─────────────────────┐
│  Browser                                                         │
│ ┌─────────────────┬────────────────────┬──────────────────────┐ │
│ │ File Explorer   │  Chat              │  Terminal            │ │
│ │ (read-only,     │  ─────────────     │  ────────────────    │ │
│ │  live from      │  Agent messages    │  Agent commands      │ │
│ │  agent)         │  User input        │  & output            │ │
│ │                 │  PR link banner    │                      │ │
│ └─────────────────┴────────────────────┴──────────────────────┘ │
│          ↕ REST + WebSocket                                      │
├─────────────────────────────────────────────────────────────────┤
│  Next.js (Frontend)                                              │
│  Auth · API Routes · Prisma                                      │
│          ↕ HTTP                                                  │
├─────────────────────────────────────────────────────────────────┤
│  FastAPI (ai_engine)                                             │
│  Sessions · WebSocket · Chat · Files · Git · GitHub/GitLab API  │
│          ↕ Docker SDK                                            │
├─────────────────────────────────────────────────────────────────┤
│  Docker Containers (per session)                                 │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐            │
│  │ Session A    │  │ Session B    │  │ Session C    │            │
│  │ /workspace   │  │ /workspace   │  │ /workspace   │            │
│  │ git, node,   │  │ git, python, │  │ git, go,     │            │
│  │ npm, tests   │  │ pip, pytest  │  │ make, tests  │            │
│  └─────────────┘  └─────────────┘  └─────────────┘            │
├─────────────────────────────────────────────────────────────────┤
│  Supabase (hosted PostgreSQL)                                    │
│  users · integrations · chat_sessions · chat_messages           │
│  Frontend → Prisma (DATABASE_URL)                               │
│  ai_engine → supabase-py (SUPABASE_URL + SUPABASE_SERVICE_KEY)  │
└─────────────────────────────────────────────────────────────────┘
```
