"""Lucid AI Engine — FastAPI application factory."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import logger, settings
from app.sdk import OPENHANDS_AVAILABLE, import_error
from app.services.sessions import store, destroy_session, reap_expired_sessions
from app.services.docker_workspace import docker_manager
from app.services.workspace_manager import workspace_manager
from app.routers import health, sessions, ws, chat, files, integrations


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Verify dependencies on boot; clean up on shutdown."""
    logger.info("Lucid AI Engine starting …")

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
        docker_available = await asyncio.to_thread(docker_manager.is_docker_available)
        if docker_available:
            logger.info("Docker daemon is accessible")
            cleaned = await asyncio.to_thread(docker_manager.cleanup_orphaned_containers)
            if cleaned:
                logger.info("Cleaned up %d orphaned sandbox containers", cleaned)
        else:
            logger.info("Docker daemon not accessible — SDK will use local execution")
    except Exception as exc:
        logger.info("Docker check skipped: %s", exc)

    if not settings.LLM_API_KEY and not settings.GOOGLE_API_KEY and not settings.ANTHROPIC_API_KEY:
        logger.warning("No LLM API keys set — agent will not function")

    # Start the background session reaper (cleans up inactive sessions after 2h)
    reaper_task = asyncio.create_task(reap_expired_sessions())
    logger.info("Session reaper started (TTL=2h, interval=2min)")

    # Start the workspace reaper (cleans up workspaces unused for 2h)
    await workspace_manager.start_reaper()

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
        await docker_manager.destroy_all()
    except Exception:
        pass
    # Destroy all lingering workspaces
    await workspace_manager.stop_reaper()
    await workspace_manager.destroy_all()
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

    application.include_router(health.router)
    application.include_router(sessions.router)
    application.include_router(ws.router)
    application.include_router(chat.router)
    application.include_router(files.router)
    application.include_router(integrations.router)

    return application


app = create_app()
