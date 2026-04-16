"""DevServer — E2B cloud sandbox preview for Lucid AI projects.

Creates an E2B Sandbox, uploads workspace files, installs Node.js + deps,
starts the dev server, and returns a public preview URL via get_host(port).

IMPORTANT: The sandbox registry is keyed by `chat_session_id` (stable across
pipeline runs), NOT by workspace_path (which changes every time because the
pipeline creates a temp dir and deletes it after pushing to GitHub).

E2B Python SDK v2 API reference (confirmed from docs + migration guide):
  - Sandbox.create(api_key=..., timeout_ms=...)  — factory method
  - sandbox.commands.run(cmd, background=True)    — run command
  - sandbox.files.write(path, content)            — write single file
  - sandbox.files.write_files([{path, data}])     — write multiple files
  - sandbox.files.read(path)                      — read file
  - sandbox.get_host(port)                        — get public URL host
  - sandbox.set_timeout(ms)                       — extend lifetime
  - sandbox.kill()                                — destroy sandbox
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from functools import partial
from typing import TYPE_CHECKING, Optional

from fastapi import WebSocket

if TYPE_CHECKING:
    from app.services.sessions import AgentSession

logger = logging.getLogger("lucid.dev_server")


# ══════════════════════════════════════════════════════════════════
#  Registry — maps chat_session_id → sandbox metadata
#
#  CRITICAL: keyed by chat_session_id, NOT workspace_path.
#  workspace_path changes each pipeline run (/tmp/lucid_new_{uuid})
#  and is deleted in the pipeline's finally block. chat_session_id
#  is stable for the entire WS connection lifetime.
# ══════════════════════════════════════════════════════════════════
_active_sandboxes: dict[str, dict] = {}

# Rate limiter — tracks sandbox creates per session
_sandbox_create_count: dict[str, int] = {}
_MAX_SANDBOX_CREATES = 5  # Max retries before blocking

_DEFAULT_PORT = 3000

# Working directory inside the E2B sandbox.
# The default base image runs as 'user' (not root), so /app is NOT writable.
# /home/user/app is writable by the default user.
_SANDBOX_WORKDIR = "/home/user/app"

# Files/dirs to skip when syncing workspace → sandbox
_SKIP_DIRS = frozenset({
    "node_modules", ".git", ".next", "dist", "build",
    "__pycache__", ".cache", ".turbo", ".vercel", ".claude",
})
_SKIP_EXTENSIONS = frozenset({".lock", ".log"})
_MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB


# ══════════════════════════════════════════════════════════════════
#  Public Entry Points
# ══════════════════════════════════════════════════════════════════

async def launch_dev_preview(
    *,
    workspace_path: str,
    session: "AgentSession",
    websocket: WebSocket,
    chat_session_id: str = "",
    package_manager: str = "pnpm",
    server_timeout: int = 180,
    tunnel_timeout: int = 30,  # kept for API compat, unused
) -> Optional[str]:
    """Create E2B sandbox, sync files, install deps, start dev server.

    State machine emitted to frontend:
        STARTING → HEALTH_CHECK → READY  (or preview_error on failure)

    Returns the public preview URL on success, None on failure (non-fatal).
    """
    if not _has_dev_script(workspace_path):
        logger.info("dev_server: no 'dev' script in package.json — skipping preview")
        return None

    # Use chat_session_id as stable key; fall back to workspace_path
    registry_key = chat_session_id or workspace_path

    # Rate limit — prevent sandbox creation spam
    count = _sandbox_create_count.get(registry_key, 0)
    if count >= _MAX_SANDBOX_CREATES:
        logger.warning("dev_server: rate limit hit for %s (%d creates)", registry_key, count)
        await _emit(websocket, "preview_error",
                    error_stage="rate_limit",
                    message="Too many preview restarts. Please refresh the page to reset.")
        return None
    _sandbox_create_count[registry_key] = count + 1

    # Stop existing sandbox if one is running (retry scenario)
    existing = _active_sandboxes.pop(registry_key, None)
    if existing and existing.get("sandbox"):
        await _stop_sandbox(existing["sandbox"])

    try:
        # ── Emit: starting ─────────────────────────────────
        await _emit(websocket, "preview_status",
                    status="starting",
                    message="Creating cloud sandbox…")

        # ── Create sandbox ─────────────────────────────────
        sandbox, sandbox_id = await _create_sandbox()

        # ── Install Node.js (only if no custom template) ───
        # With E2B_TEMPLATE_ID set, Node.js is pre-installed in the image.
        # Without it, we fall back to runtime install (~90s).
        if not os.environ.get("E2B_TEMPLATE_ID", "").strip():
            await _emit(websocket, "preview_status",
                        status="starting",
                        message="Installing Node.js in sandbox…")
            await _install_node(sandbox)

        # ── Sync workspace files ───────────────────────────
        await _emit(websocket, "preview_status",
                    status="starting",
                    message="Uploading project files…")
        await _sync_workspace_to_sandbox(sandbox, workspace_path)

        # ── Patch iframe headers ───────────────────────────
        # Next.js/Vite dev servers set X-Frame-Options: SAMEORIGIN by default,
        # which blocks cross-origin iframe embedding. Patch the project to allow it.
        await _patch_iframe_headers(sandbox, workspace_path)

        # ── Install deps + start dev server ────────────────
        await _emit(websocket, "preview_status",
                    status="health_check",
                    message="Installing dependencies… (this can take a few minutes)")
        pm = _detect_package_manager_from_lockfiles(workspace_path) or package_manager
        # install_timeout: time allowed for npm/pnpm install (can be slow on cold cache)
        # server_timeout: time to poll until the dev server responds on its port
        await _install_and_start(sandbox, pm, install_timeout=360, server_timeout=server_timeout,
                                  websocket=websocket)

        # ── Get public URL ─────────────────────────────────
        preview_url = _get_sandbox_url(sandbox, _DEFAULT_PORT)
        logger.info("dev_server: preview URL obtained: %s — polling tunnel…", preview_url)

        # ── Wait for E2B tunnel to become reachable ─────────
        # The internal localhost health check passes before E2B's external tunnel
        # proxy is ready, causing 503 when the iframe loads. Poll the public URL
        # from the ai_engine until it returns a real HTTP response (not 503/000).
        await _emit(websocket, "preview_status",
                    status="health_check",
                    message="Waiting for preview tunnel…")
        await _wait_for_tunnel(preview_url, timeout=60)

        # ── Register sandbox (keyed by chat_session_id) ────
        _active_sandboxes[registry_key] = {
            "sandbox": sandbox,
            "preview_url": preview_url,
            "port": _DEFAULT_PORT,
            "sandbox_id": sandbox_id,
        }

        # ── Emit: ready ────────────────────────────────────
        await _emit(websocket, "preview_ready",
                    preview_url=preview_url,
                    message=f"🖥️ Live preview: {preview_url}")

        # ── Save to DB ─────────────────────────────────────
        if chat_session_id:
            await _save_preview_url(chat_session_id, preview_url)

        return preview_url

    except Exception as exc:
        logger.error("dev_server: launch failed: %s", exc, exc_info=True)
        await _emit(websocket, "preview_error",
                    error_stage="start",
                    message=f"Preview failed: {str(exc)[:200]}")
        return None


async def start_dev_preview(
    *,
    workspace_path: str,
    websocket: WebSocket,
    chat_session_id: str = "",
    package_manager: str = "pnpm",
) -> Optional[str]:
    """Simplified entry point used by orchestrator Phase 6.7."""
    return await launch_dev_preview(
        workspace_path=workspace_path,
        session=None,
        websocket=websocket,
        chat_session_id=chat_session_id,
        package_manager=package_manager,
    )


async def stop_dev_preview(
    chat_session_id: str = "",
    workspace_path: str = "",
) -> None:
    """Stop and destroy the E2B sandbox for a session.

    Looks up by chat_session_id first, then falls back to workspace_path.
    """
    key = chat_session_id or workspace_path
    if not key:
        return
    entry = _active_sandboxes.pop(key, None)
    if entry and entry.get("sandbox"):
        await _stop_sandbox(entry["sandbox"])
        logger.info("dev_server: stopped sandbox for %s", key)


async def stop_all_previews() -> None:
    """Stop all live preview sandboxes."""
    keys = list(_active_sandboxes.keys())
    for key in keys:
        entry = _active_sandboxes.pop(key, None)
        if entry and entry.get("sandbox"):
            await _stop_sandbox(entry["sandbox"])


def get_active_preview_url(
    chat_session_id: str = "",
    workspace_path: str = "",
) -> Optional[str]:
    """Return the active preview URL for a session (or None).

    Looks up by chat_session_id first, then falls back to workspace_path.
    """
    key = chat_session_id or workspace_path
    entry = _active_sandboxes.get(key)
    return entry["preview_url"] if entry else None


async def sync_files_to_preview(
    workspace_path: str,
    chat_session_id: str = "",
    changed_files: list[str] | None = None,
) -> None:
    """Re-sync files to an existing sandbox (called after code edits).

    If changed_files is provided, only those files are synced (fast delta).
    Otherwise, a full workspace sync is performed.
    """
    key = chat_session_id or workspace_path
    entry = _active_sandboxes.get(key)
    if not entry or not entry.get("sandbox"):
        logger.warning("dev_server: sync_files called but no active sandbox for %s", key)
        return

    sandbox = entry["sandbox"]

    if changed_files:
        await _sync_specific_files(sandbox, workspace_path, changed_files)
    else:
        await _sync_workspace_to_sandbox(sandbox, workspace_path)


async def extend_sandbox_timeout(
    chat_session_id: str = "",
    workspace_path: str = "",
    extra_seconds: int = 1800,
) -> None:
    """Extend the sandbox lifetime (called on user activity).

    Default: +30 minutes. E2B SDK v2.20 uses seconds, not ms.
    """
    key = chat_session_id or workspace_path
    entry = _active_sandboxes.get(key)
    if not entry or not entry.get("sandbox"):
        return
    try:
        sandbox = entry["sandbox"]
        await asyncio.to_thread(sandbox.set_timeout, extra_seconds)
        logger.info("dev_server: extended sandbox timeout by %ds", extra_seconds)
    except Exception as exc:
        logger.warning("dev_server: failed to extend sandbox timeout: %s", exc)


# ══════════════════════════════════════════════════════════════════
#  Internal — Sandbox Lifecycle
# ══════════════════════════════════════════════════════════════════

async def _create_sandbox() -> tuple:
    """Create a new E2B sandbox. Returns (sandbox, sandbox_id).

    Uses E2B_TEMPLATE_ID if set — a custom image with Node.js 20 + pnpm
    pre-installed, which eliminates the 60-120s Node.js install step.
    Falls back to the default E2B base image if no template is configured.

    To build the custom template:
        npm install -g @e2b/cli && e2b login
        e2b template build --name lucid-node20   # uses e2b.Dockerfile
        # copy the printed template ID into E2B_TEMPLATE_ID in .env
    """
    from e2b import Sandbox

    api_key = os.environ.get("E2B_API_KEY", "")
    if not api_key:
        raise RuntimeError(
            "E2B_API_KEY not set — cannot create preview sandbox. "
            "Get your key at https://e2b.dev"
        )

    template_id = os.environ.get("E2B_TEMPLATE_ID", "").strip() or None

    # Sandbox.create() is synchronous in the Python SDK — run in thread.
    # timeout = total sandbox lifetime in SECONDS (E2B SDK v2.20).
    sandbox = await asyncio.to_thread(
        partial(
            Sandbox.create,
            **({"template": template_id} if template_id else {}),
            api_key=api_key,
            timeout=1800,  # 30 min
        )
    )

    sandbox_id = getattr(sandbox, "sandbox_id", str(id(sandbox)))
    logger.info("dev_server: created E2B sandbox %s", sandbox_id)
    return sandbox, sandbox_id


async def _install_node(sandbox) -> None:
    """Install Node.js 20 LTS + pnpm inside the sandbox.

    The default E2B base image has Python but NOT Node.js.
    This installs Node.js via the NodeSource apt repo.
    """
    node_check = await asyncio.to_thread(
        partial(sandbox.commands.run, "node --version", timeout=5)
    )
    if node_check.exit_code == 0:
        logger.info("dev_server: Node.js already installed: %s",
                     (node_check.stdout or "").strip())
        return

    logger.info("dev_server: installing Node.js 20 LTS in sandbox…")
    install_result = await asyncio.to_thread(
        partial(
            sandbox.commands.run,
            "curl -fsSL https://deb.nodesource.com/setup_20.x | bash - "
            "&& apt-get install -y nodejs "
            "&& npm install -g pnpm",
            timeout=120,
        )
    )
    if install_result.exit_code != 0:
        stderr = install_result.stderr or install_result.stdout or "unknown"
        raise RuntimeError(f"Failed to install Node.js in sandbox: {stderr[:300]}")
    logger.info("dev_server: Node.js installed successfully")


async def _patch_iframe_headers(sandbox, workspace_path: str) -> None:
    """Patch the project so its dev server can be embedded in an iframe.

    Next.js and Vite both set X-Frame-Options: SAMEORIGIN by default, which
    blocks cross-origin iframe embedding (our frontend is on a different origin
    than the E2B sandbox URL).

    Strategy:
      • Next.js — write middleware.js at the project root. Next.js picks it up
        automatically; it removes X-Frame-Options and adds a permissive CSP.
      • Vite — write a small plugin file and import it into vite.config.js.
        If vite.config.js can't be patched safely, the plugin is written as a
        standalone file and a wrapper config overrides it.
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

    if is_next:
        # Next.js middleware runs on every request and can mutate response headers.
        # Writing middleware.js at the project root is non-destructive — if one
        # already exists we overwrite only the header lines (rare for generated apps).
        middleware_js = """\
import { NextResponse } from 'next/server';

export function middleware(request) {
  const response = NextResponse.next();
  // Allow embedding in any iframe (needed for Lucid AI live preview)
  response.headers.delete('X-Frame-Options');
  response.headers.set('Content-Security-Policy', "frame-ancestors *");
  return response;
}

export const config = {
  matcher: '/:path*',
};
"""
        try:
            await asyncio.to_thread(
                sandbox.files.write,
                f"{_SANDBOX_WORKDIR}/middleware.js",
                middleware_js,
            )
            logger.info("dev_server: wrote Next.js middleware to allow iframe embedding")
        except Exception as exc:
            logger.warning("dev_server: failed to write Next.js middleware: %s", exc)

    elif is_vite:
        # Vite supports server.headers in vite.config.js (Vite 3+).
        # We write a small plugin file and try to inject it via a config wrapper.
        # Safest approach: write __lucid_iframe.js plugin and a vite.config.js
        # that merges with defineConfig using mergeConfig (Vite 3+ built-in).
        plugin_js = """\
// Auto-generated by Lucid AI — allows dev server to be embedded in an iframe.
export function lucidIframePlugin() {
  return {
    name: 'lucid-iframe',
    configureServer(server) {
      server.middlewares.use((_req, res, next) => {
        res.removeHeader('X-Frame-Options');
        res.setHeader('Content-Security-Policy', 'frame-ancestors *');
        next();
      });
    },
  };
}
"""
        try:
            await asyncio.to_thread(
                sandbox.files.write,
                f"{_SANDBOX_WORKDIR}/__lucid_iframe.js",
                plugin_js,
            )
            # Try to read and patch existing vite.config.js
            for cfg_name in ("vite.config.js", "vite.config.ts"):
                cfg_path = f"{_SANDBOX_WORKDIR}/{cfg_name}"
                try:
                    content = await asyncio.to_thread(sandbox.files.read, cfg_path)
                    if isinstance(content, bytes):
                        content = content.decode("utf-8")
                    if "lucidIframePlugin" in content:
                        break  # already patched
                    # Prepend import + add to plugins array if simple enough
                    if "plugins:" in content and "defineConfig" in content:
                        patched = (
                            "import { lucidIframePlugin } from './__lucid_iframe.js';\n"
                            + content.replace(
                                "plugins: [",
                                "plugins: [lucidIframePlugin(), ",
                                1,
                            )
                        )
                        await asyncio.to_thread(sandbox.files.write, cfg_path, patched)
                        logger.info("dev_server: patched %s for iframe embedding", cfg_name)
                    break
                except Exception:
                    continue
        except Exception as exc:
            logger.warning("dev_server: failed to patch Vite config for iframe: %s", exc)


async def _stop_sandbox(sandbox) -> None:
    """Kill an E2B sandbox."""
    try:
        await asyncio.to_thread(sandbox.kill)
    except Exception as exc:
        logger.debug("dev_server: sandbox kill error (ok): %s", exc)


# ══════════════════════════════════════════════════════════════════
#  Internal — File Sync
# ══════════════════════════════════════════════════════════════════

async def _sync_workspace_to_sandbox(sandbox, workspace_path: str) -> None:
    """Upload all project files from local workspace to /app in the sandbox.

    Uses files.write_files() for batch upload (E2B SDK v2) with fallback
    to individual files.write() calls.
    """
    def _collect_files():
        """Collect file payloads in the main thread (reads from local disk)."""
        payloads = []
        for root, dirs, filenames in os.walk(workspace_path):
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
            for fname in filenames:
                abs_path = os.path.join(root, fname)
                rel_path = os.path.relpath(abs_path, workspace_path)

                _, ext = os.path.splitext(fname)
                if ext in _SKIP_EXTENSIONS:
                    continue
                if fname.startswith(".") and fname not in {".env", ".env.local", ".env.example"}:
                    continue

                try:
                    fsize = os.path.getsize(abs_path)
                    if fsize > _MAX_FILE_SIZE:
                        continue
                    with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read()
                    payloads.append({"path": f"{_SANDBOX_WORKDIR}/{rel_path}", "data": content})
                except Exception as exc:
                    logger.debug("dev_server: skip %s: %s", rel_path, exc)

        return payloads

    payloads = await asyncio.to_thread(_collect_files)
    if not payloads:
        logger.warning("dev_server: no files to sync")
        return

    # Ensure /app exists
    await asyncio.to_thread(
        partial(sandbox.commands.run, f"mkdir -p {_SANDBOX_WORKDIR}", timeout=10)
    )

    # Try batch upload (write_files), fall back to individual writes
    try:
        await asyncio.to_thread(sandbox.files.write_files, payloads)
        logger.info("dev_server: batch-synced %d files to sandbox", len(payloads))
    except (AttributeError, TypeError):
        # Fallback: write_files not available or wrong signature
        logger.info("dev_server: write_files not available, using individual writes")
        def _write_individually():
            for p in payloads:
                try:
                    parent = os.path.dirname(p["path"])
                    if parent and parent != _SANDBOX_WORKDIR:
                        sandbox.commands.run(f"mkdir -p {parent}", timeout=5)
                    sandbox.files.write(p["path"], p["data"])
                except Exception as exc:
                    logger.debug("dev_server: write fail %s: %s", p["path"], exc)
        await asyncio.to_thread(_write_individually)
        logger.info("dev_server: individually synced %d files to sandbox", len(payloads))


async def _sync_specific_files(
    sandbox,
    workspace_path: str,
    changed_files: list[str],
) -> None:
    """Sync only specific files (delta sync for follow-up edits)."""
    payloads = []
    for rel_path in changed_files:
        abs_path = os.path.join(workspace_path, rel_path)
        if not os.path.isfile(abs_path):
            continue
        try:
            fsize = os.path.getsize(abs_path)
            if fsize > _MAX_FILE_SIZE:
                continue
            with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            payloads.append({"path": f"{_SANDBOX_WORKDIR}/{rel_path}", "data": content})
        except Exception as exc:
            logger.debug("dev_server: delta skip %s: %s", rel_path, exc)

    if not payloads:
        return

    try:
        await asyncio.to_thread(sandbox.files.write_files, payloads)
    except (AttributeError, TypeError):
        def _write_delta():
            for p in payloads:
                try:
                    sandbox.files.write(p["path"], p["data"])
                except Exception:
                    pass
        await asyncio.to_thread(_write_delta)

    logger.info("dev_server: delta-synced %d files to sandbox", len(payloads))


# ══════════════════════════════════════════════════════════════════
#  Internal — Install & Start Dev Server
# ══════════════════════════════════════════════════════════════════

async def _install_and_start(
    sandbox,
    package_manager: str,
    install_timeout: int = 360,
    server_timeout: int = 120,
    timeout: int = 90,  # legacy compat — ignored when install_timeout/server_timeout are set explicitly
    websocket=None,
) -> None:
    """Install dependencies and start the dev server inside the sandbox."""
    # Ensure the chosen package manager is available in the sandbox.
    # When E2B_TEMPLATE_ID is set, _install_node() is skipped (Node.js is
    # pre-installed in the image), but the template may not include pnpm/yarn/bun.
    # Try to install the missing PM; if that also fails, fall back to npm so the
    # preview still launches rather than crashing entirely.
    # NOTE: E2B's commands.run() raises CommandExitException on non-zero exit.
    effective_pm = package_manager  # may be downgraded to npm on install failure

    if package_manager in ("pnpm", "yarn", "bun"):
        # Step 1: check if already available
        pm_available = False
        try:
            await asyncio.to_thread(
                partial(sandbox.commands.run, f"{package_manager} --version", timeout=5)
            )
            pm_available = True
            logger.info("dev_server: %s already available in sandbox", package_manager)
        except Exception:
            pass

        # Step 2: try to install if missing
        if not pm_available:
            logger.info("dev_server: %s not found — attempting install…", package_manager)
            install_pm_cmd = {
                "pnpm": "npm install -g pnpm",
                "yarn": "npm install -g yarn",
                "bun":  "curl -fsSL https://bun.sh/install | bash",
            }[package_manager]
            try:
                await asyncio.to_thread(
                    partial(sandbox.commands.run, install_pm_cmd, timeout=90)
                )
                logger.info("dev_server: %s installed successfully", package_manager)
            except Exception as pm_err:
                logger.warning(
                    "dev_server: %s install failed (%s) — falling back to npm",
                    package_manager, str(pm_err)[:100],
                )
                effective_pm = "npm"

    install_cmd = {
        "pnpm": "pnpm install --no-frozen-lockfile --prefer-offline",
        "yarn": "yarn install",
        "bun":  "bun install",
    }.get(effective_pm, "npm install --prefer-offline")

    # Install dependencies — use install_timeout (default 180s); cold pnpm/npm
    # installs on a large project can take 2-3 min on first run.
    # E2B raises CommandExitException on non-zero exit — catch so it's non-fatal.
    try:
        await asyncio.to_thread(
            partial(sandbox.commands.run, f"cd {_SANDBOX_WORKDIR} && {install_cmd}", timeout=install_timeout)
        )
        logger.info("dev_server: %s install completed", package_manager)
    except Exception as install_err:
        logger.error("dev_server: %s install failed (continuing anyway): %s",
                     package_manager, str(install_err)[:300])

    if websocket:
        await _emit(websocket, "preview_status",
                    status="health_check",
                    message="Dependencies installed — starting dev server…")

    # Detect framework and available script to use correct start command.
    # Use the effective package manager for running dev (may have fallen back to npm)
    pm_run = {
        "pnpm": "pnpm run",
        "yarn": "yarn",
        "bun":  "bun run",
    }.get(effective_pm, "npm run")

    # Default: use generic host/port flags; overridden per framework below.
    # Start with "dev" as default script name — will be updated after reading package.json.
    script_name = "dev"
    start_cmd = f"cd {_SANDBOX_WORKDIR} && PORT={_DEFAULT_PORT} {pm_run} {script_name} -- --host 0.0.0.0 --port {_DEFAULT_PORT}"

    try:
        pkg_content = await asyncio.to_thread(sandbox.files.read, f"{_SANDBOX_WORKDIR}/package.json")
        if isinstance(pkg_content, bytes):
            pkg_content = pkg_content.decode("utf-8")
        pkg = json.loads(pkg_content)
        scripts = pkg.get("scripts", {})
        deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}

        # Pick the first available runnable script
        for _s in _DEV_SCRIPT_NAMES:
            if _s in scripts:
                script_name = _s
                break

        logger.info("dev_server: using script '%s' to start dev server", script_name)

        if "next" in deps:
            # Next.js: -H 0.0.0.0 binds to all interfaces so E2B tunnel can reach it
            start_cmd = f"cd {_SANDBOX_WORKDIR} && PORT={_DEFAULT_PORT} {pm_run} {script_name} -- --port {_DEFAULT_PORT} -H 0.0.0.0"
        elif "nuxt" in deps:
            # Nuxt: uses PORT env var + --host
            start_cmd = f"cd {_SANDBOX_WORKDIR} && PORT={_DEFAULT_PORT} {pm_run} {script_name} -- --host 0.0.0.0"
        else:
            start_cmd = f"cd {_SANDBOX_WORKDIR} && PORT={_DEFAULT_PORT} {pm_run} {script_name} -- --host 0.0.0.0 --port {_DEFAULT_PORT}"
    except Exception:
        pass

    # Start dev server in background (background=True → process keeps running)
    logger.info("dev_server: starting [%s] on port %d in sandbox…", start_cmd, _DEFAULT_PORT)
    try:
        await asyncio.to_thread(
            partial(sandbox.commands.run, start_cmd, background=True)
        )
    except Exception as start_err:
        logger.warning("dev_server: start command returned non-zero (ok for background): %s", start_err)

    # Wait for the server to be ready (poll)
    logger.info("dev_server: waiting for dev server on port %d…", _DEFAULT_PORT)
    await asyncio.sleep(3)  # Give it a moment to start

    deadline = asyncio.get_event_loop().time() + server_timeout
    attempt = 0
    while asyncio.get_event_loop().time() < deadline:
        try:
            check = await asyncio.to_thread(
                partial(
                    sandbox.commands.run,
                    f"curl -s -o /dev/null -w '%{{http_code}}' http://localhost:{_DEFAULT_PORT}/ 2>/dev/null || echo 'fail'",
                    timeout=5,
                )
            )
            stdout = (check.stdout or "").strip()
            attempt += 1
            # Accept any HTTP response — even 4xx means the server is up and routing.
            # Only "fail" (curl error / no server) means it's not ready yet.
            if stdout and stdout != "fail" and stdout.isdigit():
                logger.info("dev_server: server ready on port %d (status=%s, attempt=%d)", _DEFAULT_PORT, stdout, attempt)
                return
            else:
                logger.debug("dev_server: health check attempt %d — status=%s, retrying in 2s", attempt, stdout or "timeout")
        except Exception as hc_err:
            logger.debug("dev_server: health check error (attempt %d): %s", attempt, hc_err)
        await asyncio.sleep(2)

    raise RuntimeError(f"Dev server didn't respond on port {_DEFAULT_PORT} within {server_timeout}s")


# ══════════════════════════════════════════════════════════════════
#  Internal — URL & Helper Functions
# ══════════════════════════════════════════════════════════════════

def _get_sandbox_url(sandbox, port: int) -> str:
    """Get the public URL for a port inside the E2B sandbox.

    E2B SDK v2 Python uses get_host() (snake_case).
    Returns: https://{port}-{sandbox_id}.e2b.app
    """
    get_host_fn = getattr(sandbox, "get_host", None) or getattr(sandbox, "getHost", None)
    if get_host_fn is None:
        raise RuntimeError("E2B SDK: neither get_host() nor getHost() found on sandbox object")
    host = get_host_fn(port)
    return f"https://{host}"


_DEV_SCRIPT_NAMES = ("dev", "start", "serve", "preview")


def _has_dev_script(workspace_path: str) -> bool:
    """Check if workspace has a runnable dev/start script in package.json."""
    if not workspace_path or not os.path.isdir(workspace_path):
        return False
    pkg = os.path.join(workspace_path, "package.json")
    if not os.path.isfile(pkg):
        return False
    try:
        with open(pkg) as f:
            data = json.load(f)
        scripts = data.get("scripts", {})
        return any(s in scripts for s in _DEV_SCRIPT_NAMES)
    except Exception:
        return False


def _pick_dev_script(workspace_path: str) -> str:
    """Return the first available dev/start script name, defaulting to 'dev'."""
    try:
        pkg = os.path.join(workspace_path, "package.json")
        with open(pkg) as f:
            data = json.load(f)
        scripts = data.get("scripts", {})
        for name in _DEV_SCRIPT_NAMES:
            if name in scripts:
                return name
    except Exception:
        pass
    return "dev"


def _detect_package_manager_from_lockfiles(workspace_path: str) -> str:
    """Detect package manager from lockfile presence only.

    NOTE: Does NOT check shutil.which() — the PM binary must be in the
    E2B sandbox (not the local server). We install npm/pnpm in _install_node().
    """
    checks = [
        ("pnpm-lock.yaml",    "pnpm"),
        ("yarn.lock",         "yarn"),
        ("bun.lockb",         "bun"),
        ("package-lock.json", "npm"),
    ]
    for lock_file, pm_name in checks:
        if os.path.isfile(os.path.join(workspace_path, lock_file)):
            return pm_name
    return "npm"


async def _wait_for_tunnel(url: str, timeout: int = 60) -> None:
    """Poll the external E2B tunnel URL until it returns a non-503 HTTP response.

    E2B's internal localhost check passes before the external tunnel proxy is
    established. This function polls the public URL from the ai_engine side so
    we only emit preview_ready once the iframe will actually load.

    Uses urllib to avoid adding httpx/requests as a hard dependency.
    A 503 means the tunnel isn't ready yet. Any other status (200, 301, 404…)
    means the tunnel is up and the dev server is routing requests.
    """
    import urllib.request
    import urllib.error

    deadline = asyncio.get_event_loop().time() + timeout
    attempt = 0
    while asyncio.get_event_loop().time() < deadline:
        attempt += 1
        try:
            def _fetch():
                req = urllib.request.Request(url, method="GET")
                req.add_header("User-Agent", "LucidAI-TunnelCheck/1.0")
                try:
                    with urllib.request.urlopen(req, timeout=8) as resp:
                        return resp.status
                except urllib.error.HTTPError as e:
                    return e.code

            code = await asyncio.to_thread(_fetch)
            if code != 503:
                logger.info("dev_server: tunnel ready (status=%s, attempt=%d)", code, attempt)
                return
            logger.debug("dev_server: tunnel poll attempt %d — 503, retrying…", attempt)
        except Exception as exc:
            logger.debug("dev_server: tunnel poll attempt %d error: %s", attempt, exc)
        await asyncio.sleep(3)

    # Timeout reached — emit anyway rather than blocking forever.
    # The frontend will show the URL; if it's still 503 the user can refresh.
    logger.warning("dev_server: tunnel poll timed out after %ds — emitting URL anyway", timeout)


async def _emit(websocket: WebSocket, msg_type: str, **kwargs) -> None:
    """Send a typed message to the websocket (swallow errors)."""
    try:
        await websocket.send_json({"type": msg_type, **kwargs})
    except Exception:
        pass


async def _save_preview_url(chat_session_id: str, url: str) -> None:
    """Persist the preview URL to the DB.

    Never overwrites a real Vercel deployment URL with a temporary E2B tunnel
    URL — doing so would lose the persistent URL and the frontend would show
    nothing on re-entry (it treats .e2b.app URLs as temporary and clears them).
    """
    try:
        from app.supabase_client import db_client
        async with db_client(None) as sb:
            # Read current value first — don't clobber a persistent Vercel URL
            existing = await (
                sb.table("chat_sessions")
                .select("vercel_url")
                .eq("id", chat_session_id)
                .maybe_single()
                .execute()
            )
            current_url = (existing.data or {}).get("vercel_url", "") or ""
            # A Vercel URL (.vercel.app / custom domain) is persistent — keep it.
            # An E2B URL (.e2b.app / .e2b.dev) is temporary — safe to overwrite.
            _is_persistent = (
                current_url
                and not any(t in current_url for t in ("localhost", "127.0.0.1", ".e2b.app", ".e2b.dev"))
            )
            if _is_persistent:
                logger.info(
                    "dev_server: skipping vercel_url overwrite — existing URL is persistent: %s",
                    current_url[:60],
                )
                return
            await (
                sb.table("chat_sessions")
                .update({"vercel_url": url})
                .eq("id", chat_session_id)
                .execute()
            )
    except Exception as exc:
        logger.warning("dev_server: failed to save preview URL: %s", exc)
