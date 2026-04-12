"""
pipeline/step6_verify.py — Pipeline Steps 6+7: verify build, verify changes, push.

Extracted verbatim from task_pipeline.py (lines 4937–5131).
Zero logic changes.
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
        result = subprocess.run(
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
    try:
        await websocket.send_json({
            "type": "progress",
            "message": "🚀 Pushing to branch...",
        })
    except Exception:
        pass

    repo_url = validated["repo_url"]
    branch = validated["branch"]
    git_token = validated.get("git_token", "")
    commit_msg = f"AI: {task[:50]}"

    # Build authenticated URL for push
    if git_token:
        from app.services.vcs.git import _inject_token_into_url
        authed_url = _inject_token_into_url(repo_url, git_token)
    else:
        authed_url = repo_url

    # ── Primary path: direct subprocess (fast) ────────────
    git_ops = [
        {"cmd": ["git", "add", "."], "msg": "staging changes"},
        {"cmd": ["git", "commit", "-m", commit_msg], "msg": "committing changes"},
        {"cmd": ["git", "remote", "set-url", "origin", authed_url], "msg": "setting remote URL"},
        {"cmd": ["git", "push", "-u", "origin", branch], "msg": "pushing to GitHub"},
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
                "type": "complete",
                "message": f"✅ Done! Pushed to {branch}",
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
            gemini_key=validated["gemini_api_key"],
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

            # Check if push succeeded by looking at git log
            check = subprocess.run(
                ["git", "log", "--oneline", "-1"],
                cwd=workspace_path,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if commit_msg[:20] in (check.stdout or ""):
                try:
                    await websocket.send_json({
                        "type": "complete",
                        "message": f"✅ Done! Pushed to {branch}",
                    })
                except Exception:
                    pass
                return True

        # Both methods failed
        try:
            await websocket.send_json({
                "type": "error",
                "message": f"❌ Push failed: {subprocess_error}",
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
                "message": f"❌ Push failed: {str(e)[:300]}",
            })
        except Exception:
            pass
        return False
