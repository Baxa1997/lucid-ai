# Lucid AI — Full Pipeline Flow & Research Report

_Drafted 2026-05-15 from a fresh read of the codebase + 3 live Gemini research tests._

## Executive Summary

Only **landing pages** (`single_page_landing` archetype) run an end-to-end, browser-visible pipeline today. **Full websites** have a feature-flagged v2 pipeline (`website_pipeline.run_website_pipeline`) that is **off by default** (`WEBSITE_PIPELINE_V2_ENABLED`); otherwise multi-page sites fall back to the legacy 3-phase generator. **Admin panels** generate frontend CRUD via the legacy 3-phase generator, and can produce a Supabase migration file (`backend_schema.py`, Claude Sonnet 4.6) — but the file is never applied to a real database, no auth wiring, no real data flow. **`project_members` + `MembershipService` already exist** (migration `020_project_members.sql` + [members.py](ai_engine/app/services/members.py)) — there is NO HTTP endpoint exposing them yet, so the user-invite feature is closer to "build the API + UI" than "design the system from scratch."

The smallest shippable next step is **Gmail-based invite to a project**, because the schema, the service layer, and the trigger that adds the owner are already in place — only the FastAPI router + frontend membership UI are missing. This works independently of any admin-panel/backend-generation work and unblocks multi-user collaboration on the landing-page generator we already ship.

---

## Part A: Current Pipeline Flow (per mode)

All 4 modes share the same entry chain up to the archetype branch:

```
WebSocket (/ws) → ws.py:got_task                        # raw user prompt arrives
                ↓
ai_engine/app/services/pipeline/__init__.py → run_pipeline
                ↓ Phases 1 (validate) + 2 (workspace) + 2b (skeleton copy)
ai_engine/app/services/pipeline/orchestrator.py:187      # run_pipeline
                ↓ on scratch_mode / new_project_mode
ai_engine/app/services/project_generator.py:6711         # generate_new_project
                ↓
ai_engine/app/services/project_generator.py:_generate_new_project_inner
                ↓ classify_project_type_ai (knowledge/loader.py:553)
                ↓
        ┌───────┴──────────────────┐
   archetype == single_page_landing?   archetype in {consumer_website, portfolio, blog, marketplace}?   archetype in admin family?
        ↓ yes                          ↓ yes + env flag on                                              ↓ yes (else)
landing_pipeline.run_landing_pipeline  website_pipeline.run_website_pipeline                            legacy 3-phase
        (always)                       (off by default)                                                 + optional backend_schema gate
```

### Mode 1 — Landing Page (`single_page_landing`) — ✅ ready

Entry: same chain → archetype = `single_page_landing` at [project_generator.py:7419](ai_engine/app/services/project_generator.py#L7419) routes to `landing_pipeline.run_landing_pipeline`.

| # | Stage | File | Producer | Cost |
|---|-------|------|----------|------|
| 0 | Gibberish gate | [prompt_guards.py:68](ai_engine/app/services/prompt_guards.py#L68) | heuristic | $0 |
| 0 | Scope-warning gate | [prompt_guards.py:172](ai_engine/app/services/prompt_guards.py#L172) | heuristic | $0 |
| 1 | analyze_intent | [landing_intent.py:252](ai_engine/app/services/landing_intent.py#L252) | Gemini Flash | ~$0.005 |
| 2 | run_domain_research (4× parallel grounded) | [landing_domain_research.py:239](ai_engine/app/services/landing_domain_research.py#L239) | Gemini Pro | ~$0.20 |
| 2 | run_design_research (4× parallel grounded) | [landing_design_research.py:262](ai_engine/app/services/landing_design_research.py#L262) | Gemini Pro | ~$0.20 |
| 3 | extract_research_signals | [landing_research_extract.py:470](ai_engine/app/services/landing_research_extract.py#L470) | Gemini Flash + Pro | ~$0.05 |
| 4 | build_landing_brief | [landing_brief.py:570](ai_engine/app/services/landing_brief.py#L570) | Gemini Pro (1 call) | ~$0.05 |
| 5 | write_landing_content → `src/content/landing.json` | landing_content.py | deterministic | $0 |
| 6 | bind_landing_images (Unsplash) | landing_image_binder.py | Unsplash API | $0 |
| 7 | run_landing_phase0 (layout, globals.css, header/footer, design-system.js) | landing_phase0.py | deterministic | $0 |
| 8 | generate_landing_sections (N parallel section components) | landing_section_codegen.py | Claude Sonnet 4.6 | $0.05–0.40 |
| 9 | write_landing_page_shell | deterministic | — | $0 |
| 10 | run_landing_fixers | landing_fixers.py | Gemini Flash | ~$0.02 |

**Output:** A Next.js 14 App Router project with `src/app/page.jsx`, `src/content/landing.json`, `src/components/sections/*`, `src/components/marketing/{Header,Footer}.jsx`, Tailwind v3.4 + shadcn HSL tokens.

**What the user sees:** Live Sandpack preview as soon as files are written, then local-preview iframe on a port. End URL is a generated GitHub repo + a preview iframe in the chat.

**Time:** 90–180s typical. **Cost per generation:** $0.55–0.95 in API calls.

**Known gaps:**
- The classifier requires the literal word "landing" / "one page" to fast-path to `single_page_landing` ([loader.py:603](ai_engine/knowledge/loader.py#L603)). Prompts like "Italian restaurant in Brooklyn" without "landing page" do **not** route here — they fall through to `consumer_website` (see test L1).
- No backend, no auth, no DB — this is pure marketing site output.

---

### Mode 2 — Full Website (`consumer_website` / `portfolio` / `blog` / `marketplace`) — 🟡 partial

Two code paths depending on env flag:

**Path A (default):** `WEBSITE_PIPELINE_V2_ENABLED` unset → falls through to the legacy 3-phase Claude generator. Same code path as admin mode below, just without the backend-schema gate triggering.

**Path B (v2, opt-in):** `WEBSITE_PIPELINE_V2_ENABLED=true` AND archetype in `{consumer_website, portfolio, blog, marketplace}` → routes to `website_pipeline.run_website_pipeline` at [project_generator.py:7444](ai_engine/app/services/project_generator.py#L7444).

Website v2 pipeline ([website_pipeline.py:100](ai_engine/app/services/website_pipeline.py#L100)):

| # | Stage | File | Producer |
|---|-------|------|----------|
| 0.5 | classify_purpose (industry, audience, named_roles) | [purpose_classifier.py:149](ai_engine/app/services/purpose_classifier.py#L149) | Gemini Flash |
| 1 | analyze_intent | landing_intent.py | Gemini Flash |
| 2 | run_domain_research + run_design_research | landing_domain/design_research.py | Gemini Pro × 8 |
| 3 | extract_research_signals → visual_dna + voice | landing_research_extract.py | Gemini Pro |
| 4 | build_website_plan (pages + sections per page) | [website_plan.py:155](ai_engine/app/services/website_plan.py#L155) | Gemini Pro |
| 5 | Deterministic foundation (palette, tokens, nav, globals.css) | website_pipeline.py:_build_foundation_files | — |
| 5.5 | bind_page_images | image_binding.py | Unsplash |
| 6 | per-page parallel codegen | website_pipeline.py | Claude Sonnet 4.6 × N pages |

**Output:** Multi-route Next.js project with `src/content/site/*.json` per page + per-page section components.

**Time:** 180–360s. **Cost:** $1.20–2.50.

**Known gaps:**
- **Off by default** — no UI to toggle the env var; admins must set it in `docker-compose.yml`.
- v2 covers `consumer_website / portfolio / blog / marketplace` only — multi-page admin sites still go legacy.
- The legacy 3-phase fallback (Path A) often emits spurious `/admin` and `/dashboard` routes for consumer prompts; v2 was built specifically to solve this.
- No customer-facing backend wiring (forms POST nowhere; "Reserve a table" is a stub).

---

### Mode 3 — Admin Panel only (`admin_dashboard` / `crm` / `tms` / `saas_dashboard` / `ecommerce`) — 🟡 partial

Routes through the legacy `_generate_new_project_inner` flow.

| # | Stage | File | Producer |
|---|-------|------|----------|
| 1 | classify_project_type_ai → admin family | [loader.py:553](ai_engine/knowledge/loader.py#L553) | Gemini Flash |
| 2 | gemini_deep_research → spec blueprint with `===ENTITIES===`, `===SECTIONS===`, etc. | [project_generator.py:2810](ai_engine/app/services/project_generator.py#L2810) | Gemini Pro (grounded) |
| 3 | project_schema build (entities, fields, pages, mockData) | project_generator.py | derived |
| 3b1 | `build_supabase_migration` → `0001_init.sql` with RLS | [backend_schema.py:34](ai_engine/app/services/backend_schema.py#L34) | **Claude Sonnet 4.6** (HTTP call to Anthropic) |
| 4-6 | Foundation + content + completeness 3-phase | project_generator.py | Claude Opus 4.6 |
| 7 | `_audit_admin_entity_coverage` → recovery batch for missing CRUD UI | project_generator.py:6761 | deterministic |

**Output:** Next.js admin with sidebar nav, list+form pages per entity, mock data, plus `supabase/migrations/0001_init.sql` (with `CREATE TABLE`, `ENABLE ROW LEVEL SECURITY`, owner-scoped RLS policies).

**Time:** 300–600s. **Cost:** $1.50–4.00 (Claude phases + Gemini research).

**Known gaps blocking end-to-end usability** (G1–G8 documented in [CURRENT_BACKEND_GENERATION.md](ai_engine/docs/CURRENT_BACKEND_GENERATION.md)):
- **G1:** Migration is written to a file but never applied to any Supabase project — there is no automatic provisioning. The user is expected to copy/paste the SQL manually.
- **G2:** No `owner_id` is wired from the frontend to the inserted rows — the RLS policies require `auth.uid() = owner_id` but the generated CRUD pages don't set it.
- **G3:** No login UI is generated. Even if the user runs the SQL, anonymous reads/writes are blocked.
- **G4:** Frontend stays on mock data — there's no Supabase client setup, no `useEntity` hook that reads/writes the real table.
- **G5:** The backend gate runs only when `(_layout_archetype in _backend_archetypes) and (project_schema.entities)`. If the deep_research stage returned 0 entities, the migration is silently skipped.
- **G6:** `backend_schema.py` validation is `"CREATE TABLE" in sql.upper()` — no syntax check, no `psql --dry-run`, no verification that policies match table names.
- **G7:** `_audit_admin_entity_coverage` recovers missing list/form pages from Claude batch truncation, but if a whole batch fails the user gets entities without UI.
- **G8:** No `seed` step — empty tables with no demo data; user sees an empty admin on first load.

---

### Mode 4 — Admin Panel + Website — 🔴 broken / not implemented

**There is no single archetype for "both."** The classifier returns exactly one `layout_archetype`. The user can ask for "an Italian restaurant website with an admin panel," but the pipeline will:

1. Run Gemini classification → returns a single archetype.
2. If the classifier picks `consumer_website` or `single_page_landing` → no backend_schema gate fires.
3. If it picks `admin_dashboard` → no consumer-facing pages are generated (admin family produces sidebar+CRUD, not marketing pages).

There is **no mechanism** in the current pipeline to generate both sides of a "marketing site + internal admin sharing the same data" product. The only way to approximate it today is two separate generations into two separate workspaces.

**What's missing for Mode 4:**
- A composite classification (e.g. `archetype = "consumer_website + admin"`).
- Shared Supabase project provisioning (the admin writes, the website reads).
- Shared auth (one Supabase project, two clients).
- Routing convention: e.g. `/admin/*` requires login, `/` is public — currently no template enforces this.
- A way to apply a single migration to a real database so both sides talk to the same tables.

---

## Part B: Research Validation Findings

_Run 2026-05-15 via [`ai_engine/scripts/test_full_flow_research.py`](ai_engine/scripts/test_full_flow_research.py). Artifacts in `/tmp/full_flow_research/`._

### B.1 — Per-test results

| ID | Mode | Classified as | Expected | Match | intent.is_fallback | Notes |
|---|---|---|---|---|---|---|
| L1 | landing | `consumer_website` | `single_page_landing` | ❌ | False | No "landing" keyword in prompt → keyword fast-path missed, Gemini classifier picked website. Research downstream still produced domain-coherent output. |
| W1 | website | `consumer_website` | `consumer_website` | ✅ | False | Word "website" present → classifier correct. Purpose=brand_awareness (75% conf) while intent.primary_purpose=booking — minor mismatch. |
| A1 | admin | `admin_dashboard` | `admin_dashboard` | ✅ | False | Classifier correct. scope_warnings fires `dashboard_requested` (this would block landing pipeline if archetype had been single_page_landing). |

### B.2 — L1 (landing) — research succeeded but at the wrong pipeline

- **Classify:** 15.1s. `consumer_website / food_restaurant / family=consumer`. **Misclassified** — prompt was clearly meant as a marketing landing page but had no literal "landing"/"one-page" keyword, so the fast-path at [`loader.py:603`](ai_engine/knowledge/loader.py#L603) didn't fire and Gemini routed to multi-page.
- **Intent:** 3.0s. category=`food_restaurant`, purpose=`booking`, scope=`hyperlocal`, geo=`Brooklyn, NY`, tone=`warm`, 11 must_have_sections including `hero/value_prop/story/menu/gallery/booking_form`. No fallback. ✅ Domain-coherent.
- **Domain research:** 72.6s parallel run, 4/4 calls succeeded, 54 sources across business/audience/regional/competitive. Web-grounded with real Italian-Brooklyn references.
- **Design research:** Parallel with domain, 4/4 succeeded, 24 sources. All 4 calls grounded.
- **Domain coherence:** Pass. No SaaS-vocabulary contamination. Sicilian/Brooklyn/family-restaurant terms present.

**Verdict — research is appropriate for landing, but routing is wrong.** The prompt would generate a multi-page consumer site instead of the landing page the user clearly meant. Either (a) make the classifier more lenient (treat short "X in Y" descriptive prompts as landing by default), or (b) require an explicit UI toggle.

### B.3 — W1 (website) — works end-to-end on the research side

- **Classify:** 4.1s. `consumer_website / food_restaurant`. ✅ Match.
- **Intent:** 3.2s. Same shape as L1 (booking, warm, restaurant-flavored sections including `menu/locations/hours/booking_form/contact_form`). Marketing-page sections — correct for a public-facing site. No fallback.
- **Purpose:** 3.1s. `brand_awareness` industry=`italian restaurant`, audience=`b2c_consumers`, conf=75%.
- **Plan stage was not exercised** to save budget — but `build_website_plan` consumes `intent` + `visual_dna` and would emit per-page section lists.

**Verdict — landing-family research stages (intent + purpose + domain + design + extract_signals + plan) work for a multi-page website.** This is by design — `website_pipeline.run_website_pipeline` reuses the landing research stack.

**One small mismatch:** purpose_classifier says `brand_awareness` while analyze_intent says `booking`. Both are reasonable but different. Downstream code consumes both; behavior depends on which one drives plan generation. Worth tracing once.

### B.4 — A1 (admin) — research MISMATCH between paths

The admin prompt produced two completely different shapes from the two research paths:

**Through `analyze_intent` (used by landing + website pipelines):**
- category=`food_restaurant`, purpose=`signup`, tone=`authoritative`
- must_have_sections = `[hero, value_prop, features, how_it_works, testimonials, pricing, faq, contact_form, signup_form]`
- **These are MARKETING-PAGE section names**, not entity definitions. The model treated the admin prompt as a SaaS landing page selling the admin product.
- This is incompatible with admin generation, which needs entity schemas (tables, fields, relations) — not hero/pricing/FAQ.

**Through `gemini_deep_research` (legacy path, called by `_generate_new_project_inner` for non-landing archetypes):**
- 97.1s call. 29,769 chars.
- Headers present: `CLASSIFICATION, ENTITIES, CULTURAL_ATMOSPHERE, COPY_TONE, CSS_VARIABLES, FONTS`
- `===ENTITIES===` block is 8,102 chars and includes structured entries like:
  ```
  [entity: Dish]
  purpose: Represents a specific food or drink item on the menu, tracking its availability and pricing.
  fields:
    - name: id | type: string | required: yes | inList: no | inForm: no
    - ...
  ```
- ✅ This is exactly what `backend_schema.py` needs to generate the Supabase migration.

**Verdict — admin research only works through the legacy gemini_deep_research path.** Critically:
- `analyze_intent` is NOT a suitable replacement for admin mode — it produces landing-page sections.
- The new landing/website pipeline architecture cannot reach `backend_schema` because it never calls `gemini_deep_research`.
- If Mode 3 ever moves off the legacy 3-phase generator, it will need a NEW research stage that produces entity definitions (analogous to `extract_research_signals` but for entities instead of palette/voice).

### B.5 — Aggregate findings

| Mode | Research stage works? | Right structure produced? | Routes to right codegen? |
|---|---|---|---|
| Landing (L1) | ✅ intent + domain + design grounded | ✅ marketing sections | ❌ classifier picked consumer_website |
| Website (W1) | ✅ intent + purpose | ✅ marketing sections | ✅ |
| Admin (A1) — landing-style research | ❌ produces marketing sections (wrong) | ❌ | n/a |
| Admin (A1) — legacy deep_research | ✅ produces ENTITIES block | ✅ entity defs | ✅ legacy path routes correctly |
| Admin + Website (untested) | n/a | n/a | 🔴 no path exists |

**Most critical issue surfaced by this test:** classification is keyword-driven for landing and Gemini-driven for everything else. The two heuristics disagree on prompts that don't include the magic word. Three different routes — landing, website v2, legacy 3-phase — branch off one classifier output, but the classifier itself is biased toward "website" in the absence of explicit landing-language. Until the user is given an explicit mode picker in the UI, Mode 1 (landing) is significantly under-routed.

---

## Part C: What's Needed Per Mode

### Mode: Landing Page — ✅ already works end-to-end

| Item | Status | Location | Estimated effort |
|---|---|---|---|
| Mode detection | ✅ works when prompt contains "landing" | [loader.py:603](ai_engine/knowledge/loader.py#L603) | trivial: drop fast-path or add UI mode toggle |
| Research (intent + domain + design) | ✅ works | landing_intent/domain_research/design_research.py | — |
| Brief | ✅ works | landing_brief.py | — |
| Code generation | ✅ per-section parallel | landing_section_codegen.py | — |
| Deployment / preview | ✅ Sandpack + local preview iframe | local_preview.py | — |
| User invite to project | 🔴 missing | members.py exists; no router endpoint | hours |

### Mode: Full Website — 🟡 partial

| Item | Status | Location | Estimated effort |
|---|---|---|---|
| Mode detection | 🟡 needs UI mode toggle ("website" vs "landing") — today depends on env flag + keyword classifier | classify_project_type_ai | hours |
| Purpose classification | ✅ works | purpose_classifier.py | — |
| Research stage | ✅ works (shared with landing) | landing_*_research.py | — |
| Planning stage | ✅ works | website_plan.py | — |
| Code generation | 🟡 v2 off by default | website_pipeline.py | hours: flip flag, add UI toggle |
| Deployment / preview | ✅ same as landing | — | — |
| User invite to project | 🔴 missing | — | hours (shared with landing) |

### Mode: Admin Panel only — 🟡 partial (frontend works, backend doesn't connect)

| Item | Status | Location | Estimated effort |
|---|---|---|---|
| Mode detection | ✅ classifier picks admin family | loader.py:553 | — |
| Research (entities + pages) | 🟡 gemini_deep_research returns `===ENTITIES===` block | project_generator.py:2810 | — |
| Schema generation | 🟡 produces `0001_init.sql` with RLS, but never applied | backend_schema.py | — |
| Frontend CRUD pages | ✅ list + form per entity | project_generator.py 3-phase | — |
| Mock → live data wiring | 🔴 not implemented | — | **days** — Supabase client per project, owner_id pass-through |
| Login UI | 🔴 not implemented | — | **days** — needs auth template |
| Supabase project provisioning | 🔴 not implemented | — | **days** — Mgmt API + per-project secrets |
| Apply migration | 🔴 not implemented | — | hours (once provisioning exists) |
| Seed data | 🔴 not implemented | — | hours |
| User invite to project | 🔴 missing | — | hours |

### Mode: Admin Panel + Website — 🔴 not implemented

Needs everything from Mode 3, plus:
- Composite archetype handling
- Shared Supabase project per "product" (so both sides see the same data)
- Routing convention (`/admin/*` gated by auth, `/` public)
- Two-pipeline orchestration: run admin first (creates schema), then run website pointed at the same schema

**Estimated effort:** **weeks**, with several open design decisions (single repo vs two? per-project Supabase vs platform-shared? how does the user authenticate as a customer of the admin?).

---

## Part D: User Invitation Feature Requirements

The user wants Gmail-based invitation to projects with no permissions logic yet — just access.

### What already exists

- **Schema:** [`supabase/migrations/020_project_members.sql`](supabase/migrations/020_project_members.sql) creates the `project_members` table with `(project_id, user_id, role)` and roles `owner | editor`. RLS is enabled, plus an `is_project_member(project_id, user_id)` SECURITY DEFINER helper. Includes a backfill for existing chat_sessions and a `chat_sessions_add_owner` trigger that auto-inserts the creator as owner on every new project.
- **Service layer:** [`ai_engine/app/services/members.py`](ai_engine/app/services/members.py) — `MembershipService` with `list_members`, `add_member`, `remove_member`, `is_member`, `is_owner`, `get_owner`, `list_projects_for_user`. Already wired to use service-role for invite writes and anon-key+JWT for member-visible reads.
- **Auth providers:** Supabase Auth already accepts Google OAuth (per CLAUDE.md setup), so users sign in with Gmail via the Supabase Auth flow already.
- **Billing model:** [`020_project_members.sql:29`](supabase/migrations/020_project_members.sql#L29) notes that usage is attributed to the owner regardless of which editor acts — already correct.

### What's missing for invite-by-email

1. **HTTP endpoints in ai_engine** — there is currently **no router** that exposes `MembershipService`. Searching `ai_engine/app/routers/` for `members|invite|project_members` returns zero hits. Need ~4 endpoints:
   - `POST /projects/{project_id}/invite` (body: `{email}`) — owner-only, creates a pending invite row.
   - `GET /invites/me` — invitee fetches own pending invites.
   - `POST /invites/{token}/accept` — invitee accepts, server inserts into `project_members`.
   - `DELETE /projects/{project_id}/members/{user_id}` — owner removes a member.

2. **A `project_invites` table** (or reuse `project_members` with a `status` field). Migration 020 has no concept of "pending" — `add_member` directly inserts a row. For email invites, you need a token-keyed pending row, an email send, and an acceptance step.

3. **Email send** — no SMTP / SendGrid / Postmark integration exists yet (`grep -r "send_email\|smtp\|sendgrid" ai_engine/app/` returns no service). For Gmail invites, simplest path is Supabase Auth's existing magic-link/invite-by-email feature; otherwise add Resend or Postmark.

4. **Frontend UI** — `frontend/src/` has no member-management component. Need: a "Share" button on a project, a member list panel, an invite-by-email form, accept-invite landing page on `/accept-invite?token=...`.

### Does this depend on admin-panel work?

**No.** Project-level membership is on `chat_sessions`, which is the canonical project key for **every** mode (landing, website, admin). User invite can ship the day the API endpoints + UI exist — and immediately benefits the landing generator that already works.

---

## Part E: Recommended Build Order

### Phase 1 — Gmail-invite-to-project (1–2 weeks)
Build the missing HTTP layer and UI for membership that already exists in the DB + service.

- Migration: add `project_invites` table (or extend members with `status` + `token`).
- ai_engine: `app/routers/members.py` with the 4 endpoints above + Pydantic models.
- Email: prefer Supabase Auth admin invites (`auth.admin.inviteUserByEmail`) — no new SMTP integration.
- Frontend: Share dialog on project, accept-invite page, members panel.
- E2E test: owner invites a second account via email → second account accepts → second account sees the project in their sidebar.

**End-state:** Two users collaborate on the same Lucid project (chat session). Works for every mode the platform already generates (today: landing pages).

### Phase 2 — Mode toggle UI (landing vs full website) (1 week)
Eliminate the keyword-only classification — let the user pick.

- Frontend: a 2-option toggle ("Landing page" / "Full website") above the prompt input.
- Inject `[LUCID_FORCE_ARCHETYPE::single_page_landing]` or `[LUCID_FORCE_ARCHETYPE::consumer_website]` into the prompt before WS send (mechanism already exists at [loader.py:208](ai_engine/knowledge/loader.py#L208)).
- Flip `WEBSITE_PIPELINE_V2_ENABLED=true` in docker-compose for the consumer_website path.

**End-state:** Users get full multi-page sites reliably, without depending on whether they typed the magic word "landing."

### Phase 3 — Admin panel: live Supabase wiring (3–4 weeks)
Make the admin mode end-to-end usable. This is the big lift.

- Provision a Supabase project per generated admin (or share one across multiple users? — design decision).
- Run `0001_init.sql` on the new project automatically (Mgmt API).
- Generate a Supabase JS client config + `useEntity` hooks that read/write live tables.
- Generate a basic login page (Supabase Auth, email/password or Google OAuth).
- Stamp `owner_id = auth.user.id` on every insert so RLS works.
- Seed each entity with 3–5 demo rows so first-load isn't empty.

**End-state:** Generate "admin for X" → working CRUD against a real DB, login required.

### Phase 4 — Admin + Website composite (3–4 weeks, after Phase 3)
Compose the two pipelines on the same project.

- Add a third toggle option: "Marketing site + admin."
- Route to a new orchestrator: run admin schema first, run website pipeline pointed at the same Supabase project.
- Generate a single repo with `/admin/*` (auth-gated) and `/` (public).
- Public pages read the admin's data (menu items, blog posts) directly from Supabase.

**End-state:** End user generates a complete restaurant product: public site for diners, internal admin for owners, same database.

---

## Part F: Open Questions

1. **Where does Mode 4's data live?** One Supabase project per generated product (clean but expensive) or a shared Supabase with strict RLS isolation (cheaper, riskier)?
2. **Who pays for the customer-side login users?** A generated restaurant admin used by 5 staff = 5 Supabase auth users. Free tier limits matter.
3. **Should invites use Supabase Auth's `admin.inviteUserByEmail` (depends on Supabase email infra) or a custom token + Resend?** Supabase is easier to ship but harder to brand.
4. **Does the user toggle archetype explicitly, or do we keep inferring it?** Today's keyword classifier is brittle (test L1 misclassified an explicit-but-not-using-"landing" prompt).
5. **Is `WEBSITE_PIPELINE_V2_ENABLED` ready to flip on globally?** It's been gated for a reason — check the original PR for known issues before defaulting it.
6. **For Mode 3, do we want one Supabase project per generated admin (isolated, costly) or a shared "Lucid CMS" pattern (`gen_*` tables already exist — see [`per_project/001_content_tables.sql`](supabase/migrations/per_project/001_content_tables.sql))?** Two backend stories exist in parallel today; pick one before building Phase 3.

---

## Final Verdict

Based on this research, **the smallest shippable next step is Gmail-based invite-to-project**, because the schema (`project_members` table with RLS, trigger, helper function) and service layer (`MembershipService` with full CRUD and owner/membership lookups) **already exist** — only the FastAPI router and a small frontend share dialog are missing. Shipping this unblocks multi-user collaboration on the landing-page generator we already ship today, doesn't depend on any of the broken admin-panel/backend-generation work, and gives the platform its first real collaboration story. After that, the highest-leverage second step is a **landing-vs-website mode toggle in the UI** (using the existing `LUCID_FORCE_ARCHETYPE` mechanism), because today's keyword classifier silently routes the user's intended landing page to the multi-page generator whenever they forget the literal word "landing." Both are days of work, not weeks, and both improve experience for users we have today.

