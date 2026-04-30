# Preview Lifecycle — End-to-End Cycle Test

Single user-flow that exercises every preview state transition. Run this once after each preview-related change. If any step shows a flicker, an unexpected error, or an iframe remount, the refactor regressed.

## Architecture invariants (must hold at all times)

1. **Iframe `src` is always the live dev server URL** (`https://lucid.shopsready.com/preview-{port}` in prod, `http://localhost:{port}` locally). It is **never** a Vercel URL.
2. **Iframe stays mounted** once it has been mounted with a URL. Transient state changes (auto-restart, WS reconnect, brief preview_error) overlay on top of it; they do not unmount it.
3. **Vercel URL** lives in `repoInfo.deployedUrl` and surfaces only via the "Open deployed site" button. It never enters the iframe.
4. **One source of truth** for what the preview tab shows: the `previewPhase` derivation in `RightPanel.js` (`fatal | live | live-with-restart-overlay | live-with-error-overlay | crashed | booting | wizard-empty | stopped | preparing`).

## Cycle test

### Step 1 — Wizard creates a brand-new project

1. From the dashboard, click "New project" → fill out wizard → submit.
2. Workspace opens at `/dashboard/engineer/workspace/{projectId}`.
3. **Expect**: right panel shows `Preparing workspace…` with sub-text matching the current status (e.g. `Connecting to the agent…`, then `Cloning the repository…`, then `Installing dependencies…`).
4. **Forbidden**: any flash of `Preview Not Available`, `Preview stopped`, or a blank iframe.

### Step 2 — Initial preview boots

5. Backend clones template, runs `pnpm install`, starts dev server.
6. **Expect**: sub-text smoothly transitions through `Installing dependencies…` → `Starting the dev server…` → `Waiting for the dev server to come up…`.
7. When `preview_ready` fires, iframe appears with the live URL.
8. **Forbidden**: iframe appears, disappears, then re-appears.

### Step 3 — Agent generates code (status: running)

9. The agent writes files. Preview iframe stays mounted; HMR picks up changes without a full reload.
10. Top edge of iframe shows the orange `animate-hmr-slide` strip while `status === "running"`.
11. **Expect**: iframe content updates without remounting (scroll position, form state preserved).
12. **Forbidden**: any blank iframe between writes.

### Step 4 — Config-file write triggers full reload

13. Agent writes `next.config.mjs` or `tailwind.config.js`.
14. After agent finishes (status transitions running → ready), a 2s timer fires `iframe.contentWindow.location.reload()`.
15. **Expect**: smooth in-place reload; same iframe element.
16. **Forbidden**: visible blank/grey flash (the old `src=""` flash technique was removed).

### Step 5 — Agent run completes

17. Status: `running` → `ready`. HMR strip disappears.
18. **Expect**: iframe reflects the new code.
19. **Forbidden**: redirect to a different URL, fallback to `Preview stopped`.

### Step 6 — Dev server crashes (transient, e.g. port collision after long task)

20. Wait for the watchdog to detect process exit.
21. Backend emits `preview_status` with message `Dev server stopped — restarting…`.
22. **Expect**: a translucent overlay appears **on top of the existing iframe** with the "Restarting preview…" message. The iframe stays mounted underneath.
23. Backend `start_local_preview` runs again, picks a fresh port, fires `preview_ready` with the new URL.
24. The iframe `src` swaps to the new URL; overlay fades out.
25. **Forbidden**: iframe unmounts, full-screen "Preview failed" panel during a transient restart.

### Step 7 — Dev server crashes hard (uptime <30s on relaunch)

26. Force a real failure (e.g. introduce a syntax error in `next.config.mjs`).
27. Backend's restart-loop guard refuses to auto-restart (uptime < 30s).
28. Frontend receives `preview_error` with stderr message.
29. **Expect**: the existing iframe stays mounted; an error overlay appears on top with the actual stderr message and a `Restart Preview` button.
30. Click `Restart Preview` → backend retries → success → overlay clears.

### Step 8 — Publish to Vercel

31. Click `Publish` from the workspace header.
32. Backend pushes `staging` → `main`; Vercel auto-builds.
33. Frontend receives `published` event with `vercelUrl`.
34. **Expect**: `repoInfo.deployedUrl` is set; the "Open deployed site" button becomes active.
35. **Forbidden**: iframe `src` changes to the Vercel URL.

### Step 9 — Open deployed site

36. Click the "Open deployed site" button.
37. **Expect**: opens Vercel URL in a new tab.
38. **Forbidden**: anything happens to the in-app iframe.

### Step 10 — Existing project re-entry

39. Close the workspace tab, reopen it from the dashboard.
40. **Expect**: `Preparing workspace…` appears immediately. WS reconnects. Backend restarts preview (same conversation_id reuses the session). `preview_ready` fires; iframe shows live URL.
41. **Forbidden**: iframe shows the old Vercel deploy URL.

## State map (for review)

| `previewPhase` | Iframe mounted? | UI shown |
|---|---|---|
| `fatal` | no | full-screen workspace error |
| `live` | yes | iframe only |
| `live-with-restart-overlay` | yes | iframe + translucent restarting overlay |
| `live-with-error-overlay` | yes | iframe + error overlay with stderr |
| `crashed` (no latched URL) | no | full-screen "Preview failed" |
| `booting` | no | full-screen "Setting up preview…" |
| `wizard-empty` | no | full-screen "Your canvas is ready" |
| `stopped` | no | full-screen "Preview stopped" |
| `preparing` | no | full-screen "Preparing workspace…" |

The latched URL ensures rows 2–4 share the same DOM iframe across transitions, eliminating remount-induced flicker.

## Files involved (for grep)

- `frontend/src/components/workspace/RightPanel.js` — `previewPhase` derivation, latched URL, overlay rendering.
- `frontend/src/hooks/useAgentSession.js` — WS event → state mapping (`previewUrl`, `previewError`, `previewLoading`, `previewEverReady`).
- `frontend/src/app/dashboard/engineer/workspace/[projectId]/page.js` — clears `repoInfo.vercelUrl` when `previewUrl` goes null; HMR reload on config changes.
- `ai_engine/app/services/local_preview.py` — `_watch_process_exit` auto-restart with 30s uptime guard; emits `preview_status: restarting`.
- `frontend/src/components/workspace/BuildingScreen.js` — full-page overlay during workspace build (separate from the preview tab).
