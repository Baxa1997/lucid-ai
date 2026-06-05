"""
pipeline/orchestrator.py — run_pipeline: the top-level coordinator.

Extracted verbatim from task_pipeline.py (lines 5137–6344).
Zero logic changes — only import paths updated.
"""

from __future__ import annotations

import hashlib
import os
import json
import shutil
import asyncio
import subprocess
import logging

from fastapi import WebSocket

from app.services.openhands_manager import openhands_manager
from app.services.workspace_manager import workspace_manager
from app.services.llm_retry import emit_pipeline_failure, failure_code_from_exception

from .constants import PLATFORM_GITHUB_TOKEN, _PLATFORM_ORG
from .ws_utils import _send_file_tree
from .package_manager import detect_package_manager, _pm_install_cmd, _pm_env
from .github import (
    is_fine_grained_token,
    _create_github_repo,
    create_github_repo,
    derive_repo_name,
)
from .step5_fixers import _fix_broken_layout_imports
from .step1_validate import validate_inputs
from .step2_clone import clone_with_openhands
from .step3_classify import classify_task
from .step3b_edit_intent import extract_edit_intent, EditIntent
from .step4_explore import explore_with_gemini, gemini_research, gemini_create_plan
from .step4b_images import analyze_images
from .step5_execute import execute_with_claude, execute_project_in_batches
from .step5_direct import execute_direct_edit
from .step5_cli import execute_with_claude_cli
from .step5_codex import execute_with_codex_cli
from .step5b_build_verify import verify_build
from .step6_verify import verify_changes, push_with_openhands
from app.paths import NODE_MODULES_CACHE_ROOT, new_project_workspace_path

logger = logging.getLogger(__name__)


def _is_discuss_mode_task(task: str) -> bool:
    return "[DISCUSS MODE" in (task or "")


def _is_route_creation_task(task: str) -> bool:
    text = (task or "").lower()
    action = any(w in text for w in ("add", "create", "make", "build", "implement", "new"))
    target = any(w in text for w in (" page", "screen", "route", "view"))
    return action and target


def _edit_agent_provider() -> str:
    """Return the preferred agentic edit executor.

    ``codex`` is the default for the long-horizon code-edit path. Claude stays
    available as a fallback while Codex burns in.
    """
    raw = os.environ.get("LUCID_EDIT_AGENT_PROVIDER", "codex").strip().lower()
    if raw in {"codex", "openai"}:
        return "codex"
    if raw in {"claude", "anthropic"}:
        return "claude"
    if raw in {"auto", ""}:
        return "codex"
    return "codex"


async def _answer_discuss_mode(
    *,
    task: str,
    workspace_path: str,
    api_key: str,
    websocket: WebSocket,
    classification: dict,
    user_id: str | None = None,
    openai_api_key: str = "",
    openai_model: str = "",
) -> bool:
    """Answer an existing-project question with read-only Claude tools."""
    if not str(api_key or "").strip() and _edit_agent_provider() == "codex":
        try:
            from app.services.codex_cli import run_codex_session

            model = str(openai_model or "").strip()
            if model.startswith("openai/"):
                model = model.split("/", 1)[1]
            readonly_prompt = (
                "You are Lucid AI's read-only project assistant. Answer the "
                "user's question by inspecting the workspace when helpful. "
                "Do not write, edit, delete, install, run builds, commit, or "
                "push. If the user is actually asking for a code change, "
                "explain briefly that it should be run in edit mode.\n\n"
                f"USER QUESTION:\n{task}"
            )
            result = await run_codex_session(
                prompt=readonly_prompt,
                workspace_path=workspace_path,
                websocket=websocket,
                api_key=openai_api_key,
                user_id=user_id,
                model=model,
                timeout_seconds=180,
                phase_label="discuss_mode_codex",
                sandbox_mode="read-only",
            )
            return bool(result.success)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Codex discuss-mode answer failed: %s", exc, exc_info=True)

    try:
        from app.services.claude_cli import run_claude_session

        model_id = str(
            classification.get("model_id")
            or classification.get("model")
            or "claude-sonnet-4-6"
        )
        system_prompt = (
            "You are Lucid AI's read-only project assistant. Answer the user's "
            "question by inspecting the workspace when helpful. Do not write, edit, "
            "delete, install, run builds, or mutate files. If the user is actually "
            "asking for a code change, explain briefly that it should be run in edit mode."
        )
        result = await run_claude_session(
            prompt=task,
            workspace_path=workspace_path,
            api_key=api_key,
            websocket=websocket,
            user_id=user_id,
            model=model_id,
            max_turns=8,
            timeout_seconds=180,
            append_system_prompt=system_prompt,
            allowed_tools="Read,Glob,Grep,LS",
            disallowed_tools="Write,Edit,MultiEdit,Bash",
            max_consecutive_reads=12,
            phase_label="discuss_mode",
        )
        return bool(result.success)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("discuss-mode answer failed: %s", exc, exc_info=True)
        try:
            await websocket.send_json({
                "type": "chat_message",
                "role": "agent",
                "content": "I couldn't inspect the project for that question. Please try again.",
            })
        except Exception:
            pass
        return False

# ── node_modules cache ────────────────────────────────────────────────────────
# Keyed by package-manager + package.json hash so the same template reuses a
# pre-built node_modules on every subsequent run (~2s symlink vs 60-120s install).
_NM_CACHE_ROOT = NODE_MODULES_CACHE_ROOT


# ── Edit-mode routing ────────────────────────────────────────────────────
# Decides whether a follow-up edit can be handled by a single Anthropic
# ``/v1/messages`` call (direct path) or needs the agentic Claude Code SDK
# (sdk path). The SDK is valuable for exploration, but for surgical edits
# on a repo we generated ourselves it wastes ~80 % of tokens accumulating
# tool-use context across turns to do work that fits in one call.

def _intent_from_editable_target(
    target: dict,
    workspace_path: str,
) -> EditIntent:
    """Turn a click-to-edit payload into a confident EditIntent.

    Two payload shapes are accepted:

      (1) Editable-wrapped (preferred) — element had a
          ``data-editable-path`` attribute. Shape::

            { "path": "hero.title",
              "type": "text" | "longtext" | "image_url" | "url",
              "file": "src/components/sections/Hero.jsx",   # optional
              "fuzzy": false }

          We trust the path/file fully: the element was rendered by
          our own ``<Editable>`` wrapper, no LLM guessing. Confidence
          = 100, scope = narrow.

      (2) Fuzzy fallback — any other DOM element the listener could
          identify (heading, paragraph, button, image, …). Shape::

            { "path": "h1:Welcome to…",
              "type": "text" | "image_url",
              "fuzzy": true,
              "tag": "h1",
              "text": "Welcome to Acme Studios",
              "className": "text-5xl font-bold",
              "src": "/hero.jpg" }   # only for tag=img

          We can\\u2019t name the exact file, but the visible text (or
          image src) is almost always unique across the workspace and
          ``literal_anchors`` triggers a grep in step3b that locates
          it. Confidence = 85 (one notch below the wrapper path), still
          narrow scope so the direct-edit fast path stays in play.

    Empty / malformed targets return a non-actionable EditIntent so
    the caller falls back to the Flash extractor.
    """
    if not isinstance(target, dict):
        return EditIntent()
    dot_path = (target.get("path") or "").strip()
    file_hint = (target.get("file") or "").strip().lstrip("/")
    fuzzy = bool(target.get("fuzzy"))
    text_snippet = (target.get("text") or "").strip()
    img_src = (target.get("src") or "").strip()

    if not dot_path and not file_hint and not text_snippet and not img_src:
        return EditIntent()

    candidates: list[str] = []
    if file_hint:
        candidates.append(file_hint)

    # ``literal_anchors`` are the strings step3b will grep for in the
    # workspace. The dot-path is included for wrapper-mode disambig
    # (a file may contain many data-editable-path attributes). For
    # fuzzy mode the visible text or image src IS the locator.
    anchors: list[str] = []
    if dot_path:
        anchors.append(dot_path)
    if text_snippet and len(text_snippet) >= 4:
        # Grep needs enough specificity. Trim very long snippets — the
        # first ~80 chars are plenty for uniqueness in a small project.
        anchors.append(text_snippet[:80])
    if img_src and not img_src.startswith("data:"):
        # Inline data: URLs are huge and not useful for grep. File-path
        # sources (./hero.jpg, /images/x.png, http://…) all work.
        anchors.append(img_src)

    # Fuzzy mode needs candidate_files populated for ``is_actionable``
    # to be True; without files, the router falls through to the
    # extractor (which is default-off). Resolve anchors → real paths
    # via the same grep step3b uses for hallucination defense. Errors
    # are swallowed — worst case we return non-actionable and the
    # legacy Gemini file walker takes over.
    if fuzzy and anchors and not candidates:
        try:
            from .step3b_edit_intent import _grep_anchors
            matches = _grep_anchors(workspace_path, anchors)
            for rel in matches:
                if rel not in candidates:
                    candidates.append(rel)
        except Exception as exc:
            logger.debug("_intent_from_editable_target: fuzzy grep failed: %s", exc)

    # Section hint:
    #   • Wrapper mode: leading dot-segment ("hero" from "hero.title").
    #   • Fuzzy mode: don\\u2019t guess — the section is whatever file
    #     happens to contain the text anchor.
    head = dot_path.split(".")[0] if dot_path and not fuzzy else ""
    target_sections = [head.lower()] if head else []

    # ``type`` from the wrapper / fuzzy payload maps cleanly to our
    # change_type vocab. Image clicks always mean content (swap src);
    # everything else is content unless the caller flagged style.
    raw_type = (target.get("type") or "").strip().lower()
    if raw_type in ("text", "longtext", "image_url", "url"):
        change_type = "content"
    elif raw_type in ("class", "style"):
        change_type = "style"
    else:
        change_type = "content"

    # Wrapper-mode also points at the runtime content JSON as a
    # secondary candidate so direct-edit can update the on-disk copy
    # of the text the user is editing.
    if not fuzzy:
        landing_json = os.path.join(workspace_path, "src", "content", "landing.json")
        if os.path.isfile(landing_json):
            rel = "src/content/landing.json"
            if rel not in candidates:
                candidates.append(rel)

    # Fuzzy mode is slightly less certain than wrapper mode, but a
    # rendered visible-text match in our own generated repo is still
    # very high-signal — we keep narrow scope so the direct-edit
    # path stays eligible, just notch confidence down a tier.
    confidence = 85 if fuzzy else 100

    return EditIntent(
        target_pages=[],
        target_sections=target_sections,
        target_components=[],
        candidate_files=candidates,
        change_type=change_type,
        literal_anchors=anchors,
        scope="narrow",
        confidence=confidence,
        extracted=True,
    )


def _route_edit_mode(
    validated: dict,
    classification: dict,
    relevant_files: list[str],
    edit_intent: "EditIntent | None" = None,
    task: str = "",
) -> str:
    """Return ``"direct"`` or ``"sdk"`` for the current follow-up task.

    Decision rules (intentionally conservative — only send to ``direct``
    when every condition lines up):

      1. Repo was generated by our platform (``platform_repo_url`` flag)
         → we know conventions, manifest exists, direct edit is safe.
         External repos ALWAYS use SDK so Claude can explore.

      2. Gemini classified the task as simple (``ui_simple`` or
         ``feature_simple``) — complex redesigns need multi-turn work.
         OVERRIDE: when Step 3b produced an actionable narrow intent,
         the structured target (a literal class swap, a copy edit) is
         more authoritative than the freeform task_type heuristic, so
         we accept higher task_types too.

      3. Gemini identified ≤ 3 files AND ≤ 4 total relevant files from
         the file walk — anything bigger deserves the agent.
         OVERRIDE: when Step 3b is actionable we trust up to 6 files
         since the targets came from a workspace-grounded extractor
         rather than an LLM file walk.

      4. At least one relevant file was found — no files means no target.
    """
    if _is_route_creation_task(f"{task} {classification.get('intent') or ''}"):
        return "sdk"

    # Rule 1 — must be our own template/project.
    if not validated.get("platform_repo_url") and not validated.get("is_platform_owned"):
        return "sdk"

    intent_actionable = bool(
        edit_intent is not None and getattr(edit_intent, "is_actionable", False)
    )

    # Rule 2 — simple task type. Bypassed when Step 3b gave us a
    # structured narrow intent — the intent IS the simplicity proof.
    task_type = str(classification.get("task_type", "")).lower()
    if task_type not in ("ui_simple", "feature_simple") and not intent_actionable:
        return "sdk"

    # Rule 3 — small blast radius.
    files_estimate = int(classification.get("files_estimate", 99))
    max_files = 6 if intent_actionable else 4
    max_estimate = 5 if intent_actionable else 3
    if files_estimate > max_estimate or len(relevant_files) > max_files:
        return "sdk"

    # Rule 4 — must have a target.
    if not relevant_files:
        return "sdk"

    return "direct"


async def _cached_install(
    workspace_path: str,
    pm: str,
    websocket,
) -> None:
    """Install node_modules ON THE OVERLAY FS, symlink into the workspace.

    Cache layout (under /tmp/lucid_nm_cache/<pm>_<pkg-hash>/):
      package.json, lockfile, .npmrc   — copied from workspace
      node_modules/                    — installed in place (hardlinks, fast)

    Workspace just gets a symlink: workspace/node_modules → cache/node_modules.

    Why this design: the workspace lives on `/app/storage/...` which is a macOS
    Docker Desktop bind mount (fuse). Installing 730 packages there is 5–10×
    slower than overlay FS because every syscall is RPC, and pnpm hardlinks
    fail with errno -116 forcing the slower `copy` import method. Installing
    in the cache dir (on overlay) sidesteps all of it — hardlinks work, IO is
    native, install completes in 60–120s instead of 6–9 min.

    Cache hit  → symlink existing node_modules into workspace  (~0.1s)
    Cache miss → install in cache_dir, then symlink                (~60-120s)
    Always non-fatal: any exception is logged and swallowed.
    """
    pkg_path = os.path.join(workspace_path, "package.json")
    if not os.path.exists(pkg_path):
        return

    try:
        pkg_hash = hashlib.md5(open(pkg_path, "rb").read()).hexdigest()[:14]
        cache_key = f"{pm}_{pkg_hash}"
        cache_dir = os.path.join(_NM_CACHE_ROOT, cache_key)
        cache_nm = os.path.join(cache_dir, "node_modules")
        ws_nm = os.path.join(workspace_path, "node_modules")

        from .package_manager import _pm_install_cmd, _pm_env

        def _framework_binary_present(nm_root: str) -> bool:
            return (
                os.path.isfile(os.path.join(nm_root, ".bin", "next"))
                or os.path.isfile(os.path.join(nm_root, ".bin", "vite"))
                or os.path.isfile(os.path.join(nm_root, ".bin", "react-scripts"))
            )

        def _write_marker() -> None:
            try:
                import time as _time
                marker_path = os.path.join(workspace_path, ".lucid_install_done")
                with open(marker_path, "w", encoding="utf-8") as mf:
                    mf.write(f"{pm}\n{int(_time.time())}\n")
            except OSError as mexc:
                logger.debug("_cached_install: marker write failed: %s", mexc)

        def _symlink_cache_to_ws() -> bool:
            try:
                # Replace any pre-existing workspace node_modules with a symlink
                # to the cache. shutil.rmtree() on a stale half-installed tree
                # would be slow on the bind mount, so only rm if it's empty or
                # already a symlink — otherwise leave it (let local_preview deal).
                if os.path.islink(ws_nm):
                    os.unlink(ws_nm)
                elif os.path.isdir(ws_nm):
                    try:
                        if not os.listdir(ws_nm):
                            os.rmdir(ws_nm)
                        else:
                            logger.info(
                                "_cached_install: ws node_modules non-empty — skipping symlink"
                            )
                            return False
                    except OSError:
                        return False
                os.symlink(cache_nm, ws_nm)
                return True
            except OSError as exc:
                logger.warning("_cached_install: symlink failed: %s", exc)
                return False

        # ── Cache HIT ────────────────────────────────────────────
        if os.path.isdir(cache_nm) and _framework_binary_present(cache_nm):
            _symlink_cache_to_ws()
            _write_marker()
            logger.info("node_modules cache hit (%s) — symlinked in <1s", cache_key)
            try:
                await websocket.send_json({
                    "type": "progress",
                    "message": f"⚡ Dependencies ready (cache hit — {pm})",
                })
            except Exception:
                pass
            return

        # ── Cache MISS: install IN cache_dir (overlay FS) ────────
        try:
            await websocket.send_json({
                "type": "progress",
                "message": f"📦 Installing dependencies ({pm})...",
            })
        except Exception:
            pass

        os.makedirs(cache_dir, exist_ok=True)

        # Copy package.json + lockfiles + .npmrc into cache_dir so pnpm
        # has everything it needs to resolve from the workspace's lockfile.
        for fname in ("package.json", "pnpm-lock.yaml", "package-lock.json",
                      "yarn.lock", "bun.lockb", ".npmrc", ".nvmrc"):
            src = os.path.join(workspace_path, fname)
            if os.path.isfile(src):
                try:
                    shutil.copy2(src, os.path.join(cache_dir, fname))
                except OSError as cexc:
                    logger.debug("_cached_install: copy %s skipped: %s", fname, cexc)

        # Override package_import_method to hardlink — cache_dir is on overlay,
        # pnpm store is on overlay (/tmp/pnpm_store), so hardlinks work across
        # the same FS and are ~5× faster than copy. The env's default `copy`
        # is only needed when installing onto the bind mount (fallback paths).
        cache_env = dict(_pm_env(pm))
        cache_env["npm_config_package_import_method"] = "hardlink"

        # Strip the explicit --package-import-method=copy flag from the install
        # command so the hardlink env wins. Other flags (store-dir, prefer-offline)
        # stay as-is.
        install_cmd = [c for c in _pm_install_cmd(pm) if c != "--package-import-method=copy"]

        result = await asyncio.to_thread(
            subprocess.run,
            install_cmd,
            cwd=cache_dir,
            capture_output=True,
            text=True,
            # 600s cap — overlay-FS hardlink install of 730 packages usually
            # finishes in 60–120s, but cold downloads on a fresh container can
            # push to 4-5 min. 600s leaves headroom without blocking forever.
            timeout=600,
            env=cache_env,
        )

        # pnpm advisory exits (rc=1) for things like "Lockfile is up to date,
        # resolution step is skipped", ERR_PNPM_IGNORED_BUILDS, or peer-dep
        # warnings — all benign. The structural truth is: did node_modules end
        # up with the framework binary present? If yes, treat as success even
        # when rc != 0. Without this rc-agnostic check, every cache install
        # was being marked "failed" and local_preview was re-installing the
        # ENTIRE tree (12+ minutes on macOS bind mount).
        install_ok = os.path.isdir(cache_nm) and _framework_binary_present(cache_nm)
        if install_ok:
            _symlink_cache_to_ws()
            _write_marker()
            if result.returncode != 0:
                tail = (result.stderr or result.stdout or "")[:200]
                logger.info(
                    "node_modules installed into cache → %s (rc=%d, advisory: %s)",
                    cache_key, result.returncode, tail.strip()[:120],
                )
            else:
                logger.info(
                    "node_modules installed into cache → %s (symlinked to workspace)",
                    cache_key,
                )
            try:
                await websocket.send_json({
                    "type": "progress",
                    "message": f"✅ Dependencies installed ({pm})",
                })
            except Exception:
                pass
        else:
            # Genuine install failure — no binary landed. Leave cache_dir for
            # inspection; local_preview will run its own install as a fallback.
            err = (result.stderr or result.stdout or "")[:400]
            logger.warning("_cached_install: %s install non-zero in cache, no binary (non-fatal): %s", pm, err)

    except Exception as exc:
        logger.warning("_cached_install error (non-fatal): %s", exc)


async def run_pipeline(
    task: str,
    user: dict,
    websocket: WebSocket,
    task_id: str,
    conversation_id: str = "",
    chat_session_id: str = "",
    images: list = None,
    session=None,
    editable_target: dict | None = None,
) -> str | None:
    """Run all pipeline steps sequentially.

    Each step runs one at a time — never simultaneously.
    OpenHands and Claude never overlap.
    Always cleans up workspace in finally block.

    Returns the workspace path on success, None on failure.
    """
    workspace_path = None
    validated = None
    classification = {"model": "sonnet", "complexity": "medium", "task_type": "feature"}
    model = "sonnet"
    _npm_install_task = None  # background install task — awaited before build verify

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
        await _send_phase(1, "Preparing workspace", "Checking API keys and repository settings…", "active")
        validated = await validate_inputs(task, user, websocket, chat_session_id=chat_session_id)
        if validated is None:
            await _send_phase(1, "Preparing workspace", "Validation failed", "error")
            return
        await _send_phase(1, "Preparing workspace", "All inputs validated", "done")
        await asyncio.sleep(0.8)

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
            workspace_path = new_project_workspace_path(task_id, suffix=str(uuid4())[:6])
            os.makedirs(workspace_path, exist_ok=True)
            os.chmod(workspace_path, 0o777)

            await websocket.send_json({
                "type": "progress",
                "message": "🔨 Setting up local workspace...",
            })

            # Initialize a git repo with 'main' as default branch
            await asyncio.to_thread(
                subprocess.run,
                ["git", "init", "-b", "main"],
                cwd=workspace_path,
                capture_output=True, text=True, timeout=10,
            )
            await asyncio.to_thread(
                subprocess.run,
                ["git", "checkout", "-B", "main"],
                cwd=workspace_path, capture_output=True, timeout=5,
            )
            await asyncio.to_thread(
                subprocess.run,
                ["git", "config", "user.name", "Lucid AI"],
                cwd=workspace_path, capture_output=True, timeout=5,
            )
            await asyncio.to_thread(
                subprocess.run,
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

                import re
                stack_match = re.search(r"stack=(\S+)", str(task_original or ""))
                detected_stack = stack_match.group(1).strip() if stack_match else ""
                is_admin = detect_admin_from_task(task_original or task)

                logger.info("Skeleton detection: stack=%s, is_admin=%s, task=%s",
                            detected_stack, is_admin, (task_original or task)[:60])

                await _send_phase(2, "Preparing workspace", f"Loading {detected_stack or 'project'} template…", "active")

                skeleton_path = get_skeleton_for_stack(detected_stack, is_admin, task=task_original or task)
                if skeleton_path:
                    copied_files = copy_skeleton(skeleton_path, workspace_path)
                    skeleton_name = os.path.basename(skeleton_path)
                    logger.info("Copied skeleton '%s' (%d files)", skeleton_name, len(copied_files))
                    await websocket.send_json({
                        "type": "progress",
                        "message": f"📦 Skeleton loaded: {skeleton_name} ({len(copied_files)} files)",
                    })
                    validated["skeleton_name"] = skeleton_name
                    validated["skeleton_stack"] = detected_stack
                    validated["is_admin"] = is_admin
                    await asyncio.sleep(1.0)
                    await _send_phase(2, "Preparing workspace", f"Template ready: {skeleton_name}", "done")
                else:
                    logger.warning("No skeleton found for stack: %s", detected_stack)
                    await _send_phase(2, "Preparing workspace", "Using default structure", "done")
            except Exception as skel_err:
                logger.warning("Skeleton copy failed (non-fatal): %s", skel_err)

            # Check token type early and warn if invalid
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
                await emit_pipeline_failure(
                    websocket,
                    phase="prepare_workspace",
                    code="auth_error",
                    message="The platform's GitHub token isn't valid for creating repos. An admin needs to update PLATFORM_GITHUB_TOKEN to a Classic PAT with repo scope.",
                    retriable=False,
                )
                await _send_phase(2, "Preparing workspace", "Invalid GitHub token", "error")
                return

            # ── Step 2c: Install dependencies (background, cached) ────────────
            # Runs in parallel with research/generation — node_modules is only
            # needed for build verify (Phase 6), not for file writing (Phase 5).
            _npm_install_task = None
            pkg_json = os.path.join(workspace_path, "package.json")
            if os.path.exists(pkg_json):
                user_pm_pref = validated.get("package_manager", "npm")
                pm = detect_package_manager(workspace_path, user_pm_pref)
                validated["package_manager"] = pm
                logger.info("Package manager detected: %s — starting background install", pm)
                _npm_install_task = asyncio.create_task(
                    _cached_install(workspace_path, pm, websocket)
                )

            # ── Step 2d: Create .claude/settings.json (with Stitch AI MCP) ───────
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
                    },
                    "mcpServers": {
                        # Sequential Thinking — forces step-by-step decomposition before writing files.
                        "sequentialthinking": {
                            "command": "npx",
                            "args": ["-y", "@modelcontextprotocol/server-sequentialthinking"],
                        },
                        # Context7 — fetches live, version-accurate docs for any npm package.
                        # Eliminates hallucinated shadcn/ui, recharts, framer-motion APIs.
                        "context7": {
                            "command": "npx",
                            "args": ["-y", "@upstash/context7-mcp@latest"],
                        },
                    },
                }
                with open(os.path.join(claude_dir, "settings.json"), "w") as f:
                    json.dump(claude_settings, f, indent=2)

                os.chmod(workspace_path, 0o777)
                await asyncio.to_thread(
                    subprocess.run,
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
            template_clone_url = validated.get("template_clone_url", "")
            git_token = validated.get("git_token", "")

            from uuid import uuid4
            workspace_path = new_project_workspace_path(task_id, suffix=str(uuid4())[:6])
            os.makedirs(workspace_path, exist_ok=True)
            os.chmod(workspace_path, 0o777)

            if template_clone_url:
                await websocket.send_json({
                    "type": "progress",
                    "message": "📦 Cloning GitHub template into workspace...",
                })
                logger.info("new_project_mode: cloning template from %s...", template_clone_url[:60])

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
                    workspace_path = None
                else:
                    logger.info("new_project_mode: template clone succeeded in %s", workspace_path)
                    await websocket.send_json({
                        "type": "progress",
                        "message": "✅ GitHub template cloned",
                    })

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

                    pkg_json = os.path.join(workspace_path, "package.json")
                    if os.path.exists(pkg_json):
                        user_pm_pref = validated.get("package_manager", "npm")
                        pm = detect_package_manager(workspace_path, user_pm_pref)
                        validated["package_manager"] = pm
                        logger.info("new_project_mode: queuing background install with %s", pm)
                        _npm_install_task = asyncio.create_task(
                            _cached_install(workspace_path, pm, websocket)
                        )

            # Fallback: no clone_url → use local skeleton
            if not workspace_path or not os.path.exists(workspace_path) or not os.listdir(workspace_path):
                _fallback_reason = "unknown"
                if not template_clone_url:
                    _fallback_reason = "no template_clone_url provided"
                elif not workspace_path:
                    _fallback_reason = "workspace_path is None (clone failed)"
                elif not os.path.exists(workspace_path):
                    _fallback_reason = "workspace_path does not exist"
                elif not os.listdir(workspace_path):
                    _fallback_reason = "workspace is empty after clone attempt"

                logger.warning(
                    "new_project_mode: falling back to local skeleton — reason: %s, "
                    "clone_url: %s, git_token_present: %s",
                    _fallback_reason,
                    template_clone_url[:40] if template_clone_url else "NONE",
                    bool(git_token),
                )

                await websocket.send_json({
                    "type": "chat_message",
                    "role": "system",
                    "content": (
                        f"⚠️ **Template clone failed** ({_fallback_reason}). "
                        "Using local skeleton instead. The project will still generate correctly, "
                        "but may use a generic starter structure."
                    ),
                })

                workspace_path = workspace_path or new_project_workspace_path(task_id, suffix="fb")
                os.makedirs(workspace_path, exist_ok=True)
                os.chmod(workspace_path, 0o777)

                await asyncio.to_thread(subprocess.run, ["git", "init", "-b", "main"],
                                cwd=workspace_path, capture_output=True, text=True, timeout=10)
                await asyncio.to_thread(subprocess.run, ["git", "config", "user.name", "Lucid AI"],
                                cwd=workspace_path, capture_output=True)
                await asyncio.to_thread(subprocess.run, ["git", "config", "user.email", "ai@lucid.dev"],
                                cwd=workspace_path, capture_output=True)

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
                    else:
                        logger.warning("new_project_mode: no skeleton found for stack=%s", detected_stack)
                except Exception as _skel_err:
                    logger.warning("new_project_mode: fallback skeleton error: %s", _skel_err)

                # Install deps for fallback skeleton (background, cached)
                _fb_pkg = os.path.join(workspace_path, "package.json")
                if os.path.exists(_fb_pkg):
                    _fb_pm = detect_package_manager(workspace_path, validated.get("package_manager", "pnpm"))
                    validated["package_manager"] = _fb_pm
                    _npm_install_task = asyncio.create_task(
                        _cached_install(workspace_path, _fb_pm, websocket)
                    )
                    logger.info("new_project_mode fallback: queued background install with %s", _fb_pm)

                await websocket.send_json({
                    "type": "progress",
                    "message": "✅ Local workspace ready (fallback)",
                })

            # Always create .claude/settings.json
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
                    },
                    "mcpServers": {
                        # Sequential Thinking — forces step-by-step decomposition before writing files.
                        "sequentialthinking": {
                            "command": "npx",
                            "args": ["-y", "@modelcontextprotocol/server-sequentialthinking"],
                        },
                        # Context7 — fetches live, version-accurate docs for any npm package.
                        # Eliminates hallucinated shadcn/ui, recharts, framer-motion APIs.
                        "context7": {
                            "command": "npx",
                            "args": ["-y", "@upstash/context7-mcp@latest"],
                        },
                    },
                }
                with open(os.path.join(claude_dir, "settings.json"), "w") as _csf:
                    json.dump(claude_settings, _csf, indent=2)
                await asyncio.to_thread(
                    subprocess.run,
                    ["chmod", "-R", "777", workspace_path],
                    capture_output=True, timeout=10,
                )
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

        # ── Set session workspace_dir EARLY ───────────────
        if session and workspace_path:
            session.workspace_dir = workspace_path
            logger.info("Session workspace_dir set early: %s", workspace_path)

        # ── Send file tree immediately after workspace is ready ──
        await _send_file_tree(websocket, workspace_path)

        # ── HANDOFF POINT: OpenHands is now DEAD ──────────
        if await openhands_manager.is_active():
            await openhands_manager.destroy_all()
            await asyncio.sleep(0.5)

        # ── External repo index ───────────────────────────
        # User-connected repositories do not have Lucid's generated content
        # manifest/editable metadata. Build a deterministic repo briefing and
        # move edits off production-like branches before any code agent runs.
        if not (validated.get("scratch_mode") or validated.get("new_project_mode")):
            try:
                from app.services.external_project_index import (
                    index_external_project,
                    is_external_project,
                )
                if is_external_project(validated):
                    await websocket.send_json({
                        "type": "progress",
                        "message": "🧭 Indexing connected project structure...",
                    })
                    _external_index = await index_external_project(
                        workspace_path=workspace_path,
                        validated=validated,
                        task=task,
                        websocket=websocket,
                    )
                    _proj = (_external_index.get("project") or {})
                    _routes = _external_index.get("routes") or []
                    await websocket.send_json({
                        "type": "project_indexed",
                        "framework": _proj.get("framework") or "unknown",
                        "language": _proj.get("language") or "unknown",
                        "packageManager": _proj.get("package_manager") or validated.get("package_manager", "npm"),
                        "routeCount": len(_routes),
                        "branch": validated.get("branch") or "",
                        "message": (
                            f"Connected project indexed: {_proj.get('framework') or 'unknown'} "
                            f"({len(_routes)} route{'s' if len(_routes) != 1 else ''})"
                        ),
                    })
                    logger.info(
                        "External project indexed: framework=%s branch=%s routes=%d",
                        _proj.get("framework"), validated.get("branch"), len(_routes),
                    )
            except Exception as _idx_err:
                logger.warning("External project index failed (non-fatal): %s", _idx_err, exc_info=True)
                try:
                    await websocket.send_json({
                        "type": "warning",
                        "message": "Project indexing failed, so the agent will inspect the repo directly.",
                    })
                except Exception:
                    pass

        # ── Phase 3: Understand / classify ─────────────────
        # For new projects, the generator may still ask clarification questions
        # before research starts, so keep the label in the intake phase.
        # For existing repos: classify task complexity first, then explore.
        if validated.get("scratch_mode") or validated.get("new_project_mode"):
            await _send_phase(3, "Understanding project", "Checking whether I have enough detail…", "active")
        else:
            await _send_phase(3, "Classifying task", "Analyzing task complexity…", "active")
            classification = await classify_task(
                task,
                websocket,
            )
            model = classification.get("model", "sonnet")
            await _send_phase(3, "Classifying task", f"Assigned to {model} ({classification.get('complexity', 'medium')})", "done")
            await asyncio.sleep(0.8)

            if _is_discuss_mode_task(task):
                await _send_phase(
                    4,
                    "Answering question",
                    "Inspecting the project without editing files…",
                    "active",
                )
                _user_id_for_billing = (
                    (session.user_id if session else None)
                    or (user.get("user_id") if isinstance(user, dict) else None)
                )
                success = await _answer_discuss_mode(
                    task=task,
                    workspace_path=workspace_path,
                    api_key=validated["anthropic_api_key"],
                    websocket=websocket,
                    classification=classification,
                    user_id=_user_id_for_billing,
                    openai_api_key=str(validated.get("openai_api_key") or ""),
                    openai_model=str(validated.get("openai_model") or ""),
                )
                await _send_phase(
                    4,
                    "Answering question",
                    "Answered without changing files" if success else "Could not answer question",
                    "done" if success else "error",
                )
                return

            # ── Phase 3b: Structured edit-intent extraction ───────────
            # Two paths into the same EditIntent:
            #   (1) User clicked an element in the preview — the frontend
            #       sent ``editable_target = {path, type, file}``. The
            #       user already pointed at the exact thing they want
            #       changed, so we synthesize a 100%-confidence intent
            #       and skip the Flash extractor entirely.
            #   (2) Plain text prompt — run the workspace-grounded Flash
            #       extractor. Fail-open: any error returns an empty
            #       EditIntent and the legacy file walk runs unchanged.
            edit_intent: EditIntent = EditIntent()
            if editable_target:
                try:
                    edit_intent = _intent_from_editable_target(
                        editable_target, workspace_path,
                    )
                    try:
                        await websocket.send_json({
                            "type": "progress",
                            "message": (
                                f"🎯 Editing selected element: "
                                f"{editable_target.get('path') or editable_target.get('file') or 'target'}"
                            ),
                        })
                    except Exception:
                        pass
                except Exception as _et_err:
                    logger.warning(
                        "Phase 3b: editable_target synthesis failed — %s "
                        "(falling back to extractor)",
                        _et_err,
                    )
                    edit_intent = EditIntent()
            if not edit_intent.is_actionable:
                try:
                    edit_intent = await extract_edit_intent(
                        task=task,
                        workspace_path=workspace_path,
                        classification=classification,
                        websocket=websocket,
                    )
                except Exception as _intent_err:
                    logger.warning(
                        "Phase 3b: edit-intent extractor raised — %s",
                        _intent_err,
                    )
                    edit_intent = EditIntent()

        # ── Phase 3b: Generate CLAUDE.md (new projects only) ──
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
        if validated.get("scratch_mode") or validated.get("new_project_mode"):

            from app.services.project_generator import generate_new_project

            # Pipeline-side WS reference must be the reconnect-safe proxy, not
            # the raw FastAPI WebSocket. The plan-confirmation gate can park the
            # generator coroutine for up to 30 minutes — if the browser drops and
            # reconnects during that window, ws_proxy.attach() swaps in the new
            # socket, but a captured raw `websocket` is left pointing at the dead
            # one. Every post-confirm _ws_send then fails silently and the UI
            # appears stuck on "researching". Use the proxy when available.
            _ws_target = (session.ws_proxy if session and session.ws_proxy is not None else websocket)

            # Resolve user_id once for billing — Claude phase calls and Gemini
            # research inside generate_new_project read it from a contextvar.
            _user_id_for_billing = (
                (session.user_id if session else None)
                or (user.get("user_id") if isinstance(user, dict) else None)
                or ""
            )

            success = await generate_new_project(
                description=task,
                workspace_path=workspace_path,
                validated=validated,
                websocket=_ws_target,
                chat_session_id=chat_session_id,
                user_jwt=user.get("user_jwt", ""),
                user_id=_user_id_for_billing,
            )

            if not success:
                # Check if the user rejected the plan and provided a correction
                _correction = getattr(_ws_target, "_plan_correction", None)
                if _correction:
                    # Clean up and retry with the corrected description
                    try:
                        delattr(_ws_target, "_plan_correction")
                    except AttributeError:
                        pass
                    logger.info("Retrying generation with corrected description: %s", _correction[:80])
                    task = _correction  # Use the correction as the new task
                    success = await generate_new_project(
                        description=task,
                        workspace_path=workspace_path,
                        validated=validated,
                        websocket=_ws_target,
                        chat_session_id=chat_session_id,
                        user_jwt=user.get("user_jwt", ""),
                        user_id=_user_id_for_billing,
                    )
                    if not success:
                        await _send_phase(5, "Writing code", "Generation failed", "error")
                        return
                else:
                    await _send_phase(5, "Writing code", "Generation failed", "error")
                    return

            await asyncio.sleep(0.5)
            await _send_file_tree(websocket, workspace_path)

            # ── Instant Sandpack preview — send all source files to the
            # browser NOW so the user sees a live preview immediately while
            # build verification and the local dev server spin up in the
            # background.  The frontend switches from Sandpack to the real
            # iframe automatically when preview_ready arrives later.
            try:
                await _emit_preview_files(workspace_path, websocket)
                logger.info("Emitted preview_files for instant Sandpack preview")
            except Exception as _spk_err:
                logger.warning("Sandpack preview_files emit failed (non-fatal): %s", _spk_err)

            # ── Phase 6: Build already verified inside generate_new_project ──
            # generate_new_project calls verify_and_fix_build() internally and
            # sets websocket._build_ok to reflect the actual result.
            _build_ok = getattr(websocket, "_build_ok", True)
            if _build_ok:
                await _send_phase(6, "Verifying build", "Build verified", "done")
            else:
                await _send_phase(6, "Verifying build", "Build has errors — check progress log", "error")

            plan = ""
            relevant_files: list[str] = []

        else:
            await _send_phase(4, "Exploring codebase", "Identifying relevant files…", "active")
            plan, relevant_files = await explore_with_gemini(
                task,
                workspace_path,
                classification,
                websocket,
                edit_intent=edit_intent,
            )
            if validated.get("external_project_brief"):
                plan = (
                    f"{validated['external_project_brief']}\n\n"
                    "## Implementation Plan\n"
                    f"{plan or ''}"
                )
            await asyncio.sleep(0.8)
            await _send_phase(4, "Exploring codebase", "Implementation plan ready", "done")

            if plan and plan.strip():
                try:
                    # Parse plan into a structured summary for chat
                    plan_lines = [l.strip() for l in plan.strip().splitlines() if l.strip()]
                    files_to_change = [
                        l for l in plan_lines
                        if any(ext in l for ext in [".js", ".ts", ".jsx", ".tsx", ".py", ".css", ".json"])
                        and len(l) < 80
                    ][:6]
                    steps = [l for l in plan_lines if l.startswith(("1.", "2.", "3.", "4.", "5."))][:5]

                    if files_to_change or steps:
                        await websocket.send_json({
                            "type": "chat_message",
                            "role": "agent",
                            "messageType": "plan",
                            "planData": {
                                "intro": f"I'll implement this for you. Here's my plan:",
                                "features": steps or [task[:80]],
                                "design": "",
                                "entities": [],
                                "pages": [{"name": f, "type": "file"} for f in files_to_change],
                            },
                        })
                    else:
                        plan_preview = plan.strip()[:400]
                        await websocket.send_json({
                            "type": "chat_message",
                            "role": "agent",
                            "content": plan_preview,
                        })
                    await asyncio.sleep(1.5)
                except Exception:
                    pass

        # ── Phase 4.5: Analyze images (if any) ────────────
        if images:
            await _send_phase(4, "Analyzing images", f"Processing {len(images)} attached image(s)…", "active")
            image_analysis = await analyze_images(
                images,
                task,
                websocket,
            )
            if image_analysis:
                plan = (plan or "") + "\n\n## Visual Context (from attached images)\n" + image_analysis
            await _send_phase(4, "Analyzing images", f"Analyzed {len(images)} image(s)", "done")

        # ── Phase 5: Execute with Claude (edit-mode only) ──
        if not (validated.get("scratch_mode") or validated.get("new_project_mode")):
            # Router picks direct-API for small surgical edits on our
            # generated repos, SDK for everything else. See _route_edit_mode
            # for the full decision rules. A direct-path failure (bad JSON,
            # old_string not matched, HTTP error) falls through to the SDK
            # so the user never sees the optimisation miss.
            edit_path = _route_edit_mode(
                validated, classification, relevant_files,
                edit_intent=edit_intent,
                task=task,
            )
            logger.info(
                "Phase 5 router: path=%s task_type=%s files_estimate=%s relevant=%d "
                "intent_scope=%s intent_conf=%d intent_actionable=%s",
                edit_path,
                classification.get("task_type"),
                classification.get("files_estimate"),
                len(relevant_files),
                edit_intent.scope, edit_intent.confidence, edit_intent.is_actionable,
            )

            success = False
            if edit_path == "direct":
                await _send_phase(
                    5, "Writing code",
                    "Applying surgical edit (one-shot)…", "active",
                )
                # Load manifest once for the direct prompt — for our own
                # template projects it's always present and very cheap.
                _manifest_text = ""
                try:
                    _manifest_path = os.path.join(workspace_path, "TEMPLATE_MANIFEST.md")
                    if os.path.isfile(_manifest_path):
                        with open(_manifest_path, "r", encoding="utf-8", errors="replace") as _mf:
                            _manifest_text = _mf.read()
                except Exception:
                    pass

                # Pass user_id so step5_direct can attribute the LLM token
                # cost back to the user's monthly quota via the billing meter.
                _user_id_for_billing = (
                    (session.user_id if session else None)
                    or (user.get("user_id") if isinstance(user, dict) else None)
                )
                # When Step 3b locked the target with confidence, give
                # the direct path a wider file budget — extractor-anchored
                # candidates are more trustworthy than Gemini file-walk picks,
                # so the extra slots don't loosen the safety contract.
                _direct_max_files = 6 if edit_intent.is_actionable else 4
                success = await execute_direct_edit(
                    task=task,
                    workspace_path=workspace_path,
                    api_key=validated["anthropic_api_key"],
                    classification=classification,
                    plan=plan,
                    relevant_files=relevant_files,
                    websocket=websocket,
                    manifest=_manifest_text,
                    user_id=_user_id_for_billing,
                    max_files=_direct_max_files,
                )
                if not success:
                    logger.info("Direct-edit path declined — falling back to SDK")
                    try:
                        await websocket.send_json({
                            "type": "progress",
                            "message": "↪️  Falling back to agent mode…",
                        })
                    except Exception:
                        pass

            if not success:
                # Agentic path — either chosen by the router or fallback from
                # a failed direct attempt. No partial direct-edit state is
                # on disk at this point (execute_direct_edit is all-or-none).
                #
                # Default: Codex for the long-horizon project-edit pass.
                # Claude CLI/SDK remains as fallback so one provider failure
                # does not strand the user's workspace.
                preferred_agent = _edit_agent_provider()
                # If Codex is the preferred edit agent but the CLI isn't
                # installed (most local dev environments), demote to Claude
                # up-front so the phase label and progress messaging reflect
                # what will ACTUALLY run. Without this demotion the UI says
                # "Codex is implementing…" then silently falls back to Claude
                # with a "Falling back" message that confuses users.
                if preferred_agent == "codex":
                    try:
                        from app.services.codex_cli import codex_cli_available as _codex_ok
                        if not _codex_ok():
                            logger.info("orchestrator: Codex CLI unavailable — using Claude for edit")
                            preferred_agent = "claude"
                    except Exception:
                        preferred_agent = "claude"

                await _send_phase(
                    5,
                    "Writing code",
                    (
                        "Codex is implementing the task…"
                        if preferred_agent == "codex"
                        else f"Claude ({model}) is implementing the task…"
                    ),
                    "active",
                )
                try:
                    await websocket.send_json({
                        "type": "progress",
                        "message": (
                            "🤖 Codex is editing the project…"
                            if preferred_agent == "codex"
                            else "✏️  Editing the project…"
                        ),
                    })
                except Exception:
                    pass
                _user_id_for_billing = (
                    (session.user_id if session else None)
                    or (user.get("user_id") if isinstance(user, dict) else None)
                )
                _stack = (
                    validated.get("project_stack")
                    or validated.get("skeleton_stack")
                    or ""
                )

                if preferred_agent == "codex":
                    _openai_key = str(
                        validated.get("openai_api_key")
                        or os.environ.get("OPENAI_API_KEY", "")
                        or ""
                    ).strip()
                    success = await execute_with_codex_cli(
                        task,
                        workspace_path,
                        _openai_key,
                        classification,
                        plan,
                        websocket,
                        user_id=_user_id_for_billing,
                        stack=_stack,
                        model=str(validated.get("openai_model") or ""),
                    )
                    if not success and str(validated.get("anthropic_api_key") or "").strip():
                        logger.info("Codex edit path declined — falling back to Claude")
                        try:
                            await websocket.send_json({
                                "type": "progress",
                                "message": "↪️  Falling back to Claude agent mode…",
                            })
                        except Exception:
                            pass

                if not success and str(validated.get("anthropic_api_key") or "").strip():
                    _use_legacy_sdk = os.environ.get("LUCID_USE_CLAUDE_SDK", "").lower() in ("1", "true", "yes")
                    if _use_legacy_sdk:
                        success = await execute_with_claude(
                            task,
                            workspace_path,
                            validated["anthropic_api_key"],
                            classification,
                            plan,
                            websocket,
                        )
                    else:
                        success = await execute_with_claude_cli(
                            task,
                            workspace_path,
                            validated["anthropic_api_key"],
                            classification,
                            plan,
                            websocket,
                            user_id=_user_id_for_billing,
                            stack=_stack,
                        )

            if not success:
                await _send_phase(5, "Writing code", "Code execution failed", "error")
                return
            await _send_phase(5, "Writing code", "Code changes written", "done")
            await _send_file_tree(websocket, workspace_path)

        # ── Phase 6: Verify build (edit-mode only) ─────────
        if not (validated.get("scratch_mode") or validated.get("new_project_mode")):
            await _send_phase(6, "Verifying build", "Running build checks…", "active")

            try:
                await _fix_broken_layout_imports(workspace_path, websocket)
            except Exception as _fix_err:
                logger.warning("Pre-build import fixer failed (non-fatal): %s", _fix_err)

            _build_result = {"success": True, "needs_fix": False, "attempts": 0, "errors": "", "fixed_files": [], "error_count": 0}
            try:
                from app.services.build_validator import BuildValidator
                _bv = BuildValidator(
                    api_key=validated["anthropic_api_key"],
                    openai_api_key=str(validated.get("openai_api_key") or ""),
                    openai_model=str(validated.get("openai_model") or ""),
                    classification=classification,
                    websocket=websocket,
                    # 1 retry (2 attempts total). The 3-retry budget was burning
                    # up to ~27 min (4× builds × 180s + 3× Codex fix × 300s) on
                    # non-trivial build errors that Codex couldn't fix anyway.
                    # One fix attempt catches the easy cases (apostrophe, import
                    # path); beyond that, ship to staging and let the user
                    # iterate via chat. Matches landing_pipeline's bias.
                    max_retries=1,
                )
                _build_result = await _bv.validate_and_fix(workspace_path)
                logger.info(
                    "BuildValidator result: success=%s, needs_fix=%s, attempts=%d, errors=%d",
                    _build_result.get("success"), _build_result.get("needs_fix"),
                    _build_result.get("attempts", 0), _build_result.get("error_count", 0),
                )
            except Exception as _bv_err:
                logger.warning("BuildValidator failed (non-fatal, falling back): %s", _bv_err)
                await verify_build(workspace_path, validated["anthropic_api_key"], classification, websocket)
            await _send_phase(6, "Verifying build", "Build verification complete", "done")

        # ── Phase 6.5: Verify changes (edit-mode only) ──────────
        # `verify_changes` runs `git status --porcelain` and is meaningful only
        # in edit mode, where we're checking whether Claude actually touched
        # files. For scratch / new-project mode we just wrote the whole project
        # via the website/admin/landing pipelines — the diff check produces
        # false negatives (empty stdout if the workspace isn't a git repo or
        # if generation auto-committed) and silently skips the preview boot
        # below at Phase 6.7.
        if not (validated.get("scratch_mode") or validated.get("new_project_mode")):
            changed = await verify_changes(workspace_path, websocket)
            if not changed:
                await _send_phase(6, "Verifying build", "No changes detected", "error")
                return

        # ── Phase 6.7: Start Live Preview + Verify build in parallel ─────────
        # The preview dev server boot and the build verification are independent:
        # - build verify: runs `npm run build` to type-check and catch errors
        # - preview start: runs `npm run dev` to serve the app live
        # Running them simultaneously saves 30-60s.
        from app.services.local_preview import start_local_preview

        _preview_pm = validated.get("package_manager", "npm")
        # Use the route/project id as the preview registry key when present.
        # ws.py reconnect/reload paths also look up previews by project_id, so
        # starting under chat_session_id makes refresh lose a healthy server
        # whenever those ids differ.
        _conv_id    = (
            (getattr(session, "project_id", "") if session is not None else "")
            or chat_session_id
            or conversation_id
            or task_id
        )

        async def _start_preview_safe():
            try:
                await start_local_preview(
                    workspace_path=workspace_path,
                    conversation_id=_conv_id,
                    websocket=websocket,
                    package_manager=_preview_pm,
                )
            except Exception as _pe:
                logger.warning("Live preview failed (non-fatal): %s", _pe)
                try:
                    await websocket.send_json({
                        "type": "preview_error",
                        "error_stage": "start",
                        "message": "Preview could not be started — click Restart Preview to retry.",
                    })
                except Exception:
                    pass

        # For new projects: verify build AND boot preview simultaneously.
        # For edit-mode: build verify already ran above; just start preview.
        if validated.get("scratch_mode") or validated.get("new_project_mode"):
            # Both start at the same time
            await asyncio.gather(
                _start_preview_safe(),
                return_exceptions=True,
            )
        else:
            await _start_preview_safe()

        # ── Phase 6.8: UX Polish removed ──────────────────────────────────────
        # UX quality requirements (loading states, empty states, hover effects,
        # animations, accessible markup) are now baked into the generation
        # SYSTEM_PROMPT and enforced during Phase 2 code generation.
        # Removing this extra Claude Code SDK round-trip saves 30–90s per run.

        # ── Phase 7: Commit + Create Repo + Push ──────────
        if validated.get("scratch_mode"):
            await _send_phase(7, "Publishing project", "Committing code…", "active")

            commit_msg = f"Initial commit: {task[:50]} by Lucid AI"
            try:
                await asyncio.to_thread(
                    subprocess.run,
                    ["git", "add", "-A"],
                    cwd=workspace_path, capture_output=True, timeout=30,
                )
                commit_result = await asyncio.to_thread(
                    subprocess.run,
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

            platform_token = PLATFORM_GITHUB_TOKEN
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
                await emit_pipeline_failure(
                    websocket,
                    phase="publish",
                    code="auth_error",
                    message="Your GitHub token isn't compatible with creating repos. Reconnect GitHub in Settings using a Classic Personal Access Token.",
                    retriable=False,
                )
                await _send_phase(7, "Publishing project", "Invalid token type", "error")
            else:
                await websocket.send_json({
                    "type": "progress",
                    "message": "📦 Creating GitHub repository...",
                })

                repo_name, project_desc, _, _ = derive_repo_name(task_original, chat_session_id)

                repo_result = await create_github_repo(
                    project_name=repo_name,
                    github_token=git_token,
                    description=f"Generated by Lucid AI — {project_desc[:80]}",
                    is_private=True,
                    websocket=websocket,
                )

                if repo_result is None:
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

                    validated["auto_created_repo"] = html_url
                    validated["platform_owned"] = bool(platform_token)

                    await websocket.send_json({
                        "type": "progress",
                        "message": "🚀 Pushing code to GitHub...",
                    })

                    await asyncio.to_thread(
                        subprocess.run,
                        ["git", "remote", "add", "origin", auth_url],
                        cwd=workspace_path,
                        capture_output=True, text=True, timeout=10,
                    )
                    # Push to staging branch (not main). Vercel watches main →
                    # only deploys when user explicitly publishes (staging→main merge).
                    await asyncio.to_thread(
                        subprocess.run,
                        ["git", "branch", "-M", "staging"],
                        cwd=workspace_path,
                        capture_output=True, timeout=5,
                    )

                    push_result = await asyncio.to_thread(
                        subprocess.run,
                        ["git", "push", "-u", "origin", "staging"],
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
                        # Network/auth issues are usually transient — let the user retry.
                        _push_code = "auth_error" if any(s in err_msg.lower() for s in ("auth", "401", "403", "permission")) else "network"
                        await emit_pipeline_failure(
                            websocket,
                            phase="publish",
                            code=_push_code,
                            message=(
                                "Couldn't push your code to GitHub. Your token may have expired — reconnect in Settings, then try again."
                                if _push_code == "auth_error" else
                                "Couldn't push your code to GitHub right now. Please try again in a moment."
                            ),
                            retriable=True,
                        )
                        await _send_phase(7, "Publishing project", "Push failed", "error")
                    else:
                        db_session_id = chat_session_id
                        if db_session_id and html_url:
                            try:
                                from app.supabase_client import db_client
                                async with db_client(None) as sb:
                                    await (
                                        sb.table("chat_sessions")
                                        .update({
                                            "platform_repo_url": html_url,
                                            "platform_repo_branch": "staging",
                                        })
                                        .eq("id", db_session_id)
                                        .execute()
                                    )
                                logger.info("Saved platform_repo_url to session %s", db_session_id)
                            except Exception as db_err:
                                logger.warning("Failed to save platform_repo_url: %s", db_err)

                        await websocket.send_json({
                            "type": "complete",
                            "message": f"✅ Done! Code pushed to {html_url} (staging)",
                        })
                        await _send_phase(7, "Publishing project", "Code pushed to staging", "done")
                        logger.info("Project pushed to staging: %s", html_url)

        elif validated.get("new_project_mode"):
            # Build result gates the GO-LIVE step (push to main + Vercel
            # deploy). When the build fails we still create the GitHub repo
            # and push to the `staging` branch — that's the user's only
            # remote backup of the generated code. Previously this whole
            # block returned early and the entire project could be lost if
            # the local workspace was reaped before the user could re-open
            # and iterate. With the staging-only push, even a broken build
            # leaves a recoverable GitHub repo and the user can fix in chat,
            # then click Publish to merge staging→main when they're ready.
            #
            # Background-BV sync: landing_pipeline now kicks BuildValidator
            # off as an asyncio task and returns immediately so the dev-server
            # preview can boot in parallel. Before we make the publish decision
            # we MUST await that task — otherwise we'd read the provisional
            # `_build_ok=True` and ship broken code to main + Vercel.
            _build_task = getattr(websocket, "_build_task", None)
            if _build_task is not None and not _build_task.done():
                try:
                    # 5 min cap — well above the ~120s typical BV time, but
                    # bounded so a stuck build doesn't strand Phase 7 forever.
                    await asyncio.wait_for(asyncio.shield(_build_task), timeout=300)
                except asyncio.TimeoutError:
                    logger.warning(
                        "Phase 7: background BV task timed out at 300s — proceeding with current _build_ok"
                    )
                except Exception as _bv_wait_err:
                    logger.warning("Phase 7: background BV await error: %s", _bv_wait_err)
            _build_ok_for_publish = getattr(websocket, "_build_ok", True)
            _publish_draft_only = not _build_ok_for_publish
            if _publish_draft_only:
                logger.info(
                    "new_project_mode Phase 7: build failed — pushing to staging only, "
                    "skipping main + Vercel deploy. User can iterate via chat and "
                    "publish manually once the build passes."
                )
                await websocket.send_json({
                    "type": "chat_message",
                    "role": "system",
                    "content": (
                        "⚠️ The build had errors, so I saved your code to a draft branch "
                        "instead of publishing live. Paste the runtime error you see in the "
                        "preview and I'll fix it — then click Publish to go live."
                    ),
                })

            await _send_phase(7, "Publishing project", "Creating new repository…", "active")

            git_token      = validated.get("git_token", "")
            project_name   = validated.get("project_name", "lucid-project")
            project_desc   = validated.get("project_description", "")

            logger.info("new_project_mode Phase 7: creating repo '%s' → pushing workspace", project_name)

            try:
                from datetime import datetime as _dt
                # Per-second + 3-char random suffix so two generations of the
                # same prompt CANNOT collide on the same repo name. Earlier
                # versions used %m%d%H%M (minute granularity) which let the
                # second submit reuse the first's repo → reuse the same Vercel
                # project → the user saw the OLD deploy URL even though the
                # local preview rendered the new code.
                _ts = _dt.now().strftime("%m%d%H%M%S")
                _rand = os.urandom(2).hex()  # 4 hex chars, ~65k entropy
                new_repo_name = f"{project_name}-{_ts}-{_rand}"[:60]

                await websocket.send_json({
                    "type": "progress",
                    "message": f"📦 Creating new repo: {_PLATFORM_ORG}/{new_repo_name}…",
                })

                if not git_token:
                    raise RuntimeError("PLATFORM_GITHUB_TOKEN not available — cannot create repo")

                # Defensive retry: if the unique name STILL collides (extreme
                # race, or GitHub had a transient duplicate), bump the random
                # suffix and try once more. Without this, the orchestrator
                # raises and the user loses the whole generation.
                new_repo = None
                for _retry in range(3):
                    try:
                        new_repo = await _create_github_repo(
                            repo_name=new_repo_name,
                            token=git_token,
                            description=f"Generated by Lucid AI — {project_desc[:120]}" if project_desc else "Generated by Lucid AI",
                        )
                        break
                    except RuntimeError as _exc:
                        msg_l = str(_exc).lower()
                        if "422" in msg_l and ("already exists" in msg_l or "name already" in msg_l):
                            _rand = os.urandom(3).hex()
                            new_repo_name = f"{project_name}-{_ts}-{_rand}"[:60]
                            logger.info(
                                "new_project_mode: repo name collision — retrying as %s",
                                new_repo_name,
                            )
                            continue
                        raise
                if new_repo is None:
                    raise RuntimeError("Could not create a unique repo name after 3 attempts")
                new_repo_html_url  = new_repo.get("html_url", "")
                new_repo_clone_url = (
                    new_repo.get("clone_url", "")
                    .replace("https://github.com/", f"https://{git_token}@github.com/")
                )
                logger.info("new_project_mode: repo created: %s", new_repo_html_url)
                validated["auto_created_repo_id"] = new_repo.get("id", 0)

                await websocket.send_json({
                    "type": "progress",
                    "message": f"✅ Repo created: {new_repo_html_url}",
                })

                # Re-init git (detach from template remote)
                _git_dir = os.path.join(workspace_path, ".git")
                if os.path.exists(_git_dir):
                    shutil.rmtree(_git_dir)

                await asyncio.to_thread(subprocess.run, ["git", "init", "-b", "staging"],
                               cwd=workspace_path, capture_output=True, text=True, timeout=10)
                await asyncio.to_thread(subprocess.run, ["git", "config", "user.name", "Lucid AI"],
                               cwd=workspace_path, capture_output=True)
                await asyncio.to_thread(subprocess.run, ["git", "config", "user.email", "ai@lucid.dev"],
                               cwd=workspace_path, capture_output=True)
                await asyncio.to_thread(subprocess.run,
                               ["git", "remote", "add", "origin", new_repo_clone_url],
                               cwd=workspace_path, capture_output=True, timeout=10)

                await asyncio.to_thread(subprocess.run, ["git", "add", "-A"],
                               cwd=workspace_path, capture_output=True, timeout=30)

                commit_msg = f"feat: initial project generated by Lucid AI\n\nProject: {project_desc[:200] or new_repo_name}"
                commit_result = await asyncio.to_thread(
                    subprocess.run,
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

                await websocket.send_json({
                    "type": "progress",
                    "message": f"🚀 Pushing to {new_repo_html_url}…",
                })

                push_result = await asyncio.to_thread(
                    subprocess.run,
                    ["git", "push", "-u", "origin", "staging"],
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
                    _push_code2 = "auth_error" if any(s in err_msg.lower() for s in ("auth", "401", "403", "permission")) else "network"
                    await emit_pipeline_failure(
                        websocket,
                        phase="publish",
                        code=_push_code2,
                        message=(
                            "Couldn't save your project to its repository. Please try again."
                            if _push_code2 != "auth_error" else
                            "The platform's GitHub access expired or was revoked. An admin needs to refresh PLATFORM_GITHUB_TOKEN."
                        ),
                        retriable=True,
                    )
                    await _send_phase(7, "Publishing project", "Push failed", "error")
                else:
                    logger.info("new_project_mode: successfully pushed to %s", new_repo_html_url)

                    # ── Auto-publish first generation ─────────────────────
                    # Push staging → main and create a Vercel project so the
                    # user gets a live URL on the very first generation.
                    # Follow-up edits stay on staging only (publish is then
                    # explicit via the workspace's Publish button).
                    # SKIPPED when build failed: code is safely on staging,
                    # but we don't promote a broken site to main / Vercel.
                    auto_vercel_url = None
                    if _publish_draft_only:
                        logger.info(
                            "new_project_mode: staging push complete — skipping main+Vercel "
                            "because build failed. Repo: %s",
                            new_repo_html_url,
                        )
                    else:
                        main_push = await asyncio.to_thread(
                            subprocess.run,
                            ["git", "push", "origin", "staging:main"],
                            cwd=workspace_path,
                            capture_output=True, text=True, timeout=60,
                        )
                        if main_push.returncode == 0:
                            logger.info("Auto-published initial gen to main: %s", new_repo_html_url)
                            try:
                                from app.services.vercel import create_vercel_project
                                auto_vercel_url = await create_vercel_project(
                                    owner=_PLATFORM_ORG, repo=new_repo_name,
                                )
                            except Exception as _vc_err:
                                logger.warning("Vercel auto-create failed (non-fatal): %s", _vc_err)
                        else:
                            _err = (main_push.stderr or main_push.stdout or "")[:200]
                            if git_token:
                                _err = _err.replace(git_token, "***")
                            logger.warning("Auto-push to main failed (non-fatal): %s", _err)

                    if chat_session_id and new_repo_html_url:
                        try:
                            from app.supabase_client import db_client
                            _update = {
                                "platform_repo_url":    new_repo_html_url,
                                "platform_repo_branch": "staging",
                            }
                            if auto_vercel_url:
                                _update["vercel_url"] = auto_vercel_url
                            async with db_client(None) as sb:
                                await (
                                    sb.table("chat_sessions")
                                    .update(_update)
                                    .eq("id", chat_session_id)
                                    .execute()
                                )
                            logger.info("Saved new repo URL to session %s", chat_session_id)

                            # Persist deployment record so the project counts
                            # against the user's tier limit and shows up in
                            # any future "my published projects" view.
                            if auto_vercel_url:
                                from datetime import datetime as _dt2
                                user_id_for_dep = (
                                    user.get("user_id")
                                    if isinstance(user, dict) else None
                                )
                                if user_id_for_dep:
                                    try:
                                        async with db_client(None) as sb:
                                            await (
                                                sb.table("project_deployments")
                                                .upsert(
                                                    {
                                                        "user_id":       user_id_for_dep,
                                                        "project_id":    chat_session_id,
                                                        "repo_url":      new_repo_html_url,
                                                        "deploy_url":    auto_vercel_url,
                                                        "deploy_method": "vercel",
                                                        "status":        "deployed",
                                                        "deployed_at":   _dt2.utcnow().isoformat(),
                                                    },
                                                    on_conflict="user_id,project_id",
                                                )
                                                .execute()
                                            )
                                    except Exception as _dep_err:
                                        logger.warning("project_deployments upsert failed: %s", _dep_err)
                        except Exception as _db_err:
                            logger.warning("Failed to save platform_repo_url: %s", _db_err)

                    await websocket.send_json({
                        "type": "repo_created",
                        "repoUrl":    new_repo_html_url,
                        "repoName":   new_repo_name,
                        "branch":     "staging",
                        "branchUrl":  f"{new_repo_html_url}/tree/staging",
                        "prUrl":      "",
                        "platformOwned": True,
                    })

                    if auto_vercel_url:
                        await websocket.send_json({
                            "type": "published",
                            "repoUrl":   new_repo_html_url,
                            "vercelUrl": auto_vercel_url,
                            "message":   f"Your project is going live at {auto_vercel_url} (about 30 seconds)",
                        })
                        await websocket.send_json({
                            "type": "complete",
                            "message": (
                                f"Published to {auto_vercel_url}. "
                                "Type a message below to keep editing — "
                                "your changes will deploy automatically."
                            ),
                        })
                        await _send_phase(7, "Publishing project", "Project is going live", "done")
                    elif _publish_draft_only:
                        # Build failed → no Vercel deploy. Send an explicit
                        # "draft saved" banner with the staging URL so the
                        # user sees something prominent (instead of just the
                        # generic chat message above) and knows the next step.
                        await websocket.send_json({
                            "type": "published",
                            "repoUrl":   new_repo_html_url,
                            "vercelUrl": "",
                            "draft":     True,
                            "branchUrl": f"{new_repo_html_url}/tree/staging",
                            "message": (
                                "Saved to draft branch (build had errors). "
                                "Fix the errors in chat, then click Publish to go live."
                            ),
                        })
                        await websocket.send_json({
                            "type": "complete",
                            "message": (
                                f"⚠️ Saved to staging at {new_repo_html_url}/tree/staging — "
                                "the build had errors so I didn't deploy to Vercel. "
                                "Paste any preview error in chat and I'll fix it, "
                                "then Publish promotes staging → main."
                            ),
                        })
                        await _send_phase(7, "Publishing project", "Saved as draft (build errors)", "done")
                    else:
                        await websocket.send_json({
                            "type": "complete",
                            "message": (
                                f"✅ Your project is ready!\n\n"
                                f"_Click Publish in the top bar to make it live on the internet._"
                            ),
                        })
                        await _send_phase(7, "Publishing project", "Ready to publish", "done")

                    validated["auto_created_repo"] = new_repo_html_url
                    validated["platform_owned"]   = True
                    validated["auto_vercel_url"]  = auto_vercel_url

            except Exception as _p7_err:
                logger.error("new_project_mode Phase 7 failed: %s", _p7_err, exc_info=True)
                await websocket.send_json({
                    "type": "error",
                    "message": f"❌ Publish failed: {str(_p7_err)[:200]}",
                })
                await emit_pipeline_failure(
                    websocket,
                    phase="publish",
                    code=failure_code_from_exception(_p7_err),
                    message="Something went wrong while saving and publishing your project. Please try again.",
                    retriable=True,
                )
                await _send_phase(7, "Publishing project", "Publish failed", "error")

        else:
            await _send_phase(7, "Pushing changes", "Committing and pushing to remote…", "active")
            push_ok = await push_with_openhands(
                workspace_path,
                validated,
                task,
                task_id,
                websocket,
            )
            if not push_ok:
                await _send_phase(7, "Pushing changes", "Push failed", "error")
                return
            auto_repo = validated.get("auto_created_repo")
            if auto_repo:
                await websocket.send_json({
                    "type": "complete",
                    "message": f"✅ Done! Code pushed to {auto_repo}",
                })
            await _send_phase(7, "Pushing changes", "Changes pushed successfully", "done")

        # ── Phase 8: Vercel auto-deploy ───────────────────
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

                repo_parts = auto_repo.replace("https://github.com/", "").split("/")
                gh_owner = repo_parts[0] if len(repo_parts) > 0 else ""
                gh_repo = repo_parts[1] if len(repo_parts) > 1 else ""
                vercel_project_name = gh_repo[:100]

                create_payload = {
                    "name": vercel_project_name,
                    "framework": None,
                    "gitRepository": {
                        "type": "github",
                        "repo": f"{gh_owner}/{gh_repo}",
                        "repoId": validated.get("auto_created_repo_id", 0),
                    },
                }

                params = f"?teamId={vercel_team}" if vercel_team else ""

                async with httpx.AsyncClient(timeout=60) as client:
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
                                "message": "⏳ Deployment started, waiting for build...",
                            })

                            # Poll deployment status — max 300s (60 × 5s).
                            # Vercel cold first-builds routinely take 2-4 minutes,
                            # so the old 120s limit caused false "timed out" reports.
                            _poll_interval = 5
                            _poll_max = 60  # 300s total
                            for _poll_n in range(_poll_max):
                                await asyncio.sleep(_poll_interval)
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
                                    # Emit progress every ~60s so the UI shows activity
                                    if _poll_n > 0 and _poll_n % 12 == 0:
                                        elapsed = _poll_n * _poll_interval
                                        try:
                                            await websocket.send_json({
                                                "type": "progress",
                                                "message": f"⏳ Still building... ({elapsed}s elapsed)",
                                            })
                                        except Exception:
                                            pass

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
        # Structured failure event so the frontend can render specific
        # recovery copy (rate_limit → wait, auth → reconnect, etc.) instead
        # of just a generic error toast.
        try:
            await emit_pipeline_failure(
                websocket,
                phase="pipeline",
                code=failure_code_from_exception(e),
                message="The generation hit an error and stopped. Please try again — if it keeps happening, share the project ID so we can dig in.",
                retriable=True,
            )
        except Exception:
            pass
        return None

    finally:
        # Always destroy any lingering OpenHands conversations
        await openhands_manager.destroy_all()

        # node_modules are kept — the local dev server (start_local_preview)
        # runs npm run dev in the workspace and needs them to stay on disk.
        # Cleanup happens when stop_local_preview() is called or on server restart.



# ── Sandpack preview helpers ──────────────────────────────────────────────────


def _detect_sandpack_template(workspace_path: str) -> str:
    """Read package.json to pick the right Sandpack template.

    Returns 'nextjs', 'vue', or 'react' (default).
    """
    pkg_path = os.path.join(workspace_path, "package.json")
    try:
        with open(pkg_path, "r", encoding="utf-8") as f:
            pkg = json.loads(f.read())
        all_deps: dict = {}
        all_deps.update(pkg.get("dependencies", {}))
        all_deps.update(pkg.get("devDependencies", {}))
        if "next" in all_deps:
            return "nextjs"
        if "vue" in all_deps:
            return "vue"
    except Exception:
        pass
    return "react"

_PREVIEW_SKIP_DIRS = frozenset({
    "node_modules", ".git", ".next", "dist", "build",
    "__pycache__", ".cache", ".turbo", ".vercel",
})
_PREVIEW_SKIP_EXTS = frozenset({".lock", ".log"})
_PREVIEW_MAX_FILE  = 5 * 1024 * 1024  # 5 MB


async def _emit_preview_files(workspace_path: str, websocket: WebSocket) -> None:
    """Collect all workspace files and send them to the frontend as preview_files.

    The frontend's SandpackPreview component receives this message and bundles
    the project in-browser via Sandpack — no install step, no server needed.
    Template is auto-detected from package.json (nextjs / vue / react).
    """
    # Only include source files needed by Sandpack — config/lock/root files bloat the payload.
    # Sandpack needs: src/** and root-level entry points (index.html, package.json, globals.css).
    _PREVIEW_ROOT_FILES = {"index.html", "package.json", "vite.config.js", "vite.config.ts"}

    def _collect():
        files: dict[str, str] = {}
        for root, dirs, filenames in os.walk(workspace_path):
            dirs[:] = [d for d in dirs if d not in _PREVIEW_SKIP_DIRS]
            rel_root = os.path.relpath(root, workspace_path)
            # Skip non-src directories at the root level (public/, styles/, etc.)
            # Always include the workspace root itself and the src/ subtree.
            if rel_root != "." and not rel_root.startswith("src"):
                dirs.clear()
                continue
            for fname in filenames:
                _, ext = os.path.splitext(fname)
                if ext in _PREVIEW_SKIP_EXTS:
                    continue
                if fname.startswith(".") and fname not in {".env", ".env.local", ".env.example", ".gitignore"}:
                    continue
                # At workspace root, only include known entry-point files
                if rel_root == "." and fname not in _PREVIEW_ROOT_FILES:
                    continue
                abs_path = os.path.join(root, fname)
                rel_path = os.path.relpath(abs_path, workspace_path)
                try:
                    if os.path.getsize(abs_path) > _PREVIEW_MAX_FILE:
                        continue
                    with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                        files[rel_path] = f.read()
                except Exception:
                    pass
        return files

    files = await asyncio.to_thread(_collect)
    if not files:
        logger.warning("_emit_preview_files: no files found in %s", workspace_path)
        return

    template = _detect_sandpack_template(workspace_path)
    logger.info("_emit_preview_files: sending %d files to frontend (template=%s)", len(files), template)
    await websocket.send_json({
        "type": "preview_files",
        "files": files,
        "template": template,
        "message": "Booting browser preview…",
    })
