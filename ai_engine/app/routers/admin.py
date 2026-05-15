"""Admin endpoints — operational visibility into the AI engine.

Endpoints are gated by the ``X-Internal-Key`` header matching
``settings.INTERNAL_API_KEY`` (same scheme the Next.js bridge uses for
server-to-server calls). Never expose this router on a public path.
"""
from __future__ import annotations

import hmac

from fastapi import APIRouter, Header, HTTPException, status

from app.config import settings
from app.services.pipeline_cache import pipeline_cache


router = APIRouter(prefix="/api/admin", tags=["admin"])


def _require_internal_key(x_internal_key: str | None) -> None:
    """Constant-time compare against INTERNAL_API_KEY."""
    expected = settings.INTERNAL_API_KEY or ""
    if not expected or not x_internal_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing X-Internal-Key",
        )
    if not hmac.compare_digest(expected, x_internal_key):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="bad X-Internal-Key",
        )


@router.get("/cache/stats")
async def cache_stats(x_internal_key: str | None = Header(default=None)):
    """Snapshot of pipeline_cache hit/miss counters + size."""
    _require_internal_key(x_internal_key)
    return pipeline_cache.stats()


@router.post("/cache/invalidate/{project_id}")
async def cache_invalidate(
    project_id: str,
    x_internal_key: str | None = Header(default=None),
):
    """Drop every cache entry belonging to ``project_id``.

    Called when the user clicks "Start over" or otherwise wants to force
    a fresh research run.
    """
    _require_internal_key(x_internal_key)
    evicted = pipeline_cache.invalidate(project_id)
    return {"project_id": project_id, "evicted": evicted}
