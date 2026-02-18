# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Lucid AI is a multi-tenant SaaS platform for autonomous AI-powered software engineering. Users connect git repositories, launch AI agent sessions that run in Docker-sandboxed environments, and interact via a chat/terminal interface. The platform uses the OpenHands SDK for agent execution.

## Architecture

Three services, orchestrated via `docker-compose.yml`:

1. **`frontend/`** — Next.js (App Router) with NextAuth v5, Prisma ORM, Tailwind CSS, Zustand state management
2. **`ai_engine/`** — FastAPI + OpenHands SDK V1 (4-package architecture). Primary AI backend with WebSocket agent communication, LiteLLM model routing, Docker-sandboxed execution
3. **`backend/`** — Legacy FastAPI microservice (older OpenHands integration). Being superseded by `ai_engine/`

### Key data flow
- Frontend calls `POST /api/agent/start` → validates auth, decrypts git tokens, forwards to Python backend at `/api/session/init`
- Python backend creates an agent session (OpenHands CodeActAgent), returns `sessionId`
- Frontend connects via WebSocket (`/ws/{sessionId}`) for real-time agent events (actions, observations, terminal output)

### Multi-tenancy
Organization-scoped: Users belong to Organizations via Memberships. Projects and Integrations are org-scoped. Git provider tokens are encrypted with AES-256-CBC (see `frontend/src/lib/crypto.js`).

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

- `frontend/prisma/schema.prisma` — Database schema (Organization, User, Membership, Project, Integration, AgentSession)
- `frontend/src/lib/auth.js` — NextAuth v5 config (Google OAuth + dev credentials provider, JWT strategy)
- `frontend/src/lib/prisma.js` — Prisma client singleton
- `frontend/src/lib/crypto.js` — AES-256-CBC encrypt/decrypt for git tokens
- `frontend/src/hooks/useAgentSession.js` — WebSocket hook for agent communication
- `frontend/src/store/useFlowStore.js` — Zustand store for workspace flow state
- `ai_engine/main.py` — FastAPI app with session init, WebSocket handler, OpenHands agent loop
- `frontend/next.config.js` — WebSocket proxy rewrites (`/api/ws/*` → Python backend)

## Environment Variables

Frontend requires `.env` (see `.env.example`): `DATABASE_URL`, `AUTH_SECRET`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `ENCRYPTION_KEY`, `PYTHON_BACKEND_URL`, `SESSION_SECRET`

AI Engine uses: `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, `DEFAULT_MODEL_PROVIDER`, `MAX_ITERATIONS`, `AGENT_SERVER_PORT`

## Conventions

- Frontend uses JavaScript (not TypeScript) with Next.js App Router (`src/app/` directory)
- CSS utility function `cn()` from `@/lib/utils` (clsx + tailwind-merge)
- UI icons from `lucide-react`
- OpenHands SDK imports are wrapped in try/catch so the app runs in dev mode without the SDK installed (mock sessions)
- Path alias `@/` maps to `frontend/src/`
