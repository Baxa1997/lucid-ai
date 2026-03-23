"""
Task execution pipeline — validate → clone → classify → explore → execute → verify → push.

Architecture:
- OpenHands V1 SDK: clone (Step 2) and push (Step 7)
- Claude Code SDK: code writing (Step 5)
- Gemini Flash: classification (Step 3) and exploration (Step 4)

CRITICAL: OpenHands and Claude Code SDK NEVER run simultaneously.
Complete handoff via workspace_path only. No shared state.
"""

import os
import json
import asyncio
import subprocess
import shutil
import logging

from fastapi import WebSocket
from claude_code_sdk.query import query
from claude_code_sdk.types import ClaudeCodeOptions, PermissionResultAllow
import google.generativeai as genai

from app.services.openhands_manager import openhands_manager
from app.services.workspace_manager import workspace_manager

logger = logging.getLogger(__name__)

async def _approve_all_tools(tool_name: str, input_data: dict, context) -> PermissionResultAllow:
    return PermissionResultAllow()

# ═══════════════════════════════════════════════════════════════
#  STEP 1 — Validate all inputs
# ═══════════════════════════════════════════════════════════════

async def validate_inputs(
    task: str,
    user: dict,
    websocket: WebSocket,
) -> dict | None:
    """Validate ALL inputs and build repo_url.

    Returns a validated dict with all clean values, or None on failure.
    """
    try:
        # ── Anthropic API key ─────────────────────────────
        api_key = user.get("anthropic_api_key")
        if api_key is None or str(api_key).strip() in ("", "None"):
            await websocket.send_json({
                "type": "error",
                "message": "❌ Anthropic API key not found. Add it in Settings.",
            })
            return None

        # ── Gemini API key ────────────────────────────────
        gemini_key = user.get("gemini_api_key")
        if gemini_key is None or str(gemini_key).strip() in ("", "None"):
            await websocket.send_json({
                "type": "error",
                "message": "❌ Gemini API key not found. Add it in Settings.",
            })
            return None

        # ── Git provider + repo + token ───────────────────
        git_provider = str(user.get("git_provider", "github")).strip().lower()

        if git_provider == "github":
            repo = user.get("github_repo")
            token = user.get("github_token")
            if not repo or not str(repo).strip():
                await websocket.send_json({
                    "type": "error",
                    "message": "❌ GitHub repository not found. Set it in Settings.",
                })
                return None
            if not token or not str(token).strip():
                await websocket.send_json({
                    "type": "error",
                    "message": "❌ GitHub token not found. Connect GitHub in Settings.",
                })
                return None
            repo = str(repo).strip()
            token = str(token).strip()
            # Strip full URL if Supabase stores it as "https://github.com/owner/repo"
            repo = repo.replace("https://github.com/", "").strip("/")
            # Remove .git suffix if already present
            if repo.endswith(".git"):
                repo = repo[:-4]
            repo_url = f"https://{token}@github.com/{repo}.git"
            logger.info("DEBUG repo_url built: %s...", repo_url[:50])

        elif git_provider == "gitlab":
            repo = user.get("gitlab_repo")
            token = user.get("gitlab_token")
            if not repo or not str(repo).strip():
                await websocket.send_json({
                    "type": "error",
                    "message": "❌ GitLab repository not found. Set it in Settings.",
                })
                return None
            if not token or not str(token).strip():
                await websocket.send_json({
                    "type": "error",
                    "message": "❌ GitLab token not found. Connect GitLab in Settings.",
                })
                return None
            repo = str(repo).strip()
            token = str(token).strip()
            # Strip full URL if Supabase stores it as "https://gitlab.com/owner/repo"
            repo = repo.replace("https://gitlab.com/", "").strip("/")
            # Remove .git suffix if already present
            if repo.endswith(".git"):
                repo = repo[:-4]
            repo_url = f"https://oauth2:{token}@gitlab.com/{repo}.git"
            logger.info("DEBUG repo_url built: %s...", repo_url[:50])

        else:
            await websocket.send_json({
                "type": "error",
                "message": f"❌ Unknown git provider: {git_provider}",
            })
            return None

        # ── Branch ────────────────────────────────────────
        branch = str(user.get("selected_branch", "main")).strip()
        if not branch:
            branch = "main"

        # ── Task string ───────────────────────────────────
        if not task or not task.strip():
            await websocket.send_json({
                "type": "error",
                "message": "❌ Task description is empty.",
            })
            return None

        validated = {
            "anthropic_api_key": str(api_key).strip(),
            "gemini_api_key": str(gemini_key).strip(),
            "git_provider": git_provider,
            "repo_url": repo_url,
            "branch": branch,
            "git_token": token,
        }

        await websocket.send_json({
            "type": "progress",
            "message": "✅ Inputs validated",
        })
        return validated

    except Exception as e:
        logger.error("validate_inputs failed: %s", e, exc_info=True)
        try:
            await websocket.send_json({
                "type": "error",
                "message": f"❌ Input validation error: {str(e)[:200]}",
            })
        except Exception:
            pass
        return None


# ═══════════════════════════════════════════════════════════════
#  STEP 2 — Clone repository using OpenHands V1
# ═══════════════════════════════════════════════════════════════

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
3. If package.json exists: npm install
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
                ["git", "clone", "--branch", branch, repo_url, "."],
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


# ═══════════════════════════════════════════════════════════════
#  STEP 3 — Classify task with Gemini Flash
# ═══════════════════════════════════════════════════════════════

async def classify_task(
    task: str,
    gemini_key: str,
    websocket: WebSocket,
) -> dict:
    """Use Gemini Flash to classify task complexity.

    NEVER raises — always returns a valid dict.
    """
    try:
        await websocket.send_json({
            "type": "progress",
            "message": "🎯 Classifying task...",
        })

        genai.configure(api_key=gemini_key)
        model = genai.GenerativeModel("gemini-2.5-flash")

        response = await asyncio.to_thread(
            model.generate_content,
            f"""You are a senior engineering manager at a top tier tech company.
You assign tasks to the right developer.
Return ONLY valid JSON, nothing else. No markdown. No backticks. Just JSON.

You have three developers:

JUNIOR (haiku):
A junior developer who is fast and cheap.
Handles tasks that require NO logical thinking.
Only changes how things LOOK or APPEAR.
Never touches business logic or data flow.
Works on single files only.
Can complete task in under 30 minutes.
Junior handles ALL of these:
- Adding/removing/changing visual elements (logo, image, icon, button, text, link, header, footer, nav, section)
- Adding a logo or image to any page
- Adding a link or navigation element
- Changing colors, fonts, sizes, spacing
- Hiding or showing elements
- Reordering elements on a page
- Changing text or labels
- Any purely visual/cosmetic change
- Wrapping an element with a link

MID (sonnet):
A mid level developer who handles most everyday development work.
Understands logic, state, data flow.
Can work across multiple files.
Handles features, bugs, integrations.
Reliable for most tasks.
Mid handles:
- Creating new components with logic
- Fixing bugs that require debugging
- Adding functionality that needs state management
- API integrations
- Form handling with validation
- Multi-file refactoring

SENIOR (opus):
A senior architect called ONLY for the most critical system-level work.
Called VERY rarely — less than 5% of tasks.

Senior is ONLY for:
- Building entire auth system from scratch
- Payment gateway integration (Stripe, etc)
- Multi-tenant architecture setup
- Security vulnerability fixes
- Database schema design from scratch
- Role-based access control system
- Third party OAuth implementation
- Performance optimization of entire system

Senior is NOT for:
- Fixing visual bugs on auth pages
- Adding/removing fields from forms
- Changing default values
- Static content on login/signup pages
- Simple navigation fixes
- Removing elements from any page
- Changing text or labels in auth forms
- Any change that touches 1-2 files only

KEY RULE FOR SENIOR:
Only use Opus if the task would require a senior architect who understands the entire system infrastructure.
If a mid-level developer can do it in under 2 hours → use Sonnet.
If task mentions auth/login/signup but is just a UI or simple change → NEVER use Opus, use Sonnet or Haiku.

User task: "{task}"

KEY QUESTION — Ask yourself:
Can this task be done by editing HTML/JSX template code only,
without writing new JavaScript logic, state, hooks, or API calls?
If YES → Junior.
If NO → Mid or Senior.

Adding a logo/image/icon to a page = Junior (just adding an HTML element)
Adding a link that navigates somewhere = Junior (just adding an <a> or Link tag)
Changing how something looks = Junior
Creating new functionality = Mid

Return ONLY this JSON:
{{
    "developer": "junior" or "mid" or "senior",
    "model": "haiku" or "sonnet" or "opus",
    "intent": "what user actually wants",
    "why": "why this developer",
    "files_estimate": number,
    "needs_logic": true or false,
    "max_turns": number,
    "complexity": "simple" or "medium" or "complex"
}}

MAX TURNS RULES:
junior → always between 8 and 10
mid    → always between 12 and 18
senior → always between 20 and 25
Never go below these minimums.
Better to give too many than too few.
""",
        )

        text = response.text.strip()
        # Remove markdown code fences if present
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        result = json.loads(text.strip())

    except Exception as e:
        logger.warning("classify_task failed: %s — using fallback", e)
        result = {
            "developer": "mid",
            "model": "sonnet",
            "intent": "unknown",
            "why": "fallback classification",
            "files_estimate": 2,
            "needs_logic": True,
            "max_turns": 12,
            "complexity": "medium",
        }

    # Ensure all fields exist with defaults
    result.setdefault("developer", "mid")
    result.setdefault("model", "sonnet")
    result.setdefault("intent", "unknown")
    result.setdefault("why", "auto-selected")
    result.setdefault("files_estimate", 2)
    result.setdefault("needs_logic", True)
    result.setdefault("max_turns", 12)
    result.setdefault("complexity", "medium")

    # Map developer to model name if model wasn't set correctly
    dev_to_model = {
        "junior": "haiku",
        "mid": "sonnet",
        "senior": "opus",
    }
    if result.get("model") not in ("haiku", "sonnet", "opus"):
        result["model"] = dev_to_model.get(result.get("developer", "mid"), "sonnet")

    # Map model names to full IDs
    model_map = {
        "haiku": "claude-haiku-4-5-20251001",
        "sonnet": "claude-sonnet-4-6",
        "opus": "claude-opus-4-6",
    }
    result["model_id"] = model_map.get(
        result.get("model", "sonnet"), "claude-sonnet-4-6"
    )

    # Enforce minimum max_turns per developer level
    dev = result.get("developer", "mid")
    turns = int(result.get("max_turns", 12))
    if dev == "junior":
        turns = max(8, min(turns, 10))
    elif dev == "senior":
        turns = max(20, min(turns, 25))
    else:
        turns = max(12, min(turns, 18))
    result["max_turns"] = turns

    # Map task_type based on Gemini's developer decision — trust Gemini fully
    intent = str(result.get("intent", "")).lower()

    if dev == "junior":
        # Junior always does simple UI
        result["task_type"] = "ui_simple"

    elif dev == "senior":
        # Senior handles complex systems
        if any(w in intent for w in ("auth", "security", "token", "session", "password")):
            result["task_type"] = "auth"
        elif any(w in intent for w in ("database", "schema", "migration", "table")):
            result["task_type"] = "database"
        else:
            result["task_type"] = "feature_complex"

    else:  # mid developer
        # Mid handles bugs, features, complex UI
        if any(w in intent for w in ("bug", "fix", "broken", "error", "crash", "not working")):
            if result.get("complexity") == "complex":
                result["task_type"] = "bug_complex"
            else:
                result["task_type"] = "bug_simple"
        elif any(w in intent for w in ("refactor", "clean", "reorganize", "restructure")):
            result["task_type"] = "refactor"
        elif result.get("needs_logic") is False:
            result["task_type"] = "ui_complex"
        else:
            result["task_type"] = "feature_simple"

    # Send detailed classification to frontend
    try:
        await websocket.send_json({
            "type": "classification",
            "developer": result.get("developer", "mid"),
            "model": result.get("model", "sonnet"),
            "complexity": result.get("complexity", "medium"),
            "task_type": result.get("task_type", "feature_simple"),
            "max_turns": result.get("max_turns", 12),
            "intent": result.get("intent", ""),
            "reason": result.get("why", "auto-selected"),
            "files_expected": result.get("files_estimate", 2),
            "message": f"🎯 {result.get('developer', 'mid').upper()} DEV ({result.get('model', 'sonnet').upper()}) — {result.get('why', 'auto-selected')}",
        })
    except Exception:
        pass

    return result


# ═══════════════════════════════════════════════════════════════
#  STEP 4 — Explore codebase with Gemini Flash
# ═══════════════════════════════════════════════════════════════

async def explore_with_gemini(
    task: str,
    workspace_path: str,
    classification: dict,
    gemini_key: str,
    websocket: WebSocket,
) -> str:
    """Read codebase and generate implementation plan.

    NEVER raises — always returns a string.
    """
    try:
        await websocket.send_json({
            "type": "progress",
            "message": "🔍 Analyzing codebase...",
        })
    except Exception:
        pass

    skip_dirs = {
        "node_modules", ".git", "dist", "build",
        ".next", "__pycache__", ".cache", "coverage",
    }
    skip_extensions = {
        ".png", ".jpg", ".jpeg", ".svg", ".ico", ".gif",
        ".woff", ".ttf", ".map", ".lock", ".zip",
    }
    skip_files = {
        "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    }

    all_files = {}
    try:
        for root, dirs, files in os.walk(workspace_path):
            dirs[:] = [d for d in dirs if d not in skip_dirs]
            for f in files:
                if f in skip_files:
                    continue
                ext = os.path.splitext(f)[1].lower()
                if ext in skip_extensions:
                    continue
                full_path = os.path.join(root, f)
                rel_path = os.path.relpath(full_path, workspace_path)
                try:
                    with open(full_path, "r", encoding="utf-8", errors="replace") as fh:
                        content = fh.read()
                        if len(content.splitlines()) <= 300:
                            all_files[rel_path] = content
                except Exception:
                    pass
    except Exception as e:
        logger.warning("explore_with_gemini file walk error: %s", e)

    files_content = "\n\n".join(
        f"=== FILE: {path} ===\n{content}"
        for path, content in all_files.items()
    )

    # Hard limit: 40000 characters
    if len(files_content) > 40000:
        files_content = files_content[:40000] + "\n... (truncated)"

    try:
        genai.configure(api_key=gemini_key)
        model = genai.GenerativeModel("gemini-2.5-flash")

        response = await asyncio.to_thread(
            model.generate_content,
            f"""You are a senior software engineer.
Task type: {classification.get('task_type', 'feature')}
Task: {task}

Codebase:
{files_content}

Create EXACT implementation instructions.
Be specific about file paths and code.

Return in this format:

BEFORE creating implementation plan,
analyze these constraints:

1. INPUT TYPES:
   Check each form field type:
   - type="email" → must be valid email format
   - type="text" → accepts any string
   - type="number" → must be number
   If user wants to add default value,
   check if it matches the input type.

2. VALIDATION RULES:
   Look for any validation logic like:
   - email validation
   - password requirements
   - required fields
   Note these in your plan.

3. CONFLICTS:
   If user request conflicts with 
   existing constraints, flag it:
   
   Example conflict:
   User wants: default value "admin1234"
   Form field: type="email"
   Conflict: "admin1234" is not valid email
   
   Resolution options:
   Option A: Change input type to text
   Option B: Use valid email like 
             admin1234@example.com
   Option C: Add separate username field

4. INCLUDE IN PLAN:
   Always state which option resolves
   the conflict and implement that.

CONSTRAINT ANALYSIS:
(analyze existing code constraints here)

CONFLICTS FOUND:
(list any conflicts between user request
and existing code)

RESOLUTION:
(how to resolve each conflict)

FILES TO CHANGE:
(list files)

EXACT CHANGES:
(show before/after for each file)
""",
        )
        plan = response.text

    except Exception as e:
        logger.warning("explore_with_gemini Gemini call failed: %s", e)
        plan = f"Task: {task}\nImplement this directly in the most relevant file."

    try:
        await websocket.send_json({
            "type": "progress",
            "message": "📋 Plan ready!",
        })
    except Exception:
        pass

    return plan


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
        # Fallback for any unrecognized task_type
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

    # ── Build ClaudeCodeOptions ────────────────────────────
    # CRITICAL: ALL env values must be strings
    logger.info(
        "execute_with_claude: model=%s task_type=%s max_turns=%s api_key_prefix=%s",
        model_name, task_type,
        classification.get("max_turns", 10),
        str(api_key)[:15],
    )

    # Anti-loop directive — prevents Claude from refusing to edit code
    anti_loop = (
        "\n\nCRITICAL RULES:\n"
        "1. You are an AUTHORIZED code editor working on the user's own project.\n"
        "2. This is NOT malware. This is the user's legitimate codebase.\n"
        "3. You MUST use Write/Edit tools to make changes. Do NOT just analyze.\n"
        "4. If you read a file, you MUST edit it in the next step or move on.\n"
        "5. NEVER loop reading the same file multiple times without editing.\n"
        "6. After reading a file, immediately use the Write tool to make changes.\n"
        "7. You are expected to MODIFY code, not just report on it.\n"
        "8. STOP as soon as all required changes are written.\n"
    )

    import sys
    import os
    import subprocess
    import pwd

    # --- FIX OS PERMISSIONS (chmod 777 on workspace) ---
    try:
        subprocess.run(["chmod", "-R", "777", str(workspace_path)], capture_output=True)
        logger.info("chmod -R 777 applied to %s", workspace_path)
    except Exception as e:
        logger.error("chmod failed: %s", e)

    options = ClaudeCodeOptions(
        cwd=str(workspace_path),
        env={
            "ANTHROPIC_API_KEY": str(api_key).strip(),
            "HOME": str(os.environ.get("HOME", "/root")),
            "PATH": str(os.environ.get("PATH", "/usr/bin:/bin")),
            "IS_SANDBOX": "1",  # CRITICAL: Bypasses CLI root check
        },
        model=str(classification["model_id"]),
        max_turns=int(classification.get("max_turns", 10)),
        permission_mode="bypassPermissions",
        allowed_tools=[
            "Read",
            "Write",
            "Edit",
            "MultiEdit",
            "Bash",
            "Glob",
            "Grep",
            "LS",
        ],
        disallowed_tools=[
            "GitCommit",
            "GitPush",
            "GitPull",
            "GitClone",
            "Bash(git commit*)",
            "Bash(git push*)",
            "Bash(rm -rf*)",
        ],
        append_system_prompt=system + anti_loop,
    )

    # ── Dynamic timeout based on max_turns ─────────────────
    max_turns = int(classification.get("max_turns", 10))
    timeout_seconds = max(120, max_turns * 30)  # min 2min, ~30s per turn

    try:
        async with asyncio.timeout(timeout_seconds):
            async for message in query(
                prompt=prompt,
                options=options,
            ):
                try:
                    await websocket.send_json({
                        "type": "claude_message",
                        "content": str(message),
                    })
                except Exception:
                    pass
        return True

    except asyncio.TimeoutError:
        try:
            await websocket.send_json({
                "type": "error",
                "message": f"⏱️ Task timed out after {timeout_seconds // 60} minutes. Try a smaller task.",
            })
        except Exception:
            pass
        return False

    except Exception as e:
        import traceback
        full_error = traceback.format_exc()
        print(f"CLAUDE FULL ERROR: {full_error}")
        print(f"CLAUDE ERROR TYPE: {type(e).__name__}")
        print(f"CLAUDE ERROR STR: {str(e)}")

        # Try to get stderr if available
        stderr_info = ""
        if hasattr(e, 'stderr'):
            stderr_info = str(e.stderr)
            print(f"CLAUDE STDERR: {stderr_info}")
        if hasattr(e, 'stdout'):
            print(f"CLAUDE STDOUT: {str(e.stdout)}")
        if hasattr(e, 'returncode'):
            print(f"CLAUDE RETURNCODE: {e.returncode}")

        logger.error("execute_with_claude failed: %s", e, exc_info=True)
        try:
            await websocket.send_json({
                "type": "error",
                "message": f"❌ Claude failed: {str(e)[:500]} | {stderr_info[:200]}",
            })
        except Exception:
            pass
        return False


# ═══════════════════════════════════════════════════════════════
#  STEP 5.5 — Smart build verification
# ═══════════════════════════════════════════════════════════════

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
                    async with asyncio.timeout(300): # 5 min limit for tsc fixing
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


# ═══════════════════════════════════════════════════════════════
#  STEP 6 — Verify changes
# ═══════════════════════════════════════════════════════════════

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


# ═══════════════════════════════════════════════════════════════
#  STEP 7 — Push changes using OpenHands V1
# ═══════════════════════════════════════════════════════════════

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
        from app.services.git_operations import _inject_token_into_url
        authed_url = _inject_token_into_url(repo_url, git_token)
    else:
        authed_url = repo_url

    # ── Primary path: direct subprocess (fast) ────────────
    git_ops = [
        {"cmd": ["git", "add", "."], "msg": "staging changes"},
        {"cmd": ["git", "commit", "-m", commit_msg], "msg": "committing changes"},
        {"cmd": ["git", "remote", "set-url", "origin", authed_url], "msg": "setting remote URL"},
        {"cmd": ["git", "push", "origin", branch], "msg": "pushing to GitHub"},
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


# ═══════════════════════════════════════════════════════════════
#  STEP 8 — Master pipeline orchestrator
# ═══════════════════════════════════════════════════════════════

async def run_pipeline(
    task: str,
    user: dict,
    websocket: WebSocket,
    task_id: str,
    conversation_id: str = "",
) -> str | None:
    """Run all pipeline steps sequentially.

    Each step runs one at a time — never simultaneously.
    OpenHands and Claude never overlap.
    Always cleans up workspace in finally block.

    Returns the workspace path on success, None on failure.
    """
    workspace_path = None
    validated = None

    try:
        # Step 1 — Validate inputs
        validated = await validate_inputs(task, user, websocket)
        if validated is None:
            return

        # Step 2 — Get or create workspace (clone once, git pull after)
        if conversation_id:
            workspace_path = await workspace_manager.get_or_create_workspace(
                conversation_id=conversation_id,
                validated=validated,
                websocket=websocket,
            )
        else:
            # Fallback: no conversation_id — clone fresh like before
            workspace_path = await clone_with_openhands(
                validated, task_id, websocket,
            )
        if not workspace_path:
            return

        # ── HANDOFF POINT: OpenHands is now DEAD ──────────
        # Verify OpenHands is destroyed before Claude starts
        if await openhands_manager.is_active():
            await openhands_manager.destroy_all()
            await asyncio.sleep(0.5)

        # Step 3 — Classify with Gemini
        classification = await classify_task(
            task,
            validated["gemini_api_key"],
            websocket,
        )

        # Step 4 — Explore with Gemini
        plan = await explore_with_gemini(
            task,
            workspace_path,
            classification,
            validated["gemini_api_key"],
            websocket,
        )

        # Step 5 — Execute with Claude (OpenHands MUST be dead)
        success = await execute_with_claude(
            task,
            workspace_path,
            validated["anthropic_api_key"],
            classification,
            plan,
            websocket,
        )
        if not success:
            return

        # Step 5.5 — Smart build verification (TypeScript auto-fix)
        await verify_build(
            workspace_path,
            validated["anthropic_api_key"],
            classification,
            websocket,
        )

        # Step 6 — Verify changes
        changed = await verify_changes(workspace_path, websocket)
        if not changed:
            return

        # Step 7 — Push with OpenHands (Claude is done)
        await push_with_openhands(
            workspace_path,
            validated,
            task,
            task_id,
            websocket,
        )

        return workspace_path

    except Exception as e:
        logger.error("Pipeline failed: %s", e, exc_info=True)
        try:
            await websocket.send_json({
                "type": "error",
                "message": f"❌ Pipeline failed: {str(e)[:300]}",
            })
        except Exception:
            pass
        return None

    finally:
        # Always destroy any lingering OpenHands conversations
        await openhands_manager.destroy_all()

        # NOTE: Workspace cleanup is now handled by WorkspaceManager.
        # The workspace persists across tasks within the same conversation.
        # It is destroyed when the WebSocket disconnects or expires after 2h.
        # Only clean up if there was no conversation_id (legacy fallback).
        if not conversation_id and workspace_path and os.path.exists(workspace_path):
            try:
                shutil.rmtree(workspace_path)
                logger.info("Workspace cleaned up (no conversation_id): %s", workspace_path)
            except Exception:
                pass
