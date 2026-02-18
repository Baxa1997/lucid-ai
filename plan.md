# Lucid AI — Roadmap

## Product Vision

A chat interface like Devin. User connects their GitHub or GitLab account, describes a task in chat, and the agent clones the repo in a Docker sandbox, writes code, runs tests, commits, pushes, and opens a PR — fully autonomously. The user watches it happen via chat and a read-only file explorer. No IDE, no manual editing.

**Core loop:**
```
User connects repo → describes task in chat
→ Agent clones repo in Docker sandbox
→ Agent writes code, runs tests, commits
→ Agent pushes branch and opens PR automatically
→ User gets PR link
```

---

## Current State

### ✅ What's working

| Area | Status |
|------|--------|
| Docker sandbox per session | ✅ Done |
| Git clone on session start | ✅ Done |
| WebSocket agent communication | ✅ Done |
| Chat message history (PostgreSQL) | ✅ Done |
| Read-only file explorer (live from agent) | ✅ Done |
| Terminal output panel | ✅ Done |
| Conversations list page | ✅ Done |
| Auth (login with email in dev, Google OAuth in prod) | ✅ Done |
| Per-user session isolation | ✅ Done |

### ❌ What's missing (blocking the core loop)

| Area | Status |
|------|--------|
| GitHub OAuth — connect account, store token | ❌ Not built |
| GitLab OAuth — connect account, store token | ❌ Not built |
| Auto PR creation after agent pushes | ❌ Not built |
| Session resume after browser refresh | ❌ Not built |
| Agent streaming (token-by-token output) | ❌ Not built |

### 🗑️ Removed from scope

These were planned but don't belong in a chat product:
- Monaco code editor — not an IDE
- File write/edit by user — agent does the editing
- Diff viewer — agent handles diffs
- Git status panel — not needed in chat UI
- Commit & push UI — agent does this automatically
- Organization/team logic — single-user for now, remove from codebase

---

## Phase 1 — Full Agent Cycle (Priority)

Get the complete loop working: connect repo → chat → agent codes → push → PR.

### Backend

| # | Task | Priority | Description |
|---|------|----------|-------------|
| B1 | **GitHub OAuth** | P0 | OAuth 2.0 flow: `GET /api/integrations/github/auth` → `GET /api/integrations/github/callback`. Store encrypted GitHub access token per user in DB. |
| B2 | **GitLab OAuth** | P0 | Same flow for GitLab. `GET /api/integrations/gitlab/auth` → callback. Same encrypted token storage. |
| B3 | **List user repos** | P0 | `GET /api/integrations/github/repos` and `/gitlab/repos` — returns the user's repos (name, url, private/public). Used in the "start session" flow to pick a repo. |
| B4 | **Auto PR creation** | P0 | After agent pushes a branch, backend calls GitHub/GitLab API to open a PR automatically. PR title and description auto-generated from agent's task. Returns PR URL. Triggered at end of agent session or on agent decision. |
| B5 | **Session resume** | P1 | Reconnect WebSocket to an existing session after browser refresh. Session state (messages, files) already in DB — just re-attach. Currently a refresh loses the session. |
| B6 | **Token-by-token streaming** | P1 | Stream agent output as it's generated, not in batches. User sees agent "thinking" in real-time like Devin. |
| B7 | **Remove org logic from DB** | P1 | Drop `Organization` and `Membership` tables from Prisma schema. Remove org creation from auth flow. Users are standalone — no teams needed yet. |

### Frontend

| # | Task | Priority | Description |
|---|------|----------|-------------|
| F1 | ✅ **Chat + file explorer + terminal** | P0 | Done. 3-pane layout: file explorer (left) + chat (center) + terminal (right). Read-only. |
| F2 | ✅ **Conversations list** | P0 | Done. Shows real chat history from backend. |
| F3 | **Connect GitHub/GitLab page** | P0 | Settings page: "Connect GitHub" and "Connect GitLab" buttons. Shows connected account name/avatar. Disconnect button. OAuth redirect flow. |
| F4 | **Repo picker in new session flow** | P0 | When starting a new session, user picks a repo from their connected GitHub/GitLab account (dropdown list from B3). Pre-fills repoUrl + gitToken. |
| F5 | **Stop agent button** | P1 | Button in chat to stop the agent mid-task. Calls `DELETE /api/v1/sessions/{id}`. |
| F6 | **PR link notification** | P1 | When agent creates a PR, show a banner in chat: "PR opened → [link]". Also update the conversation card in the list. |

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
Now:     B1 + B2 + B3        → GitHub + GitLab OAuth, repo listing
         F3 + F4              → Connect page + repo picker in new session flow
Next:    B4 + F6              → Auto PR creation + PR link in chat
Then:    B5 + B6 + F5         → Session resume + streaming + stop button
         B7                   → Remove org logic from DB/auth
Later:   Phase 2 (Notion)     → N1→N6, NF1→NF4
Last:    Phase 3 (Polish)      → Rate limiting, cost tracking, cleanup
```

After "Now + Next" you have the complete working cycle:
- User connects GitHub → picks repo → describes task in chat
- Agent clones repo, writes code, commits, pushes
- PR opened automatically — user gets the link

---

## Architecture (Target State)

```
┌─────────────────────────────────────────────────────────────────┐
│  GitHub / GitLab              Notion (Phase 2)                   │
│  OAuth · Repos · PRs          Databases · Tasks · Write-back    │
└────────────────┬──────────────────────────┬─────────────────────┘
                 │ OAuth + API              │ OAuth + API
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
│  PostgreSQL                                                      │
│  Users · GitTokens · ChatSessions · ChatMessages                │
└─────────────────────────────────────────────────────────────────┘
```
