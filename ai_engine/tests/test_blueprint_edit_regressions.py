"""Client-stability regression checks for blueprint intake and project edits.

These tests are deliberately pure-logic:
  - no Vertex/Gemini network calls
  - no Supabase calls
  - no Codex subprocess
  - no project generation

They protect the edges most likely to create a bad client demo:
ambiguous blueprints, Uzbek apostrophes in prompts, vague edit messages, and
LLM edit-target responses that try to route toward invented files.

Run from ai_engine/:
    venv/bin/python tests/test_blueprint_edit_regressions.py
or, when pytest is installed:
    venv/bin/python -m pytest tests/test_blueprint_edit_regressions.py -v
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Required app env placeholders. The suite must never depend on real secrets.
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "x")
os.environ.setdefault("SUPABASE_JWT_SECRET", "x")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "x")
os.environ.setdefault("ENCRYPTION_KEY", "x" * 32)
os.environ.setdefault("INTERNAL_API_KEY", "x")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "test-project")
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", "global")


def _run(coro):
    return asyncio.run(coro)


async def _gemini_clear(*_args, **_kwargs) -> str:
    """Fake clarity Gemini response used when deterministic gates should pass."""
    return json.dumps({"clear": True})


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _workspace() -> tempfile.TemporaryDirectory[str]:
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    _write(root / "src/app/page.jsx", "export default function Home(){ return <main /> }\n")
    _write(root / "src/app/about/page.jsx", "export default function About(){ return <main /> }\n")
    _write(
        root / "src/components/sections/Hero.jsx",
        "export function Hero(){ return <section className=\"shadow-md\">Old headline</section> }\n",
    )
    _write(
        root / "src/components/sections/Testimonials.jsx",
        "export function Testimonials(){ return <section>Customer quote</section> }\n",
    )
    _write(
        root / "src/content/landing.json",
        json.dumps({"hero": {"headline": "Old headline"}}, indent=2),
    )
    return tmp


# ── Blueprint / prompt intake regressions ──────────────────────────────


def test_blueprint_with_roles_admin_still_asks_project_type() -> None:
    from app.services.clarity_agent import check_prompt_clarity

    task = """Blueprint
Overview: local service booking system
Roles: admin, customer, vendor
Features: auth, orders, payments
Backend: Supabase
Frontend: React
"""
    with patch("app.services.landing_gemini.structured_distill", side_effect=_gemini_clear):
        q = _run(check_prompt_clarity(task, {}, timeout_s=0.01))
    assert q is not None
    assert q["key"] == "project_type"
    option_ids = {opt["id"] for opt in q["options"]}
    assert {"landing_page", "full_website", "admin_dashboard", "marketing_with_admin", "web_app"} <= option_ids


def test_blueprint_with_frontend_backend_only_still_asks_project_type() -> None:
    from app.services.clarity_agent import check_prompt_clarity

    task = """Product blueprint
Requirements: users can upload blueprint files and generate previews
Modules: auth, billing, project history
Frontend: Next.js
Backend: Supabase
"""
    with patch("app.services.landing_gemini.structured_distill", side_effect=_gemini_clear):
        q = _run(check_prompt_clarity(task, {}, timeout_s=0.01))
    assert q is not None
    assert q["key"] == "project_type"


def test_blueprint_with_explicit_pages_can_pass_without_extra_question() -> None:
    from app.services.clarity_agent import check_prompt_clarity

    task = """Blueprint
Pages: /, /menu, /reservations, /contact
Features: reservation form, gallery, opening hours
"""
    with patch("app.services.landing_gemini.structured_distill", side_effect=_gemini_clear):
        q = _run(check_prompt_clarity(task, {}, timeout_s=0.01))
    assert q is None


def test_explicit_admin_dashboard_blueprint_can_pass_without_role_confusion() -> None:
    from app.services.clarity_agent import check_prompt_clarity

    task = """Blueprint
Admin dashboard: manage orders, inventory, users, refunds
Features: KPI cards, data table filters, export CSV
"""
    with patch("app.services.landing_gemini.structured_distill", side_effect=_gemini_clear):
        q = _run(check_prompt_clarity(task, {}, timeout_s=0.01))
    assert q is None


def test_domain_only_uzbek_apostrophe_prompt_asks_project_type() -> None:
    from app.services.clarity_agent import check_prompt_clarity

    with patch("app.services.landing_gemini.structured_distill", side_effect=_gemini_clear):
        q = _run(check_prompt_clarity("Italian restaurant in Farg'ona", {}, timeout_s=0.01))
    assert q is not None
    assert q["key"] == "project_type"


def test_bare_landing_page_asks_for_field() -> None:
    from app.services.clarity_agent import check_prompt_clarity

    with patch("app.services.landing_gemini.structured_distill", side_effect=_gemini_clear):
        q = _run(check_prompt_clarity("landing page", {}, timeout_s=0.01))
    assert q is not None
    assert q["key"] == "field"
    assert q["options"] == []


# ── Existing-project edit regressions ─────────────────────────────────


def test_vague_edit_messages_clarify_before_agent_runs() -> None:
    from app.services.followup_intent import classify_followup_message

    for text in ("change it", "edit", "make better", "do something"):
        out = classify_followup_message(text, mode="edit")
        assert out["action"] == "clarify"
        assert "change" in out["message"].lower() or "edit" in out["message"].lower()


def test_specific_edit_message_proceeds() -> None:
    from app.services.followup_intent import classify_followup_message

    out = classify_followup_message("Change the hero headline to Fresh bookings", mode="edit")
    assert out["action"] == "proceed"


def test_question_in_edit_mode_routes_to_discuss() -> None:
    from app.services.followup_intent import classify_followup_message

    out = classify_followup_message("How does the booking form work?", mode="edit")
    assert out["action"] == "discuss"


def test_edit_intent_clamps_hallucinated_target_files() -> None:
    from app.services.pipeline.step3b_edit_intent import extract_edit_intent

    async def _fake_distill(*_args, **_kwargs) -> str:
        return json.dumps({
            "target_pages": ["/"],
            "target_sections": ["Hero", "MadeUpSection"],
            "target_components": [],
            "target_files": [
                "src/components/sections/Hero.jsx",
                "src/components/sections/DoesNotExist.jsx",
            ],
            "change_type": "style",
            "literal_anchors": ["shadow-md"],
            "scope": "narrow",
            "confidence": 95,
        })

    with _workspace() as root:
        with patch("app.services.landing_gemini.structured_distill", side_effect=_fake_distill):
            intent = _run(extract_edit_intent(
                task="Change hero shadow-md to shadow-lg",
                workspace_path=root,
                classification={"task_type": "edit", "complexity": "small"},
            ))

    assert intent.extracted is True
    assert intent.scope == "narrow"
    assert intent.is_actionable is True
    assert intent.target_sections == ["hero"]
    assert intent.candidate_files == ["src/components/sections/Hero.jsx"]


def test_edit_intent_missing_literal_anchor_downgrades_actionability() -> None:
    from app.services.pipeline.step3b_edit_intent import extract_edit_intent

    async def _fake_distill(*_args, **_kwargs) -> str:
        return json.dumps({
            "target_pages": ["/"],
            "target_sections": ["Hero"],
            "target_components": [],
            "target_files": ["src/components/sections/Hero.jsx"],
            "change_type": "content",
            "literal_anchors": ["This text is not present"],
            "scope": "narrow",
            "confidence": 94,
        })

    with _workspace() as root:
        with patch("app.services.landing_gemini.structured_distill", side_effect=_fake_distill):
            intent = _run(extract_edit_intent(
                task="Replace This text is not present in the hero",
                workspace_path=root,
                classification={"task_type": "edit", "complexity": "small"},
            ))

    assert intent.extracted is True
    assert intent.scope == "ambiguous"
    assert intent.confidence <= 50
    assert intent.is_actionable is False


# ── Self-runner so the suite works without pytest installed ────────────


def _main() -> int:
    tests = [(k, v) for k, v in globals().items() if k.startswith("test_") and callable(v)]
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
