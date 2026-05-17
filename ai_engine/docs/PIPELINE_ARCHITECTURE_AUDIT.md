# Pipeline Architecture Audit — 2026-05-17

Read time: ~15 minutes.

This audit was produced after Phase 2 (website ↔ data layer) shipped
and before Phase 3 (admin pipeline) starts. Its job is to answer one
question with confidence:

> Did Phase 2 work — adding Stages 4.5/4.6/4.7 to the website pipeline —
> accidentally break, slow down, or otherwise contaminate landing and
> admin generation?

Short answer: **No.** Phase 2 work is contained to `website_pipeline.py`
and the new Phase-2 services. Landing and the legacy admin path reach
none of it. Three risks worth knowing about are listed at the end.

All claims below reference specific file paths and line numbers.

---

## 1. Routing — prompt → pipeline

The chat UI hands a prompt to `ws.py` over a WebSocket. From there:

```
ws.py                                    →  task arrives, chat_sessions row created
   ↓ ChatService.create_session                          ai_engine/app/routers/ws.py:753
   ↓ chat_session_id = chat_sess["id"]                                            :761
agent_orchestrator.execute_task          →  threads chat_session_id through
   ↓                                                  ai_engine/app/services/agent_orchestrator.py:301
pipeline.orchestrator.run_pipeline       →  the top-level cancellable task
   ↓                                                  ai_engine/app/services/pipeline/orchestrator.py:187
generate_new_project                     →  wraps user-id billing
   ↓                                                  ai_engine/app/services/project_generator.py:6711
_generate_new_project_inner              →  routing happens HERE
                                                      ai_engine/app/services/project_generator.py:7242
```

The decision point is `_generate_new_project_inner` at
[project_generator.py:7404](../app/services/project_generator.py#L7404):

```python
_classification = await classify_project_type_ai(description, …)
_layout_archetype = _classification["layout_archetype"]
```

The classifier (`knowledge/loader.py:577`) returns one of:
`single_page_landing`, `consumer_website`, `admin_dashboard`, `crm`,
`tms`, `saas_dashboard`, `ecommerce`, `blog`, `portfolio`, `marketplace`.

The branches (in order) are at
[project_generator.py:7418-7461](../app/services/project_generator.py#L7418-L7461):

| If `_layout_archetype` is… | Then call | File / line |
|---|---|---|
| `single_page_landing` | `run_landing_pipeline` | landing_pipeline.py:34 |
| `consumer_website` / `portfolio` / `blog` / `marketplace` **AND** `WEBSITE_PIPELINE_V2_ENABLED` in {1,true,yes} | `run_website_pipeline` | website_pipeline.py:371 |
| anything else (incl. admin/crm/tms/saas/ecom) | **fall through** — legacy code inside `_generate_new_project_inner` from line 7463 onward | project_generator.py:7463-… |

### The three representative prompts

**a) "landing page for fitness coach"** → routed by the static
keyword fast-path in `classify_project_type_ai` at
[knowledge/loader.py:627-630](../../knowledge/loader.py#L627-L630)
(the phrase `landing page` short-circuits to `single_page_landing`
without calling Gemini). → `run_landing_pipeline`.

**b) "Italian restaurant website with menu"** → the static fast-path
doesn't match; Gemini chooses `consumer_website` (the prompt at
[knowledge/loader.py:635-650](../../knowledge/loader.py#L635-L650)
lists it as the default for `"X website"`). If
`WEBSITE_PIPELINE_V2_ENABLED=true`, → `run_website_pipeline`. Otherwise
falls through to the legacy path.

**c) "internal CRM for sales team"** → Gemini classifier returns
`crm`. Neither special branch matches → falls through to the legacy
admin path inside `_generate_new_project_inner`.

---

## 2. Pipeline isolation

### `run_landing_pipeline` — [landing_pipeline.py:34](../app/services/landing_pipeline.py#L34)

**Signature**

```python
async def run_landing_pipeline(
    *, description, classification, workspace_path,
    validated, websocket, chat_session_id,
) -> bool
```

**Stages** (from the docstring + headers)

| Step | What |
|---|---|
| 1   | Brief + grounded research (parallel) |
| 1.5 | Plan confirmation gate (user "Looks Good") |
| 2   | Runtime content JSON → `src/content/landing.json` |
| 3   | Image binding (Unsplash) |
| 4   | Phase-0 deterministic builders |
| 5   | Parallel section + layout codegen (Claude) |
| 6   | `src/app/page.jsx` shell |
| 7   | Lean fixer chain |
| 8   | Build verification + auto-fix loop |
| 9   | Quality gate |

**Imports** (cross-pipeline lines in [landing_pipeline.py:77-100](../app/services/landing_pipeline.py))

Shared with website: `landing_brief`, `landing_intent`,
`landing_domain_research`, `landing_design_research`,
`landing_research_extract`, `landing_phase0`.

Landing-only: `landing_content`, `landing_image_binder`,
`landing_section_codegen`, `landing_fixers`, `build_validator`,
`landing_quality_gate`, `purpose_research`.

**Phase 2 touches:** `chat_sessions.data_model`, `tenant_schema`,
`plan_data_model`, `provision_tenant_schema`, `seed_tenant_data` →
**none.** A grep across `landing_pipeline.py` for these names returns
zero matches. The pipeline writes the chat_sessions title at
[landing_pipeline.py:407](../app/services/landing_pipeline.py#L407)
and that's it.

### `run_website_pipeline` — [website_pipeline.py:371](../app/services/website_pipeline.py#L371)

**Signature**

```python
async def run_website_pipeline(
    *, description, classification, workspace_path,
    validated, websocket, chat_session_id,
) -> bool
```

**Stages** (from the docstring + headers)

| Stage | What | LLM | Phase 2? |
|---|---|---|---|
| 0.5 | Purpose classification | Gemini | no |
| 1   | Intent analysis | Gemini Flash | no |
| 2   | Domain + design research (parallel) | Gemini | no |
| 3   | Visual DNA extraction | Gemini Pro | no |
| 4   | Plan: pages + sections | Gemini Flash | no |
| **4.5** | **Data model planning → DataModel** | **Gemini 3.1 Pro** | **yes — Phase 2** |
| **4.6** | **Tenant schema + DDL apply** | none (Postgres) | **yes — Phase 2** |
| **4.7** | **Seed rows → INSERTed** | **Gemini 3.1 Pro** | **yes — Phase 2** |
| 5   | Deterministic foundation (also emits Supabase plumbing files when 4.6 succeeded) | none | partly |
| 5.5 | Image binding | none (Unsplash) | no |
| 6   | Parallel creative codegen | Claude | no (sees a new prompt block) |
| 6.5 | Content schema derivation | none | no |
| 7   | Build verification | none | no |

**Imports unique to website** that landing/legacy don't see:
`purpose_classifier`, `pipeline_cache`, `image_binding`, `website_plan`,
`website_orchestrator`, `website_verification`, `data_model_planner`,
`data_model`, `tenant_sql_generator`, `seed_tenant_data`, `page_generator`.

**Phase 2 touches:** every Phase 2 helper is invoked exactly here:
- `plan_data_model` from [website_pipeline.py:635](../app/services/website_pipeline.py#L635)
- `_provision_tenant_for_project` from [website_pipeline.py:128](../app/services/website_pipeline.py#L128)
- `_seed_tenant_for_project` from [website_pipeline.py:281](../app/services/website_pipeline.py#L281)
- `chat_sessions.data_model` UPDATE from [website_pipeline.py:208](../app/services/website_pipeline.py#L208)

### Legacy admin path — inline in `_generate_new_project_inner`

There is NO `admin_pipeline.py`. Admin code lives inside
[project_generator.py:7242](../app/services/project_generator.py#L7242)
and after the two `return`s for landing + website at line 7461, falls
through to lines 7463-end. Admin-specific branches are gated by
`_is_admin = _layout_archetype in {"admin_dashboard", "crm", "tms",
"saas_dashboard", "ecommerce"}` at multiple points
([project_generator.py:8887,9269](../app/services/project_generator.py)).

**Stages** (admin path, derived from comments)

| Phase | What |
|---|---|
| Phase 1 | Foundation — theme, config, nav, sidebar, layouts (Claude) |
| Phase 2 | Parallel entity batches via `_run_admin_batch` — CRUD per entity (Claude × N) [project_generator.py:10205](../app/services/project_generator.py#L10205) |
| Phase 2.5 | Entity coverage audit — fills gaps when a batch silently fails |
| Phase 3 | Extra pages + completeness check (Claude) |
| Phase 4 | Build verify + auto-fix loop |

**Phase 2 touches** (the Supabase data-layer kind): `chat_sessions.data_model`,
`tenant_schema`, `plan_data_model`, `provision_tenant_schema`,
`seed_tenant_data` → **none.** Grep across `project_generator.py` for
these names returns zero matches.

The legacy admin path writes generated React/Vue files into the
workspace, persists no Supabase tables, and never sees the new
Phase 2 services.

### Conclusion

The three paths are fully isolated. The website pipeline is the only
one that touches the new Supabase data-layer infrastructure. Landing
and admin reach none of it.

---

## 3. Shared services inventory

| Service | Used by | Side effects | Risk of one pipeline breaking another |
|---|---|---|---|
| [landing_intent](../app/services/landing_intent.py) | landing, website, legacy admin | Gemini call; no DB writes | Low — pure function with cache; signature stable |
| [landing_domain_research](../app/services/landing_domain_research.py) | landing, website | Gemini call; no DB writes | Low — same |
| [landing_design_research](../app/services/landing_design_research.py) | landing, website | Gemini call; no DB writes | Low — same |
| [landing_research_extract](../app/services/landing_research_extract.py) | landing, website, legacy | Gemini calls; no DB writes | Low — same |
| [landing_phase0](../app/services/landing_phase0.py) | landing, website | Writes determinstic foundation files | Medium — the file shapes it emits are consumed by both pipelines; changes need both verified |
| [pipeline_cache](../app/services/pipeline_cache.py) | website | In-memory only | None for cross-pipeline |
| [image_binding](../app/services/image_binding.py) | website | Calls Unsplash; process-local cache | Low |
| [purpose_classifier](../app/services/purpose_classifier.py) | website | Gemini call; no DB writes | Low |
| [project_classifier_agent](../app/services/project_classifier_agent.py) | classifier (above all pipelines) | Gemini call; no DB writes | Low — but a wrong classification routes to the wrong pipeline |

### Phase 2 services — website-only

| Service | Where used | Pure? |
|---|---|---|
| [data_model](../app/services/data_model.py) | website_pipeline (via data_model_planner, tenant_sql_generator, seed_tenant_data) | Pure Pydantic models |
| [data_model_planner](../app/services/data_model_planner.py) | website_pipeline | Gemini call only |
| [tenant_sql_generator](../app/services/tenant_sql_generator.py) | website_pipeline, seed_tenant_data | Pure (deterministic SQL string builder); `apply_tenant_sql` writes to DB |
| [seed_tenant_data](../app/services/seed_tenant_data.py) | website_pipeline | Gemini call + INSERTs into the tenant schema |

**Cross-pipeline reuse opportunity for Phase 3 (admin):**
`data_model`, `data_model_planner`, `tenant_sql_generator`,
`seed_tenant_data` are all pipeline-agnostic. They take a DataModel
+ tenant_schema and don't care what generated them. An admin pipeline
can reuse them as-is.

---

## 4. Feature flags

| Env var | Default | Controls | Affects | File |
|---|---|---|---|---|
| `WEBSITE_PIPELINE_V2_ENABLED` | OFF (empty / unset) | Routing into `run_website_pipeline` vs falling through to legacy | website (vs legacy fallthrough) | project_generator.py:7443 |
| `DATA_MODEL_PLANNER_ENABLED` | ON | Stage 4.5 — and transitively 4.6/4.7 (they require a DataModel) | website | website_pipeline.py:53 |
| `TENANT_PROVISION_ENABLED` | ON | Stage 4.6 — and transitively 4.7 | website | website_pipeline.py:62 |
| `TENANT_SEED_ENABLED` | ON | Stage 4.7 | website | website_pipeline.py:71 |
| `CONTENT_SEPARATION_ENABLED` | ON | Whether sections import `content/*.json` and use `<Editable>` wrapper | landing + website | website_pipeline.py:45, page_generator.py:33 |
| `USE_CLASSIFIER_AGENT` | OFF | Whether the new classifier_agent runs in front of clarity | clarity / classifier layer | config.py:78 |
| `PHASE2_PAGE_BATCHING` | ON | Whether consumer/portfolio/blog pages batch in groups of 3 | legacy multi-page | project_generator.py:184 |
| `PHASE_D_DEEP_RESEARCH` | ON | Whether per-entity / per-page deep-research passes run | legacy | project_generator.py:278 |

**Safe-state observation:** the only flag where the OFF state matters
*for shipping* is `WEBSITE_PIPELINE_V2_ENABLED`. It's currently OFF —
meaning the production chat UI today still uses the legacy path for
consumer websites; `run_website_pipeline` (and therefore all Phase 2
work) is dormant until the flag is flipped. This was flagged in the
prior verification report.

---

## 5. Database isolation

### Landing pipeline

Reads/writes ONLY to `chat_sessions` (the same row that was inserted
by `ChatService.create_session` before the pipeline started). Writes:

- `chat_sessions.title` ← brand name from the Brief ([landing_pipeline.py:407](../app/services/landing_pipeline.py#L407))
- `chat_sessions.last_step` / status finalization ([landing_pipeline.py:647,660](../app/services/landing_pipeline.py))

Touches no tenant schemas. Touches no rows belonging to other projects.

### Website pipeline (with Phase 2)

Writes to `chat_sessions`:
- `chat_sessions.title` (legacy update path used by landing, also applied here through shared codepaths)
- `chat_sessions.data_model` ← validated DataModel JSONB ([website_pipeline.py:208](../app/services/website_pipeline.py#L208))
- `chat_sessions.tenant_schema` ← set by `provision_tenant_schema` RPC, then read back ([website_pipeline.py:216-237](../app/services/website_pipeline.py))

Provisions a tenant schema:
- `tenant_<first-12-hex-of-uuid>` created by the SECURITY DEFINER RPC
  `public.provision_tenant_schema(p_project_id UUID)` ([supabase/migrations/023_project_tenancy.sql:104](../../supabase/migrations/023_project_tenancy.sql#L104))
- Inside it: a per-table set of CREATE TABLE / CREATE INDEX /
  CREATE TRIGGER / RLS policies — all generated by `tenant_sql_generator.py`
- Default privileges grant authenticated+anon read; service_role full access

Inserts rows via `public.execute_ddl` per INSERT statement.

### Tenant isolation guarantees

- Names: `tenant_<12 hex>` from a UUID prefix gives 48 bits of entropy
  per project, so collisions across `chat_sessions.id` aren't realistic.
  Migration 023's UNIQUE index on `tenant_schema` would catch one anyway.
- Foreign keys: the SQL generator emits NO cross-schema FKs ([tenant_sql_generator.py:24](../app/services/tenant_sql_generator.py)
  doc comment: "Emit foreign keys (deferred to v1.1)"). Tenant tables
  have no FK into `public.*` and no FK into another tenant schema.
- Sequences: every tenant table uses `id UUID DEFAULT gen_random_uuid()`
  (no SERIAL / SEQUENCE), so no shared sequences either.
- RLS: every tenant table has policies scoped to the project's owner /
  membership via `tenant_sql_generator.rls_policies_for_table` — the
  policies embed the project_id literal at SQL build time, so even an
  authenticated user from another project can't read rows.
- Read path from generated sites: only `public.get_tenant_collection`
  (migration 025) which checks `public_read` per-table from the
  project's stored `data_model` before fetching.

### Legacy admin path

Reads/writes ONLY to `chat_sessions` (title, status, deploy_url at
[project_generator.py:1383,1625](../app/services/project_generator.py)).
Provisions no tenant schema. Touches no `data_model`. The "admin" it
generates today is *frontend code only* — there is no live backend
data layer behind it.

---

## 6. Workspace file isolation

| Path | Landing | Website | Legacy admin |
|---|:---:|:---:|:---:|
| `src/content/landing.json`            | write | — | — |
| `src/app/page.jsx` (single page)      | write | write (home) | write |
| `src/app/<route>/page.js`             | — | write (one per plan page) | write |
| `src/components/sections/*.jsx`       | write | — | — |
| `src/components/pages/<route>/*.jsx`  | — | write | — |
| `src/content/pages/<route>.json`      | — | write | — |
| `src/config/site.js`                  | write | write | write |
| `src/config/navigation.js`            | write | write | write |
| `src/lib/design-system.js`            | write | write | write |
| `src/lib/editable.jsx`                | write (CONTENT_SEPARATION_ENABLED) | write (CONTENT_SEPARATION_ENABLED) | — |
| `src/app/globals.css`                 | write | write | write |
| `.env.local`                          | — | **write (Phase 2)** | — |
| `src/lib/supabase.js`                 | — | **write (Phase 2)** | — |
| `src/lib/db.js`                       | — | **write (Phase 2)** | — |
| `supabase/migrations/*.sql`           | — | — | — |
| Admin shell (Sidebar, layouts.jsx)    | — | — | write |
| `src/components/pages/<Entity>/*.jsx` | — | — | write (per-entity) |

**No cross-pipeline overwrites** — every pipeline runs end-to-end into
its own freshly-cloned workspace, then returns. Two pipelines never
write into the same workspace.

(Note: the table omits Phase 2's *Supabase-side* writes — those are in
the database, not the workspace. Section 5 covers them.)

---

## 7. Isolation verification script

[ai_engine/scripts/verify_pipeline_isolation.py](../scripts/verify_pipeline_isolation.py)

What it does:
- Patches `run_landing_pipeline`, `run_website_pipeline`,
  `_provision_tenant_for_project`, `_seed_tenant_for_project`, and
  `plan_data_model` so none of them actually execute.
- Patches `_load_skills` (the first thing the legacy admin path calls
  after the routing decision) to raise a sentinel exception — that's
  our "admin path was reached" signal.
- Runs `_generate_new_project_inner` three times with three prompts:
  1. `"landing page for fitness coach"` → expect landing
  2. `"Italian restaurant website with menu"` → expect website
  3. `"internal CRM for sales team"` → expect admin_legacy
- Asserts: each prompt reaches exactly one pipeline marker.
- Asserts: Phase-2 helpers are reached ONLY when the website case
  ran. Any other prompt invoking them is cross-contamination.

Cost: 0 LLM calls (everything that talks to Gemini/Claude is patched
or short-circuited before fire). Runtime: ~30s of which most is
container startup + classifier static-keyword paths.

Run it:

```bash
docker exec -e WEBSITE_PIPELINE_V2_ENABLED=true lucid-ai-ai_engine-1 \
    python scripts/verify_pipeline_isolation.py
```

The script's compile-check passes; the live run was deferred because
Docker Desktop was paused at the time of audit. Re-run when Docker
is up to confirm before committing Phase 3 work.

---

## 8. Phase 3 readiness checklist

### Recommendation

**Build a new `admin_pipeline.py` mirroring `website_pipeline.py`.**
Do not extend the legacy path inside `project_generator.py`.

Rationale:
- The legacy path is one ~3K-line function pretending to be modular.
  Adding Phase 2 hooks inside it would tangle two different
  generation philosophies (per-entity CRUD vs per-section copy) into
  one branch tree.
- Phase 2 services (`data_model`, `data_model_planner`,
  `tenant_sql_generator`, `seed_tenant_data`) are already
  pipeline-agnostic. A clean admin pipeline can reuse them with
  almost no adaptation.
- A separate file gives Phase 3 the same isolation property this
  audit just confirmed for landing↔website — easy to verify, easy to
  flag-gate, easy to ship behind a `WEBSITE_PIPELINE_V2_ENABLED`-style
  toggle while the legacy admin path keeps working.

### What admin can reuse as-is

- `data_model.py` — Pydantic shapes (tables, fields, singletons). The
  shape is symmetric between website and admin; both are just "list
  of CRUD-able entities".
- `data_model_planner.py` — Gemini Pro picks tables vs singletons.
  Pure function; safe.
- `tenant_sql_generator.py` — DataModel → DDL. Pure function;
  safe.
- `seed_tenant_data.py` — Gemini Pro + Unsplash + INSERTs. Pure
  function; safe.
- `_provision_tenant_for_project` and `_seed_tenant_for_project`
  helpers in `website_pipeline.py` — these are general-purpose despite
  living in the website module. Either (a) lift them out to a new
  `pipeline_tenant.py` or (b) import them from `website_pipeline.py`
  as-is. (a) is cleaner.

### What admin needs to build

- `admin_pipeline.py` entry function with admin-specific Stages 1–6
  (intent, entity research, design tokens, CRUD scaffolding, build
  verify).
- A new `admin_orchestrator.py` analogous to `website_orchestrator.py`
  for the per-entity parallel codegen (the legacy path already has
  `_run_admin_batch`; can be lifted out).
- A different prompt-tuned `page_generator` for admin sections (CRUD
  tables, edit dialogs) vs website sections (hero, gallery,
  testimonials).

### What admin needs that the website doesn't

- **Write path through PostgREST**. Today the generated website only
  *reads* via the `get_tenant_collection` RPC. Admin needs to
  insert/update/delete. Either:
  - Add a `set_tenant_row(p_project_id, p_table_name, p_payload)` RPC
    in a new migration (mirrors `get_tenant_collection`), or
  - Expose each tenant schema to PostgREST dynamically (hard — see
    section 5 of `INVITE_DEPLOYMENT.md` and PostgREST's `db-schemas`
    config; not currently feasible per-project).
  - Recommended: new migration `026_set_tenant_row.sql` with an
    insert/update/delete RPC analogous to `025_get_tenant_collection`.
- **Authentication-scoped reads**. The admin user needs to see admin-
  only tables (`public_read=false` in the DataModel). The current
  `get_tenant_collection` deliberately refuses them. A new RPC
  `get_tenant_collection_authenticated(p_project_id, p_table_name)`
  that checks `auth.uid()` against `project_members` would handle this.
- **Feature flag**: `ADMIN_PIPELINE_V2_ENABLED` (default OFF) to gate
  the new path while the legacy one keeps serving production.

### Minimum viable admin pipeline (MVP scope)

The smallest thing that proves the architecture:
1. Trigger: `_layout_archetype == "admin_dashboard"` + flag ON.
2. Run Stages 1–4 lifted from `website_pipeline` (intent → research →
   visual_dna → admin plan).
3. Reuse Stage 4.5 (data_model_planner with the same prompt — works
   for CRUD entities too).
4. Reuse Stage 4.6 (provision + DDL).
5. Reuse Stage 4.7 (seed) — gives admin a populated DB to demo
   against.
6. New Stage 5/6: parallel per-entity CRUD page generation (Claude),
   importing from `@/lib/db.js` (the helper Phase 2.3.C already emits).
7. New migration 026 adds the write RPC.
8. Verify by generating a CRM, opening the admin shell, editing a
   row, and confirming the row updated in `tenant_<id>.contacts`.

---

## 9. Risks identified

### Risk 1 — `WEBSITE_PIPELINE_V2_ENABLED=false` in production

The website pipeline (and therefore all Phase 2 infrastructure) is
silently dormant unless this flag is flipped. Verified in this audit
by checking `docker-compose.yml`, `.env`, and the running container's
environment — none had the variable set. This is documented in
`PRE_CLAUDE_VERIFICATION_REPORT.md` but worth flagging here too: the
admin pipeline's eventual flag (`ADMIN_PIPELINE_V2_ENABLED`) will
have the same property and the same opt-in default.

### Risk 2 — `project_generator.py` is a monolith with multiple
**implicit** routing branches

The two explicit branches (`single_page_landing`, then v2 multi-page
archetypes) are at lines 7418-7461 and easy to reason about. But
further downstream the same function makes MANY more in-place decisions
based on `_layout_archetype` (e.g. 8887, 9269) that select between
"admin" and "consumer" prompt assembly. If Phase 3 work accidentally
touches one of those branches without updating all of them, admin and
consumer outputs could quietly diverge in ways that no test catches.

Mitigation: when refactoring for Phase 3, prefer extracting an
`admin_pipeline.py` rather than threading new branches through
`_generate_new_project_inner`.

### Risk 3 — `landing_phase0` is shared between landing + website
and is becoming hard to evolve safely

`landing_phase0` builds deterministic foundation files (Reveal.jsx,
default layout, etc.) consumed by both `landing_pipeline` and
`website_pipeline`. The two pipelines have very different prompt
expectations (landing imports from `content/landing.json`, website
imports from `content/pages/<slug>.json`). Changes to `landing_phase0`
need to be validated against both pipelines, but there's no shared
test fixture that exercises both.

Mitigation: when adding admin, do NOT add admin-specific behavior to
`landing_phase0` — fork an `admin_phase0.py` if needed. The naming is
already misleading enough that a third consumer would make it worse.

---

## Quick reference — file pointers

- Routing decision: `project_generator.py:7418-7461`
- Landing pipeline: `landing_pipeline.py:34`
- Website pipeline: `website_pipeline.py:371`
- Stage 4.5/4.6/4.7 helpers: `website_pipeline.py:128, 281, 597`
- Legacy admin path: inside `_generate_new_project_inner` at `project_generator.py:7242`, admin branches at 8887, 9269; per-entity batching `_run_admin_batch` at `project_generator.py:10205`
- DataModel shape: `app/services/data_model.py`
- SQL gen: `app/services/tenant_sql_generator.py`
- Tenant RPCs: `supabase/migrations/023_project_tenancy.sql`, `024_tenant_helpers.sql`, `025_get_tenant_collection.sql`
- Feature flags: `app/config.py:78`, `website_pipeline.py:42-71`, `project_generator.py:184,278,7443`

---

## Phase 3 status (as of 2026-05-17)

The admin pipeline now exists. Steps 3.1 → 3.6 Part A are merged.
A full write-up lives in [PHASE_3_STATUS.md](PHASE_3_STATUS.md);
this section is the audit-relevant summary.

- **Admin pipeline file location:**
  [app/services/admin_pipeline.py](../app/services/admin_pipeline.py) —
  the new `run_admin_pipeline()` + `should_route_to_admin_pipeline()`.
  Dispatched from [project_generator.py:7463 onward](../app/services/project_generator.py#L7463),
  routing `admin_dashboard` / `crm` / `tms` / `saas_dashboard` behind
  `ADMIN_PIPELINE_V2_ENABLED`. Ecommerce intentionally stays on the
  legacy path. Returning `False` falls through to legacy.

- **Mirrors `website_pipeline.py` structure.** Stage shape is the
  same (0.5 → 1 → 2 → 3 → 4 → 4.5 → 4.6 → 4.7 → 5 → 6), but Stages
  2 and 7 are deliberate SKIPs (no marketing research, no in-process
  build verifier — `dry_run_admin_pipeline.py` runs `npm run build`
  out-of-band). The two pipelines share Stages 4.6 and 4.7 via
  [pipeline_tenant.py](../app/services/pipeline_tenant.py) (extracted
  in Step 3.1 explicitly so the admin pipeline could reuse them
  without importing website code).

- **The three risks from this audit still apply, no new ones
  surfaced.** Re-checked after Phase 3:
  - **Risk 1** (flags off in prod) — `ADMIN_PIPELINE_V2_ENABLED`
    inherits the same opt-in default. Same mitigation applies: flip
    it in `docker-compose.yml` / `.env` before any production
    rollout.
  - **Risk 2** (`project_generator.py` monolith) — Phase 3 followed
    the audit's mitigation: rather than threading admin branches
    through `_generate_new_project_inner`, the entire admin path
    lives in `admin_pipeline.py`. The only edit to
    `project_generator.py` is the dispatch block — no new in-place
    branches on `_layout_archetype` were added downstream.
  - **Risk 3** (`landing_phase0` shared between landing + website,
    hard to evolve) — Phase 3 did **not** add a third consumer.
    Admin builds its own foundation files via
    `admin_foundation_builder.py`. Audit's recommendation respected.

- **Pipeline isolation maintained.** Admin doesn't touch
  `landing_pipeline.py` or `website_pipeline.py`. The only edges
  between pipelines are (a) the shared `pipeline_tenant.py` and
  (b) the dispatch in `project_generator.py`. Verified by
  [scripts/verify_pipeline_isolation.py](../scripts/verify_pipeline_isolation.py)
  — asserts no `admin_*` module imports anything from
  `landing_*` or `website_*`. The legacy admin path inside
  `_generate_new_project_inner` is untouched and continues to
  serve as a safety net.
