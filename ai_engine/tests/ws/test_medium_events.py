"""Tests for the medium-impact typed events (Phase 2 Step 4).

Covers:
  - fixers.run_started        (landing_fixers._run)
  - brief.distill_started     (landing_brief.build_landing_brief)
  - research.started          (pipeline/step4_explore + others)
  - cli.session_started       (claude_cli.run_claude_cli)
  - code.write_started        (pipeline/step5_execute)

These sub-step markers duplicate ``task_phase`` but carry a tiny
structured payload (research kind / task_type / model) so the chart can
label the chip instead of just ticking a generic bar forward.
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


# ── fixers.run_started ───────────────────────────────────────────────────


async def test_emit_fixers_run_started(mock_ws):
    from app.services.llm_retry import emit_fixers_run_started

    await emit_fixers_run_started(mock_ws)
    assert mock_ws.send_json.call_args[0][0] == {"type": "fixers.run_started"}


async def test_emit_fixers_run_started_with_none_ws_is_noop():
    from app.services.llm_retry import emit_fixers_run_started

    await emit_fixers_run_started(None)


async def test_emit_fixers_run_started_swallows_send_failures(mock_ws):
    from app.services.llm_retry import emit_fixers_run_started

    mock_ws.send_json.side_effect = RuntimeError("ws disconnected")
    await emit_fixers_run_started(mock_ws)


# ── brief.distill_started ───────────────────────────────────────────────


async def test_emit_brief_distill_started(mock_ws):
    from app.services.llm_retry import emit_brief_distill_started

    await emit_brief_distill_started(mock_ws)
    assert mock_ws.send_json.call_args[0][0] == {"type": "brief.distill_started"}


async def test_emit_brief_distill_started_with_none_ws_is_noop():
    from app.services.llm_retry import emit_brief_distill_started

    await emit_brief_distill_started(None)


# ── research.started ────────────────────────────────────────────────────


async def test_emit_research_started_with_kind_and_label(mock_ws):
    from app.services.llm_retry import emit_research_started

    await emit_research_started(
        mock_ws, kind="requirements", label="Researching project requirements",
    )
    payload = mock_ws.send_json.call_args[0][0]
    assert payload == {
        "type": "research.started",
        "kind": "requirements",
        "label": "Researching project requirements",
    }


async def test_emit_research_started_with_only_kind(mock_ws):
    from app.services.llm_retry import emit_research_started

    await emit_research_started(mock_ws, kind="design")
    payload = mock_ws.send_json.call_args[0][0]
    assert payload["kind"] == "design"
    assert payload["label"] == ""


async def test_emit_research_started_with_none_ws_is_noop():
    from app.services.llm_retry import emit_research_started

    await emit_research_started(None, kind="design")


async def test_emit_research_started_swallows_send_failures(mock_ws):
    from app.services.llm_retry import emit_research_started

    mock_ws.send_json.side_effect = RuntimeError("ws disconnected")
    await emit_research_started(mock_ws, kind="design")


# ── cli.session_started ─────────────────────────────────────────────────


async def test_emit_cli_session_started_with_model(mock_ws):
    from app.services.llm_retry import emit_cli_session_started

    await emit_cli_session_started(mock_ws, model="claude-sonnet-4-6")
    assert mock_ws.send_json.call_args[0][0] == {
        "type": "cli.session_started",
        "model": "claude-sonnet-4-6",
    }


async def test_emit_cli_session_started_defaults_model_to_empty(mock_ws):
    from app.services.llm_retry import emit_cli_session_started

    await emit_cli_session_started(mock_ws)
    payload = mock_ws.send_json.call_args[0][0]
    assert payload["model"] == ""


async def test_emit_cli_session_started_with_none_ws_is_noop():
    from app.services.llm_retry import emit_cli_session_started

    await emit_cli_session_started(None, model="x")


# ── code.write_started ──────────────────────────────────────────────────


async def test_emit_code_write_started_with_task_and_model(mock_ws):
    from app.services.llm_retry import emit_code_write_started

    await emit_code_write_started(
        mock_ws, task_type="ui_simple", model="sonnet",
    )
    assert mock_ws.send_json.call_args[0][0] == {
        "type": "code.write_started",
        "task_type": "ui_simple",
        "model": "sonnet",
    }


async def test_emit_code_write_started_defaults_to_empty_strings(mock_ws):
    from app.services.llm_retry import emit_code_write_started

    await emit_code_write_started(mock_ws)
    payload = mock_ws.send_json.call_args[0][0]
    assert payload["task_type"] == ""
    assert payload["model"] == ""


async def test_emit_code_write_started_with_none_ws_is_noop():
    from app.services.llm_retry import emit_code_write_started

    await emit_code_write_started(None, task_type="x", model="y")


async def test_emit_code_write_started_swallows_send_failures(mock_ws):
    from app.services.llm_retry import emit_code_write_started

    mock_ws.send_json.side_effect = RuntimeError("ws disconnected")
    await emit_code_write_started(mock_ws, task_type="x")
