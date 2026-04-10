"""Workspace state machine — defines all valid session states and the transition helper.

Every session is in exactly ONE state at all times. The backend emits a
``workspace_state`` WebSocket event on every state change so the frontend
always has the authoritative current state — no guessing from progress text.

State flow (happy path):
    ENTRY → RESOLVING → CLONING → READY → UPDATING → READY → …
                      ↘ (no repo) ↗
                        READY

Error path:
    Any state → ERROR (fatal, unrecoverable — show message + retry button)

Note: INSTALLING / STARTING / HEALTH_CHECK are reserved for future live-preview
pipeline phases (npm install → dev server → health poll). Emit them from the
task pipeline when that feature is implemented.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import WebSocket
    from app.services.sessions import AgentSession

logger = logging.getLogger(__name__)


class WorkspaceState:
    """All valid workspace states as string constants.

    Using a plain class (not Enum) so the values are bare strings — they
    serialise cleanly to JSON without needing ``.value`` everywhere.
    """

    ENTRY        = "entry"         # user just arrived — socket accepted, not yet authed
    RESOLVING    = "resolving"     # auth OK, session created, figuring out workspace
    CLONING      = "cloning"       # git clone in progress
    INSTALLING   = "installing"    # npm / pip install running (reserved)
    STARTING     = "starting"      # dev server spinning up (reserved)
    HEALTH_CHECK = "health_check"  # polling preview URL for 200 OK (reserved)
    READY        = "ready"         # workspace initialised — idle, waiting for task
    UPDATING     = "updating"      # AI agent is writing files / running task
    ERROR        = "error"         # fatal error — display message + retry button


async def transition(
    session: "AgentSession",
    websocket: "WebSocket",
    new_state: str,
    message: str = "",
    **extra,
) -> None:
    """Transition *session* to *new_state* and notify the frontend.

    This is the ONLY place session state should be mutated. Every call emits a
    ``workspace_state`` WebSocket event so the frontend never has to infer or
    guess the current state from other message types.

    Args:
        session:   The active AgentSession (must not be None).
        websocket: The open WebSocket connection to the frontend.
        new_state: One of the WorkspaceState string constants.
        message:   Optional human-readable description shown in the UI.
        **extra:   Additional fields merged into the event payload (e.g.
                   ``sessionId``, ``reconnected``).
    """
    old_state = getattr(session, "workspace_state", WorkspaceState.ENTRY)
    session.workspace_state = new_state

    logger.info(
        "Session %s state: %s → %s%s",
        session.session_id,
        old_state,
        new_state,
        f" — {message}" if message else "",
    )

    payload: dict = {
        "type": "workspace_state",
        "state": new_state,
        "sessionId": session.session_id,
    }
    if message:
        payload["message"] = message
    payload.update(extra)

    try:
        await websocket.send_json(payload)
    except Exception as send_err:
        logger.warning(
            "Failed to emit workspace_state event (%s → %s): %s",
            old_state,
            new_state,
            send_err,
        )
