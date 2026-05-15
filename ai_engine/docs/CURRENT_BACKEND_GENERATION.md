# Current backend / Supabase generation — inventory

**Scope:** this is a *no-modify* inventory of every code path in `ai_engine/` that touches database schemas, Supabase provisioning, RLS, admin/CRUD generation, or auth wiring as of 2026-05-15. Everything below is grounded in actual code references (file path + line number). If a feature is half-built, abandoned, or unwired, that is called out explicitly — there is no optimistic framing in here.

The single most important finding is at the top of section 11: **none of the per-project Supabase provisioning, migration-apply, or content-persist code is wired into the production pipeline** (`ai_engine/app/services/pipeline/orchestrator.py`). Those modules exist, have unit-style tests, but are imported only from the manual test scripts in `ai_engine/scripts/`. The only schema-generation path that *is* wired (`backend_schema.py` → `project_generator.py`) lives behind an archetype gate that the current Lucid-AI generation flow appears not to hit, and even when it does fire it only writes a `.sql` file to the workspace — it never applies it and the frontend never reads from it.

## 1. Detection / Intent

### Production pipeline (the one that actually runs)
The current production entry point is `run_pipeline` in [ai_engine/app/services/pipeline/orchestrator.py:187](ai_engine/app/services/pipeline/orchestrator.py#L187), reached from [ai_engine/app/services/pipeline/__init__.py:89](ai_engine/app/services/pipeline/__init__.py#L89) and the WS router at [ai_engine/app/routers/ws.py:30](ai_engine/app/routers/ws.py#L30) (`from app.services.pipeline import run_pipeline`).

It has no backend-schema-generation logic of its own. The only "is this an admin app?" check it does is for **skeleton selection**, not schema generation:

- [orchestrator.py:285-298](ai_engine/app/services/pipeline/orchestrator.py#L285) calls `detect_admin_from_task(task_original or task)` from `skeleton_manager.py` and uses the result to pick which template directory to copy from. That decision affects which `vite-react-admin` / `vue-admin` skeleton ships — it does not trigger any schema generation.

### Legacy / orphan pipeline (only reachable via `project_generator.run_project_generation`)
The actual "generate a Supabase migration" trigger lives in [ai_engine/app/services/project_generator.py:8155-8201](ai_engine/app/services/project_generator.py#L8155) inside `run_project_generation`. The gate is:

```python
_backend_archetypes = {
    "admin_dashboard", "crm", "tms", "saas_dashboard", "ecommerce",
}
if (
    _layout_archetype in _backend_archetypes
    and (project_schema.get("entities") or [])
):
    ...build_supabase_migration(...)...write_supabase_migration(...)
```

Two conditions must hold: the archetype string must be in that set, AND `project_schema["entities"]` must be non-empty.

**`_layout_archetype` source:** `project_generator.py` is a giant ~10K-line module. The archetype is set earlier in the same module from the classifier output; it's not loaded from any user-facing flag. Whether `run_project_generation` is still on the live code path (vs. the newer `pipeline/orchestrator.run_pipeline`) needs runtime verification — see `Known Gaps` below.

**If not detected** (any archetype outside the set, or no entities): the block silently skips. There is no user warning, no progress event, no toggle. Pipeline keeps running and emits a frontend with mock data and no `supabase/migrations/` folder.

### Admin-panel detector
There is no dedicated "this prompt needs an admin panel" function. The closest things are:

1. `skeleton_manager.detect_admin_from_task` (referenced from `orchestrator.py:285`, `:518`). Decides which template skeleton to copy. Not read here.
2. The "archetype" classifier whose output feeds `project_schema["archetype"]` ([project_schema.py:38](ai_engine/app/services/project_schema.py#L38)). Possible values include `"admin_dashboard"`, `"crm"`, `"tms"`, `"saas_dashboard"`, `"ecommerce"`, `"consumer_website"`, `"single_page_landing"`, ... — and the backend-migration gate above keys on this.

### Purpose research (does NOT cover backend)
`purpose_research.maybe_run_purpose_research` at [purpose_research.py:299](ai_engine/app/services/purpose_research.py#L299) dispatches by `intent.primary_purpose`. Only one branch is implemented: `if purpose == "hiring": return await run_recruitment_research(...)`. Everything else returns `""`. There is no purpose router that says "generate a backend." Comment at [purpose_research.py:319-321](ai_engine/app/services/purpose_research.py#L319) reads:

> Future: add lead_generation / ecommerce / booking purpose-specific calls when their generic-research outputs prove insufficient.

## 2. Research stage for data models

There is **no Gemini call dedicated to researching data models, tables, or CRUDs from the web.** The existing research stages (in `landing_domain_research.py`, `landing_design_research.py`, `landing_research_extract.py`) only research marketing/visual context — palette, typography, audience, regional, competitive, business, layout.

The closest thing is a *parser*, not a *researcher*: [backend_schema.py:113-138](ai_engine/app/services/backend_schema.py#L113) `_extract_data_model_block(spec_text)`. It scans a free-text spec (the output of `project_generator`'s research markdown) for a `## Data Model` or `## Database` section, captures up to 4000 chars of body, and feeds it to Claude as "hints" (see section 3). The hint is only honored as a tie-breaker — Claude is told **not** to add tables that aren't in the entities list.

`project_schema["entities"]` itself is populated by `_parse_schema_from_research` in [project_schema.py](ai_engine/app/services/project_schema.py) (called from `project_generator.py`). The shape is defined at [project_schema.py:74](ai_engine/app/services/project_schema.py#L74):

```
"entities": [],  # [{name, slug, fields: [{name, type, required, inList, inForm, options?}], mockData: [...]}]
```

Entities are extracted from Gemini research markdown by parsing `===ENTITIES===` / `===ENTITY_DEEP::Name===` blocks. The Gemini prompts that generate those blocks are in [project_generator.py:7604-7690](ai_engine/app/services/project_generator.py#L7604) (purpose-directive + deep-research dispatch). There is no dedicated "design a Postgres schema for this app" Gemini prompt — entity shape comes out of the same monolithic research call that produces design tokens and section copy.

## 3. Schema generation stage

**File:** [ai_engine/app/services/backend_schema.py](ai_engine/app/services/backend_schema.py) (362 lines).

**Generator:** Claude Sonnet 4.6 (`_MODEL = "claude-sonnet-4-6"` at [line 34](ai_engine/app/services/backend_schema.py#L34)). Direct httpx call to `https://api.anthropic.com/v1/messages` — does not go through `project_generator.call_claude_for_json`. `max_tokens=8000`, `timeout=90.0`.

**Tool use:** Claude is asked to emit through a single tool `emit_supabase_migration` defined at [lines 42-70](ai_engine/app/services/backend_schema.py#L42):

```python
_MIGRATION_TOOL: dict = {
    "name": "emit_supabase_migration",
    "description": (
        "Emit a complete, runnable Postgres migration for Supabase. "
        "Include CREATE TABLE statements, RLS policies, foreign keys, "
        "indexes, and a final GRANT block."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "sql": {
                "type": "string",
                "description": (
                    "The complete SQL migration. Must be runnable as-is in "
                    "the Supabase SQL editor. No markdown fences, no prose."
                ),
            },
            "tables_summary": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Short list of table names + 1-line purpose, used in the "
                    "README. Example: ``contacts — sales contact records``."
                ),
            },
        },
        "required": ["sql", "tables_summary"],
    },
}
```

**System prompt** ([backend_schema.py:166-172](ai_engine/app/services/backend_schema.py#L166)):

```
You are a senior database engineer who writes safe, idiomatic Postgres migrations for Supabase. You always include RLS policies, foreign keys with appropriate ON DELETE behavior, and indexes on join/filter columns. You never use VARCHAR (use TEXT). You never skip RLS. You never grant to anon for tables that hold user data.
```

**User prompt** (verbatim from [backend_schema.py:174-225](ai_engine/app/services/backend_schema.py#L174); braces preserved):

```
Generate a complete Supabase migration for this project.

PROJECT: {description[:300]}

ENTITIES (from project schema — these are the source of truth for tables):
{entity_block}

DATA MODEL HINTS (from product spec — use only to inform RLS + relationships;
do NOT add tables that aren't in the entities list above):
{data_model_block or "(no extra hints)"}

REQUIREMENTS — every table MUST have:
1. `id UUID PRIMARY KEY DEFAULT gen_random_uuid()`
2. `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`
3. `updated_at TIMESTAMPTZ NOT NULL DEFAULT now()` with a trigger that updates it
4. `ALTER TABLE ... ENABLE ROW LEVEL SECURITY;`
5. RLS policies for SELECT/INSERT/UPDATE/DELETE. For tables with an
   `owner_id UUID REFERENCES auth.users(id)`: use `auth.uid() = owner_id`.
   For shared/lookup tables (e.g. status_options, tags): use
   `auth.role() = 'authenticated'` for SELECT and admin-only for writes.
6. Foreign keys for any field whose name ends in `_id` and references
   another entity. Use `ON DELETE CASCADE` for child tables, `ON DELETE
   SET NULL` for optional refs.
7. CREATE INDEX on every foreign-key column and on any field used for
   filtering/sorting (status, created_at, owner_id).
8. Use proper Postgres types: TEXT, TIMESTAMPTZ, UUID, NUMERIC(12,2) for
   money, BOOLEAN, JSONB. Never VARCHAR or TIMESTAMP (without time zone).

START THE FILE WITH:
-- =====================================================================
-- Lucid AI — initial schema for {{ project name }}
-- Apply via: Supabase Dashboard → SQL Editor → paste → Run
-- =====================================================================

-- Helper trigger function for updated_at
CREATE OR REPLACE FUNCTION public.set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

END THE FILE WITH the GRANT block:
GRANT USAGE ON SCHEMA public TO authenticated;
GRANT ALL ON ALL TABLES IN SCHEMA public TO authenticated;
GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO authenticated;

Output via the emit_supabase_migration tool. The `sql` field must be the
COMPLETE file ready to paste into Supabase. The `tables_summary` field is
a short list for the README.
```

**Entity rendering for the prompt** ([backend_schema.py:75-108](ai_engine/app/services/backend_schema.py#L75)) `_summarize_entities`:

```python
### {Entity Name} (table: {slug})
- {field_name}: {type} required enum:[opt1,opt2]
  sample: {first 200 chars of mockData[0] as JSON}
```

Tables/columns/constraints come directly from `project_schema["entities"][n]["fields"][n]` — there is no separate research call. Field types are passed through to Claude verbatim with the assumption Claude maps `"text"` → `TEXT`, `"number"` → `NUMERIC`, etc.

**Output format:** dict `{"sql": str, "tables_summary": list[str]}` returned from `build_supabase_migration`. The SQL is one big string that has to contain `"CREATE TABLE"` (case-insensitive) — anything else is treated as malformed and returns `None` ([line 279](ai_engine/app/services/backend_schema.py#L279)).

**Where written:** `write_supabase_migration` at [backend_schema.py:321-362](ai_engine/app/services/backend_schema.py#L321) writes:
- `{workspace}/supabase/migrations/0001_init.sql` — raw Claude SQL, no validation, no parsing
- `{workspace}/supabase/README.md` — template at [lines 290-318](ai_engine/app/services/backend_schema.py#L290) instructing the user to paste the SQL into the Supabase Dashboard manually

No second migration file is ever generated. There is no `0002_*.sql`, no policies-only file, no seed file. The README itself says (verbatim, [line 314](ai_engine/app/services/backend_schema.py#L314)):

> ## Future migrations
> Add new files as `migrations/0002_*.sql`, `migrations/0003_*.sql`, etc. Run them in order via the same SQL Editor flow, or use the Supabase CLI for automation.

— i.e. user homework, not generator output.

**FAIL-SOFT:** every error returns `None` and the pipeline continues. Module docstring at [line 18](ai_engine/app/services/backend_schema.py#L18) is explicit: *"FAIL-SOFT: every error path returns ``None`` so generation continues even when the migration call fails."*

## 4. RLS policies

Yes, the schema-generation Claude call is told (requirement 4 + 5 above) to include `ALTER TABLE ... ENABLE ROW LEVEL SECURITY` and SELECT/INSERT/UPDATE/DELETE policies. The policies live inside the same `0001_init.sql` blob — there is no separate `policies.sql` file.

There is no programmatic validation that Claude actually emitted RLS — the only check is `"CREATE TABLE" not in sql.upper()` at [line 279](ai_engine/app/services/backend_schema.py#L279). A Claude response that emits `CREATE TABLE` but forgets RLS would still be accepted and written to disk.

Separately, the **per-project content-tables migration** at `supabase/migrations/per_project/001_content_tables.sql` has hand-written RLS policies for the `gen_*` tables — see section 6.

## 5. Seed data

No seed `.sql` file is generated for the user's domain tables. The Claude prompt above does not ask for `INSERT INTO` statements, only DDL.

Seed data exists, but only inside `project_schema["entities"][n]["mockData"]`, which is:
- Used as a `sample` line in the prompt body (one row, 200 chars max — see [backend_schema.py:99-106](ai_engine/app/services/backend_schema.py#L99))
- Written to the frontend's `db.json` mock store
- Mirrored into the `gen_entity_row` table of the per-project content schema ([supabase_content.py:346-366](ai_engine/app/services/supabase_content.py#L346)) — but only if `persist_schema_to_supabase` is actually called, which the production pipeline does not do (see section 11).

Sample shape inside `project_schema`:

```
{"entities": [{
  "name": "Contact",
  "slug": "contacts",
  "fields": [{"name": "email", "type": "text", "required": true}, ...],
  "mockData": [{"id": "1", "email": "alice@example.com", "status": "lead"}, ...]
}]}
```

## 6. Auth integration

### Per-project Supabase provisioning (exists, NOT wired into the live pipeline)
[ai_engine/app/services/supabase_provision.py](ai_engine/app/services/supabase_provision.py) (258 lines) implements full lifecycle:

- `provision_for_project(user_id, project_slug, ...)` at [supabase_provision.py:188](ai_engine/app/services/supabase_provision.py#L188) — calls Supabase Mgmt API (`create_project` from `supabase_mgmt.py`), polls until `ACTIVE_HEALTHY`, encrypts anon/service/db_password with AES-256-CBC, upserts into Lucid's central `supabase_projects` table.
- `generate_client_files(stack, supabase_url, anon_key)` at [supabase_provision.py:112](ai_engine/app/services/supabase_provision.py#L112) — emits `src/lib/supabase/client.js` and `.env.local` for Next.js or Vite.

Generated client file (Next.js, [supabase_provision.py:74-81](ai_engine/app/services/supabase_provision.py#L74)):

```javascript
import { createClient } from '@supabase/supabase-js';

export const supabase = createClient(
  process.env.NEXT_PUBLIC_SUPABASE_URL,
  process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY,
);
```

Importers of `provision_for_project` / `generate_client_files` (greps the entire `ai_engine/`):
- `ai_engine/scripts/test_step2_provision.py:77,100,135` — manual test script
- That's it. **No production caller.**

### OAuth providers
Configured at the *Lucid platform* level for users signing into the Lucid app itself ([CLAUDE.md](CLAUDE.md) describes Google / GitHub / GitLab setup). Generated customer projects get an empty Supabase project — no OAuth providers configured, no Auth UI components emitted, no sign-in pages.

### auth.users references
- The `backend_schema.py` user prompt references `auth.users(id)` once, as part of RLS policy guidance (requirement 5 above): *"For tables with an `owner_id UUID REFERENCES auth.users(id)`: use `auth.uid() = owner_id`."* It's a hint to Claude; whether Claude emits the FK is unverified.
- Generated frontends do not import any sign-in component. There is no `signInWithOAuth`, `signInWithPassword`, or session-bound page guard anywhere in the generator code.

### Effective auth wiring in a generated project
**None, end-to-end.** The Supabase client file (if it's written at all) is constructed with `createClient(URL, anon_key)` — there is no `auth.onAuthStateChange`, no login screen, no protected route. Even if the `0001_init.sql` migration is applied and uses `auth.uid()` in policies, the browser never has a JWT, so RLS-protected reads will all return 0 rows.

## 7. Frontend ↔ backend wiring

**Generated frontend code does not query Supabase.** Searched the entire `ai_engine/app/services/` for `supabase.from(`, `createClient(`, `signInWith`, `auth.onAuthStateChange`:

- Five hits, all in [supabase_provision.py:70-89](ai_engine/app/services/supabase_provision.py#L70) and [backend_schema.py:308-311](ai_engine/app/services/backend_schema.py#L308) — and they're template strings the generator writes, not consumer code.
- Section codegen ([landing_section_codegen.py](ai_engine/app/services/landing_section_codegen.py)), page codegen ([page_codegen.py](ai_engine/app/services/page_codegen.py)), copy director, landing pipeline — none of them prompt Claude to write Supabase queries. They prompt for hardcoded JSX consuming static `content/landing.json` or section-local copy.

A generated admin-skeleton project gets `@supabase/supabase-js` listed in `package.json` ([project_generator.py:5073](ai_engine/app/services/project_generator.py#L5073)) but the React components Claude writes don't import it.

**TypeScript types from the schema:** not generated. No `supabase gen types` is invoked anywhere; no `.types.ts` file is written.

**README explicitly says the wiring is the user's job** ([backend_schema.py:312](ai_engine/app/services/backend_schema.py#L312)):
> 6. Replace mock fetches in your code with real Supabase queries

And earlier ([line 14-16 of the module docstring](ai_engine/app/services/backend_schema.py#L14)):
> The frontend stays on mock data in this generation — wiring it to real Supabase is a separate (bigger) follow-up step we don't take yet.

## 8. Admin panel UI generation

### Skeleton-level admin
There are two admin-flavored *skeletons* (template directories the generator copies wholesale before LLM rewrites). Selected by `skeleton_manager.detect_admin_from_task` at [orchestrator.py:285](ai_engine/app/services/pipeline/orchestrator.py#L285). The skeletons ship Sidebar / Header / LoginPage components and a `navigation.js` contract.

### Deterministic navigation.js writer
[ai_engine/app/services/admin_navigation_config_builder.py](ai_engine/app/services/admin_navigation_config_builder.py) builds `src/config/navigation.js` deterministically from `project_schema.navigation` so Phase 1/2 LLM can't break it. Three named exports: `navigation`, `modules`, `appConfig`. Wired into the legacy pipeline at [project_generator.py:8550-8575](ai_engine/app/services/project_generator.py#L8550) under `_is_admin_stack` gate (`vite.config.*` exists OR stack contains `react-admin` / `vue-admin`). Not wired into `pipeline/orchestrator.py`.

### CRUD pages
`project_schema["pages"]` has a `type` field with `crud_list` / `crud_form` as valid values ([project_schema.py:75](ai_engine/app/services/project_schema.py#L75)):
```
"pages": [],  # [{path, title, component, type: "crud_list"|"crud_form"|"dashboard"|"settings"|"custom"}]
```

These flow into the legacy `project_generator` LLM prompts as page-type hints, and the admin skeletons have templated patterns the LLM is meant to fill in. There is **no deterministic CRUD-page builder** like the navigation builder above — CRUD UI is Claude-generated from prompts, with no codegen-side schema-driven scaffolding.

### Form / table libraries
No `react-hook-form` / `@tanstack/react-table` / `zod` is force-imported by the generator. Whether they end up in `package.json` depends on the skeleton template and what Claude decides to write. No prompt instructs Claude to use a specific data-table or form library for CRUD pages.

### Login UI
The admin skeletons ship a `LoginPage.jsx` file (referenced from [admin_navigation_config_builder.py:9-11](ai_engine/app/services/admin_navigation_config_builder.py#L9) as a consumer of `navigation.js`). Its contents come from the skeleton, not from the generator. The generator never wires it to `supabase.auth.signInWithPassword` — see section 6.

## 9. Tests

Three "step" scripts in `ai_engine/scripts/`. Each is a `python3` standalone runner, not pytest.

### test_step1_schema.py
Verifies `project_schema.EMPTY_SCHEMA` shape and that the research-parser populates `archetype`, `domain_kind`, `design`. Pure offline / no LLM. Does not verify entity parsing or anything DB-related. ~12 assertions.

### test_step2_provision.py
- `--offline` (default): crypto round-trips, sanity tests on `_canonical_project_name`, asserts `generate_client_files` emits correct env vars per stack.
- `--live`: hits the real Supabase Mgmt API — creates a project, asserts the response shape, deletes it. Requires `SUPABASE_MGMT_TOKEN`, `SUPABASE_MGMT_ORG_REF`, `ENCRYPTION_KEY` in env.

### test_step3_content.py
- `--offline`: verifies row-builder helpers (`_build_site_config_row`, `_build_navigation_rows`, `_build_page_rows`, `_build_section_rows`, `_build_entity_rows_and_data`) emit the right shapes for several archetypes.
- `--live`: provision → `apply_per_project_migrations` → `persist_schema_to_supabase` → SELECT-verify rows → cleanup. End-to-end real Supabase. Cleanup-strict.

### What's untested
- `backend_schema.build_supabase_migration` has no test file at all. Searched `ai_engine/tests/`: nothing references it. There is no offline fixture, no recorded Claude response, no `0001_init.sql` golden file checked into git.
- No test verifies the migration Claude emits is valid SQL.
- No test verifies the generated `0001_init.sql` actually applies against a real Postgres / Supabase.
- No test verifies RLS policies in the generated SQL behave correctly.
- No test for the integration path "section codegen produces a frontend that reads from Supabase." (Because that path doesn't exist — see section 7.)

## 10. Output examples

### Workspace inspection
Looked at `workspaces/` (548 directories — sampled `01058d4e-...`, `01441bef-...`, `014b3b8f-...`):
- `find workspaces -name "0001_init.sql"` → **0 hits**
- `find workspaces -name "supabase" -type d` → **0 hits**
- `grep -rln "supabase" workspaces/ --include="*.json" --include="*.js" --include="*.sql"` → only `node_modules` package.json from `preview_ws/lucid_ws_643082c8ab93` (the preview container's own deps), zero generated-project hits.

**No generated workspace on this machine contains a Supabase migration, supabase/ folder, or any code that imports `@supabase/supabase-js`.** The most likely explanations:
- All recent workspaces are landing-page archetypes, which never trigger the `_backend_archetypes` gate in `project_generator.py:8167`.
- And/or the production pipeline ([orchestrator.py:run_pipeline](ai_engine/app/services/pipeline/orchestrator.py#L187)) has replaced `project_generator.run_project_generation` and the new path doesn't call `backend_schema` at all.

### The only "schema" file that is real and shipped
`supabase/migrations/per_project/001_content_tables.sql` (186 lines), checked into the repo. This is **Lucid's own per-project CMS schema**, not user-domain tables. It creates seven tables, all prefixed `gen_`:

| Table | Purpose |
|---|---|
| `gen_site_config` | One row — brand, theme tokens, archetype, domain_kind |
| `gen_navigation` | Header / footer / sidebar nav groups |
| `gen_page` | One row per route, with `type` ∈ {custom, dashboard, settings, crud_list, crud_form, landing} |
| `gen_section` | Per-page content blocks (hero/features/etc), `props` JSONB |
| `gen_entity` | Admin-CRUD entity definitions ({name, slug, fields[]}) |
| `gen_entity_row` | The data behind an entity — JSONB blob per row |
| `gen_revision` | Edit history / undo trail |

RLS: enabled on every table. Public SELECT policies on `gen_site_config`, `gen_navigation`, `gen_page`, `gen_section` (where `visible=true`), `gen_entity`. No public read on `gen_entity_row` or `gen_revision` (PII). All writes go through service_role (the Lucid CMS / generation pipeline, which bypasses RLS).

Sample DDL (verbatim head):

```sql
CREATE TABLE IF NOT EXISTS public.gen_site_config (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    brand           JSONB NOT NULL DEFAULT '{}'::jsonb,   -- {name, tagline, description, domain}
    theme           JSONB NOT NULL DEFAULT '{}'::jsonb,   -- shadcn HSL tokens + fonts + radius
    design          JSONB NOT NULL DEFAULT '{}'::jsonb,   -- Design Director output
    archetype       TEXT,
    domain_kind     TEXT,
    status_badges   JSONB NOT NULL DEFAULT '{}'::jsonb,
    design_system   JSONB NOT NULL DEFAULT '{}'::jsonb,
    api_config      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

This file is real. The code that applies it (`apply_per_project_migrations`) is real. But neither is wired into `pipeline/orchestrator.py` — only the manual `test_step3_content.py --live` runner exercises this path end-to-end.

### Lucid central-DB migrations
`supabase/migrations/001_*.sql` through `020_project_members.sql` (20 files) — these are Lucid's *own* platform schema (users, chat_sessions, chat_messages, integrations, project_credentials, deployments, subscriptions, audit_log, etc.). They are not "generated for a customer project" — they're the Lucid product's own DB. Documented in [CLAUDE.md](CLAUDE.md).

## 11. Known gaps (honest read)

### G1. Production pipeline does not run any backend generation. **Not implemented at all.**
[pipeline/orchestrator.py:run_pipeline](ai_engine/app/services/pipeline/orchestrator.py#L187) — the live entry point reached from `ws.py` — never imports `backend_schema`, `supabase_provision`, `supabase_migrations`, or `supabase_content`. Confirmed with:

```
grep -rn "from app.services.backend_schema\|from app.services.supabase_provision\|persist_schema_to_supabase\|apply_per_project_migrations" ai_engine/app/
```

→ zero hits inside `ai_engine/app/`. All references are either inside the modules themselves, inside `project_generator.py` (legacy), or in `ai_engine/scripts/test_*.py`.

### G2. `backend_schema.py` is wired only into the legacy generator. **Implemented but never tested end-to-end; uncertain if even reached in production.**
Only caller: [project_generator.py:8155-8201](ai_engine/app/services/project_generator.py#L8155). Whether `project_generator.run_project_generation` is invoked at all in the current request flow needs runtime verification. Searched routers and pipelines — `orchestrator.py` does NOT call `run_project_generation`. So if the production flow only goes through `orchestrator.py`, this code is unreachable.

### G3. Per-project Supabase provisioning is implemented but unwired. **Implemented but never tested end-to-end** (outside the manual `--live` test script).
[supabase_provision.py:provision_for_project](ai_engine/app/services/supabase_provision.py#L188) is fully written: creates project, polls health, encrypts keys, upserts into `supabase_projects` table. Zero production callers.

### G4. Per-project content-tables migration apply is unwired. **Same as G3.**
[supabase_migrations.py:apply_per_project_migrations](ai_engine/app/services/supabase_migrations.py#L100) — applies the `001_content_tables.sql` to a customer project via the Mgmt API. Imported only by `test_step3_content.py`.

### G5. Schema → DB persist is unwired. **Same as G3.**
[supabase_content.py:persist_schema_to_supabase](ai_engine/app/services/supabase_content.py#L296) — mirrors `project_schema` into the `gen_*` tables. Imported only by `test_step3_content.py`.

### G6. Frontend never queries Supabase. **Not implemented at all.**
The generator emits no `supabase.from('foo').select(...)` code, no `useEffect` fetch hooks against Supabase, no server-component data loaders. Generated apps either render hardcoded JSX from `landing.json` or call the mock `db.json` shipped in the skeleton.

### G7. No type generation. **Not implemented at all.**
No `supabase gen types`, no zod schema, no shared `Database` type. The frontend would have to type its own queries by hand if it queried at all.

### G8. No data-model research. **Not implemented at all.**
No Gemini call is dedicated to "what tables / relationships should this app have." Entities flow from generic research markdown via a brittle text parser into `project_schema["entities"]`. The schema-generation Claude call sees field names + types + sample row, and is largely free-associating relationships from there.

### G9. No SQL validation. **Implemented but limited.**
The only check on Claude's emitted SQL is `"CREATE TABLE" in sql.upper()` ([backend_schema.py:279](ai_engine/app/services/backend_schema.py#L279)). No syntax check, no `pg_dump --schema-only` round-trip, no test-apply against a temp Postgres, no policy-presence check, no FK reachability check.

### G10. Generated `0001_init.sql` is never applied. **Works but limited.**
`backend_schema.write_supabase_migration` writes the file to disk and stops. The user has to manually paste into Supabase Dashboard SQL Editor. There is no `apply_user_migration(workspace, ref)` function that runs the generated SQL against the user's provisioned project.

### G11. No admin login wiring. **Not implemented at all.**
LoginPage.jsx ships in the admin skeleton but is never connected to `supabase.auth.signInWithPassword` / `signInWithOAuth`. The Supabase URL/anon-key env vars are emitted, but the auth flow is left as TODO for the user.

### G12. Two parallel schema stories. **Partially implemented, neither shipped end-to-end.**
- Story A: Lucid CMS via `gen_*` tables (per-project migration). Has code for full lifecycle, has tests, NOT wired into prod pipeline.
- Story B: Customer-domain tables via Claude-generated `0001_init.sql`. Has code, no tests for the SQL itself, NOT applied, frontend never reads it.

Neither delivers a working "user signs in to their admin panel and CRUDs their data" experience today.

### G13. No seed-data generation. **Works but limited.**
Mock rows are passed to Claude as a `sample:` hint and end up in the frontend's `db.json`. No `INSERT INTO` statements are generated for the user-domain tables. If the user applies `0001_init.sql`, their tables are empty.

### G14. RLS correctness is unverified. **Implemented but never tested end-to-end.**
The Claude prompt requires RLS policies, but no test exercises whether the policies emitted by Claude actually enforce ownership / role-based access. A policy like `USING (true)` would pass the `CREATE TABLE` check.

### G15. Admin-panel CRUD pages are LLM-generated, not codegen. **Implemented but never tested end-to-end.**
Unlike navigation.js / MarketingHeader / MarketingFooter (which have deterministic Python builders), `crud_list` and `crud_form` pages are written by Claude from prompts. No deterministic builder converts `project_schema["entities"]` into a typed CRUD page. Result quality depends entirely on Claude's freelancing.

## 12. Cross-reference with claimed capabilities

Without a current product/marketing page to read from, this section is based on what the codebase comments and the CLAUDE.md describe as the system's intent vs. what the code actually does. Mismatches:

| Claim (from code comments / docstrings) | Reality |
|---|---|
| `backend_schema.py:1-2` — *"generate a Supabase migration from project entities"* | ✅ Code exists and produces a migration string. ❌ Migration is never applied; frontend never reads from it. |
| `supabase_provision.py:1-15` — *"Owns the full lifecycle for the customer's isolated Supabase project"* | ✅ Code is complete. ❌ Not wired into the production pipeline. |
| `supabase_content.py:1-30` — *"Mirror project_schema into the customer's content tables"* | ✅ Functions exist. ❌ Not called from prod. |
| `001_content_tables.sql:6-9` — *"ai_engine applies it via the Mgmt API immediately after creating the project"* | ❌ The code that "applies it via the Mgmt API" (`apply_per_project_migrations`) has no production caller. |
| Module docstring [backend_schema.py:14-16](ai_engine/app/services/backend_schema.py#L14) — *"The frontend stays on mock data in this generation — wiring it to real Supabase is a separate (bigger) follow-up step we don't take yet."* | This one is honest — code matches the claim. The user-facing product implication ("you get a real Supabase backend") would not match this disclaimer. |
| `gen_revision` table — *"Every CMS write … should write one revision row first. Gives free undo and an audit trail."* | ❌ No code writes to `gen_revision` anywhere. Searched `ai_engine/` — zero INSERTs into `gen_revision`. The table exists; the CMS that fills it does not. |
| `purpose_research.py:319-321` — *"Future: add lead_generation / ecommerce / booking purpose-specific calls"* | Self-acknowledged TODO. Only `hiring` is implemented. |

---

**Cited file paths used to ground this report:**

- [ai_engine/app/services/backend_schema.py](ai_engine/app/services/backend_schema.py)
- [ai_engine/app/services/project_schema.py](ai_engine/app/services/project_schema.py)
- [ai_engine/app/services/project_generator.py](ai_engine/app/services/project_generator.py) (lines 5073, 7604-7690, 8155-8201, 8530-8575, 10938)
- [ai_engine/app/services/supabase_provision.py](ai_engine/app/services/supabase_provision.py)
- [ai_engine/app/services/supabase_migrations.py](ai_engine/app/services/supabase_migrations.py)
- [ai_engine/app/services/supabase_content.py](ai_engine/app/services/supabase_content.py)
- [ai_engine/app/services/supabase_mgmt.py](ai_engine/app/services/supabase_mgmt.py)
- [ai_engine/app/services/admin_navigation_config_builder.py](ai_engine/app/services/admin_navigation_config_builder.py)
- [ai_engine/app/services/pipeline/orchestrator.py](ai_engine/app/services/pipeline/orchestrator.py)
- [ai_engine/app/services/pipeline/__init__.py](ai_engine/app/services/pipeline/__init__.py)
- [ai_engine/app/services/pipeline/step3_classify.py](ai_engine/app/services/pipeline/step3_classify.py)
- [ai_engine/app/services/purpose_research.py](ai_engine/app/services/purpose_research.py)
- [ai_engine/app/services/landing_pipeline.py](ai_engine/app/services/landing_pipeline.py) (line 375)
- [ai_engine/app/routers/ws.py](ai_engine/app/routers/ws.py) (line 30)
- [supabase/migrations/per_project/001_content_tables.sql](supabase/migrations/per_project/001_content_tables.sql)
- [ai_engine/scripts/test_step1_schema.py](ai_engine/scripts/test_step1_schema.py)
- [ai_engine/scripts/test_step2_provision.py](ai_engine/scripts/test_step2_provision.py)
- [ai_engine/scripts/test_step3_content.py](ai_engine/scripts/test_step3_content.py)
