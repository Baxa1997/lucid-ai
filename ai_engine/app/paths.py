"""Centralised filesystem paths used across the ai_engine.

Every path the app writes to was previously spelled inline with an f-string.
When one of the naming conventions changes (e.g. moving `/tmp/lucid_ws_*`
under a different root to support a ram-disk or a per-user scratch area),
the grep hunt through a dozen files becomes error-prone. This module makes
those conventions a single edit.

Nothing here holds state — these are pure helpers so they can be imported
from anywhere (routers, services, pipeline) without cycles.
"""

from __future__ import annotations

import os

# ── Roots ────────────────────────────────────────────────────────────────
# Everything the app creates at runtime lives under ``TMP_ROOT``. Tests
# may override by setting ``LUCID_TMP_ROOT`` before import.
TMP_ROOT: str = os.environ.get("LUCID_TMP_ROOT", "/tmp")

# Preview workspaces are stored under /app/storage/preview_ws so they
# survive container restarts (the /app/storage dir is volume-mounted).
# Falls back to TMP_ROOT if LUCID_PREVIEW_WS_ROOT is overridden.
PREVIEW_WS_ROOT: str = os.environ.get("LUCID_PREVIEW_WS_ROOT", "/app/storage/preview_ws")

PREVIEW_WORKSPACE_PREFIX: str = "lucid_ws_"
NEW_PROJECT_WORKSPACE_PREFIX: str = "lucid_new_"
CONVERSATION_WORKSPACE_PREFIX: str = "lucid_conv_"
RESEARCH_CACHE_DIR: str = os.path.join(TMP_ROOT, "lucid_research_cache")

# The node_modules cache holds large native binaries (e.g. @next/swc ≈ 135 MB).
# It must NOT live on the macOS Docker Desktop bind mount (TMP_ROOT=/app/storage):
# large file writes there truncate over FUSE, producing a corrupt .node that
# SIGBUSes the moment Next mmaps/dlopens it ("Dev server crashed on startup",
# and a SIGBUS in `next build`). Point LUCID_NM_CACHE_ROOT at a Docker *named
# volume* (VM-local ext4) so large writes are reliable and mmap works. Defaults
# to TMP_ROOT for back-compat (Linux prod bind mounts and tests are unaffected).
NODE_MODULES_CACHE_ROOT: str = (
    os.environ.get("LUCID_NM_CACHE_ROOT")
    or os.path.join(TMP_ROOT, "lucid_nm_cache")
)


# ── Preview workspaces (shared across reconnects) ───────────────────────

def preview_workspace_path(conversation_id: str) -> str:
    """Stable path for the preview workspace of ``conversation_id``.

    Stored under PREVIEW_WS_ROOT (/app/storage/preview_ws) which is
    volume-mounted, so workspaces survive container restarts and the dev
    server + node_modules cache can be reused without re-cloning.
    """
    os.makedirs(PREVIEW_WS_ROOT, exist_ok=True)
    short = conversation_id.replace("-", "")[:12]
    return os.path.join(PREVIEW_WS_ROOT, f"{PREVIEW_WORKSPACE_PREFIX}{short}")


def is_preview_workspace(path: str) -> bool:
    """True if ``path`` looks like a preview workspace created by us.

    Used by cleanup routines that must NOT delete shared preview workspaces
    when destroying a single session.
    """
    base = os.path.basename(os.path.normpath(path or ""))
    return base.startswith(PREVIEW_WORKSPACE_PREFIX)


# ── New-project workspaces (per-run, disposable) ─────────────────────────

def new_project_workspace_path(task_id: str, suffix: str = "") -> str:
    """Per-run workspace for freshly generated projects.

    Every pipeline run gets a fresh directory so partial state from a prior
    attempt can't bleed into the next one. ``suffix`` lets callers add a
    short disambiguator (e.g. a 6-char uuid) for parallelism safety.
    """
    name = f"{NEW_PROJECT_WORKSPACE_PREFIX}{task_id}"
    if suffix:
        name = f"{name}_{suffix}"
    return os.path.join(TMP_ROOT, name)


# ── Conversation workspaces ──────────────────────────────────────────────

def conversation_workspace_path(conversation_id: str) -> str:
    """Per-conversation workspace (used by the older workspace_manager path)."""
    return os.path.join(TMP_ROOT, f"{CONVERSATION_WORKSPACE_PREFIX}{conversation_id}")
