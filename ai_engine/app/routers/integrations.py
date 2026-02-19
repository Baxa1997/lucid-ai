"""REST endpoints for git provider integrations (PAT management + repo listing + PR creation).

B1  POST   /api/v1/integrations                  — save / update a PAT
    GET    /api/v1/integrations                  — list connected providers (tokens masked)
    DELETE /api/v1/integrations/{provider}       — disconnect a provider
B2  GET    /api/v1/integrations/{provider}/repos — list repos via stored PAT
B3  POST   /api/v1/integrations/{provider}/pr    — open PR / MR after agent pushes
"""

from __future__ import annotations

from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.auth import AuthenticatedUser, get_current_user
from app.config import logger
from app.database import async_session
from app.services.integrations import (
    delete_integration,
    get_integration,
    github_create_pr,
    github_get_user,
    github_list_repos,
    gitlab_create_mr,
    gitlab_get_user,
    gitlab_list_repos,
    list_integrations,
    upsert_integration,
)

router = APIRouter(prefix="/api/v1/integrations", tags=["integrations"])


# ── Request models ───────────────────────────────────────────────────────

class SaveTokenRequest(BaseModel):
    provider: str           # "github" | "gitlab"
    token: str
    gitlabUrl: Optional[str] = "https://gitlab.com"


class CreatePRRequest(BaseModel):
    repoUrl: str            # e.g. "https://github.com/owner/repo"
    branch: str             # source branch the agent pushed to
    baseBranch: str = "main"
    title: str
    body: str = ""
    gitlabUrl: Optional[str] = "https://gitlab.com"


# ── Helper ───────────────────────────────────────────────────────────────

def _normalize_provider(provider: str) -> str:
    """Normalize to uppercase DB enum value, or raise 400."""
    p = provider.strip().upper()
    if p not in ("GITHUB", "GITLAB"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported provider '{provider}'. Use 'github' or 'gitlab'.",
        )
    return p


# ── B1: Save / update PAT ────────────────────────────────────────────────

@router.post("")
async def save_integration(
    payload: SaveTokenRequest,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Save (or update) a GitHub/GitLab PAT for the authenticated user.

    Validates the token against the provider's API before storing.
    Returns the connected username on success.
    """
    provider = _normalize_provider(payload.provider)

    # Validate token — also fetches the username for display purposes
    try:
        if provider == "GITHUB":
            user_info = await github_get_user(payload.token)
            external_username = user_info.get("login")
            scopes = "repo,workflow"
        else:
            gitlab_url = (payload.gitlabUrl or "https://gitlab.com").rstrip("/")
            user_info = await gitlab_get_user(payload.token, gitlab_url=gitlab_url)
            external_username = user_info.get("username")
            # Encode the custom GitLab URL into scopes using "||" as separator.
            # This avoids needing a schema change while ensuring list_repos
            # and PR creation always hit the correct self-hosted instance.
            base_scopes = "api,read_repository,write_repository"
            scopes = f"{base_scopes}||{gitlab_url}" if gitlab_url != "https://gitlab.com" else base_scopes
    except Exception as exc:
        logger.warning("Token validation failed for %s: %s", provider, exc)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Token validation failed — check that the token is valid: {exc}",
        )

    async with async_session() as db:
        await upsert_integration(
            db,
            user_id=user.user_id,
            provider=provider,
            token=payload.token,
            label=f"{external_username} ({provider.lower()})",
            external_username=external_username,
            scopes=scopes,
        )

    logger.info("Integration %s saved for user %s (%s)", provider, user.user_id, external_username)
    return {
        "status": "connected",
        "provider": provider.lower(),
        "username": external_username,
        "message": f"Connected as {external_username}",
    }


# ── List connected integrations ──────────────────────────────────────────

@router.get("")
async def get_integrations(user: AuthenticatedUser = Depends(get_current_user)):
    """List the user's connected integrations. Tokens are never returned."""
    async with async_session() as db:
        rows = await list_integrations(db, user_id=user.user_id)
    return {"integrations": rows}


# ── Disconnect a provider ────────────────────────────────────────────────

@router.delete("/{provider}")
async def remove_integration(
    provider: str,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Remove a GitHub or GitLab integration for the current user."""
    normalized = _normalize_provider(provider)
    async with async_session() as db:
        deleted = await delete_integration(db, user_id=user.user_id, provider=normalized)

    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No {provider} integration found for this user.",
        )
    return {"status": "disconnected", "provider": provider.lower()}


# ── B2: List repos ───────────────────────────────────────────────────────

@router.get("/{provider}/repos")
async def list_repos(
    provider: str,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Return repos accessible via the user's stored PAT."""
    normalized = _normalize_provider(provider)

    async with async_session() as db:
        integration = await get_integration(db, user_id=user.user_id, provider=normalized)

    if not integration:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No {provider} token saved. Add one via POST /api/v1/integrations first.",
        )

    try:
        if normalized == "GITHUB":
            repos = await github_list_repos(integration["token"])
        else:
            repos = await gitlab_list_repos(
                integration["token"],
                gitlab_url=integration.get("gitlabUrl", "https://gitlab.com"),
            )
    except Exception as exc:
        logger.warning("Failed to list %s repos for user %s: %s", provider, user.user_id, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to fetch repos from {provider}: {exc}",
        )

    return {"provider": provider.lower(), "repos": repos, "count": len(repos)}


# ── B3: Create PR / MR ───────────────────────────────────────────────────

@router.post("/{provider}/pr")
async def create_pr(
    provider: str,
    payload: CreatePRRequest,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Open a GitHub PR or GitLab MR using the user's stored PAT.

    Call this from the frontend once the agent has pushed a branch.
    The frontend passes the repo URL, pushed branch name, and PR details.
    """
    normalized = _normalize_provider(provider)

    async with async_session() as db:
        integration = await get_integration(db, user_id=user.user_id, provider=normalized)

    if not integration:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No {provider} token saved. Add one via POST /api/v1/integrations first.",
        )

    try:
        if normalized == "GITHUB":
            # Parse owner/repo from the URL
            parsed = urlparse(payload.repoUrl)
            parts = parsed.path.strip("/").split("/")
            if len(parts) < 2:
                raise ValueError(f"Cannot parse owner/repo from URL: {payload.repoUrl}")
            owner, repo = parts[0], parts[1]
            if repo.endswith(".git"):
                repo = repo[:-4]

            result = await github_create_pr(
                token=integration["token"],
                owner=owner,
                repo=repo,
                title=payload.title,
                body=payload.body,
                head=payload.branch,
                base=payload.baseBranch,
            )
        else:
            gitlab_url = payload.gitlabUrl or "https://gitlab.com"
            # Build project_id from the URL path relative to the GitLab host
            parsed = urlparse(payload.repoUrl)
            path = parsed.path.strip("/")
            if path.endswith(".git"):
                path = path[:-4]
            # URL-encode the path for the GitLab API
            project_id = path.replace("/", "%2F")

            result = await gitlab_create_mr(
                token=integration["token"],
                project_id=project_id,
                title=payload.title,
                body=payload.body,
                source_branch=payload.branch,
                target_branch=payload.baseBranch,
                gitlab_url=gitlab_url,
            )

    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Failed to create PR for user %s on %s: %s", user.user_id, provider, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to create PR: {exc}",
        )

    logger.info("PR created for user %s on %s: %s", user.user_id, provider, result.get("prUrl"))
    return {"status": "created", "provider": provider.lower(), **result}
