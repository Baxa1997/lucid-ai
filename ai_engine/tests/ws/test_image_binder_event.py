"""Tests for the image_binder.summary typed event (Phase 2 Step 4).

The free-form "✅ Bound N/M images" progress message is being replaced
with a structured ``image_binder.summary`` event carrying counts the
frontend can render properly. These tests pin down the envelope.

Coverage:
  • Shape contract — type, requested, bound, unbound, geo_rejected,
    subject_rejected, retry_used.
  • Defaults — optional fields default to 0 when not passed.
  • Fail-soft — None websocket + send failure don't bubble.
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


async def test_emits_image_binder_summary_with_all_fields(mock_ws):
    from app.services.llm_retry import emit_image_binder_summary

    await emit_image_binder_summary(
        mock_ws,
        requested=12,
        bound=11,
        unbound=1,
        geo_rejected=3,
        subject_rejected=2,
        retry_used=4,
    )
    payload = mock_ws.send_json.call_args[0][0]
    assert payload == {
        "type": "image_binder.summary",
        "requested": 12,
        "bound": 11,
        "unbound": 1,
        "geo_rejected": 3,
        "subject_rejected": 2,
        "retry_used": 4,
    }


async def test_optional_fields_default_to_zero(mock_ws):
    from app.services.llm_retry import emit_image_binder_summary

    await emit_image_binder_summary(mock_ws, requested=5, bound=5)
    payload = mock_ws.send_json.call_args[0][0]
    assert payload["unbound"] == 0
    assert payload["geo_rejected"] == 0
    assert payload["subject_rejected"] == 0
    assert payload["retry_used"] == 0


async def test_type_is_dotted_namespace(mock_ws):
    """Typed events use dotted namespace so dispatchers can route by prefix."""
    from app.services.llm_retry import emit_image_binder_summary

    await emit_image_binder_summary(mock_ws, requested=1, bound=1)
    assert mock_ws.send_json.call_args[0][0]["type"] == "image_binder.summary"


async def test_with_none_websocket_is_noop():
    from app.services.llm_retry import emit_image_binder_summary

    # Just verify it doesn't raise.
    await emit_image_binder_summary(None, requested=10, bound=10)


async def test_swallows_send_failures(mock_ws):
    from app.services.llm_retry import emit_image_binder_summary

    mock_ws.send_json.side_effect = RuntimeError("ws disconnected")
    await emit_image_binder_summary(mock_ws, requested=10, bound=10)
