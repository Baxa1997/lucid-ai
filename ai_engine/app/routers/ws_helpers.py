"""Standalone helpers for the agent WebSocket router.

Extracted from ws.py (god-module split): everything here is a
module-level function with explicit parameters — no shared state with
websocket_agent beyond what is passed in. ws.py re-imports these, so
behavior and call sites are unchanged.
"""

from __future__ import annotations

import asyncio

from fastapi import WebSocket, WebSocketDisconnect

from app.config import MOCK_STEP_DELAY_SECONDS, logger
from app.events import now_iso
from app.services.sessions import AgentSession
from app.services.vcs.git import get_git_status, push_changes


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
