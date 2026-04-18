"""
auto_bug_fixer.py — Automated bug detection for existing GitHub/GitLab repos.

Called when a user OPENS an existing repo workspace (Path B — EXISTING_REPO).
Runs a lightweight Claude Code SDK scan to detect common bugs and reports
findings as structured chat messages, then optionally auto-fixes them.

Pipeline:
  1. Scan — read key files (2-3 turns), categorise findings
  2. Report — send structured findings to frontend via WebSocket
  3. Fix   — if fixable_count > 0 AND auto_fix=True, run targeted fixes

Usage:
    from app.services.auto_bug_fixer import scan_and_report_bugs
    await scan_and_report_bugs(
        workspace_path=workspace_path,
        api_key=api_key,
        websocket=websocket,
        auto_fix=False,   # True to auto-apply fixes, False to just report
    )
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

logger = logging.getLogger("lucid.auto_bug_fixer")

_SCAN_MODEL_ID = "claude-haiku-4-5-20251001"
_FIX_MODEL_ID = "claude-sonnet-4-6"
_SCAN_MAX_TURNS = 6
_FIX_MAX_TURNS = 12
_SCAN_TIMEOUT = 90
_FIX_TIMEOUT = 180


# ── Bug Categories ─────────────────────────────────────────────────────────────

BUG_CATEGORIES = {
    "typescript": "TypeScript / type errors",
    "missing_client": "Missing 'use client' directives",
    "broken_imports": "Broken or missing imports",
    "banned_icons": "Banned lucide-react brand icons",
    "accessibility": "Accessibility issues",
    "security": "Security anti-patterns",
    "performance": "Performance issues",
}


# ── Scanner system prompt ──────────────────────────────────────────────────────

_SCAN_SYSTEM = """\
You are a senior code quality auditor specialising in Next.js, React, and TypeScript.
You scan codebases for bugs, errors, and anti-patterns in a fast, systematic way.

YOUR TASK — produce a structured bug report in this EXACT JSON format:

{
  "summary": "One sentence describing overall code health",
  "health_score": 85,
  "findings": [
    {
      "category": "typescript|missing_client|broken_imports|banned_icons|accessibility|security|performance",
      "severity": "error|warning|info",
      "file": "src/components/Button.tsx",
      "line": 12,
      "description": "Clear description of the issue",
      "fix": "Exact fix to apply (or null if complex)",
      "auto_fixable": true
    }
  ],
  "auto_fixable_count": 3,
  "critical_count": 1
}

SCANNING RULES:
1. Read at most 5 files — prioritise: package.json, tsconfig.json, src/app/*, src/components/*
2. Look ONLY for these issue types:
   - TypeScript errors (any, implicit any, type assertion abuse)
   - Missing 'use client' (file has useState/useEffect but no 'use client' at top)
   - Broken imports (importing a file that doesn't exist)
   - Banned lucide-react icons (Facebook, Instagram, Twitter, Linkedin, Youtube, Tiktok, Github brand icon)
   - Inaccessible divs acting as buttons (div with onClick but no role/aria)
   - Hardcoded secrets in source (API_KEY=, SECRET=, password= in non-.env files)
   - console.log statements left in production code
3. health_score: 100 = perfect, 70-99 = minor issues, 50-69 = moderate, <50 = critical
4. auto_fixable: true only if the fix is a simple 1-line change (add 'use client', rename import, etc.)
5. Return ONLY valid JSON — no markdown, no commentary outside the JSON
"""

_SCAN_PROMPT = """\
Scan this workspace for bugs and quality issues.

Workspace: {workspace_path}

Steps:
1. Run LS on the root to understand structure
2. Read package.json to understand dependencies
3. Read 2-3 component/page files that are likely to have issues
4. Output the structured JSON bug report

Go.
"""


# ── Fixer system prompt ────────────────────────────────────────────────────────

_FIX_SYSTEM = """\
You are a surgical code fixer. You receive a list of bugs and fix them precisely.

RULES:
- Fix ONLY the auto_fixable=true bugs from the report
- Use Edit tool for targeted line-level fixes — never rewrite whole files
- Fix one issue at a time, move to the next
- After all fixes, output: FIXES_COMPLETE: <count> issues resolved
- If a fix is unclear, skip it and output: SKIPPED: <file> — <reason>
"""

_FIX_PROMPT = """\
Fix the following auto-fixable bugs in the workspace.

Bugs to fix:
{bugs_json}

Workspace: {workspace_path}

Apply each fix using the Edit tool. Be precise and surgical.
"""


# ── Helpers ────────────────────────────────────────────────────────────────────

def _build_env(api_key: str) -> dict:
    try:
        lucidai = pwd.getpwnam("lucidai")
        home = lucidai.pw_dir
        user = "lucidai"
    except KeyError:
        home = "/root"
        user = "root"
    return {
        "ANTHROPIC_API_KEY": str(api_key).strip(),
        "HOME": home,
        "USER": user,
        "USERNAME": user,
        "LOGNAME": user,
        "PATH": f"{home}/.npm-global/bin:/usr/local/bin:/usr/bin:/bin",
        "IS_SANDBOX": "1",
    }, user


def _severity_icon(severity: str) -> str:
    return {"error": "🔴", "warning": "🟡", "info": "🔵"}.get(severity, "⚪")


async def _run_scan(workspace_path: str, api_key: str) -> dict | None:
    """Run the scanner pass; returns parsed JSON dict or None."""
    env, user = _build_env(api_key)
    subprocess.run(["chown", "-R", f"{user}:{user}", workspace_path], capture_output=True)

    options = ClaudeCodeOptions(
        cwd=str(workspace_path),
        env=env,
        model=_SCAN_MODEL_ID,
        max_turns=_SCAN_MAX_TURNS,
        permission_mode="bypassPermissions",
        allowed_tools=["Read", "Glob", "LS"],   # scan only — no writes
        disallowed_tools=["Write", "Edit", "MultiEdit", "Bash", "GitCommit", "GitPush"],
        append_system_prompt=_SCAN_SYSTEM,
    )

    prompt = _SCAN_PROMPT.format(workspace_path=workspace_path)
    raw_output = []

    try:
        async with asyncio.timeout(_SCAN_TIMEOUT):
            async for message in query(prompt=prompt, options=options):
                msg_content = getattr(message, "content", None)
                if isinstance(msg_content, list):
                    for block in msg_content:
                        btext = getattr(block, "text", None)
                        if btext:
                            raw_output.append(str(btext))
                elif isinstance(msg_content, str) and msg_content.strip():
                    raw_output.append(msg_content)
    except asyncio.TimeoutError:
        logger.warning("Bug scan timed out after %ds", _SCAN_TIMEOUT)
    except Exception as exc:
        logger.warning("Bug scan error: %s", exc)
        return None

    # Find and parse JSON from output
    full_text = "\n".join(raw_output)
    import re
    import json
    # Try to extract JSON block
    json_match = re.search(r'\{[\s\S]*"findings"[\s\S]*\}', full_text)
    if json_match:
        try:
            return json.loads(json_match.group(0))
        except json.JSONDecodeError as e:
            logger.warning("Failed to parse scan JSON: %s — raw: %s", e, full_text[:300])
    return None


async def _run_fixes(
    workspace_path: str,
    api_key: str,
    auto_fixable_bugs: list,
    websocket: WebSocket,
) -> int:
    """Apply auto-fixable bugs; returns count of fixes applied."""
    if not auto_fixable_bugs:
        return 0

    import json
    env, user = _build_env(api_key)

    options = ClaudeCodeOptions(
        cwd=str(workspace_path),
        env=env,
        model=_FIX_MODEL_ID,
        max_turns=_FIX_MAX_TURNS,
        permission_mode="bypassPermissions",
        allowed_tools=["Read", "Write", "Edit", "MultiEdit", "Glob", "LS"],
        disallowed_tools=["Bash", "GitCommit", "GitPush"],
        append_system_prompt=_FIX_SYSTEM,
    )

    prompt = _FIX_PROMPT.format(
        bugs_json=json.dumps(auto_fixable_bugs, indent=2),
        workspace_path=workspace_path,
    )

    fixes_applied = 0
    try:
        async with asyncio.timeout(_FIX_TIMEOUT):
            async for message in query(prompt=prompt, options=options):
                msg_content = getattr(message, "content", None)
                if isinstance(msg_content, list):
                    for block in msg_content:
                        bname = getattr(block, "name", None)
                        binput = getattr(block, "input", None) or {}
                        btext = getattr(block, "text", None)

                        if bname and str(bname).lower() in ("edit", "multiedit", "write"):
                            fixes_applied += 1
                            fpath = binput.get("file_path") or binput.get("path") or "file"
                            try:
                                await websocket.send_json({
                                    "type": "file_write_event",
                                    "filename": fpath,
                                    "action": "bug_fix",
                                })
                            except Exception:
                                pass

                        if btext and "FIXES_COMPLETE" in str(btext):
                            break
    except asyncio.TimeoutError:
        logger.warning("Bug fixer timed out after %ds", _FIX_TIMEOUT)
    except Exception as exc:
        logger.warning("Bug fixer error: %s", exc)

    return fixes_applied


# ── Public API ─────────────────────────────────────────────────────────────────

async def scan_and_report_bugs(
    workspace_path: str,
    api_key: str,
    websocket: WebSocket,
    auto_fix: bool = False,
) -> dict:
    """Scan repo for bugs, report findings, optionally auto-fix.

    Returns a dict:
    {
        "health_score": int,
        "total_findings": int,
        "auto_fixable_count": int,
        "fixes_applied": int,
        "summary": str,
    }
    """
    if not os.path.isdir(workspace_path):
        logger.warning("scan_and_report_bugs: workspace not found: %s", workspace_path)
        return {}

    logger.info("Starting bug scan for workspace: %s", workspace_path)

    try:
        await websocket.send_json({
            "type": "chat_message",
            "role": "agent",
            "content": "🔍 **Running code quality scan** on your repository…",
        })
    except Exception:
        pass

    report = await _run_scan(workspace_path, api_key)

    if not report:
        try:
            await websocket.send_json({
                "type": "chat_message",
                "role": "agent",
                "content": "✅ Code scan complete — no structural issues detected.",
            })
        except Exception:
            pass
        return {"health_score": 100, "total_findings": 0, "auto_fixable_count": 0, "fixes_applied": 0}

    findings = report.get("findings", [])
    health_score = report.get("health_score", 100)
    summary = report.get("summary", "Scan complete.")
    auto_fixable_count = report.get("auto_fixable_count", 0)
    critical_count = report.get("critical_count", 0)

    # ── Build structured chat message ───────────────────────────────────────────
    health_bar = "🟢" if health_score >= 85 else "🟡" if health_score >= 60 else "🔴"
    lines = [
        f"## {health_bar} Code Quality Scan Results",
        f"**Health Score:** {health_score}/100",
        f"**Summary:** {summary}",
        "",
    ]

    if findings:
        lines.append(f"**Found {len(findings)} issue(s):**")
        for f in findings[:10]:  # cap at 10 to avoid flooding chat
            icon = _severity_icon(f.get("severity", "info"))
            category = BUG_CATEGORIES.get(f.get("category", ""), f.get("category", ""))
            file_ref = f.get("file", "")
            line_ref = f" (line {f['line']})" if f.get("line") else ""
            desc = f.get("description", "")
            fix_hint = f"\n  → *Fix:* {f['fix']}" if f.get("fix") and f.get("auto_fixable") else ""
            lines.append(f"{icon} **{category}** — `{file_ref}`{line_ref}\n  {desc}{fix_hint}")
        if len(findings) > 10:
            lines.append(f"*…and {len(findings) - 10} more issues*")
    else:
        lines.append("✅ No issues found — codebase looks clean!")

    if auto_fixable_count > 0 and not auto_fix:
        lines.append(
            f"\n💡 **{auto_fixable_count} issue(s) can be auto-fixed.** "
            "Send me a message like: *\"fix the bugs you found\"* to apply them."
        )

    chat_content = "\n".join(lines)
    try:
        await websocket.send_json({
            "type": "chat_message",
            "role": "agent",
            "messageType": "bug_report",
            "content": chat_content,
            "bugReport": {
                "healthScore": health_score,
                "findings": findings,
                "autoFixableCount": auto_fixable_count,
                "criticalCount": critical_count,
            },
        })
    except Exception as e:
        logger.warning("Failed to send bug report: %s", e)

    fixes_applied = 0
    if auto_fix and auto_fixable_count > 0:
        auto_fixable_bugs = [f for f in findings if f.get("auto_fixable")]
        try:
            await websocket.send_json({
                "type": "chat_message",
                "role": "agent",
                "content": f"🔧 Auto-fixing {len(auto_fixable_bugs)} issue(s)…",
            })
        except Exception:
            pass

        fixes_applied = await _run_fixes(workspace_path, api_key, auto_fixable_bugs, websocket)

        try:
            await websocket.send_json({
                "type": "chat_message",
                "role": "agent",
                "content": (
                    f"✅ **{fixes_applied} issue(s) fixed automatically.**\n\n"
                    "The remaining issues require manual review — I can help if you ask."
                    if fixes_applied > 0
                    else "⚠️ Could not auto-fix issues — they may need manual attention."
                ),
            })
        except Exception:
            pass

    return {
        "health_score": health_score,
        "total_findings": len(findings),
        "auto_fixable_count": auto_fixable_count,
        "fixes_applied": fixes_applied,
        "summary": summary,
    }
