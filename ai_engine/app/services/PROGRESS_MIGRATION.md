# `progress` → typed events — migration recipe

Phase 2 Step 4 of the WebSocket refactor replaces the ~80 free-form
`{type: "progress", message: "..."}` emissions across the backend with
typed events that carry structured data. This file is the recipe.

> **Status (Phase 2 Step 2 cleanup, 2026-06-06):** The dual-emit window
> for all HIGH and MEDIUM impact callsites has closed. The legacy
> `progress` duplicates have been removed from the backend, and the FE
> `isLegacyDuplicateOfTypedEvent` matcher is a no-op safety net pending
> deletion. Future migrations skip step 7 below — emit the typed event
> only.

## Why

Today's free-form `progress` messages serve double duty: they
inform the user (text in chat) AND signal a milestone (which the
`task_phase` chart often shows duplicatively). The frontend has no
structured payload to consume, so it can't render anything richer than
a chat bubble — and it ends up showing the same info twice.

Typed events carry structured data the frontend can render properly
(chips in the chart, badges on cards, structured logs) and remove the
duplication.

## The recipe

For each free-form `progress` callsite:

### 1. Define the typed event helper in `llm_retry.py`

Naming: `<category>.<action>` (e.g. `image_binder.summary`,
`build.start`, `repo.create_done`).

```python
async def emit_<category>_<action>(
    websocket,
    *,
    # … structured fields …
) -> None:
    """One short docstring describing what this event signals."""
    if websocket is None:
        return
    try:
        await websocket.send_json({
            "type": "<category>.<action>",
            # … structured fields …
        })
    except Exception as exc:
        logger.debug("emit_<category>_<action>: ws send failed: %s", exc)
```

### 2. Wire it at the callsite, KEEPING the legacy progress emit

```python
# Phase 2 Step 4: typed event + legacy progress (dual-emit).
from app.services.llm_retry import emit_<category>_<action>
await emit_<category>_<action>(websocket, …)

# Legacy free-form — keep for one release for backward compat.
await websocket.send_json({
    "type": "progress",
    "message": f"✅ …",
})
```

### 3. Frontend: pure formatter in `frontend/src/lib/wsEvents.js`

Add a `format<Category><Action>` function returning `{ summary, detail,
log, subtext }`. Pure — no side effects. Easy to test.

### 4. Frontend: dispatcher branch in `useAgentSession.js`

```js
if (msg.type === '<category>.<action>') {
  const f = format<Category><Action>(msg);
  pushLog(f.log, 'system');
  setAgentStatus(prev => prev ? { ...prev, subtext: f.subtext } : null);
  return;
}
```

### 5. Frontend: suppress the legacy free-form duplicate

In `lib/wsEvents.js`, add to `isLegacyDuplicateOfTypedEvent`:

```js
// <category>.<action> legacy message
if (/^✅ <pattern>$/.test(message)) return true;
```

The `progress` branch already calls this helper and returns early when
true.

### 6. Tests

- Backend: `tests/ws/test_<category>_event.py` — 5 tests covering shape,
  defaults, fail-soft.
- Frontend: `frontend/src/lib/wsEvents.test.js` — extend the existing
  file with tests for the new formatter and legacy-pattern matcher.

### 7. After 1 release: delete the legacy emit

When old clients have rolled forward, delete the
`websocket.send_json({"type": "progress", ...})` from the callsite and
the corresponding entry in `isLegacyDuplicateOfTypedEvent`. The typed
event is now the only path.

## Catalog of pending migrations

These free-form `progress` callsites are next in line, grouped by
natural category:

### High-impact (have structured data, currently lossy as text)
- `landing_image_binder.bind_landing_images` summary — ✅ DONE (image_binder.summary)
- `landing_quality_gate` final summary — ✅ DONE (quality.summary)
- `build_validator` per-build emissions — ✅ DONE (build.start / build.result)
- `agent_orchestrator` repo creation — ✅ DONE (repo.create_started / repo.create_done) — lives in pipeline/orchestrator.py

### Medium-impact (mostly informational, but duplicate task_phase)
- `landing_fixers._run` "🔧 Running landing fixers..." — ✅ DONE (fixers.run_started)
- `landing_brief` "📋 Distilling research..." — ✅ DONE (brief.distill_started)
- `landing_*_research` "🔬 Researching..." — ✅ DONE (research.started with kind discriminator)
  - Wired at pipeline/step4_explore.py; legacy project_generator.py emits left as-is (deprecated path)
- `claude_cli` session start — ✅ DONE (cli.session_started — only one progress emit exists in claude_cli, so `cli.edit_started`/`cli.edit_done` from the original sketch was unneeded)
- `pipeline/step5_execute` writing files — ✅ DONE (code.write_started with task_type + model)

### Low-impact (purely diagnostic, can stay as `progress`)
- `workspace_manager` sweep + reaper messages
- `external_project_index` indexing progress
- `design_system_builder` per-step progress

The low-impact category may stay as `progress` (or move to logs only)
since they're developer-facing noise the typical user never reads.

## What "done" looks like for Step 4

Step 4 is complete when:
1. All HIGH-IMPACT callsites are migrated to typed events.
2. The frontend has structured renderers for each (chip, badge, or
   compact log line).
3. The legacy `progress` emissions are deleted from the migrated callsites.
4. `useAgentSession`'s `progress` branch handles only true ad-hoc
   developer messages (or is silenced in production UI).

This file should be updated as each callsite migrates.
