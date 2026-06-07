"""Canonical agent-owned workflow status events.

The frontend must not infer what the agent is doing from phase numbers,
preview text, or timing. The prompt classifier and executing pipeline emit
``agent.status`` whenever the current activity changes; UI surfaces render
the supplied label and description verbatim.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def emit_agent_status(
    websocket: Any,
    *,
    key: str,
    label: str,
    description: str = "",
    state: str = "active",
    source: str = "agent",
    workflow_id: str = "",
) -> None:
    """Emit the canonical current-agent-status envelope, fail-soft."""
    if websocket is None:
        return
    try:
        await websocket.send_json({
            "type": "agent.status",
            "key": key,
            "label": label,
            "description": description,
            "state": state,
            "source": source,
            "workflow_id": workflow_id,
        })
    except Exception as exc:
        logger.debug("agent.status send failed (%s): %s", key, exc)


async def emit_workflow_selected(
    websocket: Any,
    *,
    workflow_id: str,
    next_action: str,
    decided_by: str,
    archetype: str = "",
    reasoning: str = "",
) -> None:
    """Tell clients which workflow the prompt-identification agent selected."""
    if websocket is None:
        return
    try:
        await websocket.send_json({
            "type": "agent.workflow",
            "workflow_id": workflow_id,
            "next_action": next_action,
            "decided_by": decided_by,
            "archetype": archetype,
            "reasoning": reasoning,
        })
    except Exception as exc:
        logger.debug("agent.workflow send failed (%s): %s", workflow_id, exc)


async def emit_task_phase(
    websocket: Any,
    *,
    phase: int,
    title: str,
    description: str,
    status: str,
    mode: str,
    key: str = "",
    source: str = "pipeline",
) -> None:
    """Dual-emit chart progress and the canonical current-agent status."""
    if websocket is None:
        return
    try:
        await websocket.send_json({
            "type": "task_phase",
            "phase": phase,
            "title": title,
            "description": description,
            "status": status,
            "mode": mode,
        })
    except Exception as exc:
        logger.debug("task_phase send failed (%s): %s", phase, exc)

    await emit_agent_status(
        websocket,
        key=key or f"phase_{phase}",
        label=f"{title.rstrip('.')}...",
        description=description,
        state=status,
        source=source,
        workflow_id=mode,
    )
