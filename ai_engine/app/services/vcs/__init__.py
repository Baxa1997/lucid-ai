"""VCS bridge — git operations, token storage, GitHub and GitLab API.

Sub-modules:
  vcs.git     — subprocess git (clone, pull, push, status, diff)
  vcs.tokens  — AES-256-CBC encryption + integration CRUD (Supabase)
  vcs.github  — GitHub REST API (user, repos, PR/repo creation, token helpers)
  vcs.gitlab  — GitLab REST API (user, repos, MR creation)
"""

from .git import (
    _inject_token_into_url,
    run_git_with_retry,
    clone_repo,
    pull_latest,
    push_changes,
    get_git_status,
    get_git_diff,
)

from .tokens import (
    encrypt_token,
    decrypt_token,
    upsert_integration,
    get_integration,
    delete_integration,
    list_integrations,
)

from .github import (
    is_fine_grained_token,
    _derive_html_url,
    _sanitize_repo_name,
    derive_repo_name,
    github_get_user,
    github_list_repos,
    github_create_pr,
    _create_github_repo,
    create_github_repo,
)

from .gitlab import (
    gitlab_get_user,
    gitlab_list_repos,
    gitlab_create_mr,
)

__all__ = [
    # git
    "_inject_token_into_url", "run_git_with_retry",
    "clone_repo", "pull_latest", "push_changes",
    "get_git_status", "get_git_diff",
    # tokens
    "encrypt_token", "decrypt_token",
    "upsert_integration", "get_integration", "delete_integration", "list_integrations",
    # github
    "is_fine_grained_token", "_derive_html_url", "_sanitize_repo_name",
    "derive_repo_name", "github_get_user", "github_list_repos", "github_create_pr",
    "_create_github_repo", "create_github_repo",
    # gitlab
    "gitlab_get_user", "gitlab_list_repos", "gitlab_create_mr",
]
