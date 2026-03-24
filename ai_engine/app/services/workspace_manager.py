"""Workspace Manager — persistent workspaces per conversation.

Maps conversation_id → workspace_path. Clones once on first task,
then git pull for subsequent tasks. Auto-destroys after 2h idle.

Usage:
    from app.services.workspace_manager import workspace_manager

    # First call: clones repo (~30s)
    # Second+ call: git pull (~2s)
    path = await workspace_manager.get_or_create_workspace(
        conversation_id="abc123",
        validated=validated_dict,
        websocket=websocket,
    )

    # On disconnect:
    await workspace_manager.destroy_workspace("abc123")
"""

import asyncio
import os
import shutil
import time
import logging

from fastapi import WebSocket

from app.services.git_operations import clone_repo, pull_latest

logger = logging.getLogger(__name__)

# Workspaces expire after 2 hours of inactivity
WORKSPACE_TTL_SECONDS = 2 * 60 * 60   # 2 hours
REAPER_INTERVAL_SECONDS = 30 * 60     # check every 30 minutes


class _WorkspaceInfo:
    """Internal state for a single workspace."""

    __slots__ = ("path", "branch", "last_used", "repo_url", "git_token")

    def __init__(self, path: str, branch: str, repo_url: str, git_token: str):
        self.path = path
        self.branch = branch
        self.repo_url = repo_url
        self.git_token = git_token
        self.last_used = time.monotonic()

    def touch(self) -> None:
        self.last_used = time.monotonic()

    def is_expired(self) -> bool:
        return (time.monotonic() - self.last_used) > WORKSPACE_TTL_SECONDS


class WorkspaceManager:
    """Singleton that manages persistent workspaces per conversation.

    Thread-safe via asyncio.Lock.
    """

    def __init__(self) -> None:
        self._workspaces: dict[str, _WorkspaceInfo] = {}
        self._lock = asyncio.Lock()
        self._reaper_task: asyncio.Task | None = None

    # ── Public API ──────────────────────────────────────────

    async def get_or_create_workspace(
        self,
        conversation_id: str,
        validated: dict,
        websocket: WebSocket,
    ) -> str | None:
        """Get existing workspace or create a new one.

        First call per conversation_id: clones the repo (~30s).
        Subsequent calls: runs git pull (~2s).
        If workspace folder was deleted between calls, re-clones.

        Returns workspace_path on success, None on failure.
        """
        repo_url = validated["repo_url"]
        branch = validated["branch"]
        git_token = validated.get("git_token", "")

        async with self._lock:
            info = self._workspaces.get(conversation_id)

        # ── Case 1: Workspace exists and folder is intact ────
        if info and os.path.isdir(info.path) and os.listdir(info.path):
            # Check .git exists — if not, clone was interrupted and the
            # folder is garbage. Wipe and re-clone.
            if not os.path.isdir(os.path.join(info.path, ".git")):
                logger.warning(
                    "Workspace has no .git dir (broken clone) — will re-clone: %s",
                    info.path,
                )
                shutil.rmtree(info.path, ignore_errors=True)
                async with self._lock:
                    self._workspaces.pop(conversation_id, None)
            else:
                info.touch()
                logger.info(
                    "Reusing workspace for conversation %s at %s",
                    conversation_id, info.path,
                )

                try:
                    await websocket.send_json({
                        "type": "progress",
                        "message": "🔄 Syncing latest changes...",
                    })
                except Exception:
                    pass

                # Pull latest changes
                try:
                    await pull_latest(
                        workspace_dir=info.path,
                        branch=info.branch,
                        token=info.git_token,
                    )
                    try:
                        await websocket.send_json({
                            "type": "progress",
                            "message": "✅ Repository synced",
                        })
                    except Exception:
                        pass
                    return info.path

                except Exception as e:
                    logger.warning(
                        "git pull failed for conversation %s: %s — will re-clone",
                        conversation_id, e,
                    )
                    # Pull failed — fall through to re-clone
                    shutil.rmtree(info.path, ignore_errors=True)
                    async with self._lock:
                        self._workspaces.pop(conversation_id, None)

        # ── Case 2: Workspace was deleted or never existed — clone ──
        if info and not os.path.isdir(info.path):
            logger.warning(
                "Workspace folder missing for conversation %s — re-cloning",
                conversation_id,
            )
            async with self._lock:
                self._workspaces.pop(conversation_id, None)

        workspace_path = f"/tmp/lucid_conv_{conversation_id}"

        # If the folder already exists and is NOT empty (orphaned from a
        # cancelled task that cloned but never registered), reuse it
        # with git pull instead of wiping and re-cloning from scratch.
        if os.path.isdir(workspace_path) and os.listdir(workspace_path):
            # Only reuse if .git dir exists (complete clone).
            # If .git is missing, clone was interrupted — wipe and re-clone.
            if os.path.isdir(os.path.join(workspace_path, ".git")):
                logger.info(
                    "Found orphaned workspace folder — reusing: %s",
                    workspace_path,
                )
                # Register it and do git pull
                recovered_info = _WorkspaceInfo(
                    path=workspace_path,
                    branch=branch,
                    repo_url=repo_url,
                    git_token=git_token,
                )
                async with self._lock:
                    self._workspaces[conversation_id] = recovered_info

                try:
                    await websocket.send_json({
                        "type": "progress",
                        "message": "🔄 Syncing latest changes...",
                    })
                except Exception:
                    pass

                try:
                    await pull_latest(
                        workspace_dir=workspace_path,
                        branch=branch,
                        token=git_token,
                    )
                    try:
                        await websocket.send_json({
                            "type": "progress",
                            "message": "✅ Repository synced",
                        })
                    except Exception:
                        pass
                    return workspace_path
                except Exception as e:
                    logger.warning(
                        "git pull on orphaned workspace failed: %s — will re-clone",
                        e,
                    )
                    async with self._lock:
                        self._workspaces.pop(conversation_id, None)
                    shutil.rmtree(workspace_path, ignore_errors=True)
                    # Fall through to fresh clone below
            else:
                logger.warning(
                    "Orphaned folder has no .git dir (broken clone) — wiping: %s",
                    workspace_path,
                )
                shutil.rmtree(workspace_path, ignore_errors=True)

        os.makedirs(workspace_path, exist_ok=True)

        # Register workspace EARLY so that if the task is cancelled after
        # clone completes, the next call finds it and does git pull.
        new_info = _WorkspaceInfo(
            path=workspace_path,
            branch=branch,
            repo_url=repo_url,
            git_token=git_token,
        )
        async with self._lock:
            self._workspaces[conversation_id] = new_info

        try:
            await websocket.send_json({
                "type": "progress",
                "message": "📦 Cloning repository...",
            })
        except Exception:
            pass

        try:
            await clone_repo(
                repo_url=repo_url,
                token=git_token,
                branch=branch,
                workspace_dir=workspace_path,
            )
        except Exception as e:
            logger.error(
                "Clone failed for conversation %s: %s",
                conversation_id, e, exc_info=True,
            )
            err_msg = str(e)
            if git_token:
                err_msg = err_msg.replace(git_token, "***")
            try:
                await websocket.send_json({
                    "type": "error",
                    "message": f"❌ Clone failed: {err_msg[:300]}",
                })
            except Exception:
                pass
            # Clone failed — unregister and clean up
            async with self._lock:
                self._workspaces.pop(conversation_id, None)
            shutil.rmtree(workspace_path, ignore_errors=True)
            return None

        # Validate workspace
        if not os.path.exists(workspace_path) or not os.listdir(workspace_path):
            try:
                await websocket.send_json({
                    "type": "error",
                    "message": "❌ Clone failed: repository is empty.",
                })
            except Exception:
                pass
            async with self._lock:
                self._workspaces.pop(conversation_id, None)
            shutil.rmtree(workspace_path, ignore_errors=True)
            return None

        # Touch to update last_used timestamp
        new_info.touch()

        logger.info(
            "Workspace created for conversation %s at %s",
            conversation_id, workspace_path,
        )

        try:
            await websocket.send_json({
                "type": "progress",
                "message": "✅ Repository ready",
            })
        except Exception:
            pass

        # --- FIX 2: Create .claude/settings.json for permission bypass ---
        import json
        import subprocess
        claude_dir = os.path.join(workspace_path, ".claude")
        os.makedirs(claude_dir, exist_ok=True)
        settings = {
            "permissions": {
                "defaultMode": "bypassPermissions",
                "allow": [
                    "Read", "Write", "Edit",
                    "MultiEdit", "Bash(npm *)",
                    "Bash(npx *)", "Bash(node *)",
                    "Bash(cat *)", "Bash(ls *)",
                    "Bash(mkdir *)", "Bash(touch *)",
                    "Bash(cp *)", "Bash(mv *)",
                ],
                "deny": [
                    "Bash(git commit*)",
                    "Bash(git push*)",
                    "Bash(rm -rf*)",
                    "Bash(sudo*)",
                ],
            }
        }
        with open(os.path.join(claude_dir, "settings.json"), "w") as f:
            json.dump(settings, f, indent=2)

        # Also fix OS permissions so Claude can write to all files
        os.chmod(workspace_path, 0o755)
        subprocess.run(
            ["chmod", "-R", "755", workspace_path],
            capture_output=True,
        )
        logger.info("Claude settings.json created and chmod 755 applied to %s", workspace_path)

        return workspace_path

    async def destroy_workspace(self, conversation_id: str) -> None:
        """Destroy workspace for a conversation — called on disconnect."""
        async with self._lock:
            info = self._workspaces.pop(conversation_id, None)

        if info is None:
            return

        if os.path.isdir(info.path):
            try:
                shutil.rmtree(info.path)
                logger.info(
                    "Workspace destroyed for conversation %s: %s",
                    conversation_id, info.path,
                )
            except Exception as e:
                logger.error(
                    "Failed to destroy workspace %s: %s",
                    info.path, e,
                )

    async def destroy_all(self) -> None:
        """Destroy all workspaces — called on server shutdown."""
        async with self._lock:
            all_infos = list(self._workspaces.items())
            self._workspaces.clear()

        for conv_id, info in all_infos:
            if os.path.isdir(info.path):
                try:
                    shutil.rmtree(info.path)
                    logger.info("Shutdown cleanup — destroyed workspace: %s", info.path)
                except Exception as e:
                    logger.error("Shutdown cleanup failed for %s: %s", info.path, e)

    # ── Background reaper ───────────────────────────────────

    async def start_reaper(self) -> None:
        """Start the background reaper task."""
        if self._reaper_task is None or self._reaper_task.done():
            self._reaper_task = asyncio.create_task(self._reap_loop())
            logger.info("Workspace reaper started (interval=%ds)", REAPER_INTERVAL_SECONDS)

    async def stop_reaper(self) -> None:
        """Stop the background reaper task."""
        if self._reaper_task and not self._reaper_task.done():
            self._reaper_task.cancel()
            try:
                await self._reaper_task
            except asyncio.CancelledError:
                pass
            logger.info("Workspace reaper stopped")

    async def _reap_loop(self) -> None:
        """Periodically destroy expired workspaces."""
        while True:
            try:
                await asyncio.sleep(REAPER_INTERVAL_SECONDS)
                await self._reap_expired()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Workspace reaper error: %s", e)

    async def _reap_expired(self) -> None:
        """Find and destroy expired workspaces."""
        async with self._lock:
            expired = [
                conv_id for conv_id, info in self._workspaces.items()
                if info.is_expired()
            ]

        if not expired:
            return

        for conv_id in expired:
            logger.info("Reaping expired workspace for conversation %s", conv_id)
            await self.destroy_workspace(conv_id)

        logger.info("Reaped %d expired workspace(s)", len(expired))


# Module-level singleton
workspace_manager = WorkspaceManager()
