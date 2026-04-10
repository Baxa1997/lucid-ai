"""
pipeline/step2_clone.py — Pipeline Step 2: clone repo using OpenHands.

Extracted verbatim from task_pipeline.py (lines 998–1143).
Zero logic changes.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import logging
import asyncio

from fastapi import WebSocket

from app.services.openhands_manager import openhands_manager

logger = logging.getLogger(__name__)


async def clone_with_openhands(
    validated: dict,
    task_id: str,
    websocket: WebSocket,
) -> str | None:
    """Clone repo using OpenHands V1 SDK.

    Creates a minimal agent with TerminalTool ONLY for git clone.
    Destroys OpenHands completely after clone finishes.

    Returns workspace_path on success, None on failure.
    """
    workspace_path = f"/tmp/lucid_{task_id}"

    try:
        await websocket.send_json({
            "type": "progress",
            "message": "📦 Cloning repository...",
        })

        repo_url = validated["repo_url"]
        branch = validated["branch"]
        gemini_key = validated["gemini_api_key"]

        # Create workspace directory
        os.makedirs(workspace_path, exist_ok=True)

        # Create OpenHands conversation for clone
        conversation = await openhands_manager.create_conversation(
            task_id=f"{task_id}_clone",
            workspace=workspace_path,
            gemini_key=gemini_key,
            tools=["terminal"],
        )

        if conversation is not None:
            # Use OpenHands agent for clone + install
            clone_prompt = f"""Clone this repository and install dependencies.
Run these commands in order:
1. git clone --branch {branch} {repo_url} {workspace_path}
2. cd {workspace_path}
3. Detect package manager and install:
   - If yarn.lock exists: yarn install
   - If pnpm-lock.yaml exists: pnpm install
   - If bun.lockb exists: bun install
   - Otherwise if package.json exists: npm install
4. If requirements.txt exists: pip install -r requirements.txt
5. git config user.name "Lucid AI Agent"
6. git config user.email "agent@lucid-ai.dev"
7. Print "CLONE_COMPLETE" when done

Do nothing else. Stop after these commands."""

            try:
                await asyncio.to_thread(conversation.send_message, clone_prompt)
                await asyncio.to_thread(conversation.run)
            except Exception as e:
                logger.warning("OpenHands clone conversation failed: %s", e)
                # Fall through to fallback

            # Destroy OpenHands immediately
            await openhands_manager.destroy_conversation(f"{task_id}_clone")

        # If OpenHands wasn't available or failed, use subprocess fallback
        if not os.listdir(workspace_path) if os.path.exists(workspace_path) else True:
            logger.info("Falling back to subprocess git clone")
            result = subprocess.run(
                ["git", "clone", "--branch", branch, "--single-branch", "--depth", "1", repo_url, "."],
                cwd=workspace_path,
                capture_output=True,
                text=True,
                timeout=180,
            )
            if result.returncode != 0:
                err = result.stderr
                # Strip token from error messages
                git_token = validated.get("git_token", "")
                if git_token:
                    err = err.replace(git_token, "***")
                await websocket.send_json({
                    "type": "error",
                    "message": f"❌ Clone failed: {err.strip()[:300]}",
                })
                shutil.rmtree(workspace_path, ignore_errors=True)
                return None

            # Configure git user
            subprocess.run(
                ["git", "config", "user.name", "Lucid AI Agent"],
                cwd=workspace_path, check=True,
            )
            subprocess.run(
                ["git", "config", "user.email", "agent@lucid-ai.dev"],
                cwd=workspace_path, check=True,
            )

        # Validate workspace
        if not os.path.exists(workspace_path):
            await websocket.send_json({
                "type": "error",
                "message": "❌ Clone failed: workspace path does not exist.",
            })
            return None

        if not os.listdir(workspace_path):
            await websocket.send_json({
                "type": "error",
                "message": "❌ Clone failed: repository is empty.",
            })
            shutil.rmtree(workspace_path, ignore_errors=True)
            return None

        await websocket.send_json({
            "type": "progress",
            "message": "✅ Repository ready",
        })
        return workspace_path

    except subprocess.TimeoutExpired:
        logger.error("clone_with_openhands timed out")
        # Ensure OpenHands is destroyed even on timeout
        await openhands_manager.destroy_conversation(f"{task_id}_clone")
        try:
            await websocket.send_json({
                "type": "error",
                "message": "❌ Clone timed out. Is the repo very large?",
            })
        except Exception:
            pass
        shutil.rmtree(workspace_path, ignore_errors=True)
        return None

    except Exception as e:
        logger.error("clone_with_openhands failed: %s", e, exc_info=True)
        # Always cleanup OpenHands
        await openhands_manager.destroy_conversation(f"{task_id}_clone")
        try:
            await websocket.send_json({
                "type": "error",
                "message": f"❌ Clone error: {str(e)[:300]}",
            })
        except Exception:
            pass
        shutil.rmtree(workspace_path, ignore_errors=True)
        return None
