"""vercel.py — minimal Vercel API helper for auto-publishing generated projects.

Used by the pipeline orchestrator's Phase 7 to create a Vercel project linked
to the just-created GitHub repo, so first-generation projects get a live URL
immediately without the user having to click Publish.

Fire-and-forget by design — failures here NEVER block project generation.
"""
from __future__ import annotations

import logging
import os
import re

import httpx

logger = logging.getLogger(__name__)

_VERCEL_API = "https://api.vercel.com"


def _slugify_repo(repo: str) -> str:
    """Vercel project names: lowercase, alphanumeric + dashes, ≤100 chars."""
    s = re.sub(r"[^a-z0-9-]+", "-", repo.lower()).strip("-")
    return s[:100] or "lucid-project"


async def create_vercel_project(
    *,
    owner: str,
    repo: str,
    framework: str = "nextjs",
    branch: str = "main",
) -> str | None:
    """Create a Vercel project linked to the GitHub repo + trigger first deploy.

    Returns the predicted production URL (``https://{slug}.vercel.app``) on
    success, or None if VERCEL_TOKEN is not set or the API call fails.

    Idempotent: if a project with the same name already exists, just triggers
    a fresh deployment instead of failing.

    Why we trigger the deploy explicitly: Vercel's GitHub webhook only fires
    on pushes that happen *after* a project is linked. Our orchestrator pushes
    to main first, then links the Vercel project — so Vercel sees no commits
    via the webhook. Without a manual deploy, the URL returns a placeholder.
    """
    token = os.environ.get("VERCEL_TOKEN", "").strip()
    if not token:
        logger.info("VERCEL_TOKEN not set — skipping Vercel project creation")
        return None

    team_id = os.environ.get("VERCEL_TEAM_ID", "").strip()
    project_slug = _slugify_repo(repo)
    predicted_url = f"https://{project_slug}.vercel.app"

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    qs = f"?teamId={team_id}" if team_id else ""

    try:
        # 60s is generous — Vercel API calls themselves are fast (~1s each),
        # but GitHub repo lookups can lag during heavy traffic, and we make
        # several sequential calls. Better to wait than abort mid-flow.
        async with httpx.AsyncClient(timeout=60) as client:
            # ── 1. Create or detect the project ──────────────────
            existing = await client.get(
                f"{_VERCEL_API}/v9/projects/{project_slug}{qs}",
                headers=headers,
            )
            project_id: str | None = None
            repo_id: int | None = None
            if existing.status_code == 200:
                _proj = existing.json()
                project_id = _proj.get("id")
                repo_id = (_proj.get("link") or {}).get("repoId")
                logger.info("Vercel project %s already exists (id=%s)", project_slug, project_id)
            else:
                create = await client.post(
                    f"{_VERCEL_API}/v10/projects{qs}",
                    headers=headers,
                    json={
                        "name": project_slug,
                        "framework": framework,
                        "gitRepository": {
                            "type": "github",
                            "repo": f"{owner}/{repo}",
                        },
                    },
                )
                if create.status_code in (200, 201):
                    _proj = create.json()
                    project_id = _proj.get("id")
                    repo_id = (_proj.get("link") or {}).get("repoId")
                    logger.info("Created Vercel project %s (id=%s)", project_slug, project_id)
                elif create.status_code == 409:
                    # Race — project got created in parallel; fetch its id
                    refetch = await client.get(
                        f"{_VERCEL_API}/v9/projects/{project_slug}{qs}",
                        headers=headers,
                    )
                    if refetch.status_code == 200:
                        _proj = refetch.json()
                        project_id = _proj.get("id")
                        repo_id = (_proj.get("link") or {}).get("repoId")
                else:
                    logger.warning(
                        "Vercel create failed (%d): %s",
                        create.status_code, create.text[:300],
                    )
                    return None

            if not project_id:
                logger.warning("Vercel project_id missing after create — skipping deploy")
                return predicted_url

            # ── 2. Resolve numeric GitHub repo ID for the deploy ──
            # Vercel's POST /v13/deployments requires gitSource.repoId
            # (numeric), not "owner/name". Use the link.repoId when Vercel
            # already has it; otherwise look it up via the GitHub REST API.
            if not repo_id:
                gh_token = os.environ.get("PLATFORM_GITHUB_TOKEN", "").strip()
                if gh_token:
                    try:
                        gh = await client.get(
                            f"https://api.github.com/repos/{owner}/{repo}",
                            headers={
                                "Authorization": f"Bearer {gh_token}",
                                "Accept": "application/vnd.github+json",
                            },
                        )
                        if gh.status_code == 200:
                            repo_id = gh.json().get("id")
                    except Exception as gh_exc:
                        logger.warning("GitHub repo lookup failed: %s", gh_exc)

            if not repo_id:
                logger.warning(
                    "No repoId available for %s/%s — Vercel project linked but deploy not triggered",
                    owner, repo,
                )
                return predicted_url

            # ── 3. Trigger first deployment ───────────────────────
            # Vercel won't auto-build because our push to main happened
            # before the project link. Hit the deployments endpoint directly.
            deploy = await client.post(
                f"{_VERCEL_API}/v13/deployments{qs}",
                headers=headers,
                json={
                    "name": project_slug,
                    "project": project_id,
                    "target": "production",
                    "gitSource": {
                        "type": "github",
                        "ref": branch,
                        "repoId": repo_id,
                    },
                },
            )
            if deploy.status_code in (200, 201, 202):
                dep_id = deploy.json().get("id", "?")
                logger.info(
                    "Triggered Vercel deploy for %s (deployment=%s) → %s",
                    project_slug, dep_id, predicted_url,
                )
            else:
                # Project exists, deploy failed — log but still return URL,
                # the user can click "Republish" to retry.
                logger.warning(
                    "Vercel deploy trigger failed (%d): %s",
                    deploy.status_code, deploy.text[:300],
                )
            return predicted_url
    except Exception as exc:
        logger.warning("Vercel API error (non-fatal): %s", exc)
        return None
