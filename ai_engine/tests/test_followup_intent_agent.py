from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def _gemini_response(payload: dict) -> dict:
    return {
        "candidates": [{
            "content": {
                "parts": [{"text": json.dumps(payload)}],
            },
        }],
    }


async def test_gemini_router_selects_discuss(monkeypatch):
    from app.services.followup_intent import classify_followup_with_gemini
    from app.services import gemini_http

    async def fake_post(**_kwargs):
        return 200, _gemini_response({
            "action": "discuss",
            "reasoning": "The user asked how the current code works.",
        }), ""

    monkeypatch.setattr(gemini_http, "gemini_post", fake_post)
    result = await classify_followup_with_gemini("How does the checkout flow work?")
    assert result["action"] == "discuss"
    assert result["workflow_id"] == "discuss"
    assert result["decided_by"] == "gemini_flash"


async def test_gemini_router_selects_clarification(monkeypatch):
    from app.services.followup_intent import classify_followup_with_gemini
    from app.services import gemini_http

    async def fake_post(**_kwargs):
        return 200, _gemini_response({
            "action": "clarify",
            "reply": "What specifically would you like me to change?",
            "reasoning": "The request is vague.",
        }), ""

    monkeypatch.setattr(gemini_http, "gemini_post", fake_post)
    result = await classify_followup_with_gemini("make it better")
    assert result["action"] == "clarify"
    assert result["message"].endswith("?")
