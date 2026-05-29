"""VCS — GitLab REST API: user, repos, MR creation."""

from __future__ import annotations

import httpx


async def gitlab_get_user(token: str, *, gitlab_url: str = "https://gitlab.com") -> dict:
    """Validate token and return the authenticated GitLab user's profile."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{gitlab_url}/api/v4/user",
            headers={"PRIVATE-TOKEN": token},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()


async def gitlab_list_repos(token: str, *, gitlab_url: str = "https://gitlab.com") -> list[dict]:
    """List all GitLab projects the user is a member of (paginated)."""
    repos: list[dict] = []
    page = 1
    async with httpx.AsyncClient() as client:
        while True:
            resp = await client.get(
                f"{gitlab_url}/api/v4/projects",
                params={
                    "membership": "true",
                    "order_by": "last_activity_at",
                    "per_page": 100,
                    "page": page,
                },
                headers={"PRIVATE-TOKEN": token},
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
                    "fullName": r["path_with_namespace"],
                    "url": r["web_url"],
                    "cloneUrl": r["http_url_to_repo"],
                    "defaultBranch": r.get("default_branch", "main"),
                    "private": r.get("visibility") == "private",
                    "description": r.get("description"),
                    "language": "",
                    "stars": r.get("star_count") or 0,
                    "updated": r.get("last_activity_at"),
                    "provider": "gitlab",
                    "providerHost": gitlab_url.rstrip("/"),
                }
                for r in batch
            )
            if len(batch) < 100:
                break
            page += 1
    return repos


async def gitlab_list_branches(token: str, project_id: str, *, gitlab_url: str = "https://gitlab.com") -> list[dict]:
    """List branches for a GitLab project."""
    branches: list[dict] = []
    page = 1
    async with httpx.AsyncClient() as client:
        while True:
            resp = await client.get(
                f"{gitlab_url}/api/v4/projects/{project_id}/repository/branches",
                params={"per_page": 100, "page": page},
                headers={"PRIVATE-TOKEN": token},
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


async def gitlab_create_mr(
    *,
    token: str,
    project_id: str,
    title: str,
    body: str,
    source_branch: str,
    target_branch: str = "main",
    gitlab_url: str = "https://gitlab.com",
) -> dict:
    """Open a GitLab Merge Request and return the MR metadata."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{gitlab_url}/api/v4/projects/{project_id}/merge_requests",
            json={
                "title": title,
                "description": body,
                "source_branch": source_branch,
                "target_branch": target_branch,
            },
            headers={"PRIVATE-TOKEN": token},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    return {
        "prUrl": data["web_url"],
        "prNumber": data["iid"],
        "title": data["title"],
        "state": data["state"],
    }
