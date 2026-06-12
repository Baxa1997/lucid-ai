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
