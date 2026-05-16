"""publish.py — Bootstrap-publish endpoint for IMPORTED projects.

Handles the case where a user imported their own GitHub repo (or otherwise
has a workspace with no platform repo yet) and clicks "Publish". The flow:

  1. Read the local workspace
  2. Create a brand-new private repo on the Lucid platform GitHub org
  3. git init / commit if needed, then push to BOTH ``staging`` and ``main``
  4. Update repo visibility (private/public) per the request
  5. Trigger Vercel project creation linked to the GitHub repo
  6. Persist platform_repo_url + vercel_url to chat_sessions
  7. Insert a project_deployments row so the project counts toward tier limits

Generated projects (where Lucid created the repo on first generation) skip
this endpoint — the frontend handles their republishes directly via the
GitHub API. This endpoint is the bridge for *imported* repos only.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess
import shutil
from datetime import datetime

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.auth import AuthenticatedUser, get_current_user
from app.config import settings
from app.paths import preview_workspace_path
from app.services.pipeline.constants import PLATFORM_GITHUB_TOKEN, _PLATFORM_ORG
from app.services.vcs.github import _create_github_repo, _sanitize_repo_name
from app.services.vcs.git import clone_repo
from app.services.vcs.tokens import get_integration
from app.services.vercel import create_vercel_project
from app.supabase_client import db_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/projects", tags=["publish"])


class BootstrapPublishBody(BaseModel):
    visibility: str = "private"  # "private" | "public"


@router.post("/{project_id}/bootstrap-publish")
async def bootstrap_publish(
    project_id: str,
    body: BootstrapPublishBody,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """First-time publish for an imported project.

    Creates the platform repo, pushes the local workspace, and triggers
    Vercel — all in one shot. Idempotent on repo creation collisions.
    """
    if not PLATFORM_GITHUB_TOKEN:
        raise HTTPException(500, "PLATFORM_GITHUB_TOKEN not configured")

    # ── Resolve workspace path ───────────────────────────────────────
    # New-flow projects (landing pipeline + generated projects) live under
    # PREVIEW_WS_ROOT keyed by project_id (e.g. /app/storage/preview_ws/
    # lucid_ws_<short>). Imported-repo projects live under WORKSPACE_BASE_PATH/
    # <user>/<project>. Try the preview path first since it's where every
    # platform-generated project lands; fall back to the imported layout
    # only if the preview path is empty.
    candidate_paths = [
        preview_workspace_path(project_id),
        os.path.join(settings.WORKSPACE_BASE_PATH, user.user_id, project_id),
    ]
    workspace_path = next((p for p in candidate_paths if os.path.isdir(p)), candidate_paths[-1])
    logger.info("bootstrap_publish: workspace_path=%s (project_id=%s)", workspace_path, project_id)

    # ── Load existing chat_session (might already have platform_repo_url) ──
    # Try lookup by project_id first (canonical key). Fall back to id=<project_id>
    # because the new landing flow uses chat_session_id as project_id when the
    # frontend didn't supply one — same UUID, just stored on a different column.
    async with db_client(user.raw_jwt) as sb:
        sess_res = await (
            sb.table("chat_sessions")
            .select("id,platform_repo_url,vercel_url,user_repo_url,user_repo_provider")
            .eq("user_id", user.user_id)
            .eq("project_id", project_id)
            .maybe_single()
            .execute()
        )
        session_row = (sess_res.data if sess_res else None) or {}
        if not session_row:
            sess_res = await (
                sb.table("chat_sessions")
                .select("id,platform_repo_url,vercel_url,user_repo_url,user_repo_provider")
                .eq("user_id", user.user_id)
                .eq("id", project_id)
                .maybe_single()
                .execute()
            )
            session_row = (sess_res.data if sess_res else None) or {}

    # ── Clone-on-demand for imported projects with no workspace yet ──
    # Imported projects only get cloned when the user first triggers a chat
    # task. If they import + immediately publish, the workspace doesn't exist
    # on disk yet — fetch the source repo here so publish still works.
    if not os.path.isdir(workspace_path):
        user_repo_url = session_row.get("user_repo_url")
        user_repo_provider = (session_row.get("user_repo_provider") or "github").lower()
        if not user_repo_url:
            raise HTTPException(
                400,
                "This project has no source code yet. Open it in chat and "
                "describe what you want to build, then publish.",
            )

        # Token lookup mirrors what the preview path does — try JWT
        # user_metadata first (where Supabase Auth stores OAuth provider
        # tokens), then fall back to the integrations table (where users who
        # paste a PAT manually end up). Either source is enough to clone.
        user_token = ""
        if user.raw_jwt:
            try:
                import jwt as pyjwt
                decoded = pyjwt.decode(user.raw_jwt, options={"verify_signature": False})
                user_meta = decoded.get("user_metadata", {}) or {}
                meta_key = f"{user_repo_provider}_integration"
                user_token = (user_meta.get(meta_key) or {}).get("token", "") or ""
            except Exception as exc:
                logger.debug("Could not decode JWT for %s token: %s", user_repo_provider, exc)

        if not user_token:
            provider_key = "GITHUB" if user_repo_provider == "github" else "GITLAB"
            integration = await get_integration(
                user_id=user.user_id,
                provider=provider_key,
                user_jwt=user.raw_jwt,
            )
            if integration:
                user_token = integration.get("token") or ""

        if not user_token:
            raise HTTPException(
                400,
                f"Reconnect your {user_repo_provider.title()} account in Settings, "
                "then try again.",
            )

        # Try main first, fall back to master (legacy default branch).
        last_exc: Exception | None = None
        cloned = False
        for branch in ("main", "master"):
            try:
                await clone_repo(
                    repo_url=user_repo_url,
                    token=user_token,
                    branch=branch,
                    workspace_dir=workspace_path,
                )
                cloned = True
                break
            except PermissionError:
                raise HTTPException(
                    403,
                    f"Could not access your repository — your {user_repo_provider.title()} "
                    "token may have expired. Reconnect it in Settings.",
                )
            except RuntimeError as exc:
                last_exc = exc
                msg = str(exc).lower()
                if "remote branch" in msg or "couldn't find" in msg or "not found" in msg:
                    if os.path.isdir(workspace_path):
                        shutil.rmtree(workspace_path)
                    continue
                logger.error("Clone-on-demand failed: %s", exc, exc_info=True)
                raise HTTPException(502, f"Could not import your code: {exc}")
            except Exception as exc:
                logger.error("Clone-on-demand failed: %s", exc, exc_info=True)
                raise HTTPException(502, f"Could not import your code: {exc}")
        if not cloned:
            raise HTTPException(
                502,
                f"Could not find a main or master branch in your repository. "
                f"Detail: {last_exc}",
            )

    # Skip empty workspaces (no point creating a repo from nothing)
    has_content = any(
        f for f in os.listdir(workspace_path)
        if not f.startswith(".") and f != "node_modules"
    )
    if not has_content:
        raise HTTPException(400, "Project is empty — nothing to publish yet.")

    # If there's already a platform_repo_url, this is the wrong endpoint —
    # the frontend should be using the staging→main flow instead.
    if session_row.get("platform_repo_url"):
        return {
            "ok": True,
            "alreadyBootstrapped": True,
            "repoUrl": session_row["platform_repo_url"],
            "vercelUrl": session_row.get("vercel_url"),
            "message": "Project is already on the platform — use the regular publish flow.",
        }

    chat_session_id = session_row.get("id")

    # ── Derive a repo name from the imported URL or a fallback ──
    base_name = ""
    if session_row.get("user_repo_url"):
        m = re.search(r"/([^/]+?)(?:\.git)?/?$", session_row["user_repo_url"])
        if m:
            base_name = m.group(1)
    base_name = base_name or f"imported-{project_id[:8]}"
    ts = datetime.utcnow().strftime("%m%d%H%M")
    repo_name = _sanitize_repo_name(f"{base_name}-{ts}", max_len=60)

    # ── 1. Create the platform GitHub repo ──────────────────────
    try:
        repo_data = await _create_github_repo(
            repo_name=repo_name,
            token=PLATFORM_GITHUB_TOKEN,
            description=f"Imported via Lucid AI on {datetime.utcnow():%Y-%m-%d}",
        )
    except RuntimeError as exc:
        msg = str(exc)
        if "422" in msg and "already exists" in msg.lower():
            # Retry once with a longer suffix
            repo_name = _sanitize_repo_name(
                f"{base_name}-{ts}-{os.urandom(2).hex()}", max_len=60,
            )
            repo_data = await _create_github_repo(
                repo_name=repo_name,
                token=PLATFORM_GITHUB_TOKEN,
                description=f"Imported via Lucid AI on {datetime.utcnow():%Y-%m-%d}",
            )
        else:
            raise HTTPException(502, f"GitHub repo creation failed: {msg[:200]}")

    repo_html_url = repo_data.get("html_url", "")
    repo_clone_url = repo_data.get("clone_url", "").replace(
        "https://github.com/", f"https://{PLATFORM_GITHUB_TOKEN}@github.com/",
    )
    repo_full_name = repo_data.get("full_name", f"{_PLATFORM_ORG}/{repo_name}")

    # ── 2. Update visibility (the create call defaults to private) ──
    if body.visibility == "public":
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.patch(
                    f"https://api.github.com/repos/{repo_full_name}",
                    headers={
                        "Authorization": f"Bearer {PLATFORM_GITHUB_TOKEN}",
                        "Accept": "application/vnd.github+json",
                    },
                    json={"private": False},
                )
        except Exception as exc:
            logger.warning("Visibility update failed (non-fatal): %s", exc)

    # ── 3. Push local workspace to staging + main ───────────────
    # Re-init git so we don't carry over the source repo's remotes/history.
    git_dir = os.path.join(workspace_path, ".git")
    if os.path.isdir(git_dir):
        shutil.rmtree(git_dir)

    def _run(cmd, timeout=30):
        return subprocess.run(
            cmd, cwd=workspace_path, capture_output=True, text=True, timeout=timeout,
        )

    try:
        await asyncio.to_thread(_run, ["git", "init", "-b", "staging"], 10)
        await asyncio.to_thread(_run, ["git", "config", "user.name", "Lucid AI"], 5)
        await asyncio.to_thread(_run, ["git", "config", "user.email", "ai@lucid.dev"], 5)
        await asyncio.to_thread(_run, ["git", "remote", "add", "origin", repo_clone_url], 5)
        await asyncio.to_thread(_run, ["git", "add", "-A"], 60)

        commit = await asyncio.to_thread(
            _run, ["git", "commit", "-m", "Initial publish via Lucid AI"], 30,
        )
        if commit.returncode != 0 and "nothing to commit" not in (commit.stderr + commit.stdout).lower():
            logger.warning("Initial commit warning: %s", (commit.stderr or commit.stdout)[:200])

        push_staging = await asyncio.to_thread(
            _run, ["git", "push", "-u", "origin", "staging"], 180,
        )
        if push_staging.returncode != 0:
            err = (push_staging.stderr or push_staging.stdout or "")
            err = err.replace(PLATFORM_GITHUB_TOKEN, "***")
            raise HTTPException(502, f"Push to repo failed: {err[:200]}")

        # Mirror staging → main so Vercel has something to deploy.
        push_main = await asyncio.to_thread(
            _run, ["git", "push", "origin", "staging:main"], 60,
        )
        if push_main.returncode != 0:
            err = (push_main.stderr or push_main.stdout or "")
            err = err.replace(PLATFORM_GITHUB_TOKEN, "***")
            logger.warning("Push to main failed (non-fatal): %s", err[:200])
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(504, f"Git operation timed out: {exc}")

    # ── 4. Create Vercel project (fire-and-forget, won't fail publish) ──
    vercel_url = None
    try:
        vercel_url = await create_vercel_project(
            owner=_PLATFORM_ORG, repo=repo_name,
        )
    except Exception as exc:
        logger.warning("Vercel auto-create failed (non-fatal): %s", exc)

    # ── 5. Persist to DB ────────────────────────────────────────
    update_payload = {
        "platform_repo_url":    repo_html_url,
        "platform_repo_branch": "staging",
    }
    if vercel_url:
        update_payload["vercel_url"] = vercel_url

    try:
        async with db_client(user.raw_jwt) as sb:
            if chat_session_id:
                await (
                    sb.table("chat_sessions")
                    .update(update_payload)
                    .eq("id", chat_session_id)
                    .execute()
                )
            else:
                # No chat_session row yet — match by (user_id, project_id)
                await (
                    sb.table("chat_sessions")
                    .update(update_payload)
                    .eq("user_id", user.user_id)
                    .eq("project_id", project_id)
                    .execute()
                )

            await (
                sb.table("project_deployments")
                .upsert(
                    {
                        "user_id":       user.user_id,
                        "project_id":    project_id,
                        "repo_url":      repo_html_url,
                        "deploy_url":    vercel_url,
                        "deploy_method": "vercel",
                        "status":        "deployed",
                        "deployed_at":   datetime.utcnow().isoformat(),
                    },
                    on_conflict="user_id,project_id",
                )
                .execute()
            )
    except Exception as exc:
        logger.warning("DB write after publish failed (non-fatal): %s", exc)

    return {
        "ok": True,
        "repoUrl":   repo_html_url,
        "vercelUrl": vercel_url,
        "visibility": body.visibility,
        "message": (
            "Project published. Type a message below to keep editing — "
            "changes will deploy automatically."
            if vercel_url else
            "Your code is saved. The hosting setup is still in progress — check back in a minute."
        ),
    }


@router.post("/{project_id}/sync-workspace")
async def sync_workspace(
    project_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Snapshot the live preview workspace into the GitHub staging branch.

    Why this exists
    ───────────────
    The frontend's "publish" route only fast-forwards ``main`` to wherever
    ``staging`` already points on GitHub. It does NOT push new code. So when
    the user (or our patcher) edits a file in the live preview workspace —
    e.g. our ``next.config.mjs`` assetPrefix fix — those edits never reach
    Vercel until the next chat task triggers a fresh push.

    This endpoint closes that gap. It runs ``git add -A && git commit && git
    push origin staging`` against the live preview workspace, so the next
    publish (or just the staging push itself, if Vercel is wired to that
    branch) ships the current state.

    Idempotent: returns ``{ok: true, changed: false}`` when there's nothing
    to commit.
    """
    if not PLATFORM_GITHUB_TOKEN:
        raise HTTPException(500, "PLATFORM_GITHUB_TOKEN not configured")

    # ── Look up the project's repo info ─────────────────────────
    async with db_client(user.raw_jwt) as sb:
        sess_res = await (
            sb.table("chat_sessions")
            .select("platform_repo_url,platform_repo_branch")
            .eq("user_id", user.user_id)
            .eq("project_id", project_id)
            .maybe_single()
            .execute()
        )
    session_row = (sess_res.data if sess_res else None) or {}
    platform_repo_url = session_row.get("platform_repo_url")
    if not platform_repo_url:
        raise HTTPException(
            400,
            "This project hasn't been published yet. Use the regular Publish "
            "button first to create the GitHub repo.",
        )
    branch = session_row.get("platform_repo_branch") or "staging"

    # ── Locate the live preview workspace ───────────────────────
    workspace_path = preview_workspace_path(project_id)
    if not os.path.isdir(workspace_path):
        raise HTTPException(
            404,
            "No live workspace found for this project — open the workspace "
            "first so the preview clones it, then try sync again.",
        )
    git_dir = os.path.join(workspace_path, ".git")
    if not os.path.isdir(git_dir):
        raise HTTPException(
            500,
            "Workspace is not a git repository — cannot sync. The preview "
            "system should have cloned it; please report this.",
        )

    # ── Run git add / commit / push ─────────────────────────────
    def _run(cmd, timeout=60):
        return subprocess.run(
            cmd,
            cwd=workspace_path,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={
                **os.environ,
                # Avoid prompting on credential failures
                "GIT_TERMINAL_PROMPT": "0",
            },
        )

    try:
        # Identity (idempotent — only writes if missing)
        await asyncio.to_thread(_run, ["git", "config", "user.name", "Lucid AI"], 5)
        await asyncio.to_thread(_run, ["git", "config", "user.email", "ai@lucid.dev"], 5)

        # Detect staged + unstaged changes
        status_r = await asyncio.to_thread(_run, ["git", "status", "--porcelain"], 15)
        if status_r.returncode != 0:
            raise HTTPException(
                500,
                f"git status failed: {(status_r.stderr or status_r.stdout)[:200]}",
            )
        changed_files = [
            line[3:] for line in (status_r.stdout or "").splitlines() if line.strip()
        ]
        if not changed_files:
            return {
                "ok": True,
                "changed": False,
                "filesPushed": 0,
                "message": "Workspace is already in sync with GitHub — nothing to push.",
            }

        # Stage everything
        add_r = await asyncio.to_thread(_run, ["git", "add", "-A"], 60)
        if add_r.returncode != 0:
            raise HTTPException(
                500,
                f"git add failed: {(add_r.stderr or add_r.stdout)[:200]}",
            )

        # Commit
        commit_msg = f"Sync workspace via Lucid AI ({len(changed_files)} file{'s' if len(changed_files) != 1 else ''})"
        commit_r = await asyncio.to_thread(
            _run, ["git", "commit", "-m", commit_msg], 30,
        )
        # "nothing to commit" is OK (race between status check and commit)
        if commit_r.returncode != 0 and "nothing to commit" not in (
            commit_r.stderr + commit_r.stdout
        ).lower():
            raise HTTPException(
                500,
                f"git commit failed: {(commit_r.stderr or commit_r.stdout)[:200]}",
            )

        # Make sure origin uses the platform token (the workspace was cloned
        # by bg_preview which uses an embedded token, but it could have been
        # rewritten by something else in the workspace lifecycle).
        m = re.search(r"github\.com[:/]([^/]+)/([^/.]+)", platform_repo_url)
        if not m:
            raise HTTPException(
                500,
                f"Could not parse owner/repo from platform_repo_url: {platform_repo_url}",
            )
        owner, repo = m.group(1), m.group(2)
        push_url = f"https://{PLATFORM_GITHUB_TOKEN}@github.com/{owner}/{repo}.git"
        await asyncio.to_thread(_run, ["git", "remote", "set-url", "origin", push_url], 5)

        # Push to the project's branch (default staging)
        push_r = await asyncio.to_thread(
            _run, ["git", "push", "origin", f"HEAD:{branch}"], 180,
        )
        if push_r.returncode != 0:
            err = (push_r.stderr or push_r.stdout or "").replace(PLATFORM_GITHUB_TOKEN, "***")
            raise HTTPException(
                502,
                f"git push failed: {err[:300]}",
            )
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(504, f"Git operation timed out: {exc}")

    logger.info(
        "sync-workspace: pushed %d file change(s) to %s/%s on branch %s",
        len(changed_files), owner, repo, branch,
    )

    return {
        "ok": True,
        "changed": True,
        "filesPushed": len(changed_files),
        "branch": branch,
        "repoUrl": platform_repo_url,
        "message": (
            f"Pushed {len(changed_files)} file change{'s' if len(changed_files) != 1 else ''} "
            f"to {branch}. Click Publish to deploy to Vercel."
        ),
    }
