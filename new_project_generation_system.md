# New Project Generation System
# Complete Implementation Guide (v4 — Opus 4.6 + Multi-Call + Zero Limits)

---

## ARCHITECTURE OVERVIEW

```
Current approach (SLOW + FRAGILE):
  Gemini researches → plan.json → Claude Code SDK (38 file-by-file calls)
  → build check with SDK → push
  Total: 15-42 min, $3-7, frequent crashes (SDK exit code 1)

New approach (CORRECT):
  0. User picks "Choose for me" (default) → Gemini decides stack
  1. Clone real template from GitHub (Next.js / React / Vue)
  2. Gemini does ULTRA-DEEP research (internet search → real products → full blueprint)
  3. Claude OPUS 4.6 generates files via 3-phase multi-call (direct HTTP, NOT SDK)
     Call 1: Foundation (theme + config + layouts + main page + router)
     Call 2: Content (all sections OR all CRUD features — NO LIMIT)
     Call 3: Extra pages + polish (about, contact, settings, etc.)
  4. Backend writes files to disk after each call (REPLACES all demo content)
  5. npm install + build check → auto-fix with Opus (NOT SDK)
  6. Commit + push → done
  Total: 4-7 min, $2-4, no SDK crashes, ZERO LIMITS

Model: claude-opus-4-6 (120K+ output tokens per call)
Calls: 3 sequential (32K tokens each = 96K total capacity = 50+ files)
Limits: NONE — pages, sections, entities, layouts all fully dynamic
```

### Three Key Principles

1. **"Choose for me" is the DEFAULT** — Gemini analyzes the description and picks the best stack. User can still manually override.
2. **Claude REPLACES all demo content** — every template page, config, and style is overwritten with domain-specific content. No generic "Welcome" pages survive.
3. **Gemini does ULTRA-DEEP research** — analyzes top real products, extracts exact design systems, page specs, data entities, image sources, mock data, and complete UI patterns. Claude gets a production-ready blueprint.

---

## TEMPLATES

We have three production-ready templates on GitHub:

| Stack | Template Repo | App Type | Layout |
|-------|---------------|----------|--------|
| `nextjs` | `LucidSoftware-tech/lucid-template-nextjs-website` | Landing pages, marketing, blogs, portfolio | Header + Footer, route groups |
| `react` | `LucidSoftware-tech/lucid-template-react-admin` | Admin panels, CRM, dashboards, CRUD apps | Sidebar + Header, react-router |
| `vue` | `LucidSoftware-tech/lucid-template-vue-admin` | Admin panels, CRM, dashboards, CRUD apps | Sidebar + Header, vue-router |

Each template includes:
- **shadcn/ui components** — pre-installed, importable (Button, Card, Table, Input, Modal, etc.)
- **Layout components** — Sidebar, Header, Footer, Auth/Main layouts (DO NOT recreate)
- **API client** — Axios/fetch with token interceptors
- **Auth store** — Zustand/Pinia with persistence
- **Router** — Pre-configured with auth guards + route definitions
- **TEMPLATE_MANIFEST.md** — Complete API reference for all available components

The generator **REPLACES all demo content** in the template (pages, sections, config, styles) while keeping the infrastructure (routing, auth, UI components, layouts). The result is a fully customized, production-ready application — NOT a template with changed text.

---

## TEMPLATE REPO CHANGES (one-time setup)

Each template needs minor dependency additions for the generator to produce chart-heavy dashboards and animated landing pages without build errors.

### `lucid-template-nextjs-website`

**Missing:** `recharts` (pricing comparisons, analytics sections), `framer-motion` (premium animations)

```bash
cd /path/to/lucid-template-nextjs-website
pnpm add recharts framer-motion
git add -A && git commit -m "feat: add recharts + framer-motion for AI generator" && git push
```

### `lucid-template-react-admin`

**Already has:** recharts, zustand, react-hook-form, zod, lucide-react ✅
**Missing:** `framer-motion` (page transitions, micro-animations)

```bash
cd /path/to/lucid-template-react-admin
pnpm add framer-motion
git add -A && git commit -m "feat: add framer-motion for AI generator animations" && git push
```

### `lucid-template-vue-admin`

**Missing:** Chart library for dashboards (Vue can't use recharts — it's React only)

```bash
cd /path/to/lucid-template-vue-admin
pnpm add chart.js vue-chartjs
git add -A && git commit -m "feat: add chart.js + vue-chartjs for AI generator dashboards" && git push
```

---

## STACK SELECTION — "Choose for me" as DEFAULT

### Frontend Change: `NewProjectWizard.js`

**Current:** The wizard's initial `stack` is `null`. "Choose for me" is a secondary option.  
**New:** Default `stack` to `'auto'` (pre-selected when wizard opens). "Choose for me" moves to the TOP with a `✨ Recommended` badge.

```diff
 const [wizardState, setWizardState] = useState({
-   stack: null, description: '', descriptionFile: null,
+   stack: 'auto', description: '', descriptionFile: null,
    figmaUrl: '', skipFigma: true, backend: 'none', mcpUrl: '', deployment: 'hosted',
 });
```

In Step 0 (Stack selection), the "Choose for me" card renders FIRST, above the grid, with a prominent `✨ Recommended` badge. The manual stacks (Next.js, React, Vue, etc.) show below with a "Or choose manually:" label.

### Frontend Change: `recommend-stack/route.js`

**Current:** Only recommends Next.js or React.  
**New:** Also recommends Vue when user mentions Vue or admin panel preference.

```diff
-pick the single best frontend framework from: Next.js, React.
+pick the single best frontend framework from: Next.js, React, Vue.

 Rules:
  ...
+ Vue admin panel → Vue
+ Vue dashboard → Vue
  
 const nameMap = {
   'next.js': 'nextjs',
   'nextjs': 'nextjs',
   'react': 'react',
+  'vue': 'vue',
+  'vue.js': 'vue',
 };
```

### How the full flow works:

```
User opens wizard
  → "Choose for me" is pre-selected (stack = 'auto')
  → User types description: "Build a food delivery management CRM"
  → User clicks Continue
  → Frontend calls /api/recommend-stack with the description
  → Gemini/Claude analyzes: "CRM + management → admin panel → React"
  → stack is set to 'react' internally
  → Phase 2 clones lucid-template-react-admin
  → Phase 4 Gemini researches real food delivery CRMs
  → Phase 5 Claude generates all domain-specific files
```

If user manually picks a stack (Next.js / React / Vue), the recommendation step is skipped.

---

## WHAT HAPPENS TO THE TEMPLATE'S DEMO UI

The template ships with **real working demo pages** (generic landing page, sample dashboard, Users CRUD). Claude **FULLY REPLACES all demo content** — the JSON response includes overwrites for every page that needs domain-specific content.

```
Template demo content:                 Claude REPLACES with:
───────────────────────                ─────────────────────
src/app/(marketing)/page.js            → REWRITE: import domain-specific sections
  (generic "Hero + Features + CTA")       (HeroSection, MenuSection, DeliverySection...)
src/config/navigation.js               → REWRITE: domain navigation items
  (generic "Home, About, Contact")        ("Menu", "Delivery Zones", "Track Order")
src/config/site.js                     → REWRITE: brand name, tagline, description
  (generic "My App")                      ("FoodDash — Fast Delivery")
src/app/globals.css                    → UPDATE: :root CSS variables
  (template default purple)               (researched premium palette from Gemini)
src/pages/dashboard/DashboardPage.jsx  → REWRITE: domain-specific KPI cards
  (generic stats cards)                   ("Orders Today", "Active Drivers", "Revenue")
src/features/users/                    → REPLACED by domain features
  (generic Users CRUD)                    (Orders, Drivers, Restaurants, etc.)
```

**The template's layout/routing/auth/UI-components stay untouched.** Only content pages and config files change.

---

## STEP BY STEP IMPLEMENTATION

---

### STEP 1 — Create `ai_engine/app/services/project_generator.py`

This is a NEW file containing the entire new generation pipeline.
It replaces `execute_project_in_batches()` for new projects.

Functions:

```
1. _ws_send(websocket, type, message)
   - Safe websocket send helper (never raises)

2. _build_file_tree(workspace_path) → str
   - Flat file tree of workspace (excludes node_modules, .git, etc.)

3. _read_manifest(workspace_path) → str
   - Reads TEMPLATE_MANIFEST.md from workspace root

4. _parse_json_response(text) → dict | None
   - Extracts + repairs JSON from Claude's text (code fences, brace matching)

5. gemini_deep_research(description, app_type, stack, gemini_key, websocket) → str
   - Ultra-deep product research via Gemini with internet search
   - Returns: design system, page specs, data entities, image URLs, UX patterns

6. call_claude_for_json(system, user, api_key, ws, model="claude-opus-4-6", max_tokens=32000) → dict | None
   - Direct Claude API call via httpx (NOT Claude Code SDK)
   - Model: claude-opus-4-6 (120K+ output tokens)
   - Returns parsed JSON with {files: [...], env: {...}}

7. write_files_from_json(json_response, workspace_path) → list[str]
   - Writes files to disk
   - ONLY protects: TEMPLATE_MANIFEST.md, package.json, build configs
   - Layout files (Sidebar, Header, Footer) are FULLY REWRITABLE

8. build_phase1_prompt(...) → str
   - Foundation: theme, config, nav, layouts, main page, router

9. build_phase2_prompt(...) → str
   - Content: all sections (landing) OR all CRUD features (admin)

10. build_phase3_prompt(...) → str
    - Extra pages: about, contact, pricing, settings, profile, etc.

11. verify_and_fix_build(workspace_path, api_key, websocket) → bool
    - npm install + build + Opus auto-fix (NOT SDK)
    - Max 2 retry cycles

12. generate_new_project(description, workspace_path, validated, websocket, ...) → bool
    - Main orchestrator — 3-phase multi-call generation
    - Rebuilds file tree between calls for import resolution
    - Streams progress to websocket per phase
```

---

### STEP 2 — Template-Aware Prompt Architecture

The prompt system is the core of quality. Each template has different rules.

#### Next.js Website Template Rules

```python
NEXTJS_WEBSITE_RULES = """
## TEMPLATE: Next.js 14 Website (App Router + Tailwind + shadcn/ui)

### What ALREADY EXISTS (DO NOT create these):
- Layout: src/app/layout.js (root), src/app/(marketing)/layout.js
- Navigation: src/components/layout/MarketingHeader.jsx, MarketingFooter.jsx
- Auth pages: src/app/(auth)/login/page.js, register/page.js
- Providers: src/components/Providers.jsx (QueryClient + TooltipProvider)
- UI Components: 25+ shadcn/ui components in src/components/ui/
- Config: src/config/site.js, navigation.js, icons.js
- API client: src/lib/api-client.js
- Hooks: useDebounce, useLocalStorage, usePagination

### What YOU generate:
- src/app/globals.css — UPDATE the :root CSS variables for the project theme
- src/config/site.js — REWRITE with the project name, description, URL
- src/config/navigation.js — REWRITE with the project's nav items
- src/app/(marketing)/page.js — REWRITE with section component imports
- src/components/sections/*.jsx — CREATE all section components (Hero, Features, Pricing, etc.)
- Additional pages (src/app/(marketing)/about/page.js, pricing/page.js, contact/page.js) — CREATE
- src/app/(marketing)/blog/page.js — CREATE if blog is in the spec

### File naming (sections):
- src/components/sections/HeroSection.jsx
- src/components/sections/FeaturesSection.jsx
- src/components/sections/PricingSection.jsx
- src/components/sections/TestimonialsSection.jsx
- src/components/sections/FAQSection.jsx
- src/components/sections/CTASection.jsx
- src/components/sections/HowItWorksSection.jsx
- etc.

### Import rules:
- Icons: import { IconName } from 'lucide-react'
- UI: import { Button } from '@/components/ui/Button'  (uppercase custom)
      import { Accordion, AccordionItem } from '@/components/ui/accordion'  (lowercase shadcn)
- Config: import { siteConfig } from '@/config/site'
- Hooks: import { useDebounce } from '@/hooks'
- Next.js: 'use client' at top of any component using hooks/events
- Images: use <img> with placeholder URLs, NOT next/image
- Avatars: https://i.pravatar.cc/150?u=person{N}
- Animations: use framer-motion (import { motion } from 'framer-motion')

### Design rules:
- Use Tailwind CSS classes (bg-primary, text-foreground, bg-muted, etc.)
- NEVER hardcode hex/rgb colors — always use CSS variables via Tailwind
- Every section must be responsive (mobile-first, sm: md: lg: xl: breakpoints)
- Add hover effects, transitions, and micro-animations on interactive elements
- Use gradient backgrounds for hero sections (bg-gradient-to-br, etc.)
- Cards must have shadows, rounded corners, and hover elevation
"""
```

#### React Admin Template Rules

```python
REACT_ADMIN_RULES = """
## TEMPLATE: React + Vite Admin Panel (Tailwind + shadcn/ui)

### What ALREADY EXISTS (DO NOT create these):
- Layout: src/components/layout/MainLayout.jsx, Sidebar.jsx, Header.jsx, AuthLayout.jsx
- Router: src/router/index.jsx, routes.jsx, PrivateRoute.jsx
- Auth: src/pages/auth/LoginPage.jsx, store/auth.store.js, services/auth.service.js
- UI Components: 25+ shadcn/ui components in src/components/ui/
- DataTable wrapper: src/components/ui/data-table.jsx (uses @tanstack/react-table)
- Example CRUD: src/features/users/ (full service + hooks + pages)
- API client: src/api/client.js
- Stores: auth.store.js, ui.store.js (Zustand)
- Config: src/config/navigation.js, icons.js
- i18n: src/i18n/ (en.json, ru.json)

### What YOU generate:
- src/styles/global.css — UPDATE :root CSS variables for brand colors
- src/config/navigation.js — REWRITE with project-specific nav items + Lucide icons
- src/pages/dashboard/DashboardPage.jsx — REWRITE with domain KPI cards + Recharts charts
- src/features/<entity>/ — CREATE new CRUD features for EACH entity from research:
  - services/<entity>.service.js — API client calls (getAll, getById, create, update, delete)
  - hooks/use<Entity>.js — React Query hooks (useQuery, useMutation)
  - pages/<Entity>ListPage.jsx — DataTable with columns, actions, filters, search
  - pages/<Entity>FormPage.jsx — Create/Edit form with validation (react-hook-form + zod)
- src/router/routes.jsx — UPDATE to add new feature routes

### Feature structure pattern (FOLLOW THIS EXACTLY):
src/features/<entity>/
├── services/<entity>.service.js    # API calls
├── hooks/use<Entity>.js            # React Query wrappers
├── pages/<Entity>ListPage.jsx      # DataTable + actions
└── pages/<Entity>FormPage.jsx      # Form (create + edit)

### Import rules:
- Icons: import { IconName } from 'lucide-react'
- UI: import { Button } from '@/components/ui/Button'
      import { DataTable } from '@/components/ui/data-table'
      import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'
- Hooks: import { useDebounce } from '@/hooks'
- Store: import { useAuthStore } from '@/store/auth.store'
- Router: import { useNavigate, useParams } from 'react-router-dom'
- Charts: import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, AreaChart, Area, PieChart, Pie, Cell } from 'recharts'
- Animations: import { motion, AnimatePresence } from 'framer-motion'
- Forms: import { useForm } from 'react-hook-form'
         import { zodResolver } from '@hookform/resolvers/zod'
         import { z } from 'zod'

### Dashboard pattern:
- Row 1: 4 KPI stat cards (value, label, change %, icon, sparkline)
- Row 2: 2 charts (area chart + bar chart or pie chart)
- Row 3: Recent activity table (5-10 rows)
- ALL data must be realistic mock data (not lorem ipsum)
"""
```

#### Vue Admin Template Rules

```python
VUE_ADMIN_RULES = """
## TEMPLATE: Vue 3 + Vite Admin Panel (Tailwind + Shadcn Vue)

### What ALREADY EXISTS (DO NOT create these):
- Layout: src/components/layout/AppSidebar.vue, AppHeader.vue, MainLayout.vue, AuthLayout.vue
- Router: src/router/index.js, routes.js, guards.js
- Auth: src/pages/auth/LoginPage.vue, stores/auth.store.js, services/auth.service.js
- UI Components: 30+ Shadcn Vue components in src/components/ui/
- Example CRUD: src/features/users/ (full service + composables + pages)
- API client: src/lib/api-client.js
- Stores: auth.store.js, ui.store.js (Pinia)

### What YOU generate:
- src/assets/styles/global.css — UPDATE :root CSS variables
- src/config/navigation.js — REWRITE with project nav items
- src/pages/dashboard/DashboardPage.vue — REWRITE with domain KPIs and vue-chartjs charts
- src/features/<entity>/ — CREATE new CRUD features:
  - services/<entity>.service.js
  - composables/use<Entity>.js (TanStack Vue Query)
  - pages/<Entity>ListPage.vue
  - pages/<Entity>FormPage.vue
- src/router/routes.js — UPDATE to add new feature routes

### Vue conventions:
- Use <script setup> (Composition API, NOT Options API)
- Use defineProps/defineEmits, NOT this.$props
- Templates use kebab-case for custom components
- Import Shadcn Vue: import { Dialog, DialogTrigger } from '@/components/ui/dialog'
- Charts: import { Bar, Doughnut, Line } from 'vue-chartjs'
          import { Chart as ChartJS, ... } from 'chart.js'
"""
```

---

### STEP 3 — `call_claude_for_json()` — Direct API call

```python
import httpx
import json

async def call_claude_for_json(
    system_prompt: str,
    user_prompt: str,
    api_key: str,
    websocket,
    max_tokens: int = 32000,
    model: str = "claude-opus-4-6"
) -> dict | None:
    """Call Claude API directly and parse JSON from the response.
    
    NOT using Claude Code SDK — avoids SDK subprocess crashes
    and gives direct control over the response format.
    """
    await _ws_send(websocket, "progress", "🤖 Claude generating project...")

    async with httpx.AsyncClient(timeout=300) as client:
        response = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            json={
                "model": model,
                "max_tokens": max_tokens,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_prompt}]
            }
        )
        
        if response.status_code != 200:
            error_body = response.text[:300]
            await _ws_send(websocket, "error", f"Claude API error {response.status_code}: {error_body}")
            return None

        data = response.json()
        text = data["content"][0]["text"]
        
        return _parse_json_response(text)


def _parse_json_response(text: str) -> dict | None:
    """Extract and parse JSON from Claude's response text."""
    # Remove markdown code fences
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        text = text.split("```")[1].split("```")[0].strip()
    
    # Remove trailing description after JSON
    if "\n---\n" in text:
        text = text.split("\n---\n")[0].strip()
    
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Progressive JSON repair: find outermost { ... }
        brace_count = 0
        start = -1
        end = -1
        for i, c in enumerate(text):
            if c == '{':
                if start == -1:
                    start = i
                brace_count += 1
            elif c == '}':
                brace_count -= 1
                if brace_count == 0:
                    end = i + 1
                    break
        
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end])
            except json.JSONDecodeError:
                pass
        
        return None
```

---

### STEP 4 — `write_files_from_json()` — Safe file writer

```python
# Files that must NEVER be overwritten by the generator
# NOTE: Layout files (Sidebar, Header, Footer) are NOT protected —
# Claude can fully rewrite them to create unique project-specific layouts.
PROTECTED_FILES = {
    "TEMPLATE_MANIFEST.md",     # Reference doc for Claude
    "package.json",              # Dependencies (managed separately)
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "node_modules",
    ".gitignore",
    "tailwind.config.js",        # Build config
    "postcss.config.js",
    "vite.config.js",
    "next.config.mjs",
    "next.config.js",
    "jsconfig.json",
    "tsconfig.json",
    "components.json",           # shadcn config
}
# EVERYTHING ELSE is fair game — layouts, pages, configs, styles, sections, features

def write_files_from_json(
    json_response: dict,
    workspace_path: str,
) -> list[str]:
    """Write generated files to disk. Returns list of paths written."""
    files_written = []
    
    for file_entry in json_response.get("files", []):
        path = file_entry.get("path", "").strip()
        content = file_entry.get("content", "")

        if not path or not content:
            continue
        
        # Normalize path
        path = path.lstrip("./")
        basename = os.path.basename(path)
        
        # Never overwrite protected config files
        if basename in PROTECTED_FILES:
            continue

        full_path = os.path.join(workspace_path, path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)
        
        files_written.append(path)
    
    # Write .env if provided
    env = json_response.get("env", {})
    if env and isinstance(env, dict):
        env_path = os.path.join(workspace_path, ".env.local")
        if not os.path.exists(env_path):
            env_content = "\n".join(f"{k}={v}" for k, v in env.items())
            with open(env_path, "w") as f:
                f.write(env_content + "\n")
            files_written.append(".env.local")
    
    return files_written
```

---

### STEP 5 — `verify_and_fix_build()` — Build check with Claude API fix

```python
async def verify_and_fix_build(
    workspace_path: str,
    api_key: str,
    websocket,
    max_retries: int = 2,
) -> bool:
    """Run npm install + build, auto-fix errors via Claude API if needed."""
    
    pm = detect_package_manager(workspace_path, "npm")
    build_env = _pm_env(pm)
    
    # Install dependencies
    await _ws_send(websocket, "progress", f"📦 Installing dependencies ({pm})...")
    install_cmd = _pm_install_cmd(pm)
    await asyncio.to_thread(
        subprocess.run, install_cmd,
        cwd=workspace_path, capture_output=True, text=True,
        timeout=180, env=build_env,
    )
    
    # Build + fix loop
    for attempt in range(max_retries + 1):
        await _ws_send(websocket, "progress", f"🔍 Verifying build (attempt {attempt + 1})...")
        
        build_result = await asyncio.to_thread(
            subprocess.run, [pm, "run", "build"],
            cwd=workspace_path, capture_output=True, text=True,
            timeout=120, env=build_env,
        )
        
        if build_result.returncode == 0:
            await _ws_send(websocket, "progress", "✅ Build passed!")
            return True
        
        # Extract errors
        output = (build_result.stdout or "") + "\n" + (build_result.stderr or "")
        error_lines = [l for l in output.splitlines()
                       if any(kw in l.lower() for kw in [
                           "error", "module not found", "cannot find",
                           "unexpected token", "is not defined",
                       ])][:30]
        errors = "\n".join(error_lines)
        
        if attempt < max_retries:
            await _ws_send(websocket, "progress", "⚠️ Build errors found, auto-fixing...")
            
            file_tree = _build_file_tree(workspace_path)
            manifest = _read_manifest(workspace_path)
            
            fix_prompt = f"""Fix ALL build errors. Return fixed files as JSON.

Errors:
{errors}

File tree:
{file_tree}

Template manifest (available imports):
{manifest[:3000]}

Rules:
- Fix ONLY the files with errors
- If "Module not found" → check the manifest for the correct import path
- If "is not defined" → add the missing import
- If "Unexpected token" → fix syntax
- Return JSON: {{"files": [{{"path": "...", "content": "full file content"}}]}}
- Return ONLY the files that need fixes
"""
            fix_result = await call_claude_for_json(
                system_prompt="You are a build error fixer. Return fixed files as JSON only.",
                user_prompt=fix_prompt,
                api_key=api_key,
                websocket=websocket,
                max_tokens=12000,
            )
            if fix_result and fix_result.get("files"):
                write_files_from_json(fix_result, workspace_path)
    
    await _ws_send(websocket, "warning", "⚠️ Build has errors but project will be pushed anyway.")
    return False
```

---

### STEP 6 — `gemini_deep_research()` — Ultra-Deep Product Research

This is the **most critical function** in the entire system. The quality of the research directly determines the quality of the generated app. Gemini searches the internet, analyzes top products, and produces a complete blueprint for Claude.

```python
async def gemini_deep_research(
    description: str,
    app_type: str,
    stack: str,
    gemini_key: str,
    websocket,
) -> str:
    """Ultra-deep product research via Gemini with internet search.
    
    Analyzes 3-5 real products in the user's domain and extracts:
    - Exact design system (HSL colors, fonts, spacing)
    - Complete page specifications (sections, content, layout)
    - Data entity definitions (fields, types, relationships)  
    - Image source URLs (avatars, backgrounds, icons)
    - UI/UX patterns (animations, interactions, micro-effects)
    - Mock data (realistic, 10-20 rows per table)
    
    This is NOT "pick a color for food" — it's "study DoorDash, Uber Eats, 
    and Deliveroo, then tell Claude EXACTLY how to build it."
    """
    
    await _ws_send(websocket, "progress", "🔬 Researching top products in this domain...")

    if app_type in ("admin_panel", "ecommerce", "saas_app", "analytics"):
        research_prompt = f"""
You are a SENIOR UI/UX ARCHITECT. Your job: search the internet, study the TOP 3-5 real products 
similar to "{description}", then output a CODE-READY BLUEPRINT for a code generator.

IMPORTANT RULES:
- EVERY value must come from your actual internet research — never invent or use defaults
- Study the real products' actual UI, colors, layout, navigation, data models
- Synthesize the BEST patterns from ALL products you analyze into one unified design
- Output must be structured and specific — a code generator will parse this directly
- No paragraphs, no explanations — only structured data

UI/UX STANDARDS TO APPLY (on top of competitor research):
- Follow 8px spacing grid (padding/margins in multiples of 8: 8, 16, 24, 32, 48)
- Ensure WCAG AA contrast ratio (4.5:1 for text, 3:1 for large text) between foreground and background colors
- Use a proper type scale (e.g., 12/14/16/18/24/30/36/48px — not random sizes)
- Apply visual hierarchy: primary actions prominent, secondary subdued, destructive actions red
- Group related UI elements with clear section boundaries and whitespace
- Use consistent icon sizing (16px inline, 20px buttons, 24px nav, 40px empty states)
- Ensure touch targets are minimum 44x44px for mobile
- Apply information density best practices: admin panels = compact, landing pages = spacious
- Use progressive disclosure: show essentials first, details on demand

OUTPUT THIS EXACT STRUCTURE (fill ALL values from your research):

===PRODUCTS_ANALYZED===
1. [Product name] ([URL]) — [what makes their UI excellent]
2. [Product name] ([URL]) — [what makes their UI excellent]
3. [Product name] ([URL]) — [what makes their UI excellent]

===CSS_VARIABLES===
(Extract the dominant color palette from the best product you analyzed.
Convert to HSL format. Every line must have a real researched value.)
--primary: [hsl value from research]
--primary-foreground: [hsl value]
--secondary: [hsl value]
--secondary-foreground: [hsl value]
--accent: [hsl value]
--accent-foreground: [hsl value]
--destructive: [hsl value]
--destructive-foreground: [hsl value]
--background: [hsl value]
--foreground: [hsl value]
--card: [hsl value]
--card-foreground: [hsl value]
--muted: [hsl value]
--muted-foreground: [hsl value]
--border: [hsl value]
--ring: [hsl value]
--radius: [value]rem
--chart-1: [hsl value]
--chart-2: [hsl value]
--chart-3: [hsl value]
--chart-4: [hsl value]
--chart-5: [hsl value]
--success: [hsl value]
--warning: [hsl value]
--info: [hsl value]

===DARK_MODE_VARIABLES===
(If the analyzed products use dark mode, provide dark overrides)
--background: [hsl value]
--foreground: [hsl value]
--card: [hsl value]
(... all overrides needed)

===FONTS===
(Pick the exact fonts used by the best-looking product you analyzed)
heading: [font name] ([Google Fonts URL with weights])
body: [font name] ([Google Fonts URL with weights])

===SIDEBAR_NAVIGATION===
(Model this after the real products' actual navigation structure)
[group: "[group name from research]"]
- [Menu item] | [LucideIconName]
- [Menu item] | [LucideIconName] | badge: "[if applicable]"
[group: "[group name]"]
- [Menu item] | [LucideIconName]
- [Menu item] | [LucideIconName]
[group: "[group name]"]
- [Menu item] | [LucideIconName]
(as many groups and items as the real products have)

===DASHBOARD===
(Model this after the real products' actual dashboards — what metrics do they show?)

[kpi_cards]
(List exactly 4 KPI cards based on what the real products display)
1. label: "[metric from research]" | value: "[realistic format]" | change: "[%]" | trend: up/down | icon: [LucideIcon] | color: "[token name]"
2. label: "[metric]" | value: "[format]" | change: "[%]" | trend: up/down | icon: [LucideIcon] | color: "[token]"
3. label: "[metric]" | value: "[format]" | change: "[%]" | trend: up/down | icon: [LucideIcon] | color: "[token]"
4. label: "[metric]" | value: "[format]" | change: "[%]" | trend: up/down | icon: [LucideIcon] | color: "[token]"

[chart_1]
type: [area/bar/line — based on what the real products use]
title: "[chart title from research]"
x_axis: [what the real products use as X axis]
data_series:
  - name: "[series name]" | values: [12 realistic data points based on the domain]
color: [which CSS variable tokens to use]

[chart_2]
type: [bar/pie/donut — based on research]
title: "[chart title]"
data: [realistic categories and values for this domain]
color: [tokens]

[recent_table]
title: "[table title from research]"
columns: [columns the real products show in their main table]
rows: (provide 8 rows of realistic mock data appropriate for this specific domain)

===ENTITIES===
(List EVERY data entity that the real products manage — study their actual menus and pages)

[entity: [EntityName from research]]
list_columns: [column(type) | column(type) | ... — based on real product tables]
list_filters: [filters the real products offer]
list_actions: [actions available in real products]
form_fields:
  - [field] | type: [appropriate type] | required: [true/false] | placeholder: "[domain-specific]"
  - (list all fields based on what real products collect)
mock_data: (provide 10 rows of realistic data for this specific entity)

[entity: [EntityName]]
(repeat for every entity — as many as the real products manage)

===STATUS_BADGES===
(Based on the statuses/states the real products use)
[status]: [color] ([exact Tailwind classes])
[status]: [color] ([exact Tailwind classes])
(list all statuses relevant to this domain)

===UI_PATTERNS===
(Based on the visual style of the best product you analyzed)
card: [exact Tailwind classes for card styling]
table: [table style description with Tailwind classes]
button_primary: [exact Tailwind classes]
input: [exact Tailwind classes]
sidebar_width: [px value from research]
header_height: [px value]
content_max_width: [px value]
animation: [animation approach]
overall_vibe: [2-3 words describing the visual feel]

===AVATAR_SOURCES===
user_avatars: https://i.pravatar.cc/40?u=[unique_string_pattern]
profile_avatars: https://i.pravatar.cc/150?u=[unique_string_pattern]
company_logos: [how to render company logos without external images]

===DOMAIN_MUST_HAVES===
(Study the real products and identify features that are ESSENTIAL for this domain.
If a product in this space doesn't have these, users will consider it broken/incomplete.
List every must-have feature and specify how to implement it.)

Examples of what to look for — but discover dynamically from your research:
- TMS/logistics → interactive map (react-leaflet or @react-google-maps/api), route tracking, GPS
- Finance/accounting → transaction tables with export (CSV/PDF), currency formatting, charts
- Healthcare → appointment calendar (react-big-calendar), patient timeline, medical records
- Real estate → property gallery/carousel, map view with pins, virtual tour embed
- Restaurant/food → menu builder, table reservation grid, order queue
- HR/people → org chart, leave calendar, employee directory with photos
- E-commerce → product grid with filters, shopping cart, order tracking timeline
- Project management → Kanban board, Gantt timeline, sprint view
- Education/LMS → course progress bars, video player embed, quiz builder
- Social/community → feed/timeline, comments, user profiles, notifications

For EACH must-have you identify:
1. feature_name: [name]
2. component_type: [map/calendar/kanban/chart/gallery/timeline/table/form/...]
3. implementation: [specific library or custom component + how to render with mock data]
4. data_needed: [what mock data this feature needs]
5. why_essential: [1 sentence — why users expect this]

Do NOT skip this section. If the domain has map, calendar, kanban, timeline, gallery, or other
specialized views that ALL real competitors have — they MUST be listed here.

REMEMBER: Every single value must come from your internet research of real products.
Do NOT invent values or use generic defaults. Study the real UIs and extract exactly what they use.
"""
    else:
        # LANDING PAGE / WEBSITE research prompt
        research_prompt = f"""
You are a SENIOR UI/UX ARCHITECT. Your job: search the internet, study the TOP 3-5 real websites 
similar to "{description}", then output a CODE-READY BLUEPRINT for a code generator.

IMPORTANT RULES:
- EVERY value must come from your actual internet research — never invent or use defaults
- Study the real websites' actual design, colors, sections, copy, layout patterns
- Synthesize the BEST patterns from ALL sites you analyze into one unified design
- Output must be structured and specific — a code generator will parse this directly
- No paragraphs, no explanations — only structured data

UI/UX STANDARDS TO APPLY (on top of competitor research):
- Follow 8px spacing grid (section padding, card gaps, element margins — all multiples of 8)
- Ensure WCAG AA contrast ratio (4.5:1 text, 3:1 large text) for all color pairings
- Use a proper type scale (hero 48-72px, section headings 28-36px, body 16-18px, small 12-14px)
- Apply F-pattern and Z-pattern reading flow for content sections
- Use visual hierarchy: hero = maximum impact, each section decreases in visual weight
- Ensure CTA buttons are visually dominant (size, color contrast, whitespace around them)
- Apply the rule of thirds for hero layout balance
- Use consistent section rhythm: alternate between content-heavy and visual-break sections
- Mobile-first responsive: stack columns, increase touch targets, simplify navigation
- Apply Gestalt principles: proximity for grouping, similarity for related items, contrast for emphasis

OUTPUT THIS EXACT STRUCTURE (fill ALL values from your research):

===WEBSITES_ANALYZED===
1. [Website name] ([URL]) — [what makes their design excellent]
2. [Website name] ([URL]) — [what makes their design excellent]
3. [Website name] ([URL]) — [what makes their design excellent]

===CSS_VARIABLES===
(Extract the dominant color palette from the best website you analyzed.
Convert to HSL. Every value must come from actual sites you studied.)
--primary: [hsl value from research]
--primary-foreground: [hsl value]
--secondary: [hsl value]
--secondary-foreground: [hsl value]
--background: [hsl value]
--foreground: [hsl value]
--muted: [hsl value]
--muted-foreground: [hsl value]
--accent: [hsl value]
--accent-foreground: [hsl value]
--card: [hsl value]
--card-foreground: [hsl value]
--border: [hsl value]
--ring: [hsl value]
--radius: [value]rem

===DARK_MODE===
is_default: [true/false — based on what the best sites in this space use]
(if dark is default, provide light overrides; if light is default, provide dark overrides)

===FONTS===
(Use the exact fonts from the best-looking site you analyzed)
heading: [font name] ([Google Fonts URL with weights])
body: [font name] ([Google Fonts URL with weights])
hero_size: [size from research] / [tracking] / [weight]
section_heading: [size] / [weight]
body_size: [size] / [line-height]

===HERO_GRADIENT===
(Based on the hero treatment of the best site you analyzed)
classes: [exact Tailwind gradient classes]
overlay: [overlay classes if applicable]
pattern: [background pattern if used]

===HEADER_NAVIGATION===
(Based on the real sites' actual navigation)
logo: [Brand name for this project]
items: [nav items based on what real sites in this space include]
cta: "[CTA text from research]" → [button style]
sticky: [true/false]
blur_bg: [true/false]

===FOOTER===
(Based on real sites' footer structure)
columns:
  - title: "[column title]" | links: [relevant links for this domain]
  - title: "[column title]" | links: [relevant links]
  - title: "[column title]" | links: [relevant links]
  - title: "[column title]" | links: [relevant links]
bottom: "[copyright text]"
social: [social platforms relevant to this domain]

===SECTIONS===
(List EVERY section the landing page should have, based on what the real sites include.
Order matters. Add as many sections as the best sites have — NO LIMIT.)

[section: hero]
headline: "[Compelling headline — write original copy inspired by the best sites' tone]"
subheadline: "[Value proposition — 1-2 sentences, original copy in the same style]"
cta_primary: "[Button text from research]" | icon: [LucideIcon] | style: [from research]
cta_secondary: "[Button text]" | icon: [LucideIcon] | style: [from research]
layout: [layout pattern from the best site]
background: [exact background treatment]
animation: [animation pattern from research]

[section: social_proof]
headline: "[social proof headline]"
logos: [6 company names relevant to this domain]
style: [exact rendering style from research]

[section: features]
headline: "[section headline — original copy]"
subheadline: "[intro text]"
layout: [layout from the best site]
features:
  1. title: "[feature name relevant to this domain]" | icon: [LucideIcon] | description: "[1 sentence]"
  2. title: "[feature]" | icon: [LucideIcon] | description: "[1 sentence]"
  (list as many features as the real sites showcase — typically 4-8)
card_style: [exact Tailwind classes from research]

[section: how_it_works]
headline: "[section headline]"
layout: [layout from research]
steps:
  1. title: "[step]" | description: "[how it works for this domain]" | icon: [LucideIcon]
  (as many steps as the real sites show)

[section: pricing]
headline: "[section headline]"
subheadline: "[text]"
tiers:
  1. name: "[tier name]" | price: "[price]" | period: "[billing]" | cta: "[text]" | features: [list]
  2. name: "[tier]" | price: "[price]" | period: "[billing]" | cta: "[text]" | badge: "[if popular]" | features: [list] | highlighted: true
  3. name: "[tier]" | price: "[price]" | period: "[billing]" | cta: "[text]" | features: [list]

[section: testimonials]
headline: "[section headline]"
layout: [layout from research]
testimonials:
  1. quote: "[realistic testimonial for this domain — 2-3 sentences]" | name: "[name]" | title: "[job title]" | company: "[company]" | avatar: https://i.pravatar.cc/150?u=[unique] | rating: [N]
  2. (provide 3-4 testimonials)

[section: faq]
headline: "[headline]"
layout: [layout from research]
items:
  1. q: "[real question people ask about this type of product]" | a: "[helpful answer]"
  (provide 5-8 FAQs relevant to this domain)

[section: cta_banner]
headline: "[final CTA headline]"
subheadline: "[supporting text]"
cta: "[button text]" | icon: [LucideIcon]
background: [exact Tailwind classes]

(add MORE sections if the best sites in this space have them — stats, integrations, comparison tables, team, blog preview, etc.)

===ADDITIONAL_PAGES===
about: [describe content based on what real sites include]
pricing: [expanded pricing with comparison table]
contact: [form fields + info cards based on real sites]
blog: [listing layout based on real sites]

===UI_PATTERNS===
(Extract exact visual patterns from the best site you analyzed)
card: [exact Tailwind classes]
button_primary: [exact Tailwind classes]
button_ghost: [exact Tailwind classes]
section_spacing: [spacing pattern]
max_width: [max-width pattern]
scroll_animation: [animation approach from research]
hover_cards: [hover effect classes]
overall_vibe: [2-3 words]

===AVATAR_SOURCES===
testimonials: https://i.pravatar.cc/150?u=[unique_per_person]
team: https://i.pravatar.cc/300?u=team[N]
hero_image: [how the best sites handle hero visuals — gradient, pattern, illustration?]
feature_icons: Lucide React (specify exact icon name per feature above)

===DOMAIN_MUST_HAVES===
(Study the real websites and identify features/sections that are ESSENTIAL for this domain.
If a site in this space doesn't have these, visitors won't trust or convert.
List every must-have and specify how to implement it.)

Examples of what to look for — but discover dynamically from your research:
- SaaS/tool → interactive product demo or screenshot showcase, integration logos grid
- E-commerce site → product showcase carousel, trust badges, shipping info bar
- Fintech → ROI calculator, security trust badges, compliance logos
- Healthcare → appointment booking widget, doctor profiles, insurance logos
- Education → course catalog preview, student success stats, accreditation
- Real estate → property search with filters, neighborhood map, mortgage calculator
- Restaurant → menu preview, reservation widget, photo gallery
- Agency/portfolio → case study carousel, client logos, results metrics
- Marketplace → category showcase, seller highlights, trust/safety section

For EACH must-have you identify:
1. feature_name: [name]
2. section_type: [calculator/carousel/showcase/widget/search/map/...]
3. implementation: [how to build it with React + Tailwind + framer-motion]
4. data_needed: [what mock data to include]
5. why_essential: [1 sentence — why visitors expect this]

Do NOT skip this section. If the BEST sites in this space all have a specific section
or interactive element, it MUST be listed here.

REMEMBER: Every single value must come from your internet research of real websites.
Do NOT invent values or use generic defaults. Study the real designs and extract exactly what they use.
Write original copy inspired by the TONE and STYLE of the best sites — never copy their exact text.
"""
    
    # Call Gemini with internet search enabled
    import google.generativeai as genai
    genai.configure(api_key=gemini_key)
    
    model = genai.GenerativeModel("gemini-2.5-flash")
    response = await asyncio.to_thread(
        model.generate_content,
        research_prompt,
        generation_config=genai.GenerationConfig(
            temperature=0.7,
            max_output_tokens=4000,
        ),
        tools="google_search",
    )
    
    await _ws_send(websocket, "progress", "✅ Research complete — building project blueprint...")
    return response.text
```

---

### STEP 7 — `generate_new_project()` — 3-Phase Multi-Call Orchestrator

```python
async def generate_new_project(
    description: str,
    workspace_path: str,
    validated: dict,
    websocket,
    chat_session_id: str = "",
) -> bool:
    """3-phase multi-call orchestrator for new project generation.
    
    Uses Claude Opus 4.6 (120K+ output tokens) across 3 sequential calls:
      Call 1: Foundation (theme, config, nav, layouts, main page, router)
      Call 2: Content (all sections OR all CRUD features — NO LIMIT)
      Call 3: Extra pages + polish (about, contact, settings, etc.)
    
    Between each call, the file tree is rebuilt so imports resolve correctly.
    The template is ALREADY cloned into workspace_path by Phase 2.
    """
    api_key = validated["anthropic_api_key"]
    gemini_key = validated["gemini_api_key"]
    stack = validated.get("project_stack", "") or validated.get("skeleton_stack", "")
    
    MODEL = "claude-opus-4-6"
    MAX_TOKENS = 32000  # Per call (3 calls = 96K total capacity)

    # ── Step 1: Classify app type ──
    from knowledge.loader import classify_project_type
    app_type = classify_project_type(description)
    
    # ── Step 2: Read template manifest ──
    manifest = _read_manifest(workspace_path)
    file_tree = _build_file_tree(workspace_path)
    
    # Detect stack-specific rules
    if "next" in stack or "nextjs" in stack:
        stack_rules = NEXTJS_WEBSITE_RULES
    elif "vue" in stack:
        stack_rules = VUE_ADMIN_RULES
    else:
        stack_rules = REACT_ADMIN_RULES
    
    # ── Step 3: Gemini ULTRA-DEEP research ──
    await _ws_send(websocket, "progress", "🔬 Researching real products in this domain...")
    research = await gemini_deep_research(
        description, app_type, stack, gemini_key, websocket,
    )
    
    total_files = []
    
    # ═══════════════════════════════════════════════════════
    #  CALL 1 — FOUNDATION
    #  Theme, config, navigation, layouts, main page, router
    # ═══════════════════════════════════════════════════════
    await _ws_send(websocket, "progress", "🏗️ Phase 1/3 — Building foundation...")
    
    phase1_prompt = f"""PHASE 1 OF 3 — FOUNDATION FILES ONLY

Generate ONLY these foundation files (Phases 2 and 3 will handle sections/features/extra pages):

1. CSS THEME FILE — Complete :root block with ALL researched colors + dark mode + custom tokens:
   - All standard tokens: --primary, --secondary, --accent, --background, --foreground, etc.
   - Custom tokens: --chart-1 through --chart-5, --success, --warning, --info
   - Border radius, ring offset, sidebar colors
   - Import BOTH Google Fonts (heading + body) via @import url()

2. SITE CONFIG — Brand name, tagline, meta description, URL, social links

3. NAVIGATION CONFIG — Full domain-specific navigation with Lucide icon names, grouping, badges

4. LAYOUT COMPONENTS — FULLY REWRITE Sidebar/Header/Footer for this project's unique design:
   - Sidebar: custom width, brand colors, logo area, nav groups, user profile area
   - Header: brand-specific, search bar style, notification bell, user avatar
   - Footer: project-specific columns, links, social icons
   (DO NOT copy template defaults — create a UNIQUE layout matching the research)

5. MAIN PAGE:
   - Landing: page.js that imports section components (sections come in Phase 2)
   - Admin: DashboardPage with KPI cards, charts (recharts/vue-chartjs), activity table
     Include 4 KPI cards with realistic values, 2 charts with 12+ data points, 8-row activity table

6. ROUTER — Add routes for ALL planned pages/features (pages themselves come in Phase 2-3)

PROJECT: {description}
APP TYPE: {app_type}
STACK: {stack}

DESIGN SYSTEM FROM RESEARCH:
{research}

TEMPLATE MANIFEST:
{manifest[:4000]}

FILE TREE:
{file_tree[:2000]}

{stack_rules}

RESPOND WITH JSON ONLY: {{"files": [{{"path": "...", "content": "..."}}]}}
"""
    
    result1 = await call_claude_for_json(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=phase1_prompt,
        api_key=api_key,
        websocket=websocket,
        max_tokens=MAX_TOKENS,
        model=MODEL,
    )
    if result1:
        written = write_files_from_json(result1, workspace_path)
        total_files += written
        await _ws_send(websocket, "progress", f"✅ Foundation: {len(written)} files")
    else:
        await _ws_send(websocket, "error", "❌ Phase 1 failed")
        return False
    
    # Rebuild file tree for Phase 2
    file_tree_2 = _build_file_tree(workspace_path)
    
    # ═══════════════════════════════════════════════════════
    #  CALL 2 — CONTENT
    #  All sections (landing) OR all CRUD features (admin)
    # ═══════════════════════════════════════════════════════
    await _ws_send(websocket, "progress", "🎨 Phase 2/3 — Building content...")
    
    if app_type in ("admin_panel", "ecommerce", "saas_app", "analytics"):
        phase2_instruction = """Generate ALL CRUD feature modules.
For EACH entity from the research, create the COMPLETE feature folder:
  - services/[entity].service.js — API calls + MOCK DATA FALLBACK (10-20 realistic rows)
  - hooks/use[Entity].js — React Query / Vue Query wrappers
  - pages/[Entity]ListPage — DataTable with columns, actions, filters, search, pagination
  - pages/[Entity]FormPage — Full form with validation (react-hook-form + zod / vee-validate + zod)

Create as many entities as the research specifies — NO LIMIT.
Every service must include realistic mock data with real names, numbers, dates, statuses.
Every list page must have column definitions, status badges, action buttons.
Every form must have proper field types, placeholders, and validation rules."""
    else:
        phase2_instruction = """Generate ALL section components for the landing page.
Create EVERY section the research specifies — NO LIMIT.
Typical sections: Hero, Logos/SocialProof, Features, HowItWorks, Pricing, Testimonials, FAQ, CTA, Stats.

Each section must be:
- A complete, self-contained component
- Fully responsive (mobile-first: sm: md: lg: xl:)
- Animated with framer-motion (fade-up on scroll, hover effects)
- Using REAL domain-specific copy (not lorem ipsum)
- Using the EXACT design system from the research (colors, fonts, spacing)
- With realistic mock data (testimonials with i.pravatar.cc avatars, pricing with real USD)"""
    
    phase2_prompt = f"""PHASE 2 OF 3 — CONTENT FILES

The foundation is already built (see file tree below). DO NOT regenerate foundation files.
{phase2_instruction}

PROJECT: {description}
APP TYPE: {app_type}
STACK: {stack}

DESIGN SYSTEM FROM RESEARCH:
{research}

TEMPLATE MANIFEST:
{manifest[:4000]}

CURRENT FILE TREE (foundation already written):
{file_tree_2[:2000]}

{stack_rules}

RESPOND WITH JSON ONLY: {{"files": [{{"path": "...", "content": "..."}}]}}
"""
    
    result2 = await call_claude_for_json(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=phase2_prompt,
        api_key=api_key,
        websocket=websocket,
        max_tokens=MAX_TOKENS,
        model=MODEL,
    )
    if result2:
        written = write_files_from_json(result2, workspace_path)
        total_files += written
        await _ws_send(websocket, "progress", f"✅ Content: {len(written)} files")
    
    # Rebuild file tree for Phase 3
    file_tree_3 = _build_file_tree(workspace_path)
    
    # ═══════════════════════════════════════════════════════
    #  CALL 3 — ADDITIONAL PAGES + POLISH
    # ═══════════════════════════════════════════════════════
    await _ws_send(websocket, "progress", "✨ Phase 3/3 — Building additional pages...")
    
    phase3_prompt = f"""PHASE 3 OF 3 — ADDITIONAL PAGES + COMPLETENESS CHECK

The foundation and content are built (see file tree below). DO NOT regenerate existing files.

Generate:
1. ALL ADDITIONAL PAGES not yet created:
   - Landing: About (team, mission, stats), Pricing (expanded), Contact (form + info), Blog (listing)
   - Admin: Settings (profile/notifications/security tabs), Profile, Help/docs
   - Any other pages the research or navigation config references

2. COMPLETENESS CHECK — scan the current file tree and fix gaps:
   - Any navigation items that don't have corresponding pages → CREATE the page
   - Any imports in existing files that reference missing files → CREATE those files
   - Any placeholder sections in the main page that reference missing components → CREATE them
   - If router has routes to pages that don't exist → CREATE those pages

3. CUSTOM COMPONENTS (if needed by any page):
   - Kanban board, calendar view, timeline, progress tracker
   - Any specialized component not in shadcn/ui → CREATE from scratch with Tailwind

PROJECT: {description}
APP TYPE: {app_type}
STACK: {stack}

DESIGN SYSTEM FROM RESEARCH:
{research}

TEMPLATE MANIFEST:
{manifest[:4000]}

CURRENT FILE TREE (foundation + content already written):
{file_tree_3[:3000]}

{stack_rules}

RESPOND WITH JSON ONLY: {{"files": [{{"path": "...", "content": "..."}}]}}
"""
    
    result3 = await call_claude_for_json(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=phase3_prompt,
        api_key=api_key,
        websocket=websocket,
        max_tokens=MAX_TOKENS,
        model=MODEL,
    )
    if result3:
        written = write_files_from_json(result3, workspace_path)
        total_files += written
        await _ws_send(websocket, "progress", f"✅ Pages: {len(written)} files")
    
    await _ws_send(websocket, "progress", f"💾 Total: {len(total_files)} files generated")
    
    # ── Build verification ──
    build_ok = await verify_and_fix_build(
        workspace_path=workspace_path,
        api_key=api_key,
        websocket=websocket,
    )
    
    # ── Send file list to frontend ──
    try:
        all_files = []
        for root, dirs, fnames in os.walk(workspace_path):
            dirs[:] = [d for d in dirs if d not in {"node_modules", ".git", ".next"}]
            for f in fnames:
                all_files.append(os.path.relpath(os.path.join(root, f), workspace_path))
        await websocket.send_json({
            "type": "file_change",
            "files": sorted(all_files),
        })
    except Exception:
        pass
    
    return True
```

---

### STEP 8 — Update `task_pipeline.py`

In `run_pipeline()`, replace the Phase 5 code for `scratch_mode` and `new_project_mode`:

```python
# ── Phase 5: Execute with Claude ──────────────────
await _send_phase(5, "Writing code", f"Generating project…", "active")

if validated.get("scratch_mode") or validated.get("new_project_mode"):
    # NEW: Direct Claude API generation (no SDK)
    from app.services.project_generator import generate_new_project
    
    success = await generate_new_project(
        description=task,
        workspace_path=workspace_path,
        validated=validated,
        websocket=websocket,
        chat_session_id=chat_session_id,
    )
    
    if not success:
        await _send_phase(5, "Writing code", "Generation failed", "error")
        return

else:
    # EXISTING: Claude Code SDK for edit-mode (unchanged)
    success = await execute_with_claude(...)
```

---

### STEP 9 — The Dynamic Generation Prompt

`build_generation_prompt()` returns a (system_prompt, user_prompt) tuple.

**System Prompt (shared across all stacks):**

```
You are a SENIOR FRONTEND ENGINEER at a top product company (like Linear, Stripe, or Vercel).
You build production-grade web applications that real users pay money for.

Your job: transform a template into a FULLY WORKING, DOMAIN-SPECIFIC application.
The template gives you infrastructure (routing, auth, UI components, layouts).
You must REPLACE all demo content with real, production-quality, domain-specific content.

OUTPUT FORMAT — respond with a single JSON object:
{
  "files": [
    {"path": "src/components/sections/HeroSection.jsx", "content": "full file content..."},
    {"path": "src/config/navigation.js", "content": "..."},
    ...
  ],
  "env": {
    "VITE_API_URL": "http://localhost:3000",
    "VITE_APP_NAME": "ProjectName"
  }
}

CRITICAL RULES:
1. Return ONLY valid JSON — no markdown, no explanation, no code fences
2. Every file must have "path" (relative) and "content" (COMPLETE file source code)
3. REPLACE all template demo pages — no generic "Welcome" or "Sample" content should remain
4. Use the EXACT design system from the research context (colors, fonts, spacing)
5. Write REAL content: actual headlines, real feature descriptions, realistic data, proper metrics
6. Every page must be logically complete and fully functional (with mock data)
7. Admin panels: every CRUD entity needs list page (with DataTable), form page, service, hooks
8. Landing pages: every section must have compelling, domain-specific copy and visuals
9. Responsive: every component must work sm → xl breakpoints
10. Include ALL files that need to change — pages, configs, styles, features, navigation
11. Mock data must be REALISTIC — real names, real numbers, proper formatting
12. Use framer-motion for page transitions and micro-animations
13. Charts must have realistic data series (12 data points minimum)
```

**User Prompt (dynamic per project):**

```
PROJECT: {description}
APP TYPE: {app_type}
STACK: {stack}

DESIGN SYSTEM & FULL SPECIFICATION FROM RESEARCH (follow this EXACTLY):
{research_context}

TEMPLATE MANIFEST (infrastructure that already exists — use it, don't recreate):
{manifest[:4000]}

WORKSPACE FILE TREE:
{file_tree[:2000]}

{stack_specific_rules}

FILES YOU MUST INCLUDE:
1. CSS theme file — update :root variables with the EXACT researched color system
2. site.js / config — brand name, description, tagline from the research
3. navigation.js — domain-specific nav items with EXACT Lucide icons from the research
4. ALL page files — fully rewritten with domain content (not template defaults) 
5. ALL section components (landing) OR ALL feature modules (admin CRUD)
6. Dashboard with REAL KPI cards, charts with mock data, recent activity table
7. Router updates — add routes for all new pages/features

QUALITY REQUIREMENTS:
- Every KPI card must show a realistic value, % change, and sparkline or icon
- Every table must have 8-10 rows of realistic mock data (real names, real numbers)
- Every chart must have 12+ data points with realistic values
- Every form must use react-hook-form + zod validation with proper field types
- Every section must have compelling, specific copy (NOT generic lorem ipsum)
- Hover effects on all interactive elements (cards, buttons, rows)
- Smooth page transitions with framer-motion
- Status badges with semantic colors (green=active, yellow=pending, red=inactive)

The result must look and feel like a REAL PRODUCT built by a $50M funded startup.
A user should open this and say "this is a real app" — not "this is a template."
```

---

## WHAT CHANGES vs. WHAT STAYS THE SAME

### STAYS THE SAME (no changes needed):
- Phase 1: `validate_inputs()` — wizard header parsing, template resolution
- Phase 2: Template cloning from GitHub
- Phase 3: `generate_claude_md()` — CLAUDE.md generation
- Phase 7: Commit + create GitHub repo + push
- Phase 8: Vercel deployment (if configured)
- `knowledge/loader.py` — project type classification, pattern loading
- `knowledge/patterns/*.md` — architecture pattern files
- Frontend export API (`/api/export-code/route.js`)

### CHANGES:
- **Frontend: `NewProjectWizard.js`** — Default stack to `'auto'`, "Choose for me" at top with ✨ Recommended
- **Frontend: `recommend-stack/route.js`** — Add Vue to recommendations
- **Model**: `claude-opus-4-6` (120K+ output tokens) replaces `claude-sonnet-4-6`
- **Generation**: 3-phase multi-call replaces single call
- **Layouts**: Fully unlocked — Claude rewrites Sidebar/Header/Footer per project
- **Phase 4**: Replace `gemini_research()` with `gemini_deep_research()` (internet search, full blueprint)
- **Phase 4b**: Skip `gemini_create_plan()` (no longer needed)
- **Phase 5**: Replace `execute_project_in_batches()` with `generate_new_project()`
- **Phase 6**: Replace `BuildValidator` (SDK-based) with `verify_and_fix_build()` (Opus API-based)

### NEW FILE:
- `ai_engine/app/services/project_generator.py` — all new generation logic

### TEMPLATE REPO CHANGES (one-time):
- `lucid-template-nextjs-website` — add `recharts` + `framer-motion`
- `lucid-template-react-admin` — add `framer-motion`
- `lucid-template-vue-admin` — add `chart.js` + `vue-chartjs`

---

## ZERO LIMITS

| Aspect | Old System | New System |
|--------|-----------|------------|
| Pages per project | ~8-10 (token limit) | **Unlimited** — Gemini decides, 3 calls deliver |
| Sections per landing | ~6-8 | **Unlimited** — as many as research recommends |
| CRUD entities per admin | ~3 (truncation) | **Unlimited** — 5-7 typical, up to 10+ |
| Total files | ~15-20 | **30-50+** across 3 calls |
| Layout structure | Fixed template layouts | **Fully rewritable** — unique per project |
| UI components | shadcn/ui only | **shadcn/ui + custom** — Claude creates new ones |
| Typography | Single font | **Font pair** — heading + body fonts |
| Mock data rows | 3-5 rows | **10-20 rows** per table, realistic |
| Color system | Fixed token names | **Extensible** — new CSS vars allowed |
| Service layer | Hardcoded mock only | **API-ready** with mock data fallback |
| Chart data | Minimal | **12+ data points** per chart |
| Animations | None | **framer-motion** throughout |

---

## COMPLETE NEW FLOW

```
Phase 0: User opens wizard
  → "Choose for me" is pre-selected (default)
  → User types project description
  → Gemini/Claude decides best stack (or user overrides manually)

Phase 1: Validate inputs                ~0.5 seconds
Phase 2: Clone template from GitHub      ~3-5 seconds
Phase 3: Generate CLAUDE.md              ~0.5 seconds
Phase 4: Gemini ULTRA-DEEP research      ~15-25 seconds

Phase 5: Claude OPUS 4.6 — 3-phase generation:
  Call 1: Foundation (theme, config, layouts) ~30-60 seconds
  Call 2: Content (sections OR features)      ~45-90 seconds
  Call 3: Extra pages + polish               ~30-60 seconds

Phase 6: Write files after each call     ~1-2 seconds (incremental)
Phase 7: npm install + build verify      ~30-60 seconds
Phase 8: Auto-fix with Opus (if needed)  ~30-60 seconds
Phase 9: Commit + create repo + push     ~10-15 seconds

TOTAL: 4-7 minutes
COST:  $2-4 (3× Opus calls + 1 Gemini call)
FILES: 30-50+ fully customized, unique files
BUILD: Verified before push
LIMITS: NONE
```

---

## COMPARISON

```
CURRENT (Claude Code SDK, file-by-file):
  Time:     15-42 minutes       ❌
  Cost:     $3.00-7.00          ❌
  Calls:    22+ Claude SDK      ❌
  Crash:    SDK exit code 1     ❌
  Build:    Often fails         ❌
  Research: Shallow             ❌
  Design:   Generic colors      ❌
  Content:  Lorem ipsum         ❌
  Layout:   Same for all        ❌
  Limits:   8-10 pages max      ❌

NEW (Claude Opus 4.6, 3-phase multi-call, ultra-deep research):
  Time:     4-7 minutes         ✅
  Cost:     $2-4                ✅ (still less than current $3-7)
  Calls:    3 Opus API          ✅
  Crash:    No SDK = no crash   ✅
  Build:    Verified            ✅
  Research: Real products       ✅
  Design:   Premium (HSL pair)  ✅
  Content:  Real data           ✅
  Layout:   Unique per project  ✅
  Limits:   NONE                ✅
```

---

## IMPLEMENTATION ORDER

```
Phase A — Template prep (30 min):
  □ Add recharts + framer-motion to lucid-template-nextjs-website
  □ Add framer-motion to lucid-template-react-admin
  □ Add chart.js + vue-chartjs to lucid-template-vue-admin
  □ Push all template changes

Phase B — Frontend changes (1 hour):
  □ Update NewProjectWizard.js — default stack to 'auto', move to top
  □ Update recommend-stack/route.js — add Vue support

Phase C — Core generator (Day 1):
  □ Create project_generator.py with:
    - gemini_deep_research() with ultra-deep prompts
    - call_claude_for_json() with model="claude-opus-4-6"
    - write_files_from_json() with UNLOCKED layouts
    - build_phase1_prompt() — foundation
    - build_phase2_prompt() — content (sections OR features)
    - build_phase3_prompt() — additional pages + completeness check
    - verify_and_fix_build() with Opus
    - generate_new_project() — 3-phase orchestrator
  □ Wire into task_pipeline.py Phase 5

Phase D — End-to-end testing (Day 2):
  □ Test: "food delivery landing page" → nextjs (auto-selected)
  □ Test: "logistics CRM with shipments, drivers, routes, warehouses" → react (auto)
  □ Test: "inventory management" → vue (manual)
  □ Test: "SaaS project management tool like Linear" → nextjs (auto, dark mode)
  □ Verify each build passes
  □ Verify UNIQUE layout per project (not same sidebar everywhere)
  □ Verify 10+ rows mock data per table
  □ Verify 12+ chart data points
  □ Verify font pair applied (heading + body)
  □ Verify API-ready services with mock fallback
  □ Measure cost + time per project

Phase E — Polish (Day 3):
  □ Handle edge cases (empty responses, truncated JSON)
  □ Add progress streaming (% updates per phase)
  □ Test export flow works after generation
  □ Deploy to production
```
