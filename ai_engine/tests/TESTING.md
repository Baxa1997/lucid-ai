# ai_engine — Testing Notes

Living doc for the refactor's test infrastructure. Updated as the
status/error refactor progresses.

## Running tests

```bash
# Fast baseline (no live API keys needed — runs in <10 min in container)
docker compose exec ai_engine pytest tests/ --ignore=tests/test_admin_pipeline_skeleton.py --timeout=15

# Single file (when iterating)
docker compose exec ai_engine pytest tests/test_data_model.py -v

# By marker (once tests are tagged in Phase 1)
docker compose exec ai_engine pytest -m "unit or integration"
```

## Marker convention (pytest.ini)

| Marker        | Meaning                                                |
| ------------- | ------------------------------------------------------ |
| `unit`        | Pure unit, no network/DB/live API. CI default.         |
| `integration` | Mock-based pipeline test. CI default.                  |
| `e2e`         | Hits Claude / Gemini / Vertex / Stripe. Costs money.   |
| `live`        | Hits live Supabase / Unsplash. Pre-existing convention. Auto-skip via skipif when env vars missing. |
| `ws`          | WebSocket contract / trace replay tests.               |
| `slow`        | >5s wall time. Excluded from PR checks.                |
| `asyncio`     | pytest-asyncio test marker (third-party plugin).       |

`live` and `e2e` overlap conceptually but serve different histories:
- `live` is the OLD convention already used in several files for tests
  that need real Supabase migrations or Unsplash. Leaving in place
  avoids touching ~5 working files.
- `e2e` is the NEW convention for live Claude / Gemini / Vertex tests
  added during the refactor. Use this for new tests.

Selection examples:
```bash
pytest                       # everything except files outside testpaths
pytest -m "not live"         # default CI excludes Supabase migration tests
pytest -m "not live and not e2e"  # everything offline
pytest -m e2e                # only paid-API tests
pytest -m live               # only Supabase / Unsplash live tests
```

Mark new tests with the right marker. Pytest is configured
`--strict-markers`, so unknown markers fail fast at collection time.

### Files with `live`-marked classes

These files have a mix of unit tests + a `@pytest.mark.live` class.
Default `pytest` runs the unit tests; the live class is selected only
with `-m live`:

- `test_admin_brand_extractor.py` — `class TestAdminBrandExtractorLive`
- `test_admin_data_model_planner.py` — `class TestAdminPlannerLive`
- `test_data_model_planner.py` — `class TestDataModelPlannerLive`
- `test_tenant_sql_generator.py` — `class TestLiveApply`

### Files marked `live` at module level (whole file skips by default)

- `test_set_tenant_row.py` — needs Supabase
- `test_tenant_provisioning.py` — needs Supabase
- `test_get_tenant_collection_authenticated.py` — needs Supabase
- `test_image_binding.py` — needs Unsplash + uses script-runner pattern (Phase 1 added the module-level marker)

### Files that look like tests but are scripts

- `test_purpose_classifier.py` — has no `def test_*` (uses `async def run_live_cases()` instead). Run manually:
  ```bash
  docker compose exec ai_engine python /app/tests/test_purpose_classifier.py
  ```
  Will be moved to `scripts/` directory in a later phase.

## Baseline (2026-06-05)

Captured pre-refactor, in `tests/baseline_2026-06-05.txt`.

| Category                | Count |
| ----------------------- | ----- |
| **Total collected**     | 413   |
| **Excluded** (slow: `test_admin_pipeline_skeleton.py`) | 13 |
| **Ran**                 | 400   |
| **Passed**              | ~373  |
| **Failed**              | 9     |
| **Errored** (setup/DB)  | 18    |

The 9 failures + 18 errors are PRE-EXISTING — the refactor will not
attempt to fix them, only ensure they don't grow.

### Known pre-existing failures (refactor must not change these)

Failures (tests run but assertions failed):
- `test_admin_foundation_builder.py::test_paths_match_admin_plan_routes`
- `test_admin_plan.py::test_each_table_gets_three_pages`
- `test_admin_plan.py::test_empty_data_model_produces_minimal_plan`
- `test_get_tenant_collection_authenticated.py::test_linked_admin_reads_parent_tenant`
- `test_get_tenant_collection_authenticated.py::test_linked_admin_membership_checked_on_admin_not_parent`
- `test_tenant_sql_generator.py::TestLiveApply::test_restaurant_sql_applies_cleanly`
- `test_tenant_sql_generator.py::TestLiveApply::test_applied_tables_exist_with_expected_columns`
- `test_tenant_sql_generator.py::TestLiveApply::test_idempotent_double_apply`
- `test_tenant_sql_generator.py::TestLiveApply::test_updated_at_trigger_fires`

Errors (tests didn't run due to setup failure — typically need Supabase / live LLM):
- `test_admin_data_model_planner.py::TestAdminPlannerLive::*` (6 tests)
- `test_data_model_planner.py::TestRestaurantDataModel::*` (4 tests)
- `test_data_model_planner.py::TestEcommerceDataModel::*` (3 tests)
- `test_data_model_planner.py::TestBlogDataModel::*` (3 tests)
- `test_data_model_planner.py::TestPortfolioDataModel::*` (1 test)
- `test_data_model_planner.py::TestSaasLandingDataModel::*` (1 test)

After the refactor:
- pass/fail/error counts must not GROW
- they may SHRINK as we fix things along the way
- if a pre-existing failure newly passes, great — note it in the PR

## Deferred until Claude/Gemini tokens are available

The following test files were NOT run in the baseline because they hit
live LLM APIs and no tokens are currently provisioned. They should
all be tagged `@pytest.mark.e2e` in Phase 1 cleanup and re-run with
keys once available.

### Live-API test files (root-level, not in `tests/`)

| File                                     | API     |
| ---------------------------------------- | ------- |
| `test_landing_e2e.py`                    | Claude+Gemini |
| `test_brief_e2e.py`                      | Gemini  |
| `test_website_orchestrator.py`           | Claude  |
| `test_website_pipeline_integration.py`   | Claude  |
| `test_domain_research_e2e.py`            | Gemini  |
| `test_design_research_e2e.py`            | Gemini  |
| `test_research_to_brief_e2e.py`          | Both    |
| `test_intent_e2e.py`                     | Gemini  |
| `test_landing_research.py`               | Gemini  |
| `test_website_research.py`               | Gemini  |
| `test_vertex_e2e_smoke.py`               | Vertex  |
| `test_vertex.py`                         | Vertex  |
| `test_vertex_models.py`                  | Vertex  |
| `test_vertex_shim.py`                    | Vertex  |
| `test_clarity_agent.py`                  | Claude  |
| `test_claude.py`                         | Claude  |
| `test_resolve_llm_smoke.py`              | Claude  |
| `test_expand_page_chain_smoke.py`        | Claude  |
| `test_page_codegen_smoke.py`             | Claude  |
| `test_homepage_builder.py`               | Claude  |
| `test_research_pipeline.py`              | Gemini  |
| `test_website_pipeline_gemini_stages.py` | Gemini  |

**To run once you have tokens:**
```bash
# Export keys (or load from .env)
export ANTHROPIC_API_KEY=...
export GOOGLE_API_KEY=...
docker compose exec ai_engine pytest -m e2e
```

## WebSocket trace capture

Phase 0 added an opt-in WS trace recorder. Set `WS_TRACE_FILE` env var on
the ai_engine container; every WebSocket send/receive gets appended to
that file as JSONL.

### Capturing the golden corpus

```bash
# 1. Set the trace path in docker-compose env (one-shot for the test run)
WS_TRACE_FILE=/app/tests/fixtures/ws_traces/landing_wizard_1.jsonl docker compose restart ai_engine

# 2. Run one generation through the frontend (real flow)
#    — open https://localhost:3000, type a prompt, watch it complete

# 3. Stop tracing
WS_TRACE_FILE= docker compose restart ai_engine
```

### Target corpus (Phase 0.2)

| Trace file                                  | What it captures                       |
| ------------------------------------------- | -------------------------------------- |
| `landing_wizard_1.jsonl`                    | New landing page from dashboard prompt |
| `website_wizard_1.jsonl`                    | New website (multi-page)               |
| `admin_wizard_1.jsonl`                      | New admin panel                        |
| `landing_edit_1.jsonl`                      | Follow-up edit on existing project     |
| `landing_failure_1.jsonl`                   | Mid-pipeline failure (revoke API key)  |

These become the regression corpus used by the WS-replay test
(`tests/ws/test_ws_trace_replay.py`, written in Phase 0.3 once tokens
are available to actually run a generation).

## Frontend tests (Vitest)

```bash
docker compose exec frontend npm test          # one-shot
docker compose exec frontend npm run test:watch  # interactive
```

### Phase 0.4 status

- ✅ Vitest + @vitejs/plugin-react + jsdom + Testing Library installed
- ✅ `src/test-setup.js` provides sessionStorage / WebSocket shims
- ✅ `src/components/workspace/buildingLabel.test.js` — 23 tests pass

Coverage today: `computeBuildLabel` (the most-branched pure function in
the status pipeline). This is the regression gate for the Phase 2 status
refactor — every existing input must produce the same output until we
intentionally redesign a branch.

### Phase 1 backlog

- `useAgentSession.test.js` — the 9-state state machine
- `useAgentSession.test.js` — WS message dispatch (one test per message type)
- Reconnect / replay survival tests

These need WS mocking infrastructure that's bigger than the buildingLabel
shim — deferred until the Phase 1 test cleanup pass.

---

## Phase 1 status (2026-06-05)

### Done
- `pytest.ini` updated to recognize `live`, `asyncio` as known markers
- `test_image_binding.py` got module-level `pytestmark = [pytest.mark.live, pytest.mark.skipif(no UNSPLASH_ACCESS_KEY)]`
- Verified `--strict-markers` doesn't reject existing `live` usages
- Verified `pytest -m "not live"` correctly deselects live classes (3 passed, 6 deselected on `test_admin_data_model_planner.py`)

### Deferred
- **Directory reorg** (move root-level e2e files into `tests/e2e/`): risky without first proving the live tests pass when run. Will do after Claude/Gemini tokens are available and we can verify imports survive the move.
- **Adding `unit`/`integration` markers to every file**: optional. The default `pytest` run already does the right thing. The markers add value only if we later want fine-grained CI tiers (e.g. `pytest -m unit` for the pre-commit hook).
- **WS coverage tests** (`tests/ws/`): need WS trace fixtures first, which need a live generation, which needs tokens.

### Gaps still open (no test coverage)
- `agent_orchestrator.py`
- `workspace_resolver.py`
- `routers/ws.py` (only indirect coverage via `test_admin_pipeline_skeleton.py::TestProgressUpdates`)
- `landing_pipeline.py`
- `useAgentSession.js` state machine (only `buildingLabel` is covered today)

These are good candidates for the next batch of test writing — they don't need API keys and would shore up the safety net before any Phase 2 refactor step.
