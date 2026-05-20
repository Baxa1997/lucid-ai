"""Workspace path resolver — determines which of the 3 initialization paths applies.

Called during the RESOLVING workspace state, BEFORE any cloning.
Every step emits structured WebSocket events so the frontend always shows
meaningful progress instead of a blank screen.

────────────────────────────────────────────────────────
Path A  new_project    [LUCID_PROJECT] header in task →
                       wizard-initiated project, pick template + stack

Path B  existing_repo  session.repo_url is set, OR a platform_repo_url is
                       found in the DB for this project_id →
                       clone the repo the user (or the platform) already owns

Path C  conversation   no task header, no repo →
                       scratch / chat mode, empty workspace
────────────────────────────────────────────────────────

Usage (from ws.py, inside the RESOLVING state):

    result = await resolve_workspace_path(
        task=task,
        session=session,
        websocket=websocket,
        project_id=project_id,
        user_jwt=user_jwt,
    )
    # session.repo_url is patched in-place when Path B is recovered from DB
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import WebSocket
    from app.services.sessions import AgentSession

logger = logging.getLogger(__name__)


# ── Path constants ───────────────────────────────────────────────────────────

class ResolvePath:
    NEW_PROJECT   = "new_project"    # Path A: wizard, pick template
    EXISTING_REPO = "existing_repo"  # Path B: clone user/platform repo
    CONVERSATION  = "conversation"   # Path C: scratch / chat mode


# Display names for known template stacks
_TEMPLATE_NAMES: dict[str, str] = {
    "nextjs":          "Next.js",
    "nextjs-website":  "Next.js",
    "react":           "React",
    "react-admin":     "React + Vite (Admin)",
    "vue":             "Vue",
    "vue-admin":       "Vue",
}


# Heuristic admin-intent detection. Runs on the user's project description
# before the real classifier fires (which happens later in the pipeline).
# When this hits, the resolver overrides "Next.js" / "Auto" template
# display with "React + Vite (Admin)" so the loading screen matches what
# the admin_pipeline (Stage 5 onwards) will actually write to disk.
#
# Source: kept narrow on purpose — false positives here are worse than
# false negatives. We only override on UNAMBIGUOUS admin phrasing.
_ADMIN_INTENT_PATTERNS = (
    r"\badmin\s+(?:panel|dashboard|tool|interface|portal)\b",
    r"\bcrm\b",
    r"\btms\b",  # transport management system
    r"\berp\b",
    r"\bcms\b",
    r"\binternal\s+(?:tool|app|dashboard|admin)\b",
    r"\bback[\s-]?office\b",
    r"\bsaas\s+dashboard\b",
    r"\boperations?\s+dashboard\b",
    r"\bmanagement\s+(?:system|panel|dashboard|tool)\b",
    r"\binventory\s+management\b",
    r"\blead\s+management\b",
    r"\bcustomer\s+management\b",
)
_ADMIN_INTENT_RE = re.compile("|".join(_ADMIN_INTENT_PATTERNS), re.IGNORECASE)


def _looks_like_admin(description: str) -> bool:
    """Return True if the description unambiguously signals an admin tool.

    Pure regex match — fast (~µs), no LLM call. The real classifier
    runs later in the pipeline and is authoritative; this is just for
    making the loading-screen message accurate while the user waits.
    """
    if not description:
        return False
    return bool(_ADMIN_INTENT_RE.search(description))


# ── Result dataclass ─────────────────────────────────────────────────────────

@dataclass
class ResolveResult:
    """What the resolver decided, plus display-ready strings for the loading screen."""

    path: str                    # ResolvePath constant

    # Human-readable strings for the loading screen
    message: str = ""            # headline: "Setting up your Next.js project..."
    detail:  str = ""            # subtext / secondary line

    # Path A fields
    stack:         str = ""      # e.g. "nextjs"
    template_name: str = ""      # e.g. "Next.js" (display name)
    description:   str = ""      # project description from header

    # Path B fields
    repo_display:  str = ""      # sanitized URL without token, for display
    branch:        str = "main"


# ── Helpers ──────────────────────────────────────────────────────────────────

def _sanitize_repo_url(url: str, token: str = "") -> str:
    """Strip token from a clone URL so it's safe to display."""
    if not url:
        return ""
    if token and f"{token}@" in url:
        url = url.replace(f"https://{token}@", "https://")
    # Remove any user:token@ pattern
    url = re.sub(r"https://[^@]+@", "https://", url)
    if url.endswith(".git"):
        url = url[:-4]
    return url


async def _emit(websocket: "WebSocket", msg_type: str, **data) -> None:
    """Send a single event to the frontend, swallowing send errors."""
    try:
        await websocket.send_json({"type": msg_type, **data})
    except Exception as exc:
        logger.warning("resolver: failed to send %s event: %s", msg_type, exc)


async def _progress(websocket: "WebSocket", message: str, pct: int = 0) -> None:
    """Emit a resolving_progress event."""
    await _emit(websocket, "resolving_progress", message=message, pct=pct)


# ── Main resolver ─────────────────────────────────────────────────────────────

async def resolve_workspace_path(
    *,
    task: str,
    session: "AgentSession",
    websocket: "WebSocket",
    project_id: str = "",
    user_jwt: str | None = None,
) -> ResolveResult:
    """Determine the workspace initialization path and emit progress events.

    Side-effect: may update ``session.repo_url`` (and related fields) when
    Path B is recovered from the Supabase ``chat_sessions`` table.

    Returns a :class:`ResolveResult` describing the chosen path and
    display-ready strings the frontend loading screen can use directly.
    """

    # ── Path A: [LUCID_PROJECT] wizard header ────────────────────────────────
    task_str = str(task or "")
    header_line = ""
    for ln in task_str[:2000].split("\n"):
        if "[LUCID_PROJECT]" in ln:
            header_line = ln.strip()
            break

    if header_line:
        # Before starting a new project, check if this project was already
        # successfully created. If platform_repo_url exists in the DB, the
        # generation succeeded previously — use the existing repo (Path B)
        # instead of creating a new one. This handles re-entry after the
        # wizard task survived in sessionStorage beyond a successful run.
        if project_id:
            recovered = await _try_recover_repo_from_db(
                project_id=project_id,
                session=session,
                websocket=websocket,
            )
            if recovered:
                logger.info(
                    "resolver: [LUCID_PROJECT] task found but project already "
                    "exists — using existing repo (Path B) instead of Path A"
                )
                return recovered

        return await _resolve_new_project(
            header_line=header_line,
            session=session,
            websocket=websocket,
        )

    # ── Path B (direct): repo_url already on session ─────────────────────────
    if session.repo_url:
        return await _resolve_existing_repo(
            repo_url=session.repo_url,
            branch=session.branch or "main",
            git_token=session.git_token or "",
            session=session,
            websocket=websocket,
            source="handshake",
        )

    # ── Path B (DB recovery): check Supabase for a previously created repo ───
    if project_id:
        recovered = await _try_recover_repo_from_db(
            project_id=project_id,
            session=session,
            websocket=websocket,
        )
        if recovered:
            return recovered

    # ── Path C: no repo, no wizard header — scratch / chat mode ─────────────
    return await _resolve_conversation(session=session, websocket=websocket)


# ── Path A ───────────────────────────────────────────────────────────────────

async def _resolve_new_project(
    *,
    header_line: str,
    session: "AgentSession",
    websocket: "WebSocket",
) -> ResolveResult:
    def _hdr(field_name: str) -> str:
        m = re.search(rf"{field_name}=([^|]+)", header_line)
        return m.group(1).strip() if m else ""

    stack       = _hdr("stack")
    description = _hdr("description")

    # If the user picked "Choose for me" (stack=auto) or the default
    # stack=nextjs, run a quick admin-intent heuristic. When the
    # description unambiguously describes an admin tool, override the
    # display template so the loading screen reflects what the
    # admin_pipeline will actually write to disk (React + Vite + AuthGuard
    # + CRUD pages), not the wizard's nextjs default.
    is_default_stack = stack.lower() in ("", "auto", "nextjs", "nextjs-website")
    if is_default_stack and _looks_like_admin(description):
        effective_stack = "react-admin"
        logger.info(
            "resolver: detected admin intent in description — overriding "
            "template display (was %r → react-admin)", stack or "auto",
        )
    else:
        effective_stack = stack

    template = _TEMPLATE_NAMES.get(
        effective_stack.lower(),
        effective_stack.capitalize() or "Next.js",
    )

    logger.info(
        "resolver: Path A — new project (stack=%s, effective=%s, desc=%s)",
        stack, effective_stack, description[:60],
    )

    await _progress(websocket, "Analyzing your project description...", pct=10)
    await _progress(websocket, f"Selecting template: {template}...", pct=40)
    await _progress(websocket, f"Preparing {template} workspace...", pct=70)

    result = ResolveResult(
        path=ResolvePath.NEW_PROJECT,
        message=f"Setting up your {template} project...",
        detail=f"Template: {template} · Stack: {effective_stack or 'nextjs'}",
        stack=effective_stack,
        template_name=template,
        description=description,
    )

    await _emit(
        websocket, "resolving_info",
        path=result.path,
        message=result.message,
        detail=result.detail,
        stack=result.stack,
        templateName=result.template_name,
        description=result.description,
    )
    logger.info("resolver: Path A complete — %s", result.message)
    return result


# ── Path B ───────────────────────────────────────────────────────────────────

async def _resolve_existing_repo(
    *,
    repo_url: str,
    branch: str,
    git_token: str,
    session: "AgentSession",
    websocket: "WebSocket",
    source: str = "handshake",
) -> ResolveResult:
    display_url = _sanitize_repo_url(repo_url, git_token)

    # Extract a short repo name for display: "github.com/user/repo"
    repo_name = display_url.replace("https://", "").replace("http://", "")

    logger.info("resolver: Path B — existing repo (%s) via %s", repo_name, source)

    await _progress(websocket, f"Found repository: {repo_name}", pct=30)
    await _progress(websocket, "Preparing to clone...", pct=60)

    result = ResolveResult(
        path=ResolvePath.EXISTING_REPO,
        message=f"Cloning {repo_name}...",
        detail=f"Branch: {branch}",
        repo_display=repo_name,
        branch=branch,
    )

    await _emit(
        websocket, "resolving_info",
        path=result.path,
        message=result.message,
        detail=result.detail,
        repoDisplay=result.repo_display,
        branch=result.branch,
    )
    logger.info("resolver: Path B complete — %s", result.message)
    return result


async def _try_recover_repo_from_db(
    *,
    project_id: str,
    session: "AgentSession",
    websocket: "WebSocket",
) -> ResolveResult | None:
    """Look up an existing platform_repo_url in Supabase for this project."""
    await _progress(websocket, "Looking up project repository...", pct=20)

    try:
        from app.supabase_client import db_client
        from app.services.pipeline import PLATFORM_GITHUB_TOKEN

        async with db_client(None) as sb:
            result = await (
                sb.table("chat_sessions")
                .select("platform_repo_url")
                .eq("project_id", project_id)
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )

        platform_url = result.data[0].get("platform_repo_url") if result.data else None
        if not platform_url:
            return None

        platform_token = PLATFORM_GITHUB_TOKEN
        if not platform_token:
            logger.warning("resolver: DB found platform_repo_url but no PLATFORM_GITHUB_TOKEN")
            return None

        # Build authenticated clone URL
        repo_path = platform_url.replace("https://github.com/", "").strip("/")
        if repo_path.endswith(".git"):
            repo_path = repo_path[:-4]
        auth_url = f"https://{platform_token}@github.com/{repo_path}.git"

        # Patch session so the pre-clone block in ws.py picks it up
        session.repo_url  = auth_url
        session.git_token = platform_token
        session.branch    = session.branch or "main"

        logger.info(
            "resolver: Path B (DB recovery) — found %s for project %s",
            platform_url, project_id,
        )

        return await _resolve_existing_repo(
            repo_url=auth_url,
            branch=session.branch,
            git_token=platform_token,
            session=session,
            websocket=websocket,
            source="db",
        )

    except Exception as exc:
        logger.warning("resolver: DB repo recovery failed: %s", exc)
        return None


# ── Path C ───────────────────────────────────────────────────────────────────

async def _resolve_conversation(
    *,
    session: "AgentSession",
    websocket: "WebSocket",
) -> ResolveResult:
    logger.info("resolver: Path C — conversation / scratch mode")

    await _progress(websocket, "Setting up workspace...", pct=50)

    result = ResolveResult(
        path=ResolvePath.CONVERSATION,
        message="Workspace ready for your conversation.",
        detail="No repository connected — code will be created from scratch",
    )

    await _emit(
        websocket, "resolving_info",
        path=result.path,
        message=result.message,
        detail=result.detail,
    )
    return result
