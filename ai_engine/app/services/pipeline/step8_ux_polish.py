"""
pipeline/step8_ux_polish.py — Post-generation UX polish pass.

Runs AFTER build verification (Phase 6) and BEFORE commit (Phase 7).
Uses Claude Code SDK for a focused audit-and-fix pass targeting:
  - Missing loading / skeleton states
  - Missing empty states on lists and tables
  - Missing hover / active / focus styles on interactive elements
  - Poor accessibility (missing aria labels, div-as-button, etc.)
  - Inconsistent spacing or colour anti-patterns (hardcoded hex / var(--color-*))

The pass is intentionally lightweight: max 6 turns, read-heavy at start,
then targeted writes only. It does NOT rewrite whole files — it patches gaps.

Usage (called from orchestrator.py):
    from .step8_ux_polish import run_ux_polish
    success = await run_ux_polish(workspace_path, api_key, websocket)
"""

from __future__ import annotations

import os
import pwd
import asyncio
import subprocess
import logging

from fastapi import WebSocket
from claude_code_sdk.query import query
from claude_code_sdk.types import ClaudeCodeOptions

logger = logging.getLogger(__name__)

# Model for UX polish — haiku is fast and cheap for targeted fixes
_POLISH_MODEL_ID = "claude-haiku-4-5-20251001"
_MAX_TURNS = 8
_TIMEOUT = 120  # seconds


_SYSTEM = """\
You are a Senior UX Engineer performing a final quality audit on a newly generated
web application. You specialize in catching the gaps that code generators miss.

YOUR MISSION — make targeted fixes in this exact priority order:

PRIORITY 1 — Loading states (most impactful)
  Find every place data is fetched (useState + useEffect, or similar) and
  ensure a skeleton shimmer or spinner is shown while loading.
  If missing, add a simple skeleton:
    {isLoading && (
      <div className="animate-pulse space-y-3">
        <div className="h-4 bg-muted rounded w-3/4" />
        <div className="h-4 bg-muted rounded w-1/2" />
      </div>
    )}

PRIORITY 2 — Empty states
  Find every list, table, or grid that can be empty. If the empty case shows
  nothing (or only hides with && short-circuit), add a proper empty state with
  an icon, title, description, and a CTA button.

PRIORITY 3 — Hover/focus styles
  Find <button>, <a>, and clickable <div>s that have NO hover: or focus-visible:
  Tailwind classes. Add at minimum:
    hover:bg-primary/90 active:scale-95 transition-all duration-200
  For icon buttons:
    hover:bg-accent rounded-full p-2 transition-colors duration-200
  For links:
    hover:text-primary transition-colors duration-150
  For cards:
    hover:shadow-lg hover:-translate-y-0.5 transition-all duration-300

PRIORITY 4 — Accessibility quick wins
  - <button> with only an icon: add aria-label="…"
  - <img> missing alt attribute: add alt="" (decorative) or meaningful alt text
  - <div onClick> that acts as a button: convert to <button> or add role="button"
    tabIndex={0} onKeyDown handler

PRIORITY 5 — Color anti-patterns
  Replace any hardcoded hex colours (#fff, #000, #3B82F6, etc.) in className
  with Tailwind semantic tokens (bg-background, text-foreground, bg-primary, etc.)
  Replace any var(--color-*) references with the equivalent Tailwind class.

RULES:
- Read a file ONCE then edit — no re-reads
- Make targeted edits (Edit tool) not full rewrites
- MAX 3 files changed — pick the highest-impact ones
- After each Edit, move on — do NOT re-read the file to verify
- STOP after 6 changes or when priorities 1-3 are addressed
- If the codebase already passes all checks, output: LGTM: no fixes needed
"""


_PROMPT_TEMPLATE = """\
Workspace: {workspace_path}

Step 1 — Discovery (read only these):
1. List the src/ or components/ directory to find component files
2. Read 2-3 of the most data-heavy components (pages, dashboards, lists)
3. Identify the top 3 gaps from the priority list

Step 2 — Fix (write/edit only):
Apply the highest-priority fixes you found.
Limit: fix at most 3 issues across at most 3 files.
Use Edit (not Write) to make targeted changes.

START NOW. Be fast and decisive.
"""


def _claude_cli_available() -> bool:
    """Return True if the `claude` CLI is installed and executable."""
    try:
        result = subprocess.run(
            ["claude", "--version"],
            capture_output=True, timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


async def run_ux_polish(
    workspace_path: str,
    api_key: str,
    websocket: WebSocket,
) -> bool:
    """Run a focused UX polish pass on a newly generated project.

    Returns True on success (even if no fixes were needed), False on hard failure.
    Skips silently if the `claude` CLI is not installed in this environment.
    """
    # Guard: skip entirely if claude CLI is not available (dev machine, CI, etc.)
    if not _claude_cli_available():
        logger.info("UX polish skipped — claude CLI not found in PATH")
        return True

    try:
        await websocket.send_json({
            "type": "progress",
            "message": "🎨 UX polish pass — checking loading states, empty states, hover effects…",
        })
    except Exception:
        pass

    try:
        lucidai = pwd.getpwnam("lucidai")
        home = lucidai.pw_dir
        user = "lucidai"
    except KeyError:
        home = "/root"
        user = "root"

    env = {
        "ANTHROPIC_API_KEY": str(api_key).strip(),
        "HOME": home,
        "USER": user,
        "USERNAME": user,
        "LOGNAME": user,
        "PATH": f"{home}/.npm-global/bin:/usr/local/bin:/usr/bin:/bin",
        "IS_SANDBOX": "1",
    }

    # Ensure ownership so claude can read/write
    subprocess.run(
        ["chown", "-R", f"{user}:{user}", workspace_path],
        capture_output=True,
    )

    options = ClaudeCodeOptions(
        cwd=str(workspace_path),
        env=env,
        model=_POLISH_MODEL_ID,
        max_turns=_MAX_TURNS,
        permission_mode="bypassPermissions",
        allowed_tools=["Read", "Write", "Edit", "MultiEdit", "Glob", "LS"],
        disallowed_tools=[
            "Bash", "GitCommit", "GitPush",
        ],
        append_system_prompt=_SYSTEM,
    )

    prompt = _PROMPT_TEMPLATE.format(workspace_path=workspace_path)

    fixes_applied = 0
    lgtm = False

    try:
        async with asyncio.timeout(_TIMEOUT):
            async for message in query(prompt=prompt, options=options):
                msg_str = str(message)

                # Detect LGTM signal
                if "LGTM" in msg_str and "no fixes" in msg_str.lower():
                    lgtm = True
                    break

                # Track edits for UI feedback
                try:
                    msg_content = getattr(message, "content", None)
                    if isinstance(msg_content, list):
                        for block in msg_content:
                            bname = getattr(block, "name", None)
                            binput = getattr(block, "input", None) or {}
                            btext = getattr(block, "text", None)

                            if bname and str(bname).lower() in ("write", "edit", "multiedit"):
                                fpath = (
                                    binput.get("file_path")
                                    or binput.get("path")
                                    or "file"
                                )
                                fixes_applied += 1
                                try:
                                    await websocket.send_json({
                                        "type": "file_write_event",
                                        "filename": fpath,
                                        "action": "ux_polish",
                                    })
                                except Exception:
                                    pass

                            elif btext and len(str(btext).strip()) > 30:
                                # Surface meaningful commentary to the chat
                                text_snippet = str(btext).strip()
                                if any(kw in text_snippet.lower() for kw in (
                                    "loading", "empty", "hover", "aria", "accessibility",
                                    "fixed", "added", "improved",
                                )):
                                    try:
                                        await websocket.send_json({
                                            "type": "chat_message",
                                            "role": "agent",
                                            "content": f"🎨 {text_snippet[:300]}",
                                        })
                                    except Exception:
                                        pass
                except Exception:
                    pass

    except asyncio.TimeoutError:
        logger.warning("UX polish timed out after %ds (non-fatal)", _TIMEOUT)
    except Exception as exc:
        logger.warning("UX polish failed (non-fatal): %s", exc)
        try:
            await websocket.send_json({
                "type": "warning",
                "message": f"⚠️ UX polish skipped: {str(exc)[:120]}",
            })
        except Exception:
            pass
        return False

    # Always success — polish is best-effort, never blocks the pipeline
    if lgtm:
        msg = "✅ UX audit passed — no gaps found"
    elif fixes_applied > 0:
        msg = f"✅ UX polish complete — {fixes_applied} improvement(s) applied"
    else:
        msg = "✅ UX polish complete"

    logger.info("UX polish done: fixes_applied=%d, lgtm=%s", fixes_applied, lgtm)
    try:
        await websocket.send_json({"type": "progress", "message": msg})
    except Exception:
        pass

    return True
