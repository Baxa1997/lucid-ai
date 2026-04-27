"""pipeline/step5_cli.py — Step 5 implementation via direct Claude CLI subprocess.

Drop-in replacement for ``step5_execute.execute_with_claude`` that talks to
the Claude Code CLI directly (no Python SDK wrapper). Same input contract,
same return type (bool), same WebSocket events to the frontend.

Why this exists
───────────────
The legacy SDK path hides token usage from us, so the billing meter cannot
attribute the heavy agentic-edit cost back to users. The CLI emits structured
JSON events including a final ``result`` event with full token + USD-cost
breakdown. By dropping the wrapper we get the same Claude Code intelligence
*and* full visibility into cost.

Behavior parity
───────────────
  • Same allowed/disallowed tools as the SDK config
  • Same circuit breaker (N consecutive read tools without a write → stop)
  • Same prompt construction (shared via ``step5_prompts.build_step5_prompts``)
  • Same OpenHands safety check before starting
  • Same chmod 750 on workspace
"""
from __future__ import annotations

import asyncio
import logging
import os
import pwd
import subprocess

from fastapi import WebSocket

from app.services.openhands_manager import openhands_manager
from app.services.claude_cli import (
    DEFAULT_ALLOWED_TOOLS,
    DEFAULT_DISALLOWED_TOOLS,
    run_claude_session,
)
from .step5_prompts import build_step5_prompts

logger = logging.getLogger(__name__)


async def execute_with_claude_cli(
    task: str,
    workspace_path: str,
    api_key: str,
    classification: dict,
    plan: str,
    websocket: WebSocket,
    *,
    user_id: str | None = None,
    stack: str | None = None,
) -> bool:
    """Run Claude Code via subprocess to implement changes.

    Returns True on success, False on failure.
    """
    # ── Safety: ensure OpenHands is dead before running Claude ──
    if await openhands_manager.is_active():
        logger.error("CRITICAL: OpenHands still active when Claude should run!")
        await openhands_manager.destroy_all()
        await asyncio.sleep(1)

    try:
        await websocket.send_json({"type": "progress", "message": "🤖 Writing code..."})
    except Exception:
        pass

    # ── Build prompts (shared with legacy SDK path) ──
    system_prompt, user_prompt = build_step5_prompts(
        task=task, plan=plan, classification=classification, stack=stack,
    )

    # ── Workspace permissions parity with the SDK path ──
    # The SDK path chowns the workspace to the lucidai user (or root) and
    # tightens to 0750. We mirror that so file ownership is consistent
    # across both paths during the transition.
    try:
        try:
            pwd_entry = pwd.getpwnam("lucidai")
            chown_user = "lucidai"
        except KeyError:
            chown_user = "root"
        subprocess.run(
            ["chown", "-R", f"{chown_user}:{chown_user}", workspace_path],
            capture_output=True,
        )
        subprocess.run(["chmod", "-R", "750", workspace_path], capture_output=True)
    except Exception as exc:
        logger.warning("step5_cli: workspace perms tweak failed (non-fatal): %s", exc)

    # ── Compute max_turns the same way the SDK path does ──
    files_estimate = max(1, int(classification.get("files_estimate", 2)))
    classifier_max = int(classification.get("max_turns", 10))
    dynamic_cap = min(20, max(4, files_estimate * 3 + 2))
    effective_max_turns = min(classifier_max, dynamic_cap)

    timeout_seconds = max(120, effective_max_turns * 30)
    model_id = str(classification.get("model_id") or classification.get("model") or "claude-sonnet-4-6")

    logger.info(
        "step5_cli: model=%s task_type=%s effective_max_turns=%d timeout=%ds",
        model_id,
        classification.get("task_type", "feature_simple"),
        effective_max_turns,
        timeout_seconds,
    )

    # ── Single attempt — the CLI has its own internal retries on transient
    # API errors. We keep this simple; future Path A work will add a
    # framework-level retry with backoff if needed.
    try:
        result = await run_claude_session(
            prompt=user_prompt,
            workspace_path=workspace_path,
            api_key=api_key,
            websocket=websocket,
            user_id=user_id,
            model=model_id,
            max_turns=effective_max_turns,
            timeout_seconds=timeout_seconds,
            append_system_prompt=system_prompt,
            allowed_tools=DEFAULT_ALLOWED_TOOLS,
            disallowed_tools=DEFAULT_DISALLOWED_TOOLS,
            phase_label="step5_cli",
        )
    except asyncio.CancelledError:
        # Surface to caller so the pipeline can clean up the session.
        raise
    except Exception as exc:
        logger.error("step5_cli: run_claude_session raised: %s", exc, exc_info=True)
        try:
            await websocket.send_json({
                "type": "warning",
                "message": f"⚠️ Claude CLI error: {exc}",
            })
        except Exception:
            pass
        return False

    # ── Surface a result message + cost summary so the user sees it ──
    if result.success:
        try:
            await websocket.send_json({
                "type": "progress",
                "message": (
                    f"✅ Code generation complete — {len(result.files_written)} file(s) written, "
                    f"{result.num_turns} turn(s), ${result.total_cost_usd:.3f}"
                ),
            })
        except Exception:
            pass
        return True

    # Failure path — log + warn the UI.
    err = result.error or "unknown_error"
    logger.warning(
        "step5_cli: failed — error=%s circuit_broken=%s files_written=%d",
        err, result.circuit_broken, len(result.files_written),
    )
    try:
        await websocket.send_json({
            "type": "warning",
            "message": (
                "⚠️ Generation stopped early — circuit breaker tripped"
                if result.circuit_broken
                else f"⚠️ Generation failed: {err}"
            ),
        })
    except Exception:
        pass
    # If the agent wrote at least one file, treat as partial success so the
    # rest of the pipeline (build verify, file_change events) still runs.
    return len(result.files_written) > 0
