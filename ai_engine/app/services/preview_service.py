import asyncio
import json
import logging
import os
import socket
from fastapi import WebSocket

logger = logging.getLogger(__name__)

# Store active preview sessions keyed by task_id
active_previews = {}


# ═══════════════════════════════════════════════════════════
#  FUNCTION 3 — get_available_port()
# ═══════════════════════════════════════════════════════════
def get_available_port(start: int, end: int) -> int:
    """Find and return a free port in given range."""
    for port in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(('127.0.0.1', port)) != 0:
                return port
    raise RuntimeError(f"No free ports found in range {start}-{end}")


# ═══════════════════════════════════════════════════════════
#  FUNCTION 1 — start_novnc_preview()
# ═══════════════════════════════════════════════════════════
async def start_novnc_preview(
    repo_path: str,
    task_id: str,
    websocket: WebSocket
) -> str:
    """
    Start Xvfb + Chromium + x11vnc + noVNC + user app.
    Returns noVNC URL for iframe embedding.
    Waits up to 3 minutes for user approve/reject.
    """

    # ── 1. Find available ports ───────────────────────────
    app_port = get_available_port(3100, 3999)
    vnc_port = get_available_port(5900, 5999)
    novnc_port = get_available_port(6080, 6180)

    # Use task_id last 2 digits for display number
    display_num = ''.join(filter(str.isdigit, task_id))[-2:]
    if not display_num:
        display_num = str(abs(hash(task_id)) % 90 + 10)

    logger.info(
        "[preview] task=%s display=:%s app=%d vnc=%d novnc=%d",
        task_id, display_num, app_port, vnc_port, novnc_port
    )

    # ── 2. Start virtual display (Xvfb) ──────────────────
    xvfb_proc = await asyncio.create_subprocess_exec(
        "Xvfb", f":{display_num}", "-screen", "0", "1280x720x24",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await asyncio.sleep(1)  # let X server initialize

    env = os.environ.copy()
    env["DISPLAY"] = f":{display_num}"

    # ── 3. Start Chromium on virtual display ──────────────
    chromium_proc = await asyncio.create_subprocess_exec(
        "chromium-browser",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--window-size=1280,720",
        f"http://localhost:{app_port}",
        env=env,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )

    # ── 4. Start x11vnc VNC server ────────────────────────
    x11vnc_proc = await asyncio.create_subprocess_exec(
        "x11vnc",
        "-display", f":{display_num}",
        "-port", str(vnc_port),
        "-nopw",
        "-forever",
        "-shared",
        env=env,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )

    # ── 5. Start noVNC WebSocket proxy ────────────────────
    novnc_proc = await asyncio.create_subprocess_exec(
        "websockify",
        "--web", "/usr/share/novnc",
        str(novnc_port),
        f"localhost:{vnc_port}",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )

    # ── 6. Start user's app ──────────────────────────────
    package_json_path = os.path.join(repo_path, "package.json")
    start_cmd = ["npm", "run", "dev", "--", "--port", str(app_port)]

    if os.path.exists(package_json_path):
        try:
            with open(package_json_path, "r") as f:
                pkg = json.load(f)
            scripts = pkg.get("scripts", {})
            deps = pkg.get("dependencies", {})
            dev_deps = pkg.get("devDependencies", {})

            if "next" in deps or "next" in dev_deps:
                # Next.js
                start_cmd = ["npx", "next", "dev", "--port", str(app_port)]
            elif "vite" in deps or "vite" in dev_deps:
                # Vite / React+Vite
                start_cmd = ["npx", "vite", "--port", str(app_port)]
            # else: fallback to npm run dev
        except Exception as e:
            logger.warning("[preview] Failed to parse package.json: %s", e)

    app_proc = await asyncio.create_subprocess_exec(
        *start_cmd,
        cwd=repo_path,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )

    # ── Store all process handles ─────────────────────────
    active_previews[task_id] = {
        "xvfb_pid": xvfb_proc,
        "chromium_pid": chromium_proc,
        "x11vnc_pid": x11vnc_proc,
        "novnc_pid": novnc_proc,
        "app_pid": app_proc,
        "ports": {
            "app": app_port,
            "vnc": vnc_port,
            "novnc": novnc_port,
        },
    }

    # Wait 8 seconds for everything to start
    await asyncio.sleep(8)

    # ── 7. Send noVNC URL to frontend via websocket ───────
    preview_url = (
        f"http://localhost:{novnc_port}/vnc.html"
        f"?autoconnect=true&resize=scale&quality=6"
    )

    await websocket.send_json({
        "type": "preview_ready",
        "task_id": task_id,
        "preview_url": preview_url,
        "message": "Preview ready! Review changes in the browser.",
    })

    # ── 9. Keep alive: wait for approve / reject / timeout
    #       3-minute timeout ───────────────────────────────
    try:
        result = await asyncio.wait_for(
            _wait_for_user_decision(websocket, task_id),
            timeout=180,  # 3 minutes
        )
    except asyncio.TimeoutError:
        logger.warning("[preview] Task %s timed out after 3 minutes", task_id)
        await websocket.send_json({
            "type": "preview_timeout",
            "task_id": task_id,
            "message": "Preview timed out after 3 minutes.",
        })
        result = False
    finally:
        await stop_novnc_preview(task_id)

    # ── 8. Return the preview_url ─────────────────────────
    return preview_url


async def _wait_for_user_decision(websocket: WebSocket, task_id: str) -> bool:
    """
    Listen for approve / reject messages from the frontend.
    Returns True if approved, False if rejected.
    """
    while True:
        data = await websocket.receive_json()
        msg_type = data.get("type", "")

        if msg_type == "preview_approved" and data.get("task_id") == task_id:
            logger.info("[preview] Task %s APPROVED by user", task_id)
            return True

        if msg_type == "preview_rejected" and data.get("task_id") == task_id:
            feedback = data.get("feedback", "")
            logger.info("[preview] Task %s REJECTED: %s", task_id, feedback)
            return False

        # Ignore unrelated messages (pings, etc.)


# ═══════════════════════════════════════════════════════════
#  FUNCTION 2 — stop_novnc_preview()
# ═══════════════════════════════════════════════════════════
async def stop_novnc_preview(task_id: str):
    """Kill all processes for this task and free all ports."""
    preview = active_previews.get(task_id)
    if not preview:
        return

    logger.info("[preview] Cleaning up task %s …", task_id)

    # Kill in reverse startup order
    for key in ["app_pid", "novnc_pid", "x11vnc_pid", "chromium_pid", "xvfb_pid"]:
        proc = preview.get(key)
        if proc is None:
            continue
        try:
            proc.terminate()
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            proc.kill()
        except Exception as e:
            logger.error("[preview] Error killing %s for %s: %s", key, task_id, e)

    del active_previews[task_id]
    logger.info("[preview] Task %s cleaned up ✓", task_id)
