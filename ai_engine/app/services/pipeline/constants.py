"""
pipeline/constants.py — module-level constants shared across all pipeline steps.

Extracted verbatim from task_pipeline.py (lines 32–86, 92–96, 293–311).
Zero logic changes.
"""

import os
import pathlib
import logging

logger = logging.getLogger(__name__)

# ── Centralized Gemini Models ─────────────────────────────────
# gemini-2.0-flash: fast + cheap for classification, research, analysis (8K output)
# gemini-2.5-flash: for blueprint generation (65K output — no truncation)
GEMINI_MODEL = "gemini-2.0-flash"
GEMINI_BLUEPRINT_MODEL = "gemini-2.5-flash"

# ── Fallback Gemini API Key ──────────────────────────────────
# Used when the user doesn't have their own key in Settings.
_FALLBACK_GEMINI_KEY = os.environ.get("GOOGLE_API_KEY", "").strip()

# ── PLATFORM_GITHUB_TOKEN — loaded ONCE at module startup ────────────────────
# Load from environment first; fall back to the nearest .env file on disk.
# This avoids repeated lazy dotenv reads scattered across the pipeline and
# surfaces misconfiguration immediately when the server starts.
def _load_platform_token() -> str:
    """Return PLATFORM_GITHUB_TOKEN from env or the nearest .env file."""
    # 1. Already in environment (Docker / systemd / cloud-run injected it)
    token = os.environ.get("PLATFORM_GITHUB_TOKEN", "").strip()
    if token:
        return token

    # 2. Walk up from this file to find a .env file
    _candidates = [
        pathlib.Path(__file__).resolve().parents[4] / ".env",
        pathlib.Path(__file__).resolve().parents[3] / ".env",
        pathlib.Path(__file__).resolve().parents[2] / ".env",
        pathlib.Path("/app/.env"),
        pathlib.Path.cwd() / ".env",
    ]
    for _candidate in _candidates:
        if _candidate.exists():
            try:
                from dotenv import load_dotenv
                load_dotenv(_candidate, override=False)
                token = os.environ.get("PLATFORM_GITHUB_TOKEN", "").strip()
                if token:
                    logger.info(
                        "PLATFORM_GITHUB_TOKEN loaded from %s (prefix: %s...)",
                        _candidate,
                        token[:7],
                    )
                    return token
            except Exception as _e:
                logger.warning("dotenv load failed for %s: %s", _candidate, _e)

    logger.warning(
        "PLATFORM_GITHUB_TOKEN not found in environment or any .env file. "
        "New-project template cloning and GitHub repo creation will be disabled."
    )
    return ""


PLATFORM_GITHUB_TOKEN: str = _load_platform_token()

# ── File-tree exclude set ─────────────────────────────────────
_FILE_TREE_EXCLUDE = {
    ".git", "node_modules", "__pycache__", ".next",
    ".venv", "venv", ".mypy_cache", ".pytest_cache",
    "dist", "build", ".tox", ".eggs", ".claude",
}

# ── Template Registry ─────────────────────────────────────────────────────────
# Maps stack names (from [LUCID_PROJECT] header) to the LucidSoftware-tech
# template repos. The backend clones these DIRECTLY — no intermediate repo needed.
_TEMPLATE_REGISTRY: dict[str, str] = {
    "nextjs":          "LucidSoftware-tech/lucid-template-nextjs-website",
    "nextjs-website":  "LucidSoftware-tech/lucid-template-nextjs-website",
    "react":           "LucidSoftware-tech/lucid-template-react-admin",
    "react-admin":     "LucidSoftware-tech/lucid-template-react-admin",
    "vue":             "LucidSoftware-tech/lucid-template-vue-admin",
    "vue-admin":       "LucidSoftware-tech/lucid-template-vue-admin",
}

_PLATFORM_ORG = "LucidSoftware-tech"
