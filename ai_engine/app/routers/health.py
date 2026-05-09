"""Health-check endpoints (no auth required)."""

import os
from fastapi import APIRouter

from app.config import settings, MODEL_CONFIGS
from app.sdk import OPENHANDS_AVAILABLE
from app.services.sandbox import docker_runner_manager
from app.services.sessions import store

router = APIRouter(tags=["health"])

# Path where google-auth looks for ADC credentials inside the container
_ADC_PATH = "/root/.config/gcloud/application_default_credentials.json"


def _ai_backend_status() -> dict:
    """Return a dict describing which AI backend is currently active."""
    if settings.USE_VERTEX_AI:
        adc_present = os.path.isfile(_ADC_PATH)
        return {
            "ai_backend": "VERTEX AI ACTIVE",
            "gcp_project": settings.GOOGLE_CLOUD_PROJECT or "(not set)",
            "gcp_location": settings.GOOGLE_CLOUD_LOCATION,
            "adc_file_present": adc_present,
            "adc_path": _ADC_PATH,
        }
    return {
        "ai_backend": "AI STUDIO ACTIVE",
        "google_api_key_set": bool(settings.GOOGLE_API_KEY),
    }


@router.get("/")
async def root():
    """Detailed health check with system status."""
    return {
        "service": "Lucid AI Engine",
        "version": "1.0.0",
        "status": "healthy",
        "openhands_available": OPENHANDS_AVAILABLE,
        "docker_available": docker_runner_manager.is_docker_available(),
        "active_sandboxes": docker_runner_manager.active_container_count,
        "active_sessions": await store.count(),
        "llm_model": MODEL_CONFIGS.get(
            settings.DEFAULT_PROVIDER, {}
        ).get("model", "unknown"),
        **_ai_backend_status(),
    }


@router.get("/health")
def health():
    """Minimal health check for load balancers."""
    return {"status": "ok"}


@router.get("/health/ai")
def health_ai():
    """Quick check: which AI backend is active and is ADC wired up?"""
    return _ai_backend_status()
