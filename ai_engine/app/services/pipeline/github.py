"""pipeline/github.py — pipeline-specific GitHub helpers.

Everything except _resolve_template_repo has moved to app.services.vcs.github.
Re-exported here so pipeline-internal relative imports continue to work unchanged.
"""

from app.services.vcs.github import (
    is_fine_grained_token,
    _derive_html_url,
    _sanitize_repo_name,
    _create_github_repo,
    derive_repo_name,
    create_github_repo,
)


def _resolve_template_repo(stack: str) -> str:
    """Return the full org/repo slug for a given stack name, or empty string.

    Kept here because _TEMPLATE_REGISTRY is a pipeline-level constant.
    """
    from .constants import _TEMPLATE_REGISTRY
    key = (stack or "").strip().lower()
    return _TEMPLATE_REGISTRY.get(key, "")


__all__ = [
    "is_fine_grained_token",
    "_derive_html_url",
    "_sanitize_repo_name",
    "_create_github_repo",
    "derive_repo_name",
    "create_github_repo",
    "_resolve_template_repo",
]
