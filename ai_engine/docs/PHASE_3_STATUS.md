# Phase 3 Status — 2026-05-17

Phase 3 builds the **admin-panel generation pipeline**: a second
end-to-end pipeline (parallel to `website_pipeline`) that turns a
prompt like *"internal CRM for sales team"* into a Next.js admin
project with auth, sidebar, dashboard, and per-entity CRUD pages
backed by a Supabase tenant schema.

Status: **Steps 3.1 → 3.6 Part A complete and committed.** The
pipeline runs end-to-end; Stage 6 (per-entity CRUD codegen) is the
only piece still on mock output, awaiting Anthropic credits for
Part B.

---

## What works without Claude credits

Everything below is exercised by the dry-run script and verified to
produce a project that compiles via `next build`.

- **Admin pipeline routes correctly behind a flag.**
  `ADMIN_PIPELINE_V2_ENABLED=true` routes `admin_dashboard`, `crm`,
  `tms`, and `saas_dashboard` archetypes through
  [admin_pipeline.py](../app/services/admin_pipeline.py).
  `ecommerce` is intentionally excluded — it stays on the legacy
  code path. A `False` return from the pipeline falls through to
  legacy so users always get something.
- **Generates a Next.js 14 project that compiles cleanly.** Last
  smoke: 13 routes, 0 errors, 0 warnings on `npm run build`.
- **Working login + sidebar + dashboard + tenant DB.** All
  deterministic Next.js shell files come out of
  [admin_foundation_builder.py](../app/services/admin_foundation_builder.py)
  with no Claude calls — `@supabase/ssr` auth, sidebar listing
  every entity, dashboard at `/`, `db_admin` helpers wrapping the
  tenant RPCs.
- **Mock CRUD pages explain themselves.** Each per-entity page
  (`list`/`new`/`edit`) starts with a header explaining it is a mock
  awaiting Step 3.6 Part B. The mock imports `AuthGuard` and the
  page-specific `db_admin` helper (`listCollection` / `createRow` /
  `updateRow`) so the import contract Claude will inherit is
  exercised even without a real Anthropic call.
- **Static validator catches contract violations.** 10 checks
  (`use client`, AuthGuard wrapping, db_admin import, no TypeScript,
  no `fetch()`, no direct `@supabase/*`, balanced brackets, no
  top-level await, etc.) — runs on every generated file regardless
  of mock vs. Claude. See
  [admin_codegen_validator.py](../app/services/admin_codegen_validator.py).

---

## What needs Claude credits to validate

- **Step 3.6 Part B — real CRUD codegen.** Set
  `ADMIN_CODEGEN_MOCK=false` and run the pipeline. Claude generates
  the real list/create/edit pages for each entity using the prompts
  in [prompts/](../app/services/prompts/). Expected ~$0.05/page ×
  3 pages × ~4 entities ≈ **$0.60/project**.
- **Phase 2 Stage 6 — website collection codegen.** The website
  pipeline has its own Stage 6 (per-collection page generation) that
  has been validated only in mocked integration tests so far. The
  end-to-end Gemini + Claude path needs credits to confirm.

---

## How to test without credits (right now)

```bash
# 1. Make sure the flag is on (default in dry-run script)
export ADMIN_PIPELINE_V2_ENABLED=true
export ADMIN_CODEGEN_MOCK=true       # default; explicit for clarity

# 2. Run the dry-run with workspace preservation
docker exec -e OPENHANDS_SUPPRESS_BANNER=1 \
  -e KEEP_WORKSPACE=1 \
  -e ADMIN_CODEGEN_MOCK=true \
  lucid-ai-ai_engine-1 python scripts/dry_run_admin_pipeline.py

# 3. Find the preserved workspace path in the script's last log line
#    Example: "KEEP_WORKSPACE set — workspace preserved at /tmp/lucid_dry_run_admin_xxxxxxxx"

# 4. cd into it and build
docker exec lucid-ai-ai_engine-1 sh -c \
  'cd /tmp/lucid_dry_run_admin_xxxxxxxx && npm install --no-audit --no-fund'
docker exec lucid-ai-ai_engine-1 sh -c \
  'cd /tmp/lucid_dry_run_admin_xxxxxxxx && npm run build'

# 5. Expected: 13 routes compiling cleanly
#    (login + dashboard + 3 pages × 4 entities + _not-found)
```

All 22 dry-run verifications (a–v) should print `✓`. `npm run build`
should report `✓ Compiled successfully` and list the 13 routes.

---

## How to validate with credits (when available)

Part B procedure (will be expanded once we run it for real):

1. Confirm `ANTHROPIC_API_KEY` is set in the container and the org
   has enough credits — Stage 6 logs the per-call estimate before
   spending. Budget **$10–15** for a couple of full runs across
   different entity counts.
2. Flip `ADMIN_CODEGEN_MOCK=false` (env var, no code change needed).
3. Run the same dry-run command from above. Stage 6 will now call
   `claude-sonnet-4-6` three times per entity instead of writing
   mock files.
4. Watch the log for `claude_actual_cost` per entity — it should
   approximate `$0.05 × pages`.
5. The validator runs on every generated file. Any `validation_errors`
   in the result dict are bad outputs Claude returned (TypeScript
   syntax, missing AuthGuard, etc.) — surface them, then re-prompt.
6. `npm run build` from the preserved workspace must still produce
   13 routes with 0 errors. The mock contract is the same shape
   Claude is supposed to produce; if mocks build and Claude's don't,
   the difference is the validator's job to catch.

**Expected outcome:** real CRUD pages replace the mocks. Each list
page renders a table with row data, each create page renders a
react-hook-form, each edit page pre-fills and writes back. Same
file paths, same imports — only the page bodies change.

---

## File map (Phase 3 only)

### Step 3.1 — extract shared tenant helpers
- [app/services/pipeline_tenant.py](../app/services/pipeline_tenant.py) — `provision_tenant_for_project`, `seed_tenant_for_project`, `UUID_RE`, and the tenant-feature flags. Pulled out of `website_pipeline.py`.
- [app/services/sql_splitter.py](../app/services/sql_splitter.py) — standalone DDL-statement splitter.
- [tests/test_pipeline_tenant.py](../tests/test_pipeline_tenant.py) — covers provision + seed paths; replaces the deleted `test_website_pipeline_provision.py`.
- [scripts/verify_pipeline_isolation.py](../scripts/verify_pipeline_isolation.py) — asserts admin → website never imports.
- [docs/PIPELINE_ARCHITECTURE_AUDIT.md](PIPELINE_ARCHITECTURE_AUDIT.md) — audit that drove the extraction.

### Step 3.2 — migration 027 (authenticated reads)
- [supabase/migrations/026_set_tenant_row.sql](../../supabase/migrations/026_set_tenant_row.sql) — SECURITY DEFINER write RPC for the admin UI.
- [supabase/migrations/027_get_tenant_collection_authenticated.sql](../../supabase/migrations/027_get_tenant_collection_authenticated.sql) — SECURITY DEFINER authenticated read RPC.
- [scripts/apply_migration_026.py](../scripts/apply_migration_026.py), [scripts/apply_migration_027.py](../scripts/apply_migration_027.py) — apply each migration against the live Supabase project.
- [tests/test_set_tenant_row.py](../tests/test_set_tenant_row.py), [tests/test_get_tenant_collection_authenticated.py](../tests/test_get_tenant_collection_authenticated.py) — 9 + 15 live tests.
- [docs/TENANT_RPCS.md](TENANT_RPCS.md) — RPC reference (anon read 025, authenticated write 026, authenticated read 027).

### Step 3.3 — admin pipeline skeleton
- [app/services/admin_pipeline.py](../app/services/admin_pipeline.py) — `run_admin_pipeline()` + `should_route_to_admin_pipeline()`. Mirrors `website_pipeline.py`'s shape, reuses Stages 4.5/4.6/4.7 via `pipeline_tenant.py`.
- Modification to [app/services/project_generator.py](../app/services/project_generator.py) — early dispatch into the admin pipeline before the legacy path.
- [tests/test_admin_pipeline_skeleton.py](../tests/test_admin_pipeline_skeleton.py) — stage-by-stage progress assertions, routing-flag matrix, fall-through behavior.
- [scripts/dry_run_admin_pipeline.py](../scripts/dry_run_admin_pipeline.py) — end-to-end smoke against real Supabase + 22 verifications (a–v).

### Step 3.4 — admin-aware data model planner
- [app/services/admin_data_model_planner.py](../app/services/admin_data_model_planner.py) — Gemini-backed planner asking *"what entities does the user manage?"* (private tables, no singletons).
- [app/services/admin_plan.py](../app/services/admin_plan.py) — deterministic page-plan builder (list/new/edit per entity + dashboard/login/layout shared).
- [tests/test_admin_data_model_planner.py](../tests/test_admin_data_model_planner.py), [tests/test_admin_plan.py](../tests/test_admin_plan.py).

### Step 3.5 — admin foundation file builder
- [app/services/admin_foundation_builder.py](../app/services/admin_foundation_builder.py) — generates the full Next.js shell: package.json, jsconfig, Tailwind config, .env.local, layout/login/dashboard pages, AuthGuard/Sidebar/Header/EmptyState/Toaster/EntityListSkeleton components, supabase/db_admin/auth/utils libs, plus per-entity stub pages.
- [tests/test_admin_foundation_builder.py](../tests/test_admin_foundation_builder.py).

### Step 3.6 Part A — admin CRUD codegen (mock mode)
- [app/services/admin_codegen.py](../app/services/admin_codegen.py) — `generate_entity_crud()` orchestrates 3 pages × N entities; `_mock_page_jsx()` writes contract-honoring placeholders; `_call_claude_for_one_page()` reuses `project_generator`'s `call_claude_for_json` (lazy import).
- [app/services/admin_codegen_validator.py](../app/services/admin_codegen_validator.py) — 10 static checks every generated file must pass.
- [app/services/admin_field_renderers.py](../app/services/admin_field_renderers.py) — pure JSX-snippet helpers (per field type) used in the user prompt as worked examples.
- [app/services/prompts/admin_list_view_prompt.py](../app/services/prompts/admin_list_view_prompt.py)
- [app/services/prompts/admin_create_view_prompt.py](../app/services/prompts/admin_create_view_prompt.py)
- [app/services/prompts/admin_edit_view_prompt.py](../app/services/prompts/admin_edit_view_prompt.py)
- [app/services/prompts/\_\_init\_\_.py](../app/services/prompts/__init__.py) — re-exports the three builders.
- [tests/test_admin_codegen.py](../tests/test_admin_codegen.py) — 23 tests: 6 prompt-builder, 6 mock-mode, 11 validator.

---

## Architecture — admin pipeline stages

```
                  prompt over WebSocket
                          │
                          ▼
            project_generator._generate_new_project_inner
                          │
              ┌───────────┴────────────┐
              │ archetype routing      │
              │                        │
              ▼                        ▼
   should_route_to_admin_pipeline?     └─→ landing / website / legacy
   (admin_dashboard | crm | tms | saas)
              │
              ▼
   ┌─────────────────────────────────────────────────────────────┐
   │ admin_pipeline.run_admin_pipeline                           │
   │                                                             │
   │  Stage 0.5  purpose classification    (admin vs other)      │
   │  Stage 1    intent analysis           (Gemini)              │
   │  Stage 2    SKIPPED                   (no research needed   │
   │                                       for internal tools)   │
   │  Stage 3    visual DNA                (deterministic brand) │
   │  Stage 4    admin plan                (admin_plan.py +      │
   │                                       admin_data_model      │
   │  Stage 4.5  data model planner        — _planner.py;        │
   │                                       Gemini)               │
   │  Stage 4.6  tenant provision          (pipeline_tenant.py)  │
   │  Stage 4.7  tenant seed               (Gemini + INSERTs)    │
   │  Stage 5    foundation builder        (admin_foundation_    │
   │                                       builder.py;           │
   │                                       deterministic)        │
   │  Stage 6    CRUD codegen              (admin_codegen.py;    │
   │                                       mock OR Claude)       │
   │  Stage 7    SKIPPED                   (npm build is the     │
   │                                       canonical verifier,   │
   │                                       run by dry_run)       │
   └─────────────────────────────────────────────────────────────┘
              │
              ▼
       workspace/<id>/      ←  Next.js project, ready to npm install + build
       tenant_<12hex>/      ←  Supabase schema, populated with seed rows
```

Stage parity with website pipeline:
- Stages 0.5, 1 use the same shared services.
- Stages 4.6, 4.7 share `pipeline_tenant.py` (extracted in Step 3.1).
- Stages 4.5, 5, 6 are admin-specific implementations.

---

## What's next

- **Step 3.6 Part B** — flip `ADMIN_CODEGEN_MOCK=false`, validate
  Claude outputs end-to-end against `npm run build`. Re-prompt loop
  on validator failures.
- **Phase 4** — linking UI between a website project and its
  corresponding admin project. `parent_project_id` already wires
  Stage 4.6 to reuse the parent's tenant schema; the UI for picking
  a parent is the missing piece.
