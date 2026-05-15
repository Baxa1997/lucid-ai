"""Tests for audit_content (Stage 7.5).

Six cases per spec:
  1. File with "Lorem ipsum" → flagged as placeholder
  2. Recruitment without application form → flagged as missing required
  3. Broken /contact link → flagged
  4. Missing alt → warning (counts in issues.missing_alt_text)
  5. Duplicate paragraph → warning (counts in issues.duplicate_content)
  6. Clean website → score 95+

Run inside the ai_engine container:
    docker exec -it lucid-ai-ai_engine-1 python /app/tests/test_content_audit.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

sys.path.insert(0, "/app")

from app.services.website_verification import audit_content


def _make_workspace(files: dict[str, str]) -> str:
    ws = tempfile.mkdtemp(prefix="lucid_content_audit_")
    for rel, content in files.items():
        abs_p = os.path.join(ws, rel)
        os.makedirs(os.path.dirname(abs_p), exist_ok=True)
        with open(abs_p, "w") as f:
            f.write(content)
    return ws


def _good_hero(brand: str = "Brand") -> str:
    return f"""export default function HomeHero() {{
  return (
    <section className="py-24">
      <h1 className="text-5xl">Welcome to {brand}</h1>
      <p className="text-lg">Real working description that actually reads like a sentence and is long enough to feel finished.</p>
    </section>
  );
}}
"""


def _good_features() -> str:
    return """export default function HomeFeatures() {
  return (
    <section className="py-24">
      <h2>Three things we do well</h2>
      <p>Specific concrete benefit that distinguishes our offering from anyone else doing similar work in the same space.</p>
      <p>Second distinct claim with different vocabulary so the audit duplicate-detector has nothing to complain about either.</p>
    </section>
  );
}
"""


def _good_contact_page(slug: str = "contact") -> str:
    return f"""export default function ContactPage() {{
  return (
    <section>
      <a href="/about">About us</a>
    </section>
  );
}}
"""


# ── 1. Placeholder detection ─────────────────────────────────────────

def test_lorem_ipsum_flagged():
    ws = _make_workspace({
        "src/app/page.js": (
            'import HomeHero from "@/components/pages/home/HomeHero";\n'
            "export default function P() { return <HomeHero/>; }\n"
        ),
        "src/components/pages/home/HomeHero.jsx": (
            "export default function HomeHero() {\n"
            "  return <section><p>Lorem ipsum dolor sit amet, consectetur adipiscing elit.</p></section>;\n"
            "}\n"
        ),
    })
    try:
        r = audit_content(ws, {"primary_purpose": "brand_awareness"})
        hits = r["issues"]["placeholders"]
        assert any("HomeHero" in h for h in hits), f"expected HomeHero in placeholders, got {hits}"
        assert r["score"] <= 80, f"score should be penalized, got {r['score']}"
        print(f"  ✓ Lorem ipsum flagged — score={r['score']}, hits={hits}")
    finally:
        shutil.rmtree(ws, ignore_errors=True)


def test_todo_and_fake_address_flagged():
    """TODO/FIXME + fake address are both penalty categories."""
    ws = _make_workspace({
        "src/app/page.js": 'export default function P() { return <p>// TODO: fill this in</p>; }\n',
        "src/components/pages/home/HomeContact.jsx": (
            "export default function HomeContact() {\n"
            "  return <section><address>123 Main Street, Anytown, ZIP 12345</address>"
            "<p>example@example.com 555-0100</p></section>;\n"
            "}\n"
        ),
    })
    try:
        r = audit_content(ws, {"primary_purpose": "brand_awareness"})
        placeholders = r["issues"]["placeholders"]
        forbidden = r["issues"]["forbidden_content"]
        assert any("TODO" in p for p in placeholders), f"expected TODO in placeholders, got {placeholders}"
        # Should pick up at least 4 forbidden items (street/town/zip/email — phone may merge)
        assert len(forbidden) >= 4, f"expected >=4 forbidden_content, got {len(forbidden)}: {forbidden}"
        print(f"  ✓ TODO + fake address flagged — score={r['score']}, forbidden={len(forbidden)}")
    finally:
        shutil.rmtree(ws, ignore_errors=True)


# ── 2. Recruitment without form ─────────────────────────────────────

def test_recruitment_missing_form_flagged():
    """Recruitment site with only a hero + no form → missing required."""
    ws = _make_workspace({
        "src/app/page.js": (
            'import HomeHero from "@/components/pages/home/HomeHero";\n'
            "export default function P() { return <HomeHero/>; }\n"
        ),
        "src/components/pages/home/HomeHero.jsx": _good_hero("LogiCo"),
    })
    try:
        r = audit_content(ws, {"primary_purpose": "recruitment"})
        missing = r["issues"]["missing_required_sections"]
        # Should flag missing 'openings', 'apply' AND missing form
        assert any("openrole" in m or "openings" in m or "roles" in m or "careers" in m or "jobopening" in m or "positions" in m for m in missing), missing
        assert any("apply" in m or "application" in m for m in missing), missing
        assert any("form" in m.lower() for m in missing), f"expected form-requirement issue, got {missing}"
        print(f"  ✓ recruitment without form flagged — {len(missing)} required-section issues")
    finally:
        shutil.rmtree(ws, ignore_errors=True)


def test_recruitment_with_complete_form_passes():
    """Recruitment with a proper form satisfies the form check."""
    ws = _make_workspace({
        "src/app/page.js": (
            'import HomeHero from "@/components/pages/home/HomeHero";\n'
            'import HomeOpenRoles from "@/components/pages/home/HomeOpenRoles";\n'
            'import HomeApplicationForm from "@/components/pages/home/HomeApplicationForm";\n'
            "export default function P() { return (<><HomeHero/><HomeOpenRoles/><HomeApplicationForm/></>); }\n"
        ),
        "src/components/pages/home/HomeHero.jsx": _good_hero("LogiCo"),
        "src/components/pages/home/HomeOpenRoles.jsx": (
            "export default function HomeOpenRoles() {\n"
            "  return <section><h2>Open positions for CDL drivers</h2>"
            "<article><h3>CDL-A Driver</h3><p>Long-haul routes across the midwest with strong pay and benefits.</p></article></section>;\n"
            "}\n"
        ),
        "src/components/pages/home/HomeApplicationForm.jsx": (
            'export default function HomeApplicationForm() {\n'
            '  return (\n'
            '    <form>\n'
            '      <input name="name" type="text" />\n'
            '      <input name="email" type="email" />\n'
            '      <input name="phone" type="tel" />\n'
            '      <button>Apply</button>\n'
            '    </form>\n'
            '  );\n'
            "}\n"
        ),
    })
    try:
        r = audit_content(ws, {"primary_purpose": "recruitment"})
        assert not r["issues"]["missing_required_sections"], (
            f"shouldn't flag missing required, got: {r['issues']['missing_required_sections']}"
        )
        print(f"  ✓ recruitment with complete form passes — score={r['score']}")
    finally:
        shutil.rmtree(ws, ignore_errors=True)


# ── 3. Broken internal link ────────────────────────────────────────

def test_broken_internal_link_flagged():
    """href='/contact' must resolve to src/app/contact/page.js."""
    ws = _make_workspace({
        "src/app/page.js": (
            'import HomeHero from "@/components/pages/home/HomeHero";\n'
            "export default function P() { return <HomeHero/>; }\n"
        ),
        "src/components/pages/home/HomeHero.jsx": (
            "export default function HomeHero() {\n"
            '  return <section><a href="/contact">Get in touch</a><a href="/about">About</a></section>;\n'
            "}\n"
        ),
        # /about exists, /contact does NOT
        "src/app/about/page.js": "export default function P() { return <div/>; }\n",
    })
    try:
        r = audit_content(ws, {"primary_purpose": "brand_awareness"})
        broken = r["issues"]["broken_internal_links"]
        assert any("/contact" in b for b in broken), f"expected /contact flagged, got {broken}"
        assert not any("/about" in b for b in broken), f"/about should resolve, got {broken}"
        print(f"  ✓ broken /contact flagged, /about resolves — score={r['score']}")
    finally:
        shutil.rmtree(ws, ignore_errors=True)


# ── 4. Missing alt (warning) ────────────────────────────────────────

def test_missing_alt_flagged_as_warning():
    ws = _make_workspace({
        "src/app/page.js": "export default function P() { return <div/>; }\n",
        "src/components/pages/home/HomeHero.jsx": (
            "export default function HomeHero() {\n"
            '  return <section>\n'
            '    <img src="https://images.unsplash.com/p1" />\n'      # no alt
            '    <img src="https://images.unsplash.com/p2" alt="" />\n'  # empty alt
            '    <img src="https://images.unsplash.com/p3" alt="hero.jpg" />\n'  # filename as alt
            '    <img src="https://images.unsplash.com/p4" alt="Smiling barista at espresso machine" />\n'  # good
            '  </section>;\n'
            "}\n"
        ),
    })
    try:
        r = audit_content(ws, {"primary_purpose": "brand_awareness"})
        bad_alt = r["issues"]["missing_alt_text"]
        assert len(bad_alt) == 3, f"expected 3 alt issues, got {len(bad_alt)}: {bad_alt}"
        # warnings list mentions alt
        assert any("alt" in w for w in r["warnings"])
        print(f"  ✓ {len(bad_alt)} alt issues flagged as warnings — score={r['score']}")
    finally:
        shutil.rmtree(ws, ignore_errors=True)


# ── 5. Duplicate paragraph (warning) ────────────────────────────────

def test_duplicate_paragraph_flagged():
    paragraph = (
        "Caffe Verona has poured espresso in Florence since 1923 — third-generation roasters with single-origin beans."
    )
    ws = _make_workspace({
        "src/app/page.js": "export default function P() { return <div/>; }\n",
        "src/components/pages/home/HomeHero.jsx": (
            f"export default function HomeHero() {{ return <section><p>{paragraph}</p></section>; }}\n"
        ),
        "src/components/pages/home/HomeAbout.jsx": (
            f"export default function HomeAbout() {{ return <section><p>{paragraph}</p></section>; }}\n"
        ),
    })
    try:
        r = audit_content(ws, {"primary_purpose": "brand_awareness"})
        dup = r["issues"]["duplicate_content"]
        assert len(dup) >= 1, f"expected duplicate flagged, got {dup}"
        assert any("HomeHero" in d and "HomeAbout" in d for d in dup), dup
        print(f"  ✓ duplicate paragraph flagged — score={r['score']}, dup={dup[0][:80]}")
    finally:
        shutil.rmtree(ws, ignore_errors=True)


# ── 6. Clean website → score 95+ ────────────────────────────────────

def test_clean_website_high_score():
    """A small but complete site with no placeholders, fakes, or breakage → ≥95."""
    ws = _make_workspace({
        # Foundation
        "src/app/page.js": (
            'import HomeHero from "@/components/pages/home/HomeHero";\n'
            'import HomeFeatures from "@/components/pages/home/HomeFeatures";\n'
            "export default function P() { return (<><HomeHero/><HomeFeatures/></>); }\n"
        ),
        "src/app/about/page.js": "export default function P() { return <div/>; }\n",
        "src/components/pages/home/HomeHero.jsx": (
            "export default function HomeHero() {\n"
            '  return (\n'
            '    <section>\n'
            '      <h1>Caffe Verona — Florence&apos;s coffee since 1923</h1>\n'
            '      <p>Third-generation Italian roasters serving single-origin espresso in the heart of Florence.</p>\n'
            '      <img src="https://images.unsplash.com/photo-1" alt="Espresso pour at Caffe Verona counter" />\n'
            '      <a href="/about">Read our story</a>\n'
            '    </section>\n'
            '  );\n'
            "}\n"
        ),
        "src/components/pages/home/HomeFeatures.jsx": _good_features(),
    })
    try:
        r = audit_content(ws, {"primary_purpose": "brand_awareness"})
        assert r["score"] >= 95, f"expected score >= 95, got {r['score']} — issues: {r['issues']}"
        assert r["ok"] is True
        print(f"  ✓ clean website scored {r['score']}/100")
    finally:
        shutil.rmtree(ws, ignore_errors=True)


# ── Voice violation case (extra coverage) ──────────────────────────

def test_voice_violation_flagged():
    ws = _make_workspace({
        "src/app/page.js": "export default function P() { return <div/>; }\n",
        "src/components/pages/home/HomeHero.jsx": (
            "export default function HomeHero() {\n"
            "  return <section><p>Our amazing synergy unlocks world-class outcomes for clients.</p></section>;\n"
            "}\n"
        ),
    })
    try:
        r = audit_content(
            ws,
            {"primary_purpose": "brand_awareness"},
            {"forbidden_phrases": ["amazing", "synergy", "world-class"]},
        )
        v = r["issues"]["voice_violations"]
        assert len(v) >= 3, f"expected 3 voice violations, got {v}"
        # Voice is warning, not critical
        assert any("voice" in w for w in r["warnings"])
        print(f"  ✓ {len(v)} voice violations flagged — score={r['score']}")
    finally:
        shutil.rmtree(ws, ignore_errors=True)


def main():
    tests = [
        ("placeholder: Lorem ipsum flagged",            test_lorem_ipsum_flagged),
        ("placeholder: TODO + fake address flagged",    test_todo_and_fake_address_flagged),
        ("recruitment: missing form flagged",           test_recruitment_missing_form_flagged),
        ("recruitment: complete form passes",           test_recruitment_with_complete_form_passes),
        ("link: broken /contact flagged",               test_broken_internal_link_flagged),
        ("alt: missing/weak flagged",                   test_missing_alt_flagged_as_warning),
        ("dup: duplicate paragraph flagged",            test_duplicate_paragraph_flagged),
        ("clean: high score on good site",              test_clean_website_high_score),
        ("voice: forbidden phrases flagged",            test_voice_violation_flagged),
    ]
    print("=" * 72)
    print("CONTENT AUDIT (STAGE 7.5) — TESTS")
    print("=" * 72)
    passed = 0
    for name, fn in tests:
        print(f"\n── {name} ──")
        try:
            fn()
            passed += 1
        except AssertionError as e:
            print(f"  ✗ FAILED: {e}")
        except Exception as e:
            import traceback
            print(f"  ✗ THREW: {e}")
            traceback.print_exc()
    print(f"\n{'=' * 72}\nSCORE: {passed}/{len(tests)}\n{'=' * 72}")
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    sys.exit(main())
