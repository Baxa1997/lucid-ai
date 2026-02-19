"""Integration service — PAT storage/retrieval + GitHub/GitLab API calls.

Encryption matches the frontend's crypto.js exactly:
  key  = SHA-256(ENCRYPTION_KEY)  → 32 bytes
  iv   = random 16 bytes
  mode = AES-256-CBC + PKCS7 padding
  wire = (accessTokenEncrypted: hex, iv: hex) stored separately in DB
"""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

import httpx
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding as crypto_padding
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import logger, settings


# ── Encryption ───────────────────────────────────────────────────────────

def _get_aes_key() -> bytes:
    """Return 32-byte AES key: SHA-256(ENCRYPTION_KEY)."""
    if not settings.ENCRYPTION_KEY:
        raise ValueError(
            "ENCRYPTION_KEY is not set. "
            "Set it to the same value as the frontend ENCRYPTION_KEY env var."
        )
    return hashlib.sha256(settings.ENCRYPTION_KEY.encode("utf-8")).digest()


def encrypt_token(plaintext: str) -> tuple[str, str]:
    """Encrypt a PAT. Returns (accessTokenEncrypted hex, iv hex)."""
    key = _get_aes_key()
    iv = os.urandom(16)

    padder = crypto_padding.PKCS7(128).padder()
    padded = padder.update(plaintext.encode("utf-8")) + padder.finalize()

    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    enc = cipher.encryptor()
    encrypted = enc.update(padded) + enc.finalize()

    return encrypted.hex(), iv.hex()


def decrypt_token(encrypted_hex: str, iv_hex: str) -> str:
    """Decrypt a stored token. Inverse of frontend decrypt()."""
    key = _get_aes_key()
    iv = bytes.fromhex(iv_hex)
    encrypted = bytes.fromhex(encrypted_hex)

    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    dec = cipher.decryptor()
    padded = dec.update(encrypted) + dec.finalize()

    unpadder = crypto_padding.PKCS7(128).unpadder()
    return (unpadder.update(padded) + unpadder.finalize()).decode("utf-8")


# ── Database helpers ─────────────────────────────────────────────────────
# The integrations table is owned by Prisma; we access it via raw SQL.
# Prisma column names are camelCase in PostgreSQL → must be double-quoted.

async def upsert_integration(
    db: AsyncSession,
    *,
    user_id: str,
    provider: str,          # "GITHUB" | "GITLAB"
    token: str,
    label: Optional[str] = None,
    external_username: Optional[str] = None,
    scopes: Optional[str] = None,
) -> str:
    """Encrypt and upsert an integration. Returns the row id."""
    encrypted_hex, iv_hex = encrypt_token(token)
    now = datetime.now(timezone.utc)

    result = await db.execute(
        text('SELECT id FROM integrations WHERE "userId" = :uid AND provider::text = :prov'),
        {"uid": user_id, "prov": provider},
    )
    row = result.fetchone()

    if row:
        await db.execute(
            text("""
                UPDATE integrations
                SET "accessTokenEncrypted" = :enc,
                    iv = :iv,
                    label = :label,
                    "externalUsername" = :username,
                    scopes = :scopes,
                    "updatedAt" = :now
                WHERE "userId" = :uid AND provider::text = :prov
            """),
            {
                "enc": encrypted_hex, "iv": iv_hex,
                "label": label, "username": external_username,
                "scopes": scopes, "now": now,
                "uid": user_id, "prov": provider,
            },
        )
        integration_id = row[0]
    else:
        integration_id = str(uuid.uuid4())
        await db.execute(
            text("""
                INSERT INTO integrations
                    (id, provider, label, "accessTokenEncrypted", iv,
                     "externalUsername", scopes, "userId", "createdAt", "updatedAt")
                VALUES
                    (:id, :prov, :label, :enc, :iv, :username, :scopes, :uid, :now, :now)
            """),
            {
                "id": integration_id, "prov": provider, "label": label,
                "enc": encrypted_hex, "iv": iv_hex,
                "username": external_username, "scopes": scopes,
                "uid": user_id, "now": now,
            },
        )

    await db.commit()
    return integration_id


_GITLAB_URL_SEPARATOR = "||"


async def get_integration(
    db: AsyncSession,
    *,
    user_id: str,
    provider: str,
) -> Optional[dict]:
    """Retrieve and decrypt an integration. Returns None if not found.

    For GitLab, the gitlab_url is encoded in the scopes field after
    a ``||`` separator (e.g. ``api,read_repository||https://gitlab.example.com``).
    """
    result = await db.execute(
        text("""
            SELECT id, provider::text, label, "accessTokenEncrypted", iv,
                   "externalUsername", scopes, "createdAt"
            FROM integrations
            WHERE "userId" = :uid AND provider::text = :prov
        """),
        {"uid": user_id, "prov": provider},
    )
    row = result.fetchone()
    if not row:
        return None

    token = decrypt_token(row[3], row[4])

    # Parse gitlab_url from the scopes field if present
    raw_scopes = row[6] or ""
    gitlab_url = "https://gitlab.com"
    scopes = raw_scopes
    if _GITLAB_URL_SEPARATOR in raw_scopes:
        scopes, gitlab_url = raw_scopes.split(_GITLAB_URL_SEPARATOR, 1)

    return {
        "id": row[0],
        "provider": row[1],
        "label": row[2],
        "token": token,
        "externalUsername": row[5],
        "scopes": scopes,
        "gitlabUrl": gitlab_url,
        "createdAt": row[7],
    }


async def delete_integration(
    db: AsyncSession,
    *,
    user_id: str,
    provider: str,
) -> bool:
    """Delete an integration. Returns True if a row was removed."""
    result = await db.execute(
        text('DELETE FROM integrations WHERE "userId" = :uid AND provider::text = :prov'),
        {"uid": user_id, "prov": provider},
    )
    await db.commit()
    return result.rowcount > 0


async def list_integrations(
    db: AsyncSession,
    *,
    user_id: str,
) -> list[dict]:
    """List all integrations for a user (token is never returned)."""
    result = await db.execute(
        text("""
            SELECT id, provider::text, label, "externalUsername", scopes, "createdAt"
            FROM integrations
            WHERE "userId" = :uid
            ORDER BY "createdAt"
        """),
        {"uid": user_id},
    )
    return [
        {
            "id": r[0],
            "provider": r[1].lower(),
            "label": r[2],
            "externalUsername": r[3],
            "scopes": r[4],
            "createdAt": r[5].isoformat() if r[5] else None,
            "connected": True,
        }
        for r in result.fetchall()
    ]


# ── GitHub API ───────────────────────────────────────────────────────────

_GH_HEADERS = {"Accept": "application/vnd.github.v3+json", "X-GitHub-Api-Version": "2022-11-28"}


async def github_get_user(token: str) -> dict:
    """Validate token and return the authenticated GitHub user's profile."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://api.github.com/user",
            headers={"Authorization": f"Bearer {token}", **_GH_HEADERS},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()


async def github_list_repos(token: str) -> list[dict]:
    """List all repos accessible by the stored GitHub PAT (paginated)."""
    repos: list[dict] = []
    page = 1
    async with httpx.AsyncClient() as client:
        while True:
            resp = await client.get(
                "https://api.github.com/user/repos",
                params={"sort": "updated", "per_page": 100, "page": page},
                headers={"Authorization": f"Bearer {token}", **_GH_HEADERS},
                timeout=15,
            )
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            repos.extend(
                {
                    "name": r["name"],
                    "fullName": r["full_name"],
                    "url": r["html_url"],
                    "cloneUrl": r["clone_url"],
                    "defaultBranch": r["default_branch"],
                    "private": r["private"],
                    "description": r.get("description"),
                }
                for r in batch
            )
            if len(batch) < 100:
                break
            page += 1
    return repos


async def github_create_pr(
    *,
    token: str,
    owner: str,
    repo: str,
    title: str,
    body: str,
    head: str,
    base: str = "main",
) -> dict:
    """Open a GitHub Pull Request and return the PR metadata."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"https://api.github.com/repos/{owner}/{repo}/pulls",
            json={"title": title, "body": body, "head": head, "base": base},
            headers={"Authorization": f"Bearer {token}", **_GH_HEADERS},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    return {
        "prUrl": data["html_url"],
        "prNumber": data["number"],
        "title": data["title"],
        "state": data["state"],
    }


# ── GitLab API ───────────────────────────────────────────────────────────

async def gitlab_get_user(token: str, *, gitlab_url: str = "https://gitlab.com") -> dict:
    """Validate token and return the authenticated GitLab user's profile."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{gitlab_url}/api/v4/user",
            headers={"PRIVATE-TOKEN": token},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()


async def gitlab_list_repos(token: str, *, gitlab_url: str = "https://gitlab.com") -> list[dict]:
    """List all GitLab projects the user is a member of (paginated)."""
    repos: list[dict] = []
    page = 1
    async with httpx.AsyncClient() as client:
        while True:
            resp = await client.get(
                f"{gitlab_url}/api/v4/projects",
                params={
                    "membership": "true",
                    "order_by": "last_activity_at",
                    "per_page": 100,
                    "page": page,
                },
                headers={"PRIVATE-TOKEN": token},
                timeout=15,
            )
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            repos.extend(
                {
                    "name": r["name"],
                    "fullName": r["path_with_namespace"],
                    "url": r["web_url"],
                    "cloneUrl": r["http_url_to_repo"],
                    "defaultBranch": r.get("default_branch", "main"),
                    "private": r.get("visibility") == "private",
                    "description": r.get("description"),
                }
                for r in batch
            )
            if len(batch) < 100:
                break
            page += 1
    return repos


async def gitlab_create_mr(
    *,
    token: str,
    project_id: str,
    title: str,
    body: str,
    source_branch: str,
    target_branch: str = "main",
    gitlab_url: str = "https://gitlab.com",
) -> dict:
    """Open a GitLab Merge Request and return the MR metadata."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{gitlab_url}/api/v4/projects/{project_id}/merge_requests",
            json={
                "title": title,
                "description": body,
                "source_branch": source_branch,
                "target_branch": target_branch,
            },
            headers={"PRIVATE-TOKEN": token},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    return {
        "prUrl": data["web_url"],
        "prNumber": data["iid"],
        "title": data["title"],
        "state": data["state"],
    }
