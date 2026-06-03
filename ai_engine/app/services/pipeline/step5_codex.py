"""pipeline/step5_codex.py — Step 5 implementation via Codex CLI.

This is the Codex-backed agentic edit path for existing projects. It shares
the same router contract as the Claude CLI path: return True when the
workspace was changed, False when the caller should fall back.
"""

from __future__ import annotations

import asyncio
import logging
import os

from fastapi import WebSocket

from app.services.codex_cli import run_codex_session
from app.services.openhands_manager import openhands_manager
from .step5_prompts import build_step5_prompts

logger = logging.getLogger(__name__)


def _normalize_codex_model(model: str) -> str:
    raw = (model or "").strip()
    if raw.startswith("openai/"):
        return raw.split("/", 1)[1]
    return raw


def _codex_execution_contract() -> str:
    return """\

CODEX EXECUTION CONTRACT

You are editing a generated user project inside the current workspace.
Where the plan says "Write/Edit/MultiEdit tool", use the file-editing
mechanism available to you in Codex.

Rules:
- Edit only files required by the user's task.
- Do not commit, push, pull, clone, reset, or delete the repository.
- Do not run long-lived dev servers.
- Prefer small targeted patches over rewrites.
- If adding a page/screen/route/view, update the router/navigation references
  that make the new page reachable.
- If fixing UI/UX bugs, fix the visible issue directly: responsive overflow,
  z-index, spacing, alignment, contrast, state styles, and mobile behavior.
- If the task is impossible because critical information is missing, do not
  guess wildly. Finish with: NEEDS_CLARIFICATION: <one short question>.
- Stop after the requested change is implemented.
"""


async def execute_with_codex_cli(
    task: str,
    workspace_path: str,
    api_key: str,
    classification: dict,
    plan: str,
    websocket: WebSocket,
    *,
    user_id: str | None = None,
    stack: str | None = None,
    model: str = "",
) -> bool:
    """Run Codex to implement an existing-project edit.

    ``api_key`` is optional because Codex CLI may already be authenticated on
    the host. When present, it is passed as OPENAI_API_KEY.
    """
    if await openhands_manager.is_active():
        logger.error("CRITICAL: OpenHands still active when Codex should run")
        await openhands_manager.destroy_all()
        await asyncio.sleep(1)

    try:
        await websocket.send_json({
            "type": "progress",
            "message": "🤖 Codex is editing the project...",
        })
    except Exception:
        pass

    system_prompt, user_prompt = build_step5_prompts(
        task=task,
        plan=plan,
        classification=classification,
        stack=stack,
    )
    prompt = (
        f"{system_prompt}\n"
        f"{_codex_execution_contract()}\n\n"
        f"{user_prompt}"
    )

    files_estimate = max(1, int(classification.get("files_estimate", 2) or 2))
    timeout_seconds = max(180, min(900, files_estimate * 90 + 180))
    codex_model = _normalize_codex_model(
        model
        or os.environ.get("CODEX_EDIT_MODEL", "").strip()
        or os.environ.get("CODEX_MODEL", "").strip()
    )

    try:
        result = await run_codex_session(
            prompt=prompt,
            workspace_path=workspace_path,
            websocket=websocket,
            api_key=api_key,
            user_id=user_id,
            model=codex_model,
            timeout_seconds=timeout_seconds,
            phase_label="step5_codex",
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("step5_codex: run_codex_session raised: %s", exc, exc_info=True)
        try:
            await websocket.send_json({
                "type": "warning",
                "message": f"⚠️ Codex edit failed: {str(exc)[:160]}",
            })
        except Exception:
            pass
        return False

    if result.success and result.files_written:
        try:
            await websocket.send_json({
                "type": "progress",
                "message": (
                    f"✅ Codex edited {len(result.files_written)} file"
                    f"{'s' if len(result.files_written) != 1 else ''}"
                ),
            })
        except Exception:
            pass
        return True

    err = result.error or (
        "no files changed" if result.success else "unknown Codex failure"
    )
    logger.info("step5_codex declined: %s", err)
    try:
        await websocket.send_json({
            "type": "warning",
            "message": f"⚠️ Codex could not complete this edit: {err[:160]}",
        })
    except Exception:
        pass
    return False
