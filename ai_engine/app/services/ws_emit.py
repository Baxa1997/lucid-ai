"""WebSocket emit helpers shared by the generation pipelines.

Extracted from project_generator.py (god-module split). ``_ws_send`` is
the universal "typed message to the client" helper — every pipeline and
the LLM client use it, so it lives in its own dependency-free module.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("lucid.project_generator")


async def _ws_send(websocket, msg_type: str, message: str) -> None:
    """Send a typed message to the websocket.

    Errors are caught so a dropped client never crashes the pipeline, but they
    are logged at WARNING — a stale-socket bug used to be invisible because
    the previous version did a bare ``except: pass``. With ``WebSocketProxy``
    in place the only remaining failure mode is a real Redis/queue issue,
    which is worth knowing about.
    """
    if not websocket:
        return
    try:
        await websocket.send_json({"type": msg_type, "message": message})
    except Exception as exc:
        logger.warning("_ws_send(%s) failed (ws may be detached): %s", msg_type, exc)
