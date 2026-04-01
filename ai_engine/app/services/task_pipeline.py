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
import pathlib

from fastapi import WebSocket
from claude_code_sdk.query import query
from claude_code_sdk.types import ClaudeCodeOptions, PermissionResultAllow
import google.generativeai as genai

from app.services.openhands_manager import openhands_manager
from app.services.workspace_manager import workspace_manager

logger = logging.getLogger(__name__)

# ── Centralized Gemini Models ─────────────────────────────────
# gemini-2.0-flash: fast + cheap for classification, research, analysis (8K output)
# gemini-2.5-flash: for blueprint generation (65K output — no truncation)
GEMINI_MODEL = "gemini-2.0-flash"
GEMINI_BLUEPRINT_MODEL = "gemini-2.5-flash"

# ── Fallback Gemini API Key ──────────────────────────────────
# Used when the user doesn't have their own key in Settings.
_FALLBACK_GEMINI_KEY = os.environ.get(
    "GOOGLE_API_KEY",
    "AIzaSyDwMFS1PUpONEyTEz_nVgCF30-lvXb3gbY"
).strip()

# ── PLATFORM_GITHUB_TOKEN — loaded ONCE at module startup ────────────────────
# Load from environment first; fall back to the nearest .env file on disk.
# This avoids repeated lazy dotenv reads scattered across the pipeline and
# surfaces misconfiguration immediately when the server starts.
def _load_platform_token() -> str:
    """Return PLATFORM_GITHUB_TOKEN from env or the nearest .env file."""
    # 1. Already in environment (Docker / systemd / cloud-run injected it)
    token = os.environ.get("PLATFORM_GITHUB_TOKEN", "").strip()
    if token:
        return token

    # 2. Walk up from this file to find a .env file
    _candidates = [
        pathlib.Path(__file__).resolve().parents[3] / ".env",
        pathlib.Path(__file__).resolve().parents[2] / ".env",
        pathlib.Path("/app/.env"),
        pathlib.Path.cwd() / ".env",
    ]
    for _candidate in _candidates:
        if _candidate.exists():
            try:
                from dotenv import load_dotenv
                load_dotenv(_candidate, override=False)
                token = os.environ.get("PLATFORM_GITHUB_TOKEN", "").strip()
                if token:
                    logger.info(
                        "PLATFORM_GITHUB_TOKEN loaded from %s (prefix: %s...)",
                        _candidate,
                        token[:7],
                    )
                    return token
            except Exception as _e:
                logger.warning("dotenv load failed for %s: %s", _candidate, _e)

    logger.warning(
        "PLATFORM_GITHUB_TOKEN not found in environment or any .env file. "
        "New-project template cloning and GitHub repo creation will be disabled."
    )
    return ""


PLATFORM_GITHUB_TOKEN: str = _load_platform_token()

async def _approve_all_tools(tool_name: str, input_data: dict, context) -> PermissionResultAllow:
    return PermissionResultAllow()


async def _fix_broken_layout_imports(workspace_path: str, websocket=None):
    """Pre-build safety net: fix JS/JSX imports that reference non-existent component files.

    Common scenario: Claude rewrites layout.js to import 'Navbar'/'Footer'
    instead of the template's actual names (MarketingHeader/MarketingFooter).
    This function scans layout and page files for broken imports and auto-corrects them.
    """
    import re as _re_fix
    import glob

    layout_dir = os.path.join(workspace_path, "src", "components", "layout")
    if not os.path.isdir(layout_dir):
        return  # No layout components → nothing to fix

    # Map of actual component files that exist
    actual_files = {}
    for f in os.listdir(layout_dir):
        name_no_ext = os.path.splitext(f)[0]
        actual_files[name_no_ext.lower()] = name_no_ext  # lowered key → original name

    # Scan layout.js / layout.jsx / layout.tsx files for broken imports
    target_files = glob.glob(os.path.join(workspace_path, "src", "**", "layout.js"), recursive=True)
    target_files += glob.glob(os.path.join(workspace_path, "src", "**", "layout.jsx"), recursive=True)
    target_files += glob.glob(os.path.join(workspace_path, "src", "**", "layout.tsx"), recursive=True)

    # Also check App.vue / App.jsx
    for app_name in ["App.vue", "App.jsx", "App.tsx"]:
        app_path = os.path.join(workspace_path, "src", app_name)
        if os.path.isfile(app_path):
            target_files.append(app_path)

    # Known renames: Claude's generic names → likely template names
    _rename_map = {
        "navbar": ["marketingheader", "appheader", "header"],
        "footer": ["marketingfooter", "appfooter"],
        "sidebar": ["appsidebar"],
        "header": ["marketingheader", "appheader"],
    }

    fixes_made = 0
    for target_file in target_files:
        try:
            content = open(target_file, "r").read()
            original = content

            # Find all component imports from layout directory
            import_pattern = _re_fix.compile(
                r"""(import\s+\{?\s*)(\w+)(\s*\}?\s*from\s*['"][^'"]*?/layout/)(\w+)(['"])""",
            )
            for match in import_pattern.finditer(content):
                imported_name = match.group(4)  # The component file name
                if imported_name.lower() not in actual_files:
                    # This import references a file that doesn't exist!
                    # Try to find the correct name from rename map
                    wanted = _rename_map.get(imported_name.lower(), [])
                    replacement = None
                    for candidate in wanted:
                        if candidate in actual_files:
                            replacement = actual_files[candidate]
                            break

                    if not replacement:
                        # Fallback: find ANY file with similar purpose
                        for key, actual_name in actual_files.items():
                            if "header" in key and "header" in imported_name.lower():
                                replacement = actual_name
                                break
                            if "footer" in key and "footer" in imported_name.lower():
                                replacement = actual_name
                                break

                    if replacement:
                        old_import = match.group(0)
                        # Also fix the imported binding name
                        old_binding = match.group(2)
                        new_import = old_import.replace(imported_name, replacement)
                        if old_binding.lower() != replacement.lower():
                            new_import = new_import.replace(old_binding, replacement, 1)
                        content = content.replace(old_import, new_import)

                        # Also fix JSX usage: <Navbar /> → <MarketingHeader />
                        content = _re_fix.sub(
                            rf'<{old_binding}(\s|/|>)',
                            f'<{replacement}\\1',
                            content,
                        )
                        content = _re_fix.sub(
                            rf'</{old_binding}>',
                            f'</{replacement}>',
                            content,
                        )
                        fixes_made += 1

            if content != original:
                with open(target_file, "w") as f:
                    f.write(content)
                rel = os.path.relpath(target_file, workspace_path)
                logger.info("Pre-build import fixer: fixed imports in %s", rel)
                if websocket:
                    try:
                        await websocket.send_json({
                            "type": "progress",
                            "message": f"🔧 Auto-fixed broken imports in {rel}",
                        })
                    except Exception:
                        pass
        except Exception as _e:
            logger.warning("Pre-build import fixer: error scanning %s: %s", target_file, _e)

    if fixes_made:
        logger.info("Pre-build import fixer: %d imports auto-corrected", fixes_made)

# ═══════════════════════════════════════════════════════════════
#  HELPERS — GitHub token detection + repo creation
# ═══════════════════════════════════════════════════════════════

def is_fine_grained_token(token: str) -> bool:
    """Detect fine-grained tokens which cannot create repos.
    Fine-grained: github_pat_...   Classic: ghp_... or gho_..."""
    return token.startswith("github_pat_")


def _derive_html_url(clone_url: str, token: str = "") -> str:
    """Derive a browser-viewable GitHub HTML URL from an authenticated clone URL."""
    url = clone_url or ""
    if token and f"{token}@" in url:
        url = url.replace(f"{token}@", "")
    elif "@github.com" in url:
        idx = url.find("@github.com")
        url = "https://github.com" + url[idx + len("@github.com"):]
    if url.endswith(".git"):
        url = url[:-4]
    return url.rstrip("/")


# ── Template Registry ─────────────────────────────────────────────────────────
# Maps stack names (from [LUCID_PROJECT] header) to the LucidSoftware-tech
# template repos. The backend clones these DIRECTLY — no intermediate repo needed.
_TEMPLATE_REGISTRY: dict[str, str] = {
    "nextjs":          "LucidSoftware-tech/lucid-template-nextjs-website",
    "nextjs-website":  "LucidSoftware-tech/lucid-template-nextjs-website",
    "react":           "LucidSoftware-tech/lucid-template-react-admin",
    "react-admin":     "LucidSoftware-tech/lucid-template-react-admin",
    "vue":             "LucidSoftware-tech/lucid-template-vue-admin",
    "vue-admin":       "LucidSoftware-tech/lucid-template-vue-admin",
}

_PLATFORM_ORG = "LucidSoftware-tech"


def _resolve_template_repo(stack: str) -> str:
    """Return the full org/repo slug for a given stack name, or empty string."""
    key = (stack or "").strip().lower()
    return _TEMPLATE_REGISTRY.get(key, "")


def _sanitize_repo_name(raw: str, max_len: int = 50) -> str:
    """Turn any string into a valid GitHub repo name slug."""
    import re as _re
    slug = _re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")
    return slug[:max_len] or "lucid-project"


async def _create_github_repo(repo_name: str, token: str, description: str = "") -> dict:
    """Create a new private GitHub repo for the authenticated user via REST API.

    Returns the repo JSON from GitHub (includes html_url, clone_url, etc.)
    Raises RuntimeError on failure.
    """
    import httpx
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    
    # Since LucidSoftware-tech is a User account and the token belongs to it,
    # we must use the /user/repos endpoint to create a repository.
    url = "https://api.github.com/user/repos"
    
    payload = {
        "name": repo_name,
        "private": True,
        "auto_init": False,
        "description": description or "Generated by Lucid AI",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, json=payload, headers=headers)
    data = resp.json()
    if resp.status_code not in (200, 201):
        raise RuntimeError(
            f"GitHub API {resp.status_code}: {data.get('message', 'unknown')} "
            f"— errors: {data.get('errors', [])}"
        )
    return data





def detect_package_manager(workspace_path: str, user_preference: str = "npm") -> str:
    """Auto-detect package manager from lock files in the workspace.

    Priority: lock file detection > user preference > npm fallback.
    Supports: npm, yarn, pnpm, bun.

    SAFETY: If a lock file is found but the binary doesn't exist (e.g., pnpm
    not installed in Docker), falls back to npm and removes the conflicting
    lock file to prevent build errors.
    """
    import shutil

    def _binary_exists(name: str) -> bool:
        return shutil.which(name) is not None

    detected = None
    lock_file = None

    if os.path.exists(os.path.join(workspace_path, "yarn.lock")):
        detected, lock_file = "yarn", "yarn.lock"
    elif os.path.exists(os.path.join(workspace_path, "pnpm-lock.yaml")):
        detected, lock_file = "pnpm", "pnpm-lock.yaml"
    elif os.path.exists(os.path.join(workspace_path, "bun.lockb")):
        detected, lock_file = "bun", "bun.lockb"
    elif os.path.exists(os.path.join(workspace_path, "package-lock.json")):
        detected, lock_file = "npm", "package-lock.json"

    if detected:
        if _binary_exists(detected):
            return detected
        else:
            # Binary not installed — fall back to npm
            logger.warning(
                "detect_package_manager: %s lock file found but '%s' binary not installed — falling back to npm",
                lock_file, detected,
            )
            # Remove the lock file so npm doesn't conflict
            try:
                lf_path = os.path.join(workspace_path, lock_file)
                if os.path.isfile(lf_path):
                    os.remove(lf_path)
                    logger.info("Removed %s to allow npm install", lock_file)
            except Exception:
                pass
            return "npm"

    # No lock file found — use user preference (from settings)
    pref = (user_preference or "npm").strip().lower()
    return pref if pref in ("npm", "yarn", "pnpm", "bun") else "npm"


def _pm_install_cmd(pm: str, packages: list = None) -> list:
    """Build an install command list for the given package manager."""
    if packages:
        # Installing specific packages
        if pm == "yarn":
            return ["yarn", "add"] + packages
        elif pm == "pnpm":
            return ["pnpm", "add"] + packages
        elif pm == "bun":
            return ["bun", "add"] + packages
        else:
            return ["npm", "install", "--save", "--no-audit", "--no-fund"] + packages
    else:
        # Installing all from package.json
        if pm == "yarn":
            return ["yarn", "install", "--non-interactive"]
        elif pm == "pnpm":
            return ["pnpm", "install", "--no-frozen-lockfile"]
        elif pm == "bun":
            return ["bun", "install"]
        else:
            return ["npm", "install", "--no-audit", "--no-fund"]


def _pm_env(pm: str) -> dict:
    """Build environment variables for running a package manager."""
    import pwd as _pwd
    try:
        _lu = _pwd.getpwnam("lucidai")
        _home = _lu.pw_dir
        _user = "lucidai"
    except KeyError:
        _home = "/root"
        _user = "root"

    env = {
        **os.environ,
        "HOME": _home,
        "USER": _user,
        "PATH": f"{_home}/.npm-global/bin:/usr/local/bin:/usr/bin:/bin",
        "npm_config_loglevel": "error",
    }
    # Enable corepack for yarn/pnpm if needed
    if pm in ("yarn", "pnpm"):
        env["COREPACK_ENABLE_STRICT"] = "0"
    return env


def derive_repo_name(task: str, chat_session_id: str = "") -> tuple[str, str]:
    """Derive a clean repo name from the [LUCID_PROJECT] header or task text.

    Rules:
        1. Use description from [LUCID_PROJECT] header if present
        2. Otherwise extract first meaningful words from actual task
        3. NEVER use conversation context/title as repo name
        4. Suffix based on stack: nextjs/react → -frontend, fastapi → -backend
        5. Clean: lowercase, hyphens only, max 30 chars

    Returns (repo_name, project_description, project_stack, is_admin).
    """
    import re

    project_desc = ""
    project_stack = ""
    project_backend = ""

    # 1. Parse [LUCID_PROJECT] header — this is the ONLY reliable source
    header_match = re.match(
        r"\[LUCID_PROJECT\]\s*description=(.+?)\s*\|\s*stack=(\S+)\s*\|\s*backend=(\S+)",
        str(task or ""),
    )
    if header_match:
        project_desc = header_match.group(1).strip()
        project_stack = header_match.group(2).strip()
        project_backend = header_match.group(3).strip()
    else:
        # Fallback: extract from raw task text, but strip ALL context noise first
        raw = str(task or "").strip()

        # Remove everything that looks like context replay (conversation history)
        # These patterns indicate injected context, NOT the user's actual task
        context_markers = [
            "## previous conversation context",
            "## what happened in the previous session",
            "previous-conversation-context:",
            "previous conversation context:",
            "CURRENT TASK:",
        ]
        for marker in context_markers:
            idx = raw.lower().find(marker.lower())
            if idx >= 0:
                # If "CURRENT TASK:" found, take text AFTER it (that's the real task)
                if "current task" in marker.lower():
                    raw = raw[idx + len(marker):].strip()
                else:
                    # For context headers, check if there's a "CURRENT TASK:" after
                    current_idx = raw.lower().find("current task:", idx)
                    if current_idx >= 0:
                        raw = raw[current_idx + len("current task:"):].strip()
                    else:
                        # Last resort: take from after the context marker
                        raw = raw[idx + len(marker):].strip()

        # Remove [LUCID_PROJECT] header if still present
        raw = re.sub(r"\[LUCID_PROJECT\].*?\n\n?", "", raw, flags=re.DOTALL).strip()

        # Remove agent guidelines noise
        raw = re.split(r"\n---\n|\n##\s", raw)[0].strip()

        # Take first 100 chars of what remains
        project_desc = raw[:100]

    # 2. Extract clean project name from description
    name = project_desc.lower().strip()

    # Remove common action prefixes
    for prefix in [
        "build me a ", "build a ", "create a ", "make a ",
        "develop a ", "generate a ", "make me a ",
        "build me ", "create ", "make ", "just a ",
        "just ", "simple ", "overall ", "new ",
    ]:
        if name.startswith(prefix):
            name = name[len(prefix):]
            break

    # Remove trailing noise (stop at conjunctions/prepositions)
    name = re.split(r"\bwith\b|\band\b|\bthat\b|\busing\b|\bfor\b|\bwhich\b", name)[0].strip()

    # Take only first 4 meaningful words (avoids long descriptions)
    words = [w for w in name.split() if len(w) > 1][:4]
    name = " ".join(words) if words else "project"

    # Slugify with hyphens (not underscores) — modern convention
    slug = re.sub(r"[^a-z0-9]+", "-", name).strip("-")[:25]
    slug = slug or "project"

    # 3. Determine type suffix based on stack AND description
    desc_lower = project_desc.lower()
    is_admin = False

    if any(w in desc_lower for w in ["admin", "dashboard", "panel", "cms", "crm", "backoffice"]):
        type_suffix = "-admin-frontend"
        is_admin = True
    elif any(w in desc_lower for w in ["api", "backend", "server", "microservice"]):
        # Stack-aware backend suffix
        if project_stack in ("fastapi", "python", "django", "flask"):
            type_suffix = "-backend"
        elif project_stack in ("nodejs", "node", "express"):
            type_suffix = "-api"
        else:
            type_suffix = "-service"
    elif project_stack in ("fastapi", "python", "django", "flask"):
        type_suffix = "-backend"
    elif project_stack in ("nodejs", "node", "express"):
        type_suffix = "-api"
    else:
        type_suffix = "-frontend"

    # 4. Build final name — clean, max 30 chars total
    repo_name = f"{slug}{type_suffix}"
    if len(repo_name) > 30:
        # Trim slug to fit within 30 chars
        max_slug = 30 - len(type_suffix)
        slug = slug[:max_slug].rstrip("-")
        repo_name = f"{slug}{type_suffix}"

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
    chat_session_id: str = "",
) -> dict | None:
    """Validate ALL inputs and build repo_url.

    Returns a validated dict with all clean values, or None on failure.
    Supports SCRATCH MODE — when no repo is configured, returns
    scratch_mode=True so the pipeline creates a local workspace.

    FOLLOW-UP DETECTION: If chat_session_id is provided and no repo is
    configured, checks chat_sessions table for platform_repo_url. If found,
    uses the existing repo instead of entering scratch mode.
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
            # Fallback to platform key — don't block the user
            gemini_key = _FALLBACK_GEMINI_KEY
            logger.info("User has no Gemini key — using platform fallback")

        # ══════════════════════════════════════════════════════════════════
        # ── EARLY EXIT: [LUCID_PROJECT] wizard header detected ────────────
        # When the [LUCID_PROJECT] header is present, this is ALWAYS a new
        # wizard-initiated project. We skip ALL personal git repo validation
        # and go straight to template mode using the backend template registry.
        # ══════════════════════════════════════════════════════════════════
        import re as _re_early
        _task_str = str(task) if task else ""
        # Find the [LUCID_PROJECT] header line anywhere in the first 2000 chars
        _header_raw = ""
        for _ln in _task_str[:2000].split("\n"):
            if "[LUCID_PROJECT]" in _ln:
                _header_raw = _ln.strip()
                break
        _is_wizard_task = bool(_header_raw)

        if _is_wizard_task:
            logger.info("NEW_PROJECT_MODE DETECTED — header: %s", _header_raw[:300])

            # ── Extract fields from header ────────────────────────────────
            def _hdr(field: str) -> str:
                m = _re_early.search(rf"{field}=([^|]+)", _header_raw)
                return m.group(1).strip() if m else ""

            _stack       = _hdr("stack")        # e.g. "nextjs"
            _description = _hdr("description")  # e.g. "netflix style blog"
            _backend     = _hdr("backend")      # e.g. "none"

            # ── Resolve which template repo to clone (backend registry) ────
            # We NO LONGER depend on clone_url from the frontend.
            # The backend knows all templates via _TEMPLATE_REGISTRY.
            _template_repo_slug = _resolve_template_repo(_stack)  # e.g. "LucidSoftware-tech/lucid-template-nextjs-website"

            # ── Use module-level PLATFORM_GITHUB_TOKEN (loaded at startup) ──
            _platform_token = PLATFORM_GITHUB_TOKEN

            if not _platform_token:
                logger.warning("NEW_PROJECT_MODE: PLATFORM_GITHUB_TOKEN not set — will fall back to local skeleton")

            # ── Build authenticated clone URL ─────────────────────────────
            _template_clone_url = ""
            if _template_repo_slug and _platform_token:
                _template_clone_url = (
                    f"https://{_platform_token}@github.com/{_template_repo_slug}.git"
                )
                logger.info(
                    "NEW_PROJECT_MODE: template=%s  clone_url=%s...",
                    _template_repo_slug,
                    _template_clone_url[:60],
                )
            elif not _template_repo_slug:
                logger.warning("NEW_PROJECT_MODE: unknown stack '%s' — local skeleton fallback", _stack)
            
            # ── Derive target project name for Phase 7 ────────────────────
            # Phase 7 will CREATE a brand-new GitHub repo with this name.
            _project_name = _sanitize_repo_name(_description or _stack or "lucid-project")

            msg = (
                f"✅ Inputs validated (template mode — cloning {_template_repo_slug or 'local skeleton'})"
                if _template_clone_url else
                "✅ Inputs validated (template mode — local skeleton fallback)"
            )
            await websocket.send_json({"type": "progress", "message": msg})

            return {
                "anthropic_api_key": str(api_key).strip(),
                "gemini_api_key":    str(gemini_key).strip(),
                "git_provider":      "github",
                "repo_url":          "",
                "branch":            "main",
                "git_token":         _platform_token,
                "scratch_mode":      False,
                "new_project_mode":  True,
                "package_manager":   user.get("package_manager", "npm"),
                # Phase 2: clone this template into workspace
                "template_clone_url":      _template_clone_url,
                "template_repo_html_url":  f"https://github.com/{_template_repo_slug}" if _template_repo_slug else "",
                # Phase 5: stack/framework for batch executor
                "project_stack":     _stack,
                # Phase 7: create a new repo with this name and push
                "project_name":      _project_name,
                "project_description": _description,
            }
        # ══════════════════════════════════════════════════════════════════


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
            # ── FOLLOW-UP CHECK: Look for existing repo in chat_sessions ──
            # If this session already created a repo (wizard project),
            # reuse it instead of entering scratch mode.
            if chat_session_id:
                try:
                    from app.supabase_client import db_client
                    async with db_client(None) as sb:
                        result = await (
                            sb.table("chat_sessions")
                            .select("platform_repo_url")
                            .eq("id", chat_session_id)
                            .maybe_single()
                            .execute()
                        )
                    if result.data and result.data.get("platform_repo_url"):
                        existing_repo_url = result.data["platform_repo_url"]
                        # Use module-level PLATFORM_GITHUB_TOKEN (loaded at startup)
                        platform_token = PLATFORM_GITHUB_TOKEN
                        if platform_token:
                            # Extract owner/repo from URL
                            # e.g. "https://github.com/Baxa1997/my_project_frontend"
                            repo_path = existing_repo_url.replace("https://github.com/", "").strip("/")
                            if repo_path.endswith(".git"):
                                repo_path = repo_path[:-4]
                            repo_url = f"https://{platform_token}@github.com/{repo_path}.git"
                            token = platform_token
                            git_provider = "github"
                            scratch_mode = False
                            branch = "main"
                            logger.info(
                                "FOLLOW-UP MODE: Reusing existing repo from chat_session %s: %s",
                                chat_session_id, existing_repo_url,
                            )
                            await websocket.send_json({
                                "type": "progress",
                                "message": f"📂 Using existing project repo: {existing_repo_url}",
                            })
                        else:
                            logger.warning("FOLLOW-UP: Found platform_repo_url but no PLATFORM_GITHUB_TOKEN")
                            scratch_mode = True
                    else:
                        scratch_mode = True
                        logger.info("SCRATCH MODE: No repository configured — creating local workspace")
                except Exception as db_err:
                    logger.warning("Failed to check chat_sessions for platform_repo_url: %s", db_err)
                    scratch_mode = True
                    logger.info("SCRATCH MODE: No repository configured — creating local workspace")
            else:
                scratch_mode = True
                logger.info("SCRATCH MODE: No repository configured — creating local workspace")
        else:
            # Normal mode: validate everything
            if git_provider == "github":
                if not token or not str(token).strip():
                    # ── PLATFORM TOKEN FALLBACK ────────────────────────────────
                    # Wizard-created template projects are owned by the platform org.
                    # The frontend never sends the platform token — load it from env.
                    # Use module-level PLATFORM_GITHUB_TOKEN (loaded at startup)
                    platform_token = PLATFORM_GITHUB_TOKEN
                    if platform_token:
                        token = platform_token
                        logger.info("validate_inputs: using PLATFORM_GITHUB_TOKEN for platform-owned repo")
                    else:
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

        # Non-wizard normal tasks (user's own repo)
        # new_project_mode is always False here — wizard tasks returned early above.
        validated = {
            "anthropic_api_key": str(api_key).strip(),
            "gemini_api_key": str(gemini_key).strip(),
            "git_provider": git_provider,
            "repo_url": repo_url or "",
            "branch": branch,
            "git_token": token or "",
            "scratch_mode": scratch_mode,
            "new_project_mode": False,
            "package_manager": user.get("package_manager", "npm"),
            "template_clone_url": "",
            "template_repo_html_url": "",
        }

        mode_label = " (scratch mode)" if scratch_mode else ""
        await websocket.send_json({
            "type": "progress",
            "message": f"✅ Inputs validated{mode_label}",
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
3. Detect package manager and install:
   - If yarn.lock exists: yarn install
   - If pnpm-lock.yaml exists: pnpm install
   - If bun.lockb exists: bun install
   - Otherwise if package.json exists: npm install
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

    # Read existing skeleton file tree AND key file contents
    file_tree = []
    for root, dirs, files in os.walk(workspace_path):
        dirs[:] = [d for d in dirs if d not in {".git", "node_modules", "__pycache__"}]
        for f in files:
            rel = os.path.relpath(os.path.join(root, f), workspace_path)
            file_tree.append(rel)
    file_tree_str = "\n".join(file_tree)

    # Read key template files so Gemini knows what ALREADY EXISTS
    _spec_stack = (validated.get("project_stack", "") or validated.get("skeleton_stack", "") or "").lower()
    _spec_key_files_candidates = []
    if "nextjs" in _spec_stack or "next" in _spec_stack:
        _spec_key_files_candidates = [
            "src/app/globals.css", "src/app/layout.js", "src/app/layout.tsx",
            "src/app/page.js", "src/app/page.tsx",
            "src/lib/config.js", "src/lib/config.ts",
            "src/components/Navbar.jsx", "src/components/Footer.jsx",
        ]
    elif "vue" in _spec_stack:
        _spec_key_files_candidates = [
            "src/App.vue", "src/main.ts", "src/assets/main.css",
            "src/router/index.ts", "src/components/layout/AppSidebar.vue",
        ]
    else:
        _spec_key_files_candidates = [
            "src/index.css", "src/App.jsx", "src/App.tsx",
            "src/components/Layout.jsx", "src/components/Sidebar.jsx",
        ]

    _spec_inlined = {}
    for _sf in _spec_key_files_candidates:
        _sf_abs = os.path.join(workspace_path, _sf)
        if os.path.isfile(_sf_abs):
            try:
                with open(_sf_abs, "r", errors="replace") as _fp:
                    _sc = _fp.read()
                if len(_sc) < 6000:
                    _spec_inlined[_sf] = _sc
            except Exception:
                pass

    _template_content_block = ""
    if _spec_inlined:
        _tc_parts = []
        for _tp, _tc in _spec_inlined.items():
            _tc_parts.append(f"### {_tp}\n```\n{_tc}\n```")
        _template_content_block = (
            "\n\nKEY TEMPLATE FILES (already in workspace — do NOT recreate, only modify):\n"
            + "\n\n".join(_tc_parts)
        )

    skeleton_name = validated.get("skeleton_name", "unknown")
    is_admin = validated.get("is_admin", False)

    try:
        genai.configure(api_key=gemini_key)
        model = genai.GenerativeModel(GEMINI_MODEL)

        spec_prompt = f"""You are a world-class product researcher and UX designer.
A user wants to build a COMPLETE, PRODUCTION-READY project. Your job is to RESEARCH what this type of project actually needs in the real world, then write a detailed specification.

## CRITICAL: EXPAND VAGUE REQUESTS
The user may give a very short or vague request like "I need a CRM" or "movie website" or "build SaaS tool".
YOUR JOB is to turn that into a COMPREHENSIVE, DETAILED specification as if you were a senior product manager.
- "CRM system" → research what Salesforce, HubSpot, Pipedrive do → define pipelines, contacts, deals, activities, reports pages
- "movie website" → research what IMDb, Letterboxd, Netflix do → define catalog, movie details, genres, reviews, watchlist pages
- "restaurant" → research what real restaurant sites do → define menu, reservations, about, gallery, contact pages
DO NOT generate a generic template. RESEARCH this specific niche.

## HOW TO RESEARCH
1. Think about REAL examples of this type of project (e.g., if they want a "Netflix blog", think about Netflix's actual blog, Medium, Substack, Ghost)
2. What pages do REAL projects like this have? (NOT every project needs the same pages)
3. What sections does each page include?
4. What makes a project in this niche feel professional and complete?
5. What features differentiate a "$50 template" from a "$5000 custom build"?

USER REQUEST: {task}

TEMPLATE: {skeleton_name} (this is ONLY the starting codebase — all content, design, and logic must be built from scratch)
IS ADMIN PANEL: {is_admin}

EXISTING TEMPLATE FILES (starting code — you are building ON TOP of this):
{file_tree_str}
{_template_content_block}

REFERENCE PATTERNS (for inspiration only):
{patterns_text[:3000]}

Write a COMPREHENSIVE specification. The template provides code structure — YOUR spec defines what the actual PRODUCT looks like.

---

## Project Overview
- What this project does and WHO uses it
- What problem it solves
- 3-5 key differentiators that make it feel premium

## MVP Feature List
Research what THIS SPECIFIC type of project needs. Do NOT use a generic list.
Think: what would a real user of this product expect to see?
- List EVERY page and feature the project needs to feel COMPLETE and PRODUCTION-READY
- Include only pages and features that are RELEVANT to this specific project type
- Do NOT include "Pricing" unless this project actually sells tiered services
- Do NOT include "Testimonials" unless social proof is relevant to this project type
- For admin panels: make features SPECIFIC to the industry (hospital→patients, school→students, CRM→contacts/deals)
- Navigation with active states
- Mobile responsive design
- Loading states, empty states, error states
- Micro-animations and transitions

## Design System (UNIQUE to this project)
- Primary color: MUST match the industry (red for media/entertainment, green for eco/health, blue for finance/tech, purple for creative, orange for food/energy). NEVER use default #6366f1
- Color palette: primary + accent + neutral scale (at least 12 CSS variables)
- Background: light/dark mode with appropriate contrast
- Typography: heading + body font that matches the mood
- Card style: flat / raised / glass / bordered — pick ONE that fits
- Spacing: tight and professional (14px base, 13px secondary, 11px labels)
- Animations: subtle translateY, opacity, scale transitions

## Pages (COMPLETE — every page the project needs)
Determine what pages THIS SPECIFIC project needs. Examples:
- Movie site: Home (hero+trending+genres), Movies (catalog), Movie Details, About → NO pricing
- SaaS site: Home (hero+features+pricing+testimonials+FAQ), About, Contact → YES pricing
- Restaurant: Home (hero+highlights+chef), Menu, Reservations, About, Contact → NO blog
- CRM admin: Dashboard (deals+stats), Contacts, Companies, Deals (pipeline), Activities, Reports, Settings

For EACH page, describe in detail:
- Page name, route, and purpose
- EVERY section with specific content descriptions
- Layout: how sections are arranged (full-width hero vs centered content vs grid vs split)
- Interactive elements: buttons, forms, hover effects, scroll animations
- Mobile layout differences
- Content: specific headlines, descriptions, button labels — not "add a hero section" but "Hero with headline 'Discover Stories That Matter', subtitle about curated content, red CTA button 'Start Reading', background gradient from dark to primary"

## Shared Components
Components used across multiple pages:
- Name, props, exact visual description
- Hover/active/disabled states
- Responsive behavior

## Data Model
{"Supabase tables with columns, types, RLS policies, relationships, and 5+ sample rows per table" if is_admin else "Content structure: what data each section displays, mock data for development"}

## Content Strategy
- Exact placeholder headlines and descriptions (realistic, not "Lorem ipsum")
- Number of items in grids/lists (e.g., "6 feature cards in 3x2 grid")
- Image descriptions (what type of images to use)
- Icons (specific lucide-react icon names)

## Quality Standards
- $5000 custom-build quality, NOT a $50 template
- Tight spacing: 14px base, compact cards (16px padding), tight sidebar (240px)
- Skeleton loaders for loading states
- Empty states with muted icons and helpful messages
- Every page has a UNIQUE layout — never repeat the same grid
- Micro-animations: 0.2s ease-out, subtle translateY(2px), opacity transitions

REMEMBER: The template is a STARTING POINT for code. Your spec defines a COMPLETE, DYNAMIC product.
If two different users asking for "blog landing page" get the same spec, you have FAILED.
If the user's request is short/vague, you MUST still produce a COMPREHENSIVE spec by researching the niche.
Research the specific niche. Customize everything.
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
    """Generate a framework-agnostic project blueprint, then convert to plan.json.

    Gemini produces a blueprint describing WHAT to build (pages, sections, nav, theme).
    blueprint_to_file_plan() converts it to exact file paths based on the detected stack.
    This separation means Gemini never needs to know the file system conventions.

    Returns the plan as a JSON string. NEVER raises.
    """
    try:
        await websocket.send_json({
            "type": "progress",
            "message": "📐 Creating project blueprint...",
        })
    except Exception:
        pass

    # Read current file tree
    file_tree = []
    for root, dirs, files in os.walk(workspace_path):
        dirs[:] = [d for d in dirs if d not in {".git", "node_modules", "__pycache__", ".lucid"}]
        for f in files:
            rel = os.path.relpath(os.path.join(root, f), workspace_path)
            file_tree.append(rel)
    file_tree_str = "\n".join(file_tree)

    skeleton_name = validated.get("skeleton_name", "")
    detected_stack = (
        validated.get("project_stack", "")
        or validated.get("skeleton_stack", "")
        or validated.get("stack", "")
    )
    _stk = detected_stack.lower()

    # Stack description for Gemini (it describes the project, not filepaths)
    if "nextjs" in _stk or "next" in _stk:
        stack_description = "Next.js 14 website (App Router, file-based routing)"
    elif "vue" in _stk:
        stack_description = "Vue 3 + Vite single-page application"
    else:
        stack_description = "React + Vite single-page application"

    # Read key template files for context
    _spec_key_files_candidates = []
    if "nextjs" in _stk or "next" in _stk:
        _spec_key_files_candidates = [
            "src/app/globals.css", "src/app/layout.js", "src/app/page.js",
            "src/lib/config.js", "src/components/Navbar.jsx", "src/components/Footer.jsx",
        ]
    elif "vue" in _stk:
        _spec_key_files_candidates = [
            "src/App.vue", "src/main.ts", "src/assets/main.css",
            "src/router/index.ts", "src/components/layout/AppSidebar.vue",
        ]
    else:
        _spec_key_files_candidates = [
            "src/index.css", "src/App.jsx", "src/main.jsx",
            "src/components/Layout.jsx", "src/components/Sidebar.jsx",
        ]

    _template_ctx_parts = []
    for _sf in _spec_key_files_candidates:
        _sf_abs = os.path.join(workspace_path, _sf)
        if os.path.isfile(_sf_abs):
            try:
                with open(_sf_abs, "r", errors="replace") as _fp:
                    _sc = _fp.read()
                if len(_sc) < 5000:
                    _template_ctx_parts.append(f"### {_sf}\n```\n{_sc}\n```")
            except Exception:
                pass
    template_content = "\n\n".join(_template_ctx_parts) if _template_ctx_parts else ""

    try:
        genai.configure(api_key=gemini_key)
        model = genai.GenerativeModel(GEMINI_BLUEPRINT_MODEL)

        blueprint_prompt = f"""You are a senior product architect converting a product specification into a complete implementation blueprint.

The project MUST be a UNIQUE, DYNAMIC, production-ready application.
DO NOT just return the existing template with minor text or color changes.
You MUST instruct the AI to COMPLETELY TRANSFORM the existing components (layout, styling, logic) to fit the specific needs of this project's industry.

USER REQUEST: {task}

FRAMEWORK: {stack_description}
TEMPLATE: {skeleton_name or detected_stack or 'standard template'}

PROJECT SPECIFICATION (this is your primary source — implement EVERYTHING described here):
{spec[:6000]}

EXISTING TEMPLATE FILES (starting code to build on top of):
{template_content or file_tree_str}

## YOUR JOB
Convert the spec into a concrete blueprint. Define:
1. EVERY page the project needs (with ALL sections per page)
2. EVERY shared component
3. The complete design theme
4. Navigation structure
5. Required npm packages

You do NOT specify file paths — that is handled automatically based on the framework.

## CONTEXT-AWARE ARCHITECTURE
Think about what THIS SPECIFIC PROJECT actually needs. NOT every project is the same.
Analyze the user request and spec to determine the RIGHT pages, sections, and navigation.

EXAMPLES of how different projects need different structures:
- "Movie streaming website" → Home (hero with featured movie, trending carousel, genre grid), Movies catalog (search/filter grid), Movie details (poster, synopsis, cast, trailer, reviews), About → NO pricing page, NO testimonials
- "Restaurant website" → Home (hero with food photography, menu highlights, chef section), Menu (categorized food items with prices), Reservations (booking form), About (story, team), Contact (map, hours) → NO features grid, NO FAQ
- "SaaS landing page" → Home (hero, features, how-it-works, testimonials, pricing, FAQ, CTA), About, Contact → YES pricing, YES testimonials
- "Portfolio website" → Home (hero, selected works grid), Projects (gallery/case studies), About (bio, skills, experience), Contact → NO pricing, NO features grid
- "E-commerce store" → Home (hero banner, featured products, categories, deals), Products (search/filter/sort grid), Product detail (images, description, reviews, add-to-cart), Cart, About → NO testimonials
- "Hospital admin panel" → Dashboard (patient stats, appointment calendar, bed occupancy), Patients (table with search/filter), Appointments (calendar + table), Doctors (table), Settings → NOT generic "Products" table
- "School admin panel" → Dashboard (student enrollment stats, attendance chart, upcoming events), Students (table), Classes (table), Teachers (table), Grades (table), Settings → NOT generic "Orders" table
- "Inventory admin panel" → Dashboard (stock levels, low-stock alerts, recent transactions), Products (table with categories), Warehouses (table), Orders (table), Suppliers (table), Reports → NOT generic "Users" table

YOUR RULES:
1. Determine EXACTLY what pages this specific project needs — do NOT copy from examples above
2. Each page must have sections that are RELEVANT to the project's purpose
3. Include pages that make sense for this industry — skip pages that don't
4. Only include Pricing if the project sells something with clear pricing tiers
5. Only include Testimonials if social proof is relevant to this project type
6. For admin panels: make the data tables, forms, and dashboard cards SPECIFIC to the industry
7. Navigation items must match the actual pages you define — no dead links

Return ONLY valid JSON (no markdown, no backticks):
{{
  "projectName": "BriefName",
  "projectType": "blog|ecommerce|portfolio|saas-landing|admin-dashboard|docs|marketing|movie|restaurant|booking|other",
  "description": "One line description of what this project does",

  "theme": {{
    "--color-primary": "#hex — UNIQUE to this project, matching the industry mood. Movie=dark red/purple, Restaurant=warm orange/brown, SaaS=professional blue, Medical=clean teal. NEVER #6366f1",
    "--color-primary-light": "#hex",
    "--color-primary-dark": "#hex",
    "--color-primary-50": "#hex (very light tint for backgrounds)",
    "--color-accent": "#hex (complementary accent — adds visual interest)",
    "--color-bg": "#hex (page background — dark for cinema/entertainment, light for business/medical)",
    "--color-bg-secondary": "#hex",
    "--color-bg-tertiary": "#hex",
    "--color-surface": "#hex (card background)",
    "--color-border": "#hex",
    "--color-text": "#hex",
    "--color-text-secondary": "#hex",
    "--color-text-muted": "#hex",
    "--font-family": "'FontName', sans-serif — choose a font that matches the mood (Playfair Display for luxury, Inter for SaaS, Poppins for modern, Merriweather for editorial)",
    "--font-heading": "'HeadingFont', sans-serif",
    "--radius-sm": "0.25rem",
    "--radius-md": "0.375rem",
    "--radius-lg": "0.5rem",
    "--radius-xl": "0.75rem",
    "darkMode": true
  }},
  "googleFonts": ["FontName"],

  "navigation": {{
    "style": "top-navbar|sidebar|both — choose based on project type (sidebar for admin panels, top-navbar for websites)",
    "brand": "Project name or brand",
    "items": [
      {{"label": "Page Name", "route": "/route", "icon": "LucideIconName"}}
    ],
    "ctaButton": {{"label": "Primary Action", "route": "/action"}}
  }},

  "pages": [
    {{
      "name": "PageName",
      "route": "/route",
      "title": "SEO-optimized page title for this specific page",
      "description": "What this specific page is about",
      "sections": [
        {{
          "name": "SectionName",
          "type": "hero|features|catalog|details|gallery|stats|testimonials|pricing|faq|cta|form|table|chart|calendar|timeline|team|menu|map|newsletter|custom",
          "description": "DETAILED (80+ words): Describe the EXACT visual implementation. Include: layout structure (grid/flex/columns), specific content (real headlines, real data, real names), interactive elements (tabs/carousel/accordion), animations (hover effects, transitions), responsive behavior, and interactive states. This description must be detailed enough for an engineer to implement WITHOUT asking questions.",
          "components": ["SharedComponentName"]
        }}
      ]
    }}
  ],

  "sharedComponents": [
    {{
      "name": "ComponentName",
      "description": "Purpose, props it accepts, visual design (colors, spacing, hover states), where it's used"
    }}
  ],

  "packages": {{
    "dependencies": {{}}
  }}
}}

## CRITICAL RULES
1. Theme MUST be unique — match the project's INDUSTRY and MOOD (dark cinema palette for movies, warm earthy for restaurants, clean professional for SaaS, vibrant for social)
2. EVERY section description MUST be 80+ words with specific content, layout details, interactive elements, and REAL text that matches the project
3. Pages and sections must be UNIQUE to this project — a movie site needs different pages than a SaaS site
4. Only include Pricing if the project actually sells tiered plans/services
5. Only include Testimonials if social proof is relevant to the project type
6. For admin panels: dashboard cards, table columns, form fields must be SPECIFIC to the industry (hospital → patients, not generic "users")
7. Each section becomes its OWN component file — descriptions must be self-contained and independently implementable
8. Include REALISTIC content: real headlines, descriptions, feature names specific to this industry. NO "Lorem ipsum"
9. sharedComponents: reusable UI elements (cards, badges, buttons, modals, search bars)
10. packages: only add what's actually needed (recharts for charts, framer-motion for animations, swiper for carousels, etc.)
11. Routes MUST be simple flat paths: "/", "/about", "/movies", "/menu". NEVER use route groups like "/(marketing)/about"
12. Section names MUST be simple PascalCase: "Hero", "MovieGrid", "MenuList", "PatientTable", "BookingForm". No spaces
13. Page names MUST be simple words: "Home", "Movies", "Menu", "Patients", "Dashboard". No spaces
14. PAGES: websites need EXACTLY 3 pages (Home + 2 others). Admin panels need 4-5 pages (dashboard + 2-3 data pages)
15. SECTIONS: Home page needs EXACTLY 5 sections. Other pages need 2-3 sections each
16. MAX TOTAL: The entire project must have NO MORE THAN 15 sections total across all pages — this is a HARD LIMIT
17. sharedComponents: maximum 2-3 reusable components

Return ONLY the raw JSON object. No markdown. No backticks. No explanation.
"""
        response = await asyncio.to_thread(model.generate_content, blueprint_prompt,
            generation_config=genai.GenerationConfig(
                temperature=0.3,
                max_output_tokens=65536,
                response_mime_type="application/json",
            ))
        blueprint_text = response.text.strip()

        # Clean up markdown fences if present
        if "```" in blueprint_text:
            parts = blueprint_text.split("```")
            for part in parts:
                candidate = part.lstrip("json").strip()
                try:
                    json.loads(candidate)
                    blueprint_text = candidate
                    break
                except Exception:
                    pass

        blueprint = json.loads(blueprint_text)

    except json.JSONDecodeError:
        # ── Attempt to repair truncated JSON ──────────────────
        # Gemini may hit output token limits and truncate mid-JSON.
        # Try to close open brackets/braces to salvage the partial response.
        logger.warning("gemini_create_plan: invalid JSON — attempting repair")
        _repaired = False
        try:
            _raw = blueprint_text
            # Count open vs close brackets
            _open_braces = _raw.count("{") - _raw.count("}")
            _open_brackets = _raw.count("[") - _raw.count("]")
            # Strip trailing comma or incomplete value
            _raw = _raw.rstrip().rstrip(",").rstrip(":")
            # Remove any trailing incomplete string (unmatched quote)
            if _raw.count('"') % 2 != 0:
                # Remove everything after last complete string
                _last_quote = _raw.rfind('"')
                if _last_quote > 0:
                    _raw = _raw[:_last_quote + 1]
            # Close brackets and braces
            _raw += "]" * max(0, _open_brackets) + "}" * max(0, _open_braces)
            blueprint = json.loads(_raw)
            _repaired = True
            logger.info("gemini_create_plan: repaired truncated JSON — %d pages recovered",
                        len(blueprint.get("pages", [])))
        except Exception as _repair_err:
            logger.warning("gemini_create_plan: repair failed: %s", _repair_err)

        if not _repaired:
            logger.warning("gemini_create_plan: using minimal blueprint fallback")
            blueprint = {
                "projectName": "project",
                "projectType": "other",
                "description": task,
                "theme": {},
                "navigation": {"items": [{"label": "Home", "route": "/"}]},
                "pages": [{"name": "Home", "route": "/", "sections": [
                    {"name": "Hero", "type": "hero", "description": task},
                    {"name": "Features", "type": "features", "description": f"Key features section for: {task}"},
                    {"name": "About", "type": "custom", "description": f"About section for: {task}"},
                    {"name": "CTA", "type": "cta", "description": f"Call to action section for: {task}"},
                ]}],
                "sharedComponents": [],
            }
    except Exception as e:
        logger.warning("gemini_create_plan failed: %s", e)
        blueprint = {
            "projectName": "project",
            "projectType": "other",
            "description": task,
            "theme": {},
            "navigation": {"items": [{"label": "Home", "route": "/"}]},
            "pages": [{"name": "Home", "route": "/", "sections": [
                {"name": "Hero", "type": "hero", "description": task},
                {"name": "Features", "type": "features", "description": f"Key features section for: {task}"},
                {"name": "About", "type": "custom", "description": f"About section for: {task}"},
                {"name": "CTA", "type": "cta", "description": f"Call to action section for: {task}"},
            ]}],
            "sharedComponents": [],
        }
    # ── POST-BLUEPRINT VALIDATION ─────────────────────────────
    # Ensure every page has enough sections. Gemini may truncate
    # the JSON mid-output, leaving pages with only 1-2 sections.
    # For landing/marketing pages, we ensure at least 5 sections.
    _project_type = blueprint.get("projectType", "other")
    _is_admin = "admin" in _project_type or "dashboard" in _project_type
    _default_website_sections = [
        {"name": "Hero", "type": "hero", "description": f"Hero banner section for {blueprint.get('projectName', 'project')}. Full-width background, compelling headline, subtext, and CTA button. Visually striking, setting the tone for the entire site."},
        {"name": "Features", "type": "features", "description": f"Key features/benefits grid for {blueprint.get('projectName', 'project')}. 6 feature cards in responsive grid with icons, titles, and descriptions. Hover effects on cards."},
        {"name": "About", "type": "custom", "description": f"About/story section for {blueprint.get('projectName', 'project')}. Split layout with text on one side and visual on the other. Company mission, values, or background story."},
        {"name": "Testimonials", "type": "testimonials", "description": f"Social proof section with 3+ testimonials. Horizontal scroll or card layout with quotes, names, roles. Star ratings and avatar placeholders."},
        {"name": "CTA", "type": "cta", "description": f"Call-to-action section for {blueprint.get('projectName', 'project')}. Gradient background, bold headline, subtext, and prominent action button. Creates urgency."},
        {"name": "FAQ", "type": "faq", "description": f"Frequently asked questions with accordion/expand-collapse. 6+ relevant questions for {blueprint.get('projectName', 'project')} with detailed answers."},
    ]

    for page in blueprint.get("pages", []):
        sections = page.get("sections", [])
        section_count = len(sections)
        is_home = page.get("route") == "/" or page.get("name", "").lower() in ("home", "landing", "main")

        if is_home and not _is_admin and section_count < 4:
            # Home page needs at least 5 sections — add missing ones
            existing_types = {(s.get("type") if isinstance(s, dict) else "") for s in sections}
            existing_names = {(s.get("name", "").lower() if isinstance(s, dict) else str(s).lower()) for s in sections}
            for default_sec in _default_website_sections:
                if default_sec["type"] not in existing_types and default_sec["name"].lower() not in existing_names:
                    sections.append(default_sec)
                    if len(sections) >= 5:
                        break
            page["sections"] = sections
            logger.info("Post-validation: Home page expanded from %d to %d sections", section_count, len(sections))

    # ── HARD CAPS — prevent over-generation ($7+ builds) ──────
    # Cap pages: max 3 for websites, max 5 for admin panels
    _pages = blueprint.get("pages", [])
    _max_pages = 5 if _is_admin else 3
    if len(_pages) > _max_pages:
        logger.info("Capping pages from %d to %d", len(_pages), _max_pages)
        blueprint["pages"] = _pages[:_max_pages]

    # Cap sections per page: max 5 for home, max 3 for other pages
    for page in blueprint.get("pages", []):
        sections = page.get("sections", [])
        is_home = page.get("route") == "/" or page.get("name", "").lower() in ("home", "landing", "main")
        _max_sections = 5 if is_home else 3
        if len(sections) > _max_sections:
            logger.info("Capping '%s' sections from %d to %d", page.get("name"), len(sections), _max_sections)
            page["sections"] = sections[:_max_sections]

    # Cap shared components: max 3
    _shared = blueprint.get("sharedComponents", [])
    if len(_shared) > 3:
        blueprint["sharedComponents"] = _shared[:3]

    # Log final counts
    _total_sections = sum(len(p.get("sections", [])) for p in blueprint.get("pages", []))
    _total_shared = len(blueprint.get("sharedComponents", []))
    logger.info("Final blueprint: %d pages, %d total sections, %d shared components",
                len(blueprint.get("pages", [])), _total_sections, _total_shared)

    # Convert blueprint to file-path plan (framework-aware)
    plan_data = blueprint_to_file_plan(blueprint, detected_stack, workspace_path)
    plan_text = json.dumps(plan_data, indent=2, ensure_ascii=False)

    # Save both blueprint and plan
    try:
        lucid_dir = os.path.join(workspace_path, ".lucid")
        os.makedirs(lucid_dir, exist_ok=True)
        with open(os.path.join(lucid_dir, "blueprint.json"), "w") as f:
            f.write(json.dumps(blueprint, indent=2, ensure_ascii=False))
        with open(os.path.join(lucid_dir, "plan.json"), "w") as f:
            f.write(plan_text)
        logger.info("Saved blueprint.json + plan.json (%d files)", len(plan_data.get("files", [])))
    except Exception as e:
        logger.warning("Could not save plan: %s", e)

    try:
        page_count = len(blueprint.get("pages", []))
        comp_count = len(blueprint.get("sharedComponents", []))
        await websocket.send_json({
            "type": "progress",
            "message": f"📐 Blueprint ready: {page_count} pages, {comp_count} shared components",
        })
    except Exception:
        pass

    return plan_text


def blueprint_to_file_plan(blueprint: dict, stack: str, workspace_path: str) -> dict:
    """Convert a framework-agnostic project blueprint into a concrete file plan.

    This is the bridge between Gemini's blueprint (WHAT to build) and Claude's
    implementation (HOW to build it given the template and framework).
    """
    _stk = (stack or "").lower()
    is_nextjs = "nextjs" in _stk or "next" in _stk
    is_vue = "vue" in _stk

    pages = blueprint.get("pages", [])
    shared_components = blueprint.get("sharedComponents", [])
    theme = blueprint.get("theme", {})
    navigation = blueprint.get("navigation", {})
    packages = blueprint.get("packages", {})
    project_name = blueprint.get("projectName", "project")
    project_desc = blueprint.get("description", "")

    files = []

    # ── Detect component base path from the actual template structure ──
    # Next.js templates may use src/app/components/ or src/components/
    # React/Vue templates use src/components/
    if is_nextjs:
        if os.path.isdir(os.path.join(workspace_path, "src", "app", "components")):
            comp_base = "src/app/components"  # matches nextjs template
        else:
            comp_base = "src/components"
    elif is_vue:
        comp_base = "src/components"
    else:
        comp_base = "src/components"

    # ── Detect route group for Next.js (e.g. (marketing), (dashboard)) ──
    # If the template uses route groups, pages should be generated INSIDE them
    # so they inherit the correct layout (with header/footer).
    nextjs_route_group = ""
    if is_nextjs:
        app_dir = os.path.join(workspace_path, "src", "app")
        if os.path.isdir(app_dir):
            for entry in os.listdir(app_dir):
                # Route groups are directories wrapped in parentheses
                if entry.startswith("(") and entry.endswith(")") and entry != "(auth)":
                    group_path = os.path.join(app_dir, entry)
                    if os.path.isdir(group_path):
                        nextjs_route_group = entry  # e.g. "(marketing)"
                        logger.info("Detected Next.js route group: %s", nextjs_route_group)
                        break

    # ── 1. CSS/Globals — always first ──────────────────────────
    if is_nextjs:
        css_path = "src/app/globals.css"
    elif is_vue:
        css_path = "src/assets/main.css"
    else:
        css_path = "src/index.css"

    css_exists = os.path.isfile(os.path.join(workspace_path, css_path))
    files.append({
        "path": css_path,
        "action": "modify" if css_exists else "create",
        "priority": 1,
        "description": (
            f"Apply the project theme: update :root CSS variables with the new color palette. "
            f"Project: {project_name}. Theme values: {json.dumps(theme)}. "
            f"Add Google Font import for {blueprint.get('googleFonts', ['Inter'])}. "
            f"Set the overall feel: {'dark mode' if theme.get('darkMode') else 'light mode'}."
        ),
        "sections": ["CSS variable overrides", "Google Font import", "Base body/html styles"],
    })

    # ── 1b. Site config (direct-write — no Claude needed) ──────────
    # Update the brand name, description, and logo text for this project.
    if is_nextjs:
        _site_config_path = "src/config/site.js"
        _site_config_content = (
            f'export const siteConfig = {{\n'
            f'  name: "{project_name}",\n'
            f'  description: "{project_desc[:150]}",\n'
            f'  url: "https://example.com",\n'
            f'  logoText: "{project_name[:2].upper()}",\n'
            f'  ogImage: "/og-image.png",\n'
            f'}};\n'
        )
        files.append({
            "path": _site_config_path,
            "action": "create",
            "priority": 1,
            "_direct_content": _site_config_content,
            "description": f"Site config with brand: {project_name}",
        })

    # ── 1c. Navigation header — update for this project ──────────
    # MarketingHeader has hardcoded navLinks. Update them to match project pages.
    if is_nextjs:
        _header_path = "src/components/layout/MarketingHeader.jsx"
        if os.path.isfile(os.path.join(workspace_path, _header_path)):
            _nav_items = navigation.get("items", [])
            _nav_links_str = ", ".join(
                f'{{"href": "{item.get("route", "/")}", "label": "{item.get("label", "")}"}}' 
                for item in _nav_items if item.get("route") != "/"
            )
            files.append({
                "path": _header_path,
                "action": "modify",
                "priority": 2,
                "description": (
                    f"Update the navigation header for '{project_name}'. "
                    f"Change the navLinks array to: [{_nav_links_str}]. "
                    f"The CTA button should say '{navigation.get('cta', 'Get Started')}' and link to the most relevant page. "
                    f"Keep the ENTIRE component structure, scroll behavior, mobile menu, and Tailwind classes intact. "
                    f"ONLY change: (1) navLinks array values, (2) CTA button text/link. "
                    f"DO NOT change imports, component name, or layout structure."
                ),
            })

    # ── 1d. Footer — update for this project ──────────
    if is_nextjs:
        _footer_path = "src/components/layout/MarketingFooter.jsx"
        if os.path.isfile(os.path.join(workspace_path, _footer_path)):
            files.append({
                "path": _footer_path,
                "action": "modify",
                "priority": 2,
                "description": (
                    f"Update the footer for '{project_name}'. "
                    f"Change the brand name, tagline, and footer link columns to match this project. "
                    f"Footer links should include: {json.dumps([item.get('label') for item in navigation.get('items', [])])}. "
                    f"Add relevant footer columns (e.g., 'Quick Links', 'Services', 'Contact Info'). "
                    f"Keep the ENTIRE component structure and Tailwind classes intact. "
                    f"ONLY change: text content, link labels/hrefs, brand name, tagline."
                ),
            })

    # ── 2. Root layout (metadata + fonts ONLY, no Navbar/Footer) ──────
    # The root layout.js should ONLY handle: html/body tags, metadata, fonts, Providers.
    # Navigation and footer are handled by route group layouts (e.g. (marketing)/layout.js).
    if is_nextjs:
        layout_path = "src/app/layout.js"
        layout_exists = os.path.isfile(os.path.join(workspace_path, layout_path))
        files.append({
            "path": layout_path,
            "action": "modify" if layout_exists else "create",
            "priority": 2,
            "description": (
                f"Update ONLY the root layout metadata for {project_name}. "
                f"DO NOT add Navbar, Header, Footer, or any navigation components here. "
                f"The template already has a route group layout that handles navigation. "
                f"ONLY update: page metadata (title='{project_name}', description='{project_desc}'), "
                f"Google Fonts import for {blueprint.get('googleFonts', ['Inter'])}, "
                f"and ensure Providers wrapper is preserved. "
                f"Keep the existing structure — this is a MINIMAL modification."
            ),
            "sections": ["Page metadata update", "Google Font import"],
        })

        # Also update the route group layout (marketing) to customize nav items
        if nextjs_route_group:
            group_layout_path = f"src/app/{nextjs_route_group}/layout.js"
            group_layout_exists = os.path.isfile(os.path.join(workspace_path, group_layout_path))
            if group_layout_exists:
                nav_items_str = json.dumps(navigation.get("items", []))
                files.append({
                    "path": group_layout_path,
                    "action": "modify",
                    "priority": 2,
                    "description": (
                        f"Update the marketing layout for {project_name}. "
                        f"This layout ALREADY has MarketingHeader and MarketingFooter — keep them. "
                        f"Customize the navigation items: {nav_items_str}. "
                        f"Brand/logo: {navigation.get('brand', project_name)}. "
                        f"DO NOT rename or remove the existing header/footer components. "
                        f"Only update the props or content passed to them if needed."
                    ),
                    "sections": ["Navigation items update", "Brand customization"],
                })
    elif is_vue:
        # Detect Vue Admin template
        _vue_admin = os.path.isfile(os.path.join(workspace_path, "src", "components", "layout", "MainLayout.vue"))
        if _vue_admin:
            # Vue Admin: MainLayout.vue + AppSidebar + AppHeader already exist
            routes_path = "src/router/routes.js"
            if os.path.isfile(os.path.join(workspace_path, routes_path)):
                files.append({
                    "path": routes_path,
                    "action": "modify",
                    "priority": 2,
                    "description": (
                        f"Update Vue router routes for {project_name}. "
                        f"ADD route entries as children of MainLayout for all pages: {[p['name'] for p in pages]}. "
                        f"Use lazy imports: () => import('@/pages/<name>/<Name>Page.vue'). "
                        f"Keep existing Dashboard, Login, and NotFound routes."
                    ),
                    "sections": ["Route entries for new pages"],
                })
            nav_config_path = "src/config/navigation.js"
            if os.path.isfile(os.path.join(workspace_path, nav_config_path)):
                files.append({
                    "path": nav_config_path,
                    "action": "modify",
                    "priority": 2,
                    "description": (
                        f"Update sidebar navigation items for {project_name}. "
                        f"Items: {json.dumps(navigation.get('items', []))}. "
                        f"Use lucide icons. Keep the existing structure."
                    ),
                    "sections": ["Navigation items update"],
                })
        else:
            app_path = "src/App.vue"
            app_exists = os.path.isfile(os.path.join(workspace_path, app_path))
            files.append({
                "path": app_path,
                "action": "modify" if app_exists else "create",
                "priority": 2,
                "description": (
                    f"Update root App.vue for {project_name}. "
                    f"Navigation: {json.dumps(navigation)}. "
                    f"RouterView for page content."
                ),
                "sections": ["Navigation bar", "RouterView", "Footer"],
            })
    else:
        # Detect React Admin template
        _react_admin = os.path.isfile(os.path.join(workspace_path, "src", "components", "layout", "MainLayout.jsx"))
        if _react_admin:
            # React Admin: MainLayout.jsx + Sidebar + Header already exist
            routes_path = "src/router/routes.jsx"
            if os.path.isfile(os.path.join(workspace_path, routes_path)):
                files.append({
                    "path": routes_path,
                    "action": "modify",
                    "priority": 2,
                    "description": (
                        f"Update React router routes for {project_name}. "
                        f"ADD entries to privateRoutes for all pages: {[p['name'] for p in pages]}. "
                        f"Import each page component from src/pages/ or src/features/. "
                        f"Keep existing Dashboard and Login routes."
                    ),
                    "sections": ["Route entries for new pages"],
                })
            nav_config_path = "src/config/navigation.js"
            if os.path.isfile(os.path.join(workspace_path, nav_config_path)):
                files.append({
                    "path": nav_config_path,
                    "action": "modify",
                    "priority": 2,
                    "description": (
                        f"Update sidebar navigation items for {project_name}. "
                        f"Items: {json.dumps(navigation.get('items', []))}. "
                        f"Use lucide-react icons. Keep the existing structure."
                    ),
                    "sections": ["Navigation items update"],
                })
        else:
            app_path = "src/App.jsx"
            app_exists = os.path.isfile(os.path.join(workspace_path, app_path))
            nav_items = navigation.get("items", [])
            page_names = [p["name"] for p in pages]

            files.append({
                "path": app_path,
                "action": "modify" if app_exists else "create",
                "priority": 2,
                "description": (
                    f"Update App.jsx for {project_name}. "
                    f"Add BrowserRouter with Routes for all pages: {[p['name'] for p in pages]}. "
                    f"Include Navbar with items: {json.dumps(nav_items)}. "
                    f"Include Footer."
                ),
                "sections": ["Navbar", "React Router Routes", "Footer"],
            })

    # ── 3. Shared components ─────────────────────────────────
    for comp in shared_components:
        comp_name = comp.get("name", "Component")
        comp_desc = comp.get("description", "Shared component")
        if is_vue:
            comp_path = f"{comp_base}/{comp_name}.vue"
        else:
            comp_path = f"{comp_base}/{comp_name}.jsx"
        comp_exists = os.path.isfile(os.path.join(workspace_path, comp_path))
        files.append({
            "path": comp_path,
            "action": "modify" if comp_exists else "create",
            "priority": 3,
            "description": comp_desc,
            "sections": [comp_desc],
        })

    # ── 4. Pages — each section becomes its own component file ──
    import re as _re_sanitize
    for page in pages:
        page_name = page.get("name", "Page")
        page_route = page.get("route", "/")
        page_sections = page.get("sections", [])
        page_title = page.get("title", page_name)
        page_desc_text = page.get("description", page_name)
        # Sanitize page name: remove spaces, special chars, keep only alphanum
        safe_page = _re_sanitize.sub(r'[^a-zA-Z0-9]', '', page_name)
        if not safe_page:
            safe_page = "Page"
        # Sanitize route: strip route groups, special chars
        page_route = _re_sanitize.sub(r'\([^)]*\)/?', '', page_route).strip('/')
        if page_route:
            page_route = f"/{page_route}"
        else:
            page_route = "/"

        # ── 4a. Create a section component for each section ────
        section_component_names = []
        for sec in page_sections:
            if isinstance(sec, dict):
                sec_name = _re_sanitize.sub(r'[^a-zA-Z0-9]', '', sec.get("name", "Section"))
                sec_desc = sec.get("description", "")
                sec_type = sec.get("type", "")
                sec_components = sec.get("components", [])
            else:
                sec_name = _re_sanitize.sub(r'[^a-zA-Z0-9]', '', str(sec))
                sec_desc = str(sec)
                sec_type = ""
                sec_components = []
            if not sec_name:
                sec_name = "Section"

            # Component name: e.g. HomeHeroSection, HomeFeaturesSection
            comp_name = f"{safe_page}{sec_name}Section"
            section_component_names.append(comp_name)

            if is_vue:
                sec_file = f"{comp_base}/sections/{comp_name}.vue"
            else:
                sec_file = f"{comp_base}/sections/{comp_name}.jsx"

            sec_exists = os.path.isfile(os.path.join(workspace_path, sec_file))
            files.append({
                "path": sec_file,
                "action": "create",  # Always CREATE — stubs are just build-safety fallbacks
                "priority": 4,
                "description": (
                    f"Section component for the '{page_name}' page. "
                    f"Section: '{sec_name}' (type: {sec_type}). "
                    f"{sec_desc}. "
                    f"{'Uses components: ' + ', '.join(sec_components) + '. ' if sec_components else ''}"
                    f"This is a SELF-CONTAINED section component — it should render "
                    f"a complete section of the page with proper padding, responsive layout, "
                    f"and beautiful design using CSS variables. "
                    f"A stub file exists — OVERWRITE it completely with the real implementation. "
                    f"Export as: export function {comp_name}() {{ ... }} and then export default {comp_name}. "
                    f"Use lucide-react for icons. Make it fully responsive. "
                    f"Write SUBSTANTIAL, production-quality content with real-looking placeholder text."
                ),
                "sections": [sec_desc or sec_name],
            })

        # ── 4b. Create the page file (thin — just imports sections) ──
        if is_nextjs:
            # Use the detected route group so pages inherit the correct layout
            route_prefix = f"src/app/{nextjs_route_group}" if nextjs_route_group else "src/app"
            if page_route == "/":
                page_file = f"{route_prefix}/page.js"
            else:
                clean_route = page_route.strip("/").replace(" ", "-").lower()
                if not clean_route:
                    clean_route = safe_page.lower()
                page_file = f"{route_prefix}/{clean_route}/page.js"
        elif is_vue:
            page_file = f"src/views/{safe_page}View.vue"
        else:
            if page_route == "/":
                page_file = "src/pages/Home.jsx"
            else:
                page_file = f"src/pages/{safe_page}.jsx"

        page_file_exists = os.path.isfile(os.path.join(workspace_path, page_file))

        # Build import list for the page — use relative paths matching template structure
        import_lines = ", ".join(section_component_names)
        # Programmatically calculate relative path from the page folder to the sections folder
        page_dir = os.path.dirname(page_file)
        sections_dir = f"{comp_base}/sections"
        
        rel_path = os.path.relpath(sections_dir, page_dir)
        # Ensure relative imports start with explicitly ./ or ../
        if not rel_path.startswith('.'):
            rel_prefix = f"./{rel_path}"
        else:
            rel_prefix = rel_path

        import_paths = [
            f"import {{ {name} }} from '{rel_prefix}/{name}'"
            for name in section_component_names
        ]
        import_block = "\n".join(import_paths)

        # ── Generate the page file content DIRECTLY (no Claude needed) ──
        # Page files are deterministic: imports + JSX wrapper. Writing them
        # directly saves tokens AND eliminates path errors from Claude.
        jsx_elements = "\n      ".join(
            f"<{name} />" for name in section_component_names
        )

        if is_nextjs:
            # Next.js pages are SERVER components — no 'use client'
            # Section components have 'use client' individually
            page_content = (
                f"{import_block}\n\n"
                f"export default function {safe_page}Page() {{\n"
                f"  return (\n"
                f"    <main>\n"
                f"      {jsx_elements}\n"
                f"    </main>\n"
                f"  )\n"
                f"}}\n"
            )
        elif is_vue:
            vue_imports = "\n".join(
                f"import {name} from '{rel_prefix}/{name}.vue'"
                for name in section_component_names
            )
            vue_components = "\n    ".join(
                f"<{name} />" for name in section_component_names
            )
            page_content = (
                f"<template>\n"
                f"  <main>\n"
                f"    {vue_components}\n"
                f"  </main>\n"
                f"</template>\n\n"
                f"<script setup>\n"
                f"{vue_imports}\n"
                f"</script>\n"
            )
        else:
            # React Vite
            page_content = (
                f"{import_block}\n\n"
                f"export default function {safe_page}Page() {{\n"
                f"  return (\n"
                f"    <main>\n"
                f"      {jsx_elements}\n"
                f"    </main>\n"
                f"  )\n"
                f"}}\n"
            )

        files.append({
            "path": page_file,
            "action": "create",
            "priority": 5,
            "_direct_content": page_content,  # Flag: write directly, skip Claude
            "description": f"Thin composition page for '{page_name}'",
            "sections": [f"Import and render: {', '.join(section_component_names)}"],
        })


    # ── 5. Assemble final plan.json ─────────────────────────
    return {
        "projectName": project_name,
        "stack": stack,
        "theme": theme,
        "googleFonts": blueprint.get("googleFonts", ["Inter"]),
        "navigation": navigation,
        "packageUpdates": packages,
        "_comp_base": comp_base,  # Pass to framework rules
        "files": files,
    }






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
    """Execute new project generation in intelligent batches.

    Instead of sending everything to Claude in one massive call,
    this splits the plan into logical batches and executes each
    as a separate Claude call. Benefits:
    - No timeout on large projects
    - User sees progress file-by-file
    - If a batch fails, others still succeed
    - Retry failed batches with simpler prompts

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
    # Priority: caller arg > plan.json > [LUCID_PROJECT] header in task
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
            # Detect PM from workspace (lock file or user preference)
            pm = detect_package_manager(workspace_path, "npm")
            try:
                await websocket.send_json({
                    "type": "progress",
                    "message": f"📦 Installing {len(pkg_names)} new package(s) via {pm}...",
                })

                pkg_env = _pm_env(pm)
                pkg_cmd = _pm_install_cmd(pm, pkg_names)

                pkg_result = await asyncio.to_thread(
                    subprocess.run,
                    pkg_cmd,
                    cwd=workspace_path,
                    capture_output=True,
                    text=True,
                    timeout=90,
                    env=pkg_env,
                )
                if pkg_result.returncode == 0:
                    logger.info("Installed %d new packages via %s: %s", len(pkg_names), pm, pkg_names)
                else:
                    logger.warning("%s install packages failed: %s", pm, (pkg_result.stderr or "")[:200])
            except Exception as pkg_err:
                logger.warning("Failed to install packageUpdates (non-fatal): %s", pkg_err)

    # ── Assign execution order (CSS/config first, then components, then pages) ─
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
            assigned = 5  # default = page-level
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

    # ── Build framework rules block (injected into every Claude call) ─
    _stk = stack.lower() if stack else ""
    # Detect comp_base from plan_data or default
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
            "- ALWAYS use Tailwind utility classes in className — NEVER use inline style={{}}\n"
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
            "- NEVER use inline style={{}} for colors, spacing, or layout — always Tailwind classes\n"
            "- NEVER use hardcoded hex colors like #fff or #000 — use Tailwind's semantic colors\n"
        )
    elif "vue" in _stk:
        # Detect Vue Admin template by checking for MainLayout.vue
        _is_vue_admin = os.path.isfile(os.path.join(workspace_path, "src", "components", "layout", "MainLayout.vue"))
        if _is_vue_admin:
            FRAMEWORK_RULES = (
                "## FRAMEWORK: Vue 3 + Vite Admin Panel — READ THIS FIRST\n"
                "- Entry: src/main.js → src/App.vue → src/router/index.js\n"
                "- Layout: src/components/layout/MainLayout.vue (wraps AppSidebar + AppHeader + router-view) — DO NOT RECREATE\n"
                "- Sidebar: src/components/layout/AppSidebar.vue — modify NAV_ITEMS only, keep structure\n"
                "- Header: src/components/layout/AppHeader.vue — modify branding only, keep structure\n"
                "- Routes: src/router/routes.js (children of MainLayout) — ADD new page routes here\n"
                "- Pages: src/pages/ or src/features/<domain>/pages/ (one .vue file per page)\n"
                "- UI components: src/components/ui/ (shadcn-vue — import from here, DO NOT recreate)\n"
                "- DO NOT create Navbar.vue, Footer.vue, Sidebar.vue — MainLayout.vue handles all navigation\n"
                "- State: Pinia (src/stores/); Composables: src/composables/\n"
                "- Config: src/config/navigation.js (sidebar nav items definition)\n"
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
        # Detect React Admin template by checking for MainLayout.jsx
        _is_react_admin = os.path.isfile(os.path.join(workspace_path, "src", "components", "layout", "MainLayout.jsx"))
        if _is_react_admin:
            FRAMEWORK_RULES = (
                "## FRAMEWORK: Vite + React Admin Panel — READ THIS FIRST\n"
                "- Entry: src/main.jsx → src/App.jsx → src/router/index.jsx\n"
                "- Layout: src/components/layout/MainLayout.jsx (wraps Sidebar + Header + Outlet) — DO NOT RECREATE\n"
                "- Sidebar: src/components/layout/Sidebar.jsx — modify NAV_ITEMS/navigation config only, keep structure\n"
                "- Header: src/components/layout/Header.jsx — modify branding only, keep structure\n"
                "- Routes: src/router/routes.jsx (privateRoutes array) — ADD new page routes here\n"
                "- Pages: src/pages/ or src/features/<domain>/pages/ (one .jsx file per page)\n"
                "- UI components: src/components/ui/ (shadcn — import from here, DO NOT recreate)\n"
                "- DO NOT create Navbar.jsx, Footer.jsx — MainLayout.jsx handles all navigation\n"
                "- State: Zustand (src/store/); API: Axios (src/api/client.js)\n"
                "- Config: src/config/navigation.js (sidebar nav items definition)\n"
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

    # ── Build environment for Claude SDK ──────────────────────
    import pwd
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

    # ── Build existing file tree once ─────────────────────────
    def _build_tree(wp: str) -> str:
        existing: list[str] = []
        for root, dirs, _ff in os.walk(wp):
            dirs[:] = [d for d in dirs if d not in {".git", "node_modules", "__pycache__", ".lucid", ".claude"}]
            for f in _ff:
                existing.append(os.path.relpath(os.path.join(root, f), wp))
        return "\n".join(sorted(existing)) if existing else "(empty)"

    file_tree = _build_tree(workspace_path)

    # Brief spec excerpt — used in every per-file call
    spec_excerpt = spec_content[:2500] if spec_content else ""
    theme_summary = json.dumps(plan_data.get("theme", {}), indent=2)

    # ── Load TEMPLATE_MANIFEST.md (Layer 1 — what components exist) ──
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

    # ── Load knowledge context (Layer 2 — architecture patterns) ──
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

    # ══════════════════════════════════════════════════════════
    # PRE-CREATE: Ensure all section directories and stub files exist
    # so that page imports NEVER reference non-existent files.
    # Even if Claude times out, the project will still build.
    # ══════════════════════════════════════════════════════════
    for file_spec in files:
        fp = file_spec.get("path", "")
        direct = file_spec.get("_direct_content")
        if not fp:
            continue
        abs_fp = os.path.join(workspace_path, fp)
        # Always create parent directories
        os.makedirs(os.path.dirname(abs_fp), exist_ok=True)

        # Pre-create stub files for section AND layout components so imports never break
        # This covers: /sections/, /layout/, and any other component directory
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
                    f"    <section style={{{{ padding: '4rem 2rem', textAlign: 'center' }}}}>\n"
                    f"      <h2>{comp_name}</h2>\n"
                    f"      <p>Loading content...</p>\n"
                    f"    </section>\n"
                    f"  )\n"
                    f"}}\n\n"
                    f"export default {comp_name}\n"
                )
            with open(abs_fp, "w") as _sf:
                _sf.write(stub)
            logger.info("Pre-created stub: %s", fp)

    # Update file tree after pre-creation
    file_tree = _build_tree(workspace_path)

    # ══════════════════════════════════════════════════════════
    # FILE-BY-FILE EXECUTION — one Claude call per file
    # ══════════════════════════════════════════════════════════
    for file_idx, file_spec in enumerate(files, 1):
        file_path = file_spec.get("path", "")
        file_action = file_spec.get("action", "create")   # "create" | "modify"
        file_desc = file_spec.get("description", "Implement this file")
        file_sections = file_spec.get("sections", [])
        file_imports = file_spec.get("imports", [])
        file_components = file_spec.get("components", [])

        if not file_path:
            continue

        # ── Notify frontend ───────────────────────────────────
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

        # ── FAST PATH: Direct-write files (page compositions) ──
        # Page files are deterministic (just imports + JSX wrapper).
        # Writing them directly saves Claude API tokens and guarantees
        # correct import paths — no AI hallucination possible.
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
                # Update file tree so next Claude calls can see this file
                file_tree = _build_tree(workspace_path)
            except Exception as e:
                logger.warning("Failed to direct-write %s: %s", file_path, e)
            continue  # Skip Claude call for this file

        # ── Read existing file content if this is a modify ────
        existing_content = ""
        file_abs = os.path.join(workspace_path, file_path)
        if (file_action == "modify" or os.path.isfile(file_abs)) and "/sections/" not in file_path:
            # For section files: SKIP reading stubs — they're just placeholders
            try:
                with open(file_abs, "r", errors="replace") as _ef:
                    existing_content = _ef.read()
            except Exception:
                pass

        # ── Build file-specific prompt ─────────────────────────
        # Detect layout/navigation files — these should have DATA updated, not structure overhauled
        _is_layout_file = any(kw in file_path.lower() for kw in [
            "/layout/", "layout.", "navbar.", "footer.", "header.", "sidebar.",
            "app.jsx", "app.vue", "/router/", "navigation.",
        ])

        if existing_content:
            if _is_layout_file:
                # Extract existing import lines to explicitly protect them
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
                action_instruction = (
                    f"MODIFY the file `{file_path}` using the Write or Edit tool.\n"
                    f"UPDATE only the data (nav items, brand, routes, imports) — preserve the component structure.\n"
                )
            else:
                file_context = (
                    f"## EXISTING FILE CONTENT ({file_path})\n"
                    f"This file already exists in the template. You are MODIFYING it:\n"
                    f"```\n{existing_content[:6000]}\n```\n\n"
                    f"CRITICAL INSTRUCTION: The template is just a starting point. DO NOT just change the text.\n"
                    f"You MUST TRANSFORM the component's layout, styling, and logic to exactly match the specific requirements below.\n"
                    f"If the spec requires a completely different layout (e.g. from a grid to a slider), change the code completely.\n"
                    f"Preserve existing imports only if they are still needed; otherwise remove them.\n"
                )
                action_instruction = (
                    f"MODIFY the file `{file_path}` using the Write or Edit tool.\n"
                    f"The current content is shown above. OVERHAUL it to implement the spec below.\n"
                )
        else:
            file_context = (
                f"## FILE TO CREATE: {file_path}\n"
                f"This is a new file that does not exist yet.\n"
            )
            action_instruction = (
                f"CREATE the file `{file_path}` using the Write tool.\n"
                f"Base your implementation on the existing template components shown in the file tree.\n"
            )

        sections_block = ""
        if file_sections:
            sections_block = "Sections to implement:\n" + "\n".join(f"  - {s}" for s in file_sections) + "\n"

        imports_block = ""
        if file_imports:
            imports_block = f"Suggested imports: {', '.join(file_imports)}\n"

        components_block = ""
        if file_components:
            components_block = f"Components to use (already exist in template): {', '.join(file_components)}\n"

        # ── Conditionally include heavy context blocks ──────────
        # Only section components need the full spec — layouts/CSS don't
        _is_section_file = "/sections/" in file_path
        _spec_block = ""
        if _is_section_file:
            _spec_block = f"## PROJECT SPECIFICATION (follow this exactly)\n{spec_excerpt}\n\n"

        # Only include manifest for layout/component files, not sections
        _manifest_for_file = ""
        if _is_layout_file and _manifest_block:
            _manifest_for_file = _manifest_block

        # Only include knowledge context for section files
        _knowledge_for_file = ""
        if _is_section_file and _knowledge_context:
            _knowledge_for_file = _knowledge_context

        # Slim file tree — only show relevant directories, not everything
        _slim_tree = file_tree[:1500] if not _is_layout_file else file_tree[:3000]

        prompt = f"""## YOUR TASK
{action_instruction}
{file_context}
## WHAT TO IMPLEMENT
{file_desc}

{sections_block}{imports_block}{components_block}
## DESIGN THEME (use Tailwind classes like bg-primary, text-foreground — NEVER inline styles)
{theme_summary}

{_spec_block}## WORKSPACE FILE TREE
{_slim_tree}
{_manifest_for_file}{_knowledge_for_file}
{action_instruction}
Write the COMPLETE file using the Write tool. STOP after this ONE file.
"""

        system_prompt = (
            f"You are a SENIOR FRONTEND ENGINEER at a top design agency, building a premium {stack or 'web'} project.\n"
            f"This is a PAID PRODUCT — users pay money for this. The UI must be stunning, not just functional.\n\n"
            f"{FRAMEWORK_RULES}\n\n"
            "## YOUR ENGINEERING STANDARDS:\n"
            "1. ARCHITECTURE: Clean, modular code. Pages are thin. Section components hold all logic.\n"
            "2. DESIGN: Every component must look like it belongs on Dribbble or Awwwards. Polished, modern, premium.\n"
            "3. ANIMATIONS: Smooth transitions — hover:scale-105, hover:-translate-y-1, transition-all duration-300.\n"
            "4. RESPONSIVE: Mobile-first. Use sm:, md:, lg:, xl: Tailwind breakpoints.\n"
            "5. CONTENT: Write real, compelling content — not lorem ipsum. Headlines that sell, descriptions that inform.\n"
            "6. STYLING: Use Tailwind utility classes in className. Colors: bg-primary, text-foreground, bg-muted, bg-card, etc.\n"
            "7. SPACING: Generous whitespace. py-16 py-20 py-24 for sections. p-6 p-8 for cards. gap-6 gap-8 for grids.\n"
            "8. ICONS: Import from lucide-react. Use meaningful icons, not random ones.\n"
            "9. INTERACTIVITY: Add useState-driven UI — tabs, filters, toggles, animated counters. Sections must feel ALIVE.\n"
            "10. UNIQUENESS: Each section must have a DISTINCTIVE visual pattern. Vary layouts (asymmetric, alternating, overlapping, staggered).\n\n"
            "## FORBIDDEN:\n"
            "- NEVER use inline style={{}} — always className with Tailwind utilities\n"
            "- NEVER use var(--color-primary) or var(--color-bg) — use Tailwind's bg-primary, text-foreground, etc.\n"
            "- NEVER use hardcoded hex colors (#fff, #000, #333) — use Tailwind semantic colors\n"
            "- NEVER create new route groups — the template already has the correct structure\n"
            "- NEVER create Navbar.jsx, Footer.jsx, or Header.jsx — the template already has layout components\n"
            "- NEVER modify or create layout.js files — the template's layout structure is final\n"
            "- NEVER add 'use client' to page.js files — only section components need it\n"
            "- NEVER write minimal/placeholder content — write REAL, substantial content\n"
            "- NEVER make all sections look the same — each section type needs its OWN layout pattern\n"
            "- NEVER use identical card grids for every section — vary between grids, alternating rows, carousels, stacked layouts\n\n"
            "Write the COMPLETE file using the Write tool. Stop immediately after.\n"
        )

        # Claude just needs to Write the file. No Read needed (content is in prompt).
        # 3 turns = think + write + done. Prevents hitting max_turns before writing.
        _turns = 3

        # Layout/modify files need Read+Write to preserve structure.
        # Section files (create) only need Write for speed.
        if _is_layout_file or file_action == "modify":
            _allowed = ["Read", "Write"]
            _disallowed = ["Bash", "Edit", "MultiEdit", "GitCommit", "GitPush", "GitPull"]
        else:
            _allowed = ["Write"]
            _disallowed = ["Read", "Bash", "Edit", "MultiEdit", "GitCommit", "GitPush", "GitPull"]

        options = ClaudeCodeOptions(
            cwd=str(workspace_path),
            env=env,
            model=model_id,
            max_turns=_turns,
            permission_mode="bypassPermissions",
            allowed_tools=_allowed,
            disallowed_tools=_disallowed,
            append_system_prompt=system_prompt,
        )

        # ── Run Claude with generous timeout per file ───────────
        # 180s = ~30s per turn × 6 max_turns. Complex sections need this
        file_success = False
        for attempt in range(2):
            try:
                async with asyncio.timeout(180):   # 180s per single file for premium quality
                    async for message in query(prompt=prompt, options=options):
                        try:
                            await websocket.send_json({
                                "type": "claude_message",
                                "content": str(message),
                            })
                        except Exception:
                            pass
                file_success = True
                break  # done on first attempt

            except asyncio.TimeoutError:
                logger.warning("File '%s' timed out (attempt %d)", file_path, attempt + 1)
                if attempt == 0:
                    try:
                        await websocket.send_json({
                            "type": "warning",
                            "message": f"⚠️ {file_path} timed out, retrying with shorter prompt...",
                        })
                    except Exception:
                        pass
                    # Retry with shorter but TEMPLATE-AWARE prompt
                    _layout_guard_retry = ""
                    if _is_layout_file and existing_content:
                        _layout_guard_retry = (
                            "\n⚠️ LAYOUT FILE — Keep all existing imports EXACTLY as they are.\n"
                            "Do NOT rename MarketingHeader→Navbar or MarketingFooter→Footer.\n"
                            "Only update nav items, brand text, and colors.\n"
                        )
                    _existing_block = ""
                    if existing_content:
                        _existing_block = f"Existing content:\n```\n{existing_content[:2000]}\n```\n\n"
                    prompt = (
                        f"{FRAMEWORK_RULES}\n\n"
                        f"{'MODIFY' if existing_content else 'CREATE'} the file `{file_path}`.\n\n"
                        f"{_existing_block}"
                        f"{_layout_guard_retry}"
                        f"Task: {file_desc}\n\n"
                        f"Theme: {theme_summary[:500]}\n\n"
                        f"IMPORTANT: Only import components that ACTUALLY EXIST in the workspace.\n"
                        f"Do NOT create imports for Navbar, Footer, Header unless those exact files exist.\n"
                        f"Use CSS variables. Use Write tool. Stop after this one file.\n"
                    )
            except Exception as e:
                logger.warning("File '%s' failed (attempt %d): %s", file_path, attempt + 1, e)
                if attempt == 0:
                    try:
                        await websocket.send_json({
                            "type": "warning",
                            "message": f"⚠️ {file_path} error, retrying...",
                        })
                    except Exception:
                        pass

        # Check if file was actually written
        file_exists = os.path.isfile(file_abs) and os.path.getsize(file_abs) > 0
        if file_exists:
            total_files_created += 1
            logger.info("✓ File %d/%d: %s", file_idx, total_files_expected, file_path)
        else:
            logger.warning("✗ File %d/%d not created: %s", file_idx, total_files_expected, file_path)

        completed_files += 1

        # Update file tree for next iteration (so Claude sees newly created files)
        file_tree = _build_tree(workspace_path)

        # Send completion event per file
        try:
            await websocket.send_json({
                "type": "batch_complete",
                "batch": file_path,
                "files_created": [file_path] if file_exists else [],
                "files_expected": 1,
                "completed_batches": completed_files,
                "total_batches": total_files_expected,
                "percentage": int((completed_files / total_files_expected) * 100),
                "message": f"{'✅' if file_exists else '⚠️'} {file_path} ({'done' if file_exists else 'skipped'})",
            })
        except Exception:
            pass

    # ── Final summary ─────────────────────────────────────────
    try:
        await websocket.send_json({
            "type": "generation_complete",
            "total_files": total_files_created,
            "total_expected": total_files_expected,
            "batches_completed": completed_files,
            "total_batches": total_files_expected,
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





    # ── Build shared env + options factory ─────────────────
    import pwd
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

    anti_loop = (
        "\n\nTOOL USE CONTRACT:\n"
        "- Read a file max ONCE. Never re-read.\n"
        "- For NEW PROJECT batches: Read key template files ONCE to understand structure, then WRITE your files.\n"
        "- Never re-read a file you already read.\n"
        "- After reading, WRITE immediately.\n"
        "- Do NOT verify by re-reading written files.\n"
        "- Create ONLY the files listed below. No extras.\n"
        "- STOP when all listed files are created.\n"
    )

    model_id = str(classification.get("model_id", "claude-sonnet-4-6"))

    # ── Build existing file tree for context ───────────────
    existing_files = []
    for root, dirs, _files in os.walk(workspace_path):
        dirs[:] = [d for d in dirs if d not in {".git", "node_modules", "__pycache__", ".lucid", ".claude"}]
        for f in _files:
            rel = os.path.relpath(os.path.join(root, f), workspace_path)
            existing_files.append(rel)
    existing_tree = "\n".join(sorted(existing_files)) if existing_files else "(empty)"

    # ── TOKEN OPTIMIZATION: Read template/skeleton files ONCE and inline ──
    # We detect the stack and inline the most relevant structural files.
    # This prevents Claude from blindly recreating files that already exist.
    _stk_lower = stack.lower() if stack else ""
    is_nextjs_template = "nextjs" in _stk_lower or "next" in _stk_lower
    is_vue_template = "vue" in _stk_lower

    if is_nextjs_template:
        SKELETON_FILES_TO_INLINE = [
            # Next.js App Router structure
            "src/app/globals.css", "src/app/globals.css",           # CSS design system
            "src/app/layout.js", "src/app/layout.tsx",              # Root layout
            "src/app/page.js", "src/app/page.tsx",                  # Home page
            "src/components/Navbar.jsx", "src/components/Navbar.tsx",
            "src/components/Footer.jsx", "src/components/Footer.tsx",
            "src/components/layout/Sidebar.jsx",
            "src/lib/config.js", "src/lib/config.ts",               # Site config
        ]
    elif is_vue_template:
        SKELETON_FILES_TO_INLINE = [
            "src/App.vue",
            "src/main.ts", "src/main.js",
            "src/assets/main.css",
            "src/components/layout/AppSidebar.vue",
            "src/router/index.ts", "src/router/index.js",
        ]
    else:
        # React Vite (default)
        SKELETON_FILES_TO_INLINE = [
            "src/index.css", "src/styles/index.css",
            "src/App.jsx", "src/App.tsx",
            "src/main.jsx", "src/main.tsx",
            "src/components/Layout.jsx", "src/components/Layout.tsx",
            "src/components/DataTable.jsx", "src/components/DataTable.tsx",
            "src/components/Sidebar.jsx", "src/components/Sidebar.tsx",
        ]

    # Deduplicate while preserving order
    seen_skel: set = set()
    SKELETON_FILES_TO_INLINE = [f for f in SKELETON_FILES_TO_INLINE if not (f in seen_skel or seen_skel.add(f))]  # type: ignore

    inlined_skeleton = {}
    for skel_rel in SKELETON_FILES_TO_INLINE:
        skel_abs = os.path.join(workspace_path, skel_rel)
        if os.path.isfile(skel_abs):
            try:
                with open(skel_abs, "r", errors="replace") as sf:
                    content = sf.read()
                # Only inline if reasonably sized (< 8KB per file)
                if len(content) < 8000:
                    inlined_skeleton[skel_rel] = content
            except Exception:
                pass

    # Build skeleton context block (only for first batch)
    skeleton_context = ""
    if inlined_skeleton:
        parts = []
        for path, content in inlined_skeleton.items():
            parts.append(f"### {path}\n```\n{content}\n```")
        skeleton_context = "\n\n".join(parts)
        logger.info("Inlined %d skeleton files into prompt (saves Read tool calls)", len(inlined_skeleton))

    # Build abbreviated spec for batches 2+ (saves ~2000 tokens per batch)
    spec_abbreviated = spec_content[:800] if len(spec_content) > 800 else spec_content

    # ── Execute each batch ────────────────────────────────
    for batch_idx, batch in enumerate(batches):
        batch_name = batch["name"]
        batch_files = batch["files"]
        batch_file_paths = [f.get("path", "") for f in batch_files]
        percentage = int((batch_idx / total_batches) * 100)
        is_first_batch = (batch_idx == 0)

        # Send progress BEFORE starting
        try:
            await websocket.send_json({
                "type": "generation_progress",
                "batch": batch_name,
                "message": f"✍️ Building {batch_name}...",
                "files": batch_file_paths,
                "completed_batches": completed_batches,
                "total_batches": total_batches,
                "percentage": percentage,
            })
        except Exception:
            pass

        # Build file details for Claude
        file_details = []
        for f in batch_files:
            detail = f"### File: {f.get('path', 'unknown')}\n"
            detail += f"Action: {f.get('action', 'create')}\n"
            detail += f"Description: {f.get('description', 'No description')}\n"
            if f.get("imports"):
                detail += f"Imports: {', '.join(f['imports'])}\n"
            if f.get("components"):
                detail += f"Uses components: {', '.join(f['components'])}\n"
            if f.get("sections"):
                detail += f"Sections: {', '.join(f['sections'])}\n"
            file_details.append(detail)

        # ── TOKEN-OPTIMIZED PROMPT ──
        # Build framework-specific guidance block
        framework_block = ""
        _stk = stack.lower() if stack else ""
        if "nextjs" in _stk or "next" in _stk:
            framework_block = (
                "## FRAMEWORK: Next.js 14 (App Router)\n"
                "- File structure: src/app/ for pages/layouts, src/components/ for UI\n"
                "- Pages: src/app/page.tsx, src/app/[route]/page.tsx, src/app/layout.tsx\n"
                "- DO NOT create src/App.jsx or src/App.tsx — these do not exist in Next.js\n"
                "- Use 'use client' at top for interactive components\n"
                "- Routing: import Link from 'next/link', import { useRouter, usePathname } from 'next/navigation'\n"
                "- Images: import Image from 'next/image'\n"
            )
        elif _stk in ("react", "react-admin", "vite"):
            framework_block = (
                "## FRAMEWORK: Vite + React\n"
                "- Entry: src/main.jsx, Root component: src/App.jsx\n"
                "- Routing: react-router-dom (BrowserRouter, Routes, Route, Link, useNavigate)\n"
                "- No 'use client' directive needed\n"
            )
        elif "vue" in _stk:
            framework_block = (
                "## FRAMEWORK: Vue 3 + Vite\n"
                "- Entry: src/main.ts, Root component: src/App.vue\n"
                "- Routing: Vue Router (useRouter, useRoute, RouterLink)\n"
                "- Use Composition API (<script setup lang=\"ts\">)\n"
            )

        # First batch: full spec + inlined skeleton contents
        # Later batches: abbreviated spec, file tree only (no skeleton re-send)
        if is_first_batch and skeleton_context:
            batch_prompt = f"""You are executing batch "{batch_name}" of a new project.

{framework_block}
## Theme (apply to ALL components via CSS variables):
{theme_block}

## Specification:
{spec_content[:3000]}

## EXISTING SKELETON FILES (already in workspace — DO NOT recreate, import from them):
{skeleton_context}

## FILE TREE (all files currently in workspace):
{existing_tree}

## YOUR TASK — CREATE THESE FILES ONLY:
{chr(10).join(file_details)}

## Rules:
1. Use exact file paths specified above
2. Follow design system from the specification
3. Use CSS variables (var(--color-primary), var(--color-bg), etc.) — NEVER hardcoded hex
4. Import from correct relative paths based on the skeleton files shown above
5. Include loading and empty states where appropriate
6. Use lucide-react for icons
7. Components must be responsive
8. NEVER recreate skeleton files — their full contents are shown above, import from them
9. WRITE each file immediately — do NOT Read skeleton files (they are already in this prompt)

USE WRITE TOOL for each file. Create all {len(batch_files)} file(s) listed above.
Do NOT create files that are not in this batch.
STOP when done.
"""
        else:
            # Subsequent batches — abbreviated context to save tokens
            batch_prompt = f"""You are executing batch "{batch_name}" of a new project.

{framework_block}
## Theme (apply to ALL components via CSS variables):
{theme_block}

## Specification (summary):
{spec_abbreviated}

## FILE TREE (all files currently in workspace):
{existing_tree}

## YOUR TASK — CREATE THESE FILES ONLY:
{chr(10).join(file_details)}

## Rules:
1. Use exact file paths specified above
2. Use CSS variables (var(--color-primary), etc.) — NEVER hardcoded hex
3. Import from existing components (Layout, DataTable, Sidebar, etc.)
4. Use lucide-react for icons. Components must be responsive.
5. WRITE each file immediately — do NOT Read existing files.

USE WRITE TOOL for each file. Create all {len(batch_files)} file(s).
STOP when done.
"""

        system_prompt = (
            "You are building a new project file-by-file.\n"
            f"FRAMEWORK: {stack.upper()} — this is a {stack} project.\n"
            f"{'IMPORTANT: This is Next.js App Router — use src/app/ directory, NOT src/App.jsx or src/App.tsx. Files live in src/app/page.tsx, src/app/layout.tsx etc.\n' if 'nextjs' in stack.lower() or 'next' in stack.lower() else ''}"
            f"{'IMPORTANT: This is Vite React — entry is src/main.jsx, root component is src/App.jsx.\n' if stack.lower() in ('react', 'react-admin', 'vite') else ''}"
            f"{'IMPORTANT: This is Vue 3 — entry is src/main.ts, root component is src/App.vue.\n' if 'vue' in stack.lower() else ''}"
            "WRITE code immediately using the Write tool — do NOT Read first.\n"
            "Skeleton file contents are provided in the prompt. Use them as reference.\n"
            "Create professional, production-ready code.\n"
            "Follow the design system exactly.\n"
            "Use CSS variables for all colors.\n"
        ) + anti_loop

        # Determine turns — fewer needed since no Read calls
        batch_turns = max(4, len(batch_files) * 3)  # ~3 turns per file (write + verify)
        batch_turns = min(batch_turns, 20)  # cap at 20 per batch
        batch_timeout = max(120, batch_turns * 40)  # ~40s per turn, minimum 120s

        options = ClaudeCodeOptions(
            cwd=str(workspace_path),
            env=env,
            model=model_id,
            max_turns=batch_turns,
            permission_mode="bypassPermissions",
            allowed_tools=["Read", "Write", "Edit", "MultiEdit", "Bash", "Glob", "Grep", "LS"],
            disallowed_tools=[
                "GitCommit", "GitPush", "GitPull", "GitClone",
                "Bash(git commit*)", "Bash(git push*)", "Bash(rm -rf*)",
            ],
            append_system_prompt=system_prompt,
        )

        # ── Execute this batch with retry ─────────────────
        batch_success = False
        for attempt in range(2):  # max 2 attempts
            try:
                async with asyncio.timeout(batch_timeout):
                    async for message in query(
                        prompt=batch_prompt if attempt == 0 else _simplified_prompt(batch_files, spec_content[:1500], stack=stack),
                        options=options,
                    ):
                        msg_str = str(message)
                        try:
                            await websocket.send_json({
                                "type": "claude_message",
                                "content": msg_str,
                            })
                        except Exception:
                            pass

                batch_success = True
                break  # success — no retry needed

            except asyncio.TimeoutError:
                logger.warning("Batch '%s' timed out (attempt %d)", batch_name, attempt + 1)
                if attempt == 0:
                    try:
                        await websocket.send_json({
                            "type": "warning",
                            "message": f"⚠️ {batch_name} timed out, retrying with simpler prompt...",
                        })
                    except Exception:
                        pass
            except Exception as e:
                logger.warning("Batch '%s' failed (attempt %d): %s", batch_name, attempt + 1, e)
                if attempt == 0:
                    try:
                        await websocket.send_json({
                            "type": "warning",
                            "message": f"⚠️ {batch_name} error, retrying...",
                        })
                    except Exception:
                        pass

        # ── Check which files were actually created ───────
        created_files = []
        for fp in batch_file_paths:
            full = os.path.join(workspace_path, fp)
            if os.path.exists(full):
                created_files.append(fp)

        total_files_created += len(created_files)
        completed_batches += 1

        # Send batch completion
        try:
            await websocket.send_json({
                "type": "batch_complete",
                "batch": batch_name,
                "files_created": created_files,
                "files_expected": len(batch_files),
                "completed_batches": completed_batches,
                "total_batches": total_batches,
                "percentage": int((completed_batches / total_batches) * 100),
                "message": f"✅ {batch_name} complete ({len(created_files)}/{len(batch_files)} files)",
            })
        except Exception:
            pass

        if not created_files and batch_success:
            logger.warning("Batch '%s' ran but created 0 files", batch_name)

    # ── Final summary ─────────────────────────────────────
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
        "execute_project_in_batches done: %d/%d files, %d/%d batches",
        total_files_created, total_files_expected,
        completed_batches, total_batches,
    )

    if total_files_created == 0:
        return False
    # Pass if at least 60% of files created
    return total_files_created >= (total_files_expected * 0.6)


def _simplified_prompt(batch_files: list, spec_snippet: str, stack: str = "") -> str:
    """Build a simpler retry prompt when the first attempt fails."""
    descs = [f"{f.get('path', '?')}: {f.get('description', 'implement this file')}" for f in batch_files]

    # Add framework-specific guidance so Claude doesn't get confused
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

    return f"""Create the following files. Use CSS variables for colors. Keep implementation focused.

{framework_hint}
Files to create:
{chr(10).join(descs)}

Context:
{spec_snippet}

USE WRITE TOOL for each file. Stop when done.
"""


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
        validated = await validate_inputs(task, user, websocket, chat_session_id=chat_session_id)
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
                detected_stack = stack_match.group(1).strip() if stack_match else ""
                is_admin = detect_admin_from_task(task_original or task)

                logger.info("Skeleton detection: stack=%s, is_admin=%s, task=%s",
                            detected_stack, is_admin, (task_original or task)[:60])

                skeleton_path = get_skeleton_for_stack(detected_stack, is_admin, task=task_original or task)
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


            # Check token type early and warn if invalid (uses module-level constant)
            platform_token = PLATFORM_GITHUB_TOKEN
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

            # ── Step 2c: Install dependencies (dynamic PM) ─────
            pkg_json = os.path.join(workspace_path, "package.json")
            if os.path.exists(pkg_json):
                user_pm_pref = validated.get("package_manager", "npm")
                pm = detect_package_manager(workspace_path, user_pm_pref)
                validated["package_manager"] = pm  # store for later phases
                logger.info("Package manager detected: %s (user pref: %s)", pm, user_pm_pref)

                try:
                    await websocket.send_json({
                        "type": "progress",
                        "message": f"📦 Installing dependencies ({pm})...",
                    })

                    install_env = _pm_env(pm)
                    install_cmd = _pm_install_cmd(pm)

                    install_result = await asyncio.to_thread(
                        subprocess.run,
                        install_cmd,
                        cwd=workspace_path,
                        capture_output=True,
                        text=True,
                        timeout=120,
                        env=install_env,
                    )
                    if install_result.returncode == 0:
                        logger.info("%s install succeeded in scratch workspace", pm)
                        await websocket.send_json({
                            "type": "progress",
                            "message": f"✅ Dependencies installed ({pm})",
                        })
                    else:
                        err_snippet = (install_result.stderr or install_result.stdout or "")[:200]
                        logger.warning("%s install failed (non-fatal): %s", pm, err_snippet)
                        await websocket.send_json({
                            "type": "warning",
                            "message": f"⚠️ {pm} install failed (Claude will fix): {err_snippet[:100]}",
                        })
                except subprocess.TimeoutExpired:
                    logger.warning("%s install timed out (120s) — continuing", pm)
                except Exception as npm_err:
                    logger.warning("%s install error (non-fatal): %s", pm, npm_err)

            # ── Step 2d: Create .claude/settings.json ───────────
            try:
                claude_dir = os.path.join(workspace_path, ".claude")
                os.makedirs(claude_dir, exist_ok=True)
                claude_settings = {
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
                    json.dump(claude_settings, f, indent=2)

                # Fix OS permissions so Claude can write to all files
                os.chmod(workspace_path, 0o777)
                subprocess.run(
                    ["chmod", "-R", "777", workspace_path],
                    capture_output=True, timeout=10,
                )
                logger.info("Created .claude/settings.json and chmod 777 for scratch workspace")
            except Exception as cs_err:
                logger.warning("Failed to create .claude/settings.json (non-fatal): %s", cs_err)

            await websocket.send_json({
                "type": "progress",
                "message": "✅ Local workspace ready",
            })
        elif validated.get("new_project_mode"):
            # ── NEW PROJECT MODE: clone the real GitHub template ──────────────
            # The wizard pre-created a repo from the GitHub template.
            # Clone IT (not a local skeleton) so Claude sees the exact
            # file structure (Next.js App Router, Vue 3 Vite, React + Vite, etc.)
            # and writes code that matches the project's actual architecture.
            template_clone_url = validated.get("template_clone_url", "")
            git_token = validated.get("git_token", "")

            from uuid import uuid4
            workspace_path = f"/tmp/lucid_new_{task_id}_{str(uuid4())[:6]}"
            os.makedirs(workspace_path, exist_ok=True)
            os.chmod(workspace_path, 0o777)

            if template_clone_url:
                await websocket.send_json({
                    "type": "progress",
                    "message": "📦 Cloning GitHub template into workspace...",
                })
                logger.info("new_project_mode: cloning template from %s...", template_clone_url[:60])

                # Clone with depth=1 for speed; shallow clone is fine
                clone_result = await asyncio.to_thread(
                    subprocess.run,
                    ["git", "clone", "--depth", "1", "--branch", "main",
                     "--single-branch", template_clone_url, "."],
                    cwd=workspace_path,
                    capture_output=True,
                    text=True,
                    timeout=180,
                )

                if clone_result.returncode != 0:
                    err = (clone_result.stderr or clone_result.stdout or "unknown error")
                    if git_token:
                        err = err.replace(git_token, "***")
                    logger.error("new_project_mode: git clone failed: %s", err[:300])
                    await websocket.send_json({
                        "type": "warning",
                        "message": f"⚠️ Template clone failed, falling back to local skeleton: {err[:150]}",
                    })
                    # FALLBACK: copy local skeleton (keeps pipeline alive)
                    workspace_path = None
                else:
                    logger.info("new_project_mode: template clone succeeded in %s", workspace_path)
                    await websocket.send_json({
                        "type": "progress",
                        "message": "✅ GitHub template cloned",
                    })

                    # Configure git user
                    await asyncio.to_thread(
                        subprocess.run,
                        ["git", "config", "user.name", "Lucid AI"],
                        cwd=workspace_path, capture_output=True,
                    )
                    await asyncio.to_thread(
                        subprocess.run,
                        ["git", "config", "user.email", "ai@lucid.dev"],
                        cwd=workspace_path, capture_output=True,
                    )

                    # Install dependencies from template's package.json
                    pkg_json = os.path.join(workspace_path, "package.json")
                    if os.path.exists(pkg_json):
                        user_pm_pref = validated.get("package_manager", "npm")
                        pm = detect_package_manager(workspace_path, user_pm_pref)
                        validated["package_manager"] = pm
                        logger.info("new_project_mode: installing deps with %s", pm)
                        await websocket.send_json({
                            "type": "progress",
                            "message": f"📦 Installing template dependencies ({pm})...",
                        })
                        try:
                            install_result = await asyncio.to_thread(
                                subprocess.run,
                                _pm_install_cmd(pm),
                                cwd=workspace_path,
                                capture_output=True,
                                text=True,
                                timeout=180,
                                env=_pm_env(pm),
                            )
                            if install_result.returncode == 0:
                                await websocket.send_json({
                                    "type": "progress",
                                    "message": f"✅ Dependencies installed ({pm})",
                                })
                            else:
                                snippet = (install_result.stderr or install_result.stdout or "")[:200]
                                logger.warning("new_project_mode: %s install failed (non-fatal): %s", pm, snippet)
                                await websocket.send_json({
                                    "type": "warning",
                                    "message": f"⚠️ {pm} install failed (Claude will fix): {snippet[:100]}",
                                })
                        except Exception as _ie:
                            logger.warning("new_project_mode: install error (non-fatal): %s", _ie)

            # Fallback: no clone_url → use local skeleton (same as scratch_mode)
            if not workspace_path or not os.path.exists(workspace_path) or not os.listdir(workspace_path):
                logger.warning("new_project_mode: falling back to local skeleton for workspace")
                workspace_path = workspace_path or f"/tmp/lucid_new_{task_id}_fb"
                os.makedirs(workspace_path, exist_ok=True)
                os.chmod(workspace_path, 0o777)

                # Init git
                subprocess.run(["git", "init", "-b", "main"], cwd=workspace_path,
                                capture_output=True, text=True, timeout=10)
                subprocess.run(["git", "config", "user.name", "Lucid AI"],
                                cwd=workspace_path, capture_output=True)
                subprocess.run(["git", "config", "user.email", "ai@lucid.dev"],
                                cwd=workspace_path, capture_output=True)

                # Copy local skeleton
                try:
                    from app.services.skeleton_manager import get_skeleton_for_stack, copy_skeleton, detect_admin_from_task
                    import re as _re_sk
                    stack_match = _re_sk.search(r"stack=(\S+)", str(task_original or ""))
                    detected_stack = stack_match.group(1).strip() if stack_match else ""
                    is_admin = detect_admin_from_task(task_original or task)
                    skeleton_path = get_skeleton_for_stack(detected_stack, is_admin, task=task_original or task)
                    if skeleton_path:
                        copied = copy_skeleton(skeleton_path, workspace_path)
                        skel_name = os.path.basename(skeleton_path)
                        await websocket.send_json({
                            "type": "progress",
                            "message": f"📦 Local skeleton loaded: {skel_name} ({len(copied)} files)",
                        })
                except Exception as _skel_err:
                    logger.warning("new_project_mode: fallback skeleton error: %s", _skel_err)

                await websocket.send_json({
                    "type": "progress",
                    "message": "✅ Local workspace ready (fallback)",
                })

            # Always create .claude/settings.json for permission bypass
            try:
                claude_dir = os.path.join(workspace_path, ".claude")
                os.makedirs(claude_dir, exist_ok=True)
                claude_settings = {
                    "permissions": {
                        "defaultMode": "bypassPermissions",
                        "allow": [
                            "Read", "Write", "Edit", "MultiEdit",
                            "Bash(npm *)", "Bash(npx *)", "Bash(pnpm *)",
                            "Bash(node *)", "Bash(cat *)", "Bash(ls *)",
                            "Bash(mkdir *)", "Bash(touch *)", "Bash(cp *)", "Bash(mv *)",
                        ],
                        "deny": [
                            "Bash(git commit*)", "Bash(git push*)",
                            "Bash(rm -rf*)", "Bash(sudo*)",
                        ],
                    }
                }
                with open(os.path.join(claude_dir, "settings.json"), "w") as _csf:
                    json.dump(claude_settings, _csf, indent=2)
                subprocess.run(["chmod", "-R", "777", workspace_path], capture_output=True, timeout=10)
                logger.info("new_project_mode: .claude/settings.json created, chmod 777 applied")
            except Exception as _cs_err:
                logger.warning("new_project_mode: .claude/settings.json creation failed: %s", _cs_err)

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

        # ── Phase 3b: Generate CLAUDE.md (Knowledge Layer) ─────
        # CLAUDE.md is read automatically by Claude Code SDK before
        # any prompt. Contains: project rules, import safety guides,
        # architecture patterns, and quality standards.
        if validated.get("scratch_mode") or validated.get("new_project_mode"):
            try:
                from knowledge.loader import generate_claude_md, classify_project_type
                _gen_stack = (
                    validated.get("project_stack", "")
                    or validated.get("skeleton_stack", "")
                    or ""
                )
                _claude_md_path = generate_claude_md(task, _gen_stack, workspace_path)
                if _claude_md_path:
                    _proj_type = classify_project_type(task)
                    logger.info("Generated CLAUDE.md — project_type=%s", _proj_type)
                    try:
                        await websocket.send_json({
                            "type": "progress",
                            "message": f"📚 Knowledge loaded: {_proj_type.replace('_', ' ')} patterns",
                        })
                    except Exception:
                        pass
            except Exception as _claude_md_err:
                logger.warning("Failed to generate CLAUDE.md (non-fatal): %s", _claude_md_err)

        # ── Phase 4: Explore / Research ──────────────────────
        # scratch_mode: fresh local workspace + skeleton
        # new_project_mode: template repo cloned from GitHub — same full research+plan pipeline
        # else: existing user repo — explore and edit
        if validated.get("scratch_mode") or validated.get("new_project_mode"):
            # NEW PIPELINE: Gemini Research + Plan for new projects
            template_label = ""
            if validated.get("new_project_mode"):
                import re as _re
                _tl = _re.search(r"template=([^|\n]+)", str(task_original or ""))
                template_label = _tl.group(1).strip() if _tl else ""

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

            # Mention the template so Claude builds ON TOP of it, not from scratch
            template_note = (
                f"\n\nIMPORTANT: The workspace is pre-seeded with the \"{template_label}\" template. "
                "Build on top of its existing structure. Reuse existing components, "
                "routing, and layout — do not recreate boilerplate that already exists."
            ) if template_label else ""

            # Inject the full Gemini spec + plan into Claude's prompt.
            # CRITICAL: Claude MUST first understand the template before writing.
            # We build a comprehensive context block from the inlined template files
            # so Claude doesn't need to Read them (saves tool calls + avoids confusion).

            # Read template key files to show Claude what already exists
            _template_key_files = {}
            _template_candidates = []
            _project_stk_lower = (validated.get("project_stack", "") or "").lower()
            if "nextjs" in _project_stk_lower or "next" in _project_stk_lower:
                _template_candidates = [
                    "src/app/globals.css", "src/app/layout.js", "src/app/layout.tsx",
                    "src/app/(marketing)/layout.js", "src/app/(marketing)/page.js",
                    "src/config/site.js", "src/config/navigation.js",
                    "src/components/layout/MarketingHeader.jsx", "src/components/layout/MarketingFooter.jsx",
                    "src/components/Providers.jsx",
                ]
            elif "vue" in _project_stk_lower:
                _template_candidates = [
                    "src/App.vue", "src/main.ts",
                    "src/assets/main.css", "src/router/index.ts",
                    "src/components/layout/AppSidebar.vue",
                ]
            else:
                _template_candidates = [
                    "src/index.css", "src/App.jsx", "src/App.tsx", "src/main.jsx",
                    "src/components/Layout.jsx", "src/components/Sidebar.jsx",
                ]
            for _tc in _template_candidates:
                _tc_abs = os.path.join(workspace_path, _tc)
                if os.path.isfile(_tc_abs):
                    try:
                        with open(_tc_abs, "r", errors="replace") as _f:
                            _content = _f.read()
                        if len(_content) < 10000:  # cap large files
                            _template_key_files[_tc] = _content
                    except Exception:
                        pass

            _template_ctx = ""
            if _template_key_files:
                _parts = []
                for _path, _code in _template_key_files.items():
                    _parts.append(f"### {_path}\n```\n{_code}\n```")
                _template_ctx = (
                    "\n\n## EXISTING TEMPLATE FILES\n"
                    "These are the actual files already in your workspace. "
                    "READ them before writing anything. Extend them — do NOT recreate them.\n\n"
                    + "\n\n".join(_parts)
                )

            # Build the framework hint for Claude
            _fw_hint = ""
            if "nextjs" in _project_stk_lower or "next" in _project_stk_lower:
                _fw_hint = (
                    "\n\n## FRAMEWORK: Next.js 14 (App Router)\n"
                    "- Pages: src/app/page.js, src/app/[route]/page.js, src/app/layout.js\n"
                    "- DO NOT create src/App.jsx — this does NOT exist in Next.js\n"
                    "- Use 'use client' for any component using hooks or event handlers\n"
                    "- Import: Link from 'next/link', useRouter/usePathname from 'next/navigation'\n"
                    "- The template already has Navbar, Footer, and layout.js — REUSE them\n"
                )
            elif "vue" in _project_stk_lower:
                _fw_hint = (
                    "\n\n## FRAMEWORK: Vue 3 + Vite\n"
                    "- Entry: src/main.ts, Root: src/App.vue\n"
                    "- Use Composition API (<script setup lang=\"ts\">)\n"
                    "- Routing: Vue Router (useRouter, useRoute, RouterLink)\n"
                )
            else:
                _fw_hint = (
                    "\n\n## FRAMEWORK: Vite + React\n"
                    "- Entry: src/main.jsx, Root with routes: src/App.jsx\n"
                    "- Routing: react-router-dom (BrowserRouter, Routes, Route, Link)\n"
                )

            # Build Claude's guided prompt with MANDATORY template analysis step
            plan = f"""## New Project Implementation{template_note}
{_fw_hint}

## MANDATORY WORKFLOW — Follow these steps in ORDER:

### STEP 0 — Understand the Template (READ FIRST, do not skip):
The workspace already contains a real GitHub template with existing components.
Before writing ANY file, you MUST understand what already exists.
- Look at the file tree and the template file contents shown below
- Identify which files need to be MODIFIED vs which need to be CREATED fresh
- NEVER recreate a file that already exists unless the plan explicitly says "modify"

### STEP 1 — Apply the Theme:
1. Read `.lucid/plan.json` for the theme block
2. Update the CSS file (globals.css / index.css / style.css) :root block with ALL theme colors from plan.json
3. Add Google Font import from plan.json["googleFonts"] if specified

### STEP 2 — Implement Each File from the Plan:
1. Read `.lucid/plan.json` for the complete file list
2. Read `.lucid/spec.md` for detailed design requirements
3. For EACH file in the plan:
   - If action=\"modify\": Read the existing file FIRST, then edit it carefully
   - If action=\"create\": Write it fresh, importing from existing template components
4. NEVER use hardcoded hex colors — always use var(--color-primary), var(--color-bg), etc.
5. Use lucide-react for all icons
6. Every component must be responsive
7. Follow the spec EXACTLY for colors, layout, and component behavior

### STEP 3 — Final Wiring:
1. Ensure all new pages are linked from the navigation
2. Verify all imports resolve correctly
{_template_ctx}

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

        if validated.get("scratch_mode") or validated.get("new_project_mode"):
            # Batched execution for new projects (scratch or template-cloned)
            _batch_stack = (
                validated.get("skeleton_stack", "")
                or validated.get("project_stack", "")
                or ""
            )
            success = await execute_project_in_batches(
                workspace_path,
                task,
                validated["anthropic_api_key"],
                classification,
                plan,
                websocket,
                stack=_batch_stack,
            )
            # If batched execution returned False due to empty plan,
            # fall back to single-call execution
            if not success and not os.path.exists(os.path.join(workspace_path, ".lucid", "plan.json")):
                logger.info("Batched execution had no plan — falling back to single-call")
                success = await execute_with_claude(
                    task,
                    workspace_path,
                    validated["anthropic_api_key"],
                    classification,
                    plan,
                    websocket,
                )
        else:
            # Existing repo: single-call execution (edit mode)
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

        # ── Pre-build: fix broken component imports ────────
        # Claude sometimes rewrites layout.js to import 'Navbar'/'Footer'
        # instead of the template's actual component names (e.g. MarketingHeader/MarketingFooter).
        # This auto-fixer detects and corrects mismatched imports BEFORE the build runs.
        try:
            await _fix_broken_layout_imports(workspace_path, websocket)
        except Exception as _fix_err:
            logger.warning("Pre-build import fixer failed (non-fatal): %s", _fix_err)

        _build_result = {"success": True, "needs_fix": False, "attempts": 0, "errors": "", "fixed_files": [], "error_count": 0}
        try:
            from app.services.build_validator import BuildValidator
            _bv = BuildValidator(
                api_key=validated["anthropic_api_key"],
                classification=classification,
                websocket=websocket,
                max_retries=3,
            )
            _build_result = await _bv.validate_and_fix(workspace_path)
            logger.info(
                "BuildValidator result: success=%s, needs_fix=%s, attempts=%d, errors=%d",
                _build_result.get("success"), _build_result.get("needs_fix"),
                _build_result.get("attempts", 0), _build_result.get("error_count", 0),
            )
        except Exception as _bv_err:
            logger.warning("BuildValidator failed (non-fatal, falling back): %s", _bv_err)
            # Fallback to old verify_build
            await verify_build(workspace_path, validated["anthropic_api_key"], classification, websocket)
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
            platform_token = PLATFORM_GITHUB_TOKEN  # module-level constant
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

        elif validated.get("new_project_mode"):
            # ── NEW PROJECT MODE: create a brand-new GitHub repo and push ────────
            # The template was cloned in Phase 2. Claude generated code on top of it.
            # Now we:
            #   1. Re-init git (detach from the template remote)
            #   2. Create a brand-new private GitHub repo (via API)
            #   3. Push all generated code to the new repo as `main`
            #   4. Save the new repo URL to DB + emit repo_created event
            await _send_phase(7, "Publishing project", "Creating new repository…", "active")

            git_token      = validated.get("git_token", "")
            project_name   = validated.get("project_name", "lucid-project")
            project_desc   = validated.get("project_description", "")

            logger.info("new_project_mode Phase 7: creating repo '%s' → pushing workspace", project_name)

            try:
                # ── STEP 1: Make sure the project_name is unique ─────────────
                # Append a short timestamp to avoid name collisions when the same
                # project is generated multiple times.
                from datetime import datetime as _dt
                _ts = _dt.now().strftime("%m%d%H%M")
                new_repo_name = f"{project_name}-{_ts}"[:60]

                await websocket.send_json({
                    "type": "progress",
                    "message": f"📦 Creating new repo: {_PLATFORM_ORG}/{new_repo_name}…",
                })

                # ── STEP 2: Create the GitHub repo via API ─────────────────────
                if not git_token:
                    raise RuntimeError("PLATFORM_GITHUB_TOKEN not available — cannot create repo")

                new_repo = await _create_github_repo(
                    repo_name=new_repo_name,
                    token=git_token,
                    description=f"Generated by Lucid AI — {project_desc[:120]}" if project_desc else "Generated by Lucid AI",
                )
                new_repo_html_url  = new_repo.get("html_url", "")
                new_repo_clone_url = (
                    new_repo.get("clone_url", "")
                    .replace("https://github.com/", f"https://{git_token}@github.com/")
                )
                logger.info("new_project_mode: repo created: %s", new_repo_html_url)

                await websocket.send_json({
                    "type": "progress",
                    "message": f"✅ Repo created: {new_repo_html_url}",
                })

                # ── STEP 3: Re-init git (detach from template remote) ──────────
                # Remove the .git directory from the template clone and start fresh
                # so the new repo has a clean, unrelated history.
                import shutil as _shutil
                _git_dir = os.path.join(workspace_path, ".git")
                if os.path.exists(_git_dir):
                    _shutil.rmtree(_git_dir)

                subprocess.run(["git", "init", "-b", "main"], cwd=workspace_path,
                               capture_output=True, text=True, timeout=10)
                subprocess.run(["git", "config", "user.name", "Lucid AI"],
                               cwd=workspace_path, capture_output=True)
                subprocess.run(["git", "config", "user.email", "ai@lucid.dev"],
                               cwd=workspace_path, capture_output=True)
                subprocess.run(["git", "remote", "add", "origin", new_repo_clone_url],
                               cwd=workspace_path, capture_output=True, timeout=10)

                # ── STEP 4: Stage everything ───────────────────────────────────
                subprocess.run(["git", "add", "-A"], cwd=workspace_path,
                               capture_output=True, timeout=30)

                # ── STEP 5: Commit ─────────────────────────────────────────────
                commit_msg = f"feat: initial project generated by Lucid AI\n\nProject: {project_desc[:200] or new_repo_name}"
                commit_result = subprocess.run(
                    ["git", "commit", "-m", commit_msg],
                    cwd=workspace_path, capture_output=True, text=True, timeout=30,
                )
                if commit_result.returncode != 0:
                    stderr = commit_result.stderr or commit_result.stdout or ""
                    if "nothing to commit" in stderr or "nothing added" in stderr:
                        await websocket.send_json({
                            "type": "warning",
                            "message": "⚠️ No files were generated. Check the generation logs.",
                        })
                        await _send_phase(7, "Publishing project", "Nothing to commit", "error")
                        return workspace_path
                    logger.error("new_project_mode: git commit failed: %s", stderr[:200])

                # Build file list for frontend
                try:
                    generated_files = []
                    for _root, _dirs, _fnames in os.walk(workspace_path):
                        _dirs[:] = [d for d in _dirs if d not in ("node_modules", ".git")]
                        for _f in _fnames:
                            _rel = os.path.relpath(os.path.join(_root, _f), workspace_path)
                            generated_files.append(_rel)
                    if generated_files:
                        await websocket.send_json({"type": "file_change", "files": sorted(generated_files)})
                except Exception:
                    pass

                # ── STEP 6: Push to the new repo ──────────────────────────────
                await websocket.send_json({
                    "type": "progress",
                    "message": f"🚀 Pushing to {new_repo_html_url}…",
                })

                push_result = await asyncio.to_thread(
                    subprocess.run,
                    ["git", "push", "-u", "origin", "main"],
                    cwd=workspace_path,
                    capture_output=True,
                    text=True,
                    timeout=180,
                )

                if push_result.returncode != 0:
                    err_msg = (push_result.stderr or push_result.stdout or "unknown push error")
                    if git_token:
                        err_msg = err_msg.replace(git_token, "***")
                    logger.error("new_project_mode push failed: %s", err_msg[:300])
                    await websocket.send_json({
                        "type": "error",
                        "message": f"❌ Push failed: {err_msg[:200]}",
                    })
                    await _send_phase(7, "Publishing project", "Push failed", "error")
                else:
                    logger.info("new_project_mode: successfully pushed to %s", new_repo_html_url)

                    # ── STEP 7: Save to DB ─────────────────────────────────────
                    if chat_session_id and new_repo_html_url:
                        try:
                            from app.supabase_client import db_client
                            async with db_client(None) as sb:
                                await (
                                    sb.table("chat_sessions")
                                    .update({
                                        "platform_repo_url":    new_repo_html_url,
                                        "platform_repo_branch": "main",
                                    })
                                    .eq("id", chat_session_id)
                                    .execute()
                                )
                            logger.info("Saved new repo URL to session %s", chat_session_id)
                        except Exception as _db_err:
                            logger.warning("Failed to save platform_repo_url: %s", _db_err)

                    # ── STEP 8: Emit events to frontend ────────────────────────
                    await websocket.send_json({
                        "type": "repo_created",
                        "repoUrl":    new_repo_html_url,
                        "repoName":   new_repo_name,
                        "branch":     "main",
                        "branchUrl":  new_repo_html_url,
                        "prUrl":      "",
                        "platformOwned": True,
                    })
                    await websocket.send_json({
                        "type": "complete",
                        "message": (
                            f"✅ Project created!\n\n"
                            f"📦 Repository: [{new_repo_name}]({new_repo_html_url})\n"
                            f"🌿 Branch: `main`"
                        ),
                    })
                    await _send_phase(7, "Publishing project", f"Pushed to {new_repo_name}", "done")
                    validated["auto_created_repo"] = new_repo_html_url
                    validated["platform_owned"]   = True


            except Exception as _p7_err:
                logger.error("new_project_mode Phase 7 failed: %s", _p7_err, exc_info=True)
                await websocket.send_json({
                    "type": "error",
                    "message": f"❌ Publish failed: {str(_p7_err)[:200]}",
                })
                await _send_phase(7, "Publishing project", "Publish failed", "error")



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

        # Clean up scratch workspaces ONLY on failure.
        # Successful workspaces are kept alive so follow-up tasks
        # can reuse them. They expire naturally via workspace reaper TTL.
        # We detect failure by checking if workspace_path was returned
        # (the return statement above sets it before reaching finally).
        # If we reached finally via an exception, workspace_path is still set
        # but the function returns None due to the except block.
        # Legacy: clean up workspaces without conversation_id
        if not conversation_id and workspace_path and os.path.exists(workspace_path):
            try:
                shutil.rmtree(workspace_path)
                logger.info("Workspace cleaned up (no conversation_id): %s", workspace_path)
            except Exception:
                pass
