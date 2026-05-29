"""VCS — token encryption and integration CRUD.

Encryption matches the frontend's crypto.js exactly:
  key  = SHA-256(ENCRYPTION_KEY)  → 32 bytes
  iv   = random 16 bytes
  mode = AES-256-CBC + PKCS7 padding
  wire = (accessTokenEncrypted: hex, iv: hex) stored separately in DB
"""

from __future__ import annotations

import hashlib
import os
from typing import Optional

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding as crypto_padding
from fastapi import HTTPException
from postgrest.exceptions import APIError

from app.config import logger, settings
from app.supabase_client import db_client


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

_GITLAB_URL_SEPARATOR = "||"


async def upsert_integration(
    *,
    user_id: str,
    provider: str,          # "GITHUB" | "GITLAB"
    token: str,
    user_jwt: str | None,
    label: Optional[str] = None,
    external_username: Optional[str] = None,
    scopes: Optional[str] = None,
) -> str:
    """Encrypt and upsert an integration atomically. Returns the row id."""
    encrypted_hex, iv_hex = encrypt_token(token)

    try:
        async with db_client(user_jwt) as client:
            result = await (
                client.table("integrations")
                .upsert(
                    {
                        "user_id": user_id,
                        "provider": provider,
                        "label": label,
                        "access_token_encrypted": encrypted_hex,
                        "iv": iv_hex,
                        "external_username": external_username,
                        "scopes": scopes,
                    },
                    on_conflict="user_id,provider",
                )
                .execute()
            )
        if result.data:
            return result.data[0].get("id", "")
        return ""
    except APIError as exc:
        logger.error("Supabase error in upsert_integration: code=%s msg=%s", exc.code, exc.message)
        raise HTTPException(status_code=500, detail="Database error") from exc
    except Exception as exc:
        logger.error("Unexpected error in upsert_integration: %s", exc)
        raise HTTPException(status_code=500, detail="Internal server error") from exc


async def get_integration(
    *,
    user_id: str,
    provider: str,
    user_jwt: str | None,
) -> Optional[dict]:
    """Retrieve and decrypt an integration. Returns None if not found.

    For GitLab, the gitlab_url is encoded in the scopes field after
    a ``||`` separator (e.g. ``api,read_repository||https://gitlab.example.com``).
    """
    try:
        async with db_client(user_jwt) as client:
            result = (
                await client.table("integrations")
                .select("id,provider,label,access_token_encrypted,iv,external_username,scopes,created_at")
                .eq("user_id", user_id)
                .eq("provider", provider)
                .maybe_single()
                .execute()
            )
    except APIError as exc:
        logger.error("Supabase error in get_integration: code=%s msg=%s", exc.code, exc.message)
        raise HTTPException(status_code=500, detail="Database error") from exc
    except Exception as exc:
        logger.error("Unexpected error in get_integration: %s", exc)
        raise HTTPException(status_code=500, detail="Internal server error") from exc

    # ``maybe_single()`` may return None (no row) or an object with .data = None
    row = getattr(result, "data", None) if result is not None else None
    if not row:
        return None

    token = decrypt_token(row["access_token_encrypted"], row["iv"])

    raw_scopes = row.get("scopes") or ""
    gitlab_url = "https://gitlab.com"
    scopes = raw_scopes
    if _GITLAB_URL_SEPARATOR in raw_scopes:
        scopes, gitlab_url = raw_scopes.split(_GITLAB_URL_SEPARATOR, 1)

    return {
        "id": row["id"],
        "provider": row["provider"],
        "label": row.get("label"),
        "token": token,
        "externalUsername": row.get("external_username"),
        "scopes": scopes,
        "gitlabUrl": gitlab_url,
        "createdAt": row.get("created_at"),
    }


async def delete_integration(
    *,
    user_id: str,
    provider: str,
    user_jwt: str | None,
) -> bool:
    """Delete an integration. Returns True if a row was removed."""
    try:
        async with db_client(user_jwt) as client:
            result = (
                await client.table("integrations")
                .delete()
                .eq("user_id", user_id)
                .eq("provider", provider)
                .select("id")
                .execute()
            )
        return bool(result.data)
    except APIError as exc:
        logger.error("Supabase error in delete_integration: code=%s msg=%s", exc.code, exc.message)
        raise HTTPException(status_code=500, detail="Database error") from exc
    except Exception as exc:
        logger.error("Unexpected error in delete_integration: %s", exc)
        raise HTTPException(status_code=500, detail="Internal server error") from exc


async def list_integrations(
    *,
    user_id: str,
    user_jwt: str | None,
) -> list[dict]:
    """List all integrations for a user (token is never returned)."""
    try:
        async with db_client(user_jwt) as client:
            result = (
                await client.table("integrations")
                .select("id,provider,label,external_username,scopes,created_at")
                .eq("user_id", user_id)
                .order("created_at")
                .execute()
            )
        rows = result.data or []
    except APIError as exc:
        logger.error("Supabase error in list_integrations: code=%s msg=%s", exc.code, exc.message)
        raise HTTPException(status_code=500, detail="Database error") from exc
    except Exception as exc:
        logger.error("Unexpected error in list_integrations: %s", exc)
        raise HTTPException(status_code=500, detail="Internal server error") from exc

    out: list[dict] = []
    for r in rows:
        raw_scopes = r.get("scopes") or ""
        scopes = raw_scopes
        gitlab_url = "https://gitlab.com"
        if _GITLAB_URL_SEPARATOR in raw_scopes:
            scopes, gitlab_url = raw_scopes.split(_GITLAB_URL_SEPARATOR, 1)
        out.append({
            "id": r["id"],
            "provider": r["provider"].lower(),
            "label": r.get("label"),
            "externalUsername": r.get("external_username"),
            "scopes": scopes,
            "gitlabUrl": gitlab_url if r.get("provider") == "GITLAB" else None,
            "createdAt": r.get("created_at"),
            "connected": True,
        })
    return out
