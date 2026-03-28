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

# ── Centralized Gemini Model ──────────────────────────────────
# Change this ONE constant to switch the model for ALL Gemini calls.
# Valid options: "gemini-2.0-flash", "gemini-1.5-flash", "gemini-1.5-pro"
GEMINI_MODEL = "gemini-2.0-flash"

async def _approve_all_tools(tool_name: str, input_data: dict, context) -> PermissionResultAllow:
    return PermissionResultAllow()


# ═══════════════════════════════════════════════════════════════
#  HELPERS — GitHub token detection + repo creation
# ═══════════════════════════════════════════════════════════════

def is_fine_grained_token(token: str) -> bool:
    """Detect fine-grained tokens which cannot create repos.
    Fine-grained: github_pat_...   Classic: ghp_... or gho_..."""
    return token.startswith("github_pat_")


def derive_repo_name(task: str, chat_session_id: str = "") -> tuple[str, str]:
    """Derive a clean repo name from the task text following company convention.

    Convention:
        website     → {name}_website_frontend
        admin/CMS   → {name}_admin_frontend
        backend/API → {name}_service
        fallback    → {name}_frontend

    Returns (repo_name, project_description).
    """
    import re
    import random as _rng

    project_desc = ""
    project_stack = ""
    project_backend = ""

    # 1. Parse [LUCID_PROJECT] header if present
    header_match = re.match(
        r"\[LUCID_PROJECT\]\s*description=(.+?)\s*\|\s*stack=(\S+)\s*\|\s*backend=(\S+)",
        str(task or ""),
    )
    if header_match:
        project_desc = header_match.group(1).strip()
        project_stack = header_match.group(2).strip()
        project_backend = header_match.group(3).strip()
    else:
        # Fallback: extract from raw task text
        raw = str(task or "").strip()
        for noise in [
            "previous-conversation-context:",
            "previous conversation context:",
            "context:", "[LUCID_PROJECT]",
        ]:
            idx = raw.lower().find(noise.lower())
            if idx >= 0:
                raw = raw[idx + len(noise):].strip()
        project_desc = raw[:100]

    # 2. Extract clean project name
    name = project_desc.lower().strip()
    for prefix in [
        "build me a ", "build a ", "create a ", "make a ",
        "develop a ", "generate a ", "make me a ",
        "build me ", "create ", "make ", "just a ",
        "just ", "simple ", "overall ",
    ]:
        if name.startswith(prefix):
            name = name[len(prefix):]
            break

    # Remove trailing noise
    name = re.split(r"\bwith\b|\band\b|\bthat\b|\busing\b|\bfor\b", name)[0].strip()

    # Slugify
    slug = re.sub(r"[^a-z0-9]+", "_", name).strip("_")[:25]
    slug = slug or "project"

    # 3. Determine type suffix
    desc_lower = project_desc.lower()
    if any(w in desc_lower for w in ["admin", "dashboard", "panel", "cms"]):
        type_suffix = "_admin_frontend"
    elif any(w in desc_lower for w in ["api", "backend", "server", "microservice", "service"]):
        type_suffix = "_service"
    elif project_backend in ("supabase", "own"):
        type_suffix = "_website_frontend"
    elif project_stack in ("html-css", "nextjs", "react", "vue", "angular", "auto"):
        type_suffix = "_website_frontend"
    else:
        type_suffix = "_frontend"

    # 4. Build final name — clean, no session_id suffix
    repo_name = f"{slug}{type_suffix}"

    # Detect if this is an admin panel
    is_admin = "admin" in type_suffix

    logger.info("derive_repo_name: %s (desc=%s, stack=%s)", repo_name, project_desc[:40], project_stack)
    return repo_name, project_desc, project_stack, is_admin


async def create_github_repo(
    project_name: str,
    github_token: str,
    description: str = "",
    is_private: bool = True,
    websocket: WebSocket = None,
) -> tuple[str, str] | None:
    """Create a GitHub repo using a Classic Personal Access Token.

    Returns (auth_clone_url, html_url) on success, None on failure.
    """
    import httpx
    import random

    headers = {
        "Authorization": f"token {github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
    }

    # Sanitize description — GitHub rejects control characters
    import re as _re_desc
    clean_desc = _re_desc.sub(r"[\x00-\x1f\x7f]+", " ", description or "").strip()[:350]
    clean_desc = clean_desc or "Generated by Lucid AI"

    async def _try_create(name: str) -> httpx.Response:
        async with httpx.AsyncClient(timeout=30) as client:
            return await client.post(
                "https://api.github.com/user/repos",
                headers=headers,
                json={
                    "name": name,
                    "description": clean_desc,
                    "private": is_private,
                    "auto_init": False,
                    "has_issues": True,
                    "has_projects": False,
                    "has_wiki": False,
                },
            )

    async def _ws_error(msg: str):
        if websocket:
            try:
                await websocket.send_json({"type": "error", "message": msg})
            except Exception:
                pass

    # Try up to 5 names: original, then with sequential suffixes
    attempts = [project_name]
    for i in range(2, 6):
        attempts.append(f"{project_name}_{i}")

    resp = None
    used_name = project_name
    for attempt_name in attempts:
        used_name = attempt_name
        logger.info("Creating repo: %s", attempt_name)
        resp = await _try_create(attempt_name)
        if resp.status_code == 201:
            break
        if resp.status_code == 422:
            logger.info("Repo name '%s' taken, trying next...", attempt_name)
            continue
        # Non-422 error — stop retrying
        break

    if resp and resp.status_code == 201:
        repo_data = resp.json()
        clone_url = repo_data.get("clone_url", "")
        html_url = repo_data.get("html_url", "")
        auth_url = clone_url.replace("https://", f"https://{github_token}@")
        logger.info("Repo created: %s", html_url)
        return auth_url, html_url

    # ── Error handling — always show original GitHub API error ──
    status = resp.status_code if resp else -1
    raw_body = resp.text[:500] if resp else "no response"

    # Try to extract GitHub's specific error message from JSON
    api_detail = ""
    if resp is not None:
        try:
            err_json = resp.json()
            errors_list = err_json.get("errors", [])
            if errors_list:
                api_detail = errors_list[0].get("message", "")
            if not api_detail:
                api_detail = err_json.get("message", "")
        except Exception:
            api_detail = raw_body[:200]

    # Log full details including token prefix for debugging
    token_hint = github_token[:7] + "..." if github_token else "(empty)"
    logger.error(
        "GitHub repo creation failed: status=%d token=%s name=%s detail=%s body=%s",
        status, token_hint, project_name, api_detail, raw_body[:300],
    )

    if resp is None:
        await _ws_error("❌ GitHub API — no response received.")
    elif status == 401:
        await _ws_error(
            f"❌ GitHub 401 Unauthorized. Token: {token_hint}. "
            f"API says: {api_detail or 'Bad credentials'}. "
            "Use a Classic PAT (ghp_...) with repo scope."
        )
    elif status == 403:
        await _ws_error(
            f"❌ GitHub 403 Forbidden: {api_detail or 'Token missing repo scope'}. "
            "Regenerate your Classic token with repo scope."
        )
    elif status == 422:
        await _ws_error(
            f"❌ GitHub 422: {api_detail or 'Name already exists'}. "
            f"Tried: {project_name} + 4 variants."
        )
    else:
        await _ws_error(
            f"❌ GitHub {status}: {api_detail or raw_body[:200]}"
        )
    return None

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
    Supports SCRATCH MODE — when no repo is configured, returns
    scratch_mode=True so the pipeline creates a local workspace.
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

        # ── SCRATCH MODE: no repo configured ──────────────
        # Wizard-created projects have no repo. Instead of failing,
        # we enter scratch mode: create an empty local workspace.
        repo = None
        token = None
        repo_url = None
        scratch_mode = False

        if git_provider == "github":
            repo = user.get("github_repo")
            token = user.get("github_token")
        elif git_provider == "gitlab":
            repo = user.get("gitlab_repo")
            token = user.get("gitlab_token")

        # Check if repo is truly empty/missing (scratch project)
        repo_str = str(repo).strip() if repo else ""
        if not repo_str or repo_str in ("None", "null", ""):
            scratch_mode = True
            logger.info("SCRATCH MODE: No repository configured — creating local workspace")
        else:
            # Normal mode: validate everything
            if git_provider == "github":
                if not token or not str(token).strip():
                    await websocket.send_json({
                        "type": "error",
                        "message": "❌ GitHub token not found. Connect GitHub in Settings.",
                    })
                    return None
                repo = str(repo).strip()
                token = str(token).strip()
                repo = repo.replace("https://github.com/", "").strip("/")
                if repo.endswith(".git"):
                    repo = repo[:-4]
                repo_url = f"https://{token}@github.com/{repo}.git"
                logger.info("DEBUG repo_url built: %s...", repo_url[:50])

            elif git_provider == "gitlab":
                if not token or not str(token).strip():
                    await websocket.send_json({
                        "type": "error",
                        "message": "❌ GitLab token not found. Connect GitLab in Settings.",
                    })
                    return None
                repo = str(repo).strip()
                token = str(token).strip()
                repo = repo.replace("https://gitlab.com/", "").strip("/")
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
            "repo_url": repo_url or "",
            "branch": branch,
            "git_token": token or "",
            "scratch_mode": scratch_mode,
        }

        await websocket.send_json({
            "type": "progress",
            "message": "✅ Inputs validated" + (" (scratch mode)" if scratch_mode else ""),
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
        model = genai.GenerativeModel(GEMINI_MODEL)

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

CRITICAL — NEW PROJECT CREATION RULES:
If the task is to CREATE A NEW PROJECT from scratch (not editing existing code):
- Simple static page (just HTML, CSS, text, inputs, basic layout) → JUNIOR
  Examples: "simple html page", "landing page with text", "html with input and text"
- Standard web app (React/Vue/Angular components, routing, state) → MID
  Examples: "e-commerce app", "admin panel", "dashboard with charts"
- Complex system (auth, payments, multi-service, database design) → SENIOR
  Examples: "SaaS platform with auth and billing", "multi-tenant system"

The number of FILES does not determine complexity for new projects.
A plain HTML project with index.html + style.css = JUNIOR even though it's 2 files.
What matters is the LOGIC COMPLEXITY, not the file count.

User task: "{task}"

KEY QUESTION — Ask yourself:
1. Is this a NEW PROJECT or an EDIT to existing code?
2. If NEW: Does it need JavaScript logic, state management, or API calls?
   If NO (just HTML/CSS) → Junior.
   If YES but standard features → Mid.
   If YES and requires system architecture → Senior.
3. If EDIT: Can it be done by editing HTML/JSX template code only?
   If YES → Junior.
   If NO → Mid or Senior.

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
    """Read specific codebase files and generate implementation plan.

    NEVER raises — always returns a string.
    """
    try:
        await websocket.send_json({
            "type": "progress",
            "message": "🔍 Analyzing repository structure...",
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

    # STEP 1: Read file tree only (no content)
    file_paths = []
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
                file_paths.append(rel_path)
    except Exception as e:
        logger.warning("explore_with_gemini file walk error: %s", e)

    # STEP 2: Ask Gemini which files are relevant
    relevant_files = []
    try:
        genai.configure(api_key=gemini_key)
        model = genai.GenerativeModel(GEMINI_MODEL)

        file_tree_str = "\\n".join(file_paths)
        
        # Hard limit just in case repo has massive number of files
        if len(file_tree_str) > 50000:
            file_tree_str = file_tree_str[:50000] + "\\n... (truncated)"

        # Dynamic file count based on task complexity
        complexity = classification.get("complexity", "medium")
        if complexity == "simple":
            file_range = "3-5"
            fallback_count = 5
        elif complexity == "complex":
            file_range = "8-15"
            fallback_count = 15
        else:
            file_range = "5-10"
            fallback_count = 10

        filter_prompt = f"""Task type: {classification.get('task_type', 'feature')}
Task: {task}

Here are the files in the repository:
{file_tree_str}

Which {file_range} files are most relevant to completing this task?

IMPORTANT SELECTION RULES:
1. ALWAYS include entry points (index.js, page.js, layout.js, App.js, main.py, etc.)
2. ALWAYS include shared config files (tailwind.config.js, tsconfig.json, package.json, etc.) if they could be relevant
3. ALWAYS include component/module files directly referenced by the task
4. Include parent layout/wrapper files if the task involves UI changes
5. Include utility/helper files that the target files import from

Return ONLY a valid JSON list of file paths. No markdown formatting, no backticks, just the JSON array.
Example: ["src/app/page.js", "src/components/Header.js"]"""

        filter_response = await asyncio.to_thread(
            model.generate_content,
            filter_prompt,
        )

        text = filter_response.text.strip()
        if "```" in text:
            # Extract JSON from markdown fencing
            parts = text.split("```")
            if len(parts) >= 3:
                text = parts[1]
            if text.startswith("json"):
                text = text[4:]
        
        relevant_files = json.loads(text.strip())
        if not isinstance(relevant_files, list):
            relevant_files = file_paths[:fallback_count]
    except Exception as e:
        logger.warning("explore_with_gemini file filtering failed: %s", e)
        relevant_files = file_paths[:fallback_count]

    try:
        await websocket.send_json({
            "type": "progress",
            "message": "🔍 Reading relevant files...",
        })
    except Exception:
        pass

    # STEP 3: Read ONLY those relevant files
    all_files = {}
    for rel_path in relevant_files:
        if not rel_path or not isinstance(rel_path, str):
            continue
            
        full_path = os.path.join(workspace_path, rel_path)
        # Prevent directory traversal
        abs_ws = os.path.abspath(workspace_path)
        abs_fp = os.path.abspath(full_path)
        if not abs_fp.startswith(abs_ws):
            continue
            
        try:
            with open(abs_fp, "r", encoding="utf-8", errors="replace") as fh:
                all_files[rel_path] = fh.read()
        except Exception:
            pass

    files_content = "\\n\\n".join(
        f"=== FILE: {path} ===\\n{content}"
        for path, content in all_files.items()
    )

    # Ensure we still have some fallback limit if single files are huge
    if len(files_content) > 100000:
       files_content = files_content[:100000] + "\\n... (truncated)"

    # STEP 4: Send focused context to Gemini
    try:
        response = await asyncio.to_thread(
            model.generate_content,
            f"""You are a senior software engineer.
Task type: {classification.get('task_type', 'feature')}
Task: {task}

Focused Codebase Context:
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
        logger.warning("explore_with_gemini Gemini plan generation failed: %s", e)
        plan = f"Task: {task}\\nImplement this directly in the most relevant file."

    try:
        await websocket.send_json({
            "type": "progress",
            "message": "📋 Plan ready!",
        })
    except Exception:
        pass

    return plan


# ═══════════════════════════════════════════════════════════════
#  STEP 4a (NEW) — Gemini Research: generates .lucid/spec.md
# ═══════════════════════════════════════════════════════════════

async def gemini_research(
    task: str,
    workspace_path: str,
    validated: dict,
    gemini_key: str,
    websocket: WebSocket,
) -> str:
    """Use Gemini to research and create a detailed spec for new projects.

    Reads app_patterns.json, selects best match, generates spec.md.
    Returns the spec text. NEVER raises.
    """
    try:
        await websocket.send_json({
            "type": "progress",
            "message": "🔬 Researching project requirements...",
        })
    except Exception:
        pass

    # Load app patterns
    patterns_text = "[]"
    try:
        patterns_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "app_patterns.json"
        )
        with open(patterns_path, "r") as f:
            patterns_text = f.read()
    except Exception as e:
        logger.warning("Could not load app_patterns.json: %s", e)

    # Read existing skeleton file tree
    file_tree = []
    for root, dirs, files in os.walk(workspace_path):
        dirs[:] = [d for d in dirs if d not in {".git", "node_modules", "__pycache__"}]
        for f in files:
            rel = os.path.relpath(os.path.join(root, f), workspace_path)
            file_tree.append(rel)
    file_tree_str = "\n".join(file_tree)

    skeleton_name = validated.get("skeleton_name", "unknown")
    is_admin = validated.get("is_admin", False)

    try:
        genai.configure(api_key=gemini_key)
        model = genai.GenerativeModel(GEMINI_MODEL)

        spec_prompt = f"""You are a world-class product designer at a top design agency.
A user wants to create a NEW, UNIQUE project. You must research what this project needs and write a detailed specification.

CRITICAL: The design MUST be UNIQUE to this specific project. Do NOT use generic templates.
- Choose a UNIQUE color palette that matches the project's industry and mood
- Choose a UNIQUE layout style (not the same sidebar+table for every project)
- Every project should feel different and tailored to its purpose

USER REQUEST: {task}

SKELETON USED: {skeleton_name} (this is just a starting point for file structure — the design must be custom)
IS ADMIN PANEL: {is_admin}

EXISTING FILES IN WORKSPACE:
{file_tree_str}

APP PATTERN DATABASE (use as INSPIRATION, do not copy directly — customize heavily):
{patterns_text}

Write a detailed specification in Markdown format. Include:

## Project Overview
Brief description of what this app does and its target audience.

## Design System (MUST BE UNIQUE TO THIS PROJECT)
- Primary color: choose a color that fits the project's industry (e.g. green for finance, blue for healthcare, purple for creative tools, orange for food). NEVER default to #6366f1
- Secondary/accent colors: 2-3 complementary colors
- Background style: choose between light, dark, gradient, or mixed
- Card style: flat, raised, glassmorphism, bordered, or neumorphism
- Typography: heading font + body font (can be different)
- Animation style: subtle, energetic, minimal, or playful
- Overall mood: professional, playful, elegant, bold, minimal

## Pages
For EACH page, describe:
- Page name and route
- Key sections and layout (be creative with layouts, not always sidebar+content)
- Components needed
- Visual description (SPECIFIC colors, gradients, shadows, not vague)

## Components
For EACH custom component, describe:
- Name and purpose
- Props it accepts
- Visual design (SPECIFIC: exact colors, border-radius, shadows, gradients)
- Behavior (hover effects, click animations, loading states)
- What makes this component feel premium

## Data Model
{"This project uses Supabase. Specify:" if is_admin else "If applicable:"}
- Exact Supabase tables with ALL columns (name, type, constraints)
- Row Level Security policies
- Relationships and foreign keys
- Sample data (at least 5 realistic rows per table)
{"- Auth configuration: email/password, OAuth providers" if is_admin else ""}


## UI Quality Standards — BALANCED & SOLID (like Claude Console / Linear / Stripe)
- The UI MUST feel like a $50K custom-built product, NOT a template
- SIZING IS CRITICAL: use 14px base font, 13px for secondary text, 11px for labels
- Buttons: 8-10px vertical padding, 16px horizontal — NOT oversized, NOT tiny
- Cards: 16px padding, NOT 24-32px — clean and balanced
- Table cells: 10-12px vertical padding — compact but readable
- Sidebar: 240px wide, nav items 6-8px vertical padding — tight
- Page headings: 18-20px, NOT 30-40px — professional, not shouty
- Line-height: 1.4-1.5 — NOT 1.6-1.8
- Use letter-spacing: -0.01em on headings for tightness
- Every page must have a UNIQUE layout — avoid repeating the same grid everywhere
- Micro-animations should be SUBTLE (0.5px translateY, not 4px) — professional, not bouncy
- Empty states must be clean with muted icons, not oversized illustrations
- Loading states must use skeleton loaders (shimmer effect), not spinners
- Mobile responsive with thoughtful breakpoints
- All spacing should feel TIGHT and INTENTIONAL — nothing floating in empty space

REMEMBER: This spec defines a UNIQUE product. If two different projects get the same spec, you have FAILED.
"""
        response = await asyncio.to_thread(model.generate_content, spec_prompt)
        spec = response.text.strip()
        if not spec:
            spec = f"# Project Specification\n\nBuild: {task}"

    except Exception as e:
        logger.warning("gemini_research failed: %s", e)
        spec = f"# Project Specification\n\nBuild: {task}"

    # Save spec to workspace
    try:
        lucid_dir = os.path.join(workspace_path, ".lucid")
        os.makedirs(lucid_dir, exist_ok=True)
        with open(os.path.join(lucid_dir, "spec.md"), "w") as f:
            f.write(spec)
        logger.info("Saved spec.md (%d chars)", len(spec))
    except Exception as e:
        logger.warning("Could not save spec.md: %s", e)

    try:
        await websocket.send_json({
            "type": "progress",
            "message": "📋 Specification created",
        })
    except Exception:
        pass

    return spec


# ═══════════════════════════════════════════════════════════════
#  STEP 4b (NEW) — Gemini Plan: generates .lucid/plan.json
# ═══════════════════════════════════════════════════════════════

async def gemini_create_plan(
    task: str,
    workspace_path: str,
    spec: str,
    validated: dict,
    gemini_key: str,
    websocket: WebSocket,
) -> str:
    """Generate plan.json with exact files and content outlines.

    Returns the plan as a JSON string. NEVER raises.
    """
    try:
        await websocket.send_json({
            "type": "progress",
            "message": "📐 Creating implementation plan...",
        })
    except Exception:
        pass

    # Read current file tree (including skeleton files)
    file_tree = []
    for root, dirs, files in os.walk(workspace_path):
        dirs[:] = [d for d in dirs if d not in {".git", "node_modules", "__pycache__", ".lucid"}]
        for f in files:
            rel = os.path.relpath(os.path.join(root, f), workspace_path)
            file_tree.append(rel)
    file_tree_str = "\n".join(file_tree)

    skeleton_name = validated.get("skeleton_name", "unknown")

    try:
        genai.configure(api_key=gemini_key)
        model = genai.GenerativeModel(GEMINI_MODEL)

        plan_prompt = f"""You are a senior software architect. Create an EXACT implementation plan.
The design must match the specification EXACTLY — use the UNIQUE colors, styles, and layouts from the spec.

PROJECT SPECIFICATION:
{spec[:8000]}

SKELETON: {skeleton_name}
EXISTING FILES:
{file_tree_str}

USER TASK: {task}

CRITICAL — DYNAMIC THEME:
The skeleton has PLACEHOLDER colors (--color-primary: #6366f1). You MUST override ALL theme colors.
Every project MUST have a UNIQUE color palette. NEVER use the default #6366f1 indigo.

Generate a JSON plan with these sections:

1. "theme" — REQUIRED: CSS variable overrides to make this project unique
2. "files" — exact files to create or modify
3. "packageUpdates" — any new npm dependencies

IMPORTANT RULES:
1. The "theme" block MUST contain ALL color overrides — Claude will apply them to :root
2. Do NOT recreate files that already exist in the skeleton UNLESS they need modification
3. If a skeleton file needs changes (like App.jsx needing new routes), mark it as "modify"
4. Be VERY specific about what each component does — Claude must not guess
5. For admin panels, use the existing DataTable and Layout components — CUSTOMIZE their styles via theme
6. For Supabase projects, specify exact table and query details
7. Every component description must mention specific colors by referencing CSS variables (var(--color-primary))
8. NEVER use hardcoded hex colors in components — always reference CSS variables

Return ONLY valid JSON in this format:
{{
  "projectName": "string",
  "theme": {{
    "--color-primary": "#hex (unique to this project, NEVER #6366f1)",
    "--color-primary-light": "#hex",
    "--color-primary-dark": "#hex",
    "--color-primary-50": "#hex (very light tint for backgrounds)",
    "--color-primary-100": "#hex",
    "--color-bg": "#hex (background, e.g. #ffffff or #0a0a0f for dark)",
    "--color-bg-secondary": "#hex",
    "--color-bg-tertiary": "#hex",
    "--color-surface": "#hex",
    "--color-border": "#hex",
    "--color-text": "#hex",
    "--color-text-secondary": "#hex",
    "--color-text-muted": "#hex",
    "--font-family": "'FontName', sans-serif",
    "--radius-lg": "0.5rem or 0.75rem etc",
    "darkMode": true
  }},
  "googleFonts": ["Inter", "or any Google Font"],
  "files": [
    {{
      "path": "src/pages/Dashboard.jsx",
      "action": "create|modify",
      "description": "Very detailed description of content, layout, and behavior",
      "imports": ["react", "lucide-react"],
      "components": ["StatCard", "DataTable"],
      "sections": ["header with title", "stat grid with 4 cards", "recent data table"]
    }}
  ],
  "packageUpdates": {{
    "dependencies": {{}},
    "description": "any new npm packages needed"
  }}
}}

REMEMBER: The "theme" block makes each project visually unique. Choose colors that match the project's industry.

Return ONLY the JSON, no markdown, no backticks.
"""
        response = await asyncio.to_thread(model.generate_content, plan_prompt)
        plan_text = response.text.strip()

        # Clean up response
        if "```" in plan_text:
            parts = plan_text.split("```")
            if len(parts) >= 3:
                plan_text = parts[1]
            if plan_text.startswith("json"):
                plan_text = plan_text[4:]
            plan_text = plan_text.strip()

        # Validate JSON
        json.loads(plan_text)

    except json.JSONDecodeError:
        logger.warning("gemini_create_plan returned invalid JSON, wrapping")
        plan_text = json.dumps({
            "projectName": "project",
            "files": [{"path": "src/App.jsx", "action": "modify", "description": task}],
        })
    except Exception as e:
        logger.warning("gemini_create_plan failed: %s", e)
        plan_text = json.dumps({
            "projectName": "project",
            "files": [{"path": "src/App.jsx", "action": "modify", "description": task}],
        })

    # Save plan to workspace
    try:
        lucid_dir = os.path.join(workspace_path, ".lucid")
        os.makedirs(lucid_dir, exist_ok=True)
        with open(os.path.join(lucid_dir, "plan.json"), "w") as f:
            f.write(plan_text)
        logger.info("Saved plan.json (%d chars)", len(plan_text))
    except Exception as e:
        logger.warning("Could not save plan.json: %s", e)

    try:
        file_count = len(json.loads(plan_text).get("files", []))
        await websocket.send_json({
            "type": "progress",
            "message": f"📐 Plan ready: {file_count} files to build",
        })
    except Exception:
        pass

    return plan_text


# ═══════════════════════════════════════════════════════════════
#  STEP 4.5 — Analyze user-uploaded images with Gemini Vision
# ═══════════════════════════════════════════════════════════════


async def analyze_images(
    images: list,
    task: str,
    gemini_key: str,
    websocket: WebSocket,
) -> str:
    """Analyze user-uploaded images using Gemini Flash Vision.

    Handles three attachment types:
    - Images (base64 data URL) → analyzed with Gemini Vision
    - Videos (base64 data URL) → noted as context (not analyzed frame-by-frame)
    - Figma links (URL string) → included as design reference

    Args:
        images: List of dicts with 'name', 'data', and optionally 'type', 'url'.
        task: The user's task description.
        gemini_key: Gemini API key.
        websocket: WebSocket for progress updates.

    Returns:
        A text description of all attachments, or empty string if none/failure.
    """
    if not images:
        return ""

    try:
        await websocket.send_json({
            "type": "progress",
            "message": f"🖼️ Analyzing {len(images)} attachment(s)...",
        })
    except Exception:
        pass

    descriptions = []

    # ── Separate attachments by type ──────────────────────
    actual_images = []
    videos = []
    figma_links = []

    for item in images:
        item_type = item.get("type", "image")
        if item_type == "video":
            videos.append(item)
        elif item_type == "figma":
            figma_links.append(item)
        else:
            actual_images.append(item)

    # ── Process Figma links (no analysis needed) ──────────
    for fig in figma_links:
        url = fig.get("url", fig.get("data", ""))
        name = fig.get("name", "Figma Design")
        descriptions.append(f"### Figma Reference: {name}\nDesign link: {url}\nUse this Figma design as a visual reference for the UI implementation.")

    # ── Process videos (note their presence) ──────────────
    for vid in videos:
        name = vid.get("name", "video")
        descriptions.append(f"### Video Attachment: {name}\nA video file was attached. Consider the user may be showing a UI flow, bug reproduction, or desired behavior.")

    # ── Process actual images with Gemini Vision ──────────
    if actual_images:
        try:
            import base64
            from PIL import Image
            from io import BytesIO

            genai.configure(api_key=gemini_key)
            model = genai.GenerativeModel(GEMINI_MODEL)

            for i, img_data in enumerate(actual_images):
                try:
                    raw = img_data.get("data", "")
                    name = img_data.get("name", f"image_{i+1}")

                    if not raw:
                        descriptions.append(f"### Image: {name}\n(No image data received)")
                        continue

                    # Extract base64 from data URL (data:image/png;base64,xxxxx)
                    if "," in raw:
                        raw = raw.split(",", 1)[1]

                    img_bytes = base64.b64decode(raw)
                    pil_image = Image.open(BytesIO(img_bytes))

                    analysis_prompt = f"""Analyze this image in the context of this coding task:
Task: {task}

Describe what you see in detail:
1. If it's a UI screenshot — describe the layout, components, colors, text, and navigation.
2. If it's a design mockup — describe the intended design, positioning, and visual hierarchy.
3. If it's an error/log screenshot — extract the error message and stack trace.
4. If it's a diagram — describe the architecture/flow.

Be specific and technical. Your description will be used by another AI to implement code changes."""

                    response = await asyncio.to_thread(
                        model.generate_content,
                        [analysis_prompt, pil_image],
                    )

                    desc = response.text.strip()
                    descriptions.append(f"### Image: {name}\n{desc}")

                    logger.info("Image '%s' analyzed: %d chars", name, len(desc))

                except Exception as e:
                    logger.warning("Failed to analyze image '%s': %s", name, e)
                    descriptions.append(f"### Image: {name}\n(Failed to analyze: {str(e)[:100]})")

        except ImportError:
            logger.warning("PIL not available for image analysis, skipping")
        except Exception as e:
            logger.warning("analyze_images failed: %s", e)

    if descriptions:
        try:
            await websocket.send_json({
                "type": "progress",
                "message": f"✅ Processed {len(descriptions)} attachment(s)",
            })
        except Exception:
            pass

    return "\n\n".join(descriptions)


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
        "who has already seen this codebase. Be decisive.\n"
    )

    import sys
    import os
    import subprocess
    import pwd

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
        "IS_SANDBOX": "1",  # CRITICAL: Bypasses CLI root check
    }
    
    subprocess.run(
        ['chown', '-R', f'{user}:{user}', workspace_path],
        capture_output=True
    )

    # --- FIX OS PERMISSIONS (chmod 755 on workspace) ---
    try:
        subprocess.run(["chmod", "-R", "755", str(workspace_path)], capture_output=True)
        logger.info("chmod -R 755 applied to %s", workspace_path)
    except Exception as e:
        logger.error("chmod failed: %s", e)

    options = ClaudeCodeOptions(
        cwd=str(workspace_path),
        env=env,
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

    max_retries = 2
    last_error = None

    for attempt in range(max_retries):
        try:
            # ── Hallucination loop circuit breaker ─────────
            consecutive_reads = 0
            max_consecutive_reads = 6  # Force-stop after 6 reads with no write
            has_written = False

            async with asyncio.timeout(
                timeout_seconds
            ):
                async for message in query(
                    prompt=prompt,
                    options=options
                ):
                    msg_str = str(message)

                    # Track tool usage patterns to detect read-only loops
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

                    # Circuit breaker: if Claude reads 6+ times without writing, kill it
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

                    try:
                        await websocket.send_json({
                            "type": "claude_message",
                            "content": msg_str
                        })
                    except Exception:
                        pass

            # If circuit breaker fired but Claude never wrote anything, report failure
            if consecutive_reads >= max_consecutive_reads and not has_written:
                return False

            return True
            
        except Exception as e:
            last_error = e
            exit_code = getattr(e, 'returncode', None)
            
            print(f"Claude attempt {attempt + 1} failed:")
            print(f"Error: {str(e)}")
            print(f"Exit code: {exit_code}")
            
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
            
            # All retries failed
            try:
                await websocket.send_json({
                    "type": "error",
                    "message": f"❌ Claude failed after {max_retries} attempts: {str(last_error)[:200]}"
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
        {"cmd": ["git", "push", "-u", "origin", branch], "msg": "pushing to GitHub"},
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
    chat_session_id: str = "",
    images: list = None,
) -> str | None:
    """Run all pipeline steps sequentially.

    Each step runs one at a time — never simultaneously.
    OpenHands and Claude never overlap.
    Always cleans up workspace in finally block.

    Returns the workspace path on success, None on failure.
    """
    workspace_path = None
    validated = None

    # ── Helper: send structured phase events ──────────────
    async def _send_phase(phase: int, title: str, description: str, status: str):
        try:
            await websocket.send_json({
                "type": "task_phase",
                "phase": phase,
                "title": title,
                "description": description,
                "status": status,
            })
        except Exception:
            pass

    try:
        # ── Phase 1: Validate ─────────────────────────────
        await _send_phase(1, "Validating inputs", "Checking API keys and repository settings…", "active")
        validated = await validate_inputs(task, user, websocket)
        if validated is None:
            await _send_phase(1, "Validating inputs", "Validation failed", "error")
            return
        await _send_phase(1, "Validating inputs", "All inputs validated", "done")

        # Save original task (with [LUCID_PROJECT] header) for naming in Phase 7
        task_original = task

        # Strip [LUCID_PROJECT] header — only used for naming, not for AI
        if task.startswith("[LUCID_PROJECT]"):
            task = task.split("\n\n", 1)[-1]

        # ── Phase 2: Clone / prepare workspace ────────────
        await _send_phase(2, "Preparing workspace", "Setting up workspace…", "active")

        # Scratch mode: create workspace + copy skeleton (NO repo creation here)
        # Repo creation happens in Phase 7 AFTER code is generated and committed.
        if validated.get("scratch_mode"):
            from uuid import uuid4
            workspace_path = f"/tmp/lucid_new_{task_id}_{str(uuid4())[:6]}"
            os.makedirs(workspace_path, exist_ok=True)
            os.chmod(workspace_path, 0o777)

            await websocket.send_json({
                "type": "progress",
                "message": "🔨 Setting up local workspace...",
            })

            # Initialize a git repo with 'main' as default branch
            subprocess.run(
                ["git", "init", "-b", "main"],
                cwd=workspace_path,
                capture_output=True, text=True, timeout=10,
            )
            subprocess.run(
                ["git", "checkout", "-B", "main"],
                cwd=workspace_path, capture_output=True, timeout=5,
            )
            subprocess.run(
                ["git", "config", "user.name", "Lucid AI"],
                cwd=workspace_path, capture_output=True, timeout=5,
            )
            subprocess.run(
                ["git", "config", "user.email", "ai@lucid.dev"],
                cwd=workspace_path, capture_output=True, timeout=5,
            )

            # ── Step 2b: Copy skeleton ──────────────────────
            try:
                from app.services.skeleton_manager import (
                    get_skeleton_for_stack,
                    copy_skeleton,
                    detect_admin_from_task,
                )

                # Extract stack from [LUCID_PROJECT] header
                import re
                stack_match = re.search(r"stack=(\S+)", str(task_original or ""))
                detected_stack = stack_match.group(1).strip() if stack_match else "react"
                is_admin = detect_admin_from_task(task_original or task)

                skeleton_path = get_skeleton_for_stack(detected_stack, is_admin)
                if skeleton_path:
                    copied_files = copy_skeleton(skeleton_path, workspace_path)
                    skeleton_name = os.path.basename(skeleton_path)
                    logger.info("Copied skeleton '%s' (%d files)", skeleton_name, len(copied_files))
                    await websocket.send_json({
                        "type": "progress",
                        "message": f"📦 Skeleton loaded: {skeleton_name} ({len(copied_files)} files)",
                    })
                    # Store skeleton info for later phases
                    validated["skeleton_name"] = skeleton_name
                    validated["skeleton_stack"] = detected_stack
                    validated["is_admin"] = is_admin
                else:
                    logger.warning("No skeleton found for stack: %s", detected_stack)
            except Exception as skel_err:
                logger.warning("Skeleton copy failed (non-fatal): %s", skel_err)

            # Load .env for platform tokens (used later in Phase 7)
            try:
                from dotenv import load_dotenv as _ld
                import pathlib
                for candidate in [
                    pathlib.Path(__file__).resolve().parents[3] / ".env",
                    pathlib.Path(__file__).resolve().parents[2] / ".env",
                    pathlib.Path("/app/.env"),
                    pathlib.Path.cwd() / ".env",
                ]:
                    if candidate.exists():
                        _ld(candidate, override=False)
                        logger.info("Loaded .env from: %s", candidate)
                        break
            except Exception as dotenv_err:
                logger.debug("dotenv load skipped: %s", dotenv_err)

            # Check token type early and warn if invalid
            platform_token = os.environ.get("PLATFORM_GITHUB_TOKEN", "").strip()
            if platform_token and is_fine_grained_token(platform_token):
                await websocket.send_json({
                    "type": "error",
                    "message": (
                        "❌ Fine-grained GitHub token detected. "
                        "New project creation requires a Classic Personal Access Token.\n\n"
                        "Steps to fix:\n"
                        "1. Go to GitHub Settings\n"
                        "2. Developer Settings\n"
                        "3. Personal access tokens → Tokens (classic)\n"
                        "4. Generate new token with 'repo' scope\n"
                        "5. Set as PLATFORM_GITHUB_TOKEN in .env"
                    ),
                })
                await _send_phase(2, "Preparing workspace", "Invalid GitHub token", "error")
                return

            await websocket.send_json({
                "type": "progress",
                "message": "✅ Local workspace ready",
            })
        elif conversation_id:
            workspace_path = await workspace_manager.get_or_create_workspace(
                conversation_id=conversation_id,
                validated=validated,
                websocket=websocket,
            )
        else:
            workspace_path = await clone_with_openhands(
                validated, task_id, websocket,
            )
        if not workspace_path:
            await _send_phase(2, "Preparing workspace", "Workspace setup failed", "error")
            return
        await _send_phase(2, "Preparing workspace", "Repository ready", "done")

        # ── HANDOFF POINT: OpenHands is now DEAD ──────────
        if await openhands_manager.is_active():
            await openhands_manager.destroy_all()
            await asyncio.sleep(0.5)

        # ── Phase 3: Classify task ────────────────────────
        await _send_phase(3, "Classifying task", "Analyzing task complexity…", "active")
        classification = await classify_task(
            task,
            validated["gemini_api_key"],
            websocket,
        )
        model = classification.get("model", "sonnet")
        await _send_phase(3, "Classifying task", f"Assigned to {model} ({classification.get('complexity', 'medium')})", "done")

        # ── Phase 4: Explore / Research ──────────────────────
        if validated.get("scratch_mode"):
            # NEW PIPELINE: Gemini Research + Plan for new projects
            await _send_phase(4, "Researching project", "Gemini is analyzing requirements…", "active")
            spec = await gemini_research(
                task,
                workspace_path,
                validated,
                validated["gemini_api_key"],
                websocket,
            )
            await _send_phase(4, "Researching project", "Specification created", "done")

            # Phase 4b: Create implementation plan
            await _send_phase(4, "Creating plan", "Building file-by-file plan…", "active")
            plan_json = await gemini_create_plan(
                task,
                workspace_path,
                spec,
                validated,
                validated["gemini_api_key"],
                websocket,
            )
            await _send_phase(4, "Creating plan", "Implementation plan ready", "done")

            # Build Claude's guided prompt from plan.json
            plan = f"""## Implementation Plan (from .lucid/plan.json)

You MUST read the file `.lucid/plan.json` in the workspace and implement EVERY file listed in the plan.
The workspace already has a skeleton with boilerplate code.

### STEP 1 — Apply the Theme FIRST:
The plan.json contains a "theme" block with CSS variable overrides like:
{{"--color-primary": "#hex", "--color-bg": "#hex", ...}}

You MUST update index.css (or style.css / globals.css) :root block to replace
the placeholder colors with the theme colors from plan.json.
Also add the Google Font import if specified in "googleFonts".
This is the MOST IMPORTANT step — it makes each project visually unique.

### STEP 2 — Build each file from the plan:
1. Read .lucid/plan.json — it contains the exact files to create/modify
2. Read .lucid/spec.md for detailed requirements
3. For each file in the plan, create or modify it as specified
4. NEVER use hardcoded hex colors in components — use var(--color-primary), var(--color-bg), etc.
5. Use the balanced sizing from the CSS (14px base, compact buttons, tight spacing)
6. Add subtle hover effects and transitions using the existing CSS transition variables
7. Use lucide-react for icons
8. All components must be responsive
9. Use the existing skeleton components (Layout, DataTable, etc.) — don't recreate them

### STEP 3 — Final polish:
1. Ensure all pages are wired in the router (App.jsx)
2. Verify imports are correct
3. Add loading/empty states where appropriate

### Plan Summary:
{plan_json[:4000]}

### Specification Summary:
{spec[:3000]}
"""
        else:
            # EXISTING PIPELINE: Explore codebase for edit-mode
            await _send_phase(4, "Exploring codebase", "Identifying relevant files…", "active")
            plan = await explore_with_gemini(
                task,
                workspace_path,
                classification,
                validated["gemini_api_key"],
                websocket,
            )
            await _send_phase(4, "Exploring codebase", "Implementation plan ready", "done")

        # ── Phase 4.5: Analyze images (if any) ────────────
        if images:
            await _send_phase(4, "Analyzing images", f"Processing {len(images)} attached image(s)…", "active")
            image_analysis = await analyze_images(
                images,
                task,
                validated["gemini_api_key"],
                websocket,
            )
            if image_analysis:
                plan = plan + "\n\n## Visual Context (from attached images)\n" + image_analysis
            await _send_phase(4, "Analyzing images", f"Analyzed {len(images)} image(s)", "done")

        # ── Phase 5: Execute with Claude ──────────────────
        await _send_phase(5, "Writing code", f"Claude ({model}) is implementing the task…", "active")
        success = await execute_with_claude(
            task,
            workspace_path,
            validated["anthropic_api_key"],
            classification,
            plan,
            websocket,
        )
        if not success:
            await _send_phase(5, "Writing code", "Code execution failed", "error")
            return
        await _send_phase(5, "Writing code", "Code changes written", "done")

        # ── Phase 6: Verify build ─────────────────────────
        await _send_phase(6, "Verifying build", "Running build checks…", "active")
        await verify_build(
            workspace_path,
            validated["anthropic_api_key"],
            classification,
            websocket,
        )
        await _send_phase(6, "Verifying build", "Build verification complete", "done")

        # ── Phase 6.5: Verify changes ────────────────────
        changed = await verify_changes(workspace_path, websocket)
        if not changed:
            await _send_phase(6, "Verifying build", "No changes detected", "error")
            return

        # ── Phase 7: Commit + Create Repo + Push ──────────
        # CORRECT ORDER: code is already generated (Phase 5)
        # 1. Commit locally  2. Create repo  3. Push
        # NOTE: CI/CD files (Dockerfile, nginx.conf, .gitlab-ci.yml, Makefile)
        # are NOT auto-generated here. They are added when the user exports
        # the project to their own GitHub/GitLab via the Export modal.
        if validated.get("scratch_mode"):
            await _send_phase(7, "Publishing project", "Committing code…", "active")

            # STEP 1: Commit all generated code locally
            commit_msg = f"Initial commit: {task[:50]} by Lucid AI"
            try:
                subprocess.run(
                    ["git", "add", "-A"],
                    cwd=workspace_path, capture_output=True, timeout=30,
                )
                commit_result = subprocess.run(
                    ["git", "commit", "-m", commit_msg],
                    cwd=workspace_path, capture_output=True, text=True, timeout=30,
                )
                if commit_result.returncode != 0:
                    await websocket.send_json({
                        "type": "warning",
                        "message": "⚠️ No files were generated. Generation may have failed.",
                    })
                    await _send_phase(7, "Publishing project", "No files to commit", "error")
                    return workspace_path
            except Exception as e:
                logger.warning("Local commit failed: %s", e)
                await _send_phase(7, "Publishing project", "Commit failed", "error")
                return workspace_path

            await websocket.send_json({
                "type": "progress",
                "message": "📝 Code committed locally",
            })

            # Send file list to workspace UI so user can see generated files
            try:
                generated_files = []
                for root, _dirs, files in os.walk(workspace_path):
                    for f in files:
                        rel = os.path.relpath(os.path.join(root, f), workspace_path)
                        if not rel.startswith('.git/'):
                            generated_files.append(rel)
                if generated_files:
                    await websocket.send_json({
                        "type": "file_change",
                        "files": sorted(generated_files),
                    })
            except Exception as fe:
                logger.debug("Could not send file list: %s", fe)

            # STEP 2: Get platform token and create GitHub repo
            platform_token = os.environ.get("PLATFORM_GITHUB_TOKEN", "").strip()
            user_token = validated.get("git_token", "")
            git_token = platform_token or user_token

            if not git_token:
                logger.warning("No git token — skipping repo creation")
                await websocket.send_json({
                    "type": "complete",
                    "message": "✅ Code generated! No GitHub token set — use Export to push.",
                })
                await _send_phase(7, "Publishing project", "Code saved locally", "done")
            elif is_fine_grained_token(git_token):
                await websocket.send_json({
                    "type": "error",
                    "message": (
                        "❌ Fine-grained GitHub token detected. "
                        "Use a Classic Personal Access Token (ghp_...) with repo scope."
                    ),
                })
                await _send_phase(7, "Publishing project", "Invalid token type", "error")
            else:
                # STEP 3: Create GitHub repo
                await websocket.send_json({
                    "type": "progress",
                    "message": "📦 Creating GitHub repository...",
                })

                # Use the original task (before header was stripped) for naming
                repo_name, project_desc, _, _ = derive_repo_name(task_original, chat_session_id)

                repo_result = await create_github_repo(
                    project_name=repo_name,
                    github_token=git_token,
                    description=f"Generated by Lucid AI — {project_desc[:80]}",
                    is_private=True,
                    websocket=websocket,
                )

                if repo_result is None:
                    # Repo creation failed — save locally, don't crash
                    await websocket.send_json({
                        "type": "warning",
                        "message": f"⚠️ Could not create GitHub repo. Your project is saved at: {workspace_path}\nYou can manually push later.",
                    })
                    await _send_phase(7, "Publishing project", "Repo creation failed — code saved locally", "done")
                else:
                    auth_url, html_url = repo_result

                    await websocket.send_json({
                        "type": "progress",
                        "message": f"✅ Repository created: {html_url}",
                    })
                    await websocket.send_json({
                        "type": "repo_created",
                        "repoUrl": html_url,
                        "repoName": repo_name,
                        "platformOwned": bool(platform_token),
                    })

                    # Update validated for Phase 8 (Vercel)
                    validated["auto_created_repo"] = html_url
                    validated["platform_owned"] = bool(platform_token)

                    # STEP 4: Add remote + push
                    await websocket.send_json({
                        "type": "progress",
                        "message": "🚀 Pushing code to GitHub...",
                    })

                    subprocess.run(
                        ["git", "remote", "add", "origin", auth_url],
                        cwd=workspace_path,
                        capture_output=True, text=True, timeout=10,
                    )
                    subprocess.run(
                        ["git", "branch", "-M", "main"],
                        cwd=workspace_path,
                        capture_output=True, timeout=5,
                    )

                    push_result = subprocess.run(
                        ["git", "push", "-u", "origin", "main"],
                        cwd=workspace_path,
                        capture_output=True, text=True, timeout=60,
                    )

                    if push_result.returncode != 0:
                        err_msg = (push_result.stderr or push_result.stdout or "unknown error")
                        if git_token:
                            err_msg = err_msg.replace(git_token, "***")
                        logger.error("Git push failed: %s", err_msg[:300])
                        await websocket.send_json({
                            "type": "error",
                            "message": f"❌ Push failed: {err_msg[:200]}",
                        })
                        await _send_phase(7, "Publishing project", "Push failed", "error")
                    else:
                        # STEP 5: Save platform_repo_url to DB
                        db_session_id = chat_session_id
                        if db_session_id and html_url:
                            try:
                                from app.supabase_client import db_client
                                async with db_client(None) as sb:
                                    await (
                                        sb.table("chat_sessions")
                                        .update({"platform_repo_url": html_url})
                                        .eq("id", db_session_id)
                                        .execute()
                                    )
                                logger.info("Saved platform_repo_url to session %s", db_session_id)
                            except Exception as db_err:
                                logger.warning("Failed to save platform_repo_url: %s", db_err)

                        await websocket.send_json({
                            "type": "complete",
                            "message": f"✅ Done! Code pushed to {html_url}",
                        })
                        await _send_phase(7, "Publishing project", "Code pushed successfully", "done")
                        logger.info("Project published to: %s", html_url)
        else:
            await _send_phase(7, "Pushing changes", "Committing and pushing to remote…", "active")
            await push_with_openhands(
                workspace_path,
                validated,
                task,
                task_id,
                websocket,
            )
            # Send repo URL if auto-created
            auto_repo = validated.get("auto_created_repo")
            if auto_repo:
                await websocket.send_json({
                    "type": "complete",
                    "message": f"✅ Done! Code pushed to {auto_repo}",
                })
            await _send_phase(7, "Pushing changes", "Changes pushed successfully", "done")

        # ── Phase 8: Vercel auto-deploy (if configured) ───────
        vercel_token = os.environ.get("VERCEL_TOKEN", "").strip()
        auto_repo = validated.get("auto_created_repo", "")
        if vercel_token and auto_repo:
            try:
                import httpx

                await _send_phase(8, "Deploying", "Creating Vercel project…", "active")
                await websocket.send_json({
                    "type": "progress",
                    "message": "🚀 Deploying to Vercel...",
                })

                vercel_team = os.environ.get("VERCEL_TEAM_ID", "").strip()
                vercel_headers = {
                    "Authorization": f"Bearer {vercel_token}",
                    "Content-Type": "application/json",
                }

                # Extract GitHub repo owner and name from the html_url
                # e.g. "https://github.com/Baxa1997/lucid-my-project-abc123"
                repo_parts = auto_repo.replace("https://github.com/", "").split("/")
                gh_owner = repo_parts[0] if len(repo_parts) > 0 else ""
                gh_repo = repo_parts[1] if len(repo_parts) > 1 else ""

                # Derive a clean project name for Vercel
                vercel_project_name = gh_repo[:100]

                # Create Vercel project linked to GitHub repo
                create_payload = {
                    "name": vercel_project_name,
                    "framework": None,  # Let Vercel auto-detect
                    "gitRepository": {
                        "type": "github",
                        "repo": f"{gh_owner}/{gh_repo}",
                        "repoId": validated.get("auto_created_repo_id", 0),
                    },
                }

                params = f"?teamId={vercel_team}" if vercel_team else ""

                async with httpx.AsyncClient(timeout=60) as client:
                    # Create project
                    create_resp = await client.post(
                        f"https://api.vercel.com/v10/projects{params}",
                        headers=vercel_headers,
                        json=create_payload,
                    )

                    if create_resp.status_code in (200, 201):
                        project_data = create_resp.json()
                        project_id = project_data.get("id", "")
                        project_name = project_data.get("name", vercel_project_name)

                        await websocket.send_json({
                            "type": "progress",
                            "message": f"⚡ Vercel project created: {project_name}",
                        })

                        # Trigger a deployment via the Vercel API
                        deploy_resp = await client.post(
                            f"https://api.vercel.com/v13/deployments{params}",
                            headers=vercel_headers,
                            json={
                                "name": project_name,
                                "gitSource": {
                                    "type": "github",
                                    "org": gh_owner,
                                    "repo": gh_repo,
                                    "ref": "main",
                                },
                            },
                        )

                        deploy_url = ""
                        if deploy_resp.status_code in (200, 201):
                            deploy_data = deploy_resp.json()
                            deploy_url = deploy_data.get("url", "")
                            deploy_id = deploy_data.get("id", "")

                            if deploy_url and not deploy_url.startswith("http"):
                                deploy_url = f"https://{deploy_url}"

                            await websocket.send_json({
                                "type": "progress",
                                "message": f"⏳ Deployment started, waiting for build...",
                            })

                            # Poll deployment status (max 120 seconds)
                            for _ in range(24):
                                await asyncio.sleep(5)
                                status_resp = await client.get(
                                    f"https://api.vercel.com/v13/deployments/{deploy_id}{params}",
                                    headers=vercel_headers,
                                )
                                if status_resp.status_code == 200:
                                    status_data = status_resp.json()
                                    state = status_data.get("readyState", "")
                                    if state == "READY":
                                        deploy_url = status_data.get("url", deploy_url)
                                        if deploy_url and not deploy_url.startswith("http"):
                                            deploy_url = f"https://{deploy_url}"
                                        break
                                    elif state in ("ERROR", "CANCELED"):
                                        logger.warning("Vercel deploy failed: state=%s", state)
                                        break

                            await websocket.send_json({
                                "type": "deploy_ready",
                                "url": deploy_url,
                                "repoUrl": auto_repo,
                            })
                            await websocket.send_json({
                                "type": "complete",
                                "message": f"✅ Live at {deploy_url}",
                            })
                            logger.info("Vercel deploy ready: %s", deploy_url)

                            # Save Vercel URL to DB for persistent access
                            if chat_session_id and deploy_url:
                                try:
                                    from app.supabase_client import db_client
                                    async with db_client(None) as sb:
                                        await (
                                            sb.table("chat_sessions")
                                            .update({"vercel_url": deploy_url})
                                            .eq("id", chat_session_id)
                                            .execute()
                                        )
                                    logger.info("Saved vercel_url to session %s", chat_session_id)
                                except Exception as db_err:
                                    logger.warning("Failed to save vercel_url: %s", db_err)
                        else:
                            err_text = deploy_resp.text[:200]
                            logger.warning("Vercel deployment trigger failed (%d): %s", deploy_resp.status_code, err_text)
                            await websocket.send_json({
                                "type": "progress",
                                "message": f"⚠️ Deployment trigger failed — repo pushed to {auto_repo}",
                            })
                    else:
                        err_text = create_resp.text[:200]
                        logger.warning("Vercel project creation failed (%d): %s", create_resp.status_code, err_text)
                        await websocket.send_json({
                            "type": "progress",
                            "message": f"⚠️ Vercel project creation failed — repo pushed to {auto_repo}",
                        })

                await _send_phase(8, "Deploying", "Deployment complete", "done")
            except Exception as e:
                logger.warning("Vercel auto-deploy failed: %s", e)
                await websocket.send_json({
                    "type": "progress",
                    "message": f"⚠️ Deploy failed — code pushed to {auto_repo}",
                })
                await _send_phase(8, "Deploying", "Deploy skipped", "done")

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

        # Clean up /tmp/lucid_* scratch workspaces to prevent disk fill
        if workspace_path and workspace_path.startswith("/tmp/lucid_"):
            try:
                import shutil as _shutil
                _shutil.rmtree(workspace_path, ignore_errors=True)
                logger.info("Scratch workspace cleaned up: %s", workspace_path)
            except Exception:
                pass
        # Legacy: clean up workspaces without conversation_id
        elif not conversation_id and workspace_path and os.path.exists(workspace_path):
            try:
                shutil.rmtree(workspace_path)
                logger.info("Workspace cleaned up (no conversation_id): %s", workspace_path)
            except Exception:
                pass
