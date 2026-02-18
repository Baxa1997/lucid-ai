"""Health-check endpoints (no auth required)."""

from fastapi import APIRouter

from app.config import settings, MODEL_CONFIGS
from app.sdk import OPENHANDS_AVAILABLE
from app.services.docker_workspace import docker_manager
from app.services.sessions import store

router = APIRouter(tags=["health"])


@router.get("/")
async def root():
    """Detailed health check with system status."""
    return {
        "service": "Lucid AI Engine",
        "version": "1.0.0",
        "status": "healthy",
        "openhands_available": OPENHANDS_AVAILABLE,
        "docker_available": docker_manager.is_docker_available(),
        "active_sandboxes": docker_manager.active_container_count,
        "active_sessions": await store.count(),
        "llm_model": MODEL_CONFIGS.get(
            settings.DEFAULT_PROVIDER, {}
        ).get("model", "unknown"),
    }


@router.get("/health")
def health():
    """Minimal health check for load balancers."""
    return {"status": "ok"}
