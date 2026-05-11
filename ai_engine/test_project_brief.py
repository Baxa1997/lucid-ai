"""Tests for project_brief — multi-page Project Brief.

Covers:
  • _fallback_brief — produces valid structure for every purpose
  • _normalize_brief — coerces partial / malformed Gemini output to the
    schema downstream stages expect
  • _normalize_page_entry — slugs, section_types, primary_cta normalization

Run from ai_engine/:
    python3 test_project_brief.py
"""
from __future__ import annotations

import importlib.util
import os
import sys

# Load project_brief.py directly to avoid the full app package init
# (Supabase, postgrest, OpenHands etc. aren't available bare).
# The module imports `from app.services.pipeline.constants import _FALLBACK_GEMINI_KEY`
# which would also trigger app init; we stub it out with a tiny shim.
_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))


def _stub_constants_module() -> None:
    """Insert minimal `app.services.pipeline.constants` into sys.modules
    so project_brief's top-level import resolves without dragging in the
    rest of the FastAPI app."""
    import types
    pkgs = ["app", "app.services", "app.services.pipeline", "app.services.pipeline.constants"]
    for name in pkgs[:-1]:
        if name not in sys.modules:
            mod = types.ModuleType(name)
            mod.__path__ = []  # mark as package
            sys.modules[name] = mod
    constants = types.ModuleType("app.services.pipeline.constants")
    constants._FALLBACK_GEMINI_KEY = ""  # tests never make real calls
    sys.modules["app.services.pipeline.constants"] = constants


_stub_constants_module()

_MODULE_PATH = os.path.join(_ENGINE_DIR, "app", "services", "project_brief.py")
_spec = importlib.util.spec_from_file_location("project_brief", _MODULE_PATH)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

_fallback_brief         = _mod._fallback_brief
_normalize_brief        = _mod._normalize_brief
_normalize_page_entry   = _mod._normalize_page_entry
_VALID_SECTION_TYPES    = _mod._VALID_SECTION_TYPES
_HEADER_ARCHETYPES      = _mod._HEADER_ARCHETYPES
_FOOTER_ARCHETYPES      = _mod._FOOTER_ARCHETYPES


# ── Helpers ───────────────────────────────────────────────────────────

REQUIRED_TOP_LEVEL = [
    "brand", "palette", "typography", "design_system",
    "pages", "ctas", "domain_keywords",
    "motif", "header_archetype", "footer_archetype", "personality",
]
REQUIRED_PALETTE_KEYS = [
    "primary", "secondary", "accent", "background",
    "foreground", "muted", "border", "card",
]
REQUIRED_DS_KEYS = [
    "motion", "accent_shape", "surface", "image_treatment", "section_rhythm",
]
REQUIRED_PAGE_KEYS = ["slug", "title", "nav_label", "page_goal", "primary_cta", "section_types"]


def _assert_valid_brief(brief: dict, *, min_pages: int = 1) -> None:
    """Structural invariants every brief must satisfy."""
    for k in REQUIRED_TOP_LEVEL:
        assert k in brief, f"missing top-level key: {k}"
    for k in REQUIRED_PALETTE_KEYS:
        assert brief["palette"].get(k), f"palette.{k} empty"
        assert brief["palette"][k].startswith("#"), f"palette.{k} not hex: {brief['palette'][k]!r}"
    for k in REQUIRED_DS_KEYS:
        assert brief["design_system"].get(k), f"design_system.{k} empty"
    assert brief["typography"]["heading_font"], "heading_font empty"
    assert brief["typography"]["body_font"],    "body_font empty"
    assert brief["header_archetype"] in _HEADER_ARCHETYPES, f"bad header: {brief['header_archetype']!r}"
    assert brief["footer_archetype"] in _FOOTER_ARCHETYPES, f"bad footer: {brief['footer_archetype']!r}"
    assert isinstance(brief["pages"], list), "pages not a list"
    assert len(brief["pages"]) >= min_pages, f"only {len(brief['pages'])} pages, expected ≥ {min_pages}"
    # First page is always the home page (slug "")
    assert brief["pages"][0]["slug"] == "", f"home page slug = {brief['pages'][0]['slug']!r}, expected ''"
    # Each page has all required keys + hero first
    for i, p in enumerate(brief["pages"]):
        for k in REQUIRED_PAGE_KEYS:
            assert k in p, f"pages[{i}] missing {k}"
        assert isinstance(p["section_types"], list) and len(p["section_types"]) >= 1
        assert p["section_types"][0] == "hero", f"pages[{i}].section_types[0] = {p['section_types'][0]!r}, expected 'hero'"
        for t in p["section_types"]:
            assert t in _VALID_SECTION_TYPES, f"pages[{i}] has unknown section_type: {t!r}"
        assert p["primary_cta"]["label"], f"pages[{i}].primary_cta.label empty"
        assert p["primary_cta"]["href"],  f"pages[{i}].primary_cta.href empty"


# ── Fallback tests — every purpose ────────────────────────────────────

def test_fallback_lead_generation():
    b = _fallback_brief("acme consulting in NYC", "consulting", "lead_generation")
    _assert_valid_brief(b, min_pages=4)
    slugs = [p["slug"] for p in b["pages"]]
    assert "" in slugs and "contact" in slugs


def test_fallback_ecommerce():
    b = _fallback_brief("vintage shop online", "ecommerce", "ecommerce")
    _assert_valid_brief(b, min_pages=3)
    assert any(p["slug"] == "shop" for p in b["pages"])


def test_fallback_hiring():
    b = _fallback_brief("CDL drivers wanted", "logistics", "hiring")
    _assert_valid_brief(b, min_pages=4)
    assert any(p["slug"] == "apply" for p in b["pages"])
    apply_page = next(p for p in b["pages"] if p["slug"] == "apply")
    assert "application_form" in apply_page["section_types"]


def test_fallback_brand_awareness():
    b = _fallback_brief("Brooklyn bakery", "restaurant", "brand_awareness")
    _assert_valid_brief(b, min_pages=3)


def test_fallback_booking():
    b = _fallback_brief("dental clinic", "healthcare", "booking")
    _assert_valid_brief(b, min_pages=4)
    assert any(p["slug"] == "book" for p in b["pages"])
    book_page = next(p for p in b["pages"] if p["slug"] == "book")
    assert "booking_form" in book_page["section_types"]


def test_fallback_signup():
    b = _fallback_brief("project mgmt SaaS", "saas", "signup")
    _assert_valid_brief(b, min_pages=3)
    assert any(p["slug"] == "signup" for p in b["pages"])


def test_fallback_unknown_purpose_uses_lead_gen():
    """Unknown purpose should fall back to lead_generation, not crash."""
    b = _fallback_brief("something weird", "general", "fundraising")
    _assert_valid_brief(b, min_pages=4)
    # Lead-gen template's tail page is contact
    slugs = [p["slug"] for p in b["pages"]]
    assert "contact" in slugs


def test_fallback_empty_inputs():
    b = _fallback_brief("", "", "")
    _assert_valid_brief(b, min_pages=4)
    assert b["brand"]["name"]


# ── _normalize_page_entry ─────────────────────────────────────────────

def test_page_entry_strips_leading_slash():
    fb = {"title": "About", "page_goal": "About us", "primary_cta": {"label": "Contact", "href": "/contact"}, "section_types": ["hero", "story"]}
    np = _normalize_page_entry({"slug": "/about", "title": "About", "section_types": ["story", "team"]}, fb)
    assert np["slug"] == "about"
    assert np["section_types"][0] == "hero", "hero should be inserted first"
    assert "story" in np["section_types"]
    assert "team" in np["section_types"]


def test_page_entry_normalizes_home_aliases():
    fb = {"title": "Home", "page_goal": "Welcome", "primary_cta": {"label": "Go", "href": "/"}, "section_types": ["hero", "value_prop"]}
    for alias in ("/", "home", "index", "Home", "  / "):
        np = _normalize_page_entry({"slug": alias, "title": "Home", "section_types": ["value_prop"]}, fb)
        assert np["slug"] == "", f"alias {alias!r} → slug {np['slug']!r}"


def test_page_entry_drops_unknown_section_types():
    fb = {"title": "X", "page_goal": "g", "primary_cta": {"label": "a", "href": "/"}, "section_types": ["hero", "value_prop"]}
    np = _normalize_page_entry({
        "slug": "x",
        "title": "X",
        "section_types": ["hero", "value_prop", "rocket_ship", "moon_walk", "faq"],
    }, fb)
    assert "rocket_ship" not in np["section_types"]
    assert "moon_walk" not in np["section_types"]
    assert "faq" in np["section_types"]


def test_page_entry_strips_footer_from_section_types():
    """Footer is rendered globally — never appears in a page's section list."""
    fb = {"title": "X", "page_goal": "g", "primary_cta": {"label": "a", "href": "/"}, "section_types": ["hero", "value_prop"]}
    np = _normalize_page_entry({
        "slug": "x",
        "title": "X",
        "section_types": ["hero", "footer", "value_prop", "footer"],
    }, fb)
    assert "footer" not in np["section_types"]


def test_page_entry_caps_section_types_at_8():
    fb = {"title": "X", "page_goal": "g", "primary_cta": {"label": "a", "href": "/"}, "section_types": ["hero", "value_prop"]}
    np = _normalize_page_entry({
        "slug": "x",
        "title": "X",
        "section_types": [
            "hero", "value_prop", "features", "services", "products",
            "testimonials", "stats", "pricing", "faq", "case_studies", "cta",
        ],
    }, fb)
    assert len(np["section_types"]) == 8


def test_page_entry_falls_back_when_no_usable_section_types():
    fb = {"title": "X", "page_goal": "g", "primary_cta": {"label": "a", "href": "/"}, "section_types": ["hero", "story", "cta"]}
    np = _normalize_page_entry({"slug": "x", "title": "X", "section_types": ["nonsense_type"]}, fb)
    # Only "hero" would survive — that's < 2, so fallback kicks in.
    assert np["section_types"] == ["hero", "story", "cta"]


def test_page_entry_inserts_hero_when_missing():
    fb = {"title": "X", "page_goal": "g", "primary_cta": {"label": "a", "href": "/"}, "section_types": ["hero", "story"]}
    np = _normalize_page_entry({"slug": "x", "title": "X", "section_types": ["story", "team", "cta"]}, fb)
    assert np["section_types"][0] == "hero"
    assert "story" in np["section_types"]


# ── _normalize_brief — full-document tests ────────────────────────────

def _gemini_lookalike() -> dict:
    """Pretend output from Gemini — partially right, partially missing."""
    return {
        "brand": {
            "name":    "Trattoria del Sole",
            "tagline": "Brooklyn's heart in Italian cuisine",
            "description": "Family-owned Italian trattoria in Brooklyn since 1998.",
            "domain":  "trattoriadelsole.com",
        },
        "palette": {
            "primary":    "#7c2d12",
            "secondary":  "#a16207",
            "accent":     "#facc15",
            "background": "#fefce8",
            "foreground": "#1c1917",
            "muted":      "#fef3c7",
            "border":     "#fde68a",
            "card":       "#ffffff",
        },
        "typography": {
            "heading_font": "Playfair Display",
            "body_font":    "Inter",
        },
        "design_system": {
            "motion":          "fade-rise",
            "accent_shape":    "soft-rounded",
            "surface":         "soft-shadow",
            "image_treatment": "full-bleed-photo",
            "section_rhythm":  "alternating-bg",
        },
        "header_archetype": "transparent-pill",
        "footer_archetype": "centered-stack",
        "pages": [
            {"slug": "/", "title": "Home", "page_goal": "Drive bookings",
             "section_types": ["hero", "menu", "story", "testimonials", "cta"],
             "primary_cta": {"label": "Book a table", "href": "/book"}},
            {"slug": "menu", "title": "Menu", "page_goal": "Show the food",
             "section_types": ["hero", "menu", "footer"],
             "primary_cta": {"label": "Reserve", "href": "/book"}},
            {"slug": "/book", "title": "Book", "page_goal": "Capture reservations",
             "section_types": ["hero", "booking_form", "hours"],
             "primary_cta": {"label": "Confirm", "href": "#booking-form"}},
        ],
        "ctas": {"primary": {"label": "Book a table", "href": "/book"}},
        "domain_keywords": ["italian", "brooklyn", "trattoria", "wine bar", "homemade pasta"],
    }


def test_normalize_full_pass():
    raw = _gemini_lookalike()
    b = _normalize_brief(raw, "italian restaurant in brooklyn", "restaurant", "booking")
    _assert_valid_brief(b, min_pages=3)
    assert b["brand"]["name"] == "Trattoria del Sole"
    assert b["palette"]["primary"] == "#7c2d12"
    # First page is home (slug="") — model said "/", normalized to ""
    assert b["pages"][0]["slug"] == ""
    # Second page kept its slug
    assert b["pages"][1]["slug"] == "menu"
    # /book → book
    book_page = next(p for p in b["pages"] if p["slug"] == "book")
    assert "booking_form" in book_page["section_types"]
    # Footer stripped from menu page section_types
    menu_page = next(p for p in b["pages"] if p["slug"] == "menu")
    assert "footer" not in menu_page["section_types"]


def test_normalize_handles_missing_palette_keys():
    raw = _gemini_lookalike()
    raw["palette"].pop("border")
    raw["palette"].pop("card")
    b = _normalize_brief(raw, "x", "y", "lead_generation")
    assert b["palette"]["border"].startswith("#")
    assert b["palette"]["card"].startswith("#")


def test_normalize_rejects_invalid_archetypes():
    raw = _gemini_lookalike()
    raw["header_archetype"] = "rainbow-banner"
    raw["footer_archetype"] = "exploded-grid"
    b = _normalize_brief(raw, "x", "y", "lead_generation")
    assert b["header_archetype"] in _HEADER_ARCHETYPES
    assert b["footer_archetype"] in _FOOTER_ARCHETYPES


def test_normalize_dedupes_duplicate_slugs():
    raw = _gemini_lookalike()
    raw["pages"].append({
        "slug": "menu", "title": "Duplicate Menu", "page_goal": "dup",
        "section_types": ["hero", "menu"],
        "primary_cta": {"label": "x", "href": "/x"},
    })
    b = _normalize_brief(raw, "x", "y", "booking")
    slugs = [p["slug"] for p in b["pages"]]
    assert slugs.count("menu") == 1


def test_normalize_synthesizes_home_when_missing():
    """If Gemini returned pages but none was home, prepend a home entry."""
    raw = _gemini_lookalike()
    raw["pages"] = [p for p in raw["pages"] if p["slug"] not in ("/", "")]
    b = _normalize_brief(raw, "x", "y", "booking")
    assert b["pages"][0]["slug"] == ""


def test_normalize_falls_back_when_pages_empty():
    raw = _gemini_lookalike()
    raw["pages"] = []
    b = _normalize_brief(raw, "x", "y", "booking")
    _assert_valid_brief(b, min_pages=4)


def test_normalize_caps_pages_at_6():
    raw = _gemini_lookalike()
    raw["pages"] = [
        {"slug": f"p{i}", "title": f"P{i}", "page_goal": "g",
         "section_types": ["hero", "story"],
         "primary_cta": {"label": "x", "href": "/x"}}
        for i in range(10)
    ]
    b = _normalize_brief(raw, "x", "y", "lead_generation")
    assert len(b["pages"]) <= 7  # 6 model pages + at most 1 synthesized home


def test_normalize_keeps_secondary_cta_when_present():
    raw = _gemini_lookalike()
    raw["ctas"]["secondary"] = {"label": "View Menu", "href": "/menu"}
    b = _normalize_brief(raw, "x", "y", "booking")
    assert "secondary" in b["ctas"]
    assert b["ctas"]["secondary"]["label"] == "View Menu"


def test_normalize_drops_secondary_cta_when_incomplete():
    raw = _gemini_lookalike()
    raw["ctas"]["secondary"] = {"label": "View Menu"}  # no href
    b = _normalize_brief(raw, "x", "y", "booking")
    assert "secondary" not in b["ctas"]


# ── Manual runner ─────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL  {t.__name__}: {exc}")
        except Exception as exc:
            failed += 1
            print(f"ERROR {t.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(0 if failed == 0 else 1)
