"""vercel.py — minimal Vercel API helper for auto-publishing generated projects.

Used by the pipeline orchestrator's Phase 7 to create a Vercel project linked
to the just-created GitHub repo, so first-generation projects get a live URL
immediately without the user having to click Publish.

Fire-and-forget by design — failures here NEVER block project generation.
"""
from __future__ import annotations

import asyncio
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


def _canonical_alias(project_data: dict) -> str | None:
    """Pick the canonical *.vercel.app hostname Vercel actually serves.

    For projects whose slug exceeds Vercel's hostname budget (~35 chars once
    the team suffix is appended), Vercel truncates the assigned alias —
    so `f"{slug}.vercel.app"` 404s. The real alias lives in
    targets.production.alias. We pick the shortest plain *.vercel.app entry
    (skipping git-branch and team-scoped variants).
    """
    aliases = ((project_data.get("targets") or {}).get("production") or {}).get("alias") or []
    plain = [a for a in aliases if a.endswith(".vercel.app") and "-git-" not in a and "-projects." not in a]
    return min(plain, key=len) if plain else None


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
                # Set installCommand at create time so every deploy uses it,
                # regardless of whether vercel.json made it into the repo.
                # Claude routinely adds deps to package.json without
                # regenerating pnpm-lock.yaml, so Vercel's default
                # `pnpm install --frozen-lockfile` blows up with
                # ERR_PNPM_OUTDATED_LOCKFILE on the first deploy. The
                # `--no-frozen-lockfile` override tolerates the drift.
                create = await client.post(
                    f"{_VERCEL_API}/v10/projects{qs}",
                    headers=headers,
                    json={
                        "name": project_slug,
                        "framework": framework,
                        "installCommand": "pnpm install --no-frozen-lockfile",
                        # Vercel ignores the package.json `packageManager` pin
                        # unless corepack is enabled — without this it uses a
                        # stale bundled pnpm that dies with ERR_INVALID_THIS
                        # (undici) on Node 20+, failing every install.
                        "environmentVariables": [{
                            "key": "ENABLE_EXPERIMENTAL_COREPACK",
                            "value": "1",
                            "type": "plain",
                            "target": ["production", "preview", "development"],
                        }],
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

            # ── 1b. Force install/framework on every deploy (idempotent) ──
            # Pre-existing projects (created before these fixes) can have
            # Vercel's default `pnpm install --frozen-lockfile` AND a missing
            # framework preset (framework=None → Vercel expects a static
            # `public/` dir and fails Next.js builds with "No Output Directory").
            # PATCH both on every publish. Fail-soft: a PATCH error just falls
            # through to whatever the project already has.
            try:
                patch = await client.patch(
                    f"{_VERCEL_API}/v9/projects/{project_id}{qs}",
                    headers=headers,
                    json={
                        "installCommand": "pnpm install --no-frozen-lockfile",
                        "framework": framework,
                    },
                )
                if patch.status_code not in (200, 201, 204):
                    logger.warning(
                        "Vercel project PATCH non-2xx (%d): %s",
                        patch.status_code, patch.text[:200],
                    )
            except Exception as patch_exc:
                logger.debug("Vercel project PATCH failed (non-fatal): %s", patch_exc)

            # Ensure corepack is enabled so the package.json `packageManager`
            # pin (pnpm@9) is honored — Vercel's bundled pnpm otherwise crashes
            # with ERR_INVALID_THIS (undici) on Node 20+. Upsert is idempotent
            # for pre-existing projects that never had this env var.
            try:
                env_qs = f"{qs}&upsert=true" if qs else "?upsert=true"
                cp = await client.post(
                    f"{_VERCEL_API}/v10/projects/{project_id}/env{env_qs}",
                    headers=headers,
                    json={
                        "key": "ENABLE_EXPERIMENTAL_COREPACK", "value": "1",
                        "type": "plain",
                        "target": ["production", "preview", "development"],
                    },
                )
                if cp.status_code not in (200, 201):
                    logger.debug(
                        "Vercel corepack env upsert non-2xx (%d): %s",
                        cp.status_code, cp.text[:160],
                    )
            except Exception as env_exc:
                logger.debug("Vercel corepack env upsert failed (non-fatal): %s", env_exc)

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

            # ── 2b. Verify the deploy branch actually has a commit ────────
            # Deploying `ref: <branch>` when that branch is empty returns
            # Vercel 400 incorrect_git_source_info and a broken preview. This
            # happens (a) on a draft-only publish — code is on `staging`, `main`
            # is empty — and (b) in the brief window after a push before GitHub
            # propagates the ref. Check the branch (with a short retry for the
            # race); skip cleanly if it has no commit instead of 400-ing.
            gh_token = os.environ.get("PLATFORM_GITHUB_TOKEN", "").strip()
            if gh_token:
                branch_has_commit = False
                for _attempt in range(3):
                    try:
                        br = await client.get(
                            f"https://api.github.com/repos/{owner}/{repo}/branches/{branch}",
                            headers={
                                "Authorization": f"Bearer {gh_token}",
                                "Accept": "application/vnd.github+json",
                            },
                        )
                        if br.status_code == 200 and (br.json().get("commit") or {}).get("sha"):
                            branch_has_commit = True
                            break
                    except Exception as br_exc:
                        logger.debug("Vercel: branch ref check error: %s", br_exc)
                        break
                    if _attempt < 2:
                        await asyncio.sleep(1.5)  # GitHub propagation lag after push
                if not branch_has_commit:
                    logger.warning(
                        "Vercel: branch '%s' has no commit on %s/%s — skipping deploy "
                        "(draft-only publish, or push not yet propagated). User can Republish.",
                        branch, owner, repo,
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
                    "Triggered Vercel deploy for %s (deployment=%s)",
                    project_slug, dep_id,
                )
            else:
                # Project exists, deploy failed — log but still return URL,
                # the user can click "Republish" to retry.
                logger.warning(
                    "Vercel deploy trigger failed (%d): %s",
                    deploy.status_code, deploy.text[:300],
                )

            # Re-fetch the project so we read the assigned alias post-deploy.
            # `_proj` from the create/lookup above might have an empty
            # targets.production.alias for fresh projects; after triggering
            # the deploy Vercel publishes the canonical hostname.
            try:
                refreshed = await client.get(
                    f"{_VERCEL_API}/v9/projects/{project_slug}{qs}",
                    headers=headers,
                )
                if refreshed.status_code == 200:
                    canonical = _canonical_alias(refreshed.json())
                    if canonical:
                        logger.info("Resolved canonical Vercel URL: https://%s", canonical)
                        return f"https://{canonical}"
            except Exception as al_exc:
                logger.debug("Alias refresh failed (using predicted URL): %s", al_exc)

            return predicted_url
    except Exception as exc:
        logger.warning("Vercel API error (non-fatal): %s", exc)
        return None
