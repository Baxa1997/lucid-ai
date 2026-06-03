"""VCS — subprocess git operations: clone, pull, commit, push, status.

All operations run via subprocess so they work both inside Docker containers
and in local development. Supports GitHub and GitLab HTTPS authentication
via token-embedded URLs.
"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
from urllib.parse import quote, urlparse, urlunparse

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

    host = parsed.hostname
    if parsed.port:
        host = f"{host}:{parsed.port}"
    authed = parsed._replace(netloc=f"{user}:{token}@{host}")
    return urlunparse(authed)


def strip_auth_from_url(repo_url: str, token: str = "") -> str:
    """Return a browser-safe repo URL with credentials and .git suffix removed."""
    url = (repo_url or "").strip()
    if not url:
        return ""
    if token:
        url = url.replace(token, "***")

    parsed = urlparse(url)
    if parsed.scheme and parsed.hostname:
        host = parsed.hostname
        if parsed.port:
            host = f"{host}:{parsed.port}"
        clean = urlunparse(parsed._replace(netloc=host, query="", fragment=""))
    else:
        # Best effort for scp-style URLs: git@github.com:owner/repo.git
        match = re.match(r"^(?:[^@]+@)?([^:]+):(.+)$", url)
        clean = f"https://{match.group(1)}/{match.group(2)}" if match else url

    if clean.endswith(".git"):
        clean = clean[:-4]
    return clean.rstrip("/")


def detect_git_provider(repo_url: str = "", explicit_provider: str = "") -> str:
    """Normalize a git provider label from explicit metadata or repo host."""
    explicit = (explicit_provider or "").strip().lower()
    if explicit in {"github", "gitlab"}:
        return explicit
    host = (urlparse(repo_url or "").hostname or repo_url or "").lower()
    if "github" in host:
        return "github"
    if "gitlab" in host:
        return "gitlab"
    return "git"


def provider_display_name(provider: str) -> str:
    provider = (provider or "").lower()
    if provider == "github":
        return "GitHub"
    if provider == "gitlab":
        return "GitLab"
    return "Git"


def branch_browser_url(repo_url: str, branch: str, provider: str = "") -> str:
    """Build the browser URL for a branch on GitHub/GitLab."""
    clean = strip_auth_from_url(repo_url)
    if not clean or not branch:
        return ""
    safe_branch = quote(str(branch), safe="/")
    normalized_provider = detect_git_provider(clean, provider)
    if normalized_provider == "gitlab":
        return f"{clean}/-/tree/{safe_branch}"
    if normalized_provider == "github":
        return f"{clean}/tree/{safe_branch}"
    return clean


def review_request_url(
    repo_url: str,
    source_branch: str,
    base_branch: str = "main",
    provider: str = "",
) -> str:
    """Build a GitHub PR or GitLab MR creation URL for a pushed branch."""
    clean = strip_auth_from_url(repo_url)
    if not clean or not source_branch:
        return ""
    normalized_provider = detect_git_provider(clean, provider)
    source = quote(str(source_branch), safe="/")
    base = quote(str(base_branch or "main"), safe="/")
    if normalized_provider == "github":
        return f"{clean}/compare/{base}...{source}"
    if normalized_provider == "gitlab":
        source_q = quote(str(source_branch), safe="")
        base_q = quote(str(base_branch or "main"), safe="")
        return (
            f"{clean}/-/merge_requests/new"
            f"?merge_request[source_branch]={source_q}"
            f"&merge_request[target_branch]={base_q}"
        )
    return ""


async def run_git_with_retry(
    cmd: list,
    cwd: str,
    max_retries: int = 3,
    delay: int = 2,
) -> subprocess.CompletedProcess:

    last_error = None

    for attempt in range(max_retries):
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                cmd,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=60,
            )

            if result.returncode == 0:
                return result

            # Check if retryable error
            retryable = any(w in result.stderr for w in [
                "Could not resolve host",
                "Connection timed out",
                "Connection reset",
                "Unable to connect",
                "network",
                "timeout",
            ])

            if not retryable:
                return result  # Don't retry auth/other errors

            last_error = result.stderr

            if attempt < max_retries - 1:
                await asyncio.sleep(delay)
                continue

        except subprocess.TimeoutExpired:
            last_error = "timeout"
            if attempt < max_retries - 1:
                await asyncio.sleep(delay)
                continue

    return result


async def clone_repo(
    *,
    repo_url: str,
    token: str,
    branch: str,
    workspace_dir: str,
    git_user_name: str | None = None,
    git_user_email: str | None = None,
) -> bool:
    """Clone a repository into *workspace_dir*."""

    authed_url = _inject_token_into_url(repo_url, token) if token else repo_url

    os.makedirs(workspace_dir, exist_ok=True)

    cmd = [
        "git", "clone",
        "--branch", branch,
        "--single-branch",
        "--depth", "1",
        authed_url,
        ".",
    ]

    logger.info("Cloning %s (branch=%s) into %s", repo_url, branch, workspace_dir)

    result = await run_git_with_retry(cmd, cwd=workspace_dir, max_retries=3, delay=2)

    if result.returncode != 0:
        err = result.stderr.replace(token, "***") if token else result.stderr
        err = err.strip()
        _auth_signals = (
            "authentication failed",
            "invalid username or password",
            "could not read username",
            "repository not found",
            "403",
            "401",
            "remote: invalid",
        )
        if any(sig in err.lower() for sig in _auth_signals):
            raise PermissionError(
                f"git clone: authentication failed for {repo_url} — "
                f"check that your token is valid and has repo read access. "
                f"Detail: {err[:300]}"
            )
        raise RuntimeError(f"git clone failed: {err}")

    name = git_user_name or "Lucid AI Agent"
    email = git_user_email or "agent@lucid-ai.dev"
    await asyncio.to_thread(
        subprocess.run, ["git", "config", "user.name", name], cwd=workspace_dir, check=True
    )
    await asyncio.to_thread(
        subprocess.run, ["git", "config", "user.email", email], cwd=workspace_dir, check=True
    )

    logger.info("Clone complete — workspace: %s", workspace_dir)
    return True


async def pull_latest(
    *,
    workspace_dir: str,
    branch: str,
    token: str = "",
) -> bool:
    """Pull latest changes into an existing workspace."""

    if token:
        remote_result = await asyncio.to_thread(
            subprocess.run,
            ["git", "remote", "get-url", "origin"],
            cwd=workspace_dir,
            capture_output=True,
            text=True,
        )
        remote_url = remote_result.stdout.strip()
        if token not in remote_url:
            authed_url = _inject_token_into_url(remote_url, token)
            await asyncio.to_thread(
                subprocess.run,
                ["git", "remote", "set-url", "origin", authed_url],
                cwd=workspace_dir,
                check=True,
            )

    await asyncio.to_thread(
        subprocess.run,
        ["git", "checkout", "--", "."],
        cwd=workspace_dir,
        capture_output=True,
        text=True,
        timeout=30,
    )
    await asyncio.to_thread(
        subprocess.run,
        ["git", "clean", "-fd"],
        cwd=workspace_dir,
        capture_output=True,
        text=True,
        timeout=30,
    )

    logger.info("Pulling latest for branch %s in %s", branch, workspace_dir)
    result = await run_git_with_retry(
        ["git", "pull", "origin", branch], cwd=workspace_dir, max_retries=3, delay=2
    )

    if result.returncode != 0:
        err = result.stderr
        if token:
            err = err.replace(token, "***")
        err = err.strip()
        _auth_signals = (
            "authentication failed",
            "invalid username or password",
            "could not read username",
            "repository not found",
            "403",
            "401",
            "remote: invalid",
        )
        if any(sig in err.lower() for sig in _auth_signals):
            raise PermissionError(
                f"git pull: authentication failed — token may have expired or been revoked. "
                f"Detail: {err[:300]}"
            )
        raise RuntimeError(f"git pull failed: {err}")

    logger.info("Pull complete — workspace: %s", workspace_dir)
    return True


async def push_changes(
    *,
    workspace_dir: str,
    token: str,
    commit_message: str = "Changes by Lucid AI Agent",
    branch: str | None = None,
    new_branch: str | None = None,
) -> dict:
    """Stage all changes, commit, and push to the remote."""

    if new_branch:
        logger.info("Creating new branch %s in %s", new_branch, workspace_dir)
        await asyncio.to_thread(
            subprocess.run,
            ["git", "checkout", "-b", new_branch],
            cwd=workspace_dir,
            check=True,
        )

    status = await asyncio.to_thread(
        subprocess.run,
        ["git", "status", "--porcelain"],
        cwd=workspace_dir,
        capture_output=True,
        text=True,
    )

    if not status.stdout.strip():
        return {"committed": False, "pushed": False, "summary": "No changes to commit"}

    await asyncio.to_thread(
        subprocess.run, ["git", "add", "-A"], cwd=workspace_dir, check=True
    )

    await asyncio.to_thread(
        subprocess.run,
        ["git", "commit", "-m", commit_message],
        cwd=workspace_dir,
        capture_output=True,
        text=True,
        check=True,
    )

    if token:
        remote_result = await asyncio.to_thread(
            subprocess.run,
            ["git", "remote", "get-url", "origin"],
            cwd=workspace_dir,
            capture_output=True,
            text=True,
        )
        remote_url = remote_result.stdout.strip()

        if token not in remote_url:
            authed_url = _inject_token_into_url(remote_url, token)
            await asyncio.to_thread(
                subprocess.run,
                ["git", "remote", "set-url", "origin", authed_url],
                cwd=workspace_dir,
                check=True,
            )

    target_branch = new_branch or branch or "HEAD"
    logger.info("Pushing to %s (branch=%s)", workspace_dir, target_branch)

    cmd = ["git", "push"]
    if new_branch:
        cmd.extend(["-u", "origin", new_branch])
    else:
        cmd.extend(["origin", target_branch])

    push_result = await run_git_with_retry(cmd, cwd=workspace_dir, max_retries=3, delay=2)

    if push_result.returncode != 0:
        err = push_result.stderr
        if token:
            err = err.replace(token, "***")
        raise RuntimeError(f"git push failed: {err.strip()}")

    diff_stat = await asyncio.to_thread(
        subprocess.run,
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
