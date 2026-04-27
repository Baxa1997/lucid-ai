"""llm_retry.py — central retry wrapper for LLM API calls.

Why this exists
───────────────
Every LLM call site (Claude direct HTTP, Gemini SDK, OpenAI direct HTTP)
used to swallow transient API errors (rate limits, 5xx, network blips,
timeouts) and return None — which then crashed the whole pipeline because
downstream code assumed the call succeeded. A single 503 from Anthropic
would lose the user 12 minutes of generation.

This module provides one helper, ``call_with_retry``, that classifies
errors as transient or permanent and retries the transient ones with
exponential backoff + jitter. Permanent errors (400, 401, 403) bubble up
immediately so we don't waste time on bad input.

Usage
─────
    from app.services.llm_retry import call_with_retry, LLMTransientError

    async def make_call() -> dict:
        async with httpx.AsyncClient() as c:
            r = await c.post(url, json=payload)
            if r.status_code in (429, 500, 502, 503, 504):
                raise LLMTransientError(f"{r.status_code}: {r.text[:200]}")
            r.raise_for_status()
            return r.json()

    result = await call_with_retry(make_call, label="copy_director")
"""
from __future__ import annotations

import asyncio
import logging
import random
from typing import Awaitable, Callable, TypeVar

import httpx

logger = logging.getLogger(__name__)

T = TypeVar("T")


# ── Custom exception types ──────────────────────────────────────────────

class LLMError(Exception):
    """Base for LLM call failures."""


class LLMTransientError(LLMError):
    """Retry-able: rate limit, 5xx, network error, timeout."""


class LLMPermanentError(LLMError):
    """Don't retry: 400 bad request, 401/403 auth, content policy block."""


# ── HTTP status classification ──────────────────────────────────────────

# Status codes that are worth retrying. 429 = rate limit (Retry-After).
# 408 = request timeout. 500/502/503/504 = upstream provider hiccup.
_TRANSIENT_STATUSES = {408, 425, 429, 500, 502, 503, 504, 522, 524}

# Status codes that mean "your request is bad" — retrying won't help.
_PERMANENT_STATUSES = {400, 401, 403, 404, 422}


def classify_http_error(status_code: int, body_snippet: str = "") -> LLMError:
    """Convert an HTTP status into the right exception type."""
    msg = f"HTTP {status_code}"
    if body_snippet:
        msg += f": {body_snippet[:200]}"
    if status_code in _TRANSIENT_STATUSES:
        return LLMTransientError(msg)
    if status_code in _PERMANENT_STATUSES:
        return LLMPermanentError(msg)
    # Anything else (3xx, unknown) — treat as transient by default; safer
    # to retry once than to silently lose user work.
    return LLMTransientError(msg)


def classify_exception(exc: Exception) -> LLMError:
    """Convert a raised exception into transient/permanent."""
    if isinstance(exc, LLMError):
        return exc

    # httpx raises specific subclasses for network-level failures
    if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError)):
        return LLMTransientError(f"{type(exc).__name__}: {exc}")

    # Generic asyncio/connection errors → transient
    if isinstance(exc, (asyncio.TimeoutError, ConnectionError, OSError)):
        return LLMTransientError(f"{type(exc).__name__}: {exc}")

    # Default: assume transient. The caller can pre-classify by raising
    # LLMPermanentError directly inside their function.
    return LLMTransientError(f"{type(exc).__name__}: {exc}")


# ── Retry orchestrator ──────────────────────────────────────────────────

async def call_with_retry(
    fn: Callable[[], Awaitable[T]],
    *,
    label: str,
    max_attempts: int = 3,
    base_delay: float = 1.5,
    max_delay: float = 30.0,
    on_retry: Callable[[int, Exception], None] | None = None,
    websocket=None,
) -> T:
    """Call ``fn`` with exponential backoff + jitter on transient failures.

    Args:
        fn: Zero-arg async callable that returns the success value or
            raises an exception. The function should classify its own
            permanent failures by raising ``LLMPermanentError``.
        label: Short string used in logs (e.g. "copy_director", "gemini_explore").
        max_attempts: Total attempts including the first try. Default 3.
        base_delay: Seconds before retry #1. Doubles each attempt.
        max_delay: Cap on per-attempt sleep.
        on_retry: Optional callback ``(attempt, error)`` invoked before
            sleeping — useful for surfacing "retrying…" to the UI.
        websocket: When provided, emits a ``warning`` event on each retry so
            the frontend can surface "We hit a hiccup, retrying…" to the user.

    Returns:
        The function's success value.

    Raises:
        LLMPermanentError or LLMTransientError after attempts are exhausted.
    """
    last_exc: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            return await fn()
        except Exception as exc:
            classified = classify_exception(exc)
            last_exc = classified

            if isinstance(classified, LLMPermanentError):
                logger.warning(
                    "llm_retry [%s] permanent failure (attempt %d): %s",
                    label, attempt, classified,
                )
                raise classified

            if attempt >= max_attempts:
                logger.warning(
                    "llm_retry [%s] giving up after %d attempts: %s",
                    label, attempt, classified,
                )
                raise classified

            # Exponential backoff with full jitter (AWS-recommended pattern)
            delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
            delay = random.uniform(0, delay)

            logger.info(
                "llm_retry [%s] attempt %d/%d failed (%s) — retrying in %.1fs",
                label, attempt, max_attempts, classified, delay,
            )

            if on_retry is not None:
                try:
                    on_retry(attempt, classified)
                except Exception as cb_err:
                    logger.debug("llm_retry on_retry callback raised: %s", cb_err)

            if websocket is not None:
                try:
                    delay_str = "<1s" if delay < 1 else f"{delay:.0f}s"
                    await websocket.send_json({
                        "type": "warning",
                        "message": (
                            f"⏳ Hit a temporary issue, retrying in {delay_str} "
                            f"(attempt {attempt + 1}/{max_attempts})…"
                        ),
                    })
                except Exception as ws_err:
                    logger.debug("llm_retry ws notify failed (non-fatal): %s", ws_err)

            await asyncio.sleep(delay)

    # Unreachable, but keeps type-checkers happy
    if last_exc is not None:
        raise last_exc
    raise LLMTransientError(f"llm_retry [{label}] exhausted with no exception captured")


# ── Pipeline-level failure helper ───────────────────────────────────────

async def emit_pipeline_failure(
    websocket,
    *,
    phase: str,
    code: str,
    message: str,
    retriable: bool = True,
) -> None:
    """Emit a structured failure event so the frontend can render a
    specific recovery UI instead of a generic "Generation failed".

    Args:
        phase: Which pipeline step failed (e.g. "classify", "research", "execute").
        code: Stable machine code — one of: ``rate_limit``, ``auth_error``,
              ``content_blocked``, ``network``, ``timeout``, ``unknown``.
        message: User-facing sentence (no jargon, no stack traces).
        retriable: Whether the user should be encouraged to retry.
    """
    if websocket is None:
        return
    try:
        await websocket.send_json({
            "type": "pipeline_failure",
            "phase": phase,
            "code": code,
            "message": message,
            "retriable": retriable,
        })
    except Exception as exc:
        logger.debug("emit_pipeline_failure: ws send failed: %s", exc)


def failure_code_from_exception(exc: Exception) -> str:
    """Map an LLMError (or generic exception) to a stable code string."""
    msg = str(exc).lower()
    if isinstance(exc, LLMPermanentError):
        if "401" in msg or "403" in msg or "auth" in msg:
            return "auth_error"
        if "content" in msg or "blocked" in msg or "policy" in msg:
            return "content_blocked"
        return "bad_request"
    if "429" in msg or "rate" in msg:
        return "rate_limit"
    if "timeout" in msg or "timed out" in msg:
        return "timeout"
    if "network" in msg or "connection" in msg:
        return "network"
    return "unknown"
