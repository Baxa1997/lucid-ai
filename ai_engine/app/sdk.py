"""
OpenHands SDK facade.

Every SDK symbol the application needs is imported here once.
Other modules import from ``app.sdk`` — never directly from
``openhands.*``.  When the SDK is not installed the sentinel
``OPENHANDS_AVAILABLE`` is ``False`` and all symbols are ``None``.
"""

from __future__ import annotations

from app.config import logger

# ── Sentinels ───────────────────────────────────────────────

OPENHANDS_AVAILABLE: bool = False
import_error: str | None = None

# ── SDK symbols — default to None when SDK is absent ────────

LLM = None
Agent = None
Tool = None
Conversation = None
Workspace = None
get_default_agent = None

# ── Attempt import ──────────────────────────────────────────

try:
    from openhands.sdk import (          # noqa: F811
        LLM,
        Agent,
        Conversation,
        Tool,
        Workspace,
    )
    from openhands.tools.preset.default import get_default_agent  # noqa: F811

    OPENHANDS_AVAILABLE = True
    logger.info("OpenHands SDK is available — real agent mode enabled")

except ImportError as exc:
    import_error = str(exc)
    logger.warning(
        "OpenHands SDK not available — running in mock mode: %s", import_error
    )
