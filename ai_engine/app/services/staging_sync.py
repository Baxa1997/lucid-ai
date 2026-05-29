"""Push the live workspace snapshot to the platform staging branch."""

from __future__ import annotations

import asyncio
import os
import re
import subprocess

from app.config import logger
from app.paths import preview_workspace_path
from app.services.pipeline.constants import PLATFORM_GITHUB_TOKEN
from app.supabase_client import db_client


class StagingSyncError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


_PROJECT_LOCKS: dict[str, asyncio.Lock] = {}


def _sync_lock(project_id: str) -> asyncio.Lock:
    key = project_id or "_unknown"
    lock = _PROJECT_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _PROJECT_LOCKS[key] = lock
    return lock


async def sync_workspace_to_staging(
    *,
    project_id: str,
    user_id: str,
    user_jwt: str,
    workspace_path: str | None = None,
    commit_message: str | None = None,
) -> dict:
    """Commit local workspace changes and push them to the staging branch.

    Idempotent: if the workspace has no local changes, returns changed=false.
    Serialized per project so rapid manual edits cannot run competing pushes.
    """

    if not PLATFORM_GITHUB_TOKEN:
        raise StagingSyncError(500, "PLATFORM_GITHUB_TOKEN not configured")
    if not project_id:
        raise StagingSyncError(400, "project_id is required")

    async with _sync_lock(project_id):
        async with db_client(user_jwt) as sb:
            sess_res = await (
                sb.table("chat_sessions")
                .select("platform_repo_url,platform_repo_branch")
                .eq("user_id", user_id)
                .eq("project_id", project_id)
                .maybe_single()
                .execute()
            )
        session_row = (sess_res.data if sess_res else None) or {}
        platform_repo_url = session_row.get("platform_repo_url")
        if not platform_repo_url:
            raise StagingSyncError(
                400,
                "This project has no staging repository yet. Publish once to create it.",
            )
        branch = session_row.get("platform_repo_branch") or "staging"

        live_workspace = workspace_path or preview_workspace_path(project_id)
        if not os.path.isdir(live_workspace):
            raise StagingSyncError(
                404,
                "No live workspace found for this project.",
            )
        git_dir = os.path.join(live_workspace, ".git")
        if not os.path.isdir(git_dir):
            raise StagingSyncError(
                500,
                "Workspace is not a git repository - cannot sync.",
            )

        def _run(cmd: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
            return subprocess.run(
                cmd,
                cwd=live_workspace,
                capture_output=True,
                text=True,
                timeout=timeout,
                env={
                    **os.environ,
                    "GIT_TERMINAL_PROMPT": "0",
                },
            )

        try:
            await asyncio.to_thread(_run, ["git", "config", "user.name", "Lucid AI"], 5)
            await asyncio.to_thread(_run, ["git", "config", "user.email", "ai@lucid.dev"], 5)

            status_r = await asyncio.to_thread(_run, ["git", "status", "--porcelain"], 15)
            if status_r.returncode != 0:
                raise StagingSyncError(
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
                    "branch": branch,
                    "repoUrl": platform_repo_url,
                    "message": "Workspace is already in sync with GitHub.",
                }

            add_r = await asyncio.to_thread(_run, ["git", "add", "-A"], 60)
            if add_r.returncode != 0:
                raise StagingSyncError(
                    500,
                    f"git add failed: {(add_r.stderr or add_r.stdout)[:200]}",
                )

            default_msg = (
                f"Sync workspace via Lucid AI ({len(changed_files)} file"
                f"{'s' if len(changed_files) != 1 else ''})"
            )
            msg = (commit_message or default_msg).strip()[:180] or default_msg
            commit_r = await asyncio.to_thread(_run, ["git", "commit", "-m", msg], 30)
            if commit_r.returncode != 0 and "nothing to commit" not in (
                commit_r.stderr + commit_r.stdout
            ).lower():
                raise StagingSyncError(
                    500,
                    f"git commit failed: {(commit_r.stderr or commit_r.stdout)[:200]}",
                )

            match = re.search(r"github\.com[:/]([^/]+)/([^/.]+)", platform_repo_url)
            if not match:
                raise StagingSyncError(
                    500,
                    f"Could not parse owner/repo from platform_repo_url: {platform_repo_url}",
                )
            owner, repo = match.group(1), match.group(2)
            push_url = f"https://{PLATFORM_GITHUB_TOKEN}@github.com/{owner}/{repo}.git"
            await asyncio.to_thread(_run, ["git", "remote", "set-url", "origin", push_url], 5)

            push_r = await asyncio.to_thread(
                _run,
                ["git", "push", "origin", f"HEAD:{branch}"],
                180,
            )
            if push_r.returncode != 0:
                err = (push_r.stderr or push_r.stdout or "").replace(PLATFORM_GITHUB_TOKEN, "***")
                raise StagingSyncError(502, f"git push failed: {err[:300]}")
        except subprocess.TimeoutExpired as exc:
            raise StagingSyncError(504, f"Git operation timed out: {exc}") from exc

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
                f"Pushed {len(changed_files)} file change"
                f"{'s' if len(changed_files) != 1 else ''} to {branch}."
            ),
        }
