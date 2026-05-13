"""Tests for website_verification — runs on synthetic workspaces in /tmp.

Validates the audit catches:
  - Missing page composition files
  - Missing section component files
  - Broken imports (@/ and relative)
  - CSS var violations (Tailwind only contract)
  - Missing default exports

Run: docker exec lucid-ai-ai_engine-1 python /app/test_website_verification.py
"""
import os
import shutil
import sys
import tempfile
sys.path.insert(0, "/app")
from app.services.website_verification import audit_generated_website


def _make_workspace(files: dict[str, str]) -> str:
    """Create a temp workspace with the given files. Returns workspace path."""
    ws = tempfile.mkdtemp(prefix="lucid_audit_test_")
    for rel, content in files.items():
        abs_p = os.path.join(ws, rel)
        os.makedirs(os.path.dirname(abs_p), exist_ok=True)
        with open(abs_p, "w") as f:
            f.write(content)
    return ws


def _cleanup(ws: str):
    shutil.rmtree(ws, ignore_errors=True)


def _good_section(name: str) -> str:
    return f"""export default function {name}() {{
  return <section className="py-24"><h1 className="font-heading">Hello</h1></section>;
}}
"""


def _good_page(slug: str, comps: list[str]) -> str:
    imports = "\n".join(f'import {c} from "@/components/pages/{slug}/{c}";' for c in comps)
    body = "\n".join(f"      <{c} />" for c in comps)
    return f"""{imports}

export default function Page() {{
  return <main>
{body}
  </main>;
}}
"""


def test_happy_audit():
    plan = {
        "pages": [
            {"route": "/", "sections": [{"type": "hero"}, {"type": "value_prop"}]},
            {"route": "/about", "sections": [{"type": "hero"}, {"type": "story"}]},
        ],
    }
    files = {
        "src/app/page.js": _good_page("home", ["HomeHero", "HomeValueProp"]),
        "src/components/pages/home/HomeHero.jsx": _good_section("HomeHero"),
        "src/components/pages/home/HomeValueProp.jsx": _good_section("HomeValueProp"),
        "src/app/about/page.js": _good_page("about", ["AboutHero", "AboutStory"]),
        "src/components/pages/about/AboutHero.jsx": _good_section("AboutHero"),
        "src/components/pages/about/AboutStory.jsx": _good_section("AboutStory"),
        "src/components/layout/MarketingHeader.jsx": _good_section("MarketingHeader"),
        "src/components/layout/MarketingFooter.jsx": _good_section("MarketingFooter"),
        "src/config/site.js": "export const siteConfig = {};\n",
        "src/config/navigation.js": "export const mainNav = [];\nexport const footerNav = [];\n",
        "src/lib/design-system.js": "export const ds = {};\n",
    }
    ws = _make_workspace(files)
    try:
        r = audit_generated_website(ws, plan)
        assert r["ok"] is True, f"happy path should pass; got: {r['issues']}"
        assert r["files_audited"] >= 10, f"only audited {r['files_audited']}"
        print(f"  ✓ happy_audit: {r['summary']}")
    finally:
        _cleanup(ws)


def test_missing_page_file():
    plan = {"pages": [{"route": "/menu", "sections": [{"type": "hero"}]}]}
    ws = _make_workspace({})  # nothing
    try:
        r = audit_generated_website(ws, plan, expect_header=False, expect_footer=False)
        assert r["ok"] is False
        assert "src/app/menu/page.js" in r["issues"]["missing_page_files"]
        print(f"  ✓ missing_page_file: caught")
    finally:
        _cleanup(ws)


def test_missing_section_file():
    plan = {"pages": [{"route": "/", "sections": [{"type": "hero"}]}]}
    files = {
        "src/app/page.js": _good_page("home", ["HomeHero"]),
        # HomeHero.jsx is MISSING
    }
    ws = _make_workspace(files)
    try:
        r = audit_generated_website(ws, plan, expect_header=False, expect_footer=False)
        assert r["ok"] is False
        assert any("HomeHero" in s for s in r["issues"]["missing_section_files"])
        print(f"  ✓ missing_section_file: caught HomeHero.jsx")
    finally:
        _cleanup(ws)


def test_broken_import():
    plan = {"pages": [{"route": "/", "sections": [{"type": "hero"}]}]}
    files = {
        "src/app/page.js": 'import HomeHero from "@/components/pages/home/HomeHero";\nimport Lost from "@/this/does/not/exist";\nexport default function P(){return <HomeHero/>}',
        "src/components/pages/home/HomeHero.jsx": _good_section("HomeHero"),
    }
    ws = _make_workspace(files)
    try:
        r = audit_generated_website(ws, plan, expect_header=False, expect_footer=False)
        assert r["ok"] is False
        broken = r["issues"]["broken_imports"]
        assert any(b["import"] == "@/this/does/not/exist" for b in broken), broken
        # The good HomeHero import should resolve fine
        assert not any(b["import"] == "@/components/pages/home/HomeHero" for b in broken), broken
        print(f"  ✓ broken_import: caught {len(broken)} broken import(s)")
    finally:
        _cleanup(ws)


def test_css_var_violation():
    plan = {"pages": [{"route": "/", "sections": [{"type": "hero"}]}]}
    files = {
        "src/app/page.js": _good_page("home", ["HomeHero"]),
        "src/components/pages/home/HomeHero.jsx": (
            'export default function HomeHero() {\n'
            '  return <div style={{ color: "var(--color-primary)" }}>Bad</div>;\n'
            '}\n'
        ),
    }
    ws = _make_workspace(files)
    try:
        r = audit_generated_website(ws, plan, expect_header=False, expect_footer=False)
        assert len(r["issues"]["css_var_violations"]) >= 1, r["issues"]
        violation = r["issues"]["css_var_violations"][0]
        assert "var(--color-primary)" in violation
        print(f"  ✓ css_var_violation: caught — {violation}")
    finally:
        _cleanup(ws)


def test_missing_default_export():
    plan = {"pages": [{"route": "/", "sections": [{"type": "hero"}]}]}
    files = {
        "src/app/page.js": _good_page("home", ["HomeHero"]),
        # Section has NO default export
        "src/components/pages/home/HomeHero.jsx": (
            'export function HomeHero() { return <div />; }\n'
        ),
    }
    ws = _make_workspace(files)
    try:
        r = audit_generated_website(ws, plan, expect_header=False, expect_footer=False)
        assert r["ok"] is False
        assert any("HomeHero" in s for s in r["issues"]["missing_default_exports"])
        print(f"  ✓ missing_default_export: caught")
    finally:
        _cleanup(ws)


def test_chrome_missing_is_warning_not_critical():
    """When expect_header=False, missing header is NOT in chrome issues."""
    plan = {"pages": [{"route": "/", "sections": [{"type": "hero"}]}]}
    files = {
        "src/app/page.js": _good_page("home", ["HomeHero"]),
        "src/components/pages/home/HomeHero.jsx": _good_section("HomeHero"),
    }
    ws = _make_workspace(files)
    try:
        # expect_header=True → missing should be flagged as warning
        r = audit_generated_website(ws, plan, expect_header=True, expect_footer=True)
        assert "src/components/layout/MarketingHeader.jsx" in r["issues"]["missing_chrome_files"]
        # But still ok=True because chrome is warning-level, not critical
        # (chrome failures don't increment critical_failures)
        assert r["ok"] is True, f"chrome missing shouldn't fail audit, got: {r['issues']}"
        print(f"  ✓ chrome missing is warning, not critical")
    finally:
        _cleanup(ws)


def main():
    print("="*72)
    print("WEBSITE VERIFICATION — UNIT TESTS")
    print("="*72)
    tests = [
        test_happy_audit,
        test_missing_page_file,
        test_missing_section_file,
        test_broken_import,
        test_css_var_violation,
        test_missing_default_export,
        test_chrome_missing_is_warning_not_critical,
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
