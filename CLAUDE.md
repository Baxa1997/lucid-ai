# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Lucid AI is a chat-first AI coding assistant (think Devin / OpenHands). Users paste a GitHub or GitLab PAT to connect their repos, then describe a task in chat. An agent clones the repo in an isolated Docker sandbox, writes code, pushes a branch, and opens a PR — all visible as streaming events in the chat. There is no code editor pane; everything happens through chat. The platform uses the OpenHands SDK for agent execution.

## Architecture

Three services, orchestrated via `docker-compose.yml`:

1. **`frontend/`** — Next.js (App Router) with NextAuth v5, Prisma ORM, Tailwind CSS, Zustand state management
2. **`ai_engine/`** — FastAPI + OpenHands SDK V1 (4-package architecture). Primary AI backend with WebSocket agent communication, LiteLLM model routing, Docker-sandboxed execution
3. **`backend/`** — Legacy FastAPI microservice (older OpenHands integration). Being superseded by `ai_engine/`

### Key data flow
- Frontend calls `POST /api/agent/start` → validates auth, decrypts git tokens, forwards to Python backend at `/api/session/init`
- Python backend creates an agent session (OpenHands CodeActAgent), returns `sessionId`
- Frontend connects via WebSocket (`/ws/{sessionId}`) for real-time agent events (actions, observations, terminal output)

### Per-user isolation
No organization/multi-tenancy layer. Every resource (Project, Integration, AgentSession) belongs directly to a User. Git provider tokens (GitHub/GitLab PATs) are encrypted with AES-256-CBC before storage — the same key and algorithm is used by both the frontend (`frontend/src/lib/crypto.js`) and the ai_engine (`ai_engine/app/services/integrations.py`). The `ENCRYPTION_KEY` env var must be identical in both services.

## Common Commands

### Frontend (Next.js)
```bash
cd frontend
npm install
npm run dev          # Dev server on :3000
npm run build        # Production build
npm run lint         # ESLint (next/core-web-vitals)
npx prisma generate  # Regenerate Prisma client after schema changes
npx prisma db push   # Push schema changes to database
npx prisma studio    # Visual database browser
```

### AI Engine (Python)
```bash
cd ai_engine
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

### Docker (full stack)
```bash
docker-compose up              # All services (frontend :3000, backend :8000, postgres :5432)
docker-compose up -d db        # Just the database
```

## Key Files

- `frontend/prisma/schema.prisma` — Database schema (User, Project, Integration, AgentSession). No org/membership tables.
- `frontend/src/lib/auth.js` — NextAuth v5 config (Google OAuth + dev credentials provider, JWT strategy)
- `frontend/src/lib/prisma.js` — Prisma client singleton
- `frontend/src/lib/crypto.js` — AES-256-CBC encrypt/decrypt for git tokens (frontend side)
- `frontend/src/lib/gatekeeper.js` — Auth guard + `proxyToAI()` helper (adds `X-User-ID` + `X-Internal-Key`)
- `frontend/src/hooks/useAgentSession.js` — WebSocket hook for agent communication
- `frontend/src/store/useFlowStore.js` — Zustand store for workspace flow state
- `frontend/next.config.js` — WebSocket proxy rewrites (`/api/ws/*` → Python backend)
- `ai_engine/app/__init__.py` — FastAPI app factory; registers all routers
- `ai_engine/app/routers/ws.py` — WebSocket endpoint; session lifecycle + event streaming
- `ai_engine/app/routers/integrations.py` — PAT save/validate, repo listing, PR creation (B1/B2/B3)
- `ai_engine/app/services/integrations.py` — AES-256-CBC encrypt/decrypt + GitHub/GitLab API helpers
- `ai_engine/app/services/sessions.py` — In-memory session store + Docker sandbox lifecycle
- `ai_engine/app/auth.py` — JWT + X-User-ID/X-Internal-Key authentication

## Environment Variables

**Frontend** (`frontend/.env`): `DATABASE_URL`, `AUTH_SECRET`, `ENCRYPTION_KEY`, `PYTHON_BACKEND_URL`, `SESSION_SECRET`, `INTERNAL_API_KEY`, `NEXT_PUBLIC_AGENT_WS_URL`

**AI Engine** (`docker-compose.yml` or local env): `SESSION_SECRET`, `ENCRYPTION_KEY` (must match frontend), `INTERNAL_API_KEY` (must match frontend), `DATABASE_URL`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, `DEFAULT_MODEL_PROVIDER`, `MAX_ITERATIONS`

> `SESSION_SECRET`, `ENCRYPTION_KEY`, and `INTERNAL_API_KEY` must be **identical** in both the frontend and ai_engine services.

## Conventions

- Frontend uses JavaScript (not TypeScript) with Next.js App Router (`src/app/` directory)
- CSS utility function `cn()` from `@/lib/utils` (clsx + tailwind-merge)
- UI icons from `lucide-react`
- OpenHands SDK imports are wrapped in try/catch so the app runs in dev mode without the SDK installed (mock sessions)
- Path alias `@/` maps to `frontend/src/`
