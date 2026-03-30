# 🏥 Health Check: Create New Project — Full Pipeline Audit

**Date:** March 28, 2026  
**Audited by:** AI Code Review  
**Scope:** Every file and function in the new project creation flow

---

## Pipeline Overview

```
User clicks "New Project"
    │
    ▼
┌─ WIZARD (6 steps) ──────────────────────────────────────────┐
│ Step 0: Stack Selection (9 stacks + "Choose for me")        │
│ Step 1: Description (textarea + file upload)                │
│ Step 2: Figma (Generate from scratch / Paste URL)           │
│ Step 3: Backend (None / Supabase / Own MCP)                 │
│ Step 4: Confirm (summary review)                            │
│ Step 5: Deploy (Hosted free / Own GitHub)                   │
└─────────────────────────────────────────────────────────────┘
    │
    ▼ onWizardComplete() → calls /api/enhance-prompt
    │
┌─ FRONTEND API ROUTES ──────────────────────────────────────┐
│ /api/enhance-prompt   → Claude generates tech spec         │
│ /api/recommend-stack  → Claude picks best framework        │
│ /api/figma extraction → Figma MCP or REST API              │
└────────────────────────────────────────────────────────────┘
    │
    ▼ sessionStorage → workspace page picks up prompt
    │
┌─ WORKSPACE HANDOFF ──────────────────────────────────────────┐
│ layout.js → creates DB conversation → navigates to workspace │
│ workspace/page.js → reads sessionStorage → auto-sends prompt │
└──────────────────────────────────────────────────────────────┘
    │
    ▼ WebSocket → AI Engine Backend
    │
┌─ AI ENGINE PIPELINE (8 phases) ──────────────────────────────┐
│ Phase 1: Validate inputs (API keys, git token)               │
│ Phase 2: Create workspace + copy skeleton                    │
│ Phase 3: Classify task with Gemini Flash                     │
│ Phase 4: Gemini Research → spec.md + plan.json               │
│ Phase 5: Claude writes code (guided by plan)                 │
│ Phase 6: Verify build                                        │
│ Phase 7: Commit + Create GitHub repo + Push                  │
│ Phase 8: Vercel auto-deploy (if configured)                  │
└──────────────────────────────────────────────────────────────┘
```

---

## Section 1: Wizard UI (`NewProjectWizard.js`)
**Status:** ✅ Stable

| Check | Status | Notes |
|-------|--------|-------|
| Step validation (goNext gate) | ✅ | Internal `stepValid` gate prevents bypass on rapid clicks |
| Stack selection (9 + auto) | ✅ | All 9 stacks have SVG icons, "Choose for me" triggers `/api/recommend-stack` |
| Description auto-list | ✅ | Claude-style numbered/bullet list continuation on Enter |
| File upload (PDF/DOCX/TXT) | ✅ | Drop + click, accepted types enforced |
| Figma step default | ✅ | "Generate from scratch" is default (`skipFigma: true`) |
| Backend MCP URL validation | ✅ | `own` backend requires non-empty URL |
| Confirm step "Edit" links | ✅ | Each row links back to correct step |
| Deploy step | ✅ | "Host for me" (free) and "Own GitHub" (later) |
| Close confirm modal | ✅ | Shows only if user has entered data |
| Loading overlays | ✅ | Both "Generating build spec" and "Choosing best stack" have full-page overlays |
| Animation (no jumping) | ✅ | Uses fade-only transitions, no `translateY` |
| Error retry on enhance fail | ✅ | Shows retry button with `RefreshCw` icon |

---

## Section 2: Frontend API Routes

### `/api/enhance-prompt/route.js`
**Status:** ⚠️ Has Issues

| Check | Status | Notes |
|-------|--------|-------|
| Auth guard | ✅ | `requireAuth()` called |
| JSON body validation | ✅ | Returns 400 on invalid JSON |
| Description required check | ✅ | Rejects empty descriptions |
| Figma tokens integration | ✅ | Calls `extractDesignTokens()`, appends to prompt if successful |
| Stack/backend label mapping | ✅ | Maps IDs to readable labels |
| Claude API call | ✅ | Correct headers, `anthropic-version`, `max_tokens: 4096` |
| Error handling | ✅ | Catches network errors, returns 502 |

> [!CAUTION]
> **Model string is incorrect:** Uses `claude-sonnet-4-20250514`. Verify this is a valid model ID. If not, should be `claude-sonnet-4-6` or similar.

> [!WARNING]
> **Ignores user settings:** Hardcoded to Anthropic. If a user has configured a different LLM provider in Settings, this route ignores it. The API key comes from `.env` (`ANTHROPIC_API_KEY`), not from the user's stored settings.

### `/api/recommend-stack/route.js`
**Status:** ⚠️ Has Issues

| Check | Status | Notes |
|-------|--------|-------|
| Auth guard | ✅ | `requireAuth()` called |
| JSON parsing fallback | ✅ | Falls back to text-matching if JSON parse fails |
| Framework name normalization | ✅ | Maps `"Next.js"` → `"nextjs"`, `"Vue.js"` → `"vue"`, etc. |
| Default fallback | ✅ | Falls back to `nextjs` if nothing matches |
| Response format | ✅ | Returns `{ stack, reason }` |

> [!CAUTION]
> **Same model string issue** as enhance-prompt: `claude-sonnet-4-20250514`.

> [!WARNING]
> **Classification rules are too simplistic:**
> - "Admin panels → Angular" — Most modern teams use React for admin panels
> - "Dashboards or SaaS → React" — Many SaaS apps are better with Next.js (SSR, SEO)
> - These rules will frequently recommend incorrect stacks

### `/lib/figma.js` (Design Token Extraction)
**Status:** ✅ Stable (with dependency caveat)

| Check | Status | Notes |
|-------|--------|-------|
| URL parsing | ✅ | Handles `/file/` and `/design/` URLs, extracts `node-id` |
| MCP config discovery | ✅ | Searches 3 config paths |
| MCP extraction with fallback | ✅ | Tries MCP → falls back to REST API |
| Color extraction | ✅ | Walks node tree, extracts top 20 colors by frequency |
| Typography extraction | ✅ | Extracts font families, sizes, weights, line heights |
| Spacing extraction | ✅ | Extracts auto-layout padding/spacing values |
| Component extraction | ✅ | Lists component names and types (max 30) |
| Layout extraction | ✅ | Extracts page/frame structure |
| Silent failure | ✅ | Returns `null` on any failure, never crashes |

> [!NOTE]
> Requires `FIGMA_ACCESS_TOKEN` in `.env`. MCP SDK is optional (dynamically imported).

---

## Section 3: Wizard Completion Handoff (`layout.js`)

**Status:** 🐛 Has a Bug

| Check | Status | Notes |
|-------|--------|-------|
| DB conversation creation | ✅ | Creates via `createConversation()` |
| Fallback on creation failure | ✅ | Uses `scratch-{timestamp}` ID |
| Enhanced prompt storage | ✅ | Stores in `sessionStorage` |
| Metadata storage | ✅ | Stores stack, backend, deployment |
| Navigation | ✅ | Uses `router.replace()` to prevent back-button issues |
| Wizard close on navigation | ✅ | `useEffect` on `pathname` closes wizard |

> [!CAUTION]
> **BUG: Wrong variable reference (line 200)**
> ```js
> figmaUrl: wizardState.figmaUrl,  // ❌ WRONG — wizardState doesn't exist here
> ```
> Should be:
> ```js
> figmaUrl: wizardResult.figmaUrl,  // ✅ CORRECT — parameter name
> ```
> `wizardState` is undefined in the `handleWizardComplete` callback scope. This means `figmaUrl` is always `undefined` in the metadata stored to `sessionStorage`. The Figma URL from the wizard **never reaches the backend**.

---

## Section 4: Workspace Auto-Start (`workspace/[projectId]/page.js`)

**Status:** ✅ Stable

| Check | Status | Notes |
|-------|--------|-------|
| Wizard prompt pickup | ✅ | Reads from `sessionStorage`, runs once via `useRef` guard |
| `[LUCID_PROJECT]` header injection | ✅ | Prepends `description=...\|stack=...\|backend=...` |
| Cleanup | ✅ | Removes all `wizard_*` keys from sessionStorage after read |
| Delayed send | ✅ | 300ms delay before `sendMessage()` to ensure WebSocket is ready |
| Status gating | ✅ | Only fires when `status === 'ready'` |
| DB conversation loading | ✅ | Loads from Supabase with 5s timeout fallback |
| Git token loading | ✅ | Loads from integrations, supports scratch mode (no repo) |
| Effective token gating | ✅ | Won't connect WebSocket until all data loaded |

---

## Section 5: AI Engine Pipeline (`task_pipeline.py`)

### Phase 1: Validate Inputs
**Status:** ✅ Stable

| Check | Status | Notes |
|-------|--------|-------|
| Anthropic API key check | ✅ | Rejects if empty/None |
| Gemini API key check | ✅ | Rejects if empty/None |
| Git provider detection | ✅ | Supports GitHub + GitLab |
| Scratch mode detection | ✅ | Correctly enters scratch mode when no repo is configured |
| Token stripping from URLs | ✅ | Sanitizes repo URLs |

### Phase 2: Workspace Preparation (Scratch Mode)
**Status:** ✅ Stable

| Check | Status | Notes |
|-------|--------|-------|
| Workspace creation | ✅ | `/tmp/lucid_new_{task_id}_{uuid}` |
| Git init with `main` branch | ✅ | Uses `-b main` flag |
| Git config (user/email) | ✅ | "Lucid AI" / "ai@lucid.dev" |
| Skeleton detection from header | ✅ | Regex extracts `stack=` from `[LUCID_PROJECT]` header |
| Admin detection | ✅ | Keyword-based (`admin`, `dashboard`, `panel`, etc.) |
| Skeleton copy | ✅ | `copy_skeleton()` doesn't overwrite existing files |
| Skeleton fallback | ✅ | Falls back to `react-vite` if unknown stack |

### Phase 3: Task Classification (Gemini)
**Status:** ✅ Stable

| Check | Status | Notes |
|-------|--------|-------|
| Gemini model | ✅ | `gemini-2.0-flash` (centralized constant) |
| Developer tiers | ✅ | Junior (haiku) / Mid (sonnet) / Senior (opus) |
| New project rules | ✅ | HTML-only → Junior, Standard app → Mid, Complex system → Senior |
| Max turns enforcement | ✅ | Junior: 8-10, Mid: 12-18, Senior: 20-25 |
| Model ID mapping | ✅ | haiku → `claude-haiku-4-5-20251001`, sonnet → `claude-sonnet-4-6`, opus → `claude-opus-4-6` |
| Fallback on failure | ✅ | Defaults to `mid`/`sonnet` with 12 turns |
| JSON cleanup | ✅ | Strips markdown fences if present |

### Phase 4: Gemini Research & Plan
**Status:** ✅ Stable

| Check | Status | Notes |
|-------|--------|-------|
| `gemini_research()` | ✅ | Generates `spec.md` with design system, pages, components, data model |
| `gemini_create_plan()` | ✅ | Generates `plan.json` with theme overrides, file list, package updates |
| Anti-template enforcement | ✅ | Prompt explicitly says "NEVER use #6366f1", demands unique palettes |
| UI sizing standards | ✅ | 14px base, 13px secondary, 8-10px button padding (Claude Console style) |
| File tree reading | ✅ | Reads skeleton files so Gemini knows what already exists |
| Pattern database | ✅ | Loads `app_patterns.json` (7 app types: landing, admin, ecommerce, etc.) |
| JSON validation | ✅ | Validates plan is valid JSON, wraps in fallback if not |
| File persistence | ✅ | Saves both `spec.md` and `plan.json` to `.lucid/` directory |
| Never raises | ✅ | Both functions catch all exceptions and return fallback strings |

### Phase 5: Claude Code Execution
**Status:** ✅ Stable

| Check | Status | Notes |
|-------|--------|-------|
| Claude Code SDK | ✅ | Uses `claude_code_sdk.query` |
| Guided prompt | ✅ | Includes theme application instructions, file-by-file plan, spec summary |
| CSS var enforcement | ✅ | Prompt says "NEVER use hardcoded hex colors" |
| Model selection | ✅ | Uses classified `model_id` (haiku/sonnet/opus) |

### Phase 6: Build Verification
**Status:** ✅ Stable

| Check | Status | Notes |
|-------|--------|-------|
| Build check | ✅ | Runs build verification |
| Change detection | ✅ | `verify_changes()` checks git diff |
| Empty generation guard | ✅ | Returns error if no changes detected |

### Phase 7: Commit + Create Repo + Push
**Status:** ✅ Stable

| Check | Status | Notes |
|-------|--------|-------|
| Local git commit | ✅ | `git add -A` + `git commit` |
| File list broadcast | ✅ | Sends generated file list to frontend UI |
| Platform token priority | ✅ | `PLATFORM_GITHUB_TOKEN` → user token → skip |
| Fine-grained token detection | ✅ | Rejects `github_pat_*` tokens (can't create repos) |
| Repo name derivation | ✅ | Smart naming: `{slug}_website_frontend`, `_admin_frontend`, `_service` |
| Repo name collision handling | ✅ | Tries 5 names: `name`, `name_2`, `name_3`, ... |
| Description sanitization | ✅ | Strips control characters, limits to 350 chars |
| Git push | ✅ | `git push -u origin main`, token stripped from error messages |
| DB persistence | ✅ | Saves `platform_repo_url` to `chat_sessions` table |
| Graceful failure | ✅ | If repo creation fails, code is saved locally with Export option |

### Phase 8: Vercel Auto-Deploy
**Status:** ✅ Stable (when configured)

| Check | Status | Notes |
|-------|--------|-------|
| Token gating | ✅ | Only runs if `VERCEL_TOKEN` env var is set |
| Vercel project creation | ✅ | Links to the auto-created GitHub repo |
| Deployment trigger | ✅ | Triggers via Vercel API v13 |
| Polling | ✅ | Polls deployment status every 5s, max 120s |
| URL persistence | ✅ | Saves deployment URL to `chat_sessions.vercel_url` |
| Auto-detect framework | ✅ | Sets `framework: null` so Vercel auto-detects |

---

## Section 6: Skeleton Templates

**Status:** ✅ Stable

| Skeleton | Files | Stack |
|----------|-------|-------|
| `admin-react` | Layout, DataTable, Dashboard, Login, Supabase client | React + Vite + Supabase |
| `html-css` | index.html, style.css, script.js | Pure static |
| `nextjs` | layout.js, page.js, globals.css | Next.js App Router |
| `react-vite` | App.jsx, main.jsx, index.css, vite.config | React + Vite |

| Check | Status | Notes |
|-------|--------|-------|
| Stack mapping | ✅ | 13 aliases → 4 skeletons |
| Admin override | ✅ | Keywords trigger `admin-react` regardless of stack |
| Default fallback | ✅ | Unknown stacks → `react-vite` |
| No-overwrite copy | ✅ | Existing files are never replaced |
| `.gitkeep` handling | ✅ | Creates empty dirs, skips `.gitkeep` files |

> [!WARNING]
> **Missing skeletons for Vue and Angular.** If a user selects Vue.js or Angular, the `STACK_MAP` has no entry for them, so they fall back to `react-vite`. This means the user gets a React project despite selecting Vue/Angular.

---

## 🐛 Critical Bugs Found

### Bug 1: `wizardState` reference error in `layout.js` (line 200)
**Severity:** 🔴 High — Figma URL is never passed to backend

```js
// layout.js line 196-200
sessionStorage.setItem(`wizard_meta_${conversation.id}`, JSON.stringify({
  stack: wizardResult.stack,
  backend: wizardResult.backend,
  deployment: wizardResult.deployment,
  figmaUrl: wizardState.figmaUrl,   // ❌ wizardState is UNDEFINED here
}));
```

**Fix:** Change `wizardState.figmaUrl` → `wizardResult.figmaUrl`

### Bug 2: Enhance/Recommend routes use potentially invalid model string
**Severity:** 🟡 Medium — Will fail if `claude-sonnet-4-20250514` is not a valid model

Both `/api/enhance-prompt` and `/api/recommend-stack` use `claude-sonnet-4-20250514`. The backend pipeline uses `claude-sonnet-4-6`. These should be consistent.

### Bug 3: Vue/Angular skeletons missing
**Severity:** 🟡 Medium — Users selecting Vue or Angular get a React project

No `vue` or `angular` directories exist in `ai_engine/skeletons/`. The `STACK_MAP` falls through to `react-vite`.

### Bug 4: Recommend-stack classification rules are dubious
**Severity:** 🟡 Low-Medium — May recommend wrong frameworks

The hardcoded rules ("Admin → Angular", "Dashboard → React") are opinionated and often wrong. Most teams use React/Next.js for admin panels, not Angular.

---

## ✅ What's Working Well

1. **Pipeline resilience** — Every phase has try/catch, fallback values, and "never raises" guarantees
2. **Scratch mode** — Full end-to-end flow from empty workspace to deployed Vercel site
3. **Anti-template design** — Gemini prompts enforce unique color palettes per project
4. **Token security** — Git tokens stripped from all error messages, fine-grained tokens rejected
5. **Repo collision handling** — Tries 5 name variants before failing
6. **Wizard UX** — Loading overlays, validation gates, Claude-style auto-lists, smooth transitions
7. **Classification system** — Smart 3-tier developer assignment with enforced turn limits
8. **Dual-save messages** — Both `chat_messages` and legacy `messages` tables for reliability
