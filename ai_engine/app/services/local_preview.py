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
) -> Optional[str]:
    """Start a dev server for the workspace, return its public URL.

    If a server is already running for this conversation, returns
    the existing URL immediately (no restart needed — HMR handles
    incremental file changes automatically).

    Returns None if the workspace has no 'dev' script or startup fails.
    """
    if not _has_dev_script(workspace_path):
        logger.info("local_preview: no 'dev' script in package.json — skipping preview")
        return None

    # ── Re-use existing server if still alive AND same workspace ─
    existing = _active_servers.get(conversation_id)
    if existing and _process_alive(existing.get("process")):
        if existing.get("workspace_path") == workspace_path:
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

            _active_servers[conversation_id] = {
                "port": port,
                "process": proc,
                "workspace_path": workspace_path,
                "stderr_path": _stderr_path,
                "url": "",
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
            logger.error(
                "local_preview: dev server exited early (port %d): %s",
                port, stderr_snippet[:300],
            )
            await _stop_by_conversation(conversation_id)
            await _emit(websocket, "preview_error",
                        error_stage="crashed",
                        message=f"Dev server crashed on startup: {stderr_snippet[:200] or 'check terminal logs'}")
            return None

        except asyncio.TimeoutError:
            stderr_snippet = _read_stderr(_stderr_path)
            logger.error(
                "local_preview: timed out waiting for dev server on port %d. stderr: %s",
                port, stderr_snippet[:300],
            )
            await _stop_by_conversation(conversation_id)
            await _emit(websocket, "preview_error",
                        error_stage="timeout",
                        message="Dev server didn't start in time — click Restart Preview to retry.")
            return None

        except Exception as exc:
            stderr_snippet = _read_stderr(_stderr_path)
            logger.error(
                "local_preview: start failed: %s | stderr: %s",
                exc, stderr_snippet[:300], exc_info=True,
            )
            await _stop_by_conversation(conversation_id)
            await _emit(websocket, "preview_error",
                        error_stage="start",
                        message=f"Preview failed to start: {str(exc)[:200]}")
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
    # automatically.  This function exists only for API compatibility with
    # the old E2B-based dev_server module.
    logger.debug("local_preview: sync_files_to_preview is a no-op (HMR handles it)")


async def extend_sandbox_timeout(
    conversation_id: str = "", workspace_path: str = "", extra_seconds: int = 1800
) -> None:
    """No-op — local processes don't time out like E2B sandboxes."""
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
    domain = os.environ.get("PREVIEW_DOMAIN", "").strip()
    if domain:
        return f"https://preview-{port}.{domain}"
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
    try:
        with open(pkg_path) as f:
            pkg = json.load(f)
        deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
        is_next = "next" in deps
        is_vite = "vite" in deps
    except Exception:
        pass

    pm = _detect_pm(workspace_path) or package_manager

    # Resolve binary paths relative to the workspace (works even if not in global PATH)
    next_bin = "./node_modules/.bin/next"
    vite_bin = "./node_modules/.bin/vite"

    if is_next:
        # Direct Next.js invocation: no script wrapper, flags work correctly.
        # -H 0.0.0.0 makes it bind to all interfaces so Docker port-forwarding works.
        logger.info("local_preview: Next.js detected — invoking %s directly", next_bin)
        return f"PORT={port} {next_bin} dev -p {port} -H 0.0.0.0"
    elif is_vite:
        logger.info("local_preview: Vite detected — invoking %s directly", vite_bin)
        return f"PORT={port} {vite_bin} --port {port} --host 0.0.0.0"
    else:
        # Unknown framework — fall back to `pnpm run dev` with env vars only
        logger.info("local_preview: unknown framework — running 'pnpm run dev' with PORT/HOSTNAME env")
        return f"PORT={port} {pm} run dev"


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
        return "dev" in data.get("scripts", {})
    except Exception:
        return False


def _process_alive(proc) -> bool:
    if proc is None:
        return False
    return proc.returncode is None


class _ProcessExitedError(RuntimeError):
    """Raised when the dev server process exits before the health check passes."""


def _read_stderr(path: str) -> str:
    """Read the last 500 chars of the dev server's stderr log."""
    try:
        with open(path, "r", errors="replace") as f:
            content = f.read()
        return content[-500:].strip() if content else ""
    except Exception:
        return ""


async def _wait_for_server(port: int, proc=None, timeout: int = 90) -> None:
    """Poll until the dev server responds to ANY HTTP request on the port.

    Accepts any valid HTTP status code (1xx–5xx) — we just want to know
    the process is up and the port is bound.  A 404 from an empty Next.js
    app is still a running server.

    If ``proc`` is provided, checks for early process exit on every loop
    iteration and raises _ProcessExitedError immediately instead of
    burning the full timeout on a dead port.
    """
    deadline = asyncio.get_event_loop().time() + timeout
    await asyncio.sleep(4)  # Grace period for process startup

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
        # Any real HTTP status (100–599) means the server is up.
        if code.isdigit() and 100 <= int(code) <= 599:
            logger.info("local_preview: server on port %d responded with HTTP %s", port, code)
            return
        await asyncio.sleep(3)

    raise asyncio.TimeoutError(f"Dev server on port {port} never responded")


async def _kill_server(entry: dict) -> None:
    # Cancel the exit watcher first so it doesn't fire a spurious
    # "preview crashed" event when we intentionally kill the process.
    watcher = entry.get("watcher")
    if watcher is not None and not watcher.done():
        watcher.cancel()
        try:
            await watcher
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
    dies, then notifies the client if the exit was unexpected.

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
    logger.warning(
        "local_preview: dev server for %s exited unexpectedly (code=%s): %s",
        conversation_id, proc.returncode, stderr_snippet[:300],
    )
    _active_servers.pop(conversation_id, None)

    await _emit(
        websocket, "preview_error",
        error_stage="crashed",
        message=f"Dev server crashed: {stderr_snippet[:200] or 'check terminal logs'}",
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

    localhost URLs are intentionally NOT saved — the frontend already filters
    them out as ephemeral on page load (they don't survive a server restart).
    Only production domain URLs (PREVIEW_DOMAIN set) are worth persisting.
    """
    if "localhost" in url or "127.0.0.1" in url:
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
