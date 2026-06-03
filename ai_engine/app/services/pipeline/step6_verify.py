"""
pipeline/step6_verify.py — Pipeline Steps 6+7: verify build, verify changes, push.

Extracted from task_pipeline.py and extended with provider-aware push reporting.
"""

from __future__ import annotations

import os
import sys
import asyncio
import subprocess
import logging

from fastapi import WebSocket
from claude_code_sdk.query import query
from claude_code_sdk.types import ClaudeCodeOptions

from app.services.openhands_manager import openhands_manager

logger = logging.getLogger(__name__)


async def verify_changes(
    workspace_path: str,
    websocket: WebSocket,
) -> bool:
    """Check if Claude actually changed files via git status.

    Returns True if files changed, False otherwise.
    """
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            ["git", "status", "--porcelain"],
            cwd=workspace_path,
            capture_output=True,
            text=True,
            timeout=30,
        )

        if not result.stdout.strip():
            await websocket.send_json({
                "type": "warning",
                "message": "⚠️ No files were changed. Try rephrasing your task.",
            })
            return False

        changed = result.stdout.strip()
        file_count = len(changed.splitlines())
        await websocket.send_json({
            "type": "progress",
            "message": f"📝 {file_count} file(s) changed:\n{changed}",
        })
        return True

    except Exception as e:
        logger.error("verify_changes failed: %s", e)
        try:
            await websocket.send_json({
                "type": "error",
                "message": f"❌ Verify failed: {str(e)[:200]}",
            })
        except Exception:
            pass
        return False


async def push_with_openhands(
    workspace_path: str,
    validated: dict,
    task: str,
    task_id: str,
    websocket: WebSocket,
) -> bool:
    """Commit and push changes.

    Primary: direct subprocess calls (~3-5s).
    Fallback: OpenHands agent (if subprocess fails).

    Returns True on success, False on failure.
    """
    repo_url = validated["repo_url"]
    branch = validated["branch"]
    git_token = validated.get("git_token", "")
    commit_msg = f"AI: {task[:50]}"

    from app.services.vcs.git import (
        _inject_token_into_url,
        branch_browser_url,
        detect_git_provider,
        provider_display_name,
        review_request_url,
        strip_auth_from_url,
    )

    provider = detect_git_provider(repo_url, validated.get("git_provider", ""))
    provider_label = provider_display_name(provider)
    repo_web_url = strip_auth_from_url(repo_url, git_token)
    branch_strategy = (
        (validated.get("external_project_index") or {}).get("branch_strategy")
        or {}
    )
    base_branch = (
        branch_strategy.get("base_branch")
        or ((validated.get("external_project_index") or {}).get("git") or {}).get("selected_branch")
        or validated.get("base_branch")
        or "main"
    )
    created_branch = branch_strategy.get("created_branch") or ""
    is_new_branch = bool(created_branch) or bool(base_branch and branch != base_branch)
    branch_url = branch_browser_url(repo_web_url, branch, provider)
    pr_url = review_request_url(repo_web_url, branch, base_branch, provider) if is_new_branch else ""

    try:
        await websocket.send_json({
            "type": "progress",
            "message": f"🚀 Pushing to {provider_label} branch `{branch}`...",
        })
    except Exception:
        pass

    # Build authenticated URL for push
    if git_token:
        authed_url = _inject_token_into_url(repo_url, git_token)
    else:
        authed_url = repo_url

    # ── Primary path: direct subprocess (fast) ────────────
    git_ops = [
        {"cmd": ["git", "add", "."], "msg": "staging changes"},
        {"cmd": ["git", "commit", "-m", commit_msg], "msg": "committing changes"},
        {"cmd": ["git", "remote", "set-url", "origin", authed_url], "msg": "setting remote URL"},
        {"cmd": ["git", "push", "-u", "origin", branch], "msg": f"pushing to {provider_label}"},
    ]

    subprocess_ok = True
    subprocess_error = ""
    try:
        for op in git_ops:
            result = await asyncio.to_thread(
                subprocess.run,
                op["cmd"],
                cwd=workspace_path,
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode != 0:
                err = result.stderr or result.stdout or "unknown error"
                if git_token:
                    err = err.replace(git_token, "***")
                subprocess_error = f"Failed {op['msg']}: {err.strip()[:300]}"
                subprocess_ok = False
                break
    except Exception as e:
        subprocess_error = str(e)[:300]
        subprocess_ok = False

    if subprocess_ok:
        try:
            await websocket.send_json({
                "type": "git_push_result",
                "pushed": True,
                "provider": provider,
                "providerLabel": provider_label,
                "branch": branch,
                "baseBranch": base_branch,
                "repoUrl": repo_web_url,
                "branchUrl": branch_url,
                "prUrl": pr_url,
                "newBranch": is_new_branch,
                "summary": f"Changes pushed to {provider_label} branch {branch}.",
            })
            await websocket.send_json({
                "type": "complete",
                "message": f"✅ Done! Pushed to {provider_label} branch {branch}",
            })
        except Exception:
            pass
        return True

    # ── Fallback: OpenHands agent ─────────────────────────
    logger.warning("Subprocess push failed (%s) — trying OpenHands fallback", subprocess_error)

    try:
        conversation = await openhands_manager.create_conversation(
            task_id=f"{task_id}_push",
            workspace=workspace_path,
            tools=["terminal"],
        )

        if conversation is not None:
            push_prompt = f"""Push code changes to the remote repository.
Run these commands in order:
1. cd {workspace_path}
2. git add .
3. git commit -m "{commit_msg}"
4. git remote set-url origin {authed_url}
5. git push origin {branch}
6. Print "PUSH_COMPLETE" when done

Do nothing else. Stop after these commands."""

            try:
                await asyncio.to_thread(conversation.send_message, push_prompt)
                await asyncio.to_thread(conversation.run)
            except Exception as e:
                logger.warning("OpenHands push conversation failed: %s", e)

            # Destroy OpenHands immediately
            await openhands_manager.destroy_conversation(f"{task_id}_push")

            # Check if push succeeded by comparing local HEAD with remote branch.
            local_head = await asyncio.to_thread(
                subprocess.run,
                ["git", "rev-parse", "HEAD"],
                cwd=workspace_path,
                capture_output=True,
                text=True,
                timeout=10,
            )
            remote_head = await asyncio.to_thread(
                subprocess.run,
                ["git", "ls-remote", "origin", f"refs/heads/{branch}"],
                cwd=workspace_path,
                capture_output=True,
                text=True,
                timeout=30,
            )
            local_sha = (local_head.stdout or "").strip()
            remote_sha = (remote_head.stdout or "").split()[0] if remote_head.stdout else ""
            if local_sha and remote_sha and local_sha == remote_sha:
                try:
                    await websocket.send_json({
                        "type": "git_push_result",
                        "pushed": True,
                        "provider": provider,
                        "providerLabel": provider_label,
                        "branch": branch,
                        "baseBranch": base_branch,
                        "repoUrl": repo_web_url,
                        "branchUrl": branch_url,
                        "prUrl": pr_url,
                        "newBranch": is_new_branch,
                        "summary": f"Changes pushed to {provider_label} branch {branch}.",
                    })
                    await websocket.send_json({
                        "type": "complete",
                        "message": f"✅ Done! Pushed to {provider_label} branch {branch}",
                    })
                except Exception:
                    pass
                return True

        # Both methods failed
        try:
            hint = ""
            protected_signals = (
                "protected branch",
                "pre-receive hook declined",
                "not allowed to push",
                "permission denied",
            )
            if any(sig in subprocess_error.lower() for sig in protected_signals):
                hint = f" Try pushing to a Lucid work branch and opening a {provider_label} review request."
            await websocket.send_json({
                "type": "error",
                "message": f"❌ {provider_label} push failed: {subprocess_error}{hint}",
            })
        except Exception:
            pass
        return False

    except Exception as e:
        logger.error("push_with_openhands fallback failed: %s", e, exc_info=True)
        await openhands_manager.destroy_conversation(f"{task_id}_push")
        try:
            await websocket.send_json({
                "type": "error",
                "message": f"❌ {provider_label} push failed: {str(e)[:300]}",
            })
        except Exception:
            pass
        return False
