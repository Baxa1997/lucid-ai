"""Tests for project_classifier_agent.

Pure-logic tests (no Gemini calls) verify the resolver's deterministic
behavior given pre-baked clarify markers. Live Gemini tests are in
``scripts/test_classifier_5_prompts.py`` so we don't burn API budget on
every test run.

Run from ai_engine/:
    venv/bin/python -m pytest tests/test_project_classifier_agent.py -v
or:
    venv/bin/python tests/test_project_classifier_agent.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Required app env (placeholders for things we don't actually exercise).
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "x")
os.environ.setdefault("SUPABASE_JWT_SECRET", "x")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "x")
os.environ.setdefault("ENCRYPTION_KEY", "x" * 32)
os.environ.setdefault("INTERNAL_API_KEY", "x")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")

from app.services.project_classifier_agent import (
    resolve_classification, _build_resolved, _stage_for_key,
    STAGE_RESOLVED, STAGE_ASK_PRODUCT_TYPE, STAGE_ASK_SITE_DEPTH,
)
from knowledge.loader import (
    map_project_type_to_archetype, LAYOUT_ARCHETYPES, get_structural_family,
)


# ── Pure-logic tests ──────────────────────────────────────────────────

def test_archetype_map_landing() -> None:
    assert map_project_type_to_archetype("landing_page") == "single_page_landing"
    assert map_project_type_to_archetype("one_pager") == "single_page_landing"
    assert map_project_type_to_archetype("promo") == "single_page_landing"


def test_archetype_map_full_website() -> None:
    assert map_project_type_to_archetype("full_website") == "consumer_website"
    assert map_project_type_to_archetype("website") == "consumer_website"
    assert map_project_type_to_archetype("multi_page") == "consumer_website"


def test_archetype_map_admin() -> None:
    assert map_project_type_to_archetype("admin_dashboard") == "admin_dashboard"
    assert map_project_type_to_archetype("admin_panel") == "admin_dashboard"
    assert map_project_type_to_archetype("back_office") == "admin_dashboard"
    # Bare "admin" still maps to admin_dashboard (post-extension)
    assert map_project_type_to_archetype("admin") == "admin_dashboard"


def test_archetype_map_marketing_with_admin() -> None:
    assert map_project_type_to_archetype("marketing_with_admin") == "consumer_website_with_admin"
    assert map_project_type_to_archetype("site_with_admin") == "consumer_website_with_admin"
    assert map_project_type_to_archetype("both") == "consumer_website_with_admin"


def test_archetype_map_saas_separate_from_admin() -> None:
    # saas_dashboard should NOT collapse to admin_dashboard
    assert map_project_type_to_archetype("saas") == "saas_dashboard"
    assert map_project_type_to_archetype("web_app") == "saas_dashboard"


def test_archetype_map_unknown_returns_none() -> None:
    assert map_project_type_to_archetype("") is None
    assert map_project_type_to_archetype("garbage_value") is None


def test_consumer_website_with_admin_is_registered() -> None:
    assert "consumer_website_with_admin" in LAYOUT_ARCHETYPES
    meta = LAYOUT_ARCHETYPES["consumer_website_with_admin"]
    assert meta["has_admin_features"] is True
    assert meta["is_single_page"] is False
    # Routes through the consumer family so the website pipeline picks it up
    assert get_structural_family("consumer_website_with_admin") == "consumer"


def test_stage_for_key() -> None:
    assert _stage_for_key("project_type", {}) == STAGE_ASK_PRODUCT_TYPE
    assert _stage_for_key("site_depth", {"project_type": "landing_page"}) == STAGE_ASK_SITE_DEPTH
    # Empty key with no prior answers → INITIAL
    from app.services.project_classifier_agent import STAGE_INITIAL
    assert _stage_for_key("", {}) == STAGE_INITIAL


def test_build_resolved_marks_admin_followup() -> None:
    out = _build_resolved(
        archetype="consumer_website_with_admin",
        answers={"project_type": "marketing_with_admin"},
        description="x",
        extract_entities=False,
        reasoning="t",
    )
    assert out["status"] == "resolved"
    assert out["needs_admin_followup"] is True
    assert out["stage"] == STAGE_RESOLVED
    assert "entities" not in out  # entity extraction disabled


def test_build_resolved_does_not_mark_pure_admin_as_followup() -> None:
    out = _build_resolved(
        archetype="admin_dashboard",
        answers={"project_type": "admin_dashboard"},
        description="x",
        extract_entities=False,
        reasoning="t",
    )
    assert out["needs_admin_followup"] is False


# ── Resolver path tests (extract_entities disabled so no Gemini calls) ──

def _run(coro):
    return asyncio.run(coro)


def test_resolver_with_force_archetype_marker_bypasses_clarity() -> None:
    """A [LUCID_FORCE_ARCHETYPE::single_page_landing] marker resolves
    immediately without calling Gemini."""
    task = "[LUCID_FORCE_ARCHETYPE::single_page_landing] italian restaurant"
    result = _run(resolve_classification(task, extract_entities=False))
    assert result["status"] == "resolved"
    assert result["archetype"] == "single_page_landing"
    assert result["needs_admin_followup"] is False


def test_resolver_with_project_type_marker_shortcircuits() -> None:
    """A [LUCID_CLARIFY::project_type=landing_page] marker resolves
    without re-asking Gemini."""
    task = "[LUCID_CLARIFY::project_type=landing_page] coffee shop in Florence"
    result = _run(resolve_classification(task, extract_entities=False))
    assert result["status"] == "resolved"
    assert result["archetype"] == "single_page_landing"


def test_resolver_marketing_with_admin_marker() -> None:
    task = ("[LUCID_CLARIFY::project_type=marketing_with_admin] "
            "italian restaurant in Brooklyn")
    result = _run(resolve_classification(task, extract_entities=False))
    assert result["status"] == "resolved"
    assert result["archetype"] == "consumer_website_with_admin"
    assert result["needs_admin_followup"] is True


def test_resolver_admin_dashboard_marker() -> None:
    task = ("[LUCID_CLARIFY::project_type=admin_dashboard] "
            "internal tool to manage reservations")
    result = _run(resolve_classification(task, extract_entities=False))
    assert result["status"] == "resolved"
    assert result["archetype"] == "admin_dashboard"
    assert result["needs_admin_followup"] is False


# ── 8 new prompt scenarios (post-marker resolution paths) ─────────────
# These verify the resolution shortcuts for the 8 scenarios from the
# spec. Each test glues on the LUCID_CLARIFY marker the user *would*
# select after Gemini asks, and asserts the resolver does the right
# thing. Live Gemini behavior (what question gets asked first) is
# covered by scripts/test_classifier_5_prompts.py.

def test_resolver_ecom_with_inventory_picks_both() -> None:
    task = ("[LUCID_CLARIFY::project_type=marketing_with_admin] "
            "online store for vintage cameras with inventory management")
    result = _run(resolve_classification(task, extract_entities=False))
    assert result["archetype"] == "consumer_website_with_admin"
    assert result["needs_admin_followup"] is True


def test_resolver_internal_tool_resolves_admin() -> None:
    task = ("[LUCID_CLARIFY::project_type=admin_dashboard] "
            "internal tool to manage my team's tasks")
    result = _run(resolve_classification(task, extract_entities=False))
    assert result["archetype"] == "admin_dashboard"
    assert result["needs_admin_followup"] is False


def test_resolver_portfolio_picks_portfolio() -> None:
    task = ("[LUCID_CLARIFY::project_type=portfolio] "
            "showcase my photography portfolio")
    result = _run(resolve_classification(task, extract_entities=False))
    assert result["archetype"] == "portfolio"


def test_resolver_track_customers_resolves_admin() -> None:
    task = ("[LUCID_CLARIFY::project_type=admin_dashboard] "
            "I need a way to track my customers and send invoices")
    result = _run(resolve_classification(task, extract_entities=False))
    assert result["archetype"] == "admin_dashboard"


def test_resolver_saas_landing_resolves_landing() -> None:
    task = ("[LUCID_CLARIFY::project_type=landing_page] "
            "build a SaaS landing page for my analytics product")
    result = _run(resolve_classification(task, extract_entities=False))
    assert result["archetype"] == "single_page_landing"


def test_resolver_law_firm_with_case_mgmt_picks_both() -> None:
    task = ("[LUCID_CLARIFY::project_type=marketing_with_admin] "
            "marketing site for my law firm with case management for staff")
    result = _run(resolve_classification(task, extract_entities=False))
    assert result["archetype"] == "consumer_website_with_admin"
    assert result["needs_admin_followup"] is True


def test_resolver_yoga_with_booking_full_website_path() -> None:
    """When user picks full_website for a yoga studio + booking prompt,
    bookings stay customer-facing (not admin)."""
    task = ("[LUCID_CLARIFY::project_type=full_website] "
            "Tashkent yoga studio with booking")
    result = _run(resolve_classification(task, extract_entities=False))
    assert result["archetype"] == "consumer_website"
    assert result["needs_admin_followup"] is False


def test_resolver_company_with_employee_portal_picks_both() -> None:
    task = ("[LUCID_CLARIFY::project_type=marketing_with_admin] "
            "company website with employee portal")
    result = _run(resolve_classification(task, extract_entities=False))
    assert result["archetype"] == "consumer_website_with_admin"
    assert result["needs_admin_followup"] is True


# ── Self-runner so the file works without pytest ──────────────────────

def _main() -> int:
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  OK   {fn.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL {fn.__name__}: {exc}")
        except Exception as exc:
            failed += 1
            print(f"  ERROR {fn.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(_main())
