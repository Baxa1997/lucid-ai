"""WebSocket endpoint for real-time agent communication."""

from __future__ import annotations

import asyncio
import os
import uuid
from typing import Optional
from urllib.parse import urlparse

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
from app.services.pipeline.constants import _PLATFORM_ORG
from app.services.event_bus import WebSocketProxy
from app.services.workspace_manager import workspace_manager
from app.services.local_preview import stop_local_preview
from app.services.agent_status import (
    emit_agent_status,
    emit_task_phase,
    emit_workflow_selected,
)

from app.services.local_preview import start_local_preview
from app.paths import preview_workspace_path
from app.supabase_client import db_client
from app.services.staging_sync import StagingSyncError, sync_workspace_to_staging
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


# Standalone helpers moved to app.routers.ws_helpers (god-module split).
from app.routers.ws_helpers import (  # noqa: F401
    _auto_push_if_needed,
    _insert_marker_preserving_lucid_project,
    _is_jwt_expired_error,
    _provider_from_repo_url,
    _resolve_git_token_from_integrations,
    _run_mock_loop,
    _validate_git_pat,
)




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
    # Wrap the socket with the trace capture proxy if WS_TRACE_FILE is set.
    # No-op when the env var is unset; production paths see zero overhead.
    # IDs (project_id, conversation_id) are backfilled below once the
    # handshake reveals them (the trace's first events will have empty IDs —
    # that's expected and acceptable).
    from app.services.ws_trace import maybe_wrap, update_trace_ids
    websocket = maybe_wrap(websocket)
    logger.info("WebSocket connection accepted")

    # Authenticate from query param (if present)
    ws_user: Optional[AuthenticatedUser] = await authenticate_websocket(websocket)
    if ws_user is None and (
        websocket.application_state.name == "DISCONNECTED"
        or websocket.client_state.name == "DISCONNECTED"
    ):
        return  # closed by authenticate_websocket (e.g. expired/invalid JWT)

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
        _str_fields = ("task", "repoUrl", "repoProvider", "branch", "gitToken", "projectId",
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
            "message": "Getting started…",
        })

        user_id = ws_user.user_id
        user_jwt = ws_user.raw_jwt
        project_id = raw.get("projectId", "")
        # Backfill trace identifiers now that the handshake gave us project_id
        # and we have the per-connection conversation_id. No-op when tracing
        # is disabled.
        update_trace_ids(websocket, project_id=project_id, session_id=conversation_id)
        explicit_stop = False  # track if user explicitly stopped

        # ── 1.5 Resolve LLM Settings (Handshake > Supabase > Default) ──
        model_provider = raw.get("modelProvider") or raw.get("model_provider")
        api_key = raw.get("apiKey") or raw.get("api_key")
        openai_api_key = os.environ.get("OPENAI_API_KEY", "")
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
            # JWT-expired safety net — even though decode_jwt now raises on
            # expiry at the handshake, a token that expires AFTER handshake
            # but BEFORE the first DB call still slips through. Surface it
            # so the frontend can prompt re-auth instead of degrading silently
            # to "Workspace ready" with no project data.
            if _is_jwt_expired_error(db_err):
                logger.info("[%s] JWT expired during user-settings fetch — closing WS for re-auth", user_id)
                try:
                    await websocket.close(code=4010, reason="Authentication expired")
                except Exception:
                    pass
                return
            logger.warning("Failed to fetch user settings from Supabase: %s", db_err)

        if not model_provider:
            model_provider = settings.DEFAULT_PROVIDER

        # Provider split:
        # - Existing-project Codex edits use OpenAI/Codex credentials.
        # - New-project generation and Claude fallback still use Anthropic.
        # The settings table stores one encrypted API key, so infer its family
        # from the selected model/provider.
        _model_provider_l = str(model_provider or "").lower()
        _is_openai_model = (
            _model_provider_l.startswith(("openai", "codex", "gpt-"))
            or "/gpt-" in _model_provider_l
        )
        openai_model = str(model_provider or "").strip() if _is_openai_model else ""
        if api_key and _is_openai_model:
            openai_api_key = str(api_key).strip()
            api_key = ""

        # Fallback: if no Anthropic API key from handshake/settings, use server .env
        if not api_key:
            api_key = os.environ.get("ANTHROPIC_API_KEY") or settings.ANTHROPIC_API_KEY or ""
            if api_key:
                logger.info("Using server fallback ANTHROPIC_API_KEY for user %s", user_id)
        
        logger.info(
            "[%s] Using model: %s, anthropic_key=%s (len=%d), openai_key=%s",
            project_id or "new-session",
            model_provider,
            "set" if api_key else "missing",
            len(str(api_key or "")),
            "yes" if openai_api_key else "no",
        )

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
            fresh_repo_provider = raw.get("repoProvider", "")
            fresh_git_token = raw.get("gitToken", "")
            fresh_branch = raw.get("branch", "")
            if not fresh_git_token and (fresh_repo_url or session.repo_url) and not session.git_token:
                fresh_git_token = await _resolve_git_token_from_integrations(
                    repo_url=fresh_repo_url or session.repo_url,
                    repo_provider=fresh_repo_provider,
                    user_id=user_id,
                    user_jwt=user_jwt,
                )

            if fresh_repo_url:
                session.repo_url = fresh_repo_url
                logger.info("Session repo_url refreshed → %s", fresh_repo_url[:60])
            if fresh_repo_provider:
                session.repo_provider = str(fresh_repo_provider).strip().lower()
                logger.info("Session repo_provider refreshed → %s", session.repo_provider)
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
                _prior_attached = session.ws_proxy.is_attached
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
                    # Re-bind the proxy on reconnect so any new events
                    # produced after this point are persisted under the
                    # right chat_sessions row. The proxy itself was
                    # re-created above with the new websocket.
                    if session.ws_proxy is not None:
                        try:
                            session.ws_proxy.bind_chat(chat_session_id, user_jwt)
                        except Exception as _bind_err:
                            logger.warning(
                                "Could not re-bind chat persistence on reconnect: %s",
                                _bind_err,
                            )
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

            # ── Re-emit preview_ready on reconnect ────────────────────
            # Backend keeps the dev server alive across WS disconnects, but
            # only emits preview_ready once per sandbox boot. Without this
            # block, a client that reconnects (refresh, tab switch, network
            # blip) loses its iframe URL and shows "Preview Not Available"
            # even though the dev server is still serving on its port.
            #
            # If the in-memory registry is empty (e.g. after an ai_engine
            # restart), we fall through to ``preview_status`` so the frontend
            # shows the spinner instead of the "Not Available" empty state,
            # then kick off the bg_preview path the same way a fresh session
            # would.
            try:
                from app.services.local_preview import get_active_preview_url
                _bg_conv_id = project_id or conversation_id
                _active_url = get_active_preview_url(conversation_id=_bg_conv_id)
                if _active_url:
                    await websocket.send_json({
                        "type": "preview_ready",
                        "preview_url": _active_url,
                        "message": "Preview ready",
                    })
                    logger.info(
                        "Re-emitted preview_ready on reconnect for session %s → %s",
                        session.session_id, _active_url,
                    )
                else:
                    # No live preview — show spinner so user doesn't see the
                    # "Not Available" screen while bg_preview spins back up.
                    await websocket.send_json({
                        "type": "preview_status",
                        "status": "starting",
                        "message": "Restarting preview…",
                    })
                    logger.info(
                        "No active preview for session %s — bg_preview will restart it",
                        session.session_id,
                    )
            except Exception as pv_err:
                logger.warning("Failed to re-emit preview_ready on reconnect: %s", pv_err)

            # ── Re-emit pending plan on reconnect ─────────────────────
            # If the user disconnected during the plan-confirmation gate,
            # the pipeline is still waiting on the future and the frontend
            # has lost the plan card. Re-send it so they can confirm
            # without re-typing the task.
            try:
                from app.services.plan_store import get_persisted_plan
                _pending = await get_persisted_plan(chat_session_id or "")
                if _pending and _pending.get("plan_data"):
                    await websocket.send_json({
                        "type": "chat_message",
                        "role": "agent",
                        "messageType": "plan",
                        "planData": _pending["plan_data"],
                    })
                    await websocket.send_json({
                        "type": "plan_awaiting_confirmation",
                        "message": "Welcome back — your plan is still waiting. Click 'Looks Good' to start.",
                    })
                    logger.info(
                        "Re-emitted pending plan on reconnect for chat %s",
                        chat_session_id,
                    )
            except Exception as plan_err:
                logger.warning("Failed to re-emit pending plan on reconnect: %s", plan_err)
        else:
            # ── Create new session (no clone — workspace_manager handles it) ──
            try:
                # Resolve git_token — frontend may send it, or extract from JWT
                repo_url_raw = raw.get("repoUrl", "")
                repo_provider = raw.get("repoProvider", "")
                git_token = raw.get("gitToken", "")
                if not git_token:
                    git_token = await _resolve_git_token_from_integrations(
                        repo_url=repo_url_raw,
                        repo_provider=repo_provider,
                        user_id=user_id,
                        user_jwt=user_jwt,
                    )
                if not git_token and user_jwt:
                    # Extract git token from JWT user_metadata as fallback
                    try:
                        import jwt as pyjwt
                        decoded = pyjwt.decode(user_jwt, options={"verify_signature": False})
                        user_meta = decoded.get("user_metadata", {})
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
                    repo_provider=repo_provider,
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
                # Step 1: find the best previous session for this project.
                # PRIORITY: completed sessions > sessions with platform_repo > plain latest.
                # Without this, an empty reconnect-session created AFTER the real generation
                # gets picked first → _prev_session_data looks empty → no preview started.
                #
                # Use the admin client for this lookup. RLS-enforced queries (via the user
                # JWT) were silently returning empty for legitimate rows whenever the JWT
                # context didn't satisfy the policy (e.g. JWT minted with a kid the policy
                # didn't trust, refreshed token, etc). We still gate on user_id explicitly
                # so this remains per-project — admin only bypasses RLS, not access control.
                #
                # Member access: when a project_members member who is NOT the owner
                # connects, ``user_id`` is the member's id and a per-user filter would
                # miss the owner's completed session — leaving the member stuck on
                # "Preparing preview" forever AND spawning a fresh "New workspace
                # session" row under the member's user_id. Resolve the owner's id
                # by joining chat_sessions on the URL slug (project_id) against
                # project_members (keyed on chat_sessions.id UUID PK). For the owner
                # themselves resolve_shared_session returns None and we fall through
                # to the per-user lookup, so behavior is unchanged.
                from app.supabase_client import managed_admin_client
                from app.services.members import MembershipService
                _shared = await MembershipService.resolve_shared_session(project_id, user_id)
                _lookup_uid = _shared["owner_user_id"] if _shared else user_id
                prev_sid: str | None = None

                # ── Phase 2 Step 6: Explicit continuation token ──
                # Clients that survived to here on a prior connection know the
                # exact chat_session_id they want to resume. When they send
                # `continuationToken` in the handshake we look it up directly
                # — no 4-tier guessing, no race against newer sessions. This
                # is the deterministic path; the heuristic 1a-1d below stays
                # in place as the fallback for legacy clients and for the
                # never-seen-this-project-before case.
                _continuation_token = (raw.get("continuationToken") or "").strip()
                if _continuation_token:
                    try:
                        async with managed_admin_client() as _ct_client:
                            ct_row = await (
                                _ct_client.table("chat_sessions")
                                .select("id, user_id")
                                .eq("id", _continuation_token)
                                .eq("user_id", _lookup_uid)
                                .maybe_single()
                                .execute()
                            )
                        if ct_row and ct_row.data:
                            prev_sid = ct_row.data["id"]
                            logger.info(
                                "ws handshake: resumed via continuationToken=%s (project=%s)",
                                _continuation_token[:8], project_id,
                            )
                        else:
                            # Explicit token didn't match anything for this
                            # user — treat as a fresh start. Don't fall
                            # through to the heuristic; the client is
                            # explicitly telling us "this session is gone".
                            logger.info(
                                "ws handshake: continuationToken=%s did not match any session for user — fresh start",
                                _continuation_token[:8],
                            )
                    except Exception as _ct_exc:
                        # Lookup failure ≠ missing token. Fall through to the
                        # heuristic so a flaky DB doesn't strand the user.
                        logger.warning(
                            "ws handshake: continuationToken lookup failed (%s) — falling back to heuristic",
                            _ct_exc,
                        )

                async with managed_admin_client() as client:
                    # 1a. Most recent session with generation_complete=True
                    # Guarded by `if not prev_sid` (Phase 2 Step 6): an explicit
                    # continuation token from the handshake takes precedence;
                    # the heuristic tiers run only as a fallback.
                    if not prev_sid:
                        try:
                            completed_r = await (
                                client.table("chat_sessions")
                                .select("id")
                                .eq("user_id", _lookup_uid)
                                .eq("project_id", project_id)
                                .eq("generation_complete", True)
                                .order("created_at", desc=True)
                                .limit(1)
                                .execute()
                            )
                            if completed_r.data:
                                prev_sid = completed_r.data[0]["id"]
                        except Exception:
                            pass  # column may not exist yet

                    # 1b. Most recent session with platform_repo_url set
                    if not prev_sid:
                        try:
                            repo_r = await (
                                client.table("chat_sessions")
                                .select("id")
                                .eq("user_id", _lookup_uid)
                                .eq("project_id", project_id)
                                .not_.is_("platform_repo_url", "null")
                                .order("created_at", desc=True)
                                .limit(1)
                                .execute()
                            )
                            if repo_r.data:
                                prev_sid = repo_r.data[0]["id"]
                        except Exception:
                            pass

                    # 1c. Last resort: plain latest session
                    if not prev_sid:
                        plain_r = await (
                            client.table("chat_sessions")
                            .select("id")
                            .eq("user_id", _lookup_uid)
                            .eq("project_id", project_id)
                            .order("created_at", desc=True)
                            .limit(1)
                            .execute()
                        )
                        if plain_r.data:
                            prev_sid = plain_r.data[0]["id"]

                    # 1d. Legacy fallback — chat_sessions.project_id may be NULL on
                    # rows generated before the landing-pipeline finalisation patch
                    # set it. The frontend's /api/platform-repos route falls back
                    # to using `chat_sessions.id` as the URL projectId in that case.
                    # So when the project_id-keyed lookups all miss, try the row id.
                    # Only when project_id is UUID-shaped: `id` is a UUID column,
                    # and comparing it against a text slug makes PostgREST throw —
                    # which aborted this whole previous-session lookup via the
                    # outer except instead of just skipping this tier.
                    from app.services.pipeline_tenant import UUID_RE as _UUID_RE
                    if not prev_sid and _UUID_RE.match(project_id or ""):
                        id_r = await (
                            client.table("chat_sessions")
                            .select("id, project_id")
                            .eq("user_id", _lookup_uid)
                            .eq("id", project_id)
                            .maybe_single()
                            .execute()
                        )
                        if id_r and id_r.data:
                            prev_sid = id_r.data["id"]
                            # Backfill project_id so future lookups hit 1a-1c
                            # cheaply and the row appears in Recent Projects.
                            if not id_r.data.get("project_id"):
                                try:
                                    await (
                                        client.table("chat_sessions")
                                        .update({"project_id": project_id})
                                        .eq("id", prev_sid)
                                        .execute()
                                    )
                                    logger.info(
                                        "Backfilled project_id=%s on legacy session %s",
                                        project_id, prev_sid,
                                    )
                                except Exception as _bf_err:
                                    logger.debug(
                                        "project_id backfill skipped (%s)", _bf_err,
                                    )

                if prev_sid:

                    # Step 2: try to read optional flag columns (added in later migrations).
                    # Fail silently — if the columns don't exist the flags just stay False/None.
                    platform_repo_url = None
                    generation_complete = False
                    user_repo_url = None
                    user_repo_provider = None
                    try:
                        async with managed_admin_client() as client:
                            flags_r = await (
                                client.table("chat_sessions")
                                .select("platform_repo_url, user_repo_url, user_repo_provider, generation_complete")
                                .eq("id", prev_sid)
                                .maybe_single()
                                .execute()
                            )
                        if flags_r and flags_r.data:
                            platform_repo_url = flags_r.data.get("platform_repo_url")
                            user_repo_url = flags_r.data.get("user_repo_url")
                            user_repo_provider = flags_r.data.get("user_repo_provider")
                            generation_complete = flags_r.data.get("generation_complete", False)
                    except Exception as _flags_err:
                        logger.debug(
                            "Optional flag columns not available for session %s "
                            "(migrations 009/014 may not be applied): %s",
                            prev_sid, _flags_err,
                        )

                    # Step 3: load messages for chat history replay + wizard re-entry detection
                    async with managed_admin_client() as client:
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
                        "user_repo_provider":  user_repo_provider,
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
                if _is_jwt_expired_error(_prev_err):
                    logger.info("[%s] JWT expired during previous-session lookup — closing WS for re-auth", user_id)
                    try:
                        await websocket.close(code=4010, reason="Authentication expired")
                    except Exception:
                        pass
                    return
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

            # Bind the proxy so every chat-relevant event from this point on
            # is mirrored into chat_messages. Safe no-op if proxy is missing
            # or chat_session_id never got assigned (DB create failure above).
            if chat_session_id and session.ws_proxy is not None:
                try:
                    session.ws_proxy.bind_chat(chat_session_id, user_jwt)
                except Exception as _bind_err:
                    logger.warning("Could not bind chat persistence: %s", _bind_err)

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
            # Conditions to skip (all are EVIDENCE of a finished generation):
            #   • platform_repo_url / user_repo_url → project was pushed to a repo
            #   • generation_complete flag  → set by project_generator when done
            #   • real workspace on disk    → generated files survive in PREVIEW_WS_ROOT
            #
            # Wizard re-entry ([LUCID_PROJECT] header + previous messages) is
            # deliberately NOT sufficient on its own: it only proves a
            # generation was *attempted*. If it failed mid-way and left no
            # repo, no complete-flag and no workspace, skipping here strands
            # the user on "Project loaded" with nothing generated — instead
            # we keep the task and let the pipeline resume below.
            _wizard_reentry = (
                task
                and "[LUCID_PROJECT]" in task[:500]
                and _prev_session_data
                and _prev_session_data.get("messages")
            )
            # Tight check: a fully-generated Next.js workspace exists on disk
            # for this project_id — package.json plus a src/ or app/ directory.
            # Both must be present so we never confuse a half-cloned template
            # (package.json only, no source) with a real generated project.
            _has_real_workspace = False
            if project_id:
                try:
                    _candidate_ws = preview_workspace_path(project_id)
                    _has_real_workspace = (
                        os.path.isdir(_candidate_ws)
                        and os.path.isfile(os.path.join(_candidate_ws, "package.json"))
                        and (
                            os.path.isdir(os.path.join(_candidate_ws, "src"))
                            or os.path.isdir(os.path.join(_candidate_ws, "app"))
                        )
                    )
                except Exception:
                    _has_real_workspace = False

            # Diagnostic — exposed so reload bugs are easy to triage from logs.
            logger.info(
                "Reload decision for project=%s: prev_session=%s repo=%s user_repo=%s "
                "complete=%s wizard_reentry=%s real_workspace=%s",
                project_id,
                bool(_prev_session_data),
                bool(_prev_session_data.get("platform_repo_url") if _prev_session_data else False),
                bool(_prev_session_data.get("user_repo_url") if _prev_session_data else False),
                bool(_prev_session_data.get("generation_complete") if _prev_session_data else False),
                _wizard_reentry,
                _has_real_workspace,
            )

            _generation_evidence = bool(
                _prev_session_data
                and (
                    _prev_session_data.get("platform_repo_url")
                    or _prev_session_data.get("user_repo_url")
                    or _prev_session_data.get("generation_complete")
                    or _has_real_workspace
                )
            )

            if _wizard_reentry and not _generation_evidence:
                # Resume path: a generation was started for this project but
                # left no repo, no complete-flag and no workspace — it died
                # mid-way (server restart, crash, push failure with no
                # backup). Keep the task so the pipeline re-runs; research is
                # cached so the re-run does not start from scratch.
                logger.info(
                    "Resuming failed generation for project %s — wizard "
                    "re-entry with no completion evidence; pipeline will re-run.",
                    project_id,
                )
                try:
                    await websocket.send_json({
                        "type": "chat_message",
                        "role": "agent",
                        "content": (
                            "Your previous generation didn't finish — "
                            "picking it back up now."
                        ),
                    })
                except Exception:
                    pass

            if _prev_session_data and _generation_evidence:
                _skip_reason = (
                    "published" if _prev_session_data.get("platform_repo_url")
                    else "generation_complete flag" if _prev_session_data.get("generation_complete")
                    else "workspace files on disk (package.json + src/app)"
                )
                logger.info(
                    "Skipping pipeline for project %s — already generated (%s). "
                    "Clearing task to wait for user input.",
                    project_id, _skip_reason,
                )
                task = ""   # clear task → pipeline won't auto-run
                _skip_workspace_setup = True  # skip clone/install for returning project
                await ws_transition(
                    session, websocket, WorkspaceState.READY,
                    "Project loaded. Ask me to make changes.",
                )
                # Wrap raw send — the WS can disconnect between the two awaits
                # (browser hard-refresh races the workspace-setup awaits). Without
                # this guard, an in-flight close turns the entire session into
                # an "internal error" exception that aborts the auto-launch.
                try:
                    await websocket.send_json({
                        "type": "status",
                        "status": "ready",
                        "sessionId": session.session_id,
                        "message": "Project loaded. Ask me to make changes.",
                    })
                except Exception as _ready_send_err:
                    logger.info(
                        "[%s] ready-status send failed (client likely "
                        "disconnected mid-setup): %s",
                        session.session_id, _ready_send_err,
                    )
                    # Stop here — no point launching the bg_preview task on a
                    # dead WS (it'll just emit into the void).
                    return

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
                                                   "message": "Preview ready"})
                        if session is not None:
                            session.workspace_dir = _cached_tmp
                    except Exception:
                        pass

                if _repo_to_clone or _has_cached_ws or _has_real_workspace:
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
                                               "message": "Preview ready"})
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
                    else:
                        # No live server AND no cached workspace to restart from.
                        # Without this emit, the frontend keeps spinning forever
                        # because the earlier "Restarting preview…" status has
                        # no resolver. Surface a retry button instead.
                        logger.info("reconnect: no cached workspace for %s — surfacing manual retry", _reconnect_conv_id)
                        await websocket.send_json({
                            "type": "preview_error",
                            "error_stage": "no_workspace",
                            "message": "Preview workspace was lost — click Restart Preview to rebuild it.",
                        })
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
        # Cloning and npm install are only useful when the pipeline is about
        # to run. Without a task they waste 3-5 min. When the user submits a
        # task later, run_pipeline does lazy setup.
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
                    "message": "Working on it…",
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
                                "status": "cloning",
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
                        # Token priority — must mirror bg_preview at line ~1144:
                        # platform-owned repos (LucidSoftware-tech/*) MUST use
                        # PLATFORM_GITHUB_TOKEN; the user's personal PAT 404s
                        # against a private org repo it doesn't own. Without
                        # this guard, edit-clone on every generated project
                        # fails with "Repository not found".
                        _repo_url_for_clone = session.repo_url or ""
                        _is_platform_repo = (
                            _PLATFORM_ORG and f"github.com/{_PLATFORM_ORG}/" in _repo_url_for_clone
                        )
                        if _is_platform_repo:
                            _clone_token = (
                                PLATFORM_GITHUB_TOKEN
                                or os.environ.get("PLATFORM_GITHUB_TOKEN", "")
                                or session.git_token
                                or ""
                            )
                        else:
                            _clone_token = session.git_token or ""
                        pre_validated = {
                            "repo_url": _repo_url_for_clone,
                            "branch": session.branch or "main",
                            "git_token": _clone_token,
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
                                    "message": "Couldn't install dependencies — preview may not work.",
                                })
                                logger.warning(
                                    "%s install failed (non-fatal) for project %s: %s",
                                    _pm, project_id, _err,
                                )
                            else:
                                await websocket.send_json({
                                    "type": "progress",
                                    "message": "Dependencies installed.",
                                })
                                logger.info(
                                    "%s install succeeded for project %s", _pm, project_id
                                )
                        except _subprocess.TimeoutExpired:
                            await websocket.send_json({
                                "type": "warning",
                                "message": "Dependency install timed out — preview may not work.",
                            })
                            logger.warning(
                                "%s install timed out (subprocess) for project %s", _pm, project_id
                            )
                        except (asyncio.TimeoutError, TimeoutError):
                            await websocket.send_json({
                                "type": "warning",
                                "message": "Dependency install timed out — preview may not work.",
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
                        "message": "Couldn't clone the repository — preview is limited but you can still send tasks.",
                    })

            if not clone_fatal and not task:
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
            # Dedup guard: the SAME handshake task arrives again whenever the
            # page is refreshed mid-generation (sessionStorage re-sends the
            # wizard prompt) or the failed-generation resume path keeps the
            # task. Persisting it again creates a second identical UserTask
            # row, which the chat_history replay then renders as a doubled
            # user bubble.
            if chat_session_id:
                try:
                    _last_user = await ChatService.last_message(
                        session_id=chat_session_id, role="user",
                        user_jwt=user_jwt,
                    )
                    if (
                        _last_user
                        and (_last_user.get("content") or "").strip() == task.strip()
                    ):
                        logger.info(
                            "Skipping duplicate UserTask persist for chat %s "
                            "(handshake re-sent the same task)",
                            chat_session_id,
                        )
                    else:
                        await ChatService.add_message(
                            session_id=chat_session_id, role="user",
                            content=task, event_type="UserTask",
                            user_jwt=user_jwt,
                        )
                except Exception as exc:
                    logger.warning("Failed to persist user message: %s", exc)

            await emit_agent_status(
                websocket,
                key="analyzing_prompt",
                label="Analyzing your prompt...",
                description="Gemini Flash is deciding what to do next",
                source="gemini_flash",
            )

            # ── Ambiguity gate: ask before generating if prompt is vague ──
            # Stage 0: Gemini-powered clarity check — generates a targeted
            # question when key context is missing (location, project type,
            # audience). Falls back to the keyword-based conflict detector
            # for known archetype conflicts (landing vs ecommerce, etc.).
            # The canonical classifier runs on every intake turn and remains
            # in clarification until Gemini identifies enough explicit scope.
            # The legacy fallback keeps its historical round cap.
            #
            # When ``settings.USE_CLASSIFIER_AGENT`` is True, the new
            # ``project_classifier_agent.resolve_classification`` is used
            # instead. It wraps ``check_prompt_clarity`` AND additionally:
            #   • resolves a concrete archetype when the prompt is clear
            #   • injects a ``[LUCID_FORCE_ARCHETYPE::...]`` marker into
            #     the task so the downstream classifier short-circuits
            #   • runs entity extraction for admin paths
            # Same WS event (``clarification_needed``) is reused — no
            # frontend changes required.
            import json as _json
            from knowledge.loader import (
                detect_classification_conflict, extract_clarify_context,
                ARCHETYPE_LOCK_PREFIX,
            )
            from app.services.clarity_agent import check_prompt_clarity
            from app.config import settings as _settings

            _existing_clarify, _ = extract_clarify_context(task)
            _clarify_question: dict | None = None
            _selected_workflow = "new_project_generation"
            _classifier_decided = False

            if _settings.USE_CLASSIFIER_AGENT:
                # New path — let the resolver return either a clarification
                # question or a fully-resolved archetype. On any exception
                # we fall through to the legacy check_prompt_clarity path
                # to keep behavior safe during rollout.
                try:
                    from app.services.project_classifier_agent import (
                        resolve_classification,
                    )
                    _resolution = await resolve_classification(task)
                    _classifier_decided = True
                    _selected_workflow = str(
                        _resolution.get("workflow_id") or _selected_workflow
                    )
                    await emit_workflow_selected(
                        websocket,
                        workflow_id=_selected_workflow,
                        next_action=str(_resolution.get("next_action") or "clarify"),
                        decided_by=str(_resolution.get("decided_by") or "agent"),
                        archetype=str(_resolution.get("archetype") or ""),
                        reasoning=str(_resolution.get("reasoning") or ""),
                    )
                    if _resolution.get("status") == "needs_clarification":
                        _clarify_question = {
                            "key": _resolution.get("clarify_key", "") or "",
                            "text": _resolution.get("question", ""),
                            "options": _resolution.get("options", []),
                        }
                    elif _resolution.get("status") == "resolved":
                        _arch = _resolution.get("archetype", "")
                        if _arch:
                            # Inject the force-archetype marker so the
                            # downstream classifier (and project_generator)
                            # skip their own classification step. Existing
                            # infra: knowledge.loader.force_archetype_from_task.
                            task = _insert_marker_preserving_lucid_project(
                                f"{ARCHETYPE_LOCK_PREFIX}{_arch}]",
                                task,
                            )
                            logger.info(
                                "[%s] classifier_agent resolved → archetype=%s "
                                "followup=%s entities=%s reasoning=%r",
                                getattr(session, "session_id", "?"),
                                _arch,
                                _resolution.get("needs_admin_followup"),
                                _resolution.get("entities"),
                                str(_resolution.get("reasoning", ""))[:80],
                            )
                        # _clarify_question stays None → proceeds to generation
                except Exception as exc:
                    logger.warning(
                        "[%s] classifier_agent failed (%s) — falling back to legacy clarity_agent",
                        getattr(session, "session_id", "?"), exc,
                    )

            # Legacy path (also fallback for the new path's failures).
            # Only run AI clarity check for new/initial prompts (not follow-ups)
            # and only when fewer than 3 rounds have been used and the new
            # flow hasn't already produced a question.
            if _clarify_question is None and not _settings.USE_CLASSIFIER_AGENT and len(_existing_clarify) < 5:
                _clarify_question = await check_prompt_clarity(
                    task=task,
                    already_clarified=_existing_clarify,
                    timeout_s=22.0,
                    websocket=websocket,
                )

            # If AI clarity check passed (or skipped), fall back to keyword detector
            if _clarify_question is None and not _classifier_decided:
                _kw_conflict = detect_classification_conflict(task)
                if _kw_conflict:
                    _clarify_question = {
                        "key": "",  # empty key → legacy archetype-lock path
                        "text": _kw_conflict["question"],
                        "options": _kw_conflict["options"],
                        "_kind": _kw_conflict["kind"],
                    }

            if _clarify_question is not None:
                _is_legacy = not _clarify_question.get("key")
                _payload = {
                    "kind": _clarify_question.get("_kind") or "intent_clarify",
                    "clarify_key": _clarify_question.get("key") or "",
                    "question": _clarify_question.get("text") or _clarify_question.get("question") or "",
                    "options": _clarify_question.get("options", []),
                    "original_task": task,
                }
                if chat_session_id:
                    try:
                        await ChatService.add_message(
                            session_id=chat_session_id, role="agent",
                            content=_json.dumps(_payload),
                            event_type="ClarificationNeeded",
                            user_jwt=user_jwt,
                        )
                    except Exception as exc:
                        logger.warning("Failed to persist clarification message: %s", exc)
                try:
                    await websocket.send_json({
                        "type": "clarification_needed", **_payload,
                    })
                except Exception:
                    pass
                await emit_agent_status(
                    websocket,
                    key="waiting_for_details",
                    label="Waiting for project details...",
                    description="Answer the question in chat to continue",
                    state="waiting",
                    source="gemini_flash",
                    workflow_id="project_clarification",
                )
                await ws_transition(
                    session, websocket, WorkspaceState.READY,
                    "Awaiting your choice…",
                )
                logger.info(
                    "[%s] Clarity check — asking about %r (round %d)",
                    session.session_id, _clarify_question.get("key") or "archetype",
                    len(_existing_clarify) + 1,
                )
            else:
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
                # Phase 1 — covers everything from here through cloning the
                # template, classifying the project, and analysing intent.
                # Holding it as ACTIVE prevents the UI from flickering through
                # 5+ short status messages before research begins.
                await emit_task_phase(
                    websocket,
                    phase=1,
                    title="Preparing workspace",
                    description="Understanding the prompt, cloning template, classifying project…",
                    status="active",
                    mode=str(
                        _selected_workflow
                    ),
                )

                # ── Build enriched task + run pipeline via orchestrator ──
                enriched_task = await build_enriched_task(task, session, project_id, user_id, user_jwt)
                pipeline_user = build_pipeline_user(
                    session,
                    api_key,
                    user_package_manager,
                    user_jwt,
                    openai_api_key=openai_api_key,
                    openai_model=openai_model,
                )
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
            # Click-to-edit payload from the preview iframe — when present the
            # pipeline synthesizes a 100%-confidence EditIntent directly
            # instead of running the workspace-grounded extractor.
            followup_editable_target = data.get("editable_target")

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
                await stop_local_preview(conversation_id=conversation_id or "")
                await websocket.send_json({"type": "preview_stopped"})
                logger.info("Preview server stopped on user request")
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
                from app.services.plan_store import (
                    resolve_plan_confirmation, _confirmation_key,
                    pending_plan_confirmations, clear_persisted_plan,
                )
                _key = _confirmation_key(websocket, chat_session_id or "")
                if _key in pending_plan_confirmations:
                    logger.info("[%s] Plan confirmed by user", getattr(session, "session_id", "?"))
                    resolve_plan_confirmation(_key, {"confirmed": True})
                else:
                    # No live Future — the generator coroutine that was awaiting
                    # this confirmation is gone (ai_engine restarted, or the gate
                    # already timed out). Persistence only stores the plan
                    # envelope, not the inputs needed to resume generation, so
                    # we can't transparently continue. Surface a clear message
                    # and clear the stale plan so we don't keep re-emitting it.
                    logger.warning(
                        "[%s] plan_confirm received but no pending Future for key=%s — "
                        "generator coroutine was likely terminated (restart/timeout)",
                        getattr(session, "session_id", "?"), _key,
                    )
                    await clear_persisted_plan(chat_session_id or "")
                    try:
                        await websocket.send_json({
                            "type": "warning",
                            "message": (
                                "This plan can no longer be resumed (the server "
                                "restarted or the plan expired). Please send your "
                                "task again — research is cached, so it will be quick."
                            ),
                        })
                    except Exception:
                        pass
                continue

            if msg_type == "plan_reject":
                from app.services.plan_store import (
                    resolve_plan_confirmation, _confirmation_key,
                    pending_plan_confirmations, clear_persisted_plan,
                )
                correction = data.get("correction", "")
                _key = _confirmation_key(websocket, chat_session_id or "")
                if _key in pending_plan_confirmations:
                    logger.info(
                        "[%s] Plan rejected by user — correction: %s",
                        getattr(session, "session_id", "?"),
                        correction[:80],
                    )
                    resolve_plan_confirmation(_key, {
                        "confirmed": False,
                        "correction": correction,
                    })
                else:
                    logger.warning(
                        "[%s] plan_reject received but no pending Future for key=%s — "
                        "ignoring (generator coroutine is gone)",
                        getattr(session, "session_id", "?"), _key,
                    )
                    await clear_persisted_plan(chat_session_id or "")
                    try:
                        await websocket.send_json({
                            "type": "warning",
                            "message": (
                                "This plan can no longer be modified (the server "
                                "restarted or the plan expired). Please send your "
                                "task again with the correction included."
                            ),
                        })
                    except Exception:
                        pass
                continue

            # ── Clarification response — user answered an archetype question ──
            # Sent in reply to a clarification_needed event. We prepend the
            # internal lock marker to the original task so the classifier
            # routes correctly, then dispatch the same execute_task flow
            # used for fresh handshake tasks.
            if msg_type == "clarification_response":
                from knowledge.loader import (
                    ARCHETYPE_LOCK_PREFIX, LAYOUT_ARCHETYPES,
                    format_clarify_marker,
                )
                _kind = (data.get("kind") or "").strip()
                _clarify_key = (data.get("clarify_key") or "").strip()
                _option_id = (data.get("archetype") or data.get("id") or "").strip()
                _option_label = str(data.get("option_label") or _option_id).strip()
                _original = (data.get("task") or data.get("original_task") or "").strip()

                # Two clarification flavours share this handler:
                #   (1) Legacy archetype-conflict — option_id must be a
                #       known LAYOUT_ARCHETYPES value, locked via
                #       [LUCID_FORCE_ARCHETYPE::xxx].
                #   (2) New Stage-0 intent clarifier — option_id is an
                #       arbitrary snake_case identifier from the question,
                #       locked via [LUCID_CLARIFY::key=value]. Multiple
                #       keys can stack across rounds; each round prepends
                #       one more marker to the original task.
                _is_intent_clarify = _kind == "intent_clarify" or bool(_clarify_key)

                if _is_intent_clarify:
                    if not _clarify_key or not _option_id or not _original:
                        logger.warning(
                            "[%s] Bad intent clarification_response (key=%r id=%r task_len=%d)",
                            getattr(session, "session_id", "?"),
                            _clarify_key, _option_id, len(_original),
                        )
                        try:
                            await websocket.send_json({
                                "type": "warning",
                                "message": "Clarification response was malformed — please try again.",
                            })
                        except Exception:
                            pass
                        continue
                    _marker = format_clarify_marker(_clarify_key, _option_id)
                    _locked_task = _insert_marker_preserving_lucid_project(
                        _marker,
                        _original,
                    )
                    if _option_label:
                        _locked_task = (
                            f"{_locked_task}\n\n"
                            f"Clarification answer — {_clarify_key}: {_option_label}"
                        ).strip()
                else:
                    if _option_id not in LAYOUT_ARCHETYPES or not _original:
                        logger.warning(
                            "[%s] Bad clarification_response (archetype=%r task_len=%d)",
                            getattr(session, "session_id", "?"),
                            _option_id, len(_original),
                        )
                        try:
                            await websocket.send_json({
                                "type": "warning",
                                "message": "Clarification response was malformed — please try again.",
                            })
                        except Exception:
                            pass
                        continue
                    _locked_task = _insert_marker_preserving_lucid_project(
                        f"{ARCHETYPE_LOCK_PREFIX}{_option_id}]",
                        _original,
                    )

                if chat_session_id:
                    try:
                        await ChatService.add_message(
                            session_id=chat_session_id, role="user",
                            content=str(_option_label),
                            event_type="ClarificationResponse",
                            user_jwt=user_jwt,
                        )
                    except Exception as exc:
                        logger.warning("Failed to persist clarification reply: %s", exc)

                logger.info(
                    "[%s] User answered clarification (%s=%s) — resuming pipeline",
                    getattr(session, "session_id", "?"),
                    _clarify_key or "archetype", _option_id,
                )

                await emit_agent_status(
                    websocket,
                    key="analyzing_clarification",
                    label="Analyzing your clarification...",
                    description="Choosing the project workflow",
                    source="gemini_flash",
                    workflow_id="project_clarification",
                )
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
                    "content": f"Agent starting task: {_original}",
                })
                await websocket.send_json({
                    "type": "step", "step": "understanding",
                    "label": "Understanding the project", "done": True,
                })

                enriched_task = await build_enriched_task(
                    _locked_task, session, project_id, user_id, user_jwt,
                )
                pipeline_user = build_pipeline_user(
                    session,
                    api_key,
                    user_package_manager,
                    user_jwt,
                    openai_api_key=openai_api_key,
                    openai_model=openai_model,
                )
                _task_result: TaskResult = await agent_orchestrator.execute_task(
                    enriched_task=enriched_task,
                    session=session,
                    websocket=websocket,
                    pipeline_user=pipeline_user,
                    chat_session_id=chat_session_id or "",
                    conversation_id=conversation_id,
                    user_jwt=user_jwt,
                    task=_locked_task,
                    images=[],
                )
                if _task_result.stopped:
                    explicit_stop = True
                    break
                continue

            if msg_type == "manual_edit":
                patch = data.get("patch")
                target = data.get("editable_target")
                workspace_path = session.workspace_dir if session else ""
                try:
                    from app.services.manual_edit import apply_manual_content_edit

                    result = apply_manual_content_edit(
                        workspace_path=workspace_path,
                        editable_target=target if isinstance(target, dict) else None,
                        patch=patch if isinstance(patch, dict) else None,
                    )
                    if result.get("ok"):
                        rel_path = result.get("rel_path") or ""
                        target_path = ""
                        if isinstance(patch, dict):
                            target_path = str(patch.get("path") or "").strip()
                        payload = {
                            "type": "file_write_event",
                            "filename": rel_path,
                            "action": "edit",
                            "phase": "manual_edit",
                        }
                        content_out = result.get("content")
                        if isinstance(content_out, str):
                            payload["content"] = content_out
                            payload["size"] = len(content_out.encode("utf-8"))
                        await websocket.send_json(payload)
                        await websocket.send_json({
                            "type": "progress",
                            "message": result.get("message") or "Manual edit saved.",
                        })
                        try:
                            await websocket.send_json({
                                "type": "progress",
                                "message": "Auto-pushing manual edit to staging...",
                            })
                            sync_result = await sync_workspace_to_staging(
                                project_id=project_id,
                                user_id=user_id,
                                user_jwt=user_jwt,
                                workspace_path=workspace_path,
                                commit_message=(
                                    f"Manual edit: {target_path}"
                                    if target_path else
                                    "Manual edit via Lucid AI"
                                ),
                            )
                            await websocket.send_json({
                                "type": "staging_push_result",
                                "pushed": bool(sync_result.get("changed")),
                                "branch": sync_result.get("branch") or "staging",
                                "repoUrl": sync_result.get("repoUrl") or "",
                                "filesPushed": sync_result.get("filesPushed") or 0,
                                "message": sync_result.get("message") or "Staging is up to date.",
                            })
                        except StagingSyncError as sync_exc:
                            logger.info(
                                "manual_edit staging sync skipped (%s): %s",
                                sync_exc.status_code,
                                sync_exc.detail,
                            )
                            await websocket.send_json({
                                "type": "warning",
                                "code": "STAGING_SYNC_SKIPPED",
                                "message": sync_exc.detail,
                            })
                    else:
                        await websocket.send_json({
                            "type": "warning",
                            "code": "MANUAL_EDIT_UNSAVED",
                            "message": result.get("message") or "Manual edit changed preview only.",
                        })
                except Exception as manual_exc:
                    logger.warning("manual_edit failed: %s", manual_exc, exc_info=True)
                    try:
                        await websocket.send_json({
                            "type": "warning",
                            "code": "MANUAL_EDIT_FAILED",
                            "message": f"Manual edit changed preview only: {str(manual_exc)[:120]}",
                        })
                    except Exception:
                        pass
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
                            force_restart=True,
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
            _has_visual_edit_context = bool(followup_images) or bool(followup_editable_target)
            _is_scan_intent = any(kw in _content_lower for kw in (
                "scan for bugs", "scan my code", "find bugs", "check for bugs",
                "run quality", "quality check", "code review", "audit the code",
                "check code quality",
            ))
            _is_fix_intent = (
                not _has_visual_edit_context
                and any(kw in _content_lower for kw in (
                    "fix the bugs", "fix the issues", "fix bugs", "apply the fixes",
                    "auto fix", "auto-fix", "fix all bugs", "fix the errors",
                    "fix the error", "fix this error", "fix the issue",
                    "fix this issue",
                ))
            )
            # Detect pasted error traces: if the message looks like a build/runtime
            # error (contains Error:, at line, Cannot find module, etc.) treat it
            # as a fix request automatically — no keyword needed.
            _looks_like_error = (
                not _is_fix_intent
                and not _has_visual_edit_context
                and session.workspace_dir
                and any(sig in _content_lower for sig in (
                    "error:", "error ts", "module not found", "cannot find module",
                    "syntaxerror", "typeerror", "referenceerror", "failed to compile",
                    "webpack error", "build failed", "enoent", "unhandled error",
                    "uncaught ", "at object.", "at module.",
                ))
            )
            if _looks_like_error:
                _is_fix_intent = True

            if (_is_scan_intent or _is_fix_intent) and session.workspace_dir and api_key:
                workspace_path = session.workspace_dir
                _do_fix = _is_fix_intent
                _ws_out = session.ws_proxy if session and session.ws_proxy is not None else websocket
                try:
                    from app.services.auto_bug_fixer import scan_and_report_bugs
                    _intro = (
                        "🔧 **Running bug scan and applying fixes…**"
                        if _do_fix
                        else "🔍 **Running code quality scan…**"
                    )
                    if chat_session_id:
                        try:
                            await ChatService.add_message(
                                session_id=chat_session_id,
                                role="user",
                                content=content,
                                event_type="UserTask",
                                user_jwt=user_jwt,
                            )
                        except Exception as exc:
                            logger.warning("Failed to persist bug-scan request: %s", exc)
                    await _ws_out.send_json({
                        "type": "chat_message",
                        "role": "agent",
                        "content": _intro,
                    })
                    await scan_and_report_bugs(
                        workspace_path=workspace_path,
                        api_key=api_key,
                        websocket=_ws_out,
                        auto_fix=_do_fix,
                    )
                except Exception as _scan_exc:
                    logger.warning("On-demand bug scan failed: %s", _scan_exc)
                    await _ws_out.send_json({
                        "type": "chat_message",
                        "role": "agent",
                        "content": f"⚠️ Bug scan failed: {str(_scan_exc)[:120]}",
                    })
                continue

            await emit_agent_status(
                websocket,
                key="analyzing_prompt",
                label="Analyzing your prompt...",
                description="Gemini Flash is deciding what to do next",
                source="gemini_flash",
            )

            # ── Follow-up intent guard ─────────────────────────────
            # Existing-workspace chat is not the same as new-project intake:
            # users can ask questions, greet the agent, or type vague edits.
            # Catch the obvious non-actionable cases before running the full
            # edit pipeline. Real edits still pass through to Gemini/Claude.
            try:
                from app.services.followup_intent import classify_followup_with_gemini

                _followup_guard = await classify_followup_with_gemini(
                    content,
                    mode=chat_mode,
                    has_images=bool(followup_images),
                    editable_target=followup_editable_target,
                )
            except Exception as _guard_exc:
                logger.warning("followup_intent guard failed: %s — proceeding", _guard_exc)
                _followup_guard = {"action": "proceed"}

            _guard_action = _followup_guard.get("action", "proceed")
            await emit_workflow_selected(
                websocket,
                workflow_id=str(_followup_guard.get("workflow_id") or _guard_action),
                next_action=str(_guard_action),
                decided_by=str(_followup_guard.get("decided_by") or "agent"),
                reasoning=str(_followup_guard.get("reasoning") or ""),
            )
            if _guard_action in ("reply", "clarify"):
                _guard_message = (
                    _followup_guard.get("message")
                    or "What would you like to change or ask about this project?"
                )
                if chat_session_id:
                    try:
                        await ChatService.add_message(
                            session_id=chat_session_id, role="user",
                            content=content, event_type="UserTask",
                            user_jwt=user_jwt,
                        )
                        await ChatService.add_message(
                            session_id=chat_session_id, role="agent",
                            content=_guard_message,
                            event_type=(
                                "ClarificationNeeded"
                                if _guard_action == "clarify"
                                else "AgentResponse"
                            ),
                            user_jwt=user_jwt,
                        )
                    except Exception as exc:
                        logger.warning("Failed to persist follow-up guard message: %s", exc)
                try:
                    await websocket.send_json({
                        "type": "chat_message",
                        "role": "agent",
                        "content": _guard_message,
                    })
                    # The guard short-circuits the pipeline (no task is
                    # actually running), so the FE's optimistic state=
                    # 'running' must be released — otherwise "Analyzing
                    # your request…" sticks forever and the user can't
                    # send the next prompt.
                    await websocket.send_json({"type": "status", "status": "ready"})
                except Exception:
                    pass
                await emit_agent_status(
                    websocket,
                    key="waiting_for_details" if _guard_action == "clarify" else "ready_for_prompt",
                    label=(
                        "Waiting for project details..."
                        if _guard_action == "clarify"
                        else "Ready for your next instruction..."
                    ),
                    description=_guard_message,
                    state="waiting",
                    source="prompt_router",
                    workflow_id=_guard_action,
                )
                continue

            if _guard_action == "discuss":
                chat_mode = "discuss"

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
            pipeline_user = build_pipeline_user(
                session,
                api_key,
                user_package_manager,
                user_jwt,
                openai_api_key=openai_api_key,
                openai_model=openai_model,
            )
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
                editable_target=followup_editable_target,
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
        try:
            _RECONNECT_GRACE_SECONDS = int(
                os.environ.get("LUCID_RECONNECT_GRACE_SECONDS", "900")
            )
        except ValueError:
            _RECONNECT_GRACE_SECONDS = 900
        # Default 15 minutes to reconnect before aborting when Redis is unavailable.
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

            # Stop the local dev preview server (best-effort cleanup)
            try:
                await stop_local_preview(conversation_id=conversation_id or "")
            except Exception as exc:
                logger.debug("Preview server cleanup error (ok): %s", exc)

            # Keep workspaces for projects with persisted state — the user
            # might re-enter the workspace (browser refresh, return from
            # another tab) and expect the preview + file tree to still work.
            # Without this guard, every disconnect wiped the project files,
            # so re-entry hit "Internal Server Error" because next dev was
            # booting in an empty directory. The 2h TTL reaper still cleans
            # truly-stale workspaces in the background.
            _has_persisted_project = bool(
                session and getattr(session, "project_id", "") and chat_session_id
            )
            if _has_persisted_project:
                logger.info(
                    "Workspace kept for conversation %s — project %s persisted; reaper will clean on TTL",
                    conversation_id, session.project_id,
                )
            else:
                try:
                    await workspace_manager.destroy_workspace(conversation_id)
                    logger.info("Workspace destroyed for conversation %s", conversation_id)
                except Exception as exc:
                    logger.error("Failed to destroy workspace for conversation %s: %s", conversation_id, exc)

            # Cancel any pending plan confirmation Future ONLY when the pipeline
            # is not running. If the pipeline IS still running (and we kept the
            # workspace alive above), leave the future intact so a reconnected
            # websocket can still resolve it via plan_confirm / plan_reject.
            try:
                from app.services.plan_store import (
                    pending_plan_confirmations, _confirmation_key,
                )
                _key = _confirmation_key(websocket, chat_session_id or "")
                fut = pending_plan_confirmations.pop(_key, None)
                if fut and not fut.done():
                    fut.cancel()
            except Exception:
                pass

        logger.info("WebSocket session cleaned up")




