"""
OpenHands SDK facade.

Every SDK symbol the application needs is imported here once.
Other modules import from ``app.sdk`` — never directly from
``openhands.*``.  When the SDK is not installed the sentinel
``OPENHANDS_AVAILABLE`` is ``False`` and all symbols are ``None``.
"""

from __future__ import annotations

# ── Sentinels (set below) ───────────────────────────────────

OPENHANDS_AVAILABLE: bool = False
import_error: str | None = None

# ── SDK symbols — default to None when SDK is absent ────────

LLM = None
Agent = None
Conversation = None
Tool = None
Workspace = None
get_logger = None
RemoteConversation = None
ConversationStateUpdateEvent = None
FileEditorTool = None
TaskTrackerTool = None
TerminalTool = None
get_default_agent = None

# ── Attempt import ──────────────────────────────────────────

try:
    from openhands.sdk import (                          # noqa: F811
        LLM,
        Agent,
        Conversation,
        Tool,
        Workspace,
        get_logger,
    )
    from openhands.sdk.conversation import RemoteConversation   # noqa: F811
    from openhands.sdk.event import ConversationStateUpdateEvent  # noqa: F811
    from openhands.tools.file_editor import FileEditorTool      # noqa: F811
    from openhands.tools.task_tracker import TaskTrackerTool    # noqa: F811
    from openhands.tools.terminal import TerminalTool           # noqa: F811
    from openhands.tools.preset.default import get_default_agent  # noqa: F811

    OPENHANDS_AVAILABLE = True
except ImportError as exc:
    import_error = str(exc)
