"""REST endpoints for agent session lifecycle."""

from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, status

from app.auth import AuthenticatedUser, get_current_user
from app.config import logger
from app.schemas import InitSessionRequest, InitSessionResponse
from app.exceptions import (
    SessionNotFoundError,
    ProviderError,
    APIKeyMissingError,
)
from app.sdk import OPENHANDS_AVAILABLE
from app.services.sessions import create_session, destroy_session, store
from app.services.vcs.tokens import get_integration

router = APIRouter(prefix="/api/v1/sessions", tags=["sessions"])


def _provider_from_repo_url(repo_url: str, explicit: str = "") -> str:
    provider = (explicit or "").strip().lower()
    if provider in ("github", "gitlab"):
        return provider.upper()
    raw = (repo_url or "").lower()
    host = urlparse(repo_url or "").netloc.lower()
    if "github.com" in host or "github.com" in raw:
        return "GITHUB"
    if "gitlab" in host or "gitlab" in raw:
        return "GITLAB"
    return ""


async def _resolve_git_token(
    *,
    repo_url: str,
    repo_provider: str,
    user_id: str,
    user_jwt: str | None,
) -> str:
    provider = _provider_from_repo_url(repo_url, repo_provider)
    if not provider:
        return ""
    integration = await get_integration(
        user_id=user_id,
        provider=provider,
        user_jwt=user_jwt,
    )
    return (integration or {}).get("token") or ""


@router.post("", response_model=InitSessionResponse)
async def init_session(
    payload: InitSessionRequest,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Create a new agent session."""
    try:
        git_token = payload.gitToken or await _resolve_git_token(
            repo_url=payload.repoUrl or "",
            repo_provider=payload.repoProvider or "",
            user_id=user.user_id,
            user_jwt=user.raw_jwt,
        )
        session = await create_session(
            task=payload.task,
            user_id=user.user_id,
            repo_url=payload.repoUrl,
            repo_provider=payload.repoProvider,
            git_token=git_token,
            branch=payload.branch,
            git_user_name=payload.gitUserName,
            git_user_email=payload.gitUserEmail,
            model_provider=payload.model_provider,
            api_key=payload.api_key,
            project_id=payload.projectId,
        )

        return InitSessionResponse(
            status="mock" if not OPENHANDS_AVAILABLE else "ready",
            sessionId=session.session_id,
            message=(
                "Mock session created — OpenHands SDK not installed. "
                "Install openhands-sdk to enable real agent execution."
            ) if not OPENHANDS_AVAILABLE else (
                "Agent session initialized. Connect via WebSocket to start."
            ),
        )

    except (ProviderError, APIKeyMissingError) as exc:
        logger.error("Session init validation error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"status": "error", "message": str(exc)},
        )
    except Exception as exc:
        logger.error("Session init failed: %s", exc, exc_info=True)
        error_msg = str(exc)
        if "401" in error_msg or "invalid_api_key" in error_msg.lower():
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"status": "error", "message": "Invalid API Key"},
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"status": "error", "message": f"Failed to initialize session: {error_msg}"},
        )


@router.get("")
async def list_sessions(user: AuthenticatedUser = Depends(get_current_user)):
    """List active agent sessions for the authenticated user."""
    sessions = await store.list_all()
    return {
        "sessions": [
            {
                "sessionId": s.session_id,
                "userId": s.user_id,
                "task": s.task[:80],
                "isAlive": s.is_alive,
                "createdAt": s.created_at.isoformat(),
            }
            for s in sessions
            if s.user_id == user.user_id
        ]
    }


@router.delete("/{session_id}")
async def stop_session(
    session_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Stop and destroy an agent session."""
    session = await store.get_or_none(session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {session_id} not found.",
        )

    # Verify the session belongs to the authenticated user
    if session.user_id != user.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to stop this session.",
        )

    await destroy_session(session_id)

    return {
        "status": "stopped",
        "sessionId": session_id,
        "message": "Session stopped and resources cleaned up.",
    }
