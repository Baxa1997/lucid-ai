"""Async Supabase client helpers.

Two client modes
----------------
``managed_client(user_jwt)``
    Anon key + user JWT → RLS enforced.  Use for all user-facing DB operations.
    Creates a fresh ``AsyncClient`` per call to avoid JWT cross-contamination
    across concurrent requests.

``managed_admin_client()``
    Service-role key → RLS bypassed.  Use **only** for server-to-server calls
    (X-Internal-Key path) where no user JWT is available.  Callers must filter
    rows by ``user_id`` explicitly to enforce data isolation.

``db_client(user_jwt)``
    Dispatcher — yields ``managed_client`` when ``user_jwt`` is a non-empty
    string, ``managed_admin_client`` when it is ``None``.  Import this in
    services instead of calling the two lower-level helpers directly.

Import note
-----------
``acreate_client`` was added to the public supabase-py API in 2.5.  For older
2.x releases the internal ``supabase._async.client.create_client`` is used as
a fallback (confirmed stable by supabase-py maintainers, issue #604).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

# Prefer the stable public API (supabase-py >= 2.5) and fall back to the
# internal module path that is confirmed correct for earlier 2.x releases.
try:
    from supabase import acreate_client as _create_client, AsyncClient
except ImportError:  # pragma: no cover
    from supabase._async.client import create_client as _create_client, AsyncClient  # type: ignore[no-redef]

from app.config import settings


@asynccontextmanager
async def managed_client(user_jwt: str) -> AsyncGenerator[AsyncClient, None]:
    """Anon key + user JWT → RLS enforced.

    Usage::

        async with managed_client(user_jwt) as client:
            result = await client.table("chat_sessions").select("*").execute()
    """
    client = await _create_client(settings.SUPABASE_URL, settings.SUPABASE_ANON_KEY)
    client.postgrest.auth(user_jwt)
    try:
        yield client
    finally:
        try:
            await client.aclose()                  # supabase-py >= 2.4
        except AttributeError:
            try:
                await client.postgrest.aclose()    # postgrest-py session
            except (AttributeError, Exception):
                pass
        except Exception:
            pass


@asynccontextmanager
async def managed_admin_client() -> AsyncGenerator[AsyncClient, None]:
    """Service-role key → RLS bypassed.

    Use only for server-to-server calls where no user JWT is available.
    Callers must filter by ``user_id`` explicitly to enforce data isolation.

    Usage::

        async with managed_admin_client() as client:
            result = await client.table("chat_sessions").select("*")\\
                .eq("user_id", user_id).execute()
    """
    client = await _create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)
    try:
        yield client
    finally:
        try:
            await client.aclose()
        except AttributeError:
            try:
                await client.postgrest.aclose()
            except (AttributeError, Exception):
                pass
        except Exception:
            pass


@asynccontextmanager
async def db_client(user_jwt: str | None) -> AsyncGenerator[AsyncClient, None]:
    """Dispatcher — choose the right client based on whether a JWT is present.

    ``user_jwt`` is a string  → ``managed_client``  (RLS enforced via anon key + JWT)
    ``user_jwt`` is ``None``  → ``managed_admin_client``  (service-role, RLS bypassed)
    """
    if user_jwt is None:
        async with managed_admin_client() as client:
            yield client
    else:
        async with managed_client(user_jwt) as client:
            yield client
