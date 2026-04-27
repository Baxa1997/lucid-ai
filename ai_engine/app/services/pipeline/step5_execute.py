"""
pipeline/step5_execute.py — Pipeline Step 5: Execute with Claude Code SDK.

Extracted verbatim from task_pipeline.py (lines 2935–4721).
Zero logic changes — only import paths updated.
"""

from __future__ import annotations

import os
import sys
import json
import signal
import asyncio
import subprocess
import logging
import pwd

from fastapi import WebSocket
from claude_code_sdk.query import query
from claude_code_sdk.types import ClaudeCodeOptions

from app.services.openhands_manager import openhands_manager
from .package_manager import detect_package_manager, _pm_install_cmd, _pm_env

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════

def _kill_claude_subprocesses() -> None:
    """Best-effort SIGKILL of any orphaned claude/node child processes.

    The claude_code_sdk sends SIGTERM via transport.close() when the async
    generator is cancelled, but the Node.js process (and its children) may
    survive SIGTERM.  This function sends SIGKILL to any direct children of
    this process that look like claude/node workers.

    Called synchronously from a CancelledError handler so it must not await.
    """
    try:
        current_pid = os.getpid()
        result = subprocess.run(
            ["pgrep", "-P", str(current_pid)],
            capture_output=True, text=True, timeout=2,
        )
        for pid_str in result.stdout.strip().splitlines():
            pid_str = pid_str.strip()
            if not pid_str:
                continue
            try:
                pid = int(pid_str)
                os.kill(pid, signal.SIGKILL)
                logger.debug("Force-killed orphaned subprocess PID %d after cancellation", pid)
            except (ProcessLookupError, PermissionError, ValueError):
                pass
    except Exception as exc:
        logger.debug("_kill_claude_subprocesses: %s", exc)


# ═══════════════════════════════════════════════════════════════
#  STEP 5 — Execute with Claude (edit-mode)
# ═══════════════════════════════════════════════════════════════

async def execute_with_claude(
    task: str,
    workspace_path: str,
    api_key: str,
    classification: dict,
    plan: str,
    websocket: WebSocket,
) -> bool:
    """Run Claude Code SDK to implement changes.

    CRITICAL: OpenHands must be completely dead before this.
    Checks openhands_manager.is_active() before starting.

    Returns True on success, False on failure.
    """
    # ── Safety check: ensure OpenHands is not active ──────
    if await openhands_manager.is_active():
        logger.error("CRITICAL: OpenHands still active when Claude should run!")
        await openhands_manager.destroy_all()
        await asyncio.sleep(1)

    try:
        await websocket.send_json({
            "type": "progress",
            "message": "🤖 Writing code...",
        })
    except Exception:
        pass

    task_type = classification.get("task_type", "feature_simple")
    model_name = classification.get("model", "sonnet")

    # ── Build task-type-specific system + focused prompt ───
    if task_type == "ui_simple":
        system = (
            "You are a surgical code editor.\n"
            "You make EXACTLY the change requested.\n"
            "Nothing more. Nothing less.\n"
            "Read the specific file once.\n"
            "Use Write tool immediately.\n"
            "Make the minimal correct change.\n"
            "Verify by reading file after.\n"
            "Stop. Do not touch other files."
        )
        prompt = f"""SURGICAL TASK: {task}

Plan from analysis:
{plan}

EXECUTE:
1. Read ONLY the file mentioned in plan
2. Find EXACTLY the element to change
3. Use Write tool to make change
4. Read file to verify change is there

CONSTRAINTS TO CHECK BEFORE EDITING:
- Read the FULL component file first
- Check all input field types 
  (email, text, number, password)
- Check existing validation rules
- Check required fields
- If adding default values:
  email field → use valid@email.com format
  text field → any string works
  number field → use numbers only
- If user request conflicts with field type:
  resolve the conflict in your implementation
- Never break existing validation
- Never leave invalid values in typed fields

5. STOP

Do not modify anything else.
Do not improve other things.
Just do the exact task.
"""

    elif task_type == "ui_complex":
        system = (
            "You are an expert UI engineer at a top tier tech company.\n"
            "You create beautiful, modern interfaces.\n"
            "You make COMPREHENSIVE changes.\n"
            "You read all related files first.\n"
            "You use Write tool boldly.\n"
            "You make the UI dramatically better.\n"
            "You follow existing code patterns.\n"
            "You verify your work."
        )
        prompt = f"""UI REDESIGN TASK: {task}

Plan:
{plan}

EXECUTE COMPREHENSIVELY:
1. Read ALL related UI files
2. Read tailwind config and global styles
3. Use Write tool to make bold changes:
   - Complete color scheme if needed
   - Modern layout and spacing
   - Professional typography
   - Clean component structure
   - Proper responsive design
4. Make it look like a $10M startup
5. Verify all files compile correctly

CONSTRAINTS TO CHECK BEFORE EDITING:
- Read the FULL component file first
- Check all input field types 
  (email, text, number, password)
- Check existing validation rules
- Check required fields
- If adding default values:
  email field → use valid@email.com format
  text field → any string works
  number field → use numbers only
- If user request conflicts with field type:
  resolve the conflict in your implementation
- Never break existing validation
- Never leave invalid values in typed fields

6. STOP when fully redesigned

Be bold. Make dramatic improvements.
Do not make small safe changes.
"""

    elif task_type == "bug_simple":
        system = (
            "You are an expert debugger.\n"
            "You find bugs quickly and fix precisely.\n"
            "You read error messages carefully.\n"
            "You make minimal correct fixes.\n"
            "You verify the fix makes logical sense."
        )
        prompt = f"""BUG FIX TASK: {task}

Plan:
{plan}

EXECUTE:
1. Read the specific file with bug
2. Find the exact bug location
3. Understand why it is wrong
4. Use Write tool to fix it precisely
5. Run: npx tsc --noEmit if TypeScript
6. Fix any TypeScript errors found

CONSTRAINTS TO CHECK BEFORE EDITING:
- Read the FULL component file first
- Check all input field types 
  (email, text, number, password)
- Check existing validation rules
- Check required fields
- If adding default values:
  email field → use valid@email.com format
  text field → any string works
  number field → use numbers only
- If user request conflicts with field type:
  resolve the conflict in your implementation
- Never break existing validation
- Never leave invalid values in typed fields

7. STOP

Fix only the bug. Nothing else.
"""

    elif task_type == "bug_complex":
        system = (
            "You are a senior debugging expert.\n"
            "You trace bugs to their ROOT CAUSE.\n"
            "You read the entire execution path.\n"
            "You check: component → hook → API → backend.\n"
            "You fix root cause not symptoms.\n"
            "You verify the fix is logically correct.\n"
            "You check for related bugs."
        )
        prompt = f"""COMPLEX BUG FIX: {task}

Plan:
{plan}

EXECUTE THOROUGHLY:
1. Read ALL files in execution path
2. Trace: component → state → API → response
3. Find ROOT CAUSE (not symptom)
4. Check these common causes:
   - Null/undefined values
   - Wrong async/await
   - Missing error handling
   - Type mismatches
   - Wrong dependencies array
   - Stale closures
5. Fix root cause completely
6. Run type checker after fix
7. Verify logic is correct

CONSTRAINTS TO CHECK BEFORE EDITING:
- Read the FULL component file first
- Check all input field types 
- If user request conflicts with field type, resolve it
- Never break existing validation

8. STOP

Find and fix the REAL problem.
"""

    elif task_type == "feature_simple":
        system = (
            "You are a clean code writer.\n"
            "You write simple focused features.\n"
            "You follow existing patterns exactly.\n"
            "You reuse existing components.\n"
            "You write minimal clean code."
        )
        prompt = f"""SIMPLE FEATURE: {task}

Plan:
{plan}

EXECUTE:
1. Read existing similar code first
2. Follow exact same patterns
3. Reuse existing components/utilities
4. Write minimal clean implementation
5. Handle basic error cases

CONSTRAINTS TO CHECK BEFORE EDITING:
- Read the FULL component file first
- Check all input field types
- If adding default values, match the field type
- Never break existing validation

6. STOP when feature works

Keep it simple. Match existing style.
"""

    elif task_type == "feature_complex":
        system = (
            "You are a senior full stack engineer.\n"
            "You build complete production features.\n"
            "You handle all edge cases.\n"
            "You write clean maintainable code.\n"
            "You follow existing architecture.\n"
            "You implement end to end."
        )
        prompt = f"""COMPLEX FEATURE: {task}

Plan:
{plan}

EXECUTE COMPLETELY:
1. Read existing codebase patterns
2. Plan the full implementation
3. Implement ALL parts:
   - UI components
   - State management
   - API calls/routes
   - Error handling
   - Loading states
   - Edge cases
4. Follow existing code style exactly
5. Test logic mentally

CONSTRAINTS TO CHECK BEFORE EDITING:
- Read the FULL component file first
- Check all input field types
- If user request conflicts with field type, resolve it
- Never break existing validation

6. STOP when fully working

Build it complete and production ready.
"""

    elif task_type == "auth":
        system = (
            "You are a security expert.\n"
            "You handle auth with extreme care.\n"
            "You never expose sensitive data.\n"
            "You follow security best practices.\n"
            "You test edge cases carefully."
        )
        prompt = f"""AUTH TASK: {task}

Plan:
{plan}

EXECUTE CAREFULLY:
1. Read ALL auth related files
2. Understand complete auth flow
3. Make secure correct changes
4. Check: tokens, sessions, middleware
5. Verify RLS policies if Supabase
6. Never expose secrets or tokens
7. Handle all error cases
8. STOP when secure and working

Security first. Never compromise.
"""

    elif task_type == "database":
        system = (
            "You are a database expert.\n"
            "You write safe database changes.\n"
            "You always check existing schema first.\n"
            "You write proper migrations.\n"
            "You handle null cases.\n"
            "You verify foreign keys."
        )
        prompt = f"""DATABASE TASK: {task}

Plan:
{plan}

EXECUTE SAFELY:
1. Read existing schema first
2. Understand current data structure
3. Write safe migration/changes
4. Check RLS policies
5. Handle null and edge cases
6. Verify foreign key relationships
7. STOP when safe and correct

Data safety is paramount.
"""

    elif task_type == "refactor":
        system = (
            "You are a clean code expert.\n"
            "You improve code quality carefully.\n"
            "You maintain exact same behavior.\n"
            "You improve readability and structure.\n"
            "You follow existing patterns."
        )
        prompt = f"""REFACTOR TASK: {task}

Plan:
{plan}

EXECUTE CAREFULLY:
1. Read all files to refactor
2. Understand current behavior
3. Refactor while keeping same behavior
4. Improve: naming, structure, reuse
5. Run type checker after
6. Verify nothing is broken
7. STOP when cleaner and correct

Same behavior. Cleaner code.
"""

    else:
        system = (
            "You are an expert senior engineer.\n"
            "You WRITE CODE. You do not just analyze.\n"
            "Use Write/Edit tools immediately.\n"
            "Make complete thorough changes.\n"
            "Verify your work after writing.\n"
            "Do not stop until task is fully done."
        )
        prompt = f"""Task: {task}

Plan:
{plan}

USE THE WRITE TOOL NOW.
Implement this completely.
Stop when fully done.
"""

    # ── Anti-loop directive ────────────────────────────────
    anti_loop = (
        "\n\nTOOL USE CONTRACT — READ BEFORE ACTING\n\n"
        "You are an autonomous software engineer operating inside a real git repository.\n"
        "You have access to Read, Glob, LS, Grep, Write, Edit, MultiEdit, and Bash tools.\n\n"
        "ABSOLUTE RULES — violating any rule means task failure:\n\n"
        "RULE 1 — READ CAP:\n"
        "You may call Read, Glob, LS, or Grep a maximum of 3 times in a row.\n"
        "After 3 consecutive read-type calls with no write or edit between them,\n"
        "you MUST immediately either:\n"
        "  a) Make a code change using Write, Edit, or MultiEdit, OR\n"
        "  b) Output exactly: STALLED: <one sentence explaining why>\n"
        "     Then stop completely. Do not continue.\n\n"
        "RULE 2 — NO REPEAT READS:\n"
        "If you have already read a file, do not read it again under any circumstance.\n"
        "Its content is already in your context. Use it.\n\n"
        "RULE 3 — NO VERIFICATION READS:\n"
        "After writing or editing a file, do NOT re-read it to verify your changes.\n"
        "Trust your own output. Proceed to the next step immediately.\n\n"
        "RULE 4 — EDIT BEFORE EXPLORING:\n"
        "If you already know which file needs to change, edit it first.\n"
        "Only read additional files if the edit requires understanding\n"
        "a dependency you have not yet seen.\n\n"
        "RULE 5 — ONE FILE AT A TIME:\n"
        "Do not bulk-read every file in a directory speculatively.\n"
        "Read only what is directly needed for the next action.\n\n"
        "These rules are non-negotiable and override any other instinct\n"
        "to \"explore more\" or \"double check\". Act like a senior engineer\n"
        "who has already seen this codebase. Be decisive.\n\n"
        "RULE 6 — ICON SAFETY:\n"
        "lucide-react does NOT export brand/social icons (Facebook, Instagram, Twitter, Linkedin, Youtube, Tiktok, Github).\n"
        "These will cause 'Unsupported Server Component type: undefined' on build.\n"
        "For social icons, create inline SVG components instead.\n\n"
        "RULE 7 — 'use client' (Next.js App Router):\n"
        "EVERY .jsx/.tsx file that uses React hooks (useState, useEffect, useRef, useCallback),\n"
        "event handlers (onClick, onChange), browser APIs (window, document), or client libraries\n"
        "(framer-motion) MUST have 'use client' as the VERY FIRST line. Missing it crashes the build.\n"
        "When in doubt, ADD IT. It never hurts.\n"
    )

    logger.info(
        "execute_with_claude: model=%s task_type=%s max_turns=%s api_key_prefix=%s",
        model_name, task_type,
        classification.get("max_turns", 10),
        str(api_key)[:15],
    )

    try:
        lucidai = pwd.getpwnam('lucidai')
        home = lucidai.pw_dir
        user = 'lucidai'
    except KeyError:
        home = '/root'
        user = 'root'

    env = {
        "ANTHROPIC_API_KEY": str(api_key).strip(),
        "HOME": home,
        "USER": user,
        "USERNAME": user,
        "LOGNAME": user,
        "PATH": f"{home}/.npm-global/bin:/usr/local/bin:/usr/bin:/bin",
        "IS_SANDBOX": "1",
    }

    subprocess.run(
        ['chown', '-R', f'{user}:{user}', workspace_path],
        capture_output=True
    )

    # chmod 750 — owner+group only, no world read
    try:
        subprocess.run(["chmod", "-R", "750", str(workspace_path)], capture_output=True)
        logger.info("chmod -R 750 applied to %s", workspace_path)
    except Exception as e:
        logger.error("chmod failed: %s", e)

    # Tighten max_turns: Gemini's files_estimate is a better predictor of
    # how many turns the agent actually needs than the old blanket
    # developer-tier floor. Each turn is a Read/Edit/Write iteration, so
    # ~3 turns per file plus 2 slack turns covers the happy path. We clamp
    # to [4, 20] so small edits can't spin up 25-turn sessions and huge
    # tasks still get a reasonable ceiling.
    files_estimate = max(1, int(classification.get("files_estimate", 2)))
    classifier_max = int(classification.get("max_turns", 10))
    dynamic_cap = min(20, max(4, files_estimate * 3 + 2))
    effective_max_turns = min(classifier_max, dynamic_cap)

    options = ClaudeCodeOptions(
        cwd=str(workspace_path),
        env=env,
        model=str(classification["model_id"]),
        max_turns=effective_max_turns,
        permission_mode="bypassPermissions",
        allowed_tools=["Read", "Write", "Edit", "MultiEdit", "Bash", "Glob", "Grep", "LS"],
        disallowed_tools=[
            "GitCommit", "GitPush", "GitPull", "GitClone",
            "Bash(git commit*)", "Bash(git push*)", "Bash(rm -rf*)",
        ],
        append_system_prompt=system + anti_loop,
    )
    logger.info(
        "execute_with_claude: effective_max_turns=%d (classifier=%d, files_estimate=%d)",
        effective_max_turns, classifier_max, files_estimate,
    )

    max_turns = effective_max_turns
    timeout_seconds = max(120, max_turns * 30)
    max_retries = 2
    last_error = None

    for attempt in range(max_retries):
        try:
            consecutive_reads = 0
            max_consecutive_reads = 6
            has_written = False

            async with asyncio.timeout(timeout_seconds):
                async for message in query(prompt=prompt, options=options):
                    msg_str = str(message)
                    msg_lower = msg_str.lower()

                    is_read_tool = any(t in msg_lower for t in (
                        "tool_use: read", "tool_use: glob", "tool_use: ls",
                        "tool_use: grep", "'read'", "'glob'", "'ls'", "'grep'",
                    ))
                    is_write_tool = any(t in msg_lower for t in (
                        "tool_use: write", "tool_use: edit", "tool_use: multiedit",
                        "tool_use: bash", "'write'", "'edit'", "'multiedit'", "'bash'",
                    ))

                    if is_write_tool:
                        consecutive_reads = 0
                        has_written = True
                    elif is_read_tool:
                        consecutive_reads += 1

                    if consecutive_reads >= max_consecutive_reads:
                        logger.warning(
                            "CIRCUIT BREAKER: Claude read %d times without writing — forcing stop",
                            consecutive_reads,
                        )
                        try:
                            await websocket.send_json({
                                "type": "warning",
                                "message": "⚠️ Agent was reading files in a loop without making changes. Stopping to prevent wasted resources.",
                            })
                        except Exception:
                            pass
                        break

                    # Parse SDK message for structured events
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
                                        or ""
                                    )
                                    if fpath:
                                        # Inline content read so the frontend
                                        # diff viewer has the post-write text.
                                        # Best-effort: never fail the pipeline
                                        # for an IO hiccup.
                                        _payload = {
                                            "type": "file_write_event",
                                            "filename": fpath,
                                            "action": str(bname).lower(),
                                            "phase": "step5_execute",
                                        }
                                        try:
                                            import os as _os_inline
                                            _full = (
                                                fpath if _os_inline.path.isabs(fpath)
                                                else _os_inline.path.join(str(workspace_path), fpath)
                                            )
                                            if _os_inline.path.isfile(_full):
                                                _sz = _os_inline.path.getsize(_full)
                                                _payload["size"] = _sz
                                                if _sz <= 256 * 1024:
                                                    with open(_full, "r", encoding="utf-8", errors="replace") as _fh:
                                                        _payload["content"] = _fh.read()
                                                else:
                                                    _payload["content_truncated"] = True
                                        except Exception:
                                            pass
                                        await websocket.send_json(_payload)
                                elif btext and len(str(btext).strip()) > 20:
                                    await websocket.send_json({
                                        "type": "chat_message",
                                        "role": "agent",
                                        "content": str(btext).strip()[:800],
                                    })
                    except Exception:
                        pass

                    try:
                        await websocket.send_json({
                            "type": "claude_message",
                            "content": msg_str
                        })
                    except Exception:
                        pass

            if consecutive_reads >= max_consecutive_reads and not has_written:
                return False

            return True

        except asyncio.CancelledError:
            _kill_claude_subprocesses()
            raise

        except Exception as e:
            last_error = e
            exit_code = getattr(e, 'returncode', None)
            logger.warning(
                "Claude attempt %d/%d failed (exit_code=%s): %s",
                attempt + 1, max_retries, exit_code, str(e),
            )
            if attempt < max_retries - 1:
                try:
                    await websocket.send_json({
                        "type": "progress",
                        "message": f"⚠️ Retrying... (attempt {attempt + 2}/{max_retries})"
                    })
                except Exception:
                    pass
                await asyncio.sleep(2)
                continue

            try:
                await websocket.send_json({
                    "type": "error",
                    "message": f"❌ Claude failed after {max_retries} attempts: {str(last_error)[:200]}"
                })
            except Exception:
                pass
            return False


# ═══════════════════════════════════════════════════════════════
#  STEP 5 (NEW) — Batched project generation for scratch mode
# ═══════════════════════════════════════════════════════════════

async def execute_project_in_batches(
    workspace_path: str,
    task: str,
    api_key: str,
    classification: dict,
    plan_text: str,
    websocket: WebSocket,
    stack: str = "",
) -> bool:
    """Execute new project generation in intelligent batches (file-by-file).

    Splits the plan into per-file calls, executing each as a separate
    Claude call. Benefits:
    - No timeout on large projects
    - User sees progress file-by-file
    - If a batch fails, others still succeed
    - Retry failed files with simpler prompts

    Returns True if ≥60% of files were created.
    """
    # ── Load plan.json from workspace ─────────────────────
    plan_path = os.path.join(workspace_path, ".lucid", "plan.json")
    spec_path = os.path.join(workspace_path, ".lucid", "spec.md")

    plan_data = {}
    try:
        with open(plan_path, "r") as f:
            plan_data = json.loads(f.read())
    except Exception as e:
        logger.warning("Could not read plan.json: %s — using plan_text", e)
        try:
            plan_data = json.loads(plan_text)
        except Exception:
            plan_data = {"files": []}

    spec_content = ""
    try:
        with open(spec_path, "r") as f:
            spec_content = f.read()
    except Exception:
        spec_content = ""

    files = plan_data.get("files", [])
    if not files:
        logger.warning("No files in plan — falling back to single-call execution")
        return False

    # ── Resolve stack (framework) ──────────────────────────
    if not stack:
        stack = plan_data.get("stack", "")
    if not stack:
        import re as _re_stk
        _hdr = next(
            (ln for ln in (task or "")[:2000].split("\n") if "[LUCID_PROJECT]" in ln),
            "",
        )
        _m = _re_stk.search(r"stack=(\S+)", _hdr)
        stack = _m.group(1).strip() if _m else ""
    logger.info("execute_project_in_batches: stack=%r", stack)

    theme_block = json.dumps(plan_data.get("theme", {}), indent=2)

    # ── Install new packages from plan (packageUpdates) ───
    pkg_updates = plan_data.get("packageUpdates", {})
    new_deps = pkg_updates.get("dependencies", {})
    if new_deps and isinstance(new_deps, dict):
        pkg_names = [f"{k}@{v}" if v and v != "latest" else k for k, v in new_deps.items()]
        if pkg_names:
            pm = detect_package_manager(workspace_path, "npm")
            try:
                await websocket.send_json({
                    "type": "progress",
                    "message": f"📦 Installing {len(pkg_names)} new package(s) via {pm}...",
                })
                pkg_result = await asyncio.to_thread(
                    subprocess.run,
                    _pm_install_cmd(pm, pkg_names),
                    cwd=workspace_path,
                    capture_output=True,
                    text=True,
                    timeout=90,
                    env=_pm_env(pm),
                )
                if pkg_result.returncode == 0:
                    logger.info("Installed %d new packages via %s: %s", len(pkg_names), pm, pkg_names)
                else:
                    logger.warning("%s install packages failed: %s", pm, (pkg_result.stderr or "")[:200])
            except Exception as pkg_err:
                logger.warning("Failed to install packageUpdates (non-fatal): %s", pkg_err)

    # ── Assign execution order ─────────────────────────────
    PRIORITY_KEYWORDS = {
        1: ["globals.css", "index.css", "style.css", "tailwind", "config", "types", "constants", "utils", "helpers", "lib", "mock", "data", "context"],
        2: ["hook", "service", "api", "store", "provider"],
        3: ["button", "input", "modal", "table", "badge", "card", "avatar", "tooltip", "dropdown",
            "tabs", "select", "checkbox", "radio", "switch", "alert", "toast", "skeleton"],
        4: ["sidebar", "navbar", "header", "footer", "nav", "menu", "breadcrumb", "topbar", "layout"],
    }

    for f in files:
        if "priority" not in f:
            path_lower = f.get("path", "").lower()
            name_lower = os.path.basename(path_lower)
            assigned = 5
            for prio, keywords in PRIORITY_KEYWORDS.items():
                if any(kw in name_lower or kw in path_lower for kw in keywords):
                    assigned = prio
                    break
            f["priority"] = assigned

    files.sort(key=lambda f: f.get("priority", 5))

    total_files_expected = len(files)
    total_files_created = 0
    completed_files = 0

    logger.info("execute_project_in_batches (file-by-file): %d files, stack=%r", total_files_expected, stack)

    # ── Build framework rules block ─────────────────────────
    _stk = stack.lower() if stack else ""
    _comp_base = plan_data.get("_comp_base", "src/components")
    if "nextjs" in _stk or "next" in _stk:
        FRAMEWORK_RULES = (
            "## FRAMEWORK: Next.js 14 (App Router) + Tailwind CSS + shadcn/ui — READ THIS FIRST\n"
            f"- Components: {_comp_base}/ (this is the ACTUAL component path in this template)\n"
            f"- Section components: {_comp_base}/sections/ (each page section is its own file)\n"
            "- Layout components: already exist in the template — DO NOT create new Navbar.jsx or Footer.jsx\n"
            "- Root layout: src/app/layout.js (modify ONLY metadata and fonts — NO navigation components)\n"
            "- Styles: src/app/globals.css (ALREADY EXISTS — uses Tailwind + shadcn/ui HSL variables)\n"
            "- DO NOT create src/App.jsx — this file does NOT exist in Next.js\n"
            "- DO NOT use ReactDOM.render or BrowserRouter — Next.js handles routing\n"
            "- DO NOT create new layout.js files — the template already has the correct layout structure\n"
            "- DO NOT create Navbar.jsx, Footer.jsx, Header.jsx — the template already has layout components\n"
            "- Add 'use client' at the top of any COMPONENT that uses useState, useEffect, or onClick\n"
            "- Page files (page.js) should NOT have 'use client' — they are server components\n"
            "- Routing: import Link from 'next/link' | import { useRouter, usePathname } from 'next/navigation'\n"
            "- Images: import Image from 'next/image'\n"
            "- IMPORTS: Always use RELATIVE paths (e.g. '../components/Foo', './components/sections/Bar'). Do NOT use @/ alias\n\n"
            "## STYLING: Tailwind CSS + shadcn/ui (MANDATORY)\n"
            "- ALWAYS use Tailwind utility classes in className — NEVER use inline style={}\n"
            "- Colors (shadcn HSL system): bg-primary, text-primary-foreground, bg-secondary, text-secondary-foreground, "
            "bg-muted, text-muted-foreground, bg-accent, bg-card, text-card-foreground, bg-background, text-foreground, bg-destructive, border-border\n"
            "- Typography: text-sm, text-base, text-lg, text-xl, text-2xl, text-3xl, text-4xl, text-5xl, font-medium, font-semibold, font-bold, tracking-tight\n"
            "- Spacing: p-4, p-6, p-8, px-6, py-12, py-16, py-20, py-24, gap-4, gap-6, gap-8, space-y-4, space-y-6\n"
            "- Layout: flex, grid, grid-cols-1, md:grid-cols-2, lg:grid-cols-3, max-w-7xl, mx-auto, container\n"
            "- Responsive: sm:, md:, lg:, xl: prefixes (mobile-first)\n"
            "- Rounded: rounded-lg, rounded-xl, rounded-2xl (uses --radius variable)\n"
            "- Shadows: shadow-sm, shadow-md, shadow-lg, shadow-xl\n"
            "- Hover: hover:bg-primary/90, hover:shadow-lg, hover:-translate-y-1, transition-all, duration-300\n"
            "- Gradients: bg-gradient-to-r, from-primary, to-primary/80\n"
            "- NEVER use var(--color-primary) or var(--color-bg) — those DON'T EXIST\n"
            "- NEVER use inline style={} for colors, spacing, or layout — always Tailwind classes\n"
            "- NEVER use hardcoded hex colors like #fff or #000 — use Tailwind's semantic colors\n"
        )
    elif "vue" in _stk:
        _is_vue_admin = os.path.isfile(os.path.join(workspace_path, "src", "components", "layout", "MainLayout.vue"))
        if _is_vue_admin:
            FRAMEWORK_RULES = (
                "## FRAMEWORK: Vue 3 + Vite Admin Panel — READ THIS FIRST\n"
                "- Entry: src/main.js → src/App.vue → src/router/index.js\n"
                "- Layout: src/components/layout/MainLayout.vue — DO NOT RECREATE\n"
                "- Routes: src/router/routes.js (children of MainLayout) — ADD new page routes here\n"
                "- Pages: src/pages/ or src/features/<domain>/pages/ (one .vue file per page)\n"
                "- DO NOT create Navbar.vue, Footer.vue, Sidebar.vue — MainLayout.vue handles all navigation\n"
                "- Composition API: always use <script setup>\n"
                "- IMPORTS: Use @/ alias (configured in vite.config.js)\n"
            )
        else:
            FRAMEWORK_RULES = (
                "## FRAMEWORK: Vue 3 + Vite — READ THIS FIRST\n"
                "- Root: src/App.vue (ALREADY EXISTS)\n"
                "- Entry: src/main.ts (ALREADY EXISTS — do not recreate)\n"
                "- Routing: Vue Router — useRouter(), useRoute(), <RouterLink>\n"
                "- Composition API: always use <script setup lang=\"ts\">\n"
                "- Styles: src/assets/main.css (ALREADY EXISTS)\n"
            )
    else:
        _is_react_admin = os.path.isfile(os.path.join(workspace_path, "src", "components", "layout", "MainLayout.jsx"))
        if _is_react_admin:
            FRAMEWORK_RULES = (
                "## FRAMEWORK: Vite + React Admin Panel — READ THIS FIRST\n"
                "- Entry: src/main.jsx → src/App.jsx → src/router/index.jsx\n"
                "- Layout: src/components/layout/MainLayout.jsx — DO NOT RECREATE\n"
                "- Routes: src/router/routes.jsx (privateRoutes array) — ADD new page routes here\n"
                "- Pages: src/pages/ or src/features/<domain>/pages/ (one .jsx file per page)\n"
                "- DO NOT create Navbar.jsx, Footer.jsx — MainLayout.jsx handles all navigation\n"
                "- Routing: react-router-dom (Outlet, NavLink, useNavigate, useParams)\n"
                "- IMPORTS: Use @/ alias (configured in vite.config.js)\n"
            )
        else:
            FRAMEWORK_RULES = (
                "## FRAMEWORK: Vite + React — READ THIS FIRST\n"
                "- Entry: src/main.jsx (ALREADY EXISTS — do not recreate)\n"
                "- Root with routes: src/App.jsx\n"
                "- Routing: react-router-dom — BrowserRouter, Routes, Route, Link, useNavigate\n"
                "- Global styles: src/index.css (ALREADY EXISTS)\n"
            )

    # ── Build environment ──────────────────────────────────
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

    model_id = str(classification.get("model_id", "claude-sonnet-4-6"))

    def _build_tree(wp: str) -> str:
        existing: list[str] = []
        for root, dirs, _ff in os.walk(wp):
            dirs[:] = [d for d in dirs if d not in {".git", "node_modules", "__pycache__", ".lucid", ".claude"}]
            for f in _ff:
                existing.append(os.path.relpath(os.path.join(root, f), wp))
        return "\n".join(sorted(existing)) if existing else "(empty)"

    file_tree = _build_tree(workspace_path)
    spec_excerpt = spec_content[:2500] if spec_content else ""
    theme_summary = json.dumps(plan_data.get("theme", {}), indent=2)

    # ── Load TEMPLATE_MANIFEST.md ────────────────────────────
    _manifest_block = ""
    _manifest_path = os.path.join(workspace_path, "TEMPLATE_MANIFEST.md")
    if os.path.isfile(_manifest_path):
        try:
            with open(_manifest_path, "r", errors="replace") as _mf:
                _manifest_content = _mf.read()[:3000]
            _manifest_block = (
                "\n## TEMPLATE MANIFEST (import ONLY from these paths):\n"
                f"{_manifest_content}\n"
            )
        except Exception:
            pass

    # ── Load knowledge context ────────────────────────────────
    _knowledge_context = ""
    try:
        from knowledge.loader import build_knowledge_context
        _kc_result = build_knowledge_context(task, stack)
        if isinstance(_kc_result, dict):
            _knowledge_context = _kc_result.get("full_context", "")
        elif isinstance(_kc_result, str):
            _knowledge_context = _kc_result
        if _knowledge_context:
            _knowledge_context = f"\n## ARCHITECTURE PATTERNS (follow these exactly):\n{_knowledge_context[:2000]}\n"
    except Exception:
        pass

    # ── PRE-CREATE stub files ─────────────────────────────────
    for file_spec in files:
        fp = file_spec.get("path", "")
        direct = file_spec.get("_direct_content")
        if not fp:
            continue
        abs_fp = os.path.join(workspace_path, fp)
        os.makedirs(os.path.dirname(abs_fp), exist_ok=True)

        needs_stub = (
            ("/sections/" in fp or "/layout/" in fp or "/components/" in fp)
            and not direct
            and not os.path.isfile(abs_fp)
            and (fp.endswith(".jsx") or fp.endswith(".vue") or fp.endswith(".tsx"))
        )
        if needs_stub:
            comp_name = os.path.splitext(os.path.basename(fp))[0]
            ext = os.path.splitext(fp)[1]
            if ext == ".vue":
                stub = (
                    f"<template>\n"
                    f"  <section class=\"{comp_name.lower()}-section\">\n"
                    f"    <div class=\"container\">\n"
                    f"      <h2>{comp_name}</h2>\n"
                    f"      <p>Loading content...</p>\n"
                    f"    </div>\n"
                    f"  </section>\n"
                    f"</template>\n\n"
                    f"<script setup>\n"
                    f"// {comp_name} — will be overwritten by AI\n"
                    f"</script>\n"
                )
            else:
                stub = (
                    f"'use client'\n\n"
                    f"export function {comp_name}() {{\n"
                    f"  return (\n"
                    f"    <section className=\"py-16 px-8 text-center\">\n"
                    f"      <h2 className=\"text-2xl font-bold\">{comp_name}</h2>\n"
                    f"      <p className=\"text-muted-foreground mt-2\">Loading content...</p>\n"
                    f"    </section>\n"
                    f"  )\n"
                    f"}}\n\n"
                    f"export default {comp_name}\n"
                )
            with open(abs_fp, "w") as _sf:
                _sf.write(stub)
            logger.info("Pre-created stub: %s", fp)

    file_tree = _build_tree(workspace_path)

    # ── Sequential file-by-file execution ─────────────────────
    anti_loop_block = (
        "\n\nTOOL USE CONTRACT:\n"
        "- Read a file max ONCE. Never re-read.\n"
        "- For NEW PROJECT batches: Read key template files ONCE to understand structure, then WRITE your files.\n"
        "- Never re-read a file you already read.\n"
        "- After reading, WRITE immediately.\n"
        "- Do NOT verify by re-reading written files.\n"
        "- STOP when the file is created.\n"
    )

    async def _process_single_file(file_idx, file_spec):
        nonlocal total_files_created, completed_files, file_tree
        file_path = file_spec.get("path", "")
        file_action = file_spec.get("action", "create")
        file_desc = file_spec.get("description", "Implement this file")
        file_sections = file_spec.get("sections", [])
        file_imports = file_spec.get("imports", [])
        file_components = file_spec.get("components", [])

        if not file_path:
            return False

        try:
            await websocket.send_json({
                "type": "generation_progress",
                "batch": file_path,
                "message": f"✍️ {'Modifying' if file_action == 'modify' else 'Creating'} {file_path}...",
                "files": [file_path],
                "completed_batches": completed_files,
                "total_batches": total_files_expected,
                "percentage": int((file_idx / total_files_expected) * 100),
            })
        except Exception:
            pass

        # Fast path: direct-write files
        direct_content = file_spec.get("_direct_content")
        if direct_content:
            file_abs = os.path.join(workspace_path, file_path)
            try:
                os.makedirs(os.path.dirname(file_abs), exist_ok=True)
                with open(file_abs, "w") as _df:
                    _df.write(direct_content)
                logger.info("Direct-wrote %s (%d bytes)", file_path, len(direct_content))
                total_files_created += 1
                completed_files += 1
                try:
                    await websocket.send_json({
                        "type": "generation_progress",
                        "batch": file_path,
                        "message": f"✅ {file_path} (direct)",
                        "files": [file_path],
                        "completed_batches": completed_files,
                        "total_batches": total_files_expected,
                        "percentage": int((file_idx / total_files_expected) * 100),
                    })
                except Exception:
                    pass
                file_tree = _build_tree(workspace_path)
            except Exception as e:
                logger.warning("Failed to direct-write %s: %s", file_path, e)
            return True

        # Read existing content for modify actions
        existing_content = ""
        file_abs = os.path.join(workspace_path, file_path)
        if (file_action == "modify" or os.path.isfile(file_abs)) and "/sections/" not in file_path:
            try:
                with open(file_abs, "r", errors="replace") as _ef:
                    existing_content = _ef.read()
            except Exception:
                pass

        _is_layout_file = any(kw in file_path.lower() for kw in [
            "/layout/", "layout.", "navbar.", "footer.", "header.", "sidebar.",
            "app.jsx", "app.vue", "/router/", "navigation.",
        ])

        if existing_content:
            if _is_layout_file:
                _existing_imports = [
                    line.strip() for line in (existing_content or "").splitlines()
                    if line.strip().startswith("import ") and ("/layout/" in line or "/components/" in line)
                ]
                _import_guard = ""
                if _existing_imports:
                    _import_guard = (
                        f"\n⚠️ EXISTING IMPORTS — DO NOT CHANGE THESE:\n"
                        + "\n".join(f"  {imp}" for imp in _existing_imports[:10])
                        + "\n\nDo NOT rename MarketingHeader→Navbar, MarketingFooter→Footer, or any similar renaming.\n"
                        "If the template uses MarketingHeader, keep importing MarketingHeader.\n"
                    )
                file_context = (
                    f"## EXISTING LAYOUT FILE ({file_path})\n"
                    f"This is a LAYOUT/NAVIGATION file. You are UPDATING ITS DATA:\n"
                    f"```\n{existing_content[:6000]}\n```\n\n"
                    f"CRITICAL: Keep the component structure EXACTLY intact. Only update:\n"
                    f"- Navigation items / menu items / route definitions\n"
                    f"- Brand name, logo text, colors\n"
                    f"- Import statements for new page components ONLY (keep ALL existing imports)\n"
                    f"DO NOT change the layout architecture, component hierarchy, or CSS structure.\n"
                    f"DO NOT rename any existing component imports — keep the EXACT same import names.\n"
                    f"DO NOT replace MarketingHeader/MarketingFooter with Navbar/Footer or any other name.\n"
                    f"{_import_guard}"
                )
            else:
                file_context = (
                    f"## EXISTING FILE ({file_path}):\n"
                    f"```\n{existing_content[:8000]}\n```\n\n"
                    f"{'OVERWRITE this file entirely with the new implementation.' if '/sections/' in file_path else 'Modify this file to match the required changes.'}\n"
                )
        else:
            file_context = f"## CREATE NEW FILE: {file_path}\n(Does not exist yet — create it from scratch)\n"

        sections_block = ""
        if file_sections:
            sections_block = f"Sections to implement:\n" + "\n".join(f"- {s}" for s in file_sections) + "\n"

        imports_block = ""
        if file_imports:
            imports_block = f"Import from:\n" + "\n".join(f"- {imp}" for imp in file_imports) + "\n"

        components_block = ""
        if file_components:
            components_block = f"Uses components:\n" + "\n".join(f"- {c}" for c in file_components) + "\n"

        file_prompt = f"""{FRAMEWORK_RULES}

## DESIGN THEME:
{theme_summary[:800]}

## PROJECT SPECIFICATION (context):
{spec_excerpt[:1500]}

## FILE TREE (reference only — do NOT recreate existing files):
{file_tree}
{_manifest_block}
{_knowledge_context}

## YOUR TASK:
{file_context}

## Implementation Requirements:
{file_desc}

{sections_block}{imports_block}{components_block}

## Rules:
1. Write ONLY the file: {file_path}
2. Use Tailwind utility classes (bg-primary, text-foreground, bg-muted, etc.) — NEVER var(--color-*) or hardcoded hex
3. Use lucide-react for icons (NOT brand icons like Instagram, Twitter — use inline SVG for those)
4. Make it responsive (sm:, md:, lg: prefixes)
5. Include loading/empty states where appropriate
6. Write substantial, production-quality content with realistic placeholder text
{"7. Section components MUST start with 'use client' (they use interactive elements)" if "/sections/" in file_path and ("nextjs" in _stk or "next" in _stk) else ""}

USE WRITE TOOL to write {file_path}. Stop when done.
"""

        system_prompt = (
            f"You are building ONE file for a {stack} project.\n"
            "Write it using the Write tool. Stop when the file is written.\n"
            "Do NOT read any files — just write based on the instructions.\n"
            "Use Tailwind classes for all styling.\n"
        ) + anti_loop_block

        per_file_turns = 3
        per_file_timeout = 120

        file_options = ClaudeCodeOptions(
            cwd=str(workspace_path),
            env=env,
            model=model_id,
            max_turns=per_file_turns,
            permission_mode="bypassPermissions",
            allowed_tools=["Read", "Write", "Edit", "MultiEdit", "Bash", "Glob", "Grep", "LS"],
            disallowed_tools=[
                "GitCommit", "GitPush", "GitPull", "GitClone",
                "Bash(git commit*)", "Bash(git push*)", "Bash(rm -rf*)",
            ],
            append_system_prompt=system_prompt,
        )

        file_written = False
        for attempt in range(2):
            try:
                async with asyncio.timeout(per_file_timeout):
                    async for message in query(prompt=file_prompt, options=file_options):
                        msg_str = str(message)
                        try:
                            msg_content = getattr(message, "content", None)
                            if isinstance(msg_content, list):
                                for block in msg_content:
                                    bname = getattr(block, "name", None)
                                    binput = getattr(block, "input", None) or {}
                                    if bname and str(bname).lower() in ("write", "edit", "multiedit"):
                                        fpath = binput.get("file_path") or binput.get("path") or ""
                                        if fpath:
                                            _payload = {
                                                "type": "file_write_event",
                                                "filename": fpath,
                                                "action": str(bname).lower(),
                                                "phase": "step5_execute_per_file",
                                            }
                                            try:
                                                import os as _os_inline
                                                _full = (
                                                    fpath if _os_inline.path.isabs(fpath)
                                                    else _os_inline.path.join(str(workspace_path), fpath)
                                                )
                                                if _os_inline.path.isfile(_full):
                                                    _sz = _os_inline.path.getsize(_full)
                                                    _payload["size"] = _sz
                                                    if _sz <= 256 * 1024:
                                                        with open(_full, "r", encoding="utf-8", errors="replace") as _fh:
                                                            _payload["content"] = _fh.read()
                                                    else:
                                                        _payload["content_truncated"] = True
                                            except Exception:
                                                pass
                                            await websocket.send_json(_payload)
                        except Exception:
                            pass
                        try:
                            await websocket.send_json({
                                "type": "claude_message",
                                "content": msg_str,
                            })
                        except Exception:
                            pass
                break
            except asyncio.TimeoutError:
                logger.warning("File '%s' timed out (attempt %d)", file_path, attempt + 1)
            except Exception as e:
                logger.warning("File '%s' error (attempt %d): %s", file_path, attempt + 1, e)

        # Check if file was created
        if os.path.isfile(os.path.join(workspace_path, file_path)):
            file_stat = os.stat(os.path.join(workspace_path, file_path))
            if file_stat.st_size > 100:  # More than stub
                file_written = True
                total_files_created += 1

        completed_files += 1
        file_tree = _build_tree(workspace_path)

        try:
            await websocket.send_json({
                "type": "batch_complete",
                "batch": file_path,
                "files_created": [file_path] if file_written else [],
                "files_expected": 1,
                "completed_batches": completed_files,
                "total_batches": total_files_expected,
                "percentage": int((completed_files / total_files_expected) * 100),
                "message": f"✅ {file_path}" if file_written else f"⚠️ {file_path} (skipped)",
            })
        except Exception:
            pass

        return file_written

    # ── Execute all files sequentially ────────────────────────
    total_batches = total_files_expected
    completed_batches = 0

    for file_idx, file_spec in enumerate(files):
        await _process_single_file(file_idx, file_spec)
        completed_batches += 1

    # ── Final summary ──────────────────────────────────────────
    try:
        await websocket.send_json({
            "type": "generation_complete",
            "total_files": total_files_created,
            "total_expected": total_files_expected,
            "batches_completed": completed_batches,
            "total_batches": total_batches,
            "message": f"✅ Project generated! ({total_files_created}/{total_files_expected} files)",
        })
    except Exception:
        pass

    logger.info(
        "execute_project_in_batches done: %d/%d files",
        total_files_created, total_files_expected,
    )

    if total_files_created == 0:
        return False
    return total_files_created >= max(1, int(total_files_expected * 0.6))


def _simplified_prompt(batch_files: list, spec_snippet: str, stack: str = "") -> str:
    """Build a simpler retry prompt when the first attempt fails."""
    descs = [f"{f.get('path', '?')}: {f.get('description', 'implement this file')}" for f in batch_files]

    framework_hint = ""
    if stack and ("nextjs" in stack.lower() or "next" in stack.lower()):
        framework_hint = (
            "FRAMEWORK: Next.js 14 App Router.\n"
            "- Files go in src/app/ (e.g. src/app/page.tsx, src/app/layout.tsx)\n"
            "- DO NOT create src/App.jsx or src/App.tsx — those do not exist in Next.js\n"
            "- Use 'use client' directive for interactive components\n"
            "- Import from Next.js: import Link from 'next/link', import { useRouter } from 'next/navigation'\n"
        )
    elif stack and stack.lower() in ("react", "react-admin", "vite"):
        framework_hint = (
            "FRAMEWORK: Vite React.\n"
            "- Entry: src/main.jsx, Root: src/App.jsx\n"
            "- Use react-router-dom for routing\n"
        )
    elif stack and "vue" in stack.lower():
        framework_hint = (
            "FRAMEWORK: Vue 3 + Vite.\n"
            "- Entry: src/main.ts, Root: src/App.vue\n"
            "- Use Vue Router for navigation\n"
        )

    return f"""Create the following files. Use Tailwind classes for all styling. Keep implementation focused.

{framework_hint}
Files to create:
{chr(10).join(descs)}

Context:
{spec_snippet}

USE WRITE TOOL for each file. Stop when done.
"""
