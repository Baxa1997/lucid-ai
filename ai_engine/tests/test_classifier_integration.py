"""Integration tests for the USE_CLASSIFIER_AGENT flag wiring.

Covers (without firing real Gemini in CI):
  1. Flag off:  existing ``check_prompt_clarity`` path is taken.
  2. Flag on, clear prompt with force-archetype marker: resolver
     short-circuits, no question asked, archetype marker stays on task.
  3. Flag on, ambiguous prompt: resolver returns ``needs_clarification``
     with a question dict shaped for the existing ``clarification_needed``
     WS event.
  4. Flag on, resolver raises: falls back gracefully (the production
     fall-through is in ws.py; this test exercises the resolver itself
     refusing bad input).
  5. Force-archetype marker is preserved through ``force_archetype_from_task``
     so ``_generate_new_project_inner`` short-circuits its classifier.

These are pure-logic tests using monkey-patched stubs in place of Gemini.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "x")
os.environ.setdefault("SUPABASE_JWT_SECRET", "x")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "x")
os.environ.setdefault("ENCRYPTION_KEY", "x" * 32)
os.environ.setdefault("INTERNAL_API_KEY", "x")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")


def _run(coro):
    return asyncio.run(coro)


# ── 1. Flag default ───────────────────────────────────────────────────

def test_flag_defaults_to_true() -> None:
    from app.config import settings
    assert settings.USE_CLASSIFIER_AGENT is True, (
        "USE_CLASSIFIER_AGENT is the canonical prompt-entry router."
    )


# ── 2. Resolver short-circuits on FORCE_ARCHETYPE marker ──────────────

def test_resolver_short_circuits_on_force_marker() -> None:
    """When the task already has a force-archetype marker, the resolver
    returns ``resolved`` immediately without any Gemini call."""
    from app.services.project_classifier_agent import resolve_classification

    async def _no_gemini(*a, **kw):
        raise AssertionError("Gemini must not be called on force-marker path")

    with patch("app.services.clarity_agent.check_prompt_clarity",
               side_effect=_no_gemini):
        result = _run(resolve_classification(
            "[LUCID_FORCE_ARCHETYPE::single_page_landing] italian restaurant",
            extract_entities=False,
        ))

    assert result["status"] == "resolved"
    assert result["archetype"] == "single_page_landing"


def test_resolver_short_circuits_on_clarify_marker() -> None:
    """When the user has already answered project_type via a prior turn,
    the resolver short-circuits without re-asking."""
    from app.services.project_classifier_agent import resolve_classification

    async def _no_gemini(*a, **kw):
        raise AssertionError("Gemini must not be called when project_type is in markers")

    with patch("app.services.clarity_agent.check_prompt_clarity",
               side_effect=_no_gemini):
        result = _run(resolve_classification(
            "[LUCID_CLARIFY::project_type=admin_dashboard] manage reservations",
            extract_entities=False,
        ))

    assert result["status"] == "resolved"
    assert result["archetype"] == "admin_dashboard"


# ── 3. Resolver returns needs_clarification with WS-compatible shape ──

def test_resolver_clarification_shape_matches_ws_event() -> None:
    """The needs_clarification payload must contain the fields the WS
    layer hands to ``clarification_needed``: clarify_key, question,
    options. The frontend's useAgentSession.js handler at line 1282
    reads exactly these keys."""
    from app.services.project_classifier_agent import resolve_classification

    async def _fake_route(*a, **kw):
        return {
            "action": "clarify",
            "clarify_key": "project_type",
            "question": "What type of project do you need?",
            "reasoning": "The domain is present but project type is missing.",
        }

    with patch("app.services.project_classifier_agent.route_new_project_with_gemini",
               side_effect=_fake_route):
        result = _run(resolve_classification(
            "italian restaurant in Brooklyn",
            extract_entities=False,
        ))

    assert result["status"] == "needs_clarification"
    assert result["clarify_key"] == "project_type"
    assert result["question"]
    assert isinstance(result["options"], list)
    assert result["options"] == []


# ── 4. Resolver fallback when extract_entities is enabled but fails ───

def test_resolver_entity_extraction_failure_does_not_crash() -> None:
    """If the underlying Gemini call inside extract_admin_entities fails,
    the resolver must still return a valid resolved payload —
    entities=[] and confidence=0. The function has its own try/except;
    we patch the Gemini call (``structured_distill``) so the production
    error path is exercised."""
    from app.services.project_classifier_agent import resolve_classification

    async def _fail_gemini(*a, **kw):
        raise RuntimeError("simulated Gemini timeout")

    with patch(
        "app.services.landing_gemini.structured_distill",
        side_effect=_fail_gemini,
    ):
        result = _run(resolve_classification(
            "[LUCID_CLARIFY::project_type=admin_dashboard] manage stuff",
            extract_entities=True,
        ))

    assert result["status"] == "resolved"
    assert result["archetype"] == "admin_dashboard"
    assert result.get("entities") == []
    assert result.get("entity_confidence", -1) == 0


# ── 5. force_archetype_from_task round-trip ───────────────────────────

def test_force_archetype_marker_survives_to_classifier() -> None:
    """The ws.py wiring injects the FORCE_ARCHETYPE marker; verify the
    same marker is correctly extracted by ``force_archetype_from_task``
    (used inside ``_generate_new_project_inner``) so the downstream
    classifier short-circuits."""
    from knowledge.loader import force_archetype_from_task, ARCHETYPE_LOCK_PREFIX

    archetypes = [
        "single_page_landing",
        "consumer_website",
        "admin_dashboard",
        "consumer_website_with_admin",
    ]
    for arch in archetypes:
        marker = f"{ARCHETYPE_LOCK_PREFIX}{arch}] some user task"
        parsed, cleaned = force_archetype_from_task(marker)
        assert parsed == arch, f"expected {arch}, got {parsed}"
        assert "some user task" in cleaned
        assert ARCHETYPE_LOCK_PREFIX not in cleaned


# ── 6. classify_project_type_ai honors force_archetype param ──────────

def test_classify_short_circuits_on_force_archetype() -> None:
    """When ``classify_project_type_ai`` is called with a valid
    force_archetype, it returns immediately — no Gemini call."""
    from knowledge.loader import classify_project_type_ai

    result = _run(classify_project_type_ai(
        "some random unclassifiable prompt",
        force_archetype="consumer_website_with_admin",
    ))
    assert result["layout_archetype"] == "consumer_website_with_admin"
    assert result.get("classification_locked") is True


# ── Self-runner ───────────────────────────────────────────────────────

def _main() -> int:
    tests = [(k, v) for k, v in globals().items()
             if k.startswith("test_") and callable(v)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  OK   {name}")
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL {name}: {exc}")
        except Exception as exc:
            failed += 1
            print(f"  ERROR {name}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(_main())
