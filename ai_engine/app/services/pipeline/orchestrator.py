"""
pipeline/orchestrator.py — run_pipeline: the top-level coordinator.

Extracted verbatim from task_pipeline.py (lines 5137–6344).
Zero logic changes — only import paths updated.
"""

from __future__ import annotations

import os
import json
import shutil
import asyncio
import subprocess
import logging

from fastapi import WebSocket

from app.services.openhands_manager import openhands_manager
from app.services.workspace_manager import workspace_manager

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
from .step4_explore import explore_with_gemini, gemini_research, gemini_create_plan
from .step4b_images import analyze_images
from .step5_execute import execute_with_claude, execute_project_in_batches
from .step5b_build_verify import verify_build
from .step6_verify import verify_changes, push_with_openhands

logger = logging.getLogger(__name__)


async def run_pipeline(
    task: str,
    user: dict,
    websocket: WebSocket,
    task_id: str,
    conversation_id: str = "",
    chat_session_id: str = "",
    images: list = None,
    session=None,
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
                    validated["skeleton_name"] = skeleton_name
                    validated["skeleton_stack"] = detected_stack
                    validated["is_admin"] = is_admin
                else:
                    logger.warning("No skeleton found for stack: %s", detected_stack)
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
                await _send_phase(2, "Preparing workspace", "Invalid GitHub token", "error")
                return

            # ── Step 2c: Install dependencies ─────────────
            pkg_json = os.path.join(workspace_path, "package.json")
            if os.path.exists(pkg_json):
                user_pm_pref = validated.get("package_manager", "npm")
                pm = detect_package_manager(workspace_path, user_pm_pref)
                validated["package_manager"] = pm
                logger.info("Package manager detected: %s (user pref: %s)", pm, user_pm_pref)

                try:
                    await websocket.send_json({
                        "type": "progress",
                        "message": f"📦 Installing dependencies ({pm})...",
                    })

                    install_result = await asyncio.to_thread(
                        subprocess.run,
                        _pm_install_cmd(pm),
                        cwd=workspace_path,
                        capture_output=True,
                        text=True,
                        timeout=120,
                        env=_pm_env(pm),
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

            # ── Step 2d: Create .claude/settings.json ───────
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

                workspace_path = workspace_path or f"/tmp/lucid_new_{task_id}_fb"
                os.makedirs(workspace_path, exist_ok=True)
                os.chmod(workspace_path, 0o777)

                subprocess.run(["git", "init", "-b", "main"], cwd=workspace_path,
                                capture_output=True, text=True, timeout=10)
                subprocess.run(["git", "config", "user.name", "Lucid AI"],
                                cwd=workspace_path, capture_output=True)
                subprocess.run(["git", "config", "user.email", "ai@lucid.dev"],
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

        # ── Phase 3: Classify task ────────────────────────
        await _send_phase(3, "Classifying task", "Analyzing task complexity…", "active")
        classification = await classify_task(
            task,
            validated["gemini_api_key"],
            websocket,
        )
        model = classification.get("model", "sonnet")
        await _send_phase(3, "Classifying task", f"Assigned to {model} ({classification.get('complexity', 'medium')})", "done")

        # ── Phase 3b: Generate CLAUDE.md ─────────────────────
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
            await _send_phase(4, "Researching project", "Gemini is analyzing top products in this domain…", "active")

            from app.services.project_generator import generate_new_project

            success = await generate_new_project(
                description=task,
                workspace_path=workspace_path,
                validated=validated,
                websocket=websocket,
                chat_session_id=chat_session_id,
            )

            if not success:
                await _send_phase(5, "Writing code", "Generation failed", "error")
                return

            await _send_phase(6, "Verifying build", "Build verification complete", "done")
            await _send_file_tree(websocket, workspace_path)
            plan = ""

        else:
            await _send_phase(4, "Exploring codebase", "Identifying relevant files…", "active")
            plan = await explore_with_gemini(
                task,
                workspace_path,
                classification,
                validated["gemini_api_key"],
                websocket,
            )
            await _send_phase(4, "Exploring codebase", "Implementation plan ready", "done")

            if plan and plan.strip():
                try:
                    plan_preview = plan.strip()[:600]
                    await websocket.send_json({
                        "type": "chat_message",
                        "role": "agent",
                        "content": plan_preview,
                    })
                except Exception:
                    pass

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
                plan = (plan or "") + "\n\n## Visual Context (from attached images)\n" + image_analysis
            await _send_phase(4, "Analyzing images", f"Analyzed {len(images)} image(s)", "done")

        # ── Phase 5: Execute with Claude (edit-mode only) ──
        if not (validated.get("scratch_mode") or validated.get("new_project_mode")):
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
                await verify_build(workspace_path, validated["anthropic_api_key"], classification, websocket)
            await _send_phase(6, "Verifying build", "Build verification complete", "done")

        # ── Phase 6.5: Verify changes ────────────────────
        changed = await verify_changes(workspace_path, websocket)
        if not changed:
            await _send_phase(6, "Verifying build", "No changes detected", "error")
            return

        # ── Phase 6.7: Start Live Preview ────────────────
        preview_url = None
        try:
            from app.services.dev_server import start_dev_preview
            _pm = validated.get("package_manager", "npm")
            preview_url = await start_dev_preview(
                workspace_path=workspace_path,
                websocket=websocket,
                chat_session_id=chat_session_id,
                package_manager=_pm,
            )
            if preview_url:
                logger.info("Live preview started: %s", preview_url)
        except Exception as _preview_err:
            logger.warning("Live preview failed (non-fatal): %s", _preview_err)

        # ── Phase 7: Commit + Create Repo + Push ──────────
        if validated.get("scratch_mode"):
            await _send_phase(7, "Publishing project", "Committing code…", "active")

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
            await _send_phase(7, "Publishing project", "Creating new repository…", "active")

            git_token      = validated.get("git_token", "")
            project_name   = validated.get("project_name", "lucid-project")
            project_desc   = validated.get("project_description", "")

            logger.info("new_project_mode Phase 7: creating repo '%s' → pushing workspace", project_name)

            try:
                from datetime import datetime as _dt
                _ts = _dt.now().strftime("%m%d%H%M")
                new_repo_name = f"{project_name}-{_ts}"[:60]

                await websocket.send_json({
                    "type": "progress",
                    "message": f"📦 Creating new repo: {_PLATFORM_ORG}/{new_repo_name}…",
                })

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
                validated["auto_created_repo_id"] = new_repo.get("id", 0)

                await websocket.send_json({
                    "type": "progress",
                    "message": f"✅ Repo created: {new_repo_html_url}",
                })

                # Re-init git (detach from template remote)
                _git_dir = os.path.join(workspace_path, ".git")
                if os.path.exists(_git_dir):
                    shutil.rmtree(_git_dir)

                subprocess.run(["git", "init", "-b", "main"], cwd=workspace_path,
                               capture_output=True, text=True, timeout=10)
                subprocess.run(["git", "config", "user.name", "Lucid AI"],
                               cwd=workspace_path, capture_output=True)
                subprocess.run(["git", "config", "user.email", "ai@lucid.dev"],
                               cwd=workspace_path, capture_output=True)
                subprocess.run(["git", "remote", "add", "origin", new_repo_clone_url],
                               cwd=workspace_path, capture_output=True, timeout=10)

                subprocess.run(["git", "add", "-A"], cwd=workspace_path,
                               capture_output=True, timeout=30)

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

        # ALWAYS clean up workspace after pipeline completes.
        if workspace_path and os.path.exists(workspace_path):
            try:
                nm_path = os.path.join(workspace_path, "node_modules")
                if os.path.isdir(nm_path):
                    shutil.rmtree(nm_path, ignore_errors=True)
                    logger.info("Cleaned up node_modules: %s", nm_path)

                shutil.rmtree(workspace_path, ignore_errors=True)
                logger.info("Workspace cleaned up after pipeline: %s", workspace_path)
            except Exception as _cleanup_err:
                logger.warning("Workspace cleanup failed (non-fatal): %s", _cleanup_err)
