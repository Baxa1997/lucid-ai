"""Single entry point for Gemini REST calls.

Routes between AI Studio (legacy) and Vertex AI based on
``settings.USE_VERTEX_AI``. The request payload format is identical
between the two backends for ``generateContent`` — only the URL and
auth differ:

  AI Studio:  generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key=...
  Vertex AI:  {location}-aiplatform.googleapis.com/v1/projects/{project}/locations/{location}/publishers/google/models/{model}:generateContent
              + Authorization: Bearer <ADC token>

Response shapes are identical (candidates, usageMetadata, groundingMetadata),
so existing parsers don't need to change.
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
    """Return a valid OAuth2 access token from ADC, refreshing if needed."""
    global _cached_token, _cached_expiry
    now = time.time()
    if _cached_token and now < _cached_expiry - 300:
        return _cached_token

    with _token_lock:
        if _cached_token and now < _cached_expiry - 300:
            return _cached_token

        from google.auth import default as _adc_default
        from google.auth.transport.requests import Request as _AuthRequest

        creds, _project = _adc_default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        creds.refresh(_AuthRequest())
        _cached_token = creds.token
        # google.auth uses datetime — convert to epoch seconds.
        if creds.expiry:
            _cached_expiry = creds.expiry.timestamp()
        else:
            _cached_expiry = now + 3000  # conservative 50min default
        return _cached_token


def _build_url(model: str, *, vertex: bool) -> str:
    """Build the right generateContent URL for the chosen backend."""
    if vertex:
        location = settings.GOOGLE_CLOUD_LOCATION or "global"
        project = settings.GOOGLE_CLOUD_PROJECT
        if not project:
            raise RuntimeError(
                "USE_VERTEX_AI=true but GOOGLE_CLOUD_PROJECT is empty"
            )
        host = (
            "aiplatform.googleapis.com"
            if location == "global"
            else f"{location}-aiplatform.googleapis.com"
        )
        return (
            f"https://{host}/v1/projects/{project}/locations/{location}"
            f"/publishers/google/models/{model}:generateContent"
        )
    return (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent"
    )


def _build_auth(*, vertex: bool, api_key: str) -> tuple[str, dict[str, str]]:
    """Return (url_query_suffix, headers) for the chosen backend."""
    if vertex:
        token = _get_adc_token()
        return "", {"Authorization": f"Bearer {token}"}
    if not api_key:
        raise RuntimeError("AI Studio path requires GOOGLE_API_KEY")
    return f"?key={api_key}", {}


async def gemini_post(
    *,
    model: str,
    payload: dict[str, Any],
    timeout_s: float,
    api_key: str = "",
    label: str = "gemini",
) -> tuple[int, dict[str, Any] | None, str]:
    """POST to Gemini and return ``(status_code, json_or_none, raw_text)``.

    Identical to a manual ``httpx.post`` against the AI Studio endpoint
    today — callers keep their existing payload-building, response
    parsing, and token-billing code. The only thing this function hides
    is which backend (AI Studio vs Vertex) is hit.

    Returns:
      status_code:  HTTP status (-1 on transport error)
      json_or_none: parsed body if response was JSON, else None
      raw_text:     short prefix of the response body for logging
    """
    vertex = bool(settings.USE_VERTEX_AI)
    try:
        base = _build_url(model, vertex=vertex)
        suffix, headers = _build_auth(vertex=vertex, api_key=api_key)
        url = base + suffix
    except Exception as exc:
        logger.warning("gemini %s: config error — %s", label, exc)
        return -1, None, str(exc)

    # Vertex requires `role: "user"` on each contents entry; AI Studio is
    # tolerant. Normalize here so call sites don't need to know the
    # backend. Mutate a shallow copy to keep the caller's dict pristine.
    if vertex:
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
            "gemini %s [%s]: HTTP %d — %s",
            label,
            "vertex" if vertex else "ai_studio",
            resp.status_code,
            resp.text[:300],
        )
        return resp.status_code, None, resp.text[:500]

    try:
        return resp.status_code, resp.json(), resp.text[:200]
    except Exception:
        logger.warning("gemini %s: non-JSON response", label)
        return resp.status_code, None, resp.text[:500]
