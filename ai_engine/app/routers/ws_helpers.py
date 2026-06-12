"""Standalone helpers for the agent WebSocket router.

Extracted from ws.py (god-module split): everything here is a
module-level function with explicit parameters — no shared state with
websocket_agent beyond what is passed in. ws.py re-imports these, so
behavior and call sites are unchanged.
"""

from __future__ import annotations

import asyncio
import os
from typing import Optional

from fastapi import WebSocket, WebSocketDisconnect

from app.config import MOCK_STEP_DELAY_SECONDS, logger
from app.events import now_iso
from app.services.sessions import AgentSession
from app.services.vcs.git import get_git_status, push_changes
from app.paths import preview_workspace_path
from app.services.local_preview import start_local_preview


def _insert_marker_preserving_lucid_project(marker: str, task: str) -> str:
    """Attach an internal marker without hiding the wizard project header."""
    marker = (marker or "").strip()
    raw = task or ""
    stripped = raw.lstrip()
    leading = raw[: len(raw) - len(stripped)]
    if marker and stripped.startswith("[LUCID_PROJECT]"):
        if "\n\n" in stripped:
            header, body = stripped.split("\n\n", 1)
            return f"{leading}{header}\n\n{marker} {body}".strip()
        return f"{leading}{stripped}\n\n{marker}".strip()
    if marker:
        return f"{marker} {raw}".strip()
    return raw.strip()


def _is_jwt_expired_error(exc: Exception) -> bool:
    """True if a Supabase / DB exception was caused by an expired JWT.

    Supabase Python client raises with PostgREST error code ``PGRST303`` and
    message containing ``JWT expired``. The python-jose library raises
    ``ExpiredSignatureError`` whose message contains ``Signature has expired``.
    Any of these means we should close the WS with code 4010 and let the
    frontend prompt re-auth instead of degrading silently.
    """
    msg = str(exc).lower()
    return (
        "jwt expired" in msg
        or "pgrst303" in msg
        or "signature has expired" in msg
    )


async def _validate_git_pat(token: str, repo_url: str) -> tuple[bool, str]:
    """Validate a git provider PAT against the relevant API (single call).

    Returns ``(valid, error_message)``. On success ``error_message`` is empty.
    A short timeout is used so an unreachable provider fails fast rather than
    blocking the pipeline. Uses the repo URL to pick the right provider so
    we do not make two API calls.
    """
    if not token:
        return True, ""   # nothing to validate — server-side fallback token may apply
    try:
        import httpx
        _url = (repo_url or "").lower()
        if "gitlab" in _url and "github.com" not in _url:
            # GitLab — hit /api/v4/user on the repo's host (or gitlab.com)
            try:
                from urllib.parse import urlparse
                _host = urlparse(repo_url).netloc or "gitlab.com"
            except Exception:
                _host = "gitlab.com"
            async with httpx.AsyncClient(timeout=10.0) as _c:
                resp = await _c.get(
                    f"https://{_host}/api/v4/user",
                    headers={"PRIVATE-TOKEN": token},
                )
            if resp.status_code == 200:
                return True, ""
            if resp.status_code in (401, 403):
                return False, "GitLab token is invalid or expired. Please reconnect GitLab in Integrations."
            return False, f"GitLab token check failed (HTTP {resp.status_code})."
        # Default to GitHub
        async with httpx.AsyncClient(timeout=10.0) as _c:
            resp = await _c.get(
                "https://api.github.com/user",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            )
        if resp.status_code == 200:
            return True, ""
        if resp.status_code in (401, 403):
            return False, "GitHub token is invalid or expired. Please reconnect GitHub in Integrations."
        return False, f"GitHub token check failed (HTTP {resp.status_code})."
    except Exception as exc:
        # Network / DNS / timeout — treat as transient, don't block the user
        logger.warning("PAT validation network error: %s — skipping", exc)
        return True, ""


def _provider_from_repo_url(repo_url: str, explicit: str = "") -> str:
    """Return DB provider enum from an explicit provider or repo URL."""
    provider = (explicit or "").strip().lower()
    if provider in ("github", "gitlab"):
        return provider.upper()
    raw = (repo_url or "").lower()
    host = urlparse(repo_url or "").netloc.lower()
    if "github.com" in host or "github.com" in raw:
        return "GITHUB"
    if "gitlab" in host or "gitlab" in raw:
        return "GITLAB"
    return ""


async def _resolve_git_token_from_integrations(
    *,
    repo_url: str,
    repo_provider: str,
    user_id: str,
    user_jwt: str | None,
) -> str:
    """Resolve the user's encrypted GitHub/GitLab PAT server-side."""
    provider = _provider_from_repo_url(repo_url, repo_provider)
    if not provider:
        return ""
    try:
        from app.services.vcs.tokens import get_integration
        integration = await get_integration(
            user_id=user_id,
            provider=provider,
            user_jwt=user_jwt,
        )
        token = (integration or {}).get("token") or ""
        if token:
            logger.info("Resolved %s git token from encrypted integrations (len=%d)", provider, len(token))
        return token
    except Exception as exc:
        logger.warning("Failed to resolve %s token from integrations: %s", provider, exc)
        return ""


async def _auto_push_if_needed(
    websocket: WebSocket,
    session: AgentSession,
    commit_message: str | None = None,
    new_branch: str | None = None,
) -> None:
    """Push changes to remote if the session has a repo and git_token."""
    logger.debug(
        "_auto_push_if_needed: repo_url=%s git_token=%s workspace_dir=%s branch=%s",
        bool(session.repo_url),
        f"len={len(session.git_token)}" if session.git_token else "missing",
        bool(session.workspace_dir),
        session.branch,
    )

    if not session.repo_url or not session.git_token or not session.workspace_dir:
        skip_reason = []
        if not session.repo_url:
            skip_reason.append("no repo_url")
        if not session.git_token:
            skip_reason.append("no git_token")
        if not session.workspace_dir:
            skip_reason.append("no workspace_dir")
        reason_str = ", ".join(skip_reason)
        logger.info("Auto-push skipped: %s", reason_str)
        try:
            await websocket.send_json({
                "type": "warning",
                "message": "Couldn't push your changes to GitHub — they're saved locally.",
            })
        except Exception:
            pass
        return

    try:
        # Check for changes first
        status = await get_git_status(session.workspace_dir)
        if not status:
            await websocket.send_json({
                "type": "agent_event",
                "event": "observation",
                "eventType": "GitStatus",
                "content": "No changes to push.",
                "timestamp": now_iso(),
            })
            return

        await websocket.send_json({
            "type": "agent_event",
            "event": "action",
            "eventType": "GitPushAction",
            "content": f"Pushing changes to {session.repo_url}...\n\nFiles changed:\n{status}",
            "timestamp": now_iso(),
        })

        result = await push_changes(
            workspace_dir=session.workspace_dir,
            token=session.git_token,
            commit_message=commit_message or f"Lucid AI: {session.task[:100]}",
            branch=session.branch,
            new_branch=new_branch,
        )

        await websocket.send_json({
            "type": "agent_event",
            "event": "observation",
            "eventType": "GitPushObservation",
            "content": (
                f"✅ Changes pushed successfully!\n\n{result.get('summary', '')}"
                if result.get("pushed")
                else f"ℹ️ {result.get('summary', 'No changes to push')}"
            ),
            "timestamp": now_iso(),
        })

        # Emit a structured push result event for the frontend PR card
        if result.get("pushed"):
            target_branch = new_branch or session.branch or "main"
            repo_url = session.repo_url or ""
            from app.services.vcs.git import (
                branch_browser_url,
                detect_git_provider,
                provider_display_name,
                review_request_url,
                strip_auth_from_url,
            )
            clean_url = strip_auth_from_url(repo_url, session.git_token)
            provider = detect_git_provider(clean_url)
            provider_label = provider_display_name(provider)
            base_branch = session.branch or "main"
            pr_url = (
                review_request_url(clean_url, target_branch, base_branch, provider)
                if new_branch
                else ""
            )

            await websocket.send_json({
                "type": "git_push_result",
                "pushed": True,
                "provider": provider,
                "providerLabel": provider_label,
                "branch": target_branch,
                "baseBranch": base_branch,
                "repoUrl": clean_url,
                "branchUrl": branch_browser_url(clean_url, target_branch, provider),
                "prUrl": pr_url,
                "summary": result.get("summary", ""),
                "newBranch": bool(new_branch),
                "timestamp": now_iso(),
            })

        # If we pushed to a new branch, update the session to track it
        if result.get("pushed") and new_branch:
            logger.info("Updating session %s branch to %s", session.session_id, new_branch)
            session.branch = new_branch

    except Exception as exc:
        logger.error("Git push failed for session %s: %s", session.session_id, exc)
        try:
            await websocket.send_json({
                "type": "agent_event",
                "event": "error",
                "eventType": "GitPushError",
                "content": f"Failed to push changes: {exc}",
                "timestamp": now_iso(),
            })
        except Exception:
            pass


# ── Mock agent loop ──────────────────────────────────────────

_MOCK_STEPS: list[dict] = [
    {
        "type": "agent_event", "event": "action",
        "eventType": "ThinkAction",
        "content": "Analyzing task…",
        "thought": "Let me break this down into steps…",
    },
    {
        "type": "agent_event", "event": "action",
        "eventType": "CmdRunAction",
        "content": "mkdir -p /workspace && cd /workspace",
        "command": "mkdir -p /workspace && cd /workspace",
    },
    {
        "type": "agent_event", "event": "observation",
        "eventType": "CmdOutputObservation",
        "content": "Directory created successfully.",
        "exitCode": 0,
    },
    {
        "type": "agent_event", "event": "action",
        "eventType": "FileWriteAction",
        "content": 'print("Hello, World!")',
        "path": "/workspace/hello.py",
    },
    {
        "type": "agent_event", "event": "observation",
        "eventType": "FileWriteObservation",
        "content": "File written: /workspace/hello.py",
        "path": "/workspace/hello.py",
    },
    {
        "type": "agent_event", "event": "action",
        "eventType": "CmdRunAction",
        "content": "python /workspace/hello.py",
        "command": "python /workspace/hello.py",
    },
    {
        "type": "agent_event", "event": "observation",
        "eventType": "CmdOutputObservation",
        "content": "Hello, World!",
        "exitCode": 0,
    },
    {
        "type": "status", "status": "completed",
        "message": (
            "[MOCK] Task completed. This is a simulated response. "
            "Install the OpenHands SDK packages to enable real "
            "Docker-sandboxed agent execution."
        ),
    },
]


async def _run_mock_loop(websocket: WebSocket, session: AgentSession) -> None:
    """Simulate agent behaviour when the SDK is not installed."""
    for step in _MOCK_STEPS:
        # Copy before mutating — _MOCK_STEPS is module-level; concurrent
        # WebSocket connections would overwrite each other's timestamp/content.
        step_copy = dict(step)
        step_copy["timestamp"] = now_iso()
        if step_copy.get("eventType") == "ThinkAction":
            step_copy["content"] = f'Analyzing task: "{session.task}"'
        await websocket.send_json(step_copy)
        await asyncio.sleep(MOCK_STEP_DELAY_SECONDS)

    try:
        while True:
            data = await websocket.receive_json()
            content = data.get("content", "")
            if content:
                await websocket.send_json({
                    "type": "agent_event",
                    "event": "observation",
                    "eventType": "MockResponse",
                    "content": (
                        f'[MOCK] Received: "{content}"\n'
                        "The agent would process this in production mode."
                    ),
                    "timestamp": now_iso(),
                })
    except (WebSocketDisconnect, Exception):
        pass


async def background_preview(
    *,
    websocket,
    session,
    user_id: str,
    user_jwt: str | None,
    _prev_session_data: dict | None,
    _bg_conv_id: str,
    _platform_repo: str | None,
    _user_repo: str | None,
    _repo_to_clone: str | None,
    user_package_manager: str = "",
) -> None:
    """Clone/reuse a workspace and boot the dev server for a returning project.

    Extracted verbatim from the websocket_agent closure (god-module
    split). Parameter names intentionally keep the closure's original
    underscore-prefixed names so the 270-line body needed zero edits —
    rename only with a test harness around this path.
    """
    try:
        import subprocess as _sp
        from app.services.pipeline.package_manager import (
            detect_package_manager as _detect_pm_bg,
            _pm_install_cmd as _pm_install_bg,
            _pm_env as _pm_env_bg,
        )
        from app.services.local_preview import get_active_preview_url

        # ── Stable path — computed first so the early-return path
        # can also send the file tree and update workspace_dir. ──
        _tmp = preview_workspace_path(_bg_conv_id)

        # ── Re-use an already-running dev server for this session ──
        _existing_url = get_active_preview_url(conversation_id=_bg_conv_id)
        if _existing_url:
            logger.info("bg_preview: reusing active server at %s for %s", _existing_url, _bg_conv_id)
            # Update session workspace_dir so /api/files/read works
            if session is not None:
                session.workspace_dir = _tmp
            # Send file tree so the Code tab is populated
            if os.path.isdir(_tmp):
                try:
                    from app.services.pipeline import _send_file_tree
                    await _send_file_tree(websocket, _tmp)
                    logger.info("bg_preview: sent file_tree on server-reuse (%s)", _tmp)
                except Exception as _ft_reuse_err:
                    logger.debug("bg_preview: file_tree send on reuse failed (ok): %s", _ft_reuse_err)
            await websocket.send_json({"type": "preview_ready",
                                       "preview_url": _existing_url,
                                       "message": "Preview ready"})
            return

        # ── Use a STABLE path per conversation ──────────────────
        # Same path across reconnects — avoids re-clone + re-install
        # every time the user returns to the workspace page.
        # (_tmp already computed above)
        # Cache check must mirror local_preview's strict criterion
        # (look for the framework binary), not "any files in node_modules".
        # A loose check false-positives on PARTIAL installs left over
        # from a prior timeout — bg_preview then says "cache hit", skips
        # install, hands off to local_preview which independently re-checks,
        # decides node_modules is incomplete, and runs ANOTHER install
        # (now during dev-server startup, when the user is waiting).
        _nm = os.path.join(_tmp, "node_modules")
        _next_bin = os.path.join(_nm, ".bin", "next")
        _vite_bin = os.path.join(_nm, ".bin", "vite")
        _already_installed = os.path.isdir(_nm) and (
            os.path.isfile(_next_bin) or os.path.isfile(_vite_bin)
        )

        if _already_installed:
            # node_modules already present — skip clone + install entirely.
            # Just start (or reuse) the dev server.
            logger.info("bg_preview: node_modules cache hit for %s — skipping install", _bg_conv_id)
            await websocket.send_json({"type": "preview_status",
                                       "status": "starting",
                                       "message": "Starting preview (cached)…"})
        elif not _repo_to_clone and os.path.isdir(_tmp) and os.path.isfile(os.path.join(_tmp, "package.json")):
            # Landing-flow / locally-generated path: workspace files
            # already exist on disk (preview_ws is volume-mounted) but
            # node_modules isn't installed yet AND there's no GitHub
            # repo to clone from. Skip the clone block entirely and
            # fall through to the install + dev-server-start logic
            # below — overwriting the generated source via clone
            # would destroy the user's project.
            logger.info(
                "bg_preview: no repo to clone but workspace files exist for %s — going straight to install",
                _bg_conv_id,
            )
            await websocket.send_json({"type": "preview_status",
                                       "status": "preparing",
                                       "message": "Preparing preview…"})
        else:
            # Fresh workspace — need to clone and install.
            await websocket.send_json({"type": "preview_status",
                                       "status": "cloning",
                                       "message": "Cloning repository for preview…"})

            # Preserve node_modules across the re-clone so the
            # follow-up `pnpm install --prefer-offline` validates
            # an existing tree (~30s) instead of rebuilding it from
            # scratch (~8 min). Skipped if no node_modules existed.
            _preserved_nm: Optional[str] = None
            import time as _time, threading as _threading, shutil as _shutil
            if os.path.isdir(_nm):
                try:
                    _preserved_nm = f"{_tmp}.nm-{int(_time.time() * 1000)}"
                    os.rename(_nm, _preserved_nm)
                    logger.info("bg_preview: preserved node_modules → %s", _preserved_nm)
                except Exception as _pres_err:
                    logger.warning("bg_preview: could not preserve node_modules: %s", _pres_err)
                    _preserved_nm = None

            # Clean stale partial-clone directory so git doesn't
            # refuse to clone into a non-empty target. Use atomic
            # rename + async rmtree — `shutil.rmtree` on a half-
            # populated node_modules can take a minute and, if
            # interrupted, leaves a dir that breaks the next clone
            # ("destination path already exists and is not empty").
            # The rename is O(1); cleanup happens in a daemon thread.
            # Orphan `.trash-*` dirs are swept on engine startup.
            if os.path.isdir(_tmp) and os.listdir(_tmp):
                try:
                    _trash = f"{_tmp}.trash-{int(_time.time() * 1000)}"
                    os.rename(_tmp, _trash)
                    _threading.Thread(
                        target=lambda p=_trash: _shutil.rmtree(p, ignore_errors=True),
                        daemon=True,
                    ).start()
                    logger.info("bg_preview: moved stale dir %s → %s (async cleanup)", _tmp, _trash)
                except Exception as _rm_err:
                    logger.warning("bg_preview: could not move stale dir %s: %s", _tmp, _rm_err)

            os.makedirs(_tmp, exist_ok=True)
            # Token priority matters:
            # - Platform repos (wizard-generated): use
            #   PLATFORM_GITHUB_TOKEN — it owns those repos.
            # - User-imported repos: the user's token is the
            #   only one with access; PLATFORM_GITHUB_TOKEN
            #   would 404 against a private user repo.
            _user_repo_only = bool(_user_repo) and not bool(_platform_repo)
            if _user_repo_only:
                _gh_token = (session.git_token if session else "") or await _resolve_git_token_from_integrations(
                    repo_url=_user_repo or "",
                    repo_provider=(_prev_session_data or {}).get("user_repo_provider") or "",
                    user_id=user_id,
                    user_jwt=user_jwt,
                )
            else:
                _gh_token = (
                    os.environ.get("PLATFORM_GITHUB_TOKEN", "")
                    or (session.git_token if session else "")
                    or ""
                )
            _auth_url = (
                _repo_to_clone.replace("https://", f"https://x-access-token:{_gh_token}@")
                if _gh_token else _repo_to_clone
            )

            try:
                _clone_r = await asyncio.wait_for(
                    asyncio.to_thread(
                        _sp.run,
                        ["git", "clone", "--depth=1", _auth_url, _tmp],
                        capture_output=True,
                    ),
                    timeout=90,
                )
            except (asyncio.TimeoutError, TimeoutError):
                logger.warning("bg_preview: clone timed out (90s) for %s", _repo_to_clone)
                await websocket.send_json({"type": "preview_error",
                                           "error_stage": "clone",
                                           "message": "Clone timed out — repository may be too large or the network is slow."})
                return
            except Exception as _clone_exc:
                logger.warning("bg_preview: clone exception for %s: %s", _repo_to_clone, _clone_exc)
                await websocket.send_json({"type": "preview_error",
                                           "error_stage": "clone",
                                           "message": f"Clone failed: {str(_clone_exc)[:160]}"})
                return

            if _clone_r.returncode != 0:
                _clone_err = (_clone_r.stderr or b"").decode()[:200]
                logger.warning("bg_preview: clone failed for %s: %s", _repo_to_clone, _clone_err)
                # Preserved node_modules is now orphaned — schedule
                # async cleanup so it doesn't leak.
                if _preserved_nm and os.path.isdir(_preserved_nm):
                    _threading.Thread(
                        target=lambda p=_preserved_nm: _shutil.rmtree(p, ignore_errors=True),
                        daemon=True,
                    ).start()
                await websocket.send_json({"type": "preview_error",
                                           "error_stage": "clone",
                                           "message": f"Could not clone repository: {_clone_err or 'check token/URL'}"})
                return

            # Restore preserved node_modules into the freshly
            # cloned workspace. The follow-up `pnpm install
            # --prefer-offline` will validate the tree against
            # the new lockfile and patch any drift — much faster
            # than installing from scratch.
            if _preserved_nm and os.path.isdir(_preserved_nm):
                try:
                    if not os.path.exists(_nm):
                        os.rename(_preserved_nm, _nm)
                        logger.info("bg_preview: restored node_modules from %s", _preserved_nm)
                    else:
                        # Shouldn't happen (clone wouldn't succeed
                        # into a dir with node_modules), but be safe.
                        _threading.Thread(
                            target=lambda p=_preserved_nm: _shutil.rmtree(p, ignore_errors=True),
                            daemon=True,
                        ).start()
                except Exception as _rest_err:
                    logger.warning("bg_preview: could not restore node_modules: %s", _rest_err)

        # Point session workspace to the preview dir so the
        # /api/files/read endpoint can serve file content when
        # a user clicks a file in the Code tab.
        if session is not None:
            session.workspace_dir = _tmp

        # Send file tree so the Code tab is populated.
        # Runs on BOTH cache-hit and fresh-clone paths.
        try:
            from app.services.pipeline import _send_file_tree
            await _send_file_tree(websocket, _tmp)
            logger.info("bg_preview: sent file_tree (%s)", _tmp)
        except Exception as _ft_err:
            logger.debug("bg_preview: file_tree send failed (ok): %s", _ft_err)

        # Install dependencies (skipped if node_modules already exists)
        _pkg_json = os.path.join(_tmp, "package.json")
        if os.path.exists(_pkg_json):
            _bg_pm = _detect_pm_bg(_tmp, user_package_manager)

            if not _already_installed:
                await websocket.send_json({"type": "preview_status",
                                           "status": "installing",
                                           "message": "Installing dependencies…"})
                logger.info("bg_preview: installing deps with %s for %s", _bg_pm, _bg_conv_id)
                try:
                    # Use a shared pnpm content-store so packages are
                    # deduplicated across all preview workspaces.
                    _install_env = {
                        **_pm_env_bg(_bg_pm),
                        "PNPM_HOME": "/tmp/pnpm_global",
                        "npm_config_cache": "/tmp/npm_cache",
                    }
                    _install_cmd = _pm_install_bg(_bg_pm)
                    # Append --store-dir + --prefer-offline for pnpm so packages
                    # are cached globally and repeat installs hit the cache.
                    if _bg_pm == "pnpm":
                        _install_cmd = _install_cmd + [
                            "--store-dir", "/tmp/pnpm_store",
                            "--prefer-offline",
                        ]
                    elif _bg_pm == "npm":
                        _install_cmd = _install_cmd + ["--prefer-offline"]
                    # 10-min cap (was 300s). Cold installs of Next.js + Tailwind +
                    # shadcn routinely take 4-6 min on a fresh workspace.
                    _bg_install = await asyncio.to_thread(
                        _sp.run,
                        _install_cmd,
                        cwd=_tmp, capture_output=True, text=True,
                        timeout=600, env=_install_env,
                    )
                    if _bg_install.returncode != 0:
                        logger.warning("bg_preview: %s install failed: %s", _bg_pm, (_bg_install.stderr or "")[:200])
                    else:
                        logger.info("bg_preview: deps installed (%s)", _bg_pm)
                except Exception as _bi_err:
                    logger.warning("bg_preview: install error (non-fatal): %s", _bi_err)

            # Start the real dev server
            await start_local_preview(
                workspace_path=_tmp,
                conversation_id=_bg_conv_id,
                websocket=websocket,
                package_manager=_bg_pm,
            )
        else:
            logger.info("bg_preview: no package.json — skipping dev server")
            await websocket.send_json({"type": "preview_error",
                                       "error_stage": "no_package_json",
                                       "message": "No package.json found — preview not available for this project."})
    except Exception as _bg_err:
        logger.warning("bg_preview: failed (non-fatal): %s", _bg_err)
        try:
            await websocket.send_json({"type": "preview_error",
                                       "error_stage": "start",
                                       "message": "Preview setup failed — click Restart Preview to retry."})
        except Exception:
            pass
