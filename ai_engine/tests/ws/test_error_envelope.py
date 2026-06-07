"""Tests for the unified error envelope (Phase 2 Step 1).

The new ``emit_error()`` helper sends a single ``type: "error"`` event with
a ``severity`` field that the frontend uses to discriminate from legacy
(severity-less) error events. These tests pin down the envelope's shape +
defaults so the contract is stable across migrations.

Coverage:
  • Shape contract — every required field present + correctly typed.
  • Severity validation — bad input falls back to 'recoverable'.
  • retriable default — derived from severity when not passed.
  • recovery_hint default — per-code mapping when omitted.
  • Fail-soft — None websocket and broken send don't raise.
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
    """Async-mock WebSocket that records every send_json payload."""
    ws = AsyncMock()
    ws.send_json = AsyncMock()
    return ws


# ── Shape contract ──────────────────────────────────────────────────────


async def test_emit_error_sends_unified_shape(mock_ws):
    """Every required field is present with the correct type."""
    from app.services.llm_retry import emit_error

    await emit_error(
        mock_ws,
        phase="research",
        code="rate_limit",
        severity="recoverable",
        message="Too many requests right now.",
    )

    assert mock_ws.send_json.call_count == 1
    payload = mock_ws.send_json.call_args[0][0]

    assert payload["type"] == "error"
    assert payload["phase"] == "research"
    assert payload["code"] == "rate_limit"
    assert payload["severity"] == "recoverable"
    assert payload["message"] == "Too many requests right now."
    assert payload["retriable"] is True
    assert isinstance(payload["recovery_hint"], str)
    assert payload["recovery_hint"]  # non-empty


async def test_emit_error_carries_severity_field_for_frontend_discrimination(mock_ws):
    """`severity` is the frontend's discriminator from the legacy `error` type.

    The unified handler routes ONLY when severity is present. So this field's
    presence is load-bearing — guard it against regressions.
    """
    from app.services.llm_retry import emit_error

    await emit_error(
        mock_ws,
        phase="preview",
        code="timeout",
        severity="warn",
        message="Dev server slow to start.",
    )
    payload = mock_ws.send_json.call_args[0][0]
    assert "severity" in payload, "severity field MUST be present — frontend depends on it for routing"


# ── Severity validation ─────────────────────────────────────────────────


@pytest.mark.parametrize("severity", ["warn", "recoverable", "fatal"])
async def test_emit_error_accepts_valid_severities(mock_ws, severity):
    from app.services.llm_retry import emit_error

    await emit_error(
        mock_ws,
        phase="code",
        code="unknown",
        severity=severity,
        message="x",
    )
    assert mock_ws.send_json.call_args[0][0]["severity"] == severity


async def test_emit_error_falls_back_to_recoverable_for_invalid_severity(mock_ws):
    """Caller bug shouldn't drop the event — degrade to a safe default."""
    from app.services.llm_retry import emit_error

    await emit_error(
        mock_ws,
        phase="code",
        code="unknown",
        severity="catastrophic",  # not a valid value
        message="x",
    )
    payload = mock_ws.send_json.call_args[0][0]
    assert payload["severity"] == "recoverable"


# ── retriable default derivation ─────────────────────────────────────────


@pytest.mark.parametrize(
    "severity, expected_retriable",
    [
        ("warn", None),
        ("recoverable", True),
        ("fatal", False),
    ],
)
async def test_emit_error_derives_retriable_from_severity(
    mock_ws, severity, expected_retriable,
):
    """When `retriable` isn't passed, derive it from severity."""
    from app.services.llm_retry import emit_error

    await emit_error(
        mock_ws,
        phase="research",
        code="unknown",
        severity=severity,
        message="x",
    )
    assert mock_ws.send_json.call_args[0][0]["retriable"] == expected_retriable


async def test_emit_error_honors_explicit_retriable_over_severity_default(mock_ws):
    """Explicit retriable wins over the severity-derived default."""
    from app.services.llm_retry import emit_error

    await emit_error(
        mock_ws,
        phase="research",
        code="unknown",
        severity="fatal",
        message="x",
        retriable=True,  # override the fatal=False default
    )
    assert mock_ws.send_json.call_args[0][0]["retriable"] is True


# ── recovery_hint defaults ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "code, expected_substring",
    [
        ("rate_limit", "rate-limiting"),
        ("auth_error", "session"),
        ("content_blocked", "rephrasing"),
        ("network", "Network"),
        ("timeout", "too long"),
        ("bad_request", "malformed"),
        ("unknown", "Please try again"),
    ],
)
async def test_emit_error_fills_default_recovery_hint_per_code(
    mock_ws, code, expected_substring,
):
    """Per-code recovery hint is filled in when not passed."""
    from app.services.llm_retry import emit_error

    await emit_error(
        mock_ws,
        phase="code",
        code=code,
        severity="recoverable",
        message="x",
    )
    hint = mock_ws.send_json.call_args[0][0]["recovery_hint"]
    assert expected_substring.lower() in hint.lower(), (
        f"expected hint for code={code} to mention {expected_substring!r}, got {hint!r}"
    )


async def test_emit_error_honors_explicit_recovery_hint(mock_ws):
    """Explicit recovery_hint wins over the per-code default."""
    from app.services.llm_retry import emit_error

    await emit_error(
        mock_ws,
        phase="code",
        code="rate_limit",
        severity="recoverable",
        message="x",
        recovery_hint="Custom recovery copy.",
    )
    assert mock_ws.send_json.call_args[0][0]["recovery_hint"] == "Custom recovery copy."


async def test_emit_error_falls_back_to_unknown_recovery_hint_for_unknown_code(mock_ws):
    """A code we don't recognise still gets a non-empty hint."""
    from app.services.llm_retry import emit_error

    await emit_error(
        mock_ws,
        phase="code",
        code="some_made_up_code",
        severity="recoverable",
        message="x",
    )
    hint = mock_ws.send_json.call_args[0][0]["recovery_hint"]
    assert hint  # non-empty fallback


# ── Fail-soft contract ──────────────────────────────────────────────────


async def test_emit_error_with_none_websocket_is_noop():
    """No websocket → drop silently. Never raise into the caller."""
    from app.services.llm_retry import emit_error

    # Just verify it doesn't raise
    await emit_error(
        None,
        phase="research",
        code="rate_limit",
        severity="recoverable",
        message="x",
    )


async def test_emit_error_swallows_send_failures(mock_ws):
    """ws.send_json raising must NOT bubble into the caller — telemetry-safe."""
    from app.services.llm_retry import emit_error

    mock_ws.send_json.side_effect = RuntimeError("socket disconnected")

    # Caller of emit_error never wraps it in try/except, so a raise here
    # would tear down whatever pipeline is reporting the error.
    await emit_error(
        mock_ws,
        phase="research",
        code="network",
        severity="fatal",
        message="x",
    )
