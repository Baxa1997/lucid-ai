"""
Skeleton Manager — copies pre-made project skeletons to workspace.

Maps stack + backend flag → skeleton directory, then copies all files.

IMPORTANT: html-css skeleton is ONLY used when user explicitly selects
"HTML & CSS" in the wizard. Default fallback is always react-vite.
"""

import os
import json
import shutil
import logging

logger = logging.getLogger(__name__)

# Skeleton directory (relative to this file)
SKELETONS_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "..", "skeletons",
)


STACK_MAP = {
    # React variants
    "react": "react-vite",
    "react-vite": "react-vite",
    "vite": "react-vite",
    # Next.js
    "nextjs": "nextjs",
    "next": "nextjs",
    "next.js": "nextjs",
    # Plain HTML — ONLY when explicitly selected, never as fallback
    "html": "html-css",
    "html-css": "html-css",
    "html/css": "html-css",
    "admin-react": "admin-react",
    # Vue Admin — no local skeleton, detect from task description (do NOT silently serve React)
    "vue": None,
    "vue-admin": None,
    # Auto/empty → resolved by _detect_stack_from_description() below
    "auto": None,
}

# Keywords from app_patterns.json that suggest specific skeletons
_ADMIN_KEYWORDS = [
    "admin", "dashboard", "panel", "management", "cms",
    "backoffice", "back-office", "crm", "control panel",
    "tms", "logistics", "dispatch", "fleet",
]
_NEXTJS_KEYWORDS = [
    "website", "landing", "portfolio", "marketing", "blog",
    "homepage", "showcase", "personal", "documentation",
    "docs", "wiki", "magazine",
]


def _detect_stack_from_description(task: str) -> str:
    """Auto-detect the best skeleton from the task description.

    Rules:
        - admin/dashboard/panel/crm → admin-react
        - website/landing/portfolio → nextjs
        - app/saas/platform/ecommerce → react-vite
        - default → react-vite (NEVER html-css)
    """
    task_lower = (task or "").lower()

    # Admin check first (highest priority)
    if any(kw in task_lower for kw in _ADMIN_KEYWORDS):
        return "admin-react"

    # Website/landing/portfolio → nextjs (better SEO, SSR support)
    if any(kw in task_lower for kw in _NEXTJS_KEYWORDS):
        return "nextjs"

    # Everything else → react-vite as the safe default
    return "react-vite"


def get_skeleton_for_stack(stack: str, is_admin: bool = False, task: str = "") -> str:
    """Map stack string + admin flag → skeleton directory path.

    Returns absolute path to the skeleton folder, or empty string if unknown.
    """
    stack_lower = (stack or "").strip().lower()

    # Admin panel override — regardless of stack
    if is_admin and stack_lower in ("react", "react-vite", "vite", "", "auto"):
        skeleton_name = "admin-react"
    elif stack_lower in STACK_MAP:
        skeleton_name = STACK_MAP[stack_lower]
        # If mapped to None (auto), detect from task description
        if skeleton_name is None:
            skeleton_name = _detect_stack_from_description(task)
    elif not stack_lower or stack_lower == "auto":
        # Empty or auto stack → detect from task description
        skeleton_name = _detect_stack_from_description(task)
    else:
        # Unknown stack → default to react-vite (NEVER html-css)
        skeleton_name = "react-vite"

    skeleton_path = os.path.normpath(os.path.join(SKELETONS_DIR, skeleton_name))

    # Debug logging (requested)
    logger.debug("Stack detected: %s → skeleton: %s", stack_lower, skeleton_name)
    logger.debug("Skeleton path: %s", skeleton_path)
    logger.info(
        "get_skeleton_for_stack: stack=%s, is_admin=%s → skeleton=%s (path=%s)",
        stack_lower, is_admin, skeleton_name, skeleton_path,
    )

    if os.path.isdir(skeleton_path):
        return skeleton_path

    # Fallback to react-vite (NEVER html-css)
    fallback = os.path.normpath(os.path.join(SKELETONS_DIR, "react-vite"))
    logger.warning("Skeleton '%s' not found, falling back to react-vite", skeleton_name)
    if os.path.isdir(fallback):
        return fallback

    return ""


def detect_admin_from_task(task: str) -> bool:
    """Check if the task description suggests an admin panel."""
    task_lower = (task or "").lower()
    return any(kw in task_lower for kw in _ADMIN_KEYWORDS)


def copy_skeleton(skeleton_path: str, workspace_path: str) -> list:
    """Copy skeleton files to workspace. Returns list of relative paths copied.

    Does NOT overwrite existing files in workspace.
    """
    if not skeleton_path or not os.path.isdir(skeleton_path):
        logger.warning("Skeleton path not found: %s", skeleton_path)
        return []

    copied = []
    for root, dirs, files in os.walk(skeleton_path):
        # Skip hidden dirs
        dirs[:] = [d for d in dirs if not d.startswith(".")]

        for f in files:
            if f == ".gitkeep":
                continue

            src = os.path.join(root, f)
            rel = os.path.relpath(src, skeleton_path)
            dst = os.path.join(workspace_path, rel)

            # Don't overwrite existing files
            if os.path.exists(dst):
                continue

            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
            copied.append(rel)

    # Create empty directories from .gitkeep markers
    for root, dirs, files in os.walk(skeleton_path):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for d in dirs:
            dir_path = os.path.join(workspace_path, os.path.relpath(os.path.join(root, d), skeleton_path))
            os.makedirs(dir_path, exist_ok=True)

    logger.info("Copied %d skeleton files from %s", len(copied), os.path.basename(skeleton_path))

    # Debug logging (requested)
    try:
        ws_files = os.listdir(workspace_path)[:10]
        logger.debug("Workspace files after copy: %s", ws_files)
    except Exception:
        pass

    return copied

