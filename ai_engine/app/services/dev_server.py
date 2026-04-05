"""DevServer — start a local dev server + localtunnel for live preview.

After code generation is complete (Phase 6), the pipeline calls
`start_dev_preview()` to:
1. Run `npm run dev` (or pnpm/yarn) in the workspace
2. Wait for the dev server to be listening on port 3000
3. Start a localtunnel to expose the dev server publicly
4. Emit a `preview_ready` WebSocket event with the public URL
5. Save the preview URL to the chat_sessions DB table

The dev server process is tracked per conversation and cleaned up
when the workspace is destroyed.

Usage:
    from app.services.dev_server import start_dev_preview, stop_dev_preview

    preview_url = await start_dev_preview(
        workspace_path="/tmp/lucid_new_xxx",
        websocket=websocket,
        chat_session_id="uuid",
        package_manager="npm",
    )
"""

import asyncio
import logging
import os
import re
import signal
import subprocess
from typing import Optional

from fastapi import WebSocket

logger = logging.getLogger("lucid.dev_server")

# Track active dev server processes per workspace path
_active_servers: dict[str, dict] = {}


async def start_dev_preview(
    workspace_path: str,
    websocket: WebSocket,
    chat_session_id: str = "",
    package_manager: str = "npm",
    port: int = 3000,
    timeout: int = 60,
) -> Optional[str]:
    """Start dev server + localtunnel and return the public preview URL.

    Returns None if the dev server or tunnel fails to start.
    """
    if not workspace_path or not os.path.isdir(workspace_path):
        logger.warning("dev_server: workspace_path invalid: %s", workspace_path)
        return None

    # Check if package.json has a "dev" script
    pkg_json_path = os.path.join(workspace_path, "package.json")
    if not os.path.isfile(pkg_json_path):
        logger.info("dev_server: no package.json — skipping live preview")
        return None

    try:
        import json
        with open(pkg_json_path, "r") as f:
            pkg_data = json.loads(f.read())
        scripts = pkg_data.get("scripts", {})
        if "dev" not in scripts:
            logger.info("dev_server: no 'dev' script in package.json — skipping")
            return None
    except Exception as e:
        logger.warning("dev_server: failed to read package.json: %s", e)
        return None

    # Stop any existing dev server for this workspace
    await stop_dev_preview(workspace_path)

    try:
        await websocket.send_json({
            "type": "progress",
            "message": "🖥️ Starting development server for live preview...",
        })
    except Exception:
        pass

    # ── Step 1: Start the dev server ─────────────────────────
    dev_cmd = _build_dev_cmd(package_manager, port)
    dev_env = {
        **os.environ,
        "PORT": str(port),
        "NODE_ENV": "development",
        "BROWSER": "none",  # Don't open browser
        "npm_config_loglevel": "error",
    }

    try:
        dev_process = subprocess.Popen(
            dev_cmd,
            cwd=workspace_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=dev_env,
            preexec_fn=os.setsid,  # Create new process group for cleanup
        )
        logger.info("dev_server: started PID %d with cmd %s", dev_process.pid, dev_cmd)
    except Exception as e:
        logger.error("dev_server: failed to start dev server: %s", e)
        return None

    # ── Step 2: Wait for the dev server to be ready ──────────
    server_ready = await _wait_for_server(dev_process, port, timeout=timeout)
    if not server_ready:
        logger.warning("dev_server: server did not start within %ds", timeout)
        _kill_process(dev_process)
        try:
            await websocket.send_json({
                "type": "progress",
                "message": "⚠️ Dev server failed to start — preview unavailable",
            })
        except Exception:
            pass
        return None

    logger.info("dev_server: dev server ready on port %d", port)

    # ── Step 3: Start localtunnel ────────────────────────────
    tunnel_url = await _start_tunnel(port, workspace_path)
    if not tunnel_url:
        logger.warning("dev_server: localtunnel failed — trying fallback")
        # Fallback: try with a different subdomain
        tunnel_url = await _start_tunnel(port, workspace_path, retry=True)

    if not tunnel_url:
        logger.error("dev_server: all tunnel attempts failed")
        _kill_process(dev_process)
        try:
            await websocket.send_json({
                "type": "progress",
                "message": "⚠️ Could not create tunnel — preview unavailable",
            })
        except Exception:
            pass
        return None

    # ── Step 4: Register and emit ────────────────────────────
    _active_servers[workspace_path] = {
        "dev_process": dev_process,
        "tunnel_url": tunnel_url,
        "port": port,
    }

    logger.info("dev_server: live preview ready at %s", tunnel_url)

    # Emit preview_ready event to frontend
    try:
        await websocket.send_json({
            "type": "preview_ready",
            "preview_url": tunnel_url,
            "message": f"🖥️ Live preview ready at {tunnel_url}",
        })
    except Exception:
        pass

    # Also emit deploy_ready so the frontend iframe picks it up
    try:
        await websocket.send_json({
            "type": "deploy_ready",
            "url": tunnel_url,
        })
    except Exception:
        pass

    # ── Step 5: Save to DB ───────────────────────────────────
    if chat_session_id:
        try:
            from app.supabase_client import db_client
            async with db_client(None) as sb:
                await (
                    sb.table("chat_sessions")
                    .update({"vercel_url": tunnel_url})
                    .eq("id", chat_session_id)
                    .execute()
                )
            logger.info("dev_server: saved preview URL to session %s", chat_session_id)
        except Exception as db_err:
            logger.warning("dev_server: failed to save preview URL: %s", db_err)

    return tunnel_url


async def stop_dev_preview(workspace_path: str) -> None:
    """Stop the dev server for a given workspace."""
    info = _active_servers.pop(workspace_path, None)
    if info:
        _kill_process(info.get("dev_process"))
        logger.info("dev_server: stopped preview for %s", workspace_path)


async def stop_all_previews() -> None:
    """Stop all active dev server previews."""
    for path in list(_active_servers.keys()):
        await stop_dev_preview(path)


# ── Internal helpers ─────────────────────────────────────────


def _build_dev_cmd(package_manager: str, port: int) -> list:
    """Build the dev server command."""
    if package_manager == "pnpm":
        return ["pnpm", "run", "dev", "--", "--port", str(port)]
    elif package_manager == "yarn":
        return ["yarn", "dev", "--port", str(port)]
    else:
        return ["npm", "run", "dev", "--", "--port", str(port)]


def _kill_process(proc) -> None:
    """Kill a subprocess and its entire process group."""
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


async def _wait_for_server(process, port: int, timeout: int = 60) -> bool:
    """Wait for the dev server to start listening on the given port.

    Reads stdout looking for common "ready" patterns from Next.js, Vite, etc.
    Falls back to a port check if no pattern is found.
    """
    import socket

    ready_patterns = [
        r"ready started server",       # Next.js
        r"Local:\s+http",              # Vite
        r"listening on",               # Generic
        r"ready in \d+",              # Vite
        r"started server on",          # Next.js App Router
        r"compiled.*successfully",     # Webpack
        r"Server running",            # Custom
        r"http://localhost",           # Generic
    ]
    combined_pattern = re.compile("|".join(ready_patterns), re.IGNORECASE)

    start_time = asyncio.get_event_loop().time()

    while (asyncio.get_event_loop().time() - start_time) < timeout:
        # Check if process died
        if process.poll() is not None:
            logger.warning("dev_server: process exited with code %d", process.returncode)
            return False

        # Check stdout for ready patterns
        try:
            # Non-blocking read
            import select
            if process.stdout and select.select([process.stdout], [], [], 0.5)[0]:
                line = process.stdout.readline()
                if line:
                    decoded = line.decode("utf-8", errors="replace").strip()
                    if decoded:
                        logger.debug("dev_server stdout: %s", decoded[:200])
                        if combined_pattern.search(decoded):
                            logger.info("dev_server: detected ready pattern in: %s", decoded[:100])
                            return True
        except Exception:
            pass

        # Fallback: check if port is open
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.5)
                result = s.connect_ex(("127.0.0.1", port))
                if result == 0:
                    logger.info("dev_server: port %d is open", port)
                    await asyncio.sleep(1)  # Give it a moment to fully initialize
                    return True
        except Exception:
            pass

        await asyncio.sleep(1)

    return False


async def _start_tunnel(port: int, workspace_path: str, retry: bool = False) -> Optional[str]:
    """Start localtunnel and return the public URL.

    Uses npx localtunnel for reliability.
    """
    import hashlib

    # Generate a deterministic subdomain from workspace path
    hash_str = hashlib.md5(workspace_path.encode()).hexdigest()[:8]
    subdomain = f"lucid-preview-{hash_str}"
    if retry:
        subdomain = f"lucid-prev-{hash_str}-r"

    try:
        # Start localtunnel as a background process
        lt_cmd = ["lt", "--port", str(port), "--subdomain", subdomain]
        lt_process = subprocess.Popen(
            lt_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            preexec_fn=os.setsid,
        )

        # Read the tunnel URL from stdout (localtunnel prints it immediately)
        url = None
        for _ in range(30):  # Wait up to 15 seconds
            if lt_process.poll() is not None:
                # Process exited
                output = lt_process.stdout.read().decode("utf-8", errors="replace") if lt_process.stdout else ""
                logger.warning("dev_server: localtunnel exited: %s", output[:300])
                break

            if lt_process.stdout:
                import select
                if select.select([lt_process.stdout], [], [], 0.5)[0]:
                    line = lt_process.stdout.readline().decode("utf-8", errors="replace").strip()
                    logger.debug("dev_server lt stdout: %s", line)

                    # localtunnel outputs: "your url is: https://xxx.loca.lt"
                    url_match = re.search(r"(https?://\S+\.loca\.lt\S*)", line)
                    if url_match:
                        url = url_match.group(1).rstrip("/")
                        break

                    # Also check for other tunnel URL formats
                    url_match2 = re.search(r"(https?://\S+)", line)
                    if url_match2 and "loca" in line.lower():
                        url = url_match2.group(1).rstrip("/")
                        break

            await asyncio.sleep(0.5)

        if url:
            # Store the tunnel process for cleanup
            _active_servers.setdefault(workspace_path, {})["tunnel_process"] = lt_process
            logger.info("dev_server: tunnel URL: %s", url)
            return url
        else:
            _kill_process(lt_process)
            logger.warning("dev_server: failed to get tunnel URL")
            return None

    except FileNotFoundError:
        logger.error("dev_server: 'lt' command not found — install localtunnel globally")
        return None
    except Exception as e:
        logger.error("dev_server: tunnel error: %s", e)
        return None
