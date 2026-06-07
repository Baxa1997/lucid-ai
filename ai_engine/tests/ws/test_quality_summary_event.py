"""Tests for the quality.summary typed event (Phase 2 Step 4).

The two free-form "✓ Quality gate: …" / "✅ Quality gate: …" progress
messages are being replaced with a structured ``quality.summary`` event
the frontend can render as a status chip. The full ``quality_report``
event (per-check chart data) is unchanged.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


pytestmark = [pytest.mark.unit, pytest.mark.ws, pytest.mark.asyncio]


@pytest.fixture
def mock_ws():
    ws = AsyncMock()
    ws.send_json = AsyncMock()
    return ws


async def test_emits_quality_summary_with_all_fields(mock_ws):
    from app.services.llm_retry import emit_quality_summary

    await emit_quality_summary(
        mock_ws,
        passed=8,
        total=10,
        blockers=1,
        warnings=2,
        purpose="apply-form",
    )
    payload = mock_ws.send_json.call_args[0][0]
    assert payload == {
        "type": "quality.summary",
        "passed": 8,
        "total": 10,
        "blockers": 1,
        "warnings": 2,
        "purpose": "apply-form",
    }


async def test_optional_fields_default_to_zero_and_empty(mock_ws):
    from app.services.llm_retry import emit_quality_summary

    await emit_quality_summary(mock_ws, passed=5, total=5)
    payload = mock_ws.send_json.call_args[0][0]
    assert payload["blockers"] == 0
    assert payload["warnings"] == 0
    assert payload["purpose"] == ""


async def test_type_is_dotted_namespace(mock_ws):
    from app.services.llm_retry import emit_quality_summary

    await emit_quality_summary(mock_ws, passed=1, total=1)
    assert mock_ws.send_json.call_args[0][0]["type"] == "quality.summary"


async def test_with_none_websocket_is_noop():
    from app.services.llm_retry import emit_quality_summary

    # Just verify it doesn't raise.
    await emit_quality_summary(None, passed=10, total=10)


async def test_swallows_send_failures(mock_ws):
    from app.services.llm_retry import emit_quality_summary

    mock_ws.send_json.side_effect = RuntimeError("ws disconnected")
    await emit_quality_summary(mock_ws, passed=10, total=10)
