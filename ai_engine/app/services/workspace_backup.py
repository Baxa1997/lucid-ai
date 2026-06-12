"""Durable workspace backup for failed publishes.

Generation runs in a per-run /tmp workspace (``lucid_new_*``). On a
successful push the code is safe in the git repo; on a FAILED push the
/tmp directory is the only copy of the user's generated project — a
container restart wipes it, and the ws.py reload path then finds
nothing: no repo, no workspace, no way back.

``backup_workspace_durably`` copies the workspace into
``preview_workspace_path(project_id)`` under PREVIEW_WS_ROOT, which is
volume-mounted and survives restarts. The same path is what every
reload/reconnect already checks (``_has_real_workspace`` /
``_has_cached_ws`` in ws.py), so a backed-up project is automatically
picked up on re-entry: preview restarts from it and a later "push
again" has real files to push.

Note the success-path "promotion" in the pipelines uses a *symlink*
into /tmp — fine there because the repo is the durable copy. Here the
repo has nothing, so this helper always materialises a real directory,
replacing a symlink if one occupies the target.

Never raises: a backup failure must not mask the original push error.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil

from app.paths import preview_workspace_path

logger = logging.getLogger(__name__)

# Heavy/derived dirs are excluded: node_modules is restored by the
# reload path's install step, .next is build output, .git may hold the
# failed remote config and is re-initialised on retry anyway.
_EXCLUDE = ("node_modules", ".next", ".git", ".turbo")


def _backup_sync(workspace_path: str, project_id: str) -> str | None:
    target = preview_workspace_path(project_id)

    real_ws = os.path.realpath(workspace_path)
    real_target = os.path.realpath(target)

    # Generation already ran inside the durable path (edit-mode on a
    # shared workspace) — nothing to do.
    if real_target == real_ws and not os.path.islink(target):
        return target

    staging = f"{target}.bk_tmp"
    if os.path.lexists(staging):
        shutil.rmtree(staging, ignore_errors=True)

    shutil.copytree(
        workspace_path,
        staging,
        ignore=shutil.ignore_patterns(*_EXCLUDE),
        symlinks=False,
    )

    # Swap into place. The old target may be a dangling symlink from a
    # previous run's promotion, a live symlink into this same /tmp
    # workspace, or a stale real dir from an earlier failed push — the
    # fresh copy supersedes all of them.
    if os.path.islink(target):
        os.unlink(target)
    elif os.path.isdir(target):
        shutil.rmtree(target, ignore_errors=True)
    os.replace(staging, target)
    return target


async def backup_workspace_durably(
    workspace_path: str | None,
    project_id: str | None,
    websocket=None,
) -> str | None:
    """Copy ``workspace_path`` to the durable preview location.

    Returns the durable path on success, None when skipped or failed.
    Optionally tells the user (via ``websocket``) that their code is
    safe despite the failed push.
    """
    if not workspace_path or not project_id or not os.path.isdir(workspace_path):
        return None
    try:
        target = await asyncio.to_thread(_backup_sync, workspace_path, project_id)
    except Exception as exc:
        logger.warning(
            "Durable workspace backup failed for project %s (non-fatal): %s",
            project_id, exc,
        )
        return None

    logger.info(
        "Backed up workspace for project %s to %s after failed push",
        project_id, target,
    )
    if websocket is not None:
        try:
            await websocket.send_json({
                "type": "chat_message",
                "role": "agent",
                "content": (
                    "Your generated code is saved on the platform even though "
                    "the push failed — it will be restored when you return. "
                    "Ask me to push again once the issue is resolved."
                ),
            })
        except Exception:
            pass
    return target
