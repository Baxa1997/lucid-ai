"""VCS — GitHub REST API: user, repos, PR creation, repo creation, token helpers."""

from __future__ import annotations

import logging
import re

import httpx
from fastapi import WebSocket

logger = logging.getLogger(__name__)

_GH_HEADERS = {
    "Accept": "application/vnd.github.v3+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


# ── Token helpers ────────────────────────────────────────────────────────

def is_fine_grained_token(token: str) -> bool:
    """Detect fine-grained tokens which cannot create repos.
    Fine-grained: github_pat_...   Classic: ghp_... or gho_...
    """
    return token.startswith("github_pat_")


# ── URL helpers ──────────────────────────────────────────────────────────

def _derive_html_url(clone_url: str, token: str = "") -> str:
    """Derive a browser-viewable GitHub HTML URL from an authenticated clone URL."""
    url = clone_url or ""
    if token and f"{token}@" in url:
        url = url.replace(f"{token}@", "")
    elif "@github.com" in url:
        idx = url.find("@github.com")
        url = "https://github.com" + url[idx + len("@github.com"):]
    if url.endswith(".git"):
        url = url[:-4]
    return url.rstrip("/")


def _sanitize_repo_name(raw: str, max_len: int = 50) -> str:
    """Turn any string into a valid GitHub repo name slug."""
    slug = re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")
    return slug[:max_len] or "lucid-project"


# ── Repo name derivation ─────────────────────────────────────────────────

def derive_repo_name(task: str, chat_session_id: str = "") -> tuple[str, str, str, bool]:
    """Derive a clean repo name from the [LUCID_PROJECT] header or task text.

    Rules:
        1. Use description from [LUCID_PROJECT] header if present
        2. Otherwise extract first meaningful words from actual task
        3. NEVER use conversation context/title as repo name
        4. Suffix based on stack: nextjs/react → -frontend, fastapi → -backend
        5. Clean: lowercase, hyphens only, max 30 chars

    Returns (repo_name, project_description, project_stack, is_admin).
    """
    project_desc = ""
    project_stack = ""
    project_backend = ""

    header_match = re.match(
        r"\[LUCID_PROJECT\]\s*description=(.+?)\s*\|\s*stack=(\S+)\s*\|\s*backend=(\S+)",
        str(task or ""),
    )
    if header_match:
        project_desc = header_match.group(1).strip()
        project_stack = header_match.group(2).strip()
        project_backend = header_match.group(3).strip()
    else:
        raw = str(task or "").strip()

        context_markers = [
            "## previous conversation context",
            "## what happened in the previous session",
            "previous-conversation-context:",
            "previous conversation context:",
            "CURRENT TASK:",
        ]
        for marker in context_markers:
            idx = raw.lower().find(marker.lower())
            if idx >= 0:
                if "current task" in marker.lower():
                    raw = raw[idx + len(marker):].strip()
                else:
                    current_idx = raw.lower().find("current task:", idx)
                    if current_idx >= 0:
                        raw = raw[current_idx + len("current task:"):].strip()
                    else:
                        raw = raw[idx + len(marker):].strip()

        raw = re.sub(r"\[LUCID_PROJECT\].*?\n\n?", "", raw, flags=re.DOTALL).strip()
        raw = re.split(r"\n---\n|\n##\s", raw)[0].strip()
        project_desc = raw[:100]

    name = project_desc.lower().strip()

    for prefix in [
        "build me a ", "build a ", "create a ", "make a ",
        "develop a ", "generate a ", "make me a ",
        "build me ", "create ", "make ", "just a ",
        "just ", "simple ", "overall ", "new ",
    ]:
        if name.startswith(prefix):
            name = name[len(prefix):]
            break

    name = re.split(r"\bwith\b|\band\b|\bthat\b|\busing\b|\bfor\b|\bwhich\b", name)[0].strip()

    words = [w for w in name.split() if len(w) > 1][:4]
    name = " ".join(words) if words else "project"

    slug = re.sub(r"[^a-z0-9]+", "-", name).strip("-")[:25]
    slug = slug or "project"

    desc_lower = project_desc.lower()
    is_admin = False

    if any(w in desc_lower for w in ["admin", "dashboard", "panel", "cms", "crm", "backoffice"]):
        type_suffix = "-admin-frontend"
        is_admin = True
    elif any(w in desc_lower for w in ["api", "backend", "server", "microservice"]):
        if project_stack in ("fastapi", "python", "django", "flask"):
            type_suffix = "-backend"
        elif project_stack in ("nodejs", "node", "express"):
            type_suffix = "-api"
        else:
            type_suffix = "-service"
    elif project_stack in ("fastapi", "python", "django", "flask"):
        type_suffix = "-backend"
    elif project_stack in ("nodejs", "node", "express"):
        type_suffix = "-api"
    else:
        type_suffix = "-frontend"

    repo_name = f"{slug}{type_suffix}"
    if len(repo_name) > 30:
        max_slug = 30 - len(type_suffix)
        slug = slug[:max_slug].rstrip("-")
        repo_name = f"{slug}{type_suffix}"

    logger.info("derive_repo_name: %s (desc=%s, stack=%s)", repo_name, project_desc[:40], project_stack)
    return repo_name, project_desc, project_stack, is_admin


# ── GitHub REST API ──────────────────────────────────────────────────────

async def github_get_user(token: str) -> dict:
    """Validate token and return the authenticated GitHub user's profile."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://api.github.com/user",
            headers={"Authorization": f"Bearer {token}", **_GH_HEADERS},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()


async def github_list_repos(token: str) -> list[dict]:
    """List all repos accessible by the stored GitHub PAT (paginated)."""
    repos: list[dict] = []
    page = 1
    async with httpx.AsyncClient() as client:
        while True:
            resp = await client.get(
                "https://api.github.com/user/repos",
                params={"sort": "updated", "per_page": 100, "page": page},
                headers={"Authorization": f"Bearer {token}", **_GH_HEADERS},
                timeout=15,
            )
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            repos.extend(
                {
                    "id": r.get("id"),
                    "name": r["name"],
                    "fullName": r["full_name"],
                    "url": r["html_url"],
                    "cloneUrl": r["clone_url"],
                    "defaultBranch": r["default_branch"],
                    "private": r["private"],
                    "description": r.get("description"),
                    "language": r.get("language") or "",
                    "stars": r.get("stargazers_count") or 0,
                    "updated": r.get("updated_at"),
                    "provider": "github",
                }
                for r in batch
            )
            if len(batch) < 100:
                break
            page += 1
    return repos


async def github_list_branches(token: str, owner: str, repo: str) -> list[dict]:
    """List branches for a GitHub repo."""
    branches: list[dict] = []
    page = 1
    async with httpx.AsyncClient() as client:
        while True:
            resp = await client.get(
                f"https://api.github.com/repos/{owner}/{repo}/branches",
                params={"per_page": 100, "page": page},
                headers={"Authorization": f"Bearer {token}", **_GH_HEADERS},
                timeout=15,
            )
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            branches.extend({"name": b["name"]} for b in batch)
            if len(batch) < 100:
                break
            page += 1
    return branches


async def github_create_pr(
    *,
    token: str,
    owner: str,
    repo: str,
    title: str,
    body: str,
    head: str,
    base: str = "main",
) -> dict:
    """Open a GitHub Pull Request and return the PR metadata."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"https://api.github.com/repos/{owner}/{repo}/pulls",
            json={"title": title, "body": body, "head": head, "base": base},
            headers={"Authorization": f"Bearer {token}", **_GH_HEADERS},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    return {
        "prUrl": data["html_url"],
        "prNumber": data["number"],
        "title": data["title"],
        "state": data["state"],
    }


async def _create_github_repo(repo_name: str, token: str, description: str = "") -> dict:
    """Create a new private GitHub repo for the authenticated user via REST API.

    Returns the repo JSON from GitHub (includes html_url, clone_url, etc.)
    Raises RuntimeError on failure.
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    payload = {
        "name": repo_name,
        "private": True,
        "auto_init": False,
        "description": description or "Generated by Lucid AI",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post("https://api.github.com/user/repos", json=payload, headers=headers)
    data = resp.json()
    if resp.status_code not in (200, 201):
        raise RuntimeError(
            f"GitHub API {resp.status_code}: {data.get('message', 'unknown')} "
            f"— errors: {data.get('errors', [])}"
        )
    return data


async def create_github_repo(
    project_name: str,
    github_token: str,
    description: str = "",
    is_private: bool = True,
    websocket: WebSocket = None,
) -> tuple[str, str] | None:
    """Create a GitHub repo using a Classic Personal Access Token.

    Returns (auth_clone_url, html_url) on success, None on failure.
    """
    headers = {
        "Authorization": f"token {github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
    }

    clean_desc = re.sub(r"[\x00-\x1f\x7f]+", " ", description or "").strip()[:350]
    clean_desc = clean_desc or "Generated by Lucid AI"

    async def _try_create(name: str) -> httpx.Response:
        async with httpx.AsyncClient(timeout=30) as client:
            return await client.post(
                "https://api.github.com/user/repos",
                headers=headers,
                json={
                    "name": name,
                    "description": clean_desc,
                    "private": is_private,
                    "auto_init": False,
                    "has_issues": True,
                    "has_projects": False,
                    "has_wiki": False,
                },
            )

    async def _ws_error(msg: str):
        if websocket:
            try:
                await websocket.send_json({"type": "error", "message": msg})
            except Exception:
                pass

    attempts = [project_name] + [f"{project_name}_{i}" for i in range(2, 6)]

    resp = None
    used_name = project_name
    for attempt_name in attempts:
        used_name = attempt_name
        logger.info("Creating repo: %s", attempt_name)
        resp = await _try_create(attempt_name)
        if resp.status_code == 201:
            break
        if resp.status_code == 422:
            logger.info("Repo name '%s' taken, trying next...", attempt_name)
            continue
        break

    if resp and resp.status_code == 201:
        repo_data = resp.json()
        clone_url = repo_data.get("clone_url", "")
        html_url = repo_data.get("html_url", "")
        auth_url = clone_url.replace("https://", f"https://{github_token}@")
        logger.info("Repo created: %s", html_url)
        return auth_url, html_url

    status = resp.status_code if resp else -1
    raw_body = resp.text[:500] if resp else "no response"

    api_detail = ""
    if resp is not None:
        try:
            err_json = resp.json()
            errors_list = err_json.get("errors", [])
            if errors_list:
                api_detail = errors_list[0].get("message", "")
            if not api_detail:
                api_detail = err_json.get("message", "")
        except Exception:
            api_detail = raw_body[:200]

    token_hint = github_token[:7] + "..." if github_token else "(empty)"
    logger.error(
        "GitHub repo creation failed: status=%d token=%s name=%s detail=%s body=%s",
        status, token_hint, project_name, api_detail, raw_body[:300],
    )

    if resp is None:
        await _ws_error("❌ GitHub API — no response received.")
    elif status == 401:
        await _ws_error(
            f"❌ GitHub 401 Unauthorized. Token: {token_hint}. "
            f"API says: {api_detail or 'Bad credentials'}. "
            "Use a Classic PAT (ghp_...) with repo scope."
        )
    elif status == 403:
        await _ws_error(
            f"❌ GitHub 403 Forbidden: {api_detail or 'Token missing repo scope'}. "
            "Regenerate your Classic token with repo scope."
        )
    elif status == 422:
        await _ws_error(
            f"❌ GitHub 422: {api_detail or 'Name already exists'}. "
            f"Tried: {project_name} + 4 variants."
        )
    else:
        await _ws_error(f"❌ GitHub {status}: {api_detail or raw_body[:200]}")

    return None
