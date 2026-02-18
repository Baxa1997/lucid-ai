# Lucid AI — Roadmap to Devin-Like Product

## Current State

**Backend (ai_engine):** Functional. Per-session Docker sandboxing, git clone/push, WebSocket streaming, chat history, auth, file tree API — all implemented.

**Frontend:** Workspace now has 3-pane layout (FileExplorer + Chat + Terminal/FileViewer). Conversations page shows real chat history from the backend. WebSocket token auth fixed. File content viewer works (read files directly from agent workspace).

**What makes Devin special:** User points at a bug → agent clones repo in a sandbox → reads code, makes changes, runs tests → user sees the file changes in real-time → agent pushes fix and creates a PR. The user can intervene at any point, view files, edit code, and guide the agent.

---

## Phase 1 — Core IDE Experience (Must Have)

### Backend

| # | Task | Priority | Description |
|---|------|----------|-------------|
| B1 | **File write/edit endpoint** | P0 | `POST /api/v1/files/write` — let users edit files in the agent's workspace (Docker exec or local fs). Enables the user to intervene and fix code while agent works. |
| B2 | **Git status endpoint** | P0 | `GET /api/v1/git/status?session_id=X` — returns current branch, changed files, staged/unstaged diff. Powers the git panel in the UI. |
| B3 | **Git operations endpoints** | P0 | `POST /api/v1/git/commit`, `POST /api/v1/git/push`, `POST /api/v1/git/create-branch`, `POST /api/v1/git/checkout` — all execute inside the session's Docker container. |
| B4 | **Create PR endpoint** | P1 | `POST /api/v1/git/pull-request` — uses the git token to call GitHub/GitLab API from the backend. Returns PR URL. |
| B5 | **Stream agent output token-by-token** | P1 | Currently events are batched. Add partial content streaming so the user sees the agent "thinking" in real-time (like Devin's streaming). |
| B6 | **Session resume/reconnect** | P1 | Allow reconnecting to an existing session's WebSocket. Currently if the browser refreshes, the session is lost. Store session state so user can resume. |
| B7 | **File diff endpoint** | P2 | `GET /api/v1/git/diff?session_id=X&path=file.py` — returns unified diff for a specific file. Powers the diff viewer. |

### Frontend

| # | Task | Priority | Description |
|---|------|----------|-------------|
| F1 | ✅ **Integrate FileExplorer into workspace** | P0 | FileExplorer integrated into workspace as collapsible left sidebar (240px), shows live file tree from WebSocket file_tree events. |
| F2 | **Code viewer/editor with Monaco** | P0 | When user clicks a file in the explorer, open it in a Monaco Editor pane (read from `/api/v1/files/read`). Syntax highlighting, line numbers, minimap. |
| F3 | ✅ **Three-pane workspace layout** | P0 | Three-pane workspace layout is done: [FileExplorer 240px] | [Chat center] | [Terminal OR FileViewer right panel 400px, toggled by buttons]. |
| F3b | ✅ **Conversations page wired to real API** | P0 | Conversations page was using mock data; now fetches live chat history from `/api/chats`. |
| F4 | **Real terminal with xterm.js** | P1 | Replace the textarea terminal with xterm.js (already in dependencies). Proper terminal emulation, colors, scrollback. Connect to agent terminal output. |
| F5 | **Git status panel** | P1 | Show current branch, changed files list, staged/unstaged status below the file explorer. Click a changed file → opens diff view. |
| F6 | **Diff viewer** | P1 | Monaco diff editor showing before/after for changed files. User can see exactly what the agent modified. |
| F7 | **File tabs** | P2 | Open multiple files in tabs above the editor. Close, reorder, dirty indicator (unsaved changes). |
| F8 | **Save file from editor** | P2 | Ctrl+S in Monaco → calls `POST /api/v1/files/write` to save changes to the agent's workspace. User can manually fix code. |

---

## Phase 2 — Git Workflow & Agent Control

### Backend

| # | Task | Priority | Description |
|---|------|----------|-------------|
| B8 | **Agent task queue** | P1 | Allow queuing multiple tasks for the agent. User sends "fix this bug, then add tests, then push" — agent works through them sequentially. |
| B9 | **Agent pause/resume** | P1 | Let user pause the agent mid-task, make manual edits, then resume. Requires conversation state management. |
| B10 | **Test execution endpoint** | P2 | `POST /api/v1/exec/run-tests?session_id=X` — runs test suite in the container, streams output. Agent can also trigger this automatically. |
| B11 | **Sandbox image management** | P2 | Support custom Docker images per project (Node.js, Python, Go, Rust). Store image preference in project config. |
| B12 | **Cost tracking** | P2 | Track LLM token usage per session/user. Store in DB. Expose via `GET /api/v1/usage`. |

### Frontend

| # | Task | Priority | Description |
|---|------|----------|-------------|
| F9 | **Commit & push UI** | P1 | Button: "Commit & Push" → shows changed files, commit message input, branch selection. Calls git endpoints. |
| F10 | **Create PR button** | P1 | After push, show "Create Pull Request" button → opens form with title, description (auto-generated from agent's changes), base branch. |
| F11 | **Agent control bar** | P1 | Pause/resume/stop buttons. Task progress indicator. Current step label ("Cloning repo...", "Reading files...", "Writing fix..."). |
| F12 | **Session history** | P2 | List past sessions from `/api/v1/chats`. Click to view full conversation, files changed, and outcome. Resume if session is still alive. |
| F13 | **Notification system** | P2 | Toast notifications for: agent completed, agent error, PR created, push successful. |

---

## Phase 3 — Polish & Production

### Backend

| # | Task | Priority | Description |
|---|------|----------|-------------|
| B13 | **Rate limiting** | P2 | Limit sessions per user, API calls per minute. Prevent abuse. |
| B14 | **Container timeout & auto-cleanup** | P2 | Containers that idle for >30min are auto-destroyed. Background task checks periodically. |
| B15 | **Webhook support** | P3 | GitHub webhooks → auto-trigger agent on new issues, PR comments, CI failures. |
| B16 | **Multi-model routing** | P3 | Let agent use different models for different tasks (fast model for code search, powerful model for complex reasoning). |
| B17 | **Redis session store** | P3 | Replace in-memory SessionStore with Redis for multi-instance deployment. |

### Frontend

| # | Task | Priority | Description |
|---|------|----------|-------------|
| F14 | **Keyboard shortcuts** | P2 | Cmd+K command palette, Cmd+P file finder, Cmd+Shift+P actions. |
| F15 | **Dark/light theme** | P2 | Theme toggle. Monaco + chat + terminal all switch together. |
| F16 | **Responsive layout** | P3 | Mobile-friendly views (stack panes vertically on small screens). |
| F17 | **Onboarding flow** | P3 | First-time user guide: connect GitHub → create project → launch first agent session. |
| F18 | **Team collaboration** | P3 | Share sessions with teammates. Multiple users watch/interact with same agent. |

---

## Suggested Build Order

This is the critical path — each step unlocks visible progress:

```
Week 1:  ✅ F1 + F3 + F3b     → File explorer + 3-pane layout + real conversations API (DONE)
          B1                   → File write API (remaining from Week 1)
Week 2:  F2 + F4              → Monaco editor + xterm terminal
Week 3:  B1 + B2 + B3 + F5   → File write API + git endpoints + git status panel
Week 4:  F6 + F9 + B4         → Diff viewer + commit/push UI + PR endpoint
Week 5:  B5 + B6 + F11        → Streaming + session resume + agent controls
Week 6:  F10 + F12 + B8       → Create PR UI + session history + task queue
Week 7:  B10 + B12 + F13      → Test runner + cost tracking + notifications
Week 8:  Phase 3 polish        → Rate limiting, shortcuts, themes
```

After Week 4, you have a working Devin-like product:
- User connects a repo → agent clones it in Docker
- Agent reads code, makes changes, runs tests
- User sees file tree, views code in editor, watches diffs
- User commits, pushes, and creates a PR — all from the UI

---

## Architecture Diagram (Target State)

```
┌─────────────────────────────────────────────────────────────────┐
│  Browser                                                         │
│ ┌───────────┬──────────────────────┬──────────────────────────┐ │
│ │ File      │  Code Editor         │  Chat          Terminal  │ │
│ │ Explorer  │  (Monaco)            │  ───────────   ────────  │ │
│ │           │                      │  Agent msgs    xterm.js  │ │
│ │ Git       │  Diff Viewer         │  User input    Commands  │ │
│ │ Status    │  (Monaco Diff)       │  Controls      Output    │ │
│ └───────────┴──────────────────────┴──────────────────────────┘ │
│          ↕ REST + WebSocket                                      │
├─────────────────────────────────────────────────────────────────┤
│  Next.js (Frontend)                                              │
│  Auth · API Routes · Prisma · Zustand                           │
│          ↕ HTTP                                                  │
├─────────────────────────────────────────────────────────────────┤
│  FastAPI (ai_engine)                                             │
│  Sessions · WebSocket · Chat · Files · Git · Auth               │
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
│  Users · Orgs · Projects · ChatSessions · ChatMessages          │
└─────────────────────────────────────────────────────────────────┘
```
