"""Tests for the pipeline.declare envelope (Phase 2 Step 2).

The new ``declare_pipeline_phases()`` helper emits a single
``type: "pipeline.declare"`` event listing the phases this pipeline run
will go through, so the frontend chart can render against an actual
list instead of assuming 1-8.

Coverage:
  • Shape contract — type, pipeline_id, phases array, per-phase fields.
  • Default phase list per pipeline_id (new vs edit).
  • Custom phase list override.
  • Fail-soft on None websocket + send failure.
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


# ── Shape contract ──────────────────────────────────────────────────────


async def test_declare_pipeline_phases_sends_pipeline_declare_event(mock_ws):
    from app.services.llm_retry import declare_pipeline_phases

    await declare_pipeline_phases(mock_ws, pipeline_id="new")

    assert mock_ws.send_json.call_count == 1
    payload = mock_ws.send_json.call_args[0][0]
    assert payload["type"] == "pipeline.declare"
    assert payload["pipeline_id"] == "new"
    assert isinstance(payload["phases"], list)
    assert payload["phases"]  # non-empty


async def test_each_declared_phase_has_key_label_index(mock_ws):
    """Per-phase dict shape — key, label, index must all be present."""
    from app.services.llm_retry import declare_pipeline_phases

    await declare_pipeline_phases(mock_ws, pipeline_id="new")
    payload = mock_ws.send_json.call_args[0][0]
    for i, ph in enumerate(payload["phases"], start=1):
        assert "key" in ph, f"phase {i} missing key"
        assert "label" in ph, f"phase {i} missing label"
        assert ph["index"] == i, f"phase {i} index should be {i}, got {ph['index']}"
        assert isinstance(ph["key"], str)
        assert isinstance(ph["label"], str)


# ── Default phase sequences per pipeline_id ─────────────────────────────


async def test_pipeline_new_emits_validate_setup_research_plan_code_verify(mock_ws):
    """The new-project sequence covers the full generation path."""
    from app.services.llm_retry import declare_pipeline_phases

    await declare_pipeline_phases(mock_ws, pipeline_id="new")
    keys = [p["key"] for p in mock_ws.send_json.call_args[0][0]["phases"]]
    # The first 6 are stable; tail (publish/deploy) is allowed to evolve.
    assert keys[:6] == ["validate", "setup", "research", "plan", "code", "verify"]


async def test_pipeline_edit_skips_research_and_plan(mock_ws):
    """Edit mode goes validate → setup → classify → explore → code, not research."""
    from app.services.llm_retry import declare_pipeline_phases

    await declare_pipeline_phases(mock_ws, pipeline_id="edit")
    keys = [p["key"] for p in mock_ws.send_json.call_args[0][0]["phases"]]
    assert "classify" in keys
    assert "explore" in keys
    assert "research" not in keys
    assert "plan" not in keys


async def test_unknown_pipeline_id_falls_back_to_new_sequence(mock_ws):
    """Forward-compat: unknown pipeline_id renders the new-project sequence."""
    from app.services.llm_retry import declare_pipeline_phases

    await declare_pipeline_phases(mock_ws, pipeline_id="regenerate")
    keys = [p["key"] for p in mock_ws.send_json.call_args[0][0]["phases"]]
    assert keys[:2] == ["validate", "setup"]
    assert "research" in keys  # matches the new sequence's research+plan path


# ── Custom phase list override ──────────────────────────────────────────


async def test_caller_can_pass_explicit_phase_list(mock_ws):
    """When `phases` is passed, it replaces the canonical sequence verbatim."""
    from app.services.llm_retry import declare_pipeline_phases

    custom = [("step_a", "Step A"), ("step_b", "Step B")]
    await declare_pipeline_phases(mock_ws, pipeline_id="new", phases=custom)
    payload = mock_ws.send_json.call_args[0][0]
    assert [p["key"] for p in payload["phases"]] == ["step_a", "step_b"]
    assert [p["label"] for p in payload["phases"]] == ["Step A", "Step B"]


# ── Fail-soft contract ──────────────────────────────────────────────────


async def test_declare_with_none_websocket_is_noop():
    from app.services.llm_retry import declare_pipeline_phases

    # No exception, no error — silent drop
    await declare_pipeline_phases(None, pipeline_id="new")


async def test_declare_swallows_send_failures(mock_ws):
    from app.services.llm_retry import declare_pipeline_phases

    mock_ws.send_json.side_effect = RuntimeError("ws disconnected")

    # Must not bubble — declare is fired during pipeline init and a
    # disconnect here shouldn't tear down the run.
    await declare_pipeline_phases(mock_ws, pipeline_id="edit")
