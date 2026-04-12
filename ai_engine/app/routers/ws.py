"""WebSocket endpoint for real-time agent communication."""

from __future__ import annotations

import asyncio
import os
import uuid
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException

from app.auth import AuthenticatedUser, authenticate_websocket, authenticate_from_handshake
from app.config import (
    logger,
    settings,
    WS_INIT_TIMEOUT_SECONDS,
    MOCK_STEP_DELAY_SECONDS,
    CONVERSATION_TIMEOUT_SECONDS,
)
from app import sdk
from app.events import now_iso, stream_events_to_ws
from app.services.chat import ChatService
from app.services.sessions import (
    AgentSession,
    create_session,
    destroy_session,
    store as session_store,
)
from app.services.vcs.git import push_changes, get_git_status
from app.services.pipeline import run_pipeline, PLATFORM_GITHUB_TOKEN
from app.services.event_bus import WebSocketProxy
from app.services.workspace_manager import workspace_manager
from app.services.dev_server import launch_dev_preview
from app.supabase_client import db_client
from app.workspace_states import WorkspaceState, transition as ws_transition
from app.services.workspace_resolver import resolve_workspace_path, ResolvePath
from app.services.agent_orchestrator import (
    TaskResult,
    agent_orchestrator,
    build_pipeline_user,
    build_enriched_task,
    build_conversation_context,
)

router = APIRouter()


@router.websocket("/api/v1/ws")
async def websocket_agent(websocket: WebSocket):
    """Real-time agent communication channel.

    Protocol
    --------
    1. Client sends initial config ``{ "task": "...", ... }``
    2. Server creates a session and streams agent events back
    3. Client may send follow-ups ``{ "type": "message", "content": "..." }``
    4. On disconnect the workspace is cleaned up
    """
    await websocket.accept()
    logger.info("WebSocket connection accepted")

    # Authenticate from query param (if present)
    ws_user: Optional[AuthenticatedUser] = await authenticate_websocket(websocket)
    if ws_user is None and websocket.client_state.name == "DISCONNECTED":
        return  # closed by authenticate_websocket due to invalid token

    session: Optional[AgentSession] = None
    streaming_task: Optional[asyncio.Task] = None
    pipeline_task: Optional[asyncio.Task] = None
    chat_session_id: Optional[str] = None
    reconnect_chat_session_id: Optional[str] = None
    conversation_id: str = str(uuid.uuid4())  # unique per WS connection

    try:
        # ── 1. Receive initial config ────────────────────
        raw = await asyncio.wait_for(
            websocket.receive_json(), timeout=WS_INIT_TIMEOUT_SECONDS,
        )

        # ── Validate handshake message structure ──────────
        if not isinstance(raw, dict):
            logger.warning("WebSocket rejected — handshake is not a JSON object")
            await websocket.send_json({
                "type": "error",
                "message": "Invalid handshake: expected a JSON object.",
            })
            await websocket.close(code=4400, reason="Invalid handshake")
            return

        # String-type fields that must not be non-string values if present
        _str_fields = ("task", "repoUrl", "branch", "gitToken", "projectId",
                       "modelProvider", "apiKey")
        for _field in _str_fields:
            if _field in raw and not isinstance(raw[_field], (str, type(None))):
                logger.warning("WebSocket rejected — field %s has wrong type", _field)
                await websocket.send_json({
                    "type": "error",
                    "message": f"Invalid handshake: field '{_field}' must be a string.",
                })
                await websocket.close(code=4400, reason="Invalid handshake")
                return

        # If not authenticated from query param, try handshake token
        if ws_user is None:
            ws_user = authenticate_from_handshake(raw)

        # ── Reject unauthenticated connections ────────────
        if ws_user is None:
            logger.warning("WebSocket rejected — no valid authentication")
            await websocket.send_json({
                "type": "error",
                "message": "Authentication required. Provide a valid JWT token.",
            })
            await websocket.close(code=4010, reason="Authentication required")
            return

        task = raw.get("task", "")  # Task is now OPTIONAL in handshake

        await websocket.send_json({
            "type": "status",
            "status": "initializing",
            "message": "Setting up agent workspace...",
        })

        user_id = ws_user.user_id
        user_jwt = ws_user.raw_jwt
        project_id = raw.get("projectId", "")
        explicit_stop = False  # track if user explicitly stopped

        # ── 1.5 Resolve LLM Settings (Handshake > Supabase > Default) ──
        model_provider = raw.get("modelProvider") or raw.get("model_provider")
        api_key = raw.get("apiKey") or raw.get("api_key")
        user_package_manager = "npm"  # default, may be overridden from user_settings

        # Always fetch user_settings — needed for package_manager even if API key is provided
        try:
            async with db_client(user_jwt) as client:
                q = client.table("user_settings").select("*").eq("user_id", user_id).maybe_single()
                res = await q.execute()
                if res.data:
                    logger.info("Applying saved settings for user %s", user_id)
                    if not model_provider:
                        model_provider = res.data.get("llm_model")
                    if not api_key:
                        enc = res.data.get("api_key_enc")
                        iv = res.data.get("api_key_iv")
                        if enc and iv:
                            from app.utils.crypto import decrypt_api_key
                            try:
                                api_key = decrypt_api_key(enc, iv)
                            except Exception as dec_err:
                                logger.error("Failed to decrypt API key: %s", dec_err)
                    # Always read package manager preference
                    user_package_manager = res.data.get("package_manager") or "npm"
        except Exception as db_err:
            logger.warning("Failed to fetch user settings from Supabase: %s", db_err)

        if not model_provider:
            model_provider = settings.DEFAULT_PROVIDER

        # Fallback: if no API key from handshake or user settings, use server's .env key
        if not api_key:
            api_key = os.environ.get("ANTHROPIC_API_KEY") or settings.ANTHROPIC_API_KEY or ""
            if api_key:
                logger.info("Using server fallback ANTHROPIC_API_KEY for user %s", user_id)
        
        logger.info("[%s] Using model: %s, api_key prefix: %s (len=%d)", project_id or "new-session", model_provider, str(api_key or "")[:15], len(str(api_key or "")))

        # Resolve Gemini API key for pre-exploration
        gemini_api_key = os.environ.get("GOOGLE_API_KEY") or settings.GOOGLE_API_KEY or ""

        # ── 2. Try to reconnect to existing session ───────
        existing = await session_store.find_by_user_and_project(user_id, project_id) if project_id else None

        if existing:
            session = existing
            session.touch()
            await session_store.touch(session.session_id)

            # ── Refresh session with latest handshake data ─────
            # Always update from the fresh handshake to fix stale sessions
            # that may have empty or wrong repo_url/git_token/branch.
            fresh_repo_url = raw.get("repoUrl", "")
            fresh_git_token = raw.get("gitToken", "")
            fresh_branch = raw.get("branch", "")

            if fresh_repo_url:
                session.repo_url = fresh_repo_url
                logger.info("Session repo_url refreshed → %s", fresh_repo_url[:60])
            if fresh_git_token:
                session.git_token = fresh_git_token
                logger.info("Session git_token refreshed (len=%d)", len(fresh_git_token))
            if fresh_branch:
                session.branch = fresh_branch
                logger.info("Session branch refreshed → %s", fresh_branch)

            logger.info("Reconnecting to existing session %s for project %s", session.session_id, project_id)

            # ── Replay missed events then reattach proxy ───────────
            # Events published while the client was disconnected are stored
            # in Redis Stream. Send them now so the UI catches up, then
            # reattach so future pipeline events flow to the new WS.
            # The client may send lastEventId from a prior cursor message so
            # we only replay events the client hasn't seen yet.
            last_event_id = raw.get("lastEventId") or "0-0"
            if session.ws_proxy is not None:
                replayed = await session.ws_proxy.replay(websocket, last_id=last_event_id)
                session.ws_proxy.attach(websocket)
                if replayed:
                    logger.info(
                        "Replayed %d buffered events for session %s",
                        replayed, session.session_id,
                    )

            # ── Look up chat session early so we can replay history ────────────
            reconnect_chat_session_id = None
            try:
                async with db_client(ws_user.raw_jwt) as client:
                    result = await (
                        client.table("chat_sessions")
                        .select("id")
                        .eq("user_id", user_id)
                        .eq("agent_session_id", session.session_id)
                        .order("created_at", desc=True)
                        .limit(1)
                        .execute()
                    )
                if result.data:
                    reconnect_chat_session_id = result.data[0]["id"]
                elif project_id:
                    async with db_client(ws_user.raw_jwt) as client:
                        result = await (
                            client.table("chat_sessions")
                            .select("id")
                            .eq("user_id", user_id)
                            .eq("project_id", project_id)
                            .order("created_at", desc=True)
                            .limit(1)
                            .execute()
                        )
                    if result.data:
                        reconnect_chat_session_id = result.data[0]["id"]
            except Exception as exc:
                logger.warning("Failed to look up chat session on reconnect: %s", exc)

            await ws_transition(
                session, websocket, WorkspaceState.READY,
                "Reconnected to existing workspace.",
                reconnected=True,
            )
            await websocket.send_json({
                "type": "status",
                "status": "ready",
                "sessionId": session.session_id,
                "reconnected": True,
                "message": "Reconnected to existing workspace.",
            })

            # ── Replay recent chat history so client can restore chat panel ──
            if reconnect_chat_session_id:
                try:
                    async with db_client(ws_user.raw_jwt) as client:
                        hist = await (
                            client.table("chat_messages")
                            .select("id, role, content, created_at")
                            .eq("session_id", reconnect_chat_session_id)
                            .order("created_at", desc=False)
                            .limit(40)
                            .execute()
                        )
                    if hist.data:
                        await websocket.send_json({
                            "type": "chat_history",
                            "messages": hist.data,
                        })
                        logger.info(
                            "Sent %d chat_history messages on reconnect for session %s",
                            len(hist.data), session.session_id,
                        )
                    # Store for later use
                    chat_session_id = reconnect_chat_session_id
                except Exception as hist_err:
                    logger.warning("Failed to send chat_history on reconnect: %s", hist_err)

            # ── Send file tree immediately on reconnect ──────────
            # Without this, the Code tab stays empty until the user sends
            # a follow-up task. The file tree must be sent every time.
            if session.workspace_dir and os.path.isdir(session.workspace_dir):
                try:
                    from app.services.pipeline import _send_file_tree
                    await _send_file_tree(websocket, session.workspace_dir)
                    logger.info("Sent file_tree on reconnect for session %s", session.session_id)
                except Exception as ft_err:
                    logger.warning("Failed to send file_tree on reconnect: %s", ft_err)
        else:
            # ── Create new session (no clone — workspace_manager handles it) ──
            try:
                # Resolve git_token — frontend may send it, or extract from JWT
                git_token = raw.get("gitToken", "")
                if not git_token and user_jwt:
                    # Extract git token from JWT user_metadata as fallback
                    try:
                        import jwt as pyjwt
                        decoded = pyjwt.decode(user_jwt, options={"verify_signature": False})
                        user_meta = decoded.get("user_metadata", {})
                        repo_url_raw = raw.get("repoUrl", "")
                        if "github" in repo_url_raw.lower():
                            gh = user_meta.get("github_integration", {})
                            git_token = gh.get("token", "")
                        elif "gitlab" in repo_url_raw.lower():
                            gl = user_meta.get("gitlab_integration", {})
                            git_token = gl.get("token", "")
                        if git_token:
                            logger.info("git_token extracted from JWT (len=%d)", len(git_token))
                    except Exception as jwt_err:
                        logger.warning("Failed to extract git_token from JWT: %s", jwt_err)

                session = await create_session(
                    task=task or "Workspace initialization",
                    user_id=user_id,
                    repo_url=raw.get("repoUrl", ""),
                    git_token=git_token,
                    branch=raw.get("branch", ""),
                    git_user_name=raw.get("gitUserName", ""),
                    git_user_email=raw.get("gitUserEmail", ""),
                    model_provider=model_provider,
                    api_key=api_key or "",
                    project_id=project_id,
                )
                # Create the event proxy — all pipeline events flow through
                # this so they survive client disconnects (stored in Redis).
                session.ws_proxy = WebSocketProxy(session.session_id, websocket)
                await ws_transition(
                    session, websocket, WorkspaceState.RESOLVING,
                    "Initializing workspace...",
                )
            except HTTPException as rate_err:
                # Rate limit or other HTTP error from create_session
                logger.warning("Session creation rejected: %s", rate_err.detail)
                await websocket.send_json({
                    "type": "error",
                    "message": rate_err.detail,
                })
                await websocket.close(code=4029, reason="Rate limited")
                return

        user_jwt = ws_user.raw_jwt

        # ── Persist chat session to DB (new sessions only) ──
        # On reconnect we skip this — a chat session already exists for this
        # agent session and creating another would leave orphan records.
        #
        # For NEW sessions on an existing project (server restarted, session
        # timed out): look for a previous chat_session so we can:
        #   1. Replay chat history to restore the chat panel
        #   2. Detect if generation already completed so we skip the pipeline
        _prev_session_data: dict | None = None  # {platform_repo_url, generation_complete, messages}

        if not existing and project_id:
            try:
                async with db_client(user_jwt) as client:
                    prev_s = await (
                        client.table("chat_sessions")
                        .select("id, platform_repo_url, generation_complete")
                        .eq("user_id", user_id)
                        .eq("project_id", project_id)
                        .order("created_at", desc=True)
                        .limit(1)
                        .execute()
                    )
                if prev_s.data:
                    prev_row = prev_s.data[0]
                    prev_sid = prev_row["id"]
                    # Load messages from that session
                    async with db_client(user_jwt) as client:
                        prev_msgs = await (
                            client.table("chat_messages")
                            .select("id, role, content, created_at, event_type")
                            .eq("session_id", prev_sid)
                            .order("created_at", desc=False)
                            .limit(60)
                            .execute()
                        )
                    _prev_session_data = {
                        "session_id":          prev_sid,
                        "platform_repo_url":   prev_row.get("platform_repo_url"),
                        "generation_complete": prev_row.get("generation_complete", False),
                        "messages":            prev_msgs.data or [],
                    }
                    logger.info(
                        "Found previous session %s for project %s "
                        "(complete=%s, msgs=%d, repo=%s)",
                        prev_sid, project_id,
                        _prev_session_data["generation_complete"],
                        len(_prev_session_data["messages"]),
                        bool(_prev_session_data["platform_repo_url"]),
                    )
            except Exception as _prev_err:
                logger.warning("Failed to load previous session for project %s: %s", project_id, _prev_err)

        if not existing:
            try:
                # Reuse previous session if one exists for this project —
                # this keeps all history under one chat_session record.
                if _prev_session_data:
                    chat_session_id = _prev_session_data["session_id"]
                    logger.info("Reusing existing chat session %s for project %s", chat_session_id, project_id)
                    # Update agent_session_id so future reconnects find it
                    try:
                        async with db_client(user_jwt) as client:
                            await (
                                client.table("chat_sessions")
                                .update({"agent_session_id": session.session_id})
                                .eq("id", chat_session_id)
                                .execute()
                            )
                    except Exception:
                        pass
                else:
                    chat_sess = await ChatService.create_session(
                        user_id=user_id,
                        user_jwt=user_jwt,
                        agent_session_id=session.session_id,
                        project_id=raw.get("projectId"),
                        title=task[:255] if task else "New workspace session",
                        model_provider=model_provider,
                    )
                    chat_session_id = chat_sess["id"]
                    logger.info("Chat session %s created for user %s", chat_session_id, user_id)
            except Exception as exc:
                logger.warning("Failed to create/reuse chat session in DB: %s", exc)

            # ── Replay history for returning users (server-restart safe) ──
            if _prev_session_data and _prev_session_data["messages"]:
                try:
                    await websocket.send_json({
                        "type": "chat_history",
                        "messages": _prev_session_data["messages"],
                    })
                    logger.info(
                        "Sent %d historical messages for project %s (new session, prev data found)",
                        len(_prev_session_data["messages"]), project_id,
                    )
                except Exception as _hist_err:
                    logger.warning("Failed to send chat_history (new session): %s", _hist_err)

            # ── Skip pipeline if project was already fully generated ──
            # Conditions to skip:
            #   • platform_repo_url is set  → project was published to GitHub
            #   • generation_complete flag  → set by project_generator when done
            #   • wizard re-entry: [LUCID_PROJECT] header + previous messages exist
            #     → generation was attempted before (even if it failed mid-way);
            #     never restart research from scratch on re-entry.
            _wizard_reentry = (
                task
                and "[LUCID_PROJECT]" in task[:500]
                and _prev_session_data
                and _prev_session_data.get("messages")
            )
            if _prev_session_data and (
                _prev_session_data.get("platform_repo_url")
                or _prev_session_data.get("generation_complete")
                or _wizard_reentry
            ):
                _skip_reason = (
                    "published" if _prev_session_data.get("platform_repo_url")
                    else "generation_complete flag" if _prev_session_data.get("generation_complete")
                    else "wizard re-entry with previous messages"
                )
                logger.info(
                    "Skipping pipeline for project %s — already generated (%s). "
                    "Clearing task to wait for user input.",
                    project_id, _skip_reason,
                )
                task = ""   # clear task → pipeline won't auto-run
                await websocket.send_json({
                    "type": "status",
                    "status": "ready",
                    "message": "Project loaded. Ask me to make changes.",
                })

        else:
            # On reconnect — chat_session_id was already looked up above (early lookup)
            # and stored in chat_session_id via reconnect_chat_session_id. Nothing to do.
            if reconnect_chat_session_id:
                logger.info("Using chat session %s (found during early reconnect lookup)", reconnect_chat_session_id)

        # ── 3. Pipeline handles agent execution ──────────
        # NOTE: The old mock gate (sdk.OPENHANDS_AVAILABLE) is removed.
        # run_pipeline() has its own subprocess fallbacks for clone/push
        # and does NOT require the OpenHands SDK.
        if not sdk.OPENHANDS_AVAILABLE:
            logger.info("OpenHands SDK not installed — pipeline will use subprocess fallbacks")

        # ── 4. Session ready — workspace initialized ─────────
        # Only send the "ready" message for NEW sessions.
        # For reconnects we already sent it at step 2 above (reconnected=True).
        if not existing:
            # ── Resolve workspace path (A / B / C) ───────────────
            # Determines what to clone (or not), emits structured progress
            # events so the frontend always shows meaningful loading text.
            # Also patches session.repo_url in-place when a platform repo
            # is found in the DB (returning wizard projects).
            resolve_result = await resolve_workspace_path(
                task=task,
                session=session,
                websocket=websocket,
                project_id=project_id,
                user_jwt=user_jwt,
            )

            # ── If the wizard header was present but resolver chose Path B ──
            # This means the project was already created successfully in a
            # previous run. Strip the task so the pipeline does NOT auto-run
            # again — the user is just re-opening the workspace.
            if resolve_result.path == ResolvePath.EXISTING_REPO and "[LUCID_PROJECT]" in task:
                logger.info(
                    "Wizard task present but project already exists (Path B) — "
                    "clearing task to prevent duplicate generation for project %s",
                    project_id,
                )
                task = ""

            # ── Phase 3: Clone → validate → install ──────────────
            # Path A (new_project):  workspace is intentionally empty — AI generates code
            # Path B (existing_repo): shallow clone (30s) → validate → npm install (60s)
            # Path C (conversation):  no repo, skip clone entirely
            #
            # clone_fatal: set to True when clone fails hard so we skip READY
            # and let the frontend show an error recovery card.
            clone_fatal = False

            if resolve_result.path == ResolvePath.NEW_PROJECT:
                # Workspace starts empty — that is correct, not an error.
                # The AI will generate all files when the task runs.
                await websocket.send_json({
                    "type": "workspace_empty",
                    "message": "Waiting for AI to generate code...",
                    "path": "new_project",
                })

            elif resolve_result.path == ResolvePath.EXISTING_REPO and session.repo_url and project_id:
                await ws_transition(
                    session, websocket, WorkspaceState.CLONING,
                    "Cloning repository...",
                )

                # ── Heartbeat: progress pings every 8s so the UI never freezes ──
                _clone_stop = asyncio.Event()

                async def _clone_heartbeat():
                    _msgs = [
                        "Fetching repository objects…",
                        "Analysing file structure…",
                        "Setting up workspace environment…",
                        "Almost there…",
                    ]
                    _i = 0
                    while not _clone_stop.is_set():
                        await asyncio.sleep(8)
                        if _clone_stop.is_set():
                            break
                        try:
                            await websocket.send_json({
                                "type": "status",
                                "status": "initializing",
                                "message": _msgs[_i % len(_msgs)],
                            })
                        except Exception:
                            break
                        _i += 1

                heartbeat_task = asyncio.create_task(_clone_heartbeat())
                pre_workspace = None
                try:
                    # Hard 30-second cap on clone — shallow clone should always
                    # fit within this window. If it doesn't, something is wrong.
                    async with asyncio.timeout(30):
                        pre_validated = {
                            "repo_url": session.repo_url,
                            "branch": session.branch or "main",
                            "git_token": session.git_token or "",
                        }
                        pre_workspace = await workspace_manager.get_or_create_workspace(
                            conversation_id=project_id,
                            validated=pre_validated,
                            websocket=websocket,
                        )
                except (asyncio.TimeoutError, TimeoutError):
                    sanitised_url = resolve_result.repo_display or "repository"
                    logger.warning("Clone timed out after 30s for project %s", project_id)
                    clone_fatal = True
                    await ws_transition(
                        session, websocket, WorkspaceState.ERROR,
                        f"Clone timed out — could not fetch {sanitised_url} within 30 seconds.",
                        error_stage="clone",
                    )
                except Exception as clone_err:
                    sanitised = str(clone_err)
                    if session.git_token:
                        sanitised = sanitised.replace(session.git_token, "***")
                    logger.warning("Pre-clone failed for project %s: %s", project_id, sanitised)
                    clone_fatal = True
                    await ws_transition(
                        session, websocket, WorkspaceState.ERROR,
                        f"Could not clone repository: {sanitised[:120]}",
                        error_stage="clone",
                    )
                finally:
                    _clone_stop.set()
                    await asyncio.gather(heartbeat_task, return_exceptions=True)

                if not clone_fatal and pre_workspace:
                    session.workspace_dir = pre_workspace

                    # ── Validate: package.json must exist for npm install ──────
                    import subprocess as _subprocess
                    pkg_json = os.path.join(pre_workspace, "package.json")
                    if not os.path.exists(pkg_json):
                        logger.info(
                            "No package.json in cloned repo — skipping npm install"
                        )
                    else:
                        # ── INSTALLING: npm install with 60-second hard cap ────
                        await ws_transition(
                            session, websocket, WorkspaceState.INSTALLING,
                            "Installing dependencies...",
                        )
                        try:
                            async with asyncio.timeout(60):
                                _install = await asyncio.to_thread(
                                    _subprocess.run,
                                    [
                                        "npm", "install",
                                        "--prefer-offline",
                                        "--no-audit",
                                        "--loglevel=error",
                                    ],
                                    cwd=pre_workspace,
                                    capture_output=True,
                                    text=True,
                                    timeout=55,
                                )
                            if _install.returncode != 0:
                                _err = (_install.stderr or _install.stdout or "")[:200]
                                await websocket.send_json({
                                    "type": "warning",
                                    "message": f"npm install errors: {_err}",
                                })
                                logger.warning(
                                    "npm install failed (non-fatal) for project %s: %s",
                                    project_id, _err,
                                )
                            else:
                                await websocket.send_json({
                                    "type": "progress",
                                    "message": "✅ Dependencies installed",
                                })
                                logger.info(
                                    "npm install succeeded for project %s", project_id
                                )
                        except _subprocess.TimeoutExpired:
                            await websocket.send_json({
                                "type": "warning",
                                "message": "⚠️ npm install timed out — dependencies may be missing",
                            })
                            logger.warning(
                                "npm install timed out (subprocess) for project %s", project_id
                            )
                        except (asyncio.TimeoutError, TimeoutError):
                            await websocket.send_json({
                                "type": "warning",
                                "message": "⚠️ npm install timed out after 60s — workspace ready but dependencies may be missing",
                            })
                            logger.warning(
                                "npm install timed out (asyncio) for project %s", project_id
                            )
                        except Exception as _install_err:
                            logger.warning(
                                "npm install error (non-fatal) for project %s: %s",
                                project_id, _install_err,
                            )

                    # Send the file tree so the Code tab is populated immediately
                    try:
                        from app.services.pipeline import _send_file_tree
                        await _send_file_tree(websocket, pre_workspace)
                    except Exception as ft_err:
                        logger.warning("Pre-clone file_tree failed: %s", ft_err)

                    # ── Phase 4: Start dev server (STARTING → HEALTH_CHECK) ──
                    # Non-fatal: if it fails, workspace is still usable.
                    # Only attempted for JS projects that have a "dev" script.
                    try:
                        await launch_dev_preview(
                            workspace_path=pre_workspace,
                            session=session,
                            websocket=websocket,
                            chat_session_id=chat_session_id or "",
                        )
                    except Exception as _dev_err:
                        logger.warning(
                            "Dev server launch failed (non-fatal) for project %s: %s",
                            project_id, _dev_err,
                        )

                    logger.info(
                        "Workspace fully initialized for project %s at %s",
                        project_id, pre_workspace,
                    )
                elif not clone_fatal:
                    await websocket.send_json({
                        "type": "status",
                        "status": "preparing",
                        "message": "⚠️ Could not clone repository. Workspace ready in limited mode — you can still send tasks.",
                    })

            if not clone_fatal:
                ready_msg = "Workspace ready. You can start giving tasks."
                if session.repo_url:
                    ready_msg = (
                        "Repository cloned. "
                        "The agent has terminal, file editor, and browser tools available. "
                        "You can start giving tasks."
                    )
                await ws_transition(
                    session, websocket, WorkspaceState.READY, ready_msg,
                )
                await websocket.send_json({
                    "type": "status",
                    "status": "ready",
                    "sessionId": session.session_id,
                    "message": ready_msg,
                })

        # NOTE: stream_events_to_ws removed — OpenHands Conversation no longer
        # created. Claude Code SDK sends events directly via WebSocket.
        streaming_task = None

        # ── If task was included in handshake, run it immediately ─
        # Skip if clone_fatal — workspace is in ERROR state, no point running the task.
        if task and not existing and not clone_fatal:
            # ── Step 1: Save user task to DB ──────────────────
            if chat_session_id:
                try:
                    await ChatService.add_message(
                        session_id=chat_session_id, role="user",
                        content=task, event_type="UserTask",
                        user_jwt=user_jwt,
                    )
                except Exception as exc:
                    logger.warning("Failed to persist user message: %s", exc)

            # ── Step: Got Task ────────────────────────────────
            await ws_transition(
                session, websocket, WorkspaceState.UPDATING,
                "Agent starting task...",
            )
            await websocket.send_json({
                "type": "step", "step": "got_task",
                "label": "Got task", "done": True,
            })
            await websocket.send_json({
                "type": "agent_event", "event": "task_start",
                "content": f"Agent starting task: {task}",
            })
            await websocket.send_json({
                "type": "step", "step": "understanding",
                "label": "Understanding the project", "done": True,
            })

            # ── Build enriched task + run pipeline via orchestrator ──
            enriched_task = await build_enriched_task(task, session, project_id, user_id, user_jwt)
            pipeline_user = build_pipeline_user(session, api_key, gemini_api_key, user_package_manager, user_jwt)
            _task_result: TaskResult = await agent_orchestrator.execute_task(
                enriched_task=enriched_task,
                session=session,
                websocket=websocket,
                pipeline_user=pipeline_user,
                chat_session_id=chat_session_id or "",
                conversation_id=conversation_id,
                user_jwt=user_jwt,
                task=task,
                images=[],
            )
            if _task_result.stopped:
                explicit_stop = True

        # ── 5. Follow-up loop ────────────────────────────
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type", "message")
            content = data.get("content", "")
            followup_images = data.get("images", [])

            # Heartbeat ping — respond and continue
            if msg_type == "ping":
                await websocket.send_json({"type": "pong"})
                continue

            # Stop must be checked BEFORE empty-content guard
            # because stop messages typically have no content field
            if msg_type in ("stop", "stop_task"):
                explicit_stop = True
                await websocket.send_json({
                    "type": "status",
                    "status": "stopping",
                    "message": "Stopping agent...",
                })
                break

            if not content and not followup_images:
                # Skip truly empty messages (no text AND no images)
                continue

            # Image-only messages need a default task description
            if not content and followup_images:
                content = f"Analyze the {len(followup_images)} attached image(s) and implement any changes they suggest."

            # ── Retry preview — restart dev server without re-cloning ───
            if msg_type == "retry_preview":
                workspace_path = session.workspace_dir
                if workspace_path and os.path.isdir(workspace_path):
                    logger.info("retry_preview: restarting dev server for %s", project_id)
                    try:
                        await launch_dev_preview(
                            workspace_path=workspace_path,
                            session=session,
                            websocket=websocket,
                            chat_session_id=chat_session_id or "",
                        )
                    except Exception as retry_preview_err:
                        logger.warning("retry_preview failed: %s", retry_preview_err)
                        await websocket.send_json({
                            "type": "preview_error",
                            "error_stage": "start",
                            "message": f"Failed to restart preview: {str(retry_preview_err)[:120]}",
                        })
                else:
                    await websocket.send_json({
                        "type": "preview_error",
                        "error_stage": "no_workspace",
                        "message": "No workspace available. Please refresh the page.",
                    })
                continue

            if msg_type == "push":
                # Explicit push request from client
                new_branch = data.get("newBranch")
                await _auto_push_if_needed(
                    websocket, session, 
                    commit_message=content or "Manual push by user",
                    new_branch=new_branch
                )
                continue

            logger.info("[%s] Follow-up: %s", session.session_id, content[:80])

            # ── Save follow-up user message to DB ─────────────
            if chat_session_id:
                try:
                    await ChatService.add_message(
                        session_id=chat_session_id, role="user",
                        content=content, event_type="UserTask",
                        user_jwt=user_jwt,
                    )
                except Exception as exc:
                    logger.warning("Failed to persist follow-up message: %s", exc)

            # ── Step: Got Task ────────────────────────────────
            await ws_transition(
                session, websocket, WorkspaceState.UPDATING,
                "Agent working on task...",
            )
            await websocket.send_json({
                "type": "step", "step": "got_task",
                "label": "Got task", "done": True,
            })
            await websocket.send_json({
                "type": "agent_event", "event": "task_start",
                "content": f"Processing: {content[:80]}...",
            })

            # ── Step: Understanding ───────────────────────────
            await websocket.send_json({
                "type": "step", "step": "understanding",
                "label": "Understanding the project", "done": True,
            })

            # Build follow-up task with conversation context
            logger.info("[%s] Starting task: %s", session.session_id, content[:100])
            context_briefing = await build_conversation_context(user_id, project_id, user_jwt)
            image_note = (
                f"\n\n[User attached {len(followup_images)} image(s) — "
                "they will be analyzed for visual context.]"
                if followup_images else ""
            )
            full_task = (
                f"{context_briefing}\n\nCURRENT TASK: {content}{image_note}"
                if context_briefing
                else f"{content}{image_note}"
            )

            # Run follow-up pipeline via orchestrator (handles hydration + stop + completion)
            pipeline_user = build_pipeline_user(session, api_key, gemini_api_key, user_package_manager, user_jwt)
            _followup_result: TaskResult = await agent_orchestrator.execute_task(
                enriched_task=full_task,
                session=session,
                websocket=websocket,
                pipeline_user=pipeline_user,
                chat_session_id=chat_session_id or "",
                conversation_id=conversation_id,
                user_jwt=user_jwt,
                task=content,
                images=followup_images,
            )
            if _followup_result.stopped:
                explicit_stop = True

    except WebSocketDisconnect:
        logger.info(
            "WebSocket disconnected%s",
            f" — session {session.session_id}" if session else "",
        )
    except asyncio.TimeoutError:
        logger.warning("WebSocket initial config timeout")
        try:
            await websocket.send_json({
                "type": "error",
                "message": "Timeout waiting for initial configuration.",
            })
        except Exception:
            pass
    except Exception as exc:
        logger.error("WebSocket error (session=%s): %s", getattr(session, "session_id", "?"), exc, exc_info=True)
        try:
            if session:
                await ws_transition(
                    session, websocket, WorkspaceState.ERROR,
                    "An internal error occurred. Please try again.",
                )
            await websocket.send_json({
                "type": "error",
                "message": "An internal error occurred. Please try again.",
            })
        except Exception:
            pass
    finally:
        # Pipeline task lifecycle on disconnect:
        #   • explicit_stop (user pressed Stop) → cancel immediately
        #   • network disconnect / browser close  → detach WebSocket from proxy
        #     so the pipeline keeps running silently; events buffer in Redis
        #     and are replayed when the client reconnects.
        if pipeline_task and not pipeline_task.done():
            if explicit_stop:
                pipeline_task.cancel()
                try:
                    await pipeline_task
                except (asyncio.CancelledError, Exception):
                    pass
                logger.info("Pipeline task cancelled on explicit stop")
            else:
                # Detach the WebSocket — pipeline continues in the background.
                # Events are published to Redis Stream until the task finishes.
                if session and session.ws_proxy is not None:
                    session.ws_proxy.detach()
                logger.info(
                    "Pipeline task detached on disconnect — running in background "
                    "for session %s", getattr(session, "session_id", "?")
                )

        if streaming_task:
            streaming_task.cancel()
            try:
                await streaming_task
            except asyncio.CancelledError:
                pass

        # Determine whether a background pipeline is still running.
        # If it is, we must NOT destroy the session or workspace — the pipeline
        # is still using them. Cleanup will happen when the pipeline finishes
        # or when the session TTL reaper fires.
        _pipeline_still_running = (
            not explicit_stop
            and session is not None
            and session.pipeline_task is not None
            and not session.pipeline_task.done()
        )

        if _pipeline_still_running:
            logger.info(
                "Session %s kept alive — pipeline running in background",
                session.session_id,
            )
        else:
            # No background pipeline — safe to destroy session and workspace.
            # Code is already pushed to GitHub; workspace is redundant.
            # Each step is wrapped independently so a failure in one never
            # prevents the others from running (no partial-cleanup leak).
            pass  # cleanup continues below

        if session and not _pipeline_still_running:
            try:
                await destroy_session(session.session_id)
                logger.info("Session %s destroyed on disconnect", session.session_id)
            except Exception as exc:
                logger.error("Failed to destroy session %s: %s", session.session_id, exc)

        # Mark chat session as inactive (always — even if pipeline is running)
        if chat_session_id and ws_user:
            try:
                await ChatService.deactivate_session(
                    chat_session_id, user_id=ws_user.user_id, user_jwt=ws_user.raw_jwt
                )
            except Exception as exc:
                logger.warning("Failed to mark chat session inactive: %s", exc)

        # Destroy workspace only when no background pipeline is running.
        # If the pipeline is still active it needs the workspace on disk.
        if not _pipeline_still_running:
            try:
                await workspace_manager.destroy_workspace(conversation_id)
                logger.info("Workspace destroyed for conversation %s", conversation_id)
            except Exception as exc:
                logger.error("Failed to destroy workspace for conversation %s: %s", conversation_id, exc)

        logger.info("WebSocket session cleaned up")




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
                "message": f"⚠️ Auto-push skipped: {reason_str}. Changes are saved locally.",
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
            # Build a GitHub/GitLab compare URL for easy PR creation
            pr_url = ""
            if "github.com" in repo_url:
                # https://github.com/owner/repo/compare/main...branch
                clean_url = repo_url.rstrip(".git").rstrip("/")
                pr_url = f"{clean_url}/compare/{session.branch}...{target_branch}" if new_branch else ""
            elif "gitlab" in repo_url:
                clean_url = repo_url.rstrip(".git").rstrip("/")
                pr_url = f"{clean_url}/-/merge_requests/new?merge_request[source_branch]={target_branch}" if new_branch else ""

            await websocket.send_json({
                "type": "git_push_result",
                "pushed": True,
                "branch": target_branch,
                "repoUrl": repo_url,
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
