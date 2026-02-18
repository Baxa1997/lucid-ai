"""Lucid AI Engine — FastAPI application factory."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import logger, settings
from app.sdk import OPENHANDS_AVAILABLE, import_error
from app.services.sessions import store, destroy_session
from app.services.docker_workspace import docker_manager
from app.database import dispose_engine
from app.routers import health, sessions, ws, chat, files


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Verify Docker access on boot; clean up on shutdown."""
    logger.info("Lucid AI Engine starting …")

    # Check Docker daemon availability
    docker_available = await asyncio.to_thread(docker_manager.is_docker_available)
    if docker_available:
        logger.info("Docker daemon is accessible — per-session sandboxing enabled")
        # Clean up orphaned containers from previous runs
        cleaned = await asyncio.to_thread(docker_manager.cleanup_orphaned_containers)
        if cleaned:
            logger.info("Cleaned up %d orphaned sandbox containers", cleaned)
    else:
        logger.warning(
            "Docker daemon not accessible — falling back to local workspace mode"
        )

    if not OPENHANDS_AVAILABLE:
        logger.warning("OpenHands SDK not installed: %s", import_error or "N/A")
    if not settings.LLM_API_KEY:
        logger.warning("LLM_API_KEY not set — agent will not function")

    yield

    logger.info("Shutting down — cleaning up sessions …")
    for sid in await store.snapshot_ids():
        await destroy_session(sid)
    # Destroy any remaining Docker containers
    await docker_manager.destroy_all()
    await dispose_engine()
    logger.info("All resources cleaned up.")


def create_app() -> FastAPI:
    """Build and return the configured FastAPI application."""
    application = FastAPI(
        title="Lucid AI Engine",
        description=(
            "AI Agent microservice powered by the OpenHands Software Agent SDK V1. "
            "Runs CodeActAgent in per-session Docker-sandboxed workspaces with "
            "real-time WebSocket event streaming and git push support."
        ),
        version="1.0.0",
        lifespan=lifespan,
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    application.include_router(health.router)
    application.include_router(sessions.router)
    application.include_router(ws.router)
    application.include_router(chat.router)
    application.include_router(files.router)

    return application


app = create_app()
