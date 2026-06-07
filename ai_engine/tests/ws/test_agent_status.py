from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

pytestmark = [pytest.mark.unit, pytest.mark.ws, pytest.mark.asyncio]


async def test_emit_agent_status_shape():
    from app.services.agent_status import emit_agent_status

    ws = AsyncMock()
    await emit_agent_status(
        ws,
        key="analyzing_prompt",
        label="Analyzing your prompt...",
        description="Gemini Flash is deciding what to do next",
        source="gemini_flash",
    )

    payload = ws.send_json.call_args[0][0]
    assert payload == {
        "type": "agent.status",
        "key": "analyzing_prompt",
        "label": "Analyzing your prompt...",
        "description": "Gemini Flash is deciding what to do next",
        "state": "active",
        "source": "gemini_flash",
        "workflow_id": "",
    }


async def test_emit_task_phase_also_emits_canonical_agent_status():
    from app.services.agent_status import emit_task_phase

    ws = AsyncMock()
    await emit_task_phase(
        ws,
        phase=3,
        title="Researching project",
        description="Finding relevant references",
        status="active",
        mode="landing_generation",
        key="research",
    )

    assert ws.send_json.call_count == 2
    phase, current = [call.args[0] for call in ws.send_json.call_args_list]
    assert phase["type"] == "task_phase"
    assert current["type"] == "agent.status"
    assert current["key"] == "research"
    assert current["label"] == "Researching project..."
    assert current["description"] == "Finding relevant references"
