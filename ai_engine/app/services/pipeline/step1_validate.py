"""
pipeline/step1_validate.py — Pipeline Step 1: validate all inputs.

Extracted verbatim from task_pipeline.py (lines 708–991).
Zero logic changes.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
from urllib.parse import urlparse, urlunparse

from fastapi import WebSocket

from .constants import (
    PLATFORM_GITHUB_TOKEN,
)
from .github import (
    is_fine_grained_token,
    _resolve_template_repo,
    _sanitize_repo_name,
)

logger = logging.getLogger(__name__)


def _normalize_repo_clone_url(provider: str, repo: str) -> str:
    """Return an HTTPS clone URL without credentials for GitHub/GitLab repos."""
    raw = str(repo or "").strip()
    if not raw:
        return ""

    parsed = urlparse(raw)
    if parsed.scheme and parsed.hostname:
        host = parsed.hostname
        if parsed.port:
            host = f"{host}:{parsed.port}"
        path = parsed.path.strip("/")
        clean = urlunparse(parsed._replace(netloc=host, path=f"/{path}", query="", fragment=""))
        return clean if clean.endswith(".git") else f"{clean}.git"

    scp_match = re.match(r"^(?:[^@]+@)?([^:]+):(.+)$", raw)
    if scp_match:
        host, path = scp_match.group(1), scp_match.group(2).strip("/")
        clean = f"https://{host}/{path}"
        return clean if clean.endswith(".git") else f"{clean}.git"

    host = "github.com" if provider == "github" else "gitlab.com"
    path = raw.strip("/")
    return f"https://{host}/{path}.git"


# Cheap heuristics that catch the obvious garbage prompts ("dasdasdasdas",
# "asdfasdfasdf", "aaaaaa") BEFORE we burn a Gemini grounded-research call
# on them. Real prompts always pass — the bar is intentionally low.
def _looks_like_garbage(raw: str) -> str:
    """Return a clarifying-question string if the prompt is unintelligible, else ''.

    Phrased as a friendly question, not an error. The orchestrator sends it
    as `type: "clarify"` so the UI renders it as a chat assistant message
    rather than a red error banner.
    """
    text = (raw or "").strip().lower()
    if len(text) < 8:
        return (
            "Hmm, that's a bit short for me to work with. "
            "Tell me a little more — what kind of app or website are you building? "
            "For example: 'a landing page for my coffee shop' or 'a CRM dashboard for sales calls'."
        )
    compact = re.sub(r"\s+", "", text)
    # Repeated short cluster: "dasdasdas" → matches r"(.{1,4})\1{2,}"
    if re.fullmatch(r"(.{1,4})\1{2,}", compact):
        return (
            "That looks like a typo — I couldn't make sense of it. 🙂\n\n"
            "Could you describe what you'd like to build? A few examples:\n"
            "• A landing page for an Italian restaurant in Brooklyn\n"
            "• A SaaS pricing page for a project-management tool\n"
            "• A portfolio site for a freelance designer"
        )
    letters = [c for c in text if c.isalpha()]
    if letters:
        vowels = sum(c in "aeiou" for c in letters)
        # English/Latin prose runs 30-50% vowels. Below 15% is almost always
        # consonant-mashing like "dfgdfgdfg" or "qwrtqwrt".
        if vowels / len(letters) < 0.15:
            return (
                "I couldn't read that as words. Could you tell me, in plain English, "
                "what you'd like to build? E.g. 'a blog about plants' or 'an admin panel for orders'."
            )
    # Need at least 2 distinct ≥3-char tokens — short business descriptors like
    # "kino website", "yoga studio", "coffee shop" are legitimate intent and
    # should pass. Pure single-word inputs ("website", "app") still fail since
    # they carry no domain signal. The other heuristics above (length, vowel
    # ratio, repeated-cluster) catch the real garbage cases.
    tokens = {t for t in re.findall(r"[a-z]{3,}", text)}
    if len(tokens) < 2:
        return (
            "I need a tiny bit more to go on. What's the project about?\n\n"
            "A couple of examples:\n"
            "• 'modern coffee shop landing page'\n"
            "• 'fitness coach portfolio with booking form'\n"
            "• 'B2B logistics admin dashboard'"
        )
    # Bare project-type inputs ("landing page", "a website", "modern landing
    # page") pass the token-count check (2+ tokens) but carry NO field signal.
    # We must ask what it's FOR before building — a landing page for a coffee
    # shop looks nothing like one for a SaaS product. Mirrors the
    # deterministic check in clarity_agent.is_bare_project_type; kept in sync
    # so both gates agree.
    try:
        from app.services.clarity_agent import is_bare_project_type, _detected_type_label
        if is_bare_project_type(text):
            label = _detected_type_label(text)
            return (
                f"Got it — what's this {label} for? "
                "Tell me about the business, product, or person.\n\n"
                "A couple of examples:\n"
                "• 'modern coffee shop landing page'\n"
                "• 'fitness coach portfolio with booking form'\n"
                "• 'B2B logistics admin dashboard'"
            )
    except Exception:
        # Fail-open: if the import or check ever breaks, behave as before.
        # clarity_agent (called earlier in ws.py) is still the primary gate.
        pass
    return ""


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
        # ── LLM/code-agent keys ───────────────────────────
        # New-project generation still uses Anthropic in the downstream
        # generation pipeline. Existing-project edits can use Codex/OpenAI for
        # the agentic code-edit pass, with Claude as fallback when available.
        api_key = user.get("anthropic_api_key")
        anthropic_api_key = "" if api_key is None else str(api_key).strip()
        if anthropic_api_key == "None":
            anthropic_api_key = ""
        openai_api_key = str(
            user.get("openai_api_key")
            or os.environ.get("OPENAI_API_KEY", "")
            or ""
        ).strip()
        openai_model = str(user.get("openai_model") or "").strip()
        codex_agent_available = bool(
            openai_api_key
            or os.environ.get("CODEX_CLI_PATH")
            or shutil.which("codex")
        )

        def _has_anthropic_key() -> bool:
            return bool(anthropic_api_key)

        async def _send_missing_generation_key() -> None:
            await websocket.send_json({
                "type": "error",
                "message": (
                    "❌ Anthropic API key not found. New project generation "
                    "still requires Anthropic. Add it in Settings."
                ),
            })

        # Gemini auth is now Vertex ADC inside gemini_post — no per-user key.

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
        # Walk the first non-blank line of the task in full — do NOT slice
        # `_task_str` first. A long description (e.g. 280-char Uzbek text)
        # plus the `[LUCID_PROJECT] description=… | stack=nextjs | …` framing
        # easily exceeds 300 chars, and a pre-slice cuts the header mid-value
        # (e.g. `stack=nextjs` becomes `stack=next` → unknown stack →
        # template clone skipped → local-skeleton fallback).
        _header_raw = ""
        for _ln in _task_str.split("\n", 4):
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

            if not _has_anthropic_key():
                await _send_missing_generation_key()
                return None

            # ── Extract fields from header ────────────────────────────────
            def _hdr(field: str) -> str:
                m = _re_early.search(rf"{field}=([^|]+)", _header_raw)
                return m.group(1).strip() if m else ""

            _stack       = _hdr("stack")        # e.g. "nextjs"
            _description = _hdr("description")  # e.g. "netflix style blog"
            _backend     = _hdr("backend")      # e.g. "none"

            # If the user has already answered clarifying questions
            # ([LUCID_CLARIFY::…] markers prepended in ws.py), they have
            # engaged with the system meaningfully — don't reject them now
            # based on a short description like "acca website". The Q&A
            # itself proved the input is real intent, not keyboard mashing.
            _already_clarified = "[LUCID_CLARIFY::" in _task_str
            if not _already_clarified:
                _garbage_reason = _looks_like_garbage(_description)
                if _garbage_reason:
                    logger.info("validate_inputs: rejecting garbage wizard description %r — %s",
                                _description[:80], _garbage_reason)
                    await websocket.send_json({"type": "clarify", "message": _garbage_reason})
                    return None

            # ── Smart stack resolution when user picked "Choose for me" ─────
            # The wizard sends `stack=auto` for "Choose for me" (or sometimes
            # an empty stack). _TEMPLATE_REGISTRY only knows concrete stacks
            # (nextjs, react, vue), so without resolution the registry lookup
            # misses → no clone URL → fallback to local skeleton + a warning
            # in the chat. To make this smart, classify the description into
            # nextjs | react | vue BEFORE the registry lookup.
            #
            # Two-tier classifier (see quick_stack_classifier.py):
            #   • Layer 1: keyword pre-filter (zero LLM cost, ~µs)
            #   • Layer 2: Gemini Flash structured output (~$0.001, ~1-2s)
            # Both fail-soft to "nextjs" so this step can never block.
            if _stack.lower() in ("", "auto"):
                from app.services.quick_stack_classifier import classify_stack
                _resolved_stack = await classify_stack(_description, timeout_s=8.0)
                logger.info(
                    "NEW_PROJECT_MODE: stack=%r resolved via classifier → %r",
                    _stack or "auto", _resolved_stack,
                )
                # Inform the user that we picked a stack for them
                try:
                    _STACK_LABELS = {"nextjs": "Next.js", "react": "React + Vite", "vue": "Vue.js"}
                    await websocket.send_json({
                        "type": "progress",
                        "message": f"🎯 Auto-selected stack: {_STACK_LABELS.get(_resolved_stack, _resolved_stack)}",
                    })
                except Exception:
                    pass
                _stack = _resolved_stack

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
                "anthropic_api_key": anthropic_api_key,
                "openai_api_key": openai_api_key,
                "openai_model": openai_model,
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
        branch: str | None = None
        # Tracks whether the repo we will operate on was created by our
        # platform (wizard flow). Used by the edit-mode router to pick the
        # direct-API single-call path vs the agentic SDK path.
        platform_repo_url: str = ""

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
                # Retry up to 3 times with 1-second backoff so a transient
                # Supabase timeout does NOT silently create a brand-new project
                # instead of editing the existing wizard project.
                _db_result = None
                _db_err_last = None
                for _attempt in range(3):
                    try:
                        import asyncio as _asyncio
                        from app.supabase_client import db_client
                        async with db_client(None) as sb:
                            _db_result = await _asyncio.wait_for(
                                (
                                    sb.table("chat_sessions")
                                    .select("platform_repo_url,platform_repo_branch")
                                    .eq("id", chat_session_id)
                                    .maybe_single()
                                    .execute()
                                ),
                                timeout=8.0,
                            )
                        _db_err_last = None
                        break  # success
                    except Exception as _e:
                        _db_err_last = _e
                        logger.warning(
                            "DB lookup for platform_repo_url failed (attempt %d/3): %s",
                            _attempt + 1, _e,
                        )
                        if _attempt < 2:
                            import asyncio as _asyncio2
                            await _asyncio2.sleep(1.0)

                if _db_err_last is not None:
                    # All 3 attempts failed — warn user and fall back to scratch mode
                    logger.error(
                        "All DB retries failed for chat_session %s — entering scratch mode. Last error: %s",
                        chat_session_id, _db_err_last,
                    )
                    await websocket.send_json({
                        "type": "warning",
                        "message": (
                            "⚠️ Could not reach database to check for existing project. "
                            "If this is a follow-up task, please try again in a moment."
                        ),
                    })
                    scratch_mode = True
                elif _db_result and _db_result.data and _db_result.data.get("platform_repo_url"):
                    existing_repo_url = _db_result.data["platform_repo_url"]
                    platform_repo_url = existing_repo_url
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
                        # Default to staging — Lucid generations always live on
                        # the staging branch; main is reserved for published code.
                        branch = _db_result.data.get("platform_repo_branch") or "staging"
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
                base_repo_url = _normalize_repo_clone_url("github", repo)
                from app.services.vcs.git import _inject_token_into_url, strip_auth_from_url
                repo_url = _inject_token_into_url(base_repo_url, token)
                logger.info("repo_url built for github/%s", strip_auth_from_url(base_repo_url))

            elif git_provider == "gitlab":
                if not token or not str(token).strip():
                    await websocket.send_json({
                        "type": "error",
                        "message": "❌ GitLab token not found. Connect GitLab in Settings.",
                    })
                    return None
                repo = str(repo).strip()
                token = str(token).strip()
                base_repo_url = _normalize_repo_clone_url("gitlab", repo)
                from app.services.vcs.git import _inject_token_into_url, strip_auth_from_url
                repo_url = _inject_token_into_url(base_repo_url, token)
                logger.info("repo_url built for gitlab/%s", strip_auth_from_url(base_repo_url))

            else:
                await websocket.send_json({
                    "type": "error",
                    "message": f"❌ Unknown git provider: {git_provider}",
                })
                return None

        # ── Branch ────────────────────────────────────────
        # Follow-up mode (line 245) already set branch="staging" from chat_sessions.
        # Why: Lucid generations live on staging; main is reserved for publish.
        # If we let user.selected_branch overwrite, every follow-up edit lands on
        # main → Vercel auto-deploys before the user clicks Publish.
        if branch is None:
            branch = str(user.get("selected_branch", "main")).strip() or "main"

        # ── Task string ───────────────────────────────────
        if not task or not task.strip():
            await websocket.send_json({
                "type": "error",
                "message": "❌ Task description is empty.",
            })
            return None

        # Follow-up tasks on existing repos are often short ("fix the header") —
        # only enforce the garbage gate when this is genuinely a fresh request.
        # `scratch_mode=True` means no repo, no chat history → first message.
        if scratch_mode:
            if not _has_anthropic_key():
                await _send_missing_generation_key()
                return None
            _garbage_reason = _looks_like_garbage(task)
            if _garbage_reason:
                logger.info("validate_inputs: rejecting garbage scratch task %r — %s",
                            task[:80], _garbage_reason)
                await websocket.send_json({"type": "clarify", "message": _garbage_reason})
                return None

        # Non-wizard normal tasks (user's own repo)
        # new_project_mode is always False here — wizard tasks returned early above.
        validated = {
            "anthropic_api_key": anthropic_api_key,
            "openai_api_key": openai_api_key,
            "openai_model": openai_model,
            "git_provider": git_provider,
            "repo_url": repo_url or "",
            "branch": branch,
            "git_token": token or "",
            "scratch_mode": scratch_mode,
            "new_project_mode": False,
            "package_manager": user.get("package_manager", "npm"),
            "template_clone_url": "",
            "template_repo_html_url": "",
            # Set only when the follow-up task targets a wizard-created
            # repo we generated ourselves. Edit-mode router uses this to
            # decide direct-API vs SDK — external repos never set it.
            "platform_repo_url": platform_repo_url,
        }

        mode_label = " (scratch mode)" if scratch_mode else ""
        if not _has_anthropic_key() and not codex_agent_available:
            await websocket.send_json({
                "type": "error",
                "message": (
                    "❌ No code-edit agent is configured. Add an Anthropic key "
                    "or configure Codex/OpenAI on the server."
                ),
            })
            return None
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
