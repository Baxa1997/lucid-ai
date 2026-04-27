"""Billing meter — fire-and-forget LLM token usage reporting.

The frontend exposes ``POST /api/internal/usage`` which writes to the
``usage_periods`` table (and consumes from ``extra_token_balance`` when
the monthly quota is exhausted). This module is the ai_engine-side caller.

Design choices:
  • **Fire-and-forget**: failures are logged at WARNING and swallowed.
    A metering hiccup must never break a user's generation.
  • **Background task**: each report is dispatched to ``asyncio.create_task``
    so the LLM call returns immediately. The caller doesn't await it.
  • **Idempotency-free**: each call is a delta. Replays would double-count;
    we accept that tradeoff because retries on a billing endpoint would
    be more dangerous than under-reporting.
  • **No user_id → no-op**: skips silently. Used during wizard mode where
    user_id is sometimes resolved later.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger("lucid.ai_engine.billing")

# Resolved at module load. Falls back to in-cluster Docker DNS.
# Set explicitly via env when running outside Docker.
_FRONTEND_URL = os.environ.get("LUCID_FRONTEND_URL", "http://frontend:3000")
_INTERNAL_KEY = os.environ.get("INTERNAL_API_KEY", "")


async def _post_usage(
    user_id: str,
    input_tokens: int,
    output_tokens: int,
    source: str,
) -> None:
    if not _INTERNAL_KEY:
        # Shared secret missing — nothing to do but log once.
        logger.warning("[billing] INTERNAL_API_KEY not set; skipping usage report")
        return
    if not user_id:
        return
    if input_tokens <= 0 and output_tokens <= 0:
        return

    url = f"{_FRONTEND_URL.rstrip('/')}/api/internal/usage"
    headers = {
        "Content-Type": "application/json",
        "X-Internal-Key": _INTERNAL_KEY,
        "X-User-ID": user_id,
    }
    payload = {
        "input_tokens": int(input_tokens),
        "output_tokens": int(output_tokens),
        "source": source,
    }
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.post(url, json=payload, headers=headers)
        if r.status_code >= 400:
            logger.warning(
                "[billing] usage report HTTP %d for user=%s src=%s body=%s",
                r.status_code, user_id, source, r.text[:200],
            )
        else:
            logger.debug(
                "[billing] reported %d in / %d out tokens for user=%s src=%s",
                input_tokens, output_tokens, user_id, source,
            )
    except Exception as exc:
        logger.warning("[billing] usage report failed for user=%s: %s", user_id, exc)


def report_token_usage(
    user_id: Optional[str],
    input_tokens: int,
    output_tokens: int,
    *,
    source: str = "unknown",
) -> None:
    """Schedule a usage report. Returns immediately; the POST happens in the
    background. Safe to call from any async context."""
    if not user_id or (input_tokens <= 0 and output_tokens <= 0):
        return
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_post_usage(user_id, input_tokens, output_tokens, source))
    except RuntimeError:
        # No running loop — synchronous caller. Run inline as a last resort
        # so the data isn't lost. Should be rare in our codebase.
        try:
            asyncio.run(_post_usage(user_id, input_tokens, output_tokens, source))
        except Exception as exc:
            logger.warning("[billing] sync usage report failed: %s", exc)
