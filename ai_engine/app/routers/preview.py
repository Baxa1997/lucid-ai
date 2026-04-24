"""Preview utility endpoints.

Right now there's one:

    GET /api/v1/preview/probe?url=<absolute-url>

returns ``{alive: bool, status: int, reason: str}`` for an arbitrary URL.

Why this exists
---------------
The workspace page shows a stored deploy URL (Vercel, custom domain) in an
iframe before the local dev server boots. A cross-origin JS probe can only
distinguish "reachable" from "DNS/connection failed" — it cannot see a 404
or a 401 page because ``mode:'no-cors'`` returns an opaque response. That
leaves users staring at broken Vercel pages with no fallback UX.

This endpoint runs the probe server-side where the HTTP status is visible,
so the frontend can fall back to the "Restart Preview" card when the
stored URL returns a 4xx/5xx.

SSRF hardening
--------------
The URL is caller-supplied, so we carefully reject any input that could
turn this endpoint into a probe of our own internal network:

  • scheme must be http or https
  • hostname must resolve to a *public* IP — private/loopback/link-local/
    reserved ranges are refused
  • response body is not returned (we only expose the status code)
  • timeout is 5 s; rate-limited to 60/min/user so this can't be abused as
    a general-purpose network scanner
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth import AuthenticatedUser, get_current_user
from app.config import logger
from app.routers.integrations import _rate_limit  # re-use the sliding-window limiter

router = APIRouter(prefix="/api/v1/preview", tags=["preview"])


_PROBE_TIMEOUT_S = 5.0
_MAX_URL_LEN = 2048
_MAX_REDIRECTS = 3


def _reject_ssrf_target(url: str) -> tuple[bool, str]:
    """Return ``(ok, reason)``. ``ok=False`` means the URL must not be fetched.

    Resolving *all* A/AAAA records (not just the first) and rejecting any
    private range closes the trivial ``rebind.example.com → 127.0.0.1``
    attack while still allowing legitimate public hostnames.
    """
    try:
        parsed = urlparse(url)
    except Exception as exc:
        return False, f"invalid URL: {exc}"
    if parsed.scheme not in ("http", "https"):
        return False, "scheme must be http or https"
    host = parsed.hostname
    if not host:
        return False, "missing hostname"

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False, "hostname does not resolve"

    for _family, _type, _proto, _canon, sockaddr in infos:
        ip_str = sockaddr[0]
        # IPv6 addresses may carry a zone suffix (fe80::1%eth0) — strip it.
        ip_str = ip_str.split("%", 1)[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            return False, "resolved non-IP address"
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            return False, f"target resolves to non-public address ({ip})"
    return True, ""


@router.get("/probe")
async def probe_url(
    url: str = Query(..., description="Absolute URL to probe"),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Check whether ``url`` returns a renderable response.

    Status interpretation::

        2xx / 3xx  → alive=True   (iframe will likely render something)
        401 / 403  → alive=False  (auth wall; iframe can't pass creds)
        404 / 410  → alive=False  (deployment missing)
        5xx        → alive=False  (server error)
        timeout    → alive=False  (caller should fall back)

    A call returning ``alive=False`` means the frontend should not render
    the URL in an iframe — it should show its "Preview unavailable" card
    with a Restart button instead.
    """
    _rate_limit("preview_probe", user.user_id, max_calls=60, window_s=60.0)

    if len(url) > _MAX_URL_LEN:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"URL exceeds {_MAX_URL_LEN} characters",
        )
    ok, reason = _reject_ssrf_target(url)
    if not ok:
        logger.info("preview probe rejected %.80s: %s", url, reason)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"URL rejected: {reason}",
        )

    try:
        async with httpx.AsyncClient(
            timeout=_PROBE_TIMEOUT_S,
            follow_redirects=True,
            max_redirects=_MAX_REDIRECTS,
        ) as client:
            try:
                resp = await client.head(url)
            except httpx.HTTPError:
                raise
            # Some edges (Vercel included) reject HEAD. Fall through to GET
            # so a method-not-allowed isn't mistaken for a dead deployment.
            if resp.status_code in (405, 501):
                resp = await client.get(url)
    except httpx.TimeoutException:
        return {"alive": False, "status": 0, "reason": "timeout"}
    except httpx.HTTPError as exc:
        # Connection refused, DNS flaps, TLS errors, too many redirects.
        return {"alive": False, "status": 0, "reason": type(exc).__name__}
    except Exception as exc:
        logger.warning("preview probe unexpected failure for %.80s: %s", url, exc)
        return {"alive": False, "status": 0, "reason": "unknown"}

    alive = 200 <= resp.status_code < 400
    return {"alive": alive, "status": resp.status_code}
