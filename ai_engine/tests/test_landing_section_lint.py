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


class TestLayoutCrossEmbed:
    """Header co-locating a footer rendered a double footer (one ABOVE the
    hero) — Luminary Austin 2026-06-12."""

    HEADER_WITH_FOOTER = (
        "export default function MarketingHeader() {\n"
        "  return (<><header className=\"fixed\">nav</header><MarketingFooter /></>);\n"
        "}\n"
        "function MarketingFooter() { return <footer>links</footer>; }\n"
    )

    def test_header_embedding_footer_is_regen(self):
        v = lint_section_source(self.HEADER_WITH_FOOTER, filename="MarketingHeader")
        hits = _by_code(v, "header_embeds_footer")
        assert hits and hits[0]["severity"] == "regen"

    def test_clean_header_passes(self):
        src = "export default function MarketingHeader() { return <header>nav</header>; }"
        assert not _by_code(
            lint_section_source(src, filename="MarketingHeader"), "header_embeds_footer",
        )

    def test_footer_embedding_header_is_regen(self):
        src = "export default function MarketingFooter() { return (<><header>nav</header><footer>x</footer></>); }"
        v = lint_section_source(src, filename="MarketingFooter")
        assert _by_code(v, "footer_embeds_header")

    def test_clean_footer_passes(self):
        src = "export default function MarketingFooter() { return <footer className=\"bg-foreground\">x</footer>; }"
        assert not _by_code(
            lint_section_source(src, filename="MarketingFooter"), "footer_embeds_header",
        )

    def test_regular_sections_never_gated(self):
        # a section may legitimately contain <header> (card header) markup
        src = "<section className=\"py-20\"><header>card head</header><footer>card foot</footer></section>"
        codes = _codes(lint_section_source(src, filename="StorySection"))
        assert "header_embeds_footer" not in codes
        assert "footer_embeds_header" not in codes


class TestSectionRoleLeak:
    def test_rendered_section_role_flagged_fixable(self):
        src = '<p className="eyebrow">{indexStr} &mdash; {section.role || "Trust"}</p>'
        hits = _by_code(lint_section_source(src), "section_role_leak")
        assert hits and hits[0]["severity"] == "fixable"

    def test_item_role_is_fine(self):
        # a testimonial author's job title is real content, not rationale
        src = "<p>{t.role}</p><span>{member.role}</span>"
        assert not _by_code(lint_section_source(src), "section_role_leak")


class TestStripSectionScale:
    def test_oversized_trust_bar_flagged(self):
        src = '<section className="bg-muted/40 py-16 md:py-20 lg:py-24">stats</section>'
        v = lint_section_source(src, section={"type": "trust_bar"})
        hits = _by_code(v, "strip_section_oversized")
        assert hits and hits[0]["severity"] == "fixable"

    def test_compact_strip_passes(self):
        src = '<section className="bg-muted/40 py-8 md:py-12">stats</section>'
        assert not _by_code(
            lint_section_source(src, section={"type": "trust_bar"}),
            "strip_section_oversized",
        )

    def test_non_strip_types_keep_their_padding(self):
        src = '<section className="py-20 md:py-24">cards</section>'
        assert not _by_code(
            lint_section_source(src, section={"type": "features"}),
            "strip_section_oversized",
        )


class TestNativeDateInput:
    def test_native_date_input_is_regen(self):
        src = '<input type="date" value={checkIn} className="opacity-0" />'
        hits = _by_code(lint_section_source(src), "native_date_input")
        assert hits and hits[0]["severity"] == "regen"

    def test_custom_calendar_passes(self):
        src = (
            '<button type="button" className="h-9 w-9 rounded-full">{d.date()}</button>'
            '<input type="number" min="1" />'
        )
        assert not _by_code(lint_section_source(src), "native_date_input")


class TestBentoAspectMix:
    def test_mixed_aspects_without_rowspan_is_regen(self):
        src = (
            '<div className="grid grid-cols-12 gap-4">'
            '<div className="aspect-[4/3]" /><div className="aspect-[3/4]" />'
            '<div className="aspect-square" /></div>'
        )
        hits = _by_code(lint_section_source(src), "bento_aspect_mix")
        assert hits and hits[0]["severity"] == "regen"

    def test_rowspan_in_comment_does_not_satisfy(self):
        # the shipped suites bento PLANNED row-span-2 in a comment but never
        # applied the class — comments must not pass the structural check
        src = (
            "// Tile 0: col-span-7 row-span-2 (large hero tile)\n"
            '<div className="grid grid-cols-12 gap-4">'
            '<div className="aspect-[4/3]" /><div className="aspect-[3/4]" /></div>'
        )
        assert _by_code(lint_section_source(src), "bento_aspect_mix")

    def test_real_rowspan_passes(self):
        src = (
            '<div className="grid grid-cols-12 auto-rows-[200px] gap-4">'
            '<div className="md:row-span-2 aspect-[4/3]" /><div className="aspect-square" /></div>'
        )
        assert not _by_code(lint_section_source(src), "bento_aspect_mix")

    def test_uniform_aspect_passes(self):
        src = (
            '<div className="grid grid-cols-12 gap-4">'
            '<div className="aspect-[4/3]" /><div className="aspect-[4/3]" /></div>'
        )
        assert not _by_code(lint_section_source(src), "bento_aspect_mix")


class TestHeaderOverfrosted:
    # wordmark + nav + social each in their own translucent frosted chip
    FROSTED = (
        "export default function MarketingHeader() {\n"
        '  return (<header className="bg-background/60 backdrop-blur-md">'
        '    <div className="rounded-full bg-background/70 backdrop-blur-md px-4">Logo</div>'
        '    <nav className="rounded-full bg-background/70 backdrop-blur-md px-2">links</nav>'
        '    <div className="rounded-full bg-background/70 backdrop-blur-md p-1">social</div>'
        "  </header>);\n}\n"
    )

    def test_stacked_frosted_chips_flagged_regen(self):
        hits = _by_code(
            lint_section_source(self.FROSTED, filename="MarketingHeader"),
            "header_overfrosted",
        )
        assert hits and hits[0]["severity"] == "regen"

    def test_clean_transparent_header_passes(self):
        clean = (
            "export default function MarketingHeader() {\n"
            '  const cls = scrolled ? "bg-background/95 backdrop-blur-md border-b" : "bg-transparent";\n'
            '  return <header className={cls}><nav className="flex gap-1 text-white">links</nav></header>;\n'
            "}\n"
        )
        assert not _by_code(
            lint_section_source(clean, filename="MarketingHeader"), "header_overfrosted",
        )

    def test_solid_header_passes(self):
        src = (
            'export default function MarketingHeader() {\n'
            '  return <header className="bg-background/80 backdrop-blur-md border-b">'
            '<nav className="text-foreground/80">links</nav></header>;\n}\n'
        )
        assert not _by_code(
            lint_section_source(src, filename="MarketingHeader"), "header_overfrosted",
        )

    def test_sections_not_gated(self):
        # a section may use frosted chips legitimately — only the header file is gated
        src = (
            '<section className="bg-background/60 backdrop-blur-md">'
            '<div className="bg-background/70 backdrop-blur-md">a</div>'
            '<div className="bg-background/70 backdrop-blur-md">b</div>'
            '<div className="bg-background/70 backdrop-blur-md">c</div></section>'
        )
        assert not _by_code(
            lint_section_source(src, filename="HeroSection"), "header_overfrosted",
        )


class TestIconNameAsText:
    def test_icon_name_as_text_child_is_fixable(self):
        src = '<span className="uppercase">{item.icon}</span>'
        hits = _by_code(lint_section_source(src), "icon_name_as_text")
        assert hits and hits[0]["severity"] == "fixable"

    def test_component_render_not_flagged(self):
        # <item.icon /> renders the COMPONENT — legitimate
        assert not _by_code(lint_section_source('<item.icon className="h-4 w-4" />'), "icon_name_as_text")

    def test_prop_and_data_usage_not_flagged(self):
        assert not _by_code(lint_section_source('<Icon icon={item.icon} />'), "icon_name_as_text")
        assert not _by_code(lint_section_source('const x = { icon: item.icon };'), "icon_name_as_text")
