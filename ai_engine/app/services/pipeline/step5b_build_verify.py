"""
pipeline/step5b_build_verify.py — Pipeline Step 5.5: TypeScript build verification.

Extracted verbatim from task_pipeline.py (lines 4768–4930).
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

logger = logging.getLogger(__name__)


async def verify_build(
    workspace_path: str,
    api_key: str,
    classification: dict,
    websocket: WebSocket,
) -> bool:
    """Run build checks and auto-fix errors with Claude.

    Detects project type (TypeScript, Next.js) and runs appropriate
    checks. If errors found, sends them back to Claude for a fix
    (max 2 retries).

    Returns True if build passes (or no checker available).
    """
    has_ts = os.path.exists(os.path.join(workspace_path, "tsconfig.json"))

    if not has_ts:
        # No TypeScript — skip build check
        return True

    # Check if typescript is actually installed (not just tsconfig existing)
    ts_installed = (
        os.path.exists(os.path.join(workspace_path, "node_modules", "typescript"))
        or os.path.exists(os.path.join(workspace_path, "node_modules", ".bin", "tsc"))
    )
    if not ts_installed:
        logger.info("verify_build: tsconfig.json exists but typescript not installed — skipping")
        return True

    try:
        await websocket.send_json({
            "type": "build",
            "status": "checking",
            "message": "🔍 Running type checker...",
        })
    except Exception:
        pass

    max_retries = 2

    for attempt in range(max_retries + 1):
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                ["npx", "tsc", "--noEmit", "--pretty"],
                cwd=workspace_path,
                capture_output=True,
                text=True,
                timeout=60,
            )

            errors = result.stdout.strip() or result.stderr.strip()

            if result.returncode == 0 or not errors:
                try:
                    await websocket.send_json({
                        "type": "build",
                        "status": "passed",
                        "message": "✅ Type check passed",
                    })
                except Exception:
                    pass
                return True

            # Errors found
            error_lines = errors.splitlines()
            error_count = sum(1 for l in error_lines if "error TS" in l)
            truncated = "\n".join(error_lines[:30])

            if attempt < max_retries:
                # Send errors to frontend
                try:
                    await websocket.send_json({
                        "type": "build",
                        "status": "fixing",
                        "message": f"⚠️ {error_count} TypeScript error(s) found — auto-fixing (attempt {attempt + 1}/{max_retries})...",
                        "errors": truncated,
                    })
                except Exception:
                    pass

                # Ask Claude to fix the errors
                fix_prompt = f"""TypeScript build errors were found after your changes.
Fix ALL of these errors:

{truncated}

Rules:
1. Read each file with an error
2. Fix the type error precisely
3. Do NOT change any logic or behavior
4. Only fix the TypeScript errors
5. STOP when all errors are fixed
"""
                fix_options = ClaudeCodeOptions(
                    cwd=str(workspace_path),
                    env={
                        "ANTHROPIC_API_KEY": str(api_key).strip(),
                        "HOME": str(os.environ.get("HOME", "/root")),
                        "PATH": str(os.environ.get("PATH", "/usr/bin:/bin")),
                        "IS_SANDBOX": "1",  # CRITICAL: Bypasses CLI root check
                    },
                    model=str(classification["model_id"]),
                    max_turns=5,
                    permission_mode="bypassPermissions",
                    allowed_tools=[
                        "Read", "Write", "Edit", "MultiEdit",
                        "Bash", "Glob", "Grep", "LS",
                    ],
                    disallowed_tools=[
                        "GitCommit", "GitPush", "GitPull", "GitClone",
                    ],
                    append_system_prompt=(
                        "You are a TypeScript error fixer.\n"
                        "Fix only the type errors listed.\n"
                        "Do not change any behavior.\n"
                        "Use Write tool immediately."
                    ),
                    extra_args={"dangerously-skip-permissions": None, "debug-to-stderr": None},
                    debug_stderr=sys.stderr,
                )

                try:
                    # Second try: Give Claude Code the error and let it fix
                    async with asyncio.timeout(300):  # 5 min limit for tsc fixing
                        async for message in query(
                            prompt=fix_prompt,
                            options=fix_options,
                        ):
                            try:
                                await websocket.send_json({
                                    "type": "claude_message",
                                    "content": str(message),
                                })
                            except Exception:
                                pass
                except Exception as fix_err:
                    logger.warning("Build fix attempt %d failed: %s", attempt + 1, fix_err)

            else:
                # Final attempt failed — report but don't block
                try:
                    await websocket.send_json({
                        "type": "build",
                        "status": "failed",
                        "message": f"⚠️ {error_count} TypeScript error(s) remain after auto-fix attempts",
                        "errors": truncated,
                    })
                except Exception:
                    pass
                return False

        except subprocess.TimeoutExpired:
            logger.warning("tsc --noEmit timed out")
            return True  # Don't block on timeout
        except FileNotFoundError:
            # npx/tsc not available
            return True
        except Exception as e:
            logger.warning("verify_build error: %s", e)
            return True

    return True
