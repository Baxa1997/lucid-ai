"""Tests for the build.start / build.result typed events (Phase 2 Step 4).

Replaces the loose ``type: "build"`` + free-form message pair from
BuildValidator with structured events the frontend can render as a chart
chip (install spinner → build spinner → pass/fail/timeout chip).
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


# ── build.start ──────────────────────────────────────────────────────────


async def test_emit_build_start_install_phase(mock_ws):
    from app.services.llm_retry import emit_build_start

    await emit_build_start(
        mock_ws,
        package_manager="pnpm",
        command="pnpm install --prefer-offline",
        phase="install",
    )
    payload = mock_ws.send_json.call_args[0][0]
    assert payload == {
        "type": "build.start",
        "phase": "install",
        "package_manager": "pnpm",
        "command": "pnpm install --prefer-offline",
    }


async def test_emit_build_start_defaults_to_build_phase(mock_ws):
    from app.services.llm_retry import emit_build_start

    await emit_build_start(mock_ws, package_manager="npm", command="npm run build")
    payload = mock_ws.send_json.call_args[0][0]
    assert payload["phase"] == "build"
    assert payload["type"] == "build.start"


async def test_emit_build_start_with_none_websocket_is_noop():
    from app.services.llm_retry import emit_build_start

    await emit_build_start(None, package_manager="npm", command="npm run build")


async def test_emit_build_start_swallows_send_failures(mock_ws):
    from app.services.llm_retry import emit_build_start

    mock_ws.send_json.side_effect = RuntimeError("ws disconnected")
    await emit_build_start(mock_ws, package_manager="npm", command="npm run build")


# ── build.result ─────────────────────────────────────────────────────────


async def test_emit_build_result_success(mock_ws):
    from app.services.llm_retry import emit_build_result

    await emit_build_result(mock_ws, success=True, attempts=0, fixed_count=0)
    payload = mock_ws.send_json.call_args[0][0]
    assert payload == {
        "type": "build.result",
        "success": True,
        "attempts": 0,
        "error_count": 0,
        "fixed_count": 0,
        "install_failed": False,
        "timed_out": False,
        "needs_fix": False,
    }


async def test_emit_build_result_failure_with_fixes(mock_ws):
    from app.services.llm_retry import emit_build_result

    await emit_build_result(
        mock_ws,
        success=False,
        attempts=2,
        error_count=3,
        fixed_count=4,
        needs_fix=True,
    )
    payload = mock_ws.send_json.call_args[0][0]
    assert payload["success"] is False
    assert payload["attempts"] == 2
    assert payload["error_count"] == 3
    assert payload["fixed_count"] == 4
    assert payload["needs_fix"] is True


async def test_emit_build_result_timeout_flag(mock_ws):
    from app.services.llm_retry import emit_build_result

    await emit_build_result(
        mock_ws,
        success=False,
        attempts=1,
        error_count=1,
        timed_out=True,
        needs_fix=True,
    )
    payload = mock_ws.send_json.call_args[0][0]
    assert payload["timed_out"] is True
    assert payload["install_failed"] is False


async def test_emit_build_result_install_failed_flag(mock_ws):
    from app.services.llm_retry import emit_build_result

    await emit_build_result(
        mock_ws,
        success=False,
        attempts=1,
        error_count=1,
        install_failed=True,
        needs_fix=True,
    )
    payload = mock_ws.send_json.call_args[0][0]
    assert payload["install_failed"] is True
    assert payload["timed_out"] is False


async def test_emit_build_result_type_is_dotted_namespace(mock_ws):
    from app.services.llm_retry import emit_build_result

    await emit_build_result(mock_ws, success=True)
    assert mock_ws.send_json.call_args[0][0]["type"] == "build.result"


async def test_emit_build_result_with_none_websocket_is_noop():
    from app.services.llm_retry import emit_build_result

    await emit_build_result(None, success=True)


async def test_emit_build_result_swallows_send_failures(mock_ws):
    from app.services.llm_retry import emit_build_result

    mock_ws.send_json.side_effect = RuntimeError("ws disconnected")
    await emit_build_result(mock_ws, success=False, attempts=1, error_count=2)
