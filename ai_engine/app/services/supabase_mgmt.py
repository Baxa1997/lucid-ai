"""Supabase Management API client.

Provisions a fresh Supabase project per generated app. Each customer's app
gets its own database / URL / API keys — no shared tables, no cross-tenant
risk. All projects live under one Lucid-owned Pro org for cost control.

Endpoints used (api.supabase.com):
  POST   /v1/projects                    create project
  GET    /v1/projects                    list projects (used to confirm exists)
  GET    /v1/projects/{ref}/api-keys     fetch anon + service keys
  DELETE /v1/projects/{ref}              delete (test cleanup)

Auth: bearer token (settings.SUPABASE_MGMT_TOKEN). Token must have
projects:write scope. Treat as admin — it can delete any project in the org.

Pure async I/O via httpx. Failures raise SupabaseMgmtError with a structured
context dict so the quality gate / error reporter can act on them.
"""
from __future__ import annotations

import asyncio
import logging
import re
import secrets
import string
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_API_BASE = "https://api.supabase.com"
# Project ref is always 20 lowercase letters (Supabase convention).
_REF_RE = re.compile(r"^[a-z]{20}$")
# Project name validation: 1-64 chars, alphanumeric + dash/space/underscore.
_NAME_RE = re.compile(r"^[A-Za-z0-9 _\-]{1,64}$")


class SupabaseMgmtError(Exception):
    """Raised for any Mgmt API failure. .context carries the relevant
    request/response shape for diagnostics."""

    def __init__(self, message: str, *, context: dict[str, Any] | None = None):
        super().__init__(message)
        self.context = context or {}


@dataclass(frozen=True)
class ProvisionedProject:
    """Result of a successful project creation. Keys are sensitive — only
    log .ref and .url; persist anon_key/service_key encrypted."""
    ref: str
    url: str
    anon_key: str
    service_key: str
    db_password: str
    region: str


def _require_token() -> str:
    if not settings.SUPABASE_MGMT_TOKEN:
        raise SupabaseMgmtError(
            "SUPABASE_MGMT_TOKEN is not set. Generate a personal access "
            "token at https://supabase.com/dashboard/account/tokens with "
            "projects:write scope and add it to ai_engine/.env."
        )
    return settings.SUPABASE_MGMT_TOKEN


def _require_org() -> str:
    if not settings.SUPABASE_MGMT_ORG_REF:
        raise SupabaseMgmtError(
            "SUPABASE_MGMT_ORG_REF is not set. Find your org slug at "
            "supabase.com/dashboard/org/<slug> and add SUPABASE_MGMT_ORG_REF "
            "to ai_engine/.env."
        )
    return settings.SUPABASE_MGMT_ORG_REF


def _gen_db_password(length: int | None = None) -> str:
    """Cryptographically random URL-safe password. Avoids characters that
    cause shell-quoting headaches (no $, `, ', ", \\, space)."""
    n = length or settings.SUPABASE_DB_PASSWORD_LENGTH
    alphabet = string.ascii_letters + string.digits + "-_"
    return "".join(secrets.choice(alphabet) for _ in range(n))


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_require_token()}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


async def _request(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    *,
    json: dict[str, Any] | None = None,
    expected: tuple[int, ...] = (200, 201),
) -> Any:
    """Single Mgmt API call with structured error wrapping. Body is parsed
    as JSON when content-type allows; otherwise raw bytes are returned."""
    url = f"{_API_BASE}{path}"
    try:
        resp = await client.request(method, url, headers=_headers(), json=json)
    except httpx.HTTPError as exc:
        raise SupabaseMgmtError(
            f"Network error calling {method} {path}: {exc}",
            context={"method": method, "path": path, "exc": str(exc)},
        ) from exc

    if resp.status_code not in expected:
        body_excerpt = resp.text[:500] if resp.text else ""
        raise SupabaseMgmtError(
            f"Mgmt API {method} {path} returned {resp.status_code}: {body_excerpt}",
            context={
                "method": method,
                "path": path,
                "status": resp.status_code,
                "body": body_excerpt,
            },
        )

    if not resp.content:
        return None
    ctype = resp.headers.get("content-type", "")
    if "application/json" in ctype:
        return resp.json()
    return resp.content


async def create_project(
    *,
    name: str,
    region: str | None = None,
    plan: str = "free",
    timeout_seconds: float = 60.0,
) -> ProvisionedProject:
    """Provision a new Supabase project under the configured org.

    Args:
      name: human-readable project name; must match _NAME_RE.
      region: AWS region slug (e.g. "us-east-1"). Defaults to settings.SUPABASE_DEFAULT_REGION.
      plan: "free" or "pro". Pro orgs can mix free+paid project tiers.
      timeout_seconds: wall-clock cap for the create call. Provisioning
        usually returns within 15-30s; the project's DB may still be
        booting in the background — wait_until_ready() handles that.

    Returns ProvisionedProject with ref, url, both API keys, and the
    server-generated db_password (caller must encrypt before storing).

    Raises SupabaseMgmtError on any failure. Never persists secrets to
    logs.
    """
    if not name or not _NAME_RE.match(name):
        raise SupabaseMgmtError(
            f"Invalid project name {name!r}: must be 1-64 chars, alphanumeric + dash/space/underscore.",
            context={"name": name},
        )

    org_ref = _require_org()
    chosen_region = region or settings.SUPABASE_DEFAULT_REGION
    db_password = _gen_db_password()

    payload = {
        "name": name,
        "organization_id": org_ref,
        "region": chosen_region,
        "plan": plan,
        "db_pass": db_password,
    }

    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        created = await _request(client, "POST", "/v1/projects", json=payload, expected=(200, 201))
        if not isinstance(created, dict):
            raise SupabaseMgmtError(
                "Mgmt API returned non-object response from POST /v1/projects",
                context={"response_type": type(created).__name__},
            )

        ref = created.get("id") or created.get("ref")
        if not ref or not _REF_RE.match(ref):
            raise SupabaseMgmtError(
                f"Created project has invalid ref {ref!r}",
                context={"created": {k: v for k, v in created.items() if k != "db_pass"}},
            )

        # The create response usually contains api keys + url, but they're
        # not always populated until the project is ready. Fetch keys
        # explicitly so we have a deterministic shape.
        url = f"https://{ref}.supabase.co"
        anon_key, service_key = await _fetch_api_keys(client, ref)

    logger.info(
        "Provisioned Supabase project ref=%s region=%s plan=%s",
        ref, chosen_region, plan,
    )
    return ProvisionedProject(
        ref=ref,
        url=url,
        anon_key=anon_key,
        service_key=service_key,
        db_password=db_password,
        region=chosen_region,
    )


async def _fetch_api_keys(client: httpx.AsyncClient, ref: str) -> tuple[str, str]:
    """Return (anon_key, service_role_key) for a project. The keys endpoint
    returns a list of {name, api_key}; we extract the two we need."""
    keys = await _request(client, "GET", f"/v1/projects/{ref}/api-keys", expected=(200,))
    if not isinstance(keys, list):
        raise SupabaseMgmtError(
            "api-keys endpoint did not return a list",
            context={"ref": ref, "type": type(keys).__name__},
        )
    by_name = {item.get("name"): item.get("api_key") for item in keys if isinstance(item, dict)}
    anon = by_name.get("anon")
    service = by_name.get("service_role")
    if not anon or not service:
        raise SupabaseMgmtError(
            "Could not find both anon and service_role keys",
            context={"ref": ref, "names": list(by_name.keys())},
        )
    return anon, service


async def wait_until_ready(
    ref: str,
    *,
    timeout_seconds: float = 180.0,
    poll_interval: float = 5.0,
) -> None:
    """Poll the project status until it reports ACTIVE_HEALTHY (or equiv).

    A freshly provisioned project's API gateway is usually live within
    ~10s, but the Postgres instance can take 30-90s before SQL works.
    Migrations should not be applied until this returns successfully.
    """
    if not _REF_RE.match(ref):
        raise SupabaseMgmtError(f"Invalid ref {ref!r}", context={"ref": ref})

    deadline = asyncio.get_event_loop().time() + timeout_seconds
    last_status: str | None = None

    async with httpx.AsyncClient(timeout=20.0) as client:
        while asyncio.get_event_loop().time() < deadline:
            try:
                proj = await _request(client, "GET", f"/v1/projects/{ref}", expected=(200,))
            except SupabaseMgmtError as exc:
                # 4xx during the very first seconds is normal (project
                # row not yet visible); keep polling until deadline.
                if exc.context.get("status") in (404, 425):
                    await asyncio.sleep(poll_interval)
                    continue
                raise

            status = (proj or {}).get("status") if isinstance(proj, dict) else None
            if status != last_status:
                logger.info("Project %s status=%s", ref, status)
                last_status = status
            if status in ("ACTIVE_HEALTHY", "ACTIVE", "HEALTHY"):
                return
            await asyncio.sleep(poll_interval)

    raise SupabaseMgmtError(
        f"Project {ref} did not become ready within {timeout_seconds}s "
        f"(last status: {last_status})",
        context={"ref": ref, "last_status": last_status},
    )


async def delete_project(ref: str) -> None:
    """Permanently delete a project. Used for test cleanup and for cleaning
    up failed provisions. Does NOT prompt — caller is responsible for
    confirmation. Raises if the ref doesn't belong to the configured org."""
    if not _REF_RE.match(ref):
        raise SupabaseMgmtError(f"Invalid ref {ref!r}", context={"ref": ref})
    async with httpx.AsyncClient(timeout=30.0) as client:
        await _request(client, "DELETE", f"/v1/projects/{ref}", expected=(200, 204))
    logger.info("Deleted Supabase project ref=%s", ref)


async def list_projects() -> list[dict[str, Any]]:
    """List all projects under the auth token's accessible orgs. Used by
    the test cleanup script to detect orphan test projects."""
    async with httpx.AsyncClient(timeout=20.0) as client:
        result = await _request(client, "GET", "/v1/projects", expected=(200,))
    return result if isinstance(result, list) else []
