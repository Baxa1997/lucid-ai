"""Regression tests for the section-structure fixers added 2026-06-12.

Source bug (Saint Cecilia hotel landing, first post-restart test run):
  • hero <section> root carried `overflow-hidden` while the booking bar's
    guests dropdown (`absolute top-full ...`) opened past the section's
    bottom edge — the panel was cut in half at the section boundary.
  • the h1 mixed a manual `<br className="hidden sm:block"/>` with natural
    wrapping at text-8xl, scattering the headline across 4 sparse lines
    with an orphan em-dash line.
"""
from app.services.post_generation_fixer import (
    fix_br_in_headings,
    fix_popover_overflow_clip,
    fix_section_overflow_clip,
)


def _write_section(tmp_path, name: str, content: str) -> str:
    sections = tmp_path / "src" / "components" / "sections"
    sections.mkdir(parents=True, exist_ok=True)
    path = sections / name
    path.write_text(content, encoding="utf-8")
    return str(path)


HERO_WITH_DROPDOWN = """
export default function HeroMainSection() {
  return (
    <section id="hero-main" className="relative min-h-[100svh] flex flex-col overflow-hidden bg-foreground">
      <div className="absolute top-full left-0 mt-2 w-56 bg-background z-[80]">panel</div>
    </section>
  );
}
"""


class TestFixPopoverOverflowClip:
    def test_downgrades_overflow_hidden_when_dropdown_present(self, tmp_path):
        path = _write_section(tmp_path, "HeroMainSection.jsx", HERO_WITH_DROPDOWN)
        fixed = fix_popover_overflow_clip(str(tmp_path))
        assert fixed == [path]
        out = open(path, encoding="utf-8").read()
        assert "overflow-hidden" not in out.split(">")[0] or "overflow-x-clip" in out
        assert 'className="relative min-h-[100svh] flex flex-col overflow-x-clip bg-foreground"' in out
        # The panel itself is untouched
        assert 'absolute top-full left-0 mt-2 w-56' in out

    def test_leaves_sections_without_dropdowns_alone(self, tmp_path):
        src = HERO_WITH_DROPDOWN.replace("top-full", "top-8")
        path = _write_section(tmp_path, "FaqSection.jsx", src)
        assert fix_popover_overflow_clip(str(tmp_path)) == []
        assert open(path, encoding="utf-8").read() == src

    def test_leaves_roots_without_overflow_hidden_alone(self, tmp_path):
        src = HERO_WITH_DROPDOWN.replace("overflow-hidden ", "")
        path = _write_section(tmp_path, "HeroMainSection.jsx", src)
        assert fix_popover_overflow_clip(str(tmp_path)) == []
        assert open(path, encoding="utf-8").read() == src

    def test_overflow_clip_adder_is_dropdown_aware(self, tmp_path):
        # fix_section_overflow_clip adds clipping for decorative bleed — but
        # must pick overflow-x-clip when the file holds a dropdown panel.
        src = """
export default function Hero() {
  return (
    <section className="relative isolate">
      <span className="absolute -bottom-12 left-4 text-9xl">&</span>
      <div className="absolute top-full mt-2 z-[80]">panel</div>
    </section>
  );
}
"""
        path = _write_section(tmp_path, "Hero.jsx", src)
        fixed = fix_section_overflow_clip(str(tmp_path))
        assert fixed == [path]
        out = open(path, encoding="utf-8").read()
        assert 'className="relative isolate overflow-x-clip"' in out
        assert "overflow-hidden" not in out


class TestFixBrInHeadings:
    def test_strips_br_from_h1(self, tmp_path):
        src = (
            '<h1 className="text-8xl">A storied retreat '
            '<span className="italic">&mdash;</span> '
            '<br className="hidden sm:block" />'
            'in the heart of South Congress.</h1>'
        )
        path = _write_section(tmp_path, "Hero.jsx", src)
        fixed = fix_br_in_headings(str(tmp_path))
        assert fixed == [path]
        out = open(path, encoding="utf-8").read()
        assert "<br" not in out
        assert '{" "}' in out
        assert "in the heart of South Congress.</h1>" in out

    def test_strips_bare_and_self_closing_br_from_h2(self, tmp_path):
        src = "<h2>Plan your stay<br/>with us<br>today</h2>"
        path = _write_section(tmp_path, "Cta.jsx", src)
        assert fix_br_in_headings(str(tmp_path)) == [path]
        out = open(path, encoding="utf-8").read()
        assert "<br" not in out
        assert out.count('{" "}') == 2

    def test_keeps_br_outside_headings(self, tmp_path):
        src = "<h1>Title</h1>\n<p>line one<br/>line two</p>"
        path = _write_section(tmp_path, "Story.jsx", src)
        assert fix_br_in_headings(str(tmp_path)) == []
        assert open(path, encoding="utf-8").read() == src

    def test_noop_without_sections_dir(self, tmp_path):
        assert fix_br_in_headings(str(tmp_path)) == []
        assert fix_popover_overflow_clip(str(tmp_path)) == []


# ── 2026-06-12 second wave: double footer, role leak, strip scale, rhythm ──
from app.services.post_generation_fixer import (
    fix_layout_cross_embed,
    fix_section_role_leak,
    fix_strip_section_scale,
)


def _write_layout(tmp_path, name: str, content: str) -> str:
    layout = tmp_path / "src" / "components" / "layout"
    layout.mkdir(parents=True, exist_ok=True)
    path = layout / name
    path.write_text(content, encoding="utf-8")
    return str(path)


HEADER_WITH_COLOCATED_FOOTER = """export default function MarketingHeader() {
  return (
    <>
      <header className="fixed top-0">nav</header>
      <MarketingFooter />
    </>
  );
}
function MarketingFooter() {
  return <footer className="bg-foreground">links</footer>;
}
"""


class TestFixLayoutCrossEmbed:
    def test_strips_footer_self_render_from_header(self, tmp_path):
        path = _write_layout(tmp_path, "MarketingHeader.jsx", HEADER_WITH_COLOCATED_FOOTER)
        assert fix_layout_cross_embed(str(tmp_path)) == [path]
        out = open(path, encoding="utf-8").read()
        assert "<MarketingFooter />" not in out
        # the local definition stays (unused dead code beats unbalanced JSX)
        assert "function MarketingFooter" in out

    def test_strips_inline_footer_markup_when_no_local_function(self, tmp_path):
        src = (
            "export default function MarketingHeader() {\n"
            "  return (<>\n"
            "    <header>nav</header>\n"
            '    <footer className="bg-foreground"><p>links</p></footer>\n'
            "  </>);\n"
            "}\n"
        )
        path = _write_layout(tmp_path, "MarketingHeader.jsx", src)
        assert fix_layout_cross_embed(str(tmp_path)) == [path]
        out = open(path, encoding="utf-8").read()
        assert "<footer" not in out and "links" not in out
        assert "<header>nav</header>" in out

    def test_clean_header_untouched(self, tmp_path):
        src = "export default function MarketingHeader() { return <header>nav</header>; }\n"
        path = _write_layout(tmp_path, "MarketingHeader.jsx", src)
        assert fix_layout_cross_embed(str(tmp_path)) == []
        assert open(path, encoding="utf-8").read() == src

    def test_footer_embedding_header_also_stripped(self, tmp_path):
        src = (
            "export default function MarketingFooter() {\n"
            "  return (<>\n"
            "    <header className=\"fixed\">dup nav</header>\n"
            "    <footer>links</footer>\n"
            "  </>);\n"
            "}\n"
        )
        path = _write_layout(tmp_path, "MarketingFooter.jsx", src)
        assert fix_layout_cross_embed(str(tmp_path)) == [path]
        out = open(path, encoding="utf-8").read()
        assert "<header" not in out
        assert "<footer>links</footer>" in out


class TestFixSectionRoleLeak:
    def test_section_role_swapped_for_nav_label(self, tmp_path):
        src = '<p>{indexStr} &mdash; {section.role || "Trust"}</p>'
        path = _write_section(tmp_path, "TrustTickerSection.jsx", src)
        assert fix_section_role_leak(str(tmp_path)) == [path]
        out = open(path, encoding="utf-8").read()
        assert "section.role" not in out
        assert '{section.nav_label || "Trust"}' in out

    def test_item_role_untouched(self, tmp_path):
        src = "<p>{t.role}</p>"
        path = _write_section(tmp_path, "ReviewsSection.jsx", src)
        assert fix_section_role_leak(str(tmp_path)) == []
        assert open(path, encoding="utf-8").read() == src


class TestFixStripSectionScale:
    def test_trust_ticker_padding_capped(self, tmp_path):
        src = '<section className="bg-muted/40 py-16 md:py-20 lg:py-24 relative">x</section>'
        path = _write_section(tmp_path, "TrustTickerSection.jsx", src)
        assert fix_strip_section_scale(str(tmp_path)) == [path]
        out = open(path, encoding="utf-8").read()
        assert 'className="bg-muted/40 py-10 md:py-12 lg:py-14 relative"' in out

    def test_non_strip_filenames_untouched(self, tmp_path):
        src = '<section className="py-20 md:py-24">cards</section>'
        path = _write_section(tmp_path, "RoomsSection.jsx", src)
        assert fix_strip_section_scale(str(tmp_path)) == []
        assert open(path, encoding="utf-8").read() == src


class TestEnforceRhythmSurface:
    def _enforce(self, *args, **kwargs):
        from app.services.landing_section_codegen import _enforce_rhythm_surface
        return _enforce_rhythm_surface(*args, **kwargs)

    def test_light_drift_repaired(self):
        src = '<section className="scroll-mt-24 bg-card py-20 relative">x</section>'
        out = self._enforce(
            src, {"surface": "tint", "surface_classes": "bg-muted/40 text-foreground"},
        )
        assert "bg-muted/40" in out and "bg-card" not in out

    def test_missing_bg_injected(self):
        src = '<section className="py-20 relative">x</section>'
        out = self._enforce(
            src, {"surface": "base", "surface_classes": "bg-background text-foreground"},
        )
        assert "bg-background" in out

    def test_dark_root_left_alone(self):
        src = '<section className="bg-foreground text-background py-20">x</section>'
        out = self._enforce(
            src, {"surface": "base", "surface_classes": "bg-background text-foreground"},
        )
        assert out == src

    def test_adherent_root_untouched(self):
        src = '<section className="bg-muted/40 py-20">x</section>'
        out = self._enforce(
            src, {"surface": "tint", "surface_classes": "bg-muted/40 text-foreground"},
        )
        assert out == src

    def test_media_and_empty_assignments_noop(self):
        src = '<section className="bg-card py-20">x</section>'
        assert self._enforce(src, None) == src
        assert self._enforce(src, {"surface": "media", "surface_classes": "x"}) == src


# ── 2026-06-13: icon-name-as-text leak ──
from app.services.post_generation_fixer import fix_icon_name_as_text


class TestFixIconNameAsText:
    def test_removes_icon_name_text_span(self, tmp_path):
        src = (
            '<div className="chip">'
            '<IconComp className="h-4 w-4" />'
            '<span className="uppercase">{item.icon}</span>'
            '</div>'
        )
        path = _write_section(tmp_path, "SleepScienceSection.jsx", src)
        assert fix_icon_name_as_text(str(tmp_path)) == [path]
        out = open(path, encoding="utf-8").read()
        assert "{item.icon}" not in out
        assert "<IconComp" in out  # the real icon stays

    def test_no_icon_text_is_noop(self, tmp_path):
        src = '<div><IconComp className="h-4" /><span>{item.label}</span></div>'
        path = _write_section(tmp_path, "Faq.jsx", src)
        assert fix_icon_name_as_text(str(tmp_path)) == []
        assert open(path, encoding="utf-8").read() == src
