# Lucid AI — Roadmap
PLAN

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

## Current State (Updated March 8, 2026)

### ✅ Phase 1 — COMPLETE

The full agent cycle works end-to-end from the browser:
1. User logs in with Google/GitHub/GitLab (Supabase Auth)
2. Connects GitHub/GitLab PAT on the Integrations page
3. Picks repo + branch from dropdown → starts session
4. Agent clones repo in Docker sandbox
5. User gives task in chat → agent works: thoughts, file edits, terminal commands stream as styled cards
6. Agent pushes changes → PR card appears in chat with "Create Pull Request" button
7. Tab switch / sidebar nav / page refresh reconnects to existing workspace

---

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
| Session reconnect after refresh | ✅ Working | `find_by_user_and_project()` reconnects to existing session |
| Session rate limiting | ✅ Working | Max 3 concurrent sessions per user (HTTP 429) |
| WebSocket keepalive | ✅ Working | `--ws-ping-interval 20 --ws-ping-timeout 60 --timeout-keep-alive 65` |
| Container cleanup reaper | ✅ Working | TTL=2h, reaper interval=2min |
| Token-by-token streaming | ⚠️ Not built | Events are batched (every 20 or every 2s); cosmetic—not needed for v1 |

---

### Frontend (Next.js — port 3000)

| Feature | Status | Notes |
|---------|--------|-------|
| Auth — Supabase OAuth (Google, GitHub, GitLab) | ✅ Working | Native Supabase Auth via `@supabase/ssr`. Login page has all 3 providers. |
| Auth — middleware (session refresh + route protection) | ✅ Working | `src/middleware.js` refreshes session cookies and redirects unauthenticated users |
| Auth — callback (PKCE code exchange) | ✅ Working | `src/app/auth/callback/route.js` handles OAuth redirect |
| Conversations list page | ✅ Working | Shows past chat sessions from DB |
| Git token settings (Integrations page) | ✅ Working | PAT input, validation, masked display, connect/disconnect for GitHub and GitLab |
| Repo picker in new session flow | ✅ Working | Dashboard dropdown with search, branch picker, model selector |
| Chat UI — send message, receive events | ✅ Working | WebSocket connected; structured message display |
| Inline agent events as structured cards | ✅ Working | ThinkingBlock, ToolCard, MessageBubble with markdown parsing |
| Read-only file explorer | ✅ Working | Live workspace tree from agent; updates on file changes |
| Terminal output panel | ✅ Working | Agent command output visible |
| Stop agent button | ✅ Working | `stopSession()` sends `stop` to backend + closes WS |
| PR link card in chat | ✅ Working | Styled card with branch info, diff stats, "Create PR" button |
| Auto-title conversations | ✅ Working | Uses Gemini to generate 2-5 word title from first user message |
| Chat history persistence | ✅ Working | Unified `chat_messages` table — survives refresh + reconnect |
| Loading skeleton | ✅ Working | Shows animated skeleton while history loads, no flash of empty state |
| Error boundary | ✅ Working | `WorkspaceErrorBoundary` catches JS errors, shows "Reload" button |
| Collapsible sidebar | ✅ Working | Icon-only mode with tooltips, persisted in localStorage |
| Reconnection handling | ✅ Working | No workspace destruction on tab switch or sidebar nav |
| Agent working spinner | ✅ Working | Shows during `connecting`, `preparing`, and `running` states |

---

### 🗑️ Removed from scope

- Monaco code editor as a separate pane — not an IDE; code is visible as inline diffs in chat
- File write/edit by user — agent does all editing
- Separate git status panel — agent commits/pushes autonomously
- Commit & push UI — agent does this automatically
- GitHub/GitLab OAuth flow for git tokens — replaced with PAT input (OpenHands style)
- Organization/team logic — single-user, removed from codebase and DB schema
- **NextAuth.js** — replaced with native Supabase Auth (Google, GitHub, GitLab OAuth)
- **Dev email/password login** — removed; all auth now goes through Supabase OAuth providers

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

| # | Task | Priority | Status | Description |
|---|------|----------|--------|-------------|
| P1 | **Rate limiting** | P2 | ✅ Done | Max 3 concurrent sessions per user. HTTP 429. |
| P2 | **Container auto-cleanup** | P2 | ✅ Done | TTL=2h, reaper every 2min. Explicit stop destroys immediately. |
| P3 | **Cost tracking** | P2 | ❌ Not built | Track LLM token usage per session. Expose via `/api/v1/usage`. |
| P4 | **Multi-model routing** | P3 | ⚠️ Partial | Model selector exists in UI. Backend reads `modelProvider`. |
| P5 | **Redis session store** | P3 | ❌ Not built | Replace in-memory store for multi-instance deployment. |
| P6 | **GitHub webhook trigger** | P3 | ❌ Not built | Auto-trigger agent when a new issue is opened. |

---

## Architecture

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
│ │                 │  PR link card      │                      │ │
│ └─────────────────┴────────────────────┴──────────────────────┘ │
│          ↕ REST + WebSocket                                      │
├─────────────────────────────────────────────────────────────────┤
│  Next.js (Frontend)                                              │
│  Auth · API Routes · Supabase Client                             │
│          ↕ HTTP                                                  │
├─────────────────────────────────────────────────────────────────┤
│  FastAPI (ai_engine)                                             │
│  Sessions · WebSocket · Chat · Files · Git · GitHub/GitLab API  │
│  Rate Limiting (3 sessions/user) · Reaper (TTL 2h)              │
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
│  conversations · messages (legacy)                               │
│  Frontend → Supabase Client (RLS)                               │
│  ai_engine → supabase-py (SUPABASE_URL + SUPABASE_SERVICE_KEY)  │
└─────────────────────────────────────────────────────────────────┘
```

---

## Stability Fixes Applied (March 2026)

| Fix | Description |
|-----|------------|
| Tab switch no longer destroys workspace | Removed `visibilitychange` reconnect handler. Hardened `agentWSManager.connect()` to block duplicate connections. |
| Sidebar nav no longer resets workspace | Fixed `projectId` to consistently use `conversationId` (URL UUID) instead of async-loaded repo name. |
| Backend reconnect is clean | Skips duplicate DB records, skips duplicate "ready" messages, looks up existing `chat_session_id` on reconnect. |
| WebSocket keepalive under proxies | Configured uvicorn with `--ws-ping-interval`, `--ws-ping-timeout`, `--timeout-keep-alive` in both Dockerfile and main.py. |
| Unified message persistence | Both frontend and backend read/write from `chat_messages` table. Legacy `messages` table supported for backward compat. |
| Error recovery | `WorkspaceErrorBoundary` catches JS errors and shows styled "Reload Workspace" UI. |
