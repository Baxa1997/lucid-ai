"""DevServer — start a Vite/Next.js dev server + localtunnel for live preview.

Phase 4 of workspace initialization (called from ws.py after npm install):

    INSTALLING → STARTING → HEALTH_CHECK → READY

Entry point for Phase 4:
    from app.services.dev_server import launch_dev_preview

    preview_url = await launch_dev_preview(
        workspace_path=pre_workspace,
        session=session,
        websocket=websocket,
        chat_session_id=chat_session_id,
    )

Legacy entry point (called from task pipeline after code generation):
    from app.services.dev_server import start_dev_preview

    preview_url = await start_dev_preview(
        workspace_path=workspace_path,
        websocket=websocket,
        chat_session_id=chat_session_id,
    )
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import signal
import subprocess
from typing import TYPE_CHECKING, Optional

from fastapi import WebSocket

if TYPE_CHECKING:
    from app.services.sessions import AgentSession

logger = logging.getLogger("lucid.dev_server")


# ── Port allocator ────────────────────────────────────────────
# Each workspace gets a unique port in the range 3001-3200.
# Ports are released when the workspace dev server is stopped.

_PORT_RANGE_START = 3001
_PORT_RANGE_END   = 3200
_used_ports: set[int] = set()
_port_lock = asyncio.Lock()


async def _alloc_port() -> int:
    """Claim the lowest free port in [3001, 3200)."""
    async with _port_lock:
        for p in range(_PORT_RANGE_START, _PORT_RANGE_END):
            if p not in _used_ports:
                _used_ports.add(p)
                logger.debug("dev_server: allocated port %d", p)
                return p
    raise RuntimeError(
        f"No free preview ports in {_PORT_RANGE_START}–{_PORT_RANGE_END - 1}. "
        "Too many concurrent workspaces."
    )


async def _free_port(port: int) -> None:
    async with _port_lock:
        _used_ports.discard(port)
        logger.debug("dev_server: freed port %d", port)


# ── Active server registry ─────────────────────────────────────
# workspace_path → {"dev_process", "tunnel_process", "tunnel_url", "port"}
_active_servers: dict[str, dict] = {}


# ── Public Phase-4 entry point ────────────────────────────────

async def launch_dev_preview(
    *,
    workspace_path: str,
    session: "AgentSession",
    websocket: WebSocket,
    chat_session_id: str = "",
    package_manager: str = "npm",
    server_timeout: int = 30,
    tunnel_timeout: int = 30,
) -> Optional[str]:
    """Phase 4: STARTING → HEALTH_CHECK, emit preview_ready, return tunnel URL.

    Drives the workspace state machine:
      STARTING     — dev server process launched, waiting for port
      HEALTH_CHECK — port open, starting localtunnel, polling URL

    If any step fails the function returns None (non-fatal) and the workspace
    transitions to READY anyway so the user can still send tasks.

    Returns the public tunnel URL on success, None on failure.
    """
    from app.workspace_states import WorkspaceState, transition as ws_transition

    if not _has_dev_script(workspace_path):
        logger.info(
            "dev_server: no 'dev' script in package.json at %s — skipping preview",
            workspace_path,
        )
        return None

    # ── Allocate port ──────────────────────────────────────────
    try:
        port = await _alloc_port()
    except RuntimeError as exc:
        logger.error("dev_server: port allocation failed: %s", exc)
        return None

    # ── STARTING — launch the dev server process ──────────────
    await ws_transition(
        session, websocket, WorkspaceState.STARTING,
        f"Starting development server on port {port}...",
    )
    await _emit(websocket, "progress", message=f"🖥️ Starting dev server (port {port})…")

    dev_process = _spawn_dev_server(workspace_path, package_manager, port)
    if dev_process is None:
        await _free_port(port)
        await _emit(websocket, "warning",
                    message="⚠️ Could not launch dev server — preview unavailable")
        return None

    # ── Wait for port to open (server_timeout seconds) ────────
    port_open = await _wait_for_port(dev_process, port, timeout=server_timeout)
    if not port_open:
        logger.warning(
            "dev_server: port %d did not open within %ds — aborting",
            port, server_timeout,
        )
        _kill_process(dev_process)
        await _free_port(port)
        await _emit(websocket, "preview_error",
                    error_stage="start",
                    message=f"Dev server didn't start within {server_timeout}s — click Restart Preview to try again.")
        return None

    logger.info("dev_server: port %d is open", port)

    # ── HEALTH_CHECK — start tunnel and verify URL ─────────────
    await ws_transition(
        session, websocket, WorkspaceState.HEALTH_CHECK,
        "Connecting tunnel for live preview...",
    )
    await _emit(websocket, "progress", message="🔗 Creating tunnel to dev server…")

    tunnel_url, tunnel_process = await _start_tunnel(
        port, workspace_path, timeout=tunnel_timeout
    )

    if not tunnel_url:
        logger.warning("dev_server: tunnel failed — preview unavailable")
        _kill_process(dev_process)
        await _free_port(port)
        await _emit(websocket, "preview_error",
                    error_stage="health_check",
                    message="Could not create preview tunnel — click Restart Preview to get a fresh port.")
        return None

    # ── Register ───────────────────────────────────────────────
    _active_servers[workspace_path] = {
        "dev_process":    dev_process,
        "tunnel_process": tunnel_process,
        "tunnel_url":     tunnel_url,
        "port":           port,
    }
    logger.info("dev_server: live preview at %s (port %d)", tunnel_url, port)

    # ── Emit preview events ────────────────────────────────────
    await _emit(websocket, "preview_ready",
                preview_url=tunnel_url,
                message=f"🖥️ Live preview at {tunnel_url}")
    await _emit(websocket, "deploy_ready", url=tunnel_url)

    # ── Persist URL to DB ──────────────────────────────────────
    if chat_session_id:
        await _save_preview_url(chat_session_id, tunnel_url)

    return tunnel_url


# ── Legacy entry point (used by task pipeline after code generation) ──

async def start_dev_preview(
    workspace_path: str,
    websocket: WebSocket,
    chat_session_id: str = "",
    package_manager: str = "npm",
    port: int = 0,           # 0 = auto-allocate
    timeout: int = 60,
) -> Optional[str]:
    """Legacy entry point — does not drive state machine transitions.

    Used by the task pipeline after new-project code generation.
    Allocates a port automatically unless one is explicitly given.
    """
    if not _has_dev_script(workspace_path):
        return None

    if port == 0:
        try:
            port = await _alloc_port()
        except RuntimeError as exc:
            logger.error("dev_server: port allocation failed: %s", exc)
            return None

    await stop_dev_preview(workspace_path)

    await _emit(websocket, "progress",
                message=f"🖥️ Starting development server on port {port}…")

    dev_process = _spawn_dev_server(workspace_path, package_manager, port)
    if dev_process is None:
        await _free_port(port)
        return None

    server_ready = await _wait_for_port(dev_process, port, timeout=timeout)
    if not server_ready:
        logger.warning("dev_server: server did not start within %ds", timeout)
        _kill_process(dev_process)
        await _free_port(port)
        await _emit(websocket, "progress",
                    message="⚠️ Dev server failed to start — preview unavailable")
        return None

    tunnel_url, tunnel_process = await _start_tunnel(port, workspace_path)
    if not tunnel_url:
        _kill_process(dev_process)
        await _free_port(port)
        await _emit(websocket, "progress",
                    message="⚠️ Could not create tunnel — preview unavailable")
        return None

    _active_servers[workspace_path] = {
        "dev_process":    dev_process,
        "tunnel_process": tunnel_process,
        "tunnel_url":     tunnel_url,
        "port":           port,
    }

    logger.info("dev_server: live preview at %s", tunnel_url)
    await _emit(websocket, "preview_ready",
                preview_url=tunnel_url,
                message=f"🖥️ Live preview ready at {tunnel_url}")
    await _emit(websocket, "deploy_ready", url=tunnel_url)

    if chat_session_id:
        await _save_preview_url(chat_session_id, tunnel_url)

    return tunnel_url


# ── Stop / cleanup ─────────────────────────────────────────────

async def stop_dev_preview(workspace_path: str) -> None:
    """Stop the dev server and release its port."""
    info = _active_servers.pop(workspace_path, None)
    if info:
        _kill_process(info.get("dev_process"))
        _kill_process(info.get("tunnel_process"))
        if info.get("port"):
            await _free_port(info["port"])
        logger.info("dev_server: stopped preview for %s", workspace_path)


async def stop_all_previews() -> None:
    """Stop all active dev servers — called on server shutdown."""
    for path in list(_active_servers.keys()):
        await stop_dev_preview(path)


# ── Internal helpers ───────────────────────────────────────────

def _has_dev_script(workspace_path: str) -> bool:
    """Return True if package.json exists and has a 'dev' script."""
    if not workspace_path or not os.path.isdir(workspace_path):
        return False
    pkg = os.path.join(workspace_path, "package.json")
    if not os.path.isfile(pkg):
        return False
    try:
        import json
        with open(pkg) as f:
            data = json.load(f)
        return "dev" in data.get("scripts", {})
    except Exception:
        return False


def _spawn_dev_server(
    workspace_path: str,
    package_manager: str,
    port: int,
) -> Optional[subprocess.Popen]:
    """Launch the dev server process and return the Popen handle."""
    cmd = {
        "pnpm": ["pnpm", "run", "dev", "--", "--port", str(port), "--host", "0.0.0.0"],
        "yarn": ["yarn", "dev", "--port", str(port)],
    }.get(package_manager, ["npm", "run", "dev", "--", "--port", str(port), "--host", "0.0.0.0"])

    dev_env = {
        **os.environ,
        "PORT": str(port),
        "NODE_ENV": "development",
        "BROWSER": "none",
        "npm_config_loglevel": "error",
    }
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=workspace_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=dev_env,
            preexec_fn=os.setsid,
        )
        logger.info("dev_server: spawned PID %d cmd=%s", proc.pid, cmd)
        return proc
    except Exception as exc:
        logger.error("dev_server: failed to spawn dev server: %s", exc)
        return None


def _kill_process(proc: Optional[subprocess.Popen]) -> None:
    """Kill a process and its entire process group."""
    if proc is None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, OSError):
        pass
    try:
        proc.kill()
    except Exception:
        pass


async def _wait_for_port(
    process: subprocess.Popen,
    port: int,
    timeout: int = 30,
) -> bool:
    """Wait up to *timeout* seconds for the dev server to accept connections.

    Checks two signals in parallel:
    1. stdout lines matching known "ready" patterns (Vite / Next.js / Webpack)
    2. TCP port open on 127.0.0.1:{port}
    """
    import select
    import socket

    _READY_RE = re.compile(
        r"ready started server"           # Next.js
        r"|Local:\s+http"                 # Vite
        r"|listening on"                  # Generic
        r"|ready in \d+"                  # Vite
        r"|started server on"             # Next.js App Router
        r"|compiled.*successfully"        # Webpack
        r"|Server running"                # Custom
        r"|http://localhost",             # Generic
        re.IGNORECASE,
    )

    deadline = asyncio.get_event_loop().time() + timeout

    while asyncio.get_event_loop().time() < deadline:
        # Process died early
        if process.poll() is not None:
            logger.warning(
                "dev_server: process exited with code %d before port opened",
                process.returncode,
            )
            return False

        # Check stdout for ready patterns (non-blocking)
        try:
            if process.stdout and select.select([process.stdout], [], [], 0.2)[0]:
                line = process.stdout.readline()
                if line:
                    text = line.decode("utf-8", errors="replace").strip()
                    if text:
                        logger.debug("dev_server stdout: %s", text[:200])
                        if _READY_RE.search(text):
                            logger.info(
                                "dev_server: ready pattern matched: %s", text[:100]
                            )
                            await asyncio.sleep(0.5)  # brief grace period
                            return True
        except Exception:
            pass

        # Fallback: TCP port check
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.3)
                if s.connect_ex(("127.0.0.1", port)) == 0:
                    logger.info("dev_server: port %d is open (TCP check)", port)
                    await asyncio.sleep(0.5)
                    return True
        except Exception:
            pass

        await asyncio.sleep(0.5)

    return False


async def _start_tunnel(
    port: int,
    workspace_path: str,
    timeout: int = 30,
    retry: bool = False,
) -> tuple[Optional[str], Optional[subprocess.Popen]]:
    """Start localtunnel and return (url, process) or (None, None) on failure.

    Uses `lt` (localtunnel CLI). If not installed, returns (None, None).
    """
    import hashlib

    hash_str = hashlib.md5(workspace_path.encode()).hexdigest()[:8]
    subdomain = f"lucid-prev-{hash_str}{'-r' if retry else ''}"

    try:
        lt_proc = subprocess.Popen(
            ["lt", "--port", str(port), "--subdomain", subdomain],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            preexec_fn=os.setsid,
        )
    except FileNotFoundError:
        logger.error(
            "dev_server: 'lt' not found — install localtunnel: npm install -g localtunnel"
        )
        return None, None
    except Exception as exc:
        logger.error("dev_server: failed to start localtunnel: %s", exc)
        return None, None

    import select

    deadline = asyncio.get_event_loop().time() + timeout
    url: Optional[str] = None

    while asyncio.get_event_loop().time() < deadline:
        if lt_proc.poll() is not None:
            out = b""
            if lt_proc.stdout:
                out = lt_proc.stdout.read()
            logger.warning(
                "dev_server: localtunnel exited early: %s",
                out.decode("utf-8", errors="replace")[:300],
            )
            break

        if lt_proc.stdout:
            try:
                if select.select([lt_proc.stdout], [], [], 0.3)[0]:
                    line = lt_proc.stdout.readline().decode("utf-8", errors="replace").strip()
                    if line:
                        logger.debug("dev_server lt: %s", line)
                        m = re.search(r"(https?://\S+\.loca\.lt\S*)", line)
                        if m:
                            url = m.group(1).rstrip("/")
                            break
                        # Generic HTTPS URL fallback
                        m2 = re.search(r"(https?://\S+)", line)
                        if m2 and "loca" in line.lower():
                            url = m2.group(1).rstrip("/")
                            break
            except Exception:
                pass

        await asyncio.sleep(0.3)

    if not url:
        _kill_process(lt_proc)
        # Retry once with a different subdomain
        if not retry:
            return await _start_tunnel(port, workspace_path, timeout=timeout, retry=True)
        return None, None

    logger.info("dev_server: tunnel URL: %s", url)
    return url, lt_proc


async def _http_health_check(url: str, timeout: int = 20) -> bool:
    """Poll *url* until it returns HTTP 2xx/3xx, or *timeout* seconds elapse.

    Uses stdlib urllib so there are no extra dependencies.
    localtunnel requires the bypass header to skip its interstitial page.
    """
    import urllib.request
    import urllib.error

    headers = {"bypass-tunnel-reminder": "true", "User-Agent": "Lucid-HealthCheck/1.0"}
    deadline = asyncio.get_event_loop().time() + timeout

    while asyncio.get_event_loop().time() < deadline:
        try:
            req = urllib.request.Request(url, headers=headers)
            resp = await asyncio.to_thread(
                urllib.request.urlopen, req, timeout=4
            )
            if resp.status < 400:
                logger.info("dev_server: health check OK (%d) for %s", resp.status, url)
                return True
        except urllib.error.HTTPError as exc:
            if exc.code < 400:
                return True
        except Exception:
            pass
        await asyncio.sleep(2)

    logger.warning("dev_server: health check timed out for %s", url)
    return False


async def _emit(websocket: WebSocket, msg_type: str, **kwargs) -> None:
    """Send a WebSocket event, swallowing send errors."""
    try:
        await websocket.send_json({"type": msg_type, **kwargs})
    except Exception:
        pass


async def _save_preview_url(chat_session_id: str, url: str) -> None:
    """Persist the preview URL to the chat_sessions DB record."""
    try:
        from app.supabase_client import db_client
        async with db_client(None) as sb:
            await (
                sb.table("chat_sessions")
                .update({"vercel_url": url})
                .eq("id", chat_session_id)
                .execute()
            )
        logger.info("dev_server: saved preview URL to session %s", chat_session_id)
    except Exception as exc:
        logger.warning("dev_server: failed to save preview URL: %s", exc)
