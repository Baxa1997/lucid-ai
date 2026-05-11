"""
pipeline/__init__.py — Public API for the pipeline package.

Imports every public symbol that task_pipeline.py originally exported.
External callers (ws.py, project_generator.py, etc.) import from here
exactly as they did before from task_pipeline.py — zero import-path changes
needed anywhere in the codebase.
"""

# ── Constants ────────────────────────────────────────────────────────────────
from .constants import (
    GEMINI_MODEL,
    GEMINI_BLUEPRINT_MODEL,
    PLATFORM_GITHUB_TOKEN,
    _FILE_TREE_EXCLUDE,
    _TEMPLATE_REGISTRY,
    _PLATFORM_ORG,
)

# ── WebSocket helpers ────────────────────────────────────────────────────────
from .ws_utils import (
    _build_ws_file_tree,
    _send_file_tree,
    _send_chat_message,
    _approve_all_tools,
)

# ── Package manager ──────────────────────────────────────────────────────────
from .package_manager import (
    detect_package_manager,
    _pm_install_cmd,
    _pm_env,
)

# ── GitHub helpers ───────────────────────────────────────────────────────────
from .github import (
    is_fine_grained_token,
    _derive_html_url,
    _resolve_template_repo,
    _sanitize_repo_name,
    _create_github_repo,
    derive_repo_name,
    create_github_repo,
)

# ── Pre-build fixers ─────────────────────────────────────────────────────────
from .step5_fixers import _fix_broken_layout_imports

# ── Step 1: Validate ────────────────────────────────────────────────────────
from .step1_validate import validate_inputs

# ── Step 2: Clone ────────────────────────────────────────────────────────────
from .step2_clone import clone_with_openhands

# ── Step 3: Classify ─────────────────────────────────────────────────────────
from .step3_classify import classify_task

# ── Step 4: Explore / Research / Plan ────────────────────────────────────────
from .step4_explore import (
    explore_with_gemini,
    gemini_research,
    gemini_create_plan,
    blueprint_to_file_plan,
    _get_fallback_sections,
    _build_dynamic_fallback,
)

# ── Step 4.5: Analyze images ──────────────────────────────────────────────────
from .step4b_images import analyze_images

# ── Step 5: Execute (edit-mode + batch) ──────────────────────────────────────
from .step5_execute import (
    _kill_claude_subprocesses,
    execute_with_claude,
    execute_project_in_batches,
    _simplified_prompt,
)

# ── Step 5.5: Build verify ────────────────────────────────────────────────────
from .step5b_build_verify import verify_build

# ── Steps 6+7: Verify changes + Push ─────────────────────────────────────────
from .step6_verify import (
    verify_changes,
    push_with_openhands,
)

# ── Orchestrator ──────────────────────────────────────────────────────────────
from .orchestrator import run_pipeline

__all__ = [
    # constants
    "GEMINI_MODEL", "GEMINI_BLUEPRINT_MODEL",
    "PLATFORM_GITHUB_TOKEN", "_FILE_TREE_EXCLUDE", "_TEMPLATE_REGISTRY", "_PLATFORM_ORG",
    # ws helpers
    "_build_ws_file_tree", "_send_file_tree", "_send_chat_message", "_approve_all_tools",
    # package manager
    "detect_package_manager", "_pm_install_cmd", "_pm_env",
    # github
    "is_fine_grained_token", "_derive_html_url", "_resolve_template_repo",
    "_sanitize_repo_name", "_create_github_repo", "derive_repo_name", "create_github_repo",
    # fixers
    "_fix_broken_layout_imports",
    # steps
    "validate_inputs",
    "clone_with_openhands",
    "classify_task",
    "explore_with_gemini", "gemini_research", "gemini_create_plan",
    "blueprint_to_file_plan", "_get_fallback_sections", "_build_dynamic_fallback",
    "analyze_images",
    "_kill_claude_subprocesses", "execute_with_claude", "execute_project_in_batches",
    "_simplified_prompt",
    "verify_build",
    "verify_changes", "push_with_openhands",
    "run_pipeline",
]
