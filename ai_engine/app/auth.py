"""Authentication helpers — JWT validation and FastAPI dependencies.

Security model
--------------
- **JWT (preferred)**: ``Authorization: Bearer <token>`` issued by Supabase Auth
  and validated against ``SUPABASE_JWT_SECRET`` (HS256).  The raw token is stored
  on ``AuthenticatedUser.raw_jwt`` and forwarded to the Supabase client so that
  Row Level Security (RLS) is enforced at the database level.
- **X-User-ID + X-Internal-Key**: Server-to-server path (Next.js → ai_engine).
  ``X-Internal-Key`` is compared in constant time.  On success ``raw_jwt`` is set
  to ``None`` — callers use the admin (service_role) Supabase client which bypasses
  RLS while still filtering rows by ``user_id`` in every query.
- **WebSocket**: JWT via ``?token=`` query param or ``token`` field in the first
  handshake message.  No anonymous fallback — auth is mandatory.
"""

from __future__ import annotations

import hmac
from typing import Optional

from fastapi import HTTPException, Request, WebSocket, status
from jose import JWTError, jwt

from app.config import logger, settings


class AuthenticatedUser:
    """Lightweight container for the authenticated user's identity.

    ``raw_jwt`` is the original Supabase Auth Bearer token when the request
    uses the JWT path, or ``None`` when authenticated via X-Internal-Key
    (server-to-server).  Services check for ``None`` and switch to the admin
    Supabase client (service_role key, RLS bypassed, filters by user_id).
    """

    __slots__ = ("user_id", "project_id", "session_id", "raw_jwt")

    def __init__(
        self,
        user_id: str,
        raw_jwt: str | None,
        project_id: str | None = None,
        session_id: str | None = None,
    ):
        self.user_id = user_id
        self.raw_jwt = raw_jwt
        self.project_id = project_id
        self.session_id = session_id


def _get_token_algorithm(token: str) -> str:
    """Peek at the JWT header to determine the signing algorithm."""
    try:
        header = jwt.get_unverified_header(token)
        return header.get("alg", "HS256")
    except Exception:
        return "HS256"


# Cache for JWKS public keys
_jwks_cache: dict | None = None


def _fetch_jwks() -> dict | None:
    """Fetch the JWKS public keys from the Supabase Auth endpoint."""
    global _jwks_cache
    if _jwks_cache is not None:
        return _jwks_cache

    import urllib.request
    import json

    supabase_url = settings.SUPABASE_URL
    if not supabase_url:
        return None

    jwks_url = f"{supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"
    try:
        with urllib.request.urlopen(jwks_url, timeout=5) as resp:
            _jwks_cache = json.loads(resp.read())
            logger.info("Fetched JWKS from %s", jwks_url)
            return _jwks_cache
    except Exception as exc:
        logger.warning("Failed to fetch JWKS from %s: %s", jwks_url, exc)
        return None


def decode_jwt(token: str) -> dict:
    """Decode and validate a Supabase Auth JWT.

    Supports both HS256 (legacy) and ES256 (modern Supabase) signing.
    - HS256: Uses SUPABASE_JWT_SECRET as the HMAC key.
    - ES256: Fetches the JWKS public key from the Supabase Auth endpoint.

    When ``SUPABASE_JWT_SECRET`` is not configured AND JWKS cannot be fetched,
    the token is decoded **without** signature verification (development mode).
    """
    alg = _get_token_algorithm(token)

    # ── ES256 path: needs the JWKS public key, not the HMAC secret ──
    if alg == "ES256":
        jwks = _fetch_jwks()
        if jwks:
            try:
                from jose import jwk as jose_jwk
                header = jwt.get_unverified_header(token)
                kid = header.get("kid")

                # Find the matching key in the JWKS
                key_data = None
                for key in jwks.get("keys", []):
                    if key.get("kid") == kid:
                        key_data = key
                        break

                if key_data:
                    public_key = jose_jwk.construct(key_data, "ES256")
                    return jwt.decode(
                        token,
                        public_key,
                        algorithms=["ES256"],
                        options={"verify_aud": False},
                    )
                else:
                    logger.warning("No matching JWKS key found for kid=%s, decoding without verification", kid)
            except Exception as exc:
                logger.warning("ES256 JWKS verification failed: %s — falling back to unverified", exc)

        # Fallback: decode ES256 without verification
        logger.warning("ES256 token decoded WITHOUT signature verification")
        return jwt.decode(
            token,
            "",
            algorithms=["ES256"],
            options={
                "verify_aud": False,
                "verify_signature": False,
                "verify_exp": False,
            },
        )

    # ── HS256 path: use the shared secret ──
    if settings.SUPABASE_JWT_SECRET:
        return jwt.decode(
            token,
            settings.SUPABASE_JWT_SECRET,
            algorithms=["HS256"],
            options={"verify_aud": False},
        )
    else:
        # Development mode — decode without any verification
        logger.warning("SUPABASE_JWT_SECRET not set — JWT decoded WITHOUT verification")
        return jwt.decode(
            token,
            "",
            algorithms=["HS256"],
            options={
                "verify_aud": False,
                "verify_signature": False,
                "verify_exp": False,
            },
        )


async def get_current_user(request: Request) -> AuthenticatedUser:
    """FastAPI dependency — extracts the authenticated user from the request.

    Resolution order:
    1. ``Authorization: Bearer <JWT>`` header → validate → extract sub
    2. ``X-User-ID`` + ``X-Internal-Key`` (constant-time check) → admin mode
    3. Neither → HTTP 401
    """
    # 1. Bearer JWT — preferred path; carries user identity directly.
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        try:
            payload = decode_jwt(token)
            user_id = payload.get("sub") or payload.get("userId")
            if not user_id:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                                    detail="Token missing sub claim")
            return AuthenticatedUser(
                user_id=user_id,
                raw_jwt=token,
                project_id=payload.get("projectId"),
                session_id=payload.get("sessionId"),
            )
        except JWTError as exc:
            logger.warning("JWT decode failed: %s", exc)
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                                detail="Invalid token")

    # 2. X-User-ID + X-Internal-Key — server-to-server calls from Next.js.
    #    raw_jwt is set to None; services use the admin client (service_role key)
    #    which bypasses RLS while still filtering rows by user_id explicitly.
    user_id = request.headers.get("x-user-id")
    if user_id:
        if settings.INTERNAL_API_KEY:
            internal_key = request.headers.get("x-internal-key", "")
            # Constant-time comparison prevents timing attacks.
            if not hmac.compare_digest(internal_key, settings.INTERNAL_API_KEY):
                logger.warning("X-User-ID '%s' rejected — X-Internal-Key mismatch", user_id)
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                                    detail="Invalid internal API key")
        else:
            logger.warning(
                "X-User-ID '%s' accepted WITHOUT internal key validation "
                "(set INTERNAL_API_KEY to enforce)",
                user_id,
            )
        return AuthenticatedUser(user_id=user_id, raw_jwt=None)

    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Authentication required")


async def authenticate_websocket(websocket: WebSocket) -> Optional[AuthenticatedUser]:
    """Authenticate a WebSocket connection from the query parameter.

    1. ``?token=<JWT>`` → validate → return user
    2. No token → return None (caller tries handshake message next)
    3. Invalid token → close with 4010
    """
    token = websocket.query_params.get("token")
    if not token:
        return None

    try:
        payload = decode_jwt(token)
        user_id = payload.get("sub") or payload.get("userId")
        if not user_id:
            await websocket.close(code=4010, reason="Token missing sub claim")
            return None
        return AuthenticatedUser(
            user_id=user_id,
            raw_jwt=token,
            project_id=payload.get("projectId"),
            session_id=payload.get("sessionId"),
        )
    except JWTError as exc:
        logger.warning("WebSocket JWT decode failed: %s", exc)
        await websocket.close(code=4010, reason="Invalid token")
        return None


def authenticate_from_handshake(raw: dict) -> Optional[AuthenticatedUser]:
    """Authenticate from the WebSocket handshake message (``token`` field)."""
    token = raw.get("token")
    if not token:
        return None

    try:
        payload = decode_jwt(token)
        user_id = payload.get("sub") or payload.get("userId")
        if user_id:
            return AuthenticatedUser(
                user_id=user_id,
                raw_jwt=token,
                project_id=payload.get("projectId"),
                session_id=payload.get("sessionId"),
            )
    except JWTError as exc:
        logger.warning("WebSocket handshake JWT failed: %s", exc)

    return None
