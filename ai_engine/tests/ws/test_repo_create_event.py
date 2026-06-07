"""Tests for the repo.create_started / repo.create_done typed events
(Phase 2 Step 4).

Replaces the loose "📦 Creating GitHub repository..." / "✅ Repository
created: …" progress messages from pipeline/orchestrator.py with
structured events the chart can render as a single chip (in-progress →
pass/fail). The follow-up ``type: "repo_created"`` event (used by the FE
for repo-URL navigation) is unchanged.
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


# ── repo.create_started ─────────────────────────────────────────────────


async def test_emit_repo_create_started_user_owned(mock_ws):
    from app.services.llm_retry import emit_repo_create_started

    await emit_repo_create_started(
        mock_ws,
        provider="github",
        repo_name="my-project",
    )
    payload = mock_ws.send_json.call_args[0][0]
    assert payload == {
        "type": "repo.create_started",
        "provider": "github",
        "repo_name": "my-project",
        "owner": "",
        "platform_owned": False,
    }


async def test_emit_repo_create_started_platform_owned_includes_org(mock_ws):
    from app.services.llm_retry import emit_repo_create_started

    await emit_repo_create_started(
        mock_ws,
        provider="github",
        repo_name="proj-12345-abc",
        owner="lucid-ai-platform",
        platform_owned=True,
    )
    payload = mock_ws.send_json.call_args[0][0]
    assert payload["owner"] == "lucid-ai-platform"
    assert payload["platform_owned"] is True


async def test_emit_repo_create_started_defaults_to_github_provider(mock_ws):
    from app.services.llm_retry import emit_repo_create_started

    await emit_repo_create_started(mock_ws, repo_name="x")
    payload = mock_ws.send_json.call_args[0][0]
    assert payload["provider"] == "github"


async def test_emit_repo_create_started_with_none_websocket_is_noop():
    from app.services.llm_retry import emit_repo_create_started

    await emit_repo_create_started(None, repo_name="x")


async def test_emit_repo_create_started_swallows_send_failures(mock_ws):
    from app.services.llm_retry import emit_repo_create_started

    mock_ws.send_json.side_effect = RuntimeError("ws disconnected")
    await emit_repo_create_started(mock_ws, repo_name="x")


# ── repo.create_done ────────────────────────────────────────────────────


async def test_emit_repo_create_done_success(mock_ws):
    from app.services.llm_retry import emit_repo_create_done

    await emit_repo_create_done(
        mock_ws,
        success=True,
        provider="github",
        repo_url="https://github.com/owner/proj",
        repo_name="proj",
        platform_owned=True,
    )
    payload = mock_ws.send_json.call_args[0][0]
    assert payload == {
        "type": "repo.create_done",
        "success": True,
        "provider": "github",
        "repo_url": "https://github.com/owner/proj",
        "repo_name": "proj",
        "platform_owned": True,
        "error": "",
    }


async def test_emit_repo_create_done_failure_with_error_message(mock_ws):
    from app.services.llm_retry import emit_repo_create_done

    await emit_repo_create_done(
        mock_ws,
        success=False,
        provider="github",
        repo_name="proj",
        error="create_github_repo returned no result",
    )
    payload = mock_ws.send_json.call_args[0][0]
    assert payload["success"] is False
    assert payload["repo_url"] == ""
    assert payload["error"] == "create_github_repo returned no result"


async def test_emit_repo_create_done_type_is_dotted_namespace(mock_ws):
    from app.services.llm_retry import emit_repo_create_done

    await emit_repo_create_done(mock_ws, success=True)
    assert mock_ws.send_json.call_args[0][0]["type"] == "repo.create_done"


async def test_emit_repo_create_done_with_none_websocket_is_noop():
    from app.services.llm_retry import emit_repo_create_done

    await emit_repo_create_done(None, success=True)


async def test_emit_repo_create_done_swallows_send_failures(mock_ws):
    from app.services.llm_retry import emit_repo_create_done

    mock_ws.send_json.side_effect = RuntimeError("ws disconnected")
    await emit_repo_create_done(mock_ws, success=False, error="boom")
