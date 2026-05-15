"""Single entry point for Gemini REST calls — Vertex AI only.

All ``generateContent`` traffic routes through Vertex AI. AI Studio
(``generativelanguage.googleapis.com`` + ``GOOGLE_API_KEY``) is no longer
supported; the platform standardized on Vertex for compliance, billing,
and IAM consistency.

  Vertex AI:  {location}-aiplatform.googleapis.com/v1/projects/{project}/locations/{location}/publishers/google/models/{model}:generateContent
              + Authorization: Bearer <ADC token>

Auth is Application Default Credentials (gcloud login locally, service
account JSON / Workload Identity in prod).

Response shapes are unchanged from prior versions, so existing parsers
keep working.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


# ── ADC token cache ────────────────────────────────────────────────────
# Vertex auth uses Google ADC (gcloud login locally, or service account
# JSON / Workload Identity in prod). Tokens expire ~1h — cache + refresh
# 5min before expiry. Thread-safe so multiple async callers don't all
# refresh at once.

_token_lock = threading.Lock()
_cached_token: str | None = None
_cached_expiry: float = 0.0


def _get_adc_token() -> str:
    """Return a valid OAuth2 access token from ADC, refreshing if needed.

    The token swap hits oauth2.googleapis.com, which can fail with a DNS or
    transport error on the very first call after a container cold-start
    (Docker's embedded resolver occasionally drops the first lookup). We
    retry a few times with short backoff so a transient blip doesn't kill
    the whole pipeline — without retries, analyze_intent silently falls back
    to its generic default and poisons every downstream stage.
    """
    global _cached_token, _cached_expiry
    now = time.time()
    if _cached_token and now < _cached_expiry - 300:
        return _cached_token

    with _token_lock:
        if _cached_token and now < _cached_expiry - 300:
            return _cached_token

        from google.auth import default as _adc_default
        from google.auth.transport.requests import Request as _AuthRequest

        last_exc: Exception | None = None
        for attempt in range(1, 4):  # 3 tries: 0s + 0.5s + 1.5s
            try:
                creds, _project = _adc_default(
                    scopes=["https://www.googleapis.com/auth/cloud-platform"]
                )
                creds.refresh(_AuthRequest())
                _cached_token = creds.token
                if creds.expiry:
                    _cached_expiry = creds.expiry.timestamp()
                else:
                    _cached_expiry = now + 3000  # conservative 50min default
                if attempt > 1:
                    logger.info("ADC token refresh succeeded on attempt %d", attempt)
                return _cached_token
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "ADC token refresh attempt %d/3 failed (%s: %s)",
                    attempt, type(exc).__name__, str(exc)[:200],
                )
                if attempt < 3:
                    time.sleep(0.5 * attempt)  # 0.5s, 1.0s
        assert last_exc is not None
        raise last_exc


def _build_url(model: str) -> str:
    """Build the Vertex generateContent URL for the chosen model."""
    location = settings.GOOGLE_CLOUD_LOCATION or "global"
    project = settings.GOOGLE_CLOUD_PROJECT
    if not project:
        raise RuntimeError("GOOGLE_CLOUD_PROJECT is empty — Vertex auth requires a project")
    host = (
        "aiplatform.googleapis.com"
        if location == "global"
        else f"{location}-aiplatform.googleapis.com"
    )
    return (
        f"https://{host}/v1/projects/{project}/locations/{location}"
        f"/publishers/google/models/{model}:generateContent"
    )


async def gemini_post(
    *,
    model: str,
    payload: dict[str, Any],
    timeout_s: float,
    label: str = "gemini",
) -> tuple[int, dict[str, Any] | None, str]:
    """POST to Vertex AI ``generateContent`` and return ``(status, json, raw)``.

    All Gemini REST traffic must go through this function. Auth is handled
    inside via cached ADC tokens — callers do NOT pass an API key.

    Returns:
      status_code:  HTTP status (-1 on transport error)
      json_or_none: parsed body if response was JSON, else None
      raw_text:     short prefix of the response body for logging
    """
    try:
        url = _build_url(model)
        token = _get_adc_token()
        headers = {"Authorization": f"Bearer {token}"}
    except Exception as exc:
        logger.warning("gemini %s: config error — %s", label, exc)
        return -1, None, str(exc)

    # Vertex requires `role: "user"` on each contents entry. Mutate a
    # shallow copy so we don't tweak the caller's dict.
    contents = payload.get("contents")
    if isinstance(contents, list):
        patched = [
            {**c, "role": c.get("role", "user")} if isinstance(c, dict) else c
            for c in contents
        ]
        payload = {**payload, "contents": patched}

    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.post(url, json=payload, headers=headers)
    except httpx.TimeoutException:
        logger.warning("gemini %s: timeout after %ss", label, timeout_s)
        return -1, None, "timeout"
    except Exception as exc:
        logger.warning("gemini %s: transport error — %s", label, exc)
        return -1, None, str(exc)

    if resp.status_code != 200:
        logger.warning(
            "gemini %s [vertex]: HTTP %d — %s",
            label, resp.status_code, resp.text[:300],
        )
        return resp.status_code, None, resp.text[:500]

    try:
        return resp.status_code, resp.json(), resp.text[:200]
    except Exception:
        logger.warning("gemini %s: non-JSON response", label)
        return resp.status_code, None, resp.text[:500]
