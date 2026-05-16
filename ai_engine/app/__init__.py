"""Lucid AI Engine — FastAPI application factory."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import logger, settings
from app.sdk import OPENHANDS_AVAILABLE, import_error
from app.services.sessions import store, destroy_session, reap_expired_sessions
from app.services.sandbox import docker_runner_manager
from app.services.workspace_manager import workspace_manager
from app.services.redis_client import connect_redis, disconnect_redis
from app.routers import health, sessions, ws, chat, files, integrations, preview, publish, admin, invites, projects


# Hard cap on incoming request body size for HTTP endpoints.
# WebSockets are not affected — they use their own handshake/message paths.
# 2 MB is well above any legitimate PAT/JSON payload (largest is repo list
# pagination response at ~200 KB) and under the point where an attacker can
# flood the process with a single request.
_MAX_BODY_BYTES = 2 * 1024 * 1024


class _BodySizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject HTTP requests whose declared body exceeds ``_MAX_BODY_BYTES``.

    We check ``Content-Length`` up-front so large uploads are rejected
    without being streamed into memory. Requests without a Content-Length
    header (chunked encoding) fall through — FastAPI/Starlette apply their
    own per-read limits via ``request.body()`` and the downstream handlers
    still operate on the parsed payload.
    """

    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith("/api/v1/ws"):
            return await call_next(request)
        cl = request.headers.get("content-length")
        if cl and cl.isdigit() and int(cl) > _MAX_BODY_BYTES:
            return JSONResponse(
                status_code=413,
                content={"detail": f"Request body too large (max {_MAX_BODY_BYTES} bytes)."},
            )
        return await call_next(request)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Verify dependencies on boot; clean up on shutdown."""
    logger.info("Lucid AI Engine starting …")

    # Connect Redis (session persistence — gracefully skipped if unavailable)
    await connect_redis()

    # Report SDK availability
    if OPENHANDS_AVAILABLE:
        logger.info("✅ OpenHands SDK is installed — real agent mode enabled")
    else:
        logger.warning(
            "⚠️ OpenHands SDK not installed — running in mock mode: %s",
            import_error or "N/A",
        )

    # Check Docker daemon availability (optional, mainly for cleanup)
    try:
        docker_available = await asyncio.to_thread(docker_runner_manager.is_docker_available)
        if docker_available:
            logger.info("Docker daemon is accessible")
            cleaned = await asyncio.to_thread(docker_runner_manager.cleanup_orphaned_containers)
            if cleaned:
                logger.info("Cleaned up %d orphaned sandbox containers", cleaned)
        else:
            logger.info("Docker daemon not accessible — SDK will use local execution")
    except Exception as exc:
        logger.info("Docker check skipped: %s", exc)

    if not settings.LLM_API_KEY and not settings.ANTHROPIC_API_KEY:
        logger.warning("No Anthropic / LLM keys set — agent will not function")

    # ── AI backend banner ───────────────────────────────────
    # All Gemini traffic goes through Vertex AI via ADC. AI Studio is
    # not supported.
    import os as _os
    _ADC = "/root/.config/gcloud/application_default_credentials.json"
    # Distinguish "missing" vs "bind-mounted as a directory" — Docker auto-
    # creates a directory at the destination if the host source path doesn't
    # exist, so isfile() alone wouldn't catch an empty-dir mount.
    if _os.path.isdir(_ADC):
        _adc_status = "❌ DIR (host secrets/gcloud-adc.json missing — Docker created an empty dir)"
        _adc_ok = False
    elif _os.path.isfile(_ADC):
        _adc_status = "✅ found"
        _adc_ok = True
    else:
        _adc_status = "❌ MISSING — mount secrets/gcloud-adc.json"
        _adc_ok = False

    logger.info(
        "🔷 VERTEX AI ACTIVE | project=%s | location=%s | ADC=%s",
        settings.GOOGLE_CLOUD_PROJECT or "(not set)",
        settings.GOOGLE_CLOUD_LOCATION,
        _adc_status,
    )
    if not _adc_ok:
        logger.error(
            "ADC file not found at %s. Every Gemini call will fail and the "
            "pipeline will silently emit placeholder content (\"Built for "
            "what's next\"). Fix: cp ~/.config/gcloud/application_default_credentials.json "
            "<repo>/secrets/gcloud-adc.json (delete the empty dir at that path first).",
            _ADC,
        )
    if not settings.GOOGLE_CLOUD_PROJECT:
        logger.error(
            "GOOGLE_CLOUD_PROJECT is empty — Vertex calls will fail. "
            "Set it in .env / docker-compose.yml."
        )

    # ── Active ADC self-test ──────────────────────────────────
    # `isfile` only catches the path; a real token fetch verifies the
    # credentials are valid AND the project is reachable. Caught silently
    # before — first user prompt failed with no obvious cause.
    if _adc_ok and settings.GOOGLE_CLOUD_PROJECT:
        try:
            from app.services.gemini_http import _get_adc_token  # internal helper
            await asyncio.to_thread(_get_adc_token)
            logger.info("✅ ADC token fetch succeeded — Vertex AI reachable")
        except Exception as _adc_exc:
            logger.error(
                "❌ ADC token fetch FAILED at startup — Vertex calls will all fall "
                "back to placeholder content. Error: %s", _adc_exc,
            )

    # Start the background session reaper (cleans up inactive sessions after 2h)
    reaper_task = asyncio.create_task(reap_expired_sessions())
    logger.info("Session reaper started (TTL=2h, interval=2min)")

    # Start the workspace reaper (cleans up workspaces unused for 2h)
    await workspace_manager.start_reaper()

    # Sweep orphan preview-trash directories left over from a previous
    # process crashing mid-rmtree. bg_preview moves stale dirs to
    # `<path>.trash-<ts>` and rmtrees them in a daemon thread; if the
    # engine dies before the thread finishes, the trash leaks.
    async def _sweep_preview_trash() -> None:
        import os, shutil
        from app.paths import PREVIEW_WS_ROOT
        try:
            if not os.path.isdir(PREVIEW_WS_ROOT):
                return
            swept = 0
            for entry in os.listdir(PREVIEW_WS_ROOT):
                if ".trash-" in entry or ".nm-" in entry:
                    path = os.path.join(PREVIEW_WS_ROOT, entry)
                    await asyncio.to_thread(shutil.rmtree, path, True)
                    swept += 1
            if swept:
                logger.info("Swept %d orphan preview-trash dirs from %s", swept, PREVIEW_WS_ROOT)
        except Exception as exc:
            logger.warning("preview-trash sweep failed: %s", exc)

    asyncio.create_task(_sweep_preview_trash())

    yield

    logger.info("Shutting down — cleaning up sessions …")
    reaper_task.cancel()
    try:
        await reaper_task
    except asyncio.CancelledError:
        pass
    for sid in await store.snapshot_ids():
        await destroy_session(sid)
    # Destroy any remaining Docker containers
    try:
        await docker_runner_manager.destroy_all()
    except Exception:
        pass
    # Destroy all lingering workspaces
    await workspace_manager.stop_reaper()
    await workspace_manager.destroy_all()
    await disconnect_redis()
    logger.info("All resources cleaned up.")


def create_app() -> FastAPI:
    """Build and return the configured FastAPI application."""
    application = FastAPI(
        title="Lucid AI Engine",
        description=(
            "AI Agent microservice powered by the OpenHands Software Agent SDK V1. "
            "Runs CodeActAgent in local workspace directories with "
            "real-time WebSocket event streaming and git push support."
        ),
        version="1.0.0",
        lifespan=lifespan,
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.add_middleware(_BodySizeLimitMiddleware)

    application.include_router(health.router)
    application.include_router(sessions.router)
    application.include_router(ws.router)
    application.include_router(chat.router)
    application.include_router(files.router)
    application.include_router(integrations.router)
    application.include_router(preview.router)
    application.include_router(publish.router)
    application.include_router(admin.router)
    application.include_router(invites.router)
    application.include_router(projects.router)

    return application


app = create_app()
