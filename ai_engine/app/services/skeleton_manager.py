"""
Skeleton Manager — copies pre-made project skeletons to workspace.

Maps stack + backend flag → skeleton directory, then copies all files.
"""

import os
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
    # Plain HTML
    "html": "html-css",
    "html-css": "html-css",
    "html/css": "html-css",
    "static": "html-css",
    "vanilla": "html-css",
    # Admin (detected by keywords, not just stack)
    "admin-react": "admin-react",
}


def get_skeleton_for_stack(stack: str, is_admin: bool = False) -> str:
    """Map stack string + admin flag → skeleton directory path.

    Returns absolute path to the skeleton folder, or empty string if unknown.
    """
    stack_lower = (stack or "").strip().lower()

    # Admin panel override
    if is_admin and stack_lower in ("react", "react-vite", "vite", ""):
        skeleton_name = "admin-react"
    else:
        skeleton_name = STACK_MAP.get(stack_lower, "react-vite")  # default to react-vite

    skeleton_path = os.path.normpath(os.path.join(SKELETONS_DIR, skeleton_name))
    if os.path.isdir(skeleton_path):
        return skeleton_path

    # Fallback to react-vite
    fallback = os.path.normpath(os.path.join(SKELETONS_DIR, "react-vite"))
    if os.path.isdir(fallback):
        return fallback

    return ""


def detect_admin_from_task(task: str) -> bool:
    """Check if the task description suggests an admin panel."""
    keywords = [
        "admin", "dashboard", "panel", "management", "cms",
        "backoffice", "back-office", "crm", "control panel",
    ]
    task_lower = (task or "").lower()
    return any(kw in task_lower for kw in keywords)


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
    return copied
