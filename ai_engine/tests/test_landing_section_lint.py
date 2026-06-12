"""Tests for landing_section_lint + the contrast/size fixers built on it.

The lint feeds the guided-retry loop in _generate_one_section: "regen"
violations re-prompt the model with the violation list; "fixable" ones are
repaired by post_generation_fixer without burning an API call.
"""
from app.services.landing_section_lint import (
    lint_section_source,
    regen_feedback,
)
from app.services.post_generation_fixer import (
    fix_nonhero_viewport_heights,
    fix_same_element_contrast,
)


def _codes(violations):
    return {v["code"] for v in violations}


def _by_code(violations, code):
    return [v for v in violations if v["code"] == code]


class TestRegenClass:
    def test_hardcoded_headline_detected(self):
        section = {"headline": "A storied retreat in the heart of South Congress"}
        src = "<h1>A storied retreat in the heart of South Congress</h1>"
        v = lint_section_source(src, section=section)
        assert _by_code(v, "hardcoded_headline")
        assert _by_code(v, "hardcoded_headline")[0]["severity"] == "regen"

    def test_interpolated_headline_passes(self):
        section = {"headline": "A storied retreat in the heart of South Congress"}
        src = "<h1>{section.headline}</h1>"
        assert not _by_code(lint_section_source(src, section=section), "hardcoded_headline")

    def test_short_headline_not_flagged(self):
        # short strings collide with nav labels / common words too easily
        section = {"headline": "Our Story"}
        src = "<a>Our Story</a><h1>{section.headline}</h1>"
        assert not _by_code(lint_section_source(src, section=section), "hardcoded_headline")

    def test_raw_hex_and_inline_style_and_css_var(self):
        src = (
            '<div className="bg-[#1a1a2e] text-foreground" '
            "style={{ color: '#fff' }}>x</div>"
            "<p className=\"x\" style={{ background: 'var(--color-primary)' }}>y</p>"
        )
        codes = _codes(lint_section_source(src))
        assert {"raw_hex_color", "inline_style_color", "css_var_color"} <= codes

    def test_clean_source_no_regen(self):
        src = '<section className="py-16 bg-background"><h2>{section.headline}</h2></section>'
        assert all(v["severity"] != "regen" for v in lint_section_source(src))


class TestFixableClass:
    def test_br_in_heading(self):
        src = "<h1>line one<br/>line two</h1>"
        assert "br_in_heading" in _codes(lint_section_source(src))

    def test_br_in_paragraph_ok(self):
        src = "<h1>title</h1><p>a<br/>b</p>"
        assert "br_in_heading" not in _codes(lint_section_source(src))

    def test_popover_overflow_clip(self):
        src = (
            '<section className="relative overflow-hidden">'
            '<div className="absolute top-full mt-2">panel</div></section>'
        )
        assert "popover_overflow_clip" in _codes(lint_section_source(src))

    def test_same_element_contrast_pairs(self):
        src = (
            '<footer className="bg-foreground text-foreground">x</footer>'
            '<button className="bg-primary text-primary">y</button>'
            '<div className="bg-muted text-muted">z</div>'
        )
        v = _by_code(lint_section_source(src), "same_element_contrast")
        assert len(v) == 3

    def test_tinted_surface_not_flagged(self):
        src = '<div className="bg-foreground/10 text-foreground">fine</div>'
        assert "same_element_contrast" not in _codes(lint_section_source(src))

    def test_primary_foreground_not_flagged(self):
        src = '<button className="bg-primary text-primary-foreground">ok</button>'
        assert "same_element_contrast" not in _codes(lint_section_source(src))

    def test_nonhero_viewport_height(self):
        src = '<section className="min-h-screen py-16">x</section>'
        v = lint_section_source(src, section={"type": "testimonials"}, filename="GuestVoicesSection.jsx")
        assert "nonhero_viewport_height" in _codes(v)

    def test_hero_viewport_height_allowed(self):
        src = '<section className="min-h-[100svh]">x</section>'
        v = lint_section_source(src, section={"type": "hero", "id": "hero-main"}, filename="HeroMainSection.jsx")
        assert "nonhero_viewport_height" not in _codes(v)

    def test_oversized_padding(self):
        src = '<section className="py-32 bg-background">x</section>'
        assert "oversized_padding" in _codes(lint_section_source(src))
        src_ok = '<section className="py-16 md:py-24">x</section>'
        assert "oversized_padding" not in _codes(lint_section_source(src_ok))


class TestRegenFeedback:
    def test_feedback_lists_all_violations(self):
        v = lint_section_source(
            '<div className="bg-[#fff]" style={{ color: "#000" }}>x</div>'
        )
        fb = regen_feedback(v)
        assert "PREVIOUS ATTEMPT REJECTED" in fb
        assert "raw_hex_color" in fb and "inline_style_color" in fb

    def test_empty_violations_empty_feedback(self):
        assert regen_feedback([]) == ""


def _write(tmp_path, rel, content):
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return str(path)


class TestContrastFixer:
    def test_repairs_dark_footer_pairs_in_layout_dir(self, tmp_path):
        path = _write(
            tmp_path, "src/components/layout/MarketingFooter.jsx",
            '<footer className="bg-foreground text-foreground">'
            '<p className="text-muted-foreground">desc</p>'
            '<a className="bg-primary text-primary/80 px-4">cta</a></footer>',
        )
        assert fix_same_element_contrast(str(tmp_path)) == [path]
        out = open(path, encoding="utf-8").read()
        assert 'className="bg-foreground text-background"' in out
        # standalone muted paragraph (different element) untouched
        assert '<p className="text-muted-foreground">' in out
        # opacity suffix dropped only because replacement has none; primary pair swaps
        assert "text-primary-foreground" in out and "text-primary/80" not in out

    def test_leaves_correct_pairs_alone(self, tmp_path):
        src = '<div className="bg-foreground text-background">ok</div>'
        path = _write(tmp_path, "src/components/sections/Cta.jsx", src)
        assert fix_same_element_contrast(str(tmp_path)) == []
        assert open(path, encoding="utf-8").read() == src


class TestViewportHeightFixer:
    def test_strips_min_h_screen_from_non_hero_root(self, tmp_path):
        path = _write(
            tmp_path, "src/components/sections/GuestVoicesSection.jsx",
            '<section id="reviews" className="relative min-h-screen py-32 bg-muted/40">x</section>',
        )
        assert fix_nonhero_viewport_heights(str(tmp_path)) == [path]
        out = open(path, encoding="utf-8").read()
        assert "min-h-screen" not in out
        assert "py-24" in out and "py-32" not in out
        assert 'className="relative py-24 bg-muted/40"' in out

    def test_hero_keeps_viewport_height_but_caps_padding(self, tmp_path):
        path = _write(
            tmp_path, "src/components/sections/HeroMainSection.jsx",
            '<section className="relative min-h-[100svh] py-40">x</section>',
        )
        assert fix_nonhero_viewport_heights(str(tmp_path)) == [path]
        out = open(path, encoding="utf-8").read()
        assert "min-h-[100svh]" in out
        assert "py-24" in out and "py-40" not in out

    def test_inner_divs_untouched(self, tmp_path):
        src = (
            '<section className="py-16">'
            '<div className="min-h-screen">carousel viewport trick</div></section>'
        )
        path = _write(tmp_path, "src/components/sections/Gallery.jsx", src)
        assert fix_nonhero_viewport_heights(str(tmp_path)) == []
        assert open(path, encoding="utf-8").read() == src
