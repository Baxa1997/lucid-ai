"""Pure-Python tests for page_generator helpers and prompt assembly.

No Claude calls — validates name conventions, slug logic, anatomy lookup,
and prompt structure.

Run: docker exec lucid-ai-ai_engine-1 python /app/test_page_generator.py
"""
import sys
sys.path.insert(0, "/app")
from app.services.page_generator import (
    _slug_from_route,
    _slug_pascal,
    _section_component_name,
    _section_anatomy_for,
    _build_system_prompt,
    _build_user_prompt,
    plan_section_components_for_page,
)


def test_slug_conversion():
    cases = [
        ("/",                "home"),
        ("",                 "home"),
        ("/menu",            "menu"),
        ("/about",           "about"),
        ("/private-events",  "private-events"),
        ("/our_story",       "our_story"),
        ("/services/web",    "services/web"),
    ]
    for inp, expected in cases:
        actual = _slug_from_route(inp)
        assert actual == expected, f"slug({inp!r}) = {actual!r}, expected {expected!r}"
    print(f"  ✓ slug_conversion: {len(cases)}/{len(cases)}")


def test_slug_pascal():
    cases = [
        ("home",            "Home"),
        ("menu",            "Menu"),
        ("private-events",  "PrivateEvents"),
        ("our_story",       "OurStory"),
        ("services/web",    "ServicesWeb"),
    ]
    for inp, expected in cases:
        actual = _slug_pascal(inp)
        assert actual == expected, f"pascal({inp!r}) = {actual!r}, expected {expected!r}"
    print(f"  ✓ slug_pascal: {len(cases)}/{len(cases)}")


def test_section_component_name():
    cases = [
        ("home",            "hero",          "HomeHero"),
        ("menu",            "showcase",      "MenuShowcase"),
        ("about",           "story",         "AboutStory"),
        ("private-events",  "gallery",       "PrivateEventsGallery"),
        ("home",            "cta",           "HomeCta"),
        ("contact",         "form",          "ContactForm"),
    ]
    for slug, stype, expected in cases:
        actual = _section_component_name(slug, stype)
        assert actual == expected, f"component({slug},{stype}) = {actual!r}, expected {expected!r}"
    print(f"  ✓ component_naming: {len(cases)}/{len(cases)}")


def test_anatomy_lookup():
    vd = {
        "section_anatomies": {
            "hero":   "section min-h-screen with espresso-photo overlay",
            "menu":   "two-column dotted-leader Italian editorial",
            "footer": "minimalist-row, 1px border-top",
        },
    }
    assert _section_anatomy_for(vd, "hero").startswith("section min-h")
    assert _section_anatomy_for(vd, "menu").startswith("two-column")
    assert _section_anatomy_for(vd, "story") == ""  # not in DNA
    assert _section_anatomy_for({}, "hero") == ""    # empty DNA
    print(f"  ✓ anatomy_lookup")


def test_plan_section_components():
    page = {
        "route": "/menu",
        "sections": [
            {"type": "hero"},
            {"type": "showcase"},
            {"type": "story"},
        ],
    }
    plan = plan_section_components_for_page(page)
    assert plan == ["MenuHero", "MenuShowcase", "MenuStory"], plan
    print(f"  ✓ plan_section_components: {plan}")


def test_system_prompt_with_dna():
    vd = {
        "cultural_intensity": "bold",
        "cultural_palette_emphasis": "Forest greens + cream + terracotta accents",
        "typography_voice": "Editorial serif + clean grotesk body",
        "decorative_motifs": ["olive branch sprig", "hand-poured espresso ring"],
        "signature_textures": ["aged Tuscan stucco", "linen weave"],
        "iconography_anchors": ["espresso cup", "olive leaf"],
    }
    foundation = {
        "globals.css": "Theme palette + Google Fonts",
        "design-system.js": "ds tokens",
    }
    sys_p = _build_system_prompt(
        brand_name="Caffè Verona",
        tagline="Florence's artisan coffee since 1923",
        domain="food_and_beverage",
        visual_dna=vd,
        foundation_imports=foundation,
    )
    # Must include brand + DNA + foundation contract
    assert "Caffè Verona" in sys_p
    assert "Forest greens" in sys_p
    assert "olive branch" in sys_p
    assert "aged Tuscan stucco" in sys_p
    assert "ds.section" in sys_p   # design-system contract
    assert "globals.css" in sys_p
    assert "bold" in sys_p.lower()
    print(f"  ✓ system_prompt: {len(sys_p)} chars, all DNA + foundation refs present")


def test_user_prompt():
    page = {
        "route": "/menu",
        "title": "Our Seasonal Menu",
        "purpose": "Showcase dishes with cultural narrative + price-line typography",
        "sections": [
            {"type": "hero", "purpose": "Open with seasonal headline"},
            {"type": "menu", "purpose": "Category-sorted dish list with photos"},
        ],
    }
    vd = {
        "section_anatomies": {
            "hero": "section min-h-[70vh] espresso-overlay full-bleed",
            "menu": "Italian editorial — categories left, dotted leaders to prices",
        },
    }
    section_specs = [
        {"type": "hero", "component": "MenuHero",
         "purpose": "seasonal headline", "anatomy": vd["section_anatomies"]["hero"]},
        {"type": "menu", "component": "MenuList",
         "purpose": "dishes with prices", "anatomy": vd["section_anatomies"]["menu"]},
    ]
    usr_p = _build_user_prompt(
        page=page, slug="menu",
        section_specs=section_specs, visual_dna=vd,
    )
    assert "/menu" in usr_p
    assert "Our Seasonal Menu" in usr_p
    assert "MenuHero" in usr_p
    assert "MenuList" in usr_p
    assert "src/app/menu/page.js" in usr_p
    assert "src/components/pages/menu" in usr_p
    assert "espresso-overlay" in usr_p
    assert "dotted leaders" in usr_p
    print(f"  ✓ user_prompt: {len(usr_p)} chars, page-specific + anatomy refs present")


def test_home_route():
    """Home route → 'home' slug, page.js at src/app/page.js (no subfolder)."""
    page = {
        "route": "/",
        "title": "Home",
        "sections": [
            {"type": "hero"},
            {"type": "value_prop"},
        ],
    }
    plan = plan_section_components_for_page(page)
    assert plan == ["HomeHero", "HomeValueProp"], plan
    usr_p = _build_user_prompt(
        page=page, slug="home",
        section_specs=[
            {"type": "hero", "component": "HomeHero", "purpose": "", "anatomy": ""},
            {"type": "value_prop", "component": "HomeValueProp", "purpose": "", "anatomy": ""},
        ],
        visual_dna={},
    )
    assert "src/app/page.js" in usr_p     # home special case
    assert "src/components/pages/home" in usr_p
    print(f"  ✓ home_route: composition file at root src/app/page.js")


def main():
    print("="*72)
    print("PAGE GENERATOR — UNIT TESTS")
    print("="*72)
    tests = [
        test_slug_conversion,
        test_slug_pascal,
        test_section_component_name,
        test_anatomy_lookup,
        test_plan_section_components,
        test_system_prompt_with_dna,
        test_user_prompt,
        test_home_route,
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
