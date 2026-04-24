"""Audit log — append-only record of security-relevant actions.

Writes a row into the ``audit_logs`` Supabase table (see migration 016).
Every logger call is fail-soft: the caller never sees an exception if the
DB is unreachable, because losing an audit row should never block a user
action. The failure is logged at warning level so ops visibility is kept.

Typical use::

    from app.services.audit import record_event

    await record_event(
        user_jwt=user.raw_jwt,
        user_id=user.user_id,
        event_type="integration.saved",
        event_key=provider.lower(),
        metadata={"external_username": external_username},
        request=request,
    )

The ``request`` argument is optional; when provided we extract client IP
and user-agent automatically. Pass ``None`` for server-to-server calls.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import Request

from app.supabase_client import db_client

logger = logging.getLogger("lucid.audit")


def _client_ip(request: Optional[Request]) -> Optional[str]:
    if request is None:
        return None
    # Respect X-Forwarded-For (behind a CDN / load balancer) when present.
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        # Left-most entry is the origin client.
        return fwd.split(",")[0].strip() or None
    client = request.client
    return client.host if client else None


async def record_event(
    *,
    user_id: str,
    user_jwt: Optional[str],
    event_type: str,
    event_key: str = "",
    metadata: Optional[dict[str, Any]] = None,
    request: Optional[Request] = None,
) -> None:
    """Insert one audit row. Never raises."""
    if not user_id or not event_type:
        return
    ip = _client_ip(request)
    ua = request.headers.get("user-agent") if request is not None else None
    row: dict[str, Any] = {
        "user_id": user_id,
        "event_type": event_type[:120],
        "event_key": (event_key or "")[:255],
        "metadata": metadata or {},
    }
    if ip:
        row["ip_addr"] = ip
    if ua:
        row["user_agent"] = ua[:512]
    try:
        async with db_client(user_jwt) as client:
            await client.table("audit_logs").insert(row).execute()
    except Exception as exc:
        # Losing an audit row must never break the user's action. Keep
        # visibility by logging at warning — ops dashboards can alert on
        # sustained audit-insert failures.
        logger.warning(
            "audit insert failed (event=%s user=%s): %s",
            event_type, user_id, exc,
        )
