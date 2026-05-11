"""Local dev server preview for Lucid AI projects.

Runs the project's npm/pnpm/yarn dev server as a subprocess inside the
ai_engine container.  Files are read directly from the workspace on disk —
no upload step, no sandbox — so HMR works instantly when the agent writes
a file.

URL strategy
------------
  Development (no PREVIEW_DOMAIN env var):
      http://localhost:{port}   — exposed via docker-compose port mapping

  Production (PREVIEW_DOMAIN=preview.yourdomain.com):
      https://preview-{port}.preview.yourdomain.com
      Caddy wildcard proxy routes *.preview.yourdomain.com → host port.

Port range: 4000-4050 (configure via PREVIEW_PORT_START / PREVIEW_PORT_END).

Registry is keyed by conversation_id (stable across pipeline runs).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import socket
from typing import Optional

from fastapi import WebSocket

logger = logging.getLogger("lucid.local_preview")

# ── Port range ────────────────────────────────────────────
_PORT_START = int(os.environ.get("PREVIEW_PORT_START", "4000"))
_PORT_END   = int(os.environ.get("PREVIEW_PORT_END",   "4050"))

# ── Registry: conversation_id → {port, process, workspace_path, url} ──
_active_servers: dict[str, dict] = {}

# ── Per-conversation start lock: serializes overlapping start_local_preview
# calls (e.g. the exit-watcher's auto-restart racing the user's "Restart
# Preview" click). Without it both can spawn dev servers on different ports.
_start_locks: dict[str, asyncio.Lock] = {}


def _start_lock(conversation_id: str) -> asyncio.Lock:
    lock = _start_locks.get(conversation_id)
    if lock is None:
        lock = asyncio.Lock()
        _start_locks[conversation_id] = lock
    return lock

# ── Skip list for package.json dev script detection ──────
_NO_DEV_DIRS = frozenset({"node_modules", ".git", ".next", "dist", "build"})


# ══════════════════════════════════════════════════════════
#  Public API
# ══════════════════════════════════════════════════════════

async def start_local_preview(
    *,
    workspace_path: str,
    conversation_id: str,
    websocket: WebSocket,
    package_manager: str = "npm",
    force_restart: bool = False,
) -> Optional[str]:
    """Start a dev server for the workspace, return its public URL.

    If a server is already running for this conversation and ``force_restart``
    is False, returns the existing URL immediately (HMR handles incremental
    file changes). When ``force_restart`` is True, any existing server is
    killed first and a fresh one is started — used by the "Restart Preview"
    button so a wedged-but-bound process doesn't get reused.

    Returns None if the workspace has no 'dev' script or startup fails.
    """
    if not _has_dev_script(workspace_path):
        logger.info("local_preview: no 'dev' script in package.json — skipping preview")
        return None

    # Serialize concurrent starts for this conversation. The exit-watcher's
    # auto-restart and a user-clicked "Restart Preview" can both call this
    # function within milliseconds; without the lock they each spawn a
    # dev server on a different port and the frontend gets two preview_ready
    # events.
    async with _start_lock(conversation_id):
        return await _start_local_preview_locked(
            workspace_path=workspace_path,
            conversation_id=conversation_id,
            websocket=websocket,
            package_manager=package_manager,
            force_restart=force_restart,
        )


async def _start_local_preview_locked(
    *,
    workspace_path: str,
    conversation_id: str,
    websocket: WebSocket,
    package_manager: str,
    force_restart: bool,
) -> Optional[str]:
    """The actual start logic — runs under _start_lock(conversation_id)."""
    # ── Re-use existing server if still alive AND same workspace ─
    existing = _active_servers.get(conversation_id)
    if existing and _process_alive(existing.get("process")):
        if force_restart:
            logger.info(
                "local_preview: force_restart requested — killing existing server on port %d for %s",
                existing.get("port", 0), conversation_id,
            )
            await _kill_server(existing)
            _active_servers.pop(conversation_id, None)
            # Wait for the port to be fully released so the next bind doesn't
            # race the kernel's TIME_WAIT state.
            stale_port = existing.get("port")
            if isinstance(stale_port, int):
                await _wait_port_released(stale_port, timeout=5)
        elif existing.get("workspace_path") == workspace_path:
            # Same workspace — reuse (HMR handles file changes)
            logger.info("local_preview: reusing server on port %d for %s",
                        existing["port"], conversation_id)
            await _emit(websocket, "preview_ready",
                        preview_url=existing["url"],
                        message=f"🖥️ Preview refreshed at {existing['url']}")
            return existing["url"]
        else:
            # New pipeline run has a different workspace path — restart server
            logger.info("local_preview: workspace changed, restarting server for %s", conversation_id)
            await _kill_server(existing)
            _active_servers.pop(conversation_id, None)
            stale_port = existing.get("port")
            if isinstance(stale_port, int):
                await _wait_port_released(stale_port, timeout=5)
    elif existing:
        # Dead process — clean up stale entry
        await _kill_server(existing)
        _active_servers.pop(conversation_id, None)

    # ── Pre-flight: restore template UI files from git (fixes corrupted ui/ files) ─
    try:
        from app.services.post_generation_fixer import restore_template_ui_files
        if restore_template_ui_files(workspace_path):
            logger.info("local_preview: restored src/components/ui/ from git HEAD")
    except Exception as _ui_err:
        logger.debug("local_preview: UI restore skipped: %s", _ui_err)

    # ── Pre-flight: restore missing @tailwind directives in globals.css ─
    # If Claude's rewrite of the theme CSS stripped the @tailwind base/
    # components/utilities directives, every component renders unstyled.
    # This fixer prepends them when absent.
    try:
        from app.services.post_generation_fixer import fix_missing_tailwind_directives
        patched_css = fix_missing_tailwind_directives(workspace_path)
        if patched_css:
            logger.warning(
                "local_preview: restored @tailwind directives in %d CSS file(s): %s",
                len(patched_css), patched_css,
            )
    except Exception as _css_err:
        logger.debug("local_preview: tailwind directive fixer skipped: %s", _css_err)

    # ── Pre-flight: unescape HTML entities that leaked into JSX attributes ─
    # Claude sometimes writes className=&quot;...&quot; instead of className="..."
    # which the SWC parser rejects with "Expression expected". Unescape first.
    try:
        from app.services.post_generation_fixer import fix_html_entities_in_attributes
        fixed_attrs = fix_html_entities_in_attributes(workspace_path)
        if fixed_attrs:
            logger.warning(
                "local_preview: unescaped HTML entities in attributes of %d file(s): %s",
                len(fixed_attrs), fixed_attrs,
            )
    except Exception as _attr_err:
        logger.debug("local_preview: attribute-entity fixer skipped: %s", _attr_err)

    # ── Pre-flight: fix dynamic route conflicts ───────────
    try:
        from app.services.post_generation_fixer import fix_dynamic_route_conflicts
        removed = fix_dynamic_route_conflicts(workspace_path)
        if removed:
            logger.info("local_preview: removed %d conflicting dynamic route dir(s): %s",
                        len(removed), removed)
    except Exception as _fix_err:
        logger.debug("local_preview: route conflict fixer skipped: %s", _fix_err)

    # ── Pre-flight: fix named-import / default-export mismatches ─
    # Case A: `import { X }` from a file that only has `export default function X`
    try:
        from app.services.post_generation_fixer import fix_named_import_default_export_mismatch
        mismatches = fix_named_import_default_export_mismatch(workspace_path)
        if mismatches:
            logger.info("local_preview: fixed %d named-import mismatch(es): %s",
                        len(mismatches), mismatches)
    except Exception as _fix_err2:
        logger.debug("local_preview: import mismatch fixer skipped: %s", _fix_err2)

    # Case B: `import X from './X'` but X.jsx has no `export default` (only named export)
    try:
        from app.services.post_generation_fixer import fix_missing_default_export
        missing = fix_missing_default_export(workspace_path)
        if missing:
            logger.info("local_preview: added missing default export in %d file(s): %s",
                        len(missing), missing)
    except Exception as _fix_err3:
        logger.debug("local_preview: missing-default-export fixer skipped: %s", _fix_err3)

    # ── Pre-flight: replace banned lucide-react icons with inline SVGs ──
    # Safety net for files written AFTER landing_fixers ran (e.g. late
    # post-write callbacks). Without this, MarketingFooter.jsx etc. can
    # surface "Twitter is not exported from lucide-react" 500s on first
    # render even though landing_fixers polyfilled the rest.
    try:
        from app.services.post_generation_fixer import fix_banned_icons
        banned_fixed = fix_banned_icons(workspace_path)
        if banned_fixed:
            logger.info("local_preview: polyfilled banned lucide icons in %d file(s): %s",
                        len(banned_fixed), banned_fixed)
    except Exception as _fix_err4:
        logger.debug("local_preview: banned-icons fixer skipped: %s", _fix_err4)

    # ── Pre-flight: eslint --fix (auto-fixes unescaped entities, etc.) ──
    # Runs after all code fixers so ESLint operates on the corrected files.
    # Fixes are committed in Phase 7, so Vercel also receives clean code.
    try:
        _eslint_cmd = (
            "npx eslint --fix 'src/**/*.{js,jsx,ts,tsx}' "
            "--rule 'react/no-unescaped-entities: error' "
            "--no-ignore 2>/dev/null || true"
        )
        _eslint_proc = await asyncio.create_subprocess_shell(
            _eslint_cmd,
            cwd=workspace_path,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(_eslint_proc.wait(), timeout=30)
        logger.info("local_preview: eslint --fix completed")
    except Exception as _esl_err:
        logger.debug("local_preview: eslint --fix skipped: %s", _esl_err)

    # ── Pre-flight: npm install if node_modules is missing ─────────────
    # Repos are cloned fresh (no node_modules in git). Without this step
    # Next.js/Vite can't find tailwindcss/postcss → page renders unstyled.
    await _ensure_node_modules(workspace_path, websocket)

    # ── Pre-flight: wipe .next so the RSC client-reference-manifest can't
    # carry stale module pointers across pre-flight fixers. Without this, the
    # dev server can serve "Could not find the module …MarketingHeader.jsx#default
    # in the React Client Manifest" until the user refreshes — files were
    # rewritten by the fixers above but the on-disk manifest still points at
    # the pre-fixer chunks.
    import shutil as _shutil
    _next_cache_dir = os.path.join(workspace_path, ".next")
    if os.path.isdir(_next_cache_dir):
        try:
            await asyncio.to_thread(_shutil.rmtree, _next_cache_dir, ignore_errors=True)
            logger.info("local_preview: wiped .next cache before dev server boot")
        except Exception as _wipe_err:
            logger.debug("local_preview: .next wipe skipped: %s", _wipe_err)

    # ── Port allocation + spawn + health check — retryable on port race ──
    # Between _find_free_port releasing the port and the dev server binding
    # it, another process can grab it (EADDRINUSE). Up to 3 attempts with
    # a fresh port each time. Retry ONLY for port-in-use; other failures
    # (code crash, health-check timeout) are non-retryable and surface
    # the error to the user immediately.
    import tempfile

    _node_paths = "/usr/local/bin:/usr/bin:/bin"
    tried_ports: set[int] = set()

    for _attempt in range(3):
        port = _find_free_port(_PORT_START, _PORT_END, exclude=tried_ports)
        if port is None:
            logger.error("local_preview: no free port in %d-%d", _PORT_START, _PORT_END)
            await _emit(websocket, "preview_error",
                        error_stage="port",
                        message="No preview ports available — please wait and retry.")
            return None
        tried_ports.add(port)

        # Patch next.config.mjs with basePath/assetPrefix so /_next/ assets
        # are served at /preview-{port}/_next/ — matches our nginx proxy rule.
        await _patch_nextjs_base_path(workspace_path, port)

        cmd = _build_start_cmd(workspace_path, package_manager, port)
        logger.info(
            "local_preview: starting [%s] on port %d for %s (attempt %d/3)",
            cmd, port, conversation_id, _attempt + 1,
        )

        if _attempt == 0:
            await _emit(websocket, "preview_status", status="starting",
                        message="Starting dev server…")

        # Fresh stderr file per attempt — previous attempt's file is kept
        # until that attempt's _stop_by_conversation cleanup runs.
        _stderr_file = tempfile.NamedTemporaryFile(
            mode="w", delete=False, suffix=".log", prefix="lucid_preview_stderr_"
        )
        _stderr_path = _stderr_file.name
        _stderr_file.close()

        try:
            env = {
                **os.environ,
                "PORT": str(port),
                "HOST": "0.0.0.0",
                "HOSTNAME": "0.0.0.0",
                "PATH": f"{os.environ.get('PATH', _node_paths)}:{_node_paths}",
                # Next.js: picked up by next.config.mjs assetPrefix
                "NEXT_PUBLIC_ASSET_PREFIX": f"/preview-{port}",
                "NEXT_TELEMETRY_DISABLED": "1",
                # CRA / webpack: sets the public base path for all asset URLs
                "PUBLIC_URL": f"/preview-{port}",
                # Prevent react-scripts from opening a browser window
                "BROWSER": "none",
                # Prevent react-scripts from treating warnings as errors in CI
                "CI": "false",
            }

            with open(_stderr_path, "w") as _stderr_fh:
                proc = await asyncio.create_subprocess_shell(
                    cmd,
                    cwd=workspace_path,
                    stdout=_stderr_fh,
                    stderr=_stderr_fh,
                    env=env,
                    start_new_session=True,
                )

            import time as _time
            _active_servers[conversation_id] = {
                "port": port,
                "process": proc,
                "workspace_path": workspace_path,
                "stderr_path": _stderr_path,
                "url": "",
                "started_at": _time.time(),
            }

            if _attempt == 0:
                await _emit(websocket, "preview_status", status="health_check",
                            message="Waiting for dev server to start…")
            await _wait_for_server(port, proc=proc, timeout=90)

            preview_url = _build_url(port)
            _active_servers[conversation_id]["url"] = preview_url
            _active_servers[conversation_id]["watcher"] = asyncio.create_task(
                _watch_process_exit(conversation_id, proc, _stderr_path, websocket)
            )
            _active_servers[conversation_id]["health_watcher"] = asyncio.create_task(
                _watch_http_health(conversation_id, port, websocket)
            )

            logger.info("local_preview: ready at %s", preview_url)
            await _emit(websocket, "preview_ready",
                        preview_url=preview_url,
                        message=f"🖥️ Live preview: {preview_url}")
            await _save_preview_url(conversation_id, preview_url)
            return preview_url

        except _ProcessExitedError:
            stderr_snippet = _read_stderr(_stderr_path)
            if _is_port_in_use_error(stderr_snippet) and _attempt < 2:
                logger.warning(
                    "local_preview: port %d already in use (race) — retrying with fresh port",
                    port,
                )
                await _stop_by_conversation(conversation_id)
                continue
            summary = _summarize_dev_server_error(stderr_snippet)
            logger.error(
                "local_preview: dev server exited early (port %d): %s",
                port, summary[:500],
            )
            await _stop_by_conversation(conversation_id)
            await _emit(websocket, "preview_error",
                        error_stage="crashed",
                        message=f"Dev server crashed on startup:\n{summary[:1500]}")
            return None

        except asyncio.TimeoutError:
            stderr_snippet = _read_stderr(_stderr_path)
            summary = _summarize_dev_server_error(stderr_snippet)
            logger.error(
                "local_preview: timed out waiting for dev server on port %d. stderr: %s",
                port, summary[:500],
            )
            await _stop_by_conversation(conversation_id)
            await _emit(websocket, "preview_error",
                        error_stage="timeout",
                        message=f"Dev server didn't start in time. Last output:\n{summary[:1500]}")
            return None

        except Exception as exc:
            stderr_snippet = _read_stderr(_stderr_path)
            summary = _summarize_dev_server_error(stderr_snippet)
            logger.error(
                "local_preview: start failed: %s | stderr: %s",
                exc, summary[:500], exc_info=True,
            )
            await _stop_by_conversation(conversation_id)
            await _emit(websocket, "preview_error",
                        error_stage="start",
                        message=f"Preview failed to start: {str(exc)[:200]}\n{summary[:1500]}")
            return None

    # All 3 attempts hit EADDRINUSE — extremely rare, surface to user
    logger.error("local_preview: all %d attempts hit port-in-use races", len(tried_ports))
    await _emit(websocket, "preview_error",
                error_stage="port",
                message="Preview ports are busy — please wait a moment and retry.")
    return None


def get_active_preview_url(conversation_id: str = "", workspace_path: str = "") -> Optional[str]:
    """Return the active preview URL for a session, or None."""
    key = conversation_id or workspace_path
    entry = _active_servers.get(key)
    if entry and _process_alive(entry.get("process")):
        return entry.get("url")
    return None


async def stop_local_preview(conversation_id: str = "", workspace_path: str = "") -> None:
    """Stop and clean up the dev server for a session."""
    key = conversation_id or workspace_path
    entry = _active_servers.pop(key, None)
    if entry:
        await _kill_server(entry)
        logger.info("local_preview: stopped server for %s", key)


async def sync_files_to_preview(
    workspace_path: str,
    conversation_id: str = "",
    changed_files: list[str] | None = None,
) -> None:
    """No-op — HMR watches disk files directly, no sync needed."""
    # With a local dev server, file changes on disk are picked up by HMR
    # automatically. The function still exists because legacy callers expect
    # the symbol to be importable.
    logger.debug("local_preview: sync_files_to_preview is a no-op (HMR handles it)")


async def extend_sandbox_timeout(
    conversation_id: str = "", workspace_path: str = "", extra_seconds: int = 1800
) -> None:
    """No-op — local processes don't time out the way cloud sandboxes do."""
    pass


async def stop_all_previews() -> None:
    """Stop all running dev servers — called on server shutdown."""
    keys = list(_active_servers.keys())
    for key in keys:
        entry = _active_servers.pop(key, None)
        if entry:
            await _kill_server(entry)
    logger.info("local_preview: stopped all dev servers")


# ══════════════════════════════════════════════════════════
#  Internal helpers
# ══════════════════════════════════════════════════════════

def _find_free_port(
    start: int, end: int, exclude: Optional[set[int]] = None
) -> Optional[int]:
    _excl = exclude or set()
    for port in range(start, end):
        if port in _excl:
            continue
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind(("0.0.0.0", port))
                return port
        except OSError:
            continue
    return None


def _is_port_in_use_error(stderr: str) -> bool:
    """True if *stderr* indicates another process grabbed the port first.

    Covers Node (EADDRINUSE), Next.js ("port is already in use"), Vite,
    and generic POSIX ("address already in use").
    """
    s = (stderr or "").lower()
    return (
        "eaddrinuse" in s
        or "address already in use" in s
        or "port is already in use" in s
    )


def _build_url(port: int) -> str:
    # Option 1: PREVIEW_BASE_URL — path-based proxy via existing HTTPS domain.
    #   e.g. PREVIEW_BASE_URL=https://lucid.shopsready.com
    #   → https://lucid.shopsready.com/preview-4001/
    #   Nginx routes location ~ ^/preview-(\d+)/(.*) → localhost:$1/$2
    #   Works with existing SSL cert — no wildcard cert needed.
    # Production servers set PREVIEW_BASE_URL in their environment (server
    # docker-compose / systemd / pipeline). Local dev MUST leave it unset —
    # the production proxy URL doesn't reach a dev server running on a laptop.
    base_url = os.environ.get("PREVIEW_BASE_URL", "").strip().rstrip("/")
    if base_url:
        return f"{base_url}/preview-{port}"

    # Option 2: PREVIEW_DOMAIN — subdomain-based (requires wildcard SSL + DNS).
    #   e.g. PREVIEW_DOMAIN=preview.yourdomain.com
    #   → https://preview-4001.preview.yourdomain.com
    domain = os.environ.get("PREVIEW_DOMAIN", "").strip()
    if domain:
        return f"https://preview-{port}.{domain}"

    # Default for local development — browser connects directly to the dev
    # server via the docker port mapping (4000-4050).
    return f"http://localhost:{port}"


def _build_start_cmd(workspace_path: str, package_manager: str, port: int) -> str:
    """Return the shell command to start the dev server on the given port.

    Strategy: invoke the framework binary directly via node_modules/.bin instead
    of going through `pnpm run dev`.  This avoids two problems:
      1. pnpm passes `--` literally to the script, breaking `next dev -- -p PORT`.
      2. Wrapper scripts (concurrently, npm-run-all) treat appended flags as
         extra commands to run, causing immediate failures.

    Direct binary invocation also lets us always pass -p PORT -H 0.0.0.0 reliably.
    HOSTNAME=0.0.0.0 is also set in the caller's env as a belt-and-suspenders fix.
    """
    pkg_path = os.path.join(workspace_path, "package.json")
    is_next = False
    is_vite = False
    is_cra = False
    scripts: dict = {}
    try:
        with open(pkg_path) as f:
            pkg = json.load(f)
        deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
        scripts = pkg.get("scripts", {})
        is_next = "next" in deps
        is_vite = "vite" in deps
        is_cra = (
            "react-scripts" in deps
            or "react-scripts" in str(scripts.get("start", ""))
            or "react-scripts" in str(scripts.get("dev", ""))
        )
    except Exception:
        pass

    pm = _detect_pm(workspace_path) or package_manager

    # Resolve binary paths relative to the workspace (works even if not in global PATH)
    next_bin = "./node_modules/.bin/next"
    vite_bin = "./node_modules/.bin/vite"
    cra_bin  = "./node_modules/.bin/react-scripts"

    if is_next:
        # Direct Next.js invocation: no script wrapper, flags work correctly.
        # -H 0.0.0.0 makes it bind to all interfaces so Docker port-forwarding works.
        logger.info("local_preview: Next.js detected — invoking %s directly", next_bin)
        return f"PORT={port} {next_bin} dev -p {port} -H 0.0.0.0"
    elif is_vite:
        logger.info("local_preview: Vite detected — invoking %s directly", vite_bin)
        # --base sets the public base path so Vite generates /@vite/client and
        # /src/main.jsx as /preview-{port}/@vite/client etc., matching the nginx
        # path-proxy rule. Without this every asset request goes to the root
        # domain and gets a 404 from FastAPI.
        return f"PORT={port} {vite_bin} --port {port} --host 0.0.0.0 --base=/preview-{port}/"
    elif is_cra:
        # CRA uses react-scripts start (not 'dev'). PUBLIC_URL sets the asset
        # base path through webpack so all /_next/-style URLs become
        # /preview-{port}/static/... and match the nginx path-proxy rule.
        # BROWSER=none prevents react-scripts from trying to open a browser.
        # CI=false stops react-scripts from treating warnings as errors.
        logger.info("local_preview: CRA detected — invoking %s directly", cra_bin)
        return f"PORT={port} PUBLIC_URL=/preview-{port} HOST=0.0.0.0 BROWSER=none CI=false {cra_bin} start"
    else:
        # Unknown framework — prefer 'dev', fall back to 'start'
        script_name = "dev" if "dev" in scripts else "start"
        logger.info("local_preview: unknown framework — running '%s run %s' with PORT/HOST env", pm, script_name)
        return f"PORT={port} HOST=0.0.0.0 {pm} run {script_name}"


def _detect_pm(workspace_path: str) -> Optional[str]:
    checks = [
        ("pnpm-lock.yaml",    "pnpm"),
        ("yarn.lock",         "yarn"),
        ("bun.lockb",         "bun"),
        ("package-lock.json", "npm"),
    ]
    for lock_file, pm_name in checks:
        if os.path.isfile(os.path.join(workspace_path, lock_file)):
            return pm_name
    return None


def _has_dev_script(workspace_path: str) -> bool:
    pkg = os.path.join(workspace_path, "package.json")
    if not os.path.isfile(pkg):
        return False
    try:
        with open(pkg) as f:
            data = json.load(f)
        scripts = data.get("scripts", {})
        return "dev" in scripts or "start" in scripts
    except Exception:
        return False


def _process_alive(proc) -> bool:
    if proc is None:
        return False
    return proc.returncode is None


async def _ensure_node_modules(workspace_path: str, websocket) -> None:
    """Run the appropriate package-manager install if node_modules is absent.

    This is the most common cause of missing Tailwind CSS in previews:
    repos are cloned fresh (node_modules is gitignored) so dependencies
    must be installed before the dev server can process Tailwind/PostCSS.

    Also handles the case where node_modules exists but tailwindcss is
    missing (e.g. partial install from a previous run).
    """
    nm = os.path.join(workspace_path, "node_modules")
    tailwind_bin = os.path.join(nm, ".bin", "tailwindcss")
    next_bin = os.path.join(nm, ".bin", "next")
    vite_bin = os.path.join(nm, ".bin", "vite")

    # Skip if node_modules has the framework binary (install already done)
    if os.path.isfile(next_bin) or os.path.isfile(vite_bin):
        # Also verify tailwindcss is present — it can be missing after
        # a partial install or if the lock file was updated.
        if os.path.isfile(tailwind_bin):
            logger.debug("local_preview: node_modules OK — skipping install")
            return
        logger.info("local_preview: tailwindcss missing from node_modules — reinstalling")
    else:
        logger.info("local_preview: node_modules missing or incomplete — installing deps")

    pm = _detect_pm(workspace_path) or "pnpm"
    # Always try pnpm first (fastest), fall back to npm then yarn.
    # --prefer-offline + shared store lets repeat installs hit the cache and
    # finish in seconds instead of minutes. The store dir matches bg_preview
    # in ws.py so they share packages across all preview workspaces.
    install_cmd = (
        "pnpm install --prefer-offline --store-dir /tmp/pnpm_store "
        "|| npm install --prefer-offline "
        "|| yarn install"
    )

    logger.info("local_preview: running [%s] in %s", install_cmd, workspace_path)
    await _emit(websocket, "preview_status", status="installing",
                message="📦 Installing dependencies (pnpm → npm → yarn)…")
    try:
        _node_paths = "/usr/local/bin:/usr/bin:/bin"
        env = {
            **os.environ,
            "PATH": f"{os.environ.get('PATH', _node_paths)}:{_node_paths}",
            "CI": "1",  # suppress interactive prompts
            "PNPM_HOME": "/tmp/pnpm_global",
            "npm_config_cache": "/tmp/npm_cache",
        }
        proc = await asyncio.create_subprocess_shell(
            install_cmd,
            cwd=workspace_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )
        # 10-min cap (was 300s). Cold installs of Next.js + Tailwind + shadcn
        # routinely take 4-6 min on a fresh container; 5-min was too tight and
        # consistently produced the "next: not found" downstream failure.
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=600)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            logger.error("local_preview: dependency install timed out after 600s")
            # Wipe the partially-installed node_modules so the next attempt
            # starts from a clean slate. Without this, a half-installed dir
            # poisons every subsequent cache check (looks "present" but is
            # missing the framework binary).
            _wipe_partial_node_modules(workspace_path)
            await _emit(websocket, "preview_status", status="install_timeout",
                        message="⚠️ Dependency install timed out — preview may be unstyled")
            return

        if proc.returncode != 0:
            snippet = (stdout or b"").decode(errors="replace")[-400:]
            logger.error("local_preview: install failed (rc=%d): %s", proc.returncode, snippet)
            _wipe_partial_node_modules(workspace_path)
            await _emit(websocket, "preview_status", status="install_failed",
                        message=f"⚠️ Dependency install failed — preview may be unstyled\n{snippet[:200]}")
        else:
            logger.info("local_preview: dependencies installed successfully")
            await _emit(websocket, "preview_status", status="install_done",
                        message="✅ Dependencies installed")
    except Exception as exc:
        logger.warning("local_preview: install error (non-fatal): %s", exc)
        _wipe_partial_node_modules(workspace_path)


def _wipe_partial_node_modules(workspace_path: str) -> None:
    """Remove a partial node_modules so the next install starts clean.

    Only fires when the framework binary (next/vite) is MISSING — never
    delete a healthy install. Logs but does not raise; cleanup is best-effort.
    """
    nm = os.path.join(workspace_path, "node_modules")
    if not os.path.isdir(nm):
        return
    next_bin = os.path.join(nm, ".bin", "next")
    vite_bin = os.path.join(nm, ".bin", "vite")
    if os.path.isfile(next_bin) or os.path.isfile(vite_bin):
        # The binary is present — install is intact, don't touch it.
        return
    try:
        import shutil as _shutil
        _shutil.rmtree(nm, ignore_errors=True)
        logger.info("local_preview: wiped partial node_modules at %s", nm)
    except Exception as exc:
        logger.debug("local_preview: node_modules wipe failed (ok): %s", exc)


async def _patch_nextjs_base_path(workspace_path: str, port: int) -> None:
    """Inject env-driven assetPrefix into next.config.mjs before starting dev server.

    Without this, Next.js generates HTML with absolute asset URLs like:
        <script src="/_next/static/chunks/main.js">

    When served through the nginx path-proxy at /preview-{port}/*, the browser
    requests /_next/... from the root domain — which hits FastAPI (404).

    PRODUCTION-SAFE FORM: we write
        assetPrefix: process.env.NEXT_PUBLIC_ASSET_PREFIX || undefined,
    instead of a literal "/preview-4000" string. The local preview spawn env
    sets NEXT_PUBLIC_ASSET_PREFIX so dev gets the prefix, but when this same
    file is pushed to GitHub and built on Vercel (env var unset), assetPrefix
    becomes undefined and assets are served from the root — which is what
    production needs. A literal would be SHIPPED to production and 404 every
    CSS/JS file there ("preview works, vercel has no styles" symptom).
    """
    import re

    # Env-driven form — production-safe. Vercel doesn't set this env var, so
    # `assetPrefix` evaluates to undefined and assets serve from root.
    asset_prefix_expr = "process.env.NEXT_PUBLIC_ASSET_PREFIX || undefined"
    asset_prefix_line = f"assetPrefix: {asset_prefix_expr},"

    for fname in ("next.config.mjs", "next.config.js", "next.config.ts"):
        config_path = os.path.join(workspace_path, fname)
        if not os.path.isfile(config_path):
            continue

        try:
            with open(config_path) as f:
                original_content = f.read()
            content = original_content

            # Already env-driven? Nothing to do.
            if asset_prefix_expr in content:
                logger.debug("local_preview: %s already env-driven — skipping patch", fname)
                return

            # Legacy template that reads NEXT_PUBLIC_ASSET_PREFIX with `unoptimized` —
            # also nothing to do (older check kept for safety).
            if "NEXT_PUBLIC_ASSET_PREFIX" in content and "unoptimized" in content:
                logger.debug("local_preview: %s reads NEXT_PUBLIC_ASSET_PREFIX from env — skipping patch", fname)
                return

            # Found a literal assetPrefix (e.g. "/preview-4000" from old patcher
            # OR an unrelated user-set value). Replace with the env-driven form so
            # production builds don't ship the literal preview path.
            if "assetPrefix" in content:
                content = re.sub(
                    r"assetPrefix:\s*[^,\n}]+,?",
                    f"{asset_prefix_line}",
                    content,
                    count=1,
                )
                # Strip any leftover basePath / trailingSlash injected by older code.
                if "basePath" in content:
                    content = re.sub(r"\s*basePath:\s*['\"][^'\"]*['\"],?\n?", "", content)
                if "trailingSlash" in content:
                    content = re.sub(r"\s*trailingSlash:\s*\w+,?\n?", "", content)
                if content != original_content:
                    with open(config_path, "w") as f:
                        f.write(content)
                    logger.info("local_preview: rewrote %s assetPrefix → env-driven", fname)
                else:
                    logger.debug("local_preview: %s already correct — skipping write", fname)
                return

            # First time — inject assetPrefix right after the opening `{` of the
            # config object. Tries multiple patterns to handle the wide variety of
            # Next.js config formats found in user-imported repos.
            injected = False
            for pat in (
                # ESM: const <varname> = {  (any variable name, no TS type)
                r"(const\s+\w+\s*=\s*\{)",
                # TypeScript: const <varname>: <Type> = {
                r"(const\s+\w+\s*:\s*[^=\{]+\s*=\s*\{)",
                # ESM inline export: export default {
                r"(export\s+default\s+\{)",
                # CJS: module.exports = {
                r"(module\.exports\s*=\s*\{)",
                # Wrapped ESM: export default withPlugin({
                r"(export\s+default\s+\w+\s*\(\s*\{)",
                # Wrapped CJS: module.exports = withPlugin({
                r"(module\.exports\s*=\s*\w+\s*\(\s*\{)",
            ):
                new_content = re.sub(
                    pat,
                    rf'\1\n  {asset_prefix_line}',
                    content,
                    count=1,
                )
                if new_content != content:
                    content = new_content
                    injected = True
                    break

            if not injected:
                # No recognised pattern — back up original (only on first write)
                # and write a minimal standalone config so /_next/ assets load via
                # the proxy prefix.
                bak_path = config_path + ".bak"
                if not os.path.isfile(bak_path):
                    try:
                        with open(bak_path, "w") as _bak:
                            _bak.write(original_content)
                    except Exception:
                        pass
                is_cjs = "module.exports" in original_content
                if is_cjs:
                    override = (
                        f'/** Lucid AI preview patch — original at {fname}.bak */\n'
                        f'module.exports = {{\n'
                        f'  {asset_prefix_line}\n'
                        f'  images: {{ unoptimized: true }},\n'
                        f'}};\n'
                    )
                else:
                    override = (
                        f'/** Lucid AI preview patch — original at {fname}.bak */\n'
                        f'const nextConfig = {{\n'
                        f'  {asset_prefix_line}\n'
                        f'  images: {{ unoptimized: true }},\n'
                        f'}};\n'
                        f'export default nextConfig;\n'
                    )
                if override != original_content:
                    with open(config_path, "w") as f:
                        f.write(override)
                    logger.warning(
                        "local_preview: unknown config format in %s — wrote minimal override, original at %s.bak",
                        fname, fname,
                    )
                return

            # Inject images: { unoptimized: true } so Next.js doesn't reject
            # external image URLs common in user-imported repos.
            if "images:" in content:
                content = re.sub(
                    r"(images\s*:\s*\{)",
                    r"\1\n    unoptimized: true,",
                    content,
                    count=1,
                )
            else:
                content = re.sub(
                    r"(assetPrefix\s*:\s*\"[^\"]*\",)",
                    r'\1\n  images: { unoptimized: true },',
                    content,
                    count=1,
                )

            if content != original_content:
                with open(config_path, "w") as f:
                    f.write(content)
                logger.info("local_preview: patched %s with assetPrefix=%s", fname, base_path)
            else:
                logger.debug("local_preview: %s unchanged — skipping write", fname)
        except Exception as exc:
            logger.warning("local_preview: failed to patch %s: %s", fname, exc)
        return  # only patch the first config file found

    # No next.config.* file found — create a minimal one so /_next/ assets
    # are prefixed by the proxy path and load correctly in production.
    config_path = os.path.join(workspace_path, "next.config.mjs")
    try:
        minimal = (
            f'/** Lucid AI preview patch */\n'
            f'const nextConfig = {{\n'
            f'  assetPrefix: "{base_path}",\n'
            f'  reactStrictMode: true,\n'
            f'  images: {{ unoptimized: true }},\n'
            f'}};\n'
            f'export default nextConfig;\n'
        )
        with open(config_path, "w") as f:
            f.write(minimal)
        logger.info(
            "local_preview: no next.config found — created next.config.mjs with assetPrefix=%s",
            base_path,
        )
    except Exception as exc:
        logger.warning("local_preview: failed to create next.config.mjs: %s", exc)


class _ProcessExitedError(RuntimeError):
    """Raised when the dev server process exits before the health check passes."""


def _read_stderr(path: str, *, tail: int = 4000) -> str:
    """Read the tail of the dev server's stderr log.

    Default 4 KB tail — large enough to keep the actual stack trace / module
    resolution error after Next.js's startup banner + telemetry notice (which
    together span ~600 chars). Bumping from 500 → 4000 fixed cases where the
    UI showed only "✓ Starting..." instead of the real failure reason.
    """
    try:
        with open(path, "r", errors="replace") as f:
            content = f.read()
        return content[-tail:].strip() if content else ""
    except Exception:
        return ""


def _summarize_dev_server_error(stderr: str) -> str:
    """Extract the meaningful error from a Next.js dev-server stderr dump.

    The raw tail contains startup chatter (`▲ Next.js x.y.z`, `Local: …`,
    `✓ Starting…`) followed by the actual failure. We scan for known error
    markers and return the first hit + a few lines of context. Falls back
    to the last ~600 chars of the raw stream if no marker matches.
    """
    if not stderr:
        return "check terminal logs"

    markers = (
        "Error:",
        "SyntaxError",
        "TypeError",
        "ReferenceError",
        "Module not found",
        "Cannot find module",
        "ENOENT",
        "EADDRINUSE",
        "FATAL",
        "JavaScript heap out of memory",
        "Failed to compile",
        "Unhandled Runtime Error",
    )
    lines = stderr.splitlines()
    for i, line in enumerate(lines):
        for m in markers:
            if m in line:
                # Return the marker line + up to 8 following lines for context.
                snippet = "\n".join(lines[i : i + 9]).strip()
                if snippet:
                    return snippet
    # No structured marker — return the last few lines verbatim.
    tail_lines = [l for l in lines[-12:] if l.strip()]
    return "\n".join(tail_lines).strip() or "check terminal logs"


async def _wait_for_server(port: int, proc=None, timeout: int = 90) -> None:
    """Poll until the dev server responds with a non-error HTTP status.

    Accepts 1xx–4xx — a 404 from an empty Next.js app is still a running
    server. Rejects 5xx because a server that's bound but throwing 500s
    on every request is broken; reporting `preview_ready` for it would
    show the user a Next.js error overlay instead of their app.

    If ``proc`` is provided, checks for early process exit on every loop
    iteration and raises _ProcessExitedError immediately instead of
    burning the full timeout on a dead port.
    """
    deadline = asyncio.get_event_loop().time() + timeout
    await asyncio.sleep(4)  # Grace period for process startup
    last_5xx: Optional[int] = None

    while asyncio.get_event_loop().time() < deadline:
        # ── Process liveness check ────────────────────────
        if proc is not None and proc.returncode is not None:
            raise _ProcessExitedError(
                f"Dev server process exited with code {proc.returncode}"
            )

        curl = await asyncio.create_subprocess_shell(
            f"curl -s -o /dev/null -w '%{{http_code}}' "
            f"http://127.0.0.1:{port}/ 2>/dev/null || echo 000",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await curl.communicate()
        code = (stdout or b"").decode().strip()
        if code.isdigit():
            n = int(code)
            if 100 <= n < 500:
                logger.info("local_preview: server on port %d responded with HTTP %s", port, code)
                return
            if 500 <= n <= 599:
                # Keep polling — the dev server may still be compiling. Surface
                # the persistent 5xx via TimeoutError if it never recovers.
                last_5xx = n
        await asyncio.sleep(3)

    if last_5xx is not None:
        raise asyncio.TimeoutError(
            f"Dev server on port {port} kept returning HTTP {last_5xx} — likely a compile error"
        )
    raise asyncio.TimeoutError(f"Dev server on port {port} never responded")


async def _wait_port_released(port: int, timeout: int = 5) -> None:
    """Wait until *port* is no longer bound by any process.

    Called after killing a dev server before allocating the next port, so
    the new bind doesn't race the kernel's TIME_WAIT cleanup.
    """
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind(("0.0.0.0", port))
                return
        except OSError:
            await asyncio.sleep(0.2)
    logger.debug("local_preview: port %d still bound after %ds — proceeding anyway", port, timeout)


async def _kill_server(entry: dict) -> None:
    # Cancel the exit + health watchers first so neither fires a spurious
    # "preview crashed" event when we intentionally kill the process.
    # Skip the current task — calling `cancel()` then `await` on yourself
    # either raises RuntimeError or hangs. The triggering task is expected
    # to return on its own immediately after handing work off.
    try:
        current = asyncio.current_task()
    except RuntimeError:
        current = None
    for key in ("watcher", "health_watcher"):
        task = entry.get(key)
        if task is None or task.done() or task is current:
            continue
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    proc = entry.get("process")
    if not proc:
        return
    try:
        # Kill the entire process group (shell + next/vite children).
        # start_new_session=True puts the shell in its own group, so
        # SIGTERM here reaches next dev / vite as well.
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            proc.terminate()  # fallback: just the shell
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                proc.kill()
            await proc.wait()
    except Exception as exc:
        logger.debug("local_preview: kill error (ok): %s", exc)


async def _watch_process_exit(
    conversation_id: str,
    proc,
    stderr_path: str,
    websocket,
) -> None:
    """Fires exactly once when the dev server process exits.

    Event-driven — blocks on ``proc.wait()`` with zero CPU until the process
    dies. On unexpected exit, attempts one auto-restart before surfacing
    the failure. The restart picks a fresh port, so port-in-use crashes
    self-heal silently.

    A stop via ``stop_local_preview`` cancels this task before sending SIGTERM,
    so intentional kills don't emit a spurious crash event.
    """
    try:
        await proc.wait()
    except asyncio.CancelledError:
        return
    except Exception as exc:
        logger.debug("local_preview: watcher error for %s: %s", conversation_id, exc)
        return

    # Confirm the entry is still registered under this proc — if not, the
    # kill was intentional (entry popped by _kill_server's caller) and we
    # have nothing to report.
    entry = _active_servers.get(conversation_id)
    if not entry or entry.get("process") is not proc:
        return

    stderr_snippet = _read_stderr(stderr_path)
    summary = _summarize_dev_server_error(stderr_snippet)
    logger.warning(
        "local_preview: dev server for %s exited unexpectedly (code=%s): %s",
        conversation_id, proc.returncode, summary[:500],
    )
    workspace_path = entry.get("workspace_path", "")
    started_at = entry.get("started_at", 0)
    _active_servers.pop(conversation_id, None)

    # ── Restart-loop guard ────────────────────────────────────
    # If the previous run died in under 30s, restarting will almost
    # certainly fail the same way (compile error, missing dep). Surface
    # the failure instead so the user can act on the stderr.
    import time as _time
    uptime = _time.time() - started_at if started_at else 0
    short_lived = uptime > 0 and uptime < 30

    # ── Auto-restart once before surfacing the error ──────────
    # Most crashes we see in production are port collisions (another
    # session grabbed the port) or transient Next.js boot failures that
    # clear on a fresh attempt. Trying once before the user sees an error
    # makes the preview self-heal in the common case.
    if not short_lived and workspace_path and os.path.isdir(workspace_path):
        await _emit(websocket, "preview_status", status="restarting",
                    message="Dev server stopped — restarting…")
        try:
            restarted_url = await start_local_preview(
                workspace_path=workspace_path,
                conversation_id=conversation_id,
                websocket=websocket,
                package_manager=_detect_pm(workspace_path) or "npm",
            )
            if restarted_url:
                logger.info("local_preview: auto-restart succeeded for %s → %s",
                            conversation_id, restarted_url)
                return
        except Exception as exc:
            logger.error("local_preview: auto-restart failed for %s: %s",
                         conversation_id, exc, exc_info=True)
            summary = (
                f"{summary}\n\nAuto-restart also failed: {exc}"
                if summary else f"Auto-restart failed: {exc}"
            )

    await _emit(
        websocket, "preview_error",
        error_stage="crashed",
        message=f"Dev server crashed:\n{summary[:1500] or 'check terminal logs'}",
    )


# ── HTTP health watcher ──────────────────────────────────────────────────
# `_watch_process_exit` only fires when the dev-server PROCESS dies. A
# process that is still running but hung — bound port, no responses, or a
# 5xx storm from a corrupted route — keeps that watcher silent and the user
# sees a blank iframe forever. This loop probes the server over HTTP every
# `_HEALTH_INTERVAL` seconds and reacts to two distinct failure modes:
#
#   * Connection-level failure (refused / timeout) for `_HEALTH_FAIL_THRESHOLD`
#     consecutive checks → server is wedged. Force-restart it (the auto-
#     restart inside `start_local_preview` picks a fresh port).
#   * 5xx response for `_HEALTH_FAIL_THRESHOLD` consecutive checks → server is
#     up but the app is broken (compile error, runtime crash on root route).
#     Surface a `preview_error` so the UI shows the existing crash overlay
#     with its "Restart Preview" button. We do NOT auto-restart — restarting
#     won't fix the underlying app code, and looping would burn CPU.

_HEALTH_INTERVAL = float(os.environ.get("PREVIEW_HEALTH_INTERVAL_SECS", "60"))
_HEALTH_FAIL_THRESHOLD = int(os.environ.get("PREVIEW_HEALTH_FAIL_THRESHOLD", "3"))
_HEALTH_REQUEST_TIMEOUT = 5.0
# Don't restart from the health watcher unless the server has been alive at
# least this long — same protection `_watch_process_exit` uses against tight
# crash-restart loops on a server that's broken at startup.
_HEALTH_MIN_UPTIME_FOR_RESTART = 90.0


async def _watch_http_health(
    conversation_id: str,
    port: int,
    websocket,
) -> None:
    """Per-preview HTTP probe. Cancelled by `_kill_server` (when not self).

    Two failure modes, two reactions:
      * Connection-level (refused/timeout) for `_HEALTH_FAIL_THRESHOLD`
        consecutive checks → server is wedged, schedule a force-restart and
        exit. Restart guarded by `_HEALTH_MIN_UPTIME_FOR_RESTART` so we don't
        loop on a server that's broken at boot.
      * 5xx for the same threshold → app code is broken; surface once via
        `preview_error` and keep probing. We don't auto-restart because
        restarting won't fix the user's code.
    """
    import httpx  # local import — keeps cold-start time low

    probe_url = f"http://127.0.0.1:{port}/"
    conn_failures = 0
    http5xx_failures = 0
    surfaced_5xx = False  # latched: stop spamming preview_error on each tick

    # Wait one full interval before first probe — gives Next.js time to
    # finish its first compile after `preview_ready`.
    try:
        await asyncio.sleep(_HEALTH_INTERVAL)
    except asyncio.CancelledError:
        return

    while True:
        # Bail if the entry was rotated out (kill, restart, new port).
        entry = _active_servers.get(conversation_id)
        if not entry or entry.get("port") != port:
            return

        try:
            async with httpx.AsyncClient(timeout=_HEALTH_REQUEST_TIMEOUT) as client:
                resp = await client.get(probe_url)
            status = resp.status_code
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, OSError):
            status = None  # connection-level failure
        except Exception as exc:
            logger.debug("local_preview: health probe %s raised %s", conversation_id, exc)
            status = None

        if status is None:
            conn_failures += 1
            http5xx_failures = 0  # reset the other counter
            logger.debug(
                "local_preview: health probe failed (%d/%d) for %s on port %d",
                conn_failures, _HEALTH_FAIL_THRESHOLD, conversation_id, port,
            )
            if conn_failures >= _HEALTH_FAIL_THRESHOLD:
                # Restart-loop guard — give the server time to prove itself
                # before assuming "wedged". A server crash-looping every 30s
                # would otherwise pile up restarts indefinitely.
                import time as _time
                started_at = entry.get("started_at", 0)
                uptime = _time.time() - started_at if started_at else 0
                if uptime < _HEALTH_MIN_UPTIME_FOR_RESTART:
                    logger.warning(
                        "local_preview: %s wedged but uptime %.0fs < %.0fs guard — "
                        "surfacing as crash, no restart",
                        conversation_id, uptime, _HEALTH_MIN_UPTIME_FOR_RESTART,
                    )
                    if not surfaced_5xx:
                        surfaced_5xx = True
                        await _emit(
                            websocket, "preview_error",
                            error_stage="crashed",
                            message=(
                                "Dev server stopped responding shortly after startup. "
                                "Click Restart Preview to retry."
                            ),
                        )
                    # Fall through to the sleep + next probe rather than restart.
                    conn_failures = 0
                else:
                    logger.warning(
                        "local_preview: health watcher scheduling force-restart of "
                        "wedged server %s on port %d (uptime %.0fs)",
                        conversation_id, port, uptime,
                    )
                    workspace_path = entry.get("workspace_path", "")
                    if workspace_path and os.path.isdir(workspace_path):
                        await _emit(
                            websocket, "preview_status", status="restarting",
                            message="Dev server is unresponsive — restarting…",
                        )
                        # Schedule the restart as a separate task so this
                        # watcher can return cleanly first. `_kill_server`
                        # would otherwise try to cancel + await *us*, the
                        # current task, which deadlocks.
                        asyncio.create_task(
                            _restart_from_health_watcher(
                                workspace_path, conversation_id, websocket,
                            )
                        )
                    return
        elif 500 <= status < 600:
            conn_failures = 0
            http5xx_failures += 1
            if http5xx_failures >= _HEALTH_FAIL_THRESHOLD and not surfaced_5xx:
                surfaced_5xx = True
                # Read the dev-server stderr tail so the user (and our logs)
                # can see WHICH route/import is broken instead of a generic
                # "runtime error" message.
                stderr_tail = ""
                stderr_path = entry.get("stderr_path") if entry else None
                if stderr_path:
                    try:
                        raw_tail = _read_stderr(stderr_path) or ""
                        stderr_tail = _summarize_dev_server_error(raw_tail) or raw_tail[-1500:]
                    except Exception as _exc:
                        logger.debug("local_preview: stderr read failed: %s", _exc)
                logger.warning(
                    "local_preview: server %s returning %d on root for %d consecutive checks. stderr tail: %s",
                    conversation_id, status, http5xx_failures, (stderr_tail or "(empty)")[:500],
                )
                _user_msg = (
                    f"Dev server is up but the app is returning HTTP {status}. "
                    "Click Restart Preview to retry; if the error persists, the "
                    "generated code has a runtime error on the root route."
                )
                if stderr_tail:
                    _user_msg = f"{_user_msg}\n\n{stderr_tail[-1500:]}"
                await _emit(
                    websocket, "preview_error",
                    error_stage="crashed",
                    message=_user_msg,
                )
        else:
            # Any 2xx/3xx/4xx response means the server is responsive enough
            # to count as healthy. Clear both counters and re-arm the 5xx
            # surface so a future bout can be reported.
            conn_failures = 0
            http5xx_failures = 0
            surfaced_5xx = False

        try:
            await asyncio.sleep(_HEALTH_INTERVAL)
        except asyncio.CancelledError:
            return


async def _restart_from_health_watcher(
    workspace_path: str,
    conversation_id: str,
    websocket,
) -> None:
    """Run a force-restart in a fresh task so the calling health watcher can
    return before `_kill_server` tries to cancel-and-await it.
    """
    try:
        await start_local_preview(
            workspace_path=workspace_path,
            conversation_id=conversation_id,
            websocket=websocket,
            package_manager=_detect_pm(workspace_path) or "npm",
            force_restart=True,
        )
    except Exception as exc:
        logger.error(
            "local_preview: health-watcher restart failed for %s: %s",
            conversation_id, exc,
        )


async def _stop_by_conversation(conversation_id: str) -> None:
    entry = _active_servers.pop(conversation_id, None)
    if entry:
        await _kill_server(entry)


async def _emit(websocket: WebSocket, msg_type: str, **kwargs) -> None:
    try:
        await websocket.send_json({"type": msg_type, **kwargs})
    except Exception:
        pass


async def _save_preview_url(conversation_id: str, url: str) -> None:
    """Persist the preview URL to chat_sessions table.

    Ephemeral URLs (localhost and path-proxy preview ports) are NOT saved —
    they point at ports that die on container restart and cause 502s if restored.
    Only stable deployment URLs (e.g. Vercel) are worth persisting.
    """
    if "localhost" in url or "127.0.0.1" in url:
        return
    import re as _re
    if _re.search(r'/preview-\d+', url):
        return
    try:
        from app.supabase_client import db_client
        async with db_client(None) as sb:
            await (
                sb.table("chat_sessions")
                .update({"vercel_url": url})
                .eq("project_id", conversation_id)
                .execute()
            )
    except Exception as exc:
        logger.warning("local_preview: failed to save preview URL: %s", exc)
