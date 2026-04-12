"""
pipeline/step1_validate.py — Pipeline Step 1: validate all inputs.

Extracted verbatim from task_pipeline.py (lines 708–991).
Zero logic changes.
"""

from __future__ import annotations

import logging

from fastapi import WebSocket

from .constants import (
    PLATFORM_GITHUB_TOKEN,
    _FALLBACK_GEMINI_KEY,
)
from .github import (
    is_fine_grained_token,
    _resolve_template_repo,
    _sanitize_repo_name,
)

logger = logging.getLogger(__name__)


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
        # [LUCID_PROJECT] header is ALWAYS the very first non-empty line of a
        # genuine wizard task.  Searching further into the string would cause
        # false positives when a previous wizard session's last_task is injected
        # as conversation context (e.g. "## What happened in the previous
        # session\n\nTask: [LUCID_PROJECT]...").
        _header_raw = ""
        for _ln in _task_str[:300].split("\n"):
            _ln_stripped = _ln.strip()
            if not _ln_stripped:
                continue  # skip blank lines at the top
            # The first real content line must contain the marker to be a wizard task
            if "[LUCID_PROJECT]" in _ln_stripped:
                _header_raw = _ln_stripped
            break  # stop after the first non-empty line regardless
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
