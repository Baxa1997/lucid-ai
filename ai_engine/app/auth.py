"""Authentication helpers — JWT validation and FastAPI dependencies.

Security model
--------------
- **JWT (preferred)**: ``Authorization: Bearer <token>`` validated against
  ``SESSION_SECRET``.  This is the only fully trusted auth method.
- **X-User-ID header**: Only trusted when accompanied by a valid
  ``X-Internal-Key`` header matching ``INTERNAL_API_KEY``.  This is the
  server-to-server path (Next.js → ai_engine).  When ``INTERNAL_API_KEY``
  is not configured (dev mode), X-User-ID is accepted with a warning.
- **WebSocket**: JWT via ``?token=`` query param or ``token`` field in the
  first handshake message.  No anonymous fallback — auth is mandatory.
"""

from __future__ import annotations

from typing import Optional

from fastapi import HTTPException, Request, WebSocket, status
from jose import JWTError, jwt

from app.config import logger, settings


class AuthenticatedUser:
    """Lightweight container for the authenticated user's identity."""

    __slots__ = ("user_id", "project_id", "session_id")

    def __init__(self, user_id: str, project_id: str | None = None, session_id: str | None = None):
        self.user_id = user_id
        self.project_id = project_id
        self.session_id = session_id


def decode_jwt(token: str) -> dict:
    """Decode and validate a JWT signed by the frontend (HS256)."""
    return jwt.decode(token, settings.SESSION_SECRET, algorithms=["HS256"])


async def get_current_user(request: Request) -> AuthenticatedUser:
    """FastAPI dependency — extracts authenticated user from the request.

    Resolution order:
    1. ``Authorization: Bearer <JWT>`` header → decode JWT → extract userId
    2. ``X-User-ID`` header → only if ``X-Internal-Key`` matches ``INTERNAL_API_KEY``
    3. Neither → HTTP 401
    """
    # 1. Try Bearer JWT (always preferred)
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        try:
            payload = decode_jwt(token)
            user_id = payload.get("userId") or payload.get("sub")
            if not user_id:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token missing userId")
            return AuthenticatedUser(
                user_id=user_id,
                project_id=payload.get("projectId"),
                session_id=payload.get("sessionId"),
            )
        except JWTError as exc:
            logger.warning("JWT decode failed: %s", exc)
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    # 2. X-User-ID — trusted only with valid internal key
    user_id = request.headers.get("x-user-id")
    if user_id:
        if settings.INTERNAL_API_KEY:
            # Production mode: require matching internal key
            internal_key = request.headers.get("x-internal-key", "")
            if internal_key != settings.INTERNAL_API_KEY:
                logger.warning(
                    "X-User-ID '%s' rejected — invalid or missing X-Internal-Key",
                    user_id,
                )
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid internal API key",
                )
            return AuthenticatedUser(user_id=user_id)
        else:
            # Dev mode: accept but warn
            logger.warning(
                "X-User-ID '%s' accepted WITHOUT internal key validation "
                "(set INTERNAL_API_KEY to enforce)",
                user_id,
            )
            return AuthenticatedUser(user_id=user_id)

    # 3. No credentials
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")


async def authenticate_websocket(websocket: WebSocket) -> Optional[AuthenticatedUser]:
    """Authenticate a WebSocket connection from query param.

    Resolution order:
    1. ``?token=<JWT>`` query parameter → decode → return user
    2. No token → return None (caller should try handshake message)
    3. Invalid token → close with 4010
    """
    token = websocket.query_params.get("token")
    if not token:
        return None

    try:
        payload = decode_jwt(token)
        user_id = payload.get("userId") or payload.get("sub")
        if not user_id:
            await websocket.close(code=4010, reason="Token missing userId")
            return None
        return AuthenticatedUser(
            user_id=user_id,
            project_id=payload.get("projectId"),
            session_id=payload.get("sessionId"),
        )
    except JWTError as exc:
        logger.warning("WebSocket JWT decode failed: %s", exc)
        await websocket.close(code=4010, reason="Invalid token")
        return None


def authenticate_from_handshake(raw: dict) -> Optional[AuthenticatedUser]:
    """Try to authenticate from the WebSocket handshake message.

    Looks for a ``token`` field containing a valid JWT.
    Returns None if no token or invalid token.
    """
    token = raw.get("token")
    if not token:
        return None

    try:
        payload = decode_jwt(token)
        user_id = payload.get("userId") or payload.get("sub")
        if user_id:
            return AuthenticatedUser(
                user_id=user_id,
                project_id=payload.get("projectId"),
                session_id=payload.get("sessionId"),
            )
    except JWTError as exc:
        logger.warning("WebSocket handshake JWT failed: %s", exc)

    return None
