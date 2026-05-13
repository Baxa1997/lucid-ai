"""Unit tests for header_footer_generator (no Claude needed)."""
import sys
sys.path.insert(0, "/app")
from app.services.header_footer_generator import (
    _hf_system_prompt,
    _header_user_prompt,
    _footer_user_prompt,
)


def test_system_prompt_header():
    vd = {
        "cultural_intensity": "bold",
        "cultural_palette_emphasis": "Espresso brown + olive green + cream",
        "typography_voice": "Editorial serif wordmark, sans body",
        "decorative_motifs": ["olive branch", "espresso ring"],
        "iconography_anchors": ["espresso cup", "olive leaf"],
        "section_anatomies": {
            "header": "transparent → bg-bg/95 + blur on scroll; wordmark left, nav center, CTA right",
        },
    }
    sp = _hf_system_prompt(
        component="MarketingHeader",
        brand_name="Caffè Verona", tagline="Florence's coffee since 1923",
        domain="food_and_beverage", visual_dna=vd,
    )
    assert "MarketingHeader" in sp
    assert "Caffè Verona" in sp
    assert "Espresso brown" in sp
    assert "olive branch" in sp
    assert "espresso cup" in sp
    assert "transparent → bg-bg" in sp
    assert "siteConfig" in sp
    assert "mainNav" in sp
    print(f"  ✓ system_prompt_header: {len(sp)} chars")


def test_system_prompt_footer_pulls_anatomy():
    """Footer system prompt should pull the footer anatomy, not header."""
    vd = {
        "section_anatomies": {
            "header": "AAA",
            "footer": "BBB-mega-columns layout with social row",
        },
    }
    sp = _hf_system_prompt(
        component="MarketingFooter",
        brand_name="X", tagline="y", domain="z", visual_dna=vd,
    )
    assert "BBB-mega-columns" in sp
    assert "AAA" not in sp   # don't mix up anatomies
    print("  ✓ footer anatomy correctly scoped")


def test_header_user_prompt_includes_cta_table():
    up = _header_user_prompt(brand_name="X", domain="food_and_beverage", visual_dna={})
    assert "Reserve a Table" in up
    assert "Order Online" in up
    assert "useEffect" in up  # scroll-aware instruction
    assert "lucide-react" in up
    assert "mainNav" in up
    print(f"  ✓ header_user_prompt: includes CTA table + scroll behavior")


def test_footer_user_prompt_variants():
    up = _footer_user_prompt(brand_name="X", domain="food_and_beverage", visual_dna={})
    assert "mega-columns" in up
    assert "minimalist-row" in up
    assert "cta-band" in up
    assert "footerNav" in up
    print(f"  ✓ footer_user_prompt: includes variant hints")


def test_footer_variant_hint_from_dna():
    vd = {"section_anatomies": {"footer": "minimalist-row 1-line links + small social row"}}
    up = _footer_user_prompt(brand_name="X", domain="food", visual_dna=vd)
    assert "minimalist-row 1-line links" in up
    assert "VARIANT HINT FROM RESEARCH" in up
    print(f"  ✓ footer variant hint surfaced when DNA has it")


def test_subtle_intensity_note():
    vd = {"cultural_intensity": "subtle"}
    sp = _hf_system_prompt(
        component="MarketingHeader",
        brand_name="X", tagline="y", domain="saas", visual_dna=vd,
    )
    assert "accent positions only" in sp
    assert "Modern restraint" in sp
    print(f"  ✓ subtle intensity changes the note")


def test_bold_intensity_note():
    vd = {"cultural_intensity": "bold"}
    sp = _hf_system_prompt(
        component="MarketingHeader",
        brand_name="X", tagline="y", domain="food", visual_dna=vd,
    )
    assert "strong role" in sp
    assert "unmistakable" in sp.lower()
    print(f"  ✓ bold intensity changes the note")


def main():
    print("="*72)
    print("HEADER/FOOTER GENERATOR — UNIT TESTS")
    print("="*72)
    tests = [
        test_system_prompt_header,
        test_system_prompt_footer_pulls_anatomy,
        test_header_user_prompt_includes_cta_table,
        test_footer_user_prompt_variants,
        test_footer_variant_hint_from_dna,
        test_subtle_intensity_note,
        test_bold_intensity_note,
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
