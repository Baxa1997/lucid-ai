"""Unit tests for website_plan and website_pipeline foundation builders.

No Claude or Gemini calls. Validates plan validation/normalization and
the deterministic foundation file builders.

Run: docker exec lucid-ai-ai_engine-1 python /app/test_website_plan_and_pipeline.py
"""
import sys
sys.path.insert(0, "/app")
from app.services.website_plan import (
    _looks_valid, _normalize, _fallback_with_brand,
)
from app.services.website_pipeline import (
    _build_foundation_files, _build_site_config,
    _build_navigation, _build_design_system,
)


# ─── website_plan tests ────────────────────────────────────────────────

def test_looks_valid_happy():
    plan = {
        "brand": {"name": "X"},
        "pages": [{"route": "/", "title": "Home", "sections": [{"type": "hero"}]}],
    }
    assert _looks_valid(plan)
    print("  ✓ looks_valid: happy")


def test_looks_valid_rejects_missing_brand():
    assert not _looks_valid({"pages": []})
    assert not _looks_valid({"brand": {}, "pages": [{"route": "/", "title": "X", "sections": [{"type": "h"}]}]})
    assert not _looks_valid("not a dict")
    print("  ✓ looks_valid: rejects bad inputs")


def test_looks_valid_rejects_empty_pages():
    p = {"brand": {"name": "X"}, "pages": []}
    assert not _looks_valid(p)
    p = {"brand": {"name": "X"}, "pages": [{"route": "/", "title": "X", "sections": []}]}
    assert not _looks_valid(p)
    print("  ✓ looks_valid: rejects empty pages/sections")


def test_normalize_moves_home_first():
    plan = {
        "brand": {"name": "X"},
        "pages": [
            {"route": "/about", "title": "About", "sections": [{"type": "hero"}]},
            {"route": "/",      "title": "Home",  "sections": [{"type": "hero"}]},
        ],
    }
    out = _normalize(plan)
    assert out["pages"][0]["route"] == "/", f"home should be first, got {out['pages'][0]['route']}"
    assert out["pages"][1]["route"] == "/about"
    print("  ✓ normalize: home moved to first")


def test_normalize_dedupes():
    plan = {
        "brand": {"name": "X"},
        "pages": [
            {"route": "/about", "title": "About", "sections": [{"type": "hero"}]},
            {"route": "/about", "title": "Dup",   "sections": [{"type": "hero"}]},
            {"route": "/",      "title": "Home",  "sections": [{"type": "hero"}]},
        ],
    }
    out = _normalize(plan)
    routes = [p["route"] for p in out["pages"]]
    assert routes == ["/", "/about"], routes
    print("  ✓ normalize: dedupes")


def test_normalize_synthesizes_home_if_missing():
    plan = {
        "brand": {"name": "X"},
        "pages": [
            {"route": "/about", "title": "About", "sections": [{"type": "hero"}]},
            {"route": "/menu",  "title": "Menu",  "sections": [{"type": "menu"}]},
        ],
    }
    out = _normalize(plan)
    assert out["pages"][0]["route"] == "/"
    assert out["pages"][0]["title"] == "Home"
    print("  ✓ normalize: synthesizes home when missing")


def test_normalize_drops_forbidden_routes():
    plan = {
        "brand": {"name": "X"},
        "pages": [
            {"route": "/",        "title": "Home",    "sections": [{"type": "hero"}]},
            {"route": "/about",   "title": "About",   "sections": [{"type": "story"}]},
            {"route": "/privacy", "title": "Privacy", "sections": [{"type": "hero"}]},  # forbidden
            {"route": "/blog",    "title": "Blog",    "sections": [{"type": "hero"}]},  # forbidden
            {"route": "/login",   "title": "Login",   "sections": [{"type": "hero"}]},  # forbidden
            {"route": "/contact", "title": "Contact", "sections": [{"type": "contact"}]},
        ],
    }
    out = _normalize(plan)
    routes = [p["route"] for p in out["pages"]]
    assert routes == ["/", "/about", "/contact"], routes
    print(f"  ✓ normalize: drops forbidden routes (/privacy, /blog, /login)")


def test_normalize_lowercases_section_types():
    plan = {
        "brand": {"name": "X"},
        "pages": [
            {"route": "/", "title": "H", "sections": [{"type": "Hero"}, {"type": "VALUE_PROP"}]},
        ],
    }
    out = _normalize(plan)
    types = [s["type"] for s in out["pages"][0]["sections"]]
    assert types == ["hero", "value_prop"], types
    print("  ✓ normalize: lowercases section types")


def test_fallback_uses_intent():
    intent = {"business_category": "coffee_shop", "business_subcategory": "espresso bar"}
    plan = _fallback_with_brand(intent)
    assert plan["brand"]["name"] == "espresso bar"
    assert plan["brand"]["domain"] == "coffee_shop"
    assert len(plan["pages"]) >= 3
    print(f"  ✓ fallback: brand from intent — {plan['brand']}")


# ─── foundation builders ───────────────────────────────────────────────

def test_site_config():
    js = _build_site_config("Caffè Verona", "Florence's coffee since 1923")
    assert "Caffè Verona" in js
    assert "Florence's coffee since 1923" in js
    assert "export const siteConfig" in js
    print(f"  ✓ site_config: {len(js)} chars")


def test_navigation():
    pages = [
        {"route": "/",        "title": "Home"},
        {"route": "/menu",    "title": "Menu"},
        {"route": "/about",   "title": "About"},
        {"route": "/contact", "title": "Contact"},
    ]
    js = _build_navigation(pages)
    # Home should NOT appear in mainNav (no item for "/")
    assert "/menu" in js
    assert "/about" in js
    assert "/contact" in js
    assert "Menu" in js
    assert "export const mainNav" in js
    assert "export const footerNav" in js
    print(f"  ✓ navigation: {len(js)} chars, excludes home")


def test_design_system_bold():
    js = _build_design_system("bold")
    assert "py-24 md:py-32" in js   # bolder spacing
    assert "rounded-2xl" in js       # bolder card
    assert "export const ds" in js
    assert "framer-motion" not in js  # don't reference lib by name, just props
    print(f"  ✓ design_system bold: {len(js)} chars")


def test_design_system_subtle():
    js = _build_design_system("subtle")
    assert "py-16 md:py-24" in js   # restrained spacing
    assert "rounded-lg" in js        # subtler card
    print(f"  ✓ design_system subtle: {len(js)} chars")


def test_foundation_full_plan():
    plan = {
        "brand": {"name": "Caffè Verona", "tagline": "Since 1923", "domain": "food_and_beverage"},
        "pages": [
            {"route": "/",        "title": "Home"},
            {"route": "/menu",    "title": "Menu",     "sections": [{"type": "hero"}]},
            {"route": "/about",   "title": "About",    "sections": [{"type": "story"}]},
            {"route": "/contact", "title": "Contact",  "sections": [{"type": "contact"}]},
        ],
    }
    vd = {"cultural_intensity": "bold"}
    files = _build_foundation_files(plan, vd, design_signal={})

    # Required foundation files
    assert "src/config/site.js" in files
    assert "src/config/navigation.js" in files
    assert "src/lib/design-system.js" in files

    # Route shells for inner pages
    assert "src/app/menu/page.js" in files
    assert "src/app/about/page.js" in files
    assert "src/app/contact/page.js" in files
    assert "src/app/page.js" not in files  # home is NOT a deterministic shell

    # Each route shell imports from src/components/pages/<route>/<PageName>
    menu_shell = files["src/app/menu/page.js"]
    assert 'from "@/components/pages/menu/MenuPage"' in menu_shell
    assert "<MenuPage />" in menu_shell
    print(f"  ✓ foundation_full_plan: {len(files)} files (site/nav/ds + 3 route shells)")


def test_foundation_skips_dynamic_routes():
    plan = {
        "brand": {"name": "X"},
        "pages": [
            {"route": "/",          "title": "H"},
            {"route": "/blog/[id]", "title": "Post"},
        ],
    }
    files = _build_foundation_files(plan, {}, design_signal={})
    assert "src/app/blog/[id]/page.js" not in files
    assert "src/app/blog" not in str(files)
    print("  ✓ foundation: skips dynamic routes")


def main():
    print("="*72)
    print("WEBSITE PLAN + PIPELINE FOUNDATION — UNIT TESTS")
    print("="*72)
    tests = [
        test_looks_valid_happy,
        test_looks_valid_rejects_missing_brand,
        test_looks_valid_rejects_empty_pages,
        test_normalize_moves_home_first,
        test_normalize_dedupes,
        test_normalize_synthesizes_home_if_missing,
        test_normalize_drops_forbidden_routes,
        test_normalize_lowercases_section_types,
        test_fallback_uses_intent,
        test_site_config,
        test_navigation,
        test_design_system_bold,
        test_design_system_subtle,
        test_foundation_full_plan,
        test_foundation_skips_dynamic_routes,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except AssertionError as e:
            print(f"  ✗ {t.__name__}: {e}")
        except Exception as e:
            print(f"  ✗ {t.__name__} threw: {e}")
    print(f"\nSCORE: {passed}/{len(tests)}")
    print("="*72)


if __name__ == "__main__":
    main()
