import subprocess
import asyncio
import os
import json
import signal
import logging
from fastapi import WebSocket

try:
    import httpx
except ImportError:
    httpx = None

try:
    from playwright.async_api import async_playwright
except ImportError:
    async_playwright = None

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════
#  Module-level state
# ═══════════════════════════════════════════════════════════
active_previews = {}  # task_id -> { "app_process", "playwright_browser", "novnc_process", "timeout_task" }

PREVIEW_TIMEOUT = 300  # 5 minutes
APP_PORT = 3000
NOVNC_PORT = 6080
MAX_READY_ATTEMPTS = 15
READY_POLL_INTERVAL = 2


# ═══════════════════════════════════════════════════════════
#  FUNCTION 3 — Detect project type
# ═══════════════════════════════════════════════════════════
def detect_project_type(repo_path: str) -> str:
    """Check files in repo_path and return project type."""
    package_json_path = os.path.join(repo_path, "package.json")
    requirements_path = os.path.join(repo_path, "requirements.txt")

    if os.path.isfile(package_json_path):
        try:
            with open(package_json_path, "r") as f:
                pkg = json.load(f)
            deps = {
                **pkg.get("dependencies", {}),
                **pkg.get("devDependencies", {}),
            }
            if "next" in deps:
                return "nextjs"
            if "vite" in deps:
                return "vite"
            if "vue" in deps:
                return "vue"
            # Generic node project
            return "vite"
        except (json.JSONDecodeError, IOError):
            return "vite"

    if os.path.isfile(requirements_path):
        return "python"

    # Check for index.html
    if os.path.isfile(os.path.join(repo_path, "index.html")):
        return "static"

    return "static"


def _get_start_command(project_type: str) -> str:
    """Return the correct dev server command for the project type."""
    commands = {
        "nextjs": "npm run dev -- --port 3000",
        "vite": "npm run dev -- --port 3000",
        "vue": "npm run serve -- --port 3000",
        "python": "uvicorn main:app --host 0.0.0.0 --port 3000",
        "static": "python -m http.server 3000",
    }
    return commands.get(project_type, "python -m http.server 3000")


def _get_install_command(project_type: str, repo_path: str) -> str | None:
    """Return the dependency install command if needed."""
    if project_type in ("nextjs", "vite", "vue"):
        if os.path.isfile(os.path.join(repo_path, "yarn.lock")):
            return "yarn install"
        if os.path.isfile(os.path.join(repo_path, "pnpm-lock.yaml")):
            return "pnpm install"
        if os.path.isfile(os.path.join(repo_path, "package.json")):
            return "npm install"
    if project_type == "python":
        if os.path.isfile(os.path.join(repo_path, "requirements.txt")):
            return "pip install -r requirements.txt"
    return None


# ═══════════════════════════════════════════════════════════
#  FUNCTION 1 — Start preview
# ═══════════════════════════════════════════════════════════
async def start_preview(
    repo_path: str,
    websocket: WebSocket,
    task_id: str,
) -> str:
    """
    Start a live preview of the project.
    1. Detect project type
    2. Install dependencies
    3. Start dev server
    4. Wait for ready
    5. Start Playwright + noVNC
    6. Return preview URL
    """

    # ── 1. Detect project type ────────────────────────────
    project_type = detect_project_type(repo_path)
    logger.info("[preview] Detected project type: %s", project_type)

    try:
        await websocket.send_json({
            "type": "preview_starting",
            "message": f"Detected {project_type} project. Setting up preview...",
        })
    except Exception:
        pass

    # ── 2. Install dependencies ───────────────────────────
    install_cmd = _get_install_command(project_type, repo_path)
    if install_cmd:
        try:
            await websocket.send_json({
                "type": "preview_starting",
                "message": f"Installing dependencies: {install_cmd}",
            })
        except Exception:
            pass

        try:
            install_result = await asyncio.to_thread(
                subprocess.run,
                install_cmd.split(),
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=120,
            )
            if install_result.returncode != 0:
                logger.warning("[preview] Install warning: %s", install_result.stderr[:500])
        except subprocess.TimeoutExpired:
            logger.warning("[preview] Dependency install timed out")
        except Exception as e:
            logger.warning("[preview] Install failed: %s", e)

    # ── 3. Start app in background ────────────────────────
    start_command = _get_start_command(project_type)
    logger.info("[preview] Starting app: %s", start_command)

    try:
        await websocket.send_json({
            "type": "preview_starting",
            "message": f"Starting dev server: {start_command}",
        })
    except Exception:
        pass

    env = os.environ.copy()
    env["PORT"] = str(APP_PORT)
    env["NODE_ENV"] = "development"

    app_process = subprocess.Popen(
        start_command.split(),
        cwd=repo_path,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        preexec_fn=os.setsid,  # create process group for clean kill
    )

    # Store immediately so stop_preview can clean up on failure
    active_previews[task_id] = {
        "app_process": app_process,
        "playwright_browser": None,
        "novnc_process": None,
        "timeout_task": None,
    }

    # ── 4. Wait for app to be ready ───────────────────────
    app_url = f"http://localhost:{APP_PORT}"
    ready = False

    for attempt in range(1, MAX_READY_ATTEMPTS + 1):
        try:
            await websocket.send_json({
                "type": "preview_starting",
                "message": f"Waiting for app to start... (attempt {attempt}/{MAX_READY_ATTEMPTS})",
            })
        except Exception:
            pass

        # Check if process crashed
        if app_process.poll() is not None:
            stderr_output = ""
            try:
                stderr_output = app_process.stderr.read().decode()[:500]
            except Exception:
                pass
            logger.error("[preview] App process crashed: %s", stderr_output)
            await stop_preview(task_id)
            try:
                await websocket.send_json({
                    "type": "error",
                    "message": f"App failed to start: {stderr_output[:200]}",
                })
            except Exception:
                pass
            return ""

        # Poll the app
        try:
            if httpx:
                async with httpx.AsyncClient(timeout=2.0) as client:
                    resp = await client.get(app_url)
                    if resp.status_code < 500:
                        ready = True
                        break
            else:
                # Fallback without httpx
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection("localhost", APP_PORT),
                    timeout=2.0,
                )
                writer.close()
                await writer.wait_closed()
                ready = True
                break
        except Exception:
            pass

        await asyncio.sleep(READY_POLL_INTERVAL)

    if not ready:
        logger.error("[preview] App did not start within timeout")
        await stop_preview(task_id)
        try:
            await websocket.send_json({
                "type": "error",
                "message": "App did not start within 30 seconds. Check for build errors.",
            })
        except Exception:
            pass
        return ""

    logger.info("[preview] App is ready at %s", app_url)

    # ── 5. Start Playwright + noVNC ───────────────────────
    preview_url = ""

    try:
        if async_playwright:
            try:
                await websocket.send_json({
                    "type": "preview_starting",
                    "message": "Starting browser preview...",
                })
            except Exception:
                pass

            pw = await async_playwright().start()
            browser = await pw.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-gpu"],
            )
            page = await browser.new_page(viewport={"width": 1280, "height": 720})
            await page.goto(app_url, wait_until="domcontentloaded", timeout=15000)

            active_previews[task_id]["playwright_browser"] = browser

            # Start noVNC websockify proxy
            try:
                novnc_process = subprocess.Popen(
                    [
                        "websockify",
                        str(NOVNC_PORT),
                        f"localhost:{APP_PORT}",
                        "--web", "/usr/share/novnc",
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                active_previews[task_id]["novnc_process"] = novnc_process
                preview_url = f"http://localhost:{NOVNC_PORT}/vnc.html?autoconnect=true&resize=scale"
            except FileNotFoundError:
                # noVNC/websockify not installed — fall back to direct URL
                logger.warning("[preview] websockify not found, using direct URL")
                preview_url = app_url
        else:
            # Playwright not available — just use direct URL
            logger.warning("[preview] Playwright not installed, using direct app URL")
            preview_url = app_url

    except Exception as e:
        logger.warning("[preview] Browser preview setup failed: %s", e)
        preview_url = app_url

    # ── 6. Send preview URL to frontend ───────────────────
    try:
        await websocket.send_json({
            "type": "preview_ready",
            "preview_url": preview_url,
            "task_id": task_id,
            "message": f"Preview ready at {preview_url}",
        })
    except Exception:
        pass

    # ── 7. Start auto-timeout ─────────────────────────────
    async def _auto_expire():
        await asyncio.sleep(PREVIEW_TIMEOUT)
        logger.info("[preview] Preview expired for task %s", task_id)
        try:
            await websocket.send_json({
                "type": "preview_expired",
                "task_id": task_id,
                "message": "Preview expired after 5 minutes. Approve or reject changes?",
            })
        except Exception:
            pass
        await stop_preview(task_id)

    timeout_task = asyncio.create_task(_auto_expire())
    active_previews[task_id]["timeout_task"] = timeout_task

    return preview_url


# ═══════════════════════════════════════════════════════════
#  FUNCTION 2 — Stop preview
# ═══════════════════════════════════════════════════════════
async def stop_preview(task_id: str):
    """Kill all preview processes and clean up."""
    entry = active_previews.pop(task_id, None)
    if not entry:
        return

    # Cancel auto-timeout
    timeout_task = entry.get("timeout_task")
    if timeout_task and not timeout_task.done():
        timeout_task.cancel()

    # Kill Playwright browser
    browser = entry.get("playwright_browser")
    if browser:
        try:
            await browser.close()
        except Exception as e:
            logger.warning("[preview] Failed to close browser: %s", e)

    # Kill noVNC server
    novnc_process = entry.get("novnc_process")
    if novnc_process and novnc_process.poll() is None:
        try:
            novnc_process.terminate()
            novnc_process.wait(timeout=5)
        except Exception:
            try:
                novnc_process.kill()
            except Exception:
                pass

    # Kill app process (and its process group)
    app_process = entry.get("app_process")
    if app_process and app_process.poll() is None:
        try:
            os.killpg(os.getpgid(app_process.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            app_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(app_process.pid), signal.SIGKILL)
            except Exception:
                pass

    logger.info("[preview] Preview stopped for task %s", task_id)
