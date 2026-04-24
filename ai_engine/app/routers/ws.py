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
from app.services.dev_server import stop_dev_preview

from app.services.local_preview import start_local_preview
from app.paths import preview_workspace_path
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
    background_preview_task: Optional[asyncio.Task] = None  # track for cleanup
    chat_session_id: Optional[str] = None
    reconnect_chat_session_id: Optional[str] = None
    conversation_id: str = str(uuid.uuid4())  # unique per WS connection
    explicit_stop: bool = False  # initialise before try so finally block always has it

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
                "code": "HANDSHAKE_INVALID",
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
                    "code": "HANDSHAKE_INVALID",
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
                "code": "AUTH_EXPIRED",
                "message": "Authentication required. Your session may have expired — please sign in again.",
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
                # Multi-tab collision: another WS is still attached when we
                # reconnect. Detaching it below means the other tab goes dark,
                # so tell this tab what happened — the frontend renders a
                # toast so the user can close the stale tab.
                _prior_attached = session.ws_proxy.is_attached()
                replayed = await session.ws_proxy.replay(websocket, last_id=last_event_id)
                session.ws_proxy.attach(websocket)
                if replayed:
                    logger.info(
                        "Replayed %d buffered events for session %s",
                        replayed, session.session_id,
                    )
                if _prior_attached:
                    logger.info(
                        "Multi-tab takeover detected for session %s — warning new tab",
                        session.session_id,
                    )
                    try:
                        await websocket.send_json({
                            "type": "warning",
                            "code": "MULTI_TAB",
                            "message": (
                                "This workspace was also open in another tab. "
                                "Events will flow here from now on — close the "
                                "other tab to avoid confusion."
                            ),
                        })
                    except Exception:
                        pass

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
            # Prefer the stable /tmp/lucid_ws_* path (populated by background
            # preview) because it always has the real source files. Fall back
            # to session.workspace_dir when the preview path doesn't exist yet.
            _reconnect_ft_path = None
            if project_id:
                _rc_preview_tmp = preview_workspace_path(project_id)
                if os.path.isdir(_rc_preview_tmp):
                    _reconnect_ft_path = _rc_preview_tmp
                    # Keep session workspace_dir in sync
                    session.workspace_dir = _rc_preview_tmp
            if not _reconnect_ft_path and session.workspace_dir and os.path.isdir(session.workspace_dir):
                _reconnect_ft_path = session.workspace_dir
            if _reconnect_ft_path:
                try:
                    from app.services.pipeline import _send_file_tree
                    await _send_file_tree(websocket, _reconnect_ft_path)
                    logger.info("Sent file_tree on reconnect for session %s from %s",
                                session.session_id, _reconnect_ft_path)
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
                _err_code = "RATE_LIMITED" if rate_err.status_code == 429 else "SESSION_CREATE_FAILED"
                await websocket.send_json({
                    "type": "error",
                    "code": _err_code,
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
        _skip_workspace_setup = False  # set True when re-entering a completed project

        if not existing and project_id:
            try:
                # Step 1: find the session row using only guaranteed-existing columns.
                # Do NOT select optional columns (platform_repo_url, generation_complete)
                # in this query — they require migrations 009/014 which may not be run,
                # and a PostgREST error here would leave _prev_session_data as None and
                # cause the pipeline to re-run on every re-entry.
                async with db_client(user_jwt) as client:
                    prev_s = await (
                        client.table("chat_sessions")
                        .select("id")
                        .eq("user_id", user_id)
                        .eq("project_id", project_id)
                        .order("created_at", desc=True)
                        .limit(1)
                        .execute()
                    )
                if prev_s.data:
                    prev_sid = prev_s.data[0]["id"]

                    # Step 2: try to read optional flag columns (added in later migrations).
                    # Fail silently — if the columns don't exist the flags just stay False/None.
                    platform_repo_url = None
                    generation_complete = False
                    user_repo_url = None
                    try:
                        async with db_client(user_jwt) as client:
                            flags_r = await (
                                client.table("chat_sessions")
                                .select("platform_repo_url, user_repo_url, generation_complete")
                                .eq("id", prev_sid)
                                .maybe_single()
                                .execute()
                            )
                        if flags_r.data:
                            platform_repo_url = flags_r.data.get("platform_repo_url")
                            user_repo_url = flags_r.data.get("user_repo_url")
                            generation_complete = flags_r.data.get("generation_complete", False)
                    except Exception as _flags_err:
                        logger.debug(
                            "Optional flag columns not available for session %s "
                            "(migrations 009/014 may not be applied): %s",
                            prev_sid, _flags_err,
                        )

                    # Step 3: load messages for chat history replay + wizard re-entry detection
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
                        "platform_repo_url":   platform_repo_url,
                        "user_repo_url":       user_repo_url,
                        "generation_complete": generation_complete,
                        "messages":            prev_msgs.data or [],
                    }
                    logger.info(
                        "Found previous session %s for project %s "
                        "(complete=%s, msgs=%d, repo=%s)",
                        prev_sid, project_id,
                        generation_complete,
                        len(_prev_session_data["messages"]),
                        bool(platform_repo_url),
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
                or _prev_session_data.get("user_repo_url")
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
                _skip_workspace_setup = True  # skip clone/install/E2B for returning project
                await ws_transition(
                    session, websocket, WorkspaceState.READY,
                    "Project loaded. Ask me to make changes.",
                )
                await websocket.send_json({
                    "type": "status",
                    "status": "ready",
                    "sessionId": session.session_id,
                    "message": "Project loaded. Ask me to make changes.",
                })

                # ── Start local dev server in background for returning projects ──
                # Clone the platform repo (or user's repo as fallback) into a
                # temp workspace, install deps, then start the real dev server.
                _platform_repo = _prev_session_data.get("platform_repo_url") if _prev_session_data else None
                _user_repo = _prev_session_data.get("user_repo_url") if _prev_session_data else None
                _repo_to_clone = _platform_repo or _user_repo or (session.repo_url if session and session.repo_url else None)

                # Also check if there's already a cached workspace in /tmp from the
                # original generation — if so, we can restart the dev server without
                # re-cloning even if we have no repo URL (e.g. push failed mid-way).
                _bg_conv_id_early = project_id or conversation_id
                _cached_tmp = preview_workspace_path(_bg_conv_id_early)
                _has_cached_ws = (
                    os.path.isdir(_cached_tmp)
                    and os.path.isdir(os.path.join(_cached_tmp, "node_modules"))
                )

                # Check if dev server is already alive — if so skip all the setup below
                _early_active_url = None
                try:
                    from app.services.local_preview import get_active_preview_url as _gap
                    _early_active_url = _gap(conversation_id=_bg_conv_id_early)
                except Exception:
                    pass
                if _early_active_url:
                    try:
                        await websocket.send_json({"type": "preview_ready",
                                                   "preview_url": _early_active_url,
                                                   "message": f"🖥️ Live preview: {_early_active_url}"})
                        if session is not None:
                            session.workspace_dir = _cached_tmp
                    except Exception:
                        pass

                if _repo_to_clone or _has_cached_ws:
                    # Capture conv_id for the closure (stable across reconnects)
                    _bg_conv_id = project_id or conversation_id

                    async def _background_preview():
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
                                                           "message": f"🖥️ Live preview: {_existing_url}"})
                                return

                            # ── Use a STABLE path per conversation ──────────────────
                            # Same path across reconnects — avoids re-clone + re-install
                            # every time the user returns to the workspace page.
                            # (_tmp already computed above)
                            _nm = os.path.join(_tmp, "node_modules")
                            _already_installed = os.path.isdir(_nm) and any(os.scandir(_nm))

                            if _already_installed:
                                # node_modules already present — skip clone + install entirely.
                                # Just start (or reuse) the dev server.
                                logger.info("bg_preview: node_modules cache hit for %s — skipping install", _bg_conv_id)
                                await websocket.send_json({"type": "preview_status",
                                                           "status": "starting",
                                                           "message": "Starting preview (cached)…"})
                            else:
                                # Fresh workspace — need to clone and install.
                                await websocket.send_json({"type": "preview_status",
                                                           "status": "cloning",
                                                           "message": "Cloning repository for preview…"})

                                # Clean stale partial-clone directory so git doesn't
                                # refuse to clone into a non-empty target.
                                if os.path.isdir(_tmp) and os.listdir(_tmp):
                                    import shutil as _shutil
                                    try:
                                        _shutil.rmtree(_tmp)
                                        logger.info("bg_preview: cleared stale directory %s before re-clone", _tmp)
                                    except Exception as _rm_err:
                                        logger.warning("bg_preview: could not clear stale dir %s: %s", _tmp, _rm_err)

                                os.makedirs(_tmp, exist_ok=True)
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
                                    await websocket.send_json({"type": "preview_error",
                                                               "error_stage": "clone",
                                                               "message": f"Could not clone repository: {_clone_err or 'check token/URL'}"})
                                    return

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
                                                               "message": f"Installing dependencies ({_bg_pm})…"})
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
                                        # Append --store-dir for pnpm so packages are cached globally
                                        if _bg_pm == "pnpm":
                                            _install_cmd = _install_cmd + ["--store-dir", "/tmp/pnpm_store"]
                                        _bg_install = await asyncio.to_thread(
                                            _sp.run,
                                            _install_cmd,
                                            cwd=_tmp, capture_output=True, text=True,
                                            timeout=300, env=_install_env,
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

                    background_preview_task = asyncio.create_task(_background_preview())

        else:
            # On reconnect — chat_session_id was already looked up above (early lookup)
            # and stored in chat_session_id via reconnect_chat_session_id. Nothing to do.
            if reconnect_chat_session_id:
                logger.info("Using chat session %s (found during early reconnect lookup)", reconnect_chat_session_id)

            # If a dev server is already running for this project (started by a
            # previous connection's _background_preview), tell the frontend immediately.
            # If not running but a cached workspace exists (node_modules present),
            # restart the dev server in the background so the preview recovers
            # automatically without requiring the user to click "Restart Preview".
            try:
                from app.services.local_preview import get_active_preview_url
                _reconnect_conv_id = project_id or conversation_id
                _reconnect_preview_url = get_active_preview_url(conversation_id=_reconnect_conv_id)
                if _reconnect_preview_url:
                    logger.info("reconnect: active preview at %s for %s — sending preview_ready",
                                _reconnect_preview_url, _reconnect_conv_id)
                    await websocket.send_json({"type": "preview_ready",
                                               "preview_url": _reconnect_preview_url,
                                               "message": f"🖥️ Live preview: {_reconnect_preview_url}"})
                else:
                    # Dev server died — try to restart from cached workspace
                    _recon_ws = None
                    if project_id:
                        _recon_tmp = preview_workspace_path(project_id)
                        if os.path.isdir(_recon_tmp) and os.path.isdir(os.path.join(_recon_tmp, "node_modules")):
                            _recon_ws = _recon_tmp
                            if session is not None:
                                session.workspace_dir = _recon_ws
                    if not _recon_ws and session and session.workspace_dir:
                        _sd = session.workspace_dir
                        if os.path.isdir(_sd) and os.path.isdir(os.path.join(_sd, "node_modules")):
                            _recon_ws = _sd
                    if _recon_ws:
                        _rc_conv = _reconnect_conv_id
                        _rc_ws = _recon_ws
                        _rc_pm = user_package_manager

                        async def _reconnect_dev_restart():
                            try:
                                await start_local_preview(
                                    workspace_path=_rc_ws,
                                    conversation_id=_rc_conv,
                                    websocket=websocket,
                                    package_manager=_rc_pm,
                                )
                            except Exception as _rde:
                                logger.warning("reconnect: dev server restart failed: %s", _rde)

                        background_preview_task = asyncio.create_task(_reconnect_dev_restart())
                        logger.info("reconnect: restarting dev server for %s from cached workspace %s",
                                    _rc_conv, _recon_ws)
            except Exception as _rp_err:
                logger.debug("reconnect: preview URL check failed (ok): %s", _rp_err)

        # ── 3. Pipeline handles agent execution ──────────
        # NOTE: The old mock gate (sdk.OPENHANDS_AVAILABLE) is removed.
        # run_pipeline() has its own subprocess fallbacks for clone/push
        # and does NOT require the OpenHands SDK.
        if not sdk.OPENHANDS_AVAILABLE:
            logger.info("OpenHands SDK not installed — pipeline will use subprocess fallbacks")

        # ── 4. Session ready — workspace initialized ─────────
        # Only send the "ready" message for NEW sessions.
        # For reconnects we already sent it at step 2 above (reconnected=True).
        # If there is no task to execute, skip the whole workspace setup.
        # Cloning, npm install, and launching E2B are only useful when the
        # pipeline is about to run. Without a task they waste 3-5 min and
        # overwrite the real Vercel URL with a temporary E2B URL.
        # When the user submits a task later, run_pipeline does lazy setup.
        if not existing and not task and not _skip_workspace_setup:
            logger.info("No task on new session for project %s — skipping workspace setup", project_id)
            _skip_workspace_setup = True
            await ws_transition(session, websocket, WorkspaceState.READY, "Workspace ready.")
            await websocket.send_json({
                "type": "status",
                "status": "ready",
                "sessionId": session.session_id,
                "message": "Project loaded. Send a message to make changes.",
            })

        if not existing and not _skip_workspace_setup:
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

                    # ── Validate: package.json must exist for install ──────────
                    import subprocess as _subprocess
                    from app.services.pipeline.package_manager import (
                        detect_package_manager as _detect_pm,
                        _pm_install_cmd,
                        _pm_env,
                    )
                    pkg_json = os.path.join(pre_workspace, "package.json")
                    if not os.path.exists(pkg_json):
                        logger.info(
                            "No package.json in cloned repo — skipping install"
                        )
                    else:
                        # Detect from lock files — never blindly use npm.
                        # Running npm install on a pnpm repo creates package-lock.json
                        # alongside pnpm-lock.yaml, breaking subsequent pnpm commands.
                        _pm = _detect_pm(pre_workspace, user_package_manager)
                        _install_cmd = _pm_install_cmd(_pm)
                        _install_env = _pm_env(_pm)

                        # ── Install deps silently (no workspace state change) ─────
                        # 300s (5 min) outer / 290s inner gives room for heavy
                        # dependency trees (Next.js + shadcn/ui + radix = ~150 pkgs).
                        # pnpm resolves from cache after the first install so
                        # subsequent workspaces are much faster (~10-20s).
                        try:
                            async with asyncio.timeout(300):
                                _install = await asyncio.to_thread(
                                    _subprocess.run,
                                    _install_cmd,
                                    cwd=pre_workspace,
                                    capture_output=True,
                                    text=True,
                                    timeout=290,
                                    env=_install_env,
                                )
                            if _install.returncode != 0:
                                _err = (_install.stderr or _install.stdout or "")[:200]
                                await websocket.send_json({
                                    "type": "warning",
                                    "message": f"{_pm} install errors: {_err}",
                                })
                                logger.warning(
                                    "%s install failed (non-fatal) for project %s: %s",
                                    _pm, project_id, _err,
                                )
                            else:
                                await websocket.send_json({
                                    "type": "progress",
                                    "message": f"✅ Dependencies installed ({_pm})",
                                })
                                logger.info(
                                    "%s install succeeded for project %s", _pm, project_id
                                )
                        except _subprocess.TimeoutExpired:
                            await websocket.send_json({
                                "type": "warning",
                                "message": f"⚠️ {_pm} install timed out — dependencies may be missing",
                            })
                            logger.warning(
                                "%s install timed out (subprocess) for project %s", _pm, project_id
                            )
                        except (asyncio.TimeoutError, TimeoutError):
                            await websocket.send_json({
                                "type": "warning",
                                "message": f"⚠️ {_pm} install timed out after 5 min — workspace ready but dependencies may be missing",
                            })
                            logger.warning(
                                "%s install timed out (asyncio) for project %s", _pm, project_id
                            )
                        except Exception as _install_err:
                            logger.warning(
                                "%s install error (non-fatal) for project %s: %s",
                                _pm, project_id, _install_err,
                            )

                    # Send the file tree so the Code tab is populated immediately
                    try:
                        from app.services.pipeline import _send_file_tree
                        await _send_file_tree(websocket, pre_workspace)
                    except Exception as ft_err:
                        logger.warning("Pre-clone file_tree failed: %s", ft_err)

                    # ── Start local dev server for live preview ────────────
                    # Runs npm run dev in the cloned workspace on a free port
                    # in the 4000-4050 range (exposed by docker-compose).
                    await ws_transition(
                        session, websocket, WorkspaceState.STARTING,
                        "Running the code for Preview...",
                    )
                    try:
                        await start_local_preview(
                            workspace_path=pre_workspace,
                            conversation_id=project_id or conversation_id,
                            websocket=websocket,
                        )
                    except Exception as _dev_err:
                        logger.warning(
                            "Local preview failed (non-fatal) for project %s: %s",
                            project_id, _dev_err,
                        )

                    logger.info(
                        "Workspace fully initialized for project %s at %s",
                        project_id, pre_workspace,
                    )

                    # ── Auto bug scan (background) ─────────────────────────────
                    # Run a lightweight quality scan on the freshly cloned repo.
                    # Results appear in the chat as a structured report.
                    # Runs in the background so it never blocks workspace startup.
                    if api_key:
                        _scan_workspace = pre_workspace
                        _scan_api_key   = api_key

                        async def _background_bug_scan():
                            try:
                                # Small delay so the workspace READY message arrives first
                                await asyncio.sleep(3)
                                from app.services.auto_bug_fixer import scan_and_report_bugs
                                await scan_and_report_bugs(
                                    workspace_path=_scan_workspace,
                                    api_key=_scan_api_key,
                                    websocket=websocket,
                                    auto_fix=False,  # report only; user can ask to fix
                                )
                            except Exception as _scan_err:
                                logger.warning("Background bug scan failed (non-fatal): %s", _scan_err)

                        asyncio.create_task(_background_bug_scan())

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
            # ── Pre-flight: validate user-provided PAT once per session ───
            # Fails fast with a structured error instead of wasting minutes on
            # clone/push attempts against a revoked or expired token.
            _pat_to_check = session.git_token if session else ""
            if _pat_to_check and session and session.repo_url:
                _pat_ok, _pat_err = await _validate_git_pat(_pat_to_check, session.repo_url)
                if not _pat_ok:
                    logger.warning("PAT validation failed for user %s: %s", user_id, _pat_err)
                    try:
                        await websocket.send_json({
                            "type": "error",
                            "code": "PAT_INVALID",
                            "message": _pat_err,
                        })
                    except Exception:
                        pass
                    return

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
            chat_mode = data.get("mode", "edit")       # "edit" (default) or "discuss"
            web_search = data.get("web_search", True)  # frontend toggle

            # Heartbeat ping — respond and continue
            if msg_type == "ping":
                await websocket.send_json({"type": "pong"})
                continue

            # Stop preview sandbox on demand (user closes preview / navigates away)
            if msg_type == "stop_preview":
                if background_preview_task and not background_preview_task.done():
                    background_preview_task.cancel()
                    try:
                        await background_preview_task
                    except (asyncio.CancelledError, Exception):
                        pass
                await stop_dev_preview(chat_session_id=chat_session_id or "")
                await websocket.send_json({"type": "preview_stopped"})
                logger.info("Preview sandbox stopped on user request")
                continue

            # Stop must be checked BEFORE empty-content guard
            # because stop messages typically have no content field
            if msg_type in ("stop", "stop_task"):
                explicit_stop = True
                # Always ACK so the frontend knows the message was received
                try:
                    await websocket.send_json({"type": "stop_ack"})
                except Exception:
                    pass
                try:
                    await websocket.send_json({
                        "type": "status",
                        "status": "stopping",
                        "message": "Stopping agent...",
                    })
                except Exception:
                    pass
                break

            # ── Plan confirmation — user approves or rejects the plan ───
            if msg_type == "plan_confirm":
                from app.services.project_generator import resolve_plan_confirmation
                logger.info("[%s] Plan confirmed by user", getattr(session, "session_id", "?"))
                resolve_plan_confirmation(websocket, {"confirmed": True})
                continue

            if msg_type == "plan_reject":
                from app.services.project_generator import resolve_plan_confirmation
                correction = data.get("correction", "")
                logger.info(
                    "[%s] Plan rejected by user — correction: %s",
                    getattr(session, "session_id", "?"),
                    correction[:80],
                )
                resolve_plan_confirmation(websocket, {
                    "confirmed": False,
                    "correction": correction,
                })
                continue

            if not content and not followup_images:
                # Skip truly empty messages (no text AND no images)
                continue

            # Image-only messages need a default task description
            if not content and followup_images:
                content = f"Analyze the {len(followup_images)} attached image(s) and implement any changes they suggest."

            # ── Retry preview — restart local dev server ─────────────
            if msg_type == "retry_preview":
                workspace_path = session.workspace_dir
                if workspace_path and os.path.isdir(workspace_path):
                    logger.info("retry_preview: restarting local dev server for %s", project_id)
                    try:
                        await start_local_preview(
                            workspace_path=workspace_path,
                            conversation_id=project_id or conversation_id,
                            websocket=websocket,
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

            # ── Bug scan / fix shortcut ───────────────────────────
            # Detect intent: "scan for bugs", "fix the bugs", "run quality check", etc.
            # Also detects pasted error traces (webpack errors, module-not-found, etc.)
            _content_lower = content.lower().strip()
            _is_scan_intent = any(kw in _content_lower for kw in (
                "scan for bugs", "scan my code", "find bugs", "check for bugs",
                "run quality", "quality check", "code review", "audit the code",
                "check code quality",
            ))
            _is_fix_intent = any(kw in _content_lower for kw in (
                "fix the bugs", "fix the issues", "fix bugs", "apply the fixes",
                "auto fix", "auto-fix", "fix all bugs", "fix the errors",
                "fix this", "fix it", "fix the error",
            ))
            # Detect pasted error traces: if the message looks like a build/runtime
            # error (contains Error:, at line, Cannot find module, etc.) treat it
            # as a fix request automatically — no keyword needed.
            _looks_like_error = (
                not _is_fix_intent
                and session.workspace_dir
                and any(sig in content for sig in (
                    "Error:", "error TS", "Module not found", "Cannot find module",
                    "SyntaxError", "TypeError", "ReferenceError", "Failed to compile",
                    "webpack error", "Build failed", "ENOENT", "Unhandled error",
                    "Uncaught ", "at Object.", "at Module.",
                ))
            )
            if _looks_like_error:
                _is_fix_intent = True

            if (_is_scan_intent or _is_fix_intent) and session.workspace_dir and api_key:
                workspace_path = session.workspace_dir
                _do_fix = _is_fix_intent
                try:
                    from app.services.auto_bug_fixer import scan_and_report_bugs
                    await websocket.send_json({
                        "type": "chat_message",
                        "role": "agent",
                        "content": (
                            "🔧 **Running bug scan and applying fixes…**"
                            if _do_fix
                            else "🔍 **Running code quality scan…**"
                        ),
                    })
                    await scan_and_report_bugs(
                        workspace_path=workspace_path,
                        api_key=api_key,
                        websocket=websocket,
                        auto_fix=_do_fix,
                    )
                except Exception as _scan_exc:
                    logger.warning("On-demand bug scan failed: %s", _scan_exc)
                    await websocket.send_json({
                        "type": "chat_message",
                        "role": "agent",
                        "content": f"⚠️ Bug scan failed: {str(_scan_exc)[:120]}",
                    })
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
            logger.info("[%s] Starting task: %s (mode=%s, web_search=%s)",
                        session.session_id, content[:100], chat_mode, web_search)
            context_briefing = await build_conversation_context(user_id, project_id, user_jwt)
            image_note = (
                f"\n\n[User attached {len(followup_images)} image(s) — "
                "they will be analyzed for visual context.]"
                if followup_images else ""
            )
            # Discuss mode: agent must NOT modify files — analysis/explanation only
            _discuss_prefix = (
                "[DISCUSS MODE — Analyze and explain only. "
                "Do NOT write, edit, or delete any files. Just answer the question.]\n\n"
                if chat_mode == "discuss" else ""
            )
            # Web search hint for the pipeline (Gemini research step reads this)
            _web_search_note = "" if web_search else "\n\n[Web search disabled — use only existing codebase knowledge.]"
            full_task = (
                f"{context_briefing}\n\nCURRENT TASK: {_discuss_prefix}{content}{image_note}{_web_search_note}"
                if context_briefing
                else f"{_discuss_prefix}{content}{image_note}{_web_search_note}"
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
                "code": "HANDSHAKE_TIMEOUT",
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
                "code": "INTERNAL_ERROR",
                "message": "An internal error occurred. Please try again.",
            })
        except Exception:
            pass
    finally:
        # Pipeline task lifecycle on disconnect:
        #   • explicit_stop (user pressed Stop) → cancel immediately
        #   • network disconnect / browser close  → check Redis availability:
        #       - Redis available  → detach + buffer events for reconnect replay
        #       - Redis unavailable (no Redis in docker-compose) → schedule a
        #         watchdog that cancels the pipeline after RECONNECT_GRACE_SECONDS
        #         to avoid burning API credits on a session that can never be
        #         recovered (in-memory only, no persistence).
        _RECONNECT_GRACE_SECONDS = 120  # 2 minutes to reconnect before aborting
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

                # Check if Redis is available for session persistence.
                # Without Redis, session cannot survive a browser reload, so
                # we cap background running time to RECONNECT_GRACE_SECONDS.
                _redis_available = False
                try:
                    from app.services.redis_client import get_redis as _get_redis
                    _redis_available = _get_redis() is not None
                except Exception:
                    pass

                if not _redis_available:
                    # No Redis → schedule a watchdog to cancel the pipeline
                    # if no reconnection happens within the grace period.
                    _pt = pipeline_task
                    async def _disconnect_watchdog() -> None:
                        await asyncio.sleep(_RECONNECT_GRACE_SECONDS)
                        if not _pt.done():
                            _pt.cancel()
                            logger.warning(
                                "Pipeline cancelled after %ds disconnect timeout "
                                "(no Redis — session unrecoverable). "
                                "Session: %s",
                                _RECONNECT_GRACE_SECONDS,
                                getattr(session, "session_id", "?"),
                            )
                    asyncio.create_task(_disconnect_watchdog())
                    logger.info(
                        "Pipeline detached (no Redis) — will auto-cancel in %ds if "
                        "client does not reconnect. Session: %s",
                        _RECONNECT_GRACE_SECONDS,
                        getattr(session, "session_id", "?"),
                    )
                else:
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
            # Background preview task lifecycle on disconnect:
            #   • explicit_stop (user pressed Stop) → cancel immediately
            #   • network disconnect / browser close  → let it finish in the
            #     background so the dev server completes startup and registers
            #     its URL in _active_servers.  The reconnect path reads
            #     get_active_preview_url() and sends preview_ready to the new
            #     WebSocket.  _emit() in local_preview.py already swallows
            #     send errors, so the task completes cleanly after disconnect.
            if background_preview_task and not background_preview_task.done():
                if explicit_stop:
                    background_preview_task.cancel()
                    try:
                        await background_preview_task
                    except (asyncio.CancelledError, Exception):
                        pass
                    logger.info("Background preview task cancelled on explicit stop")
                else:
                    logger.info(
                        "Background preview task detached on disconnect — "
                        "running in background until dev server is ready"
                    )

            # Stop E2B preview sandbox
            try:
                await stop_dev_preview(chat_session_id=chat_session_id or "")
            except Exception as exc:
                logger.debug("Preview sandbox cleanup error (ok): %s", exc)

            try:
                await workspace_manager.destroy_workspace(conversation_id)
                logger.info("Workspace destroyed for conversation %s", conversation_id)
            except Exception as exc:
                logger.error("Failed to destroy workspace for conversation %s: %s", conversation_id, exc)

        # Cancel any pending plan confirmation Future so the generator
        # doesn't block forever waiting for a user that already disconnected.
        try:
            from app.services.project_generator import pending_plan_confirmations
            fut = pending_plan_confirmations.pop(id(websocket), None)
            if fut and not fut.done():
                fut.cancel()
        except Exception:
            pass

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
