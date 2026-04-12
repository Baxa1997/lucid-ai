"""Redis client singleton.

Provides a single shared async Redis connection for the lifetime of the process.
All other modules call get_redis() — it returns None when Redis is disabled or
unavailable, so callers must handle the None case gracefully.

Usage:
    from app.services.redis_client import get_redis

    redis = get_redis()
    if redis:
        await redis.set("key", "value", ex=3600)
"""

from __future__ import annotations

from app.config import logger, settings

try:
    import redis.asyncio as aioredis
    _REDIS_AVAILABLE = True
except ImportError:
    aioredis = None  # type: ignore
    _REDIS_AVAILABLE = False

_redis: "aioredis.Redis | None" = None


async def connect_redis() -> None:
    """Open the Redis connection. Called once at application startup."""
    global _redis

    if not _REDIS_AVAILABLE:
        logger.warning("redis package not installed — session persistence disabled")
        return

    if not settings.REDIS_URL:
        logger.info("REDIS_URL not set — session persistence disabled")
        return

    try:
        _redis = aioredis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=3,
            socket_timeout=3,
        )
        await _redis.ping()
        logger.info("Redis connected: %s", settings.REDIS_URL)
    except Exception as exc:
        logger.warning(
            "Redis unavailable (%s) — falling back to in-memory-only session store", exc
        )
        _redis = None


async def disconnect_redis() -> None:
    """Close the Redis connection. Called once at application shutdown."""
    global _redis
    if _redis is not None:
        try:
            await _redis.aclose()
        except Exception:
            pass
        _redis = None
        logger.info("Redis disconnected")


def get_redis() -> "aioredis.Redis | None":
    """Return the active Redis client, or None if Redis is not available."""
    return _redis
