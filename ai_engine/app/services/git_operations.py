"""Git operations — clone, commit, and push.

All git operations are performed via subprocess calls so they work
both inside Docker containers and in local development.

Supports both GitHub and GitLab authentication via token-embedded URLs.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from urllib.parse import urlparse, urlunparse

from app.config import logger


def _inject_token_into_url(repo_url: str, token: str) -> str:
    """Embed an auth token into a git HTTPS URL.

    GitHub:  https://x-access-token:{token}@github.com/owner/repo.git
    GitLab:  https://oauth2:{token}@gitlab.com/owner/repo.git
    """
    parsed = urlparse(repo_url)

    if not parsed.scheme or not parsed.hostname:
        return repo_url  # scp-style URL or invalid — return as-is

    host = parsed.hostname.lower()
    if "github" in host:
        user = "x-access-token"
    else:
        user = "oauth2"  # GitLab convention

    authed = parsed._replace(netloc=f"{user}:{token}@{parsed.hostname}")
    return urlunparse(authed)


async def clone_repo(
    *,
    repo_url: str,
    token: str,
    branch: str,
    workspace_dir: str,
    git_user_name: str | None = None,
    git_user_email: str | None = None,
) -> bool:
    """Clone a repository into *workspace_dir*.

    Runs in a thread to avoid blocking the event loop.
    Returns True on success, raises on failure.
    """

    def _clone():
        authed_url = _inject_token_into_url(repo_url, token) if token else repo_url

        # Ensure the target directory exists and is empty
        os.makedirs(workspace_dir, exist_ok=True)

        # Remove --depth 1 for reliability — shallow clones cause push issues.
        # User explicitly requested stable push/pull across branches.
        cmd = [
            "git", "clone",
            "--branch", branch,
            authed_url,
            ".",  # clone into current dir
        ]

        logger.info(
            "Cloning %s (branch=%s) into %s",
            repo_url, branch, workspace_dir,
        )
        result = subprocess.run(
            cmd,
            cwd=workspace_dir,
            capture_output=True,
            text=True,
            timeout=180,  # Increased timeout for full clones
        )

        if result.returncode != 0:
            # Strip token from error message
            err = result.stderr.replace(token, "***") if token else result.stderr
            raise RuntimeError(f"git clone failed: {err.strip()}")

        # Configure git user for commits
        name = git_user_name or "Lucid AI Agent"
        email = git_user_email or "agent@lucid-ai.dev"
        subprocess.run(
            ["git", "config", "user.name", name],
            cwd=workspace_dir,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.email", email],
            cwd=workspace_dir,
            check=True,
        )

        logger.info("Clone complete — workspace: %s", workspace_dir)

    await asyncio.to_thread(_clone)
    return True


async def push_changes(
    *,
    workspace_dir: str,
    token: str,
    commit_message: str = "Changes by Lucid AI Agent",
    branch: str | None = None,
    new_branch: str | None = None,
) -> dict:
    """Stage all changes, commit, and push to the remote.

    Returns a dict with {committed: bool, pushed: bool, summary: str}.
    """

    def _push():
        # 1. Create and switch to new branch if requested
        if new_branch:
            logger.info("Creating new branch %s in %s", new_branch, workspace_dir)
            subprocess.run(
                ["git", "checkout", "-b", new_branch],
                cwd=workspace_dir,
                check=True,
            )

        # 2. Check if there are changes to commit
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=workspace_dir,
            capture_output=True,
            text=True,
        )

        if not status.stdout.strip():
            return {"committed": False, "pushed": False, "summary": "No changes to commit"}

        # 3. Stage all changes
        subprocess.run(
            ["git", "add", "-A"],
            cwd=workspace_dir,
            check=True,
        )

        # 4. Commit
        subprocess.run(
            ["git", "commit", "-m", commit_message],
            cwd=workspace_dir,
            capture_output=True,
            text=True,
            check=True,
        )

        # 5. Ensure the remote URL contains the token for push auth
        if token:
            remote_result = subprocess.run(
                ["git", "remote", "get-url", "origin"],
                cwd=workspace_dir,
                capture_output=True,
                text=True,
            )
            remote_url = remote_result.stdout.strip()

            # Only re-inject if not already present
            if token not in remote_url:
                authed_url = _inject_token_into_url(remote_url, token)
                subprocess.run(
                    ["git", "remote", "set-url", "origin", authed_url],
                    cwd=workspace_dir,
                    check=True,
                )

        # 6. Push
        target_branch = new_branch or branch or "HEAD"
        logger.info("Pushing to %s (branch=%s)", workspace_dir, target_branch)
        
        # If pushing a new branch for the first time, we need -u origin
        cmd = ["git", "push"]
        if new_branch:
            cmd.extend(["-u", "origin", new_branch])
        else:
            cmd.extend(["origin", target_branch])

        push_result = subprocess.run(
            cmd,
            cwd=workspace_dir,
            capture_output=True,
            text=True,
            timeout=120,
        )

        if push_result.returncode != 0:
            err = push_result.stderr
            if token:
                err = err.replace(token, "***")
            raise RuntimeError(f"git push failed: {err.strip()}")

        # 7. Get a summary of what was changed
        diff_stat = subprocess.run(
            ["git", "diff", "--stat", "HEAD~1"],
            cwd=workspace_dir,
            capture_output=True,
            text=True,
        )

        return {
            "committed": True,
            "pushed": True,
            "summary": diff_stat.stdout.strip() or "Changes pushed successfully",
        }

    result = await asyncio.to_thread(_push)
    logger.info("Push result for %s: %s", workspace_dir, result)
    return result


async def get_git_status(workspace_dir: str) -> str:
    """Return `git status --short` output for the workspace."""

    def _status():
        result = subprocess.run(
            ["git", "status", "--short"],
            cwd=workspace_dir,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()

    return await asyncio.to_thread(_status)


async def get_git_diff(workspace_dir: str) -> str:
    """Return `git diff` output for unstaged changes."""

    def _diff():
        result = subprocess.run(
            ["git", "diff"],
            cwd=workspace_dir,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()

    return await asyncio.to_thread(_diff)
