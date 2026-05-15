"""Tests for the Stage-6 content/code-separation contract.

Five cases per spec:
  1. Clean JSX (uses {content.x}) → no hardcoded copy flagged
  2. Hardcoded copy present → check_no_hardcoded_copy flags the line
  3. <Editable path="..."> referencing a real key → valid; missing key → flagged
  4. Content JSON parses → derive_schema_for_page returns matching paths
  5. audit_content_separation catches each violation type in a dirty workspace

Run inside the ai_engine container:
    docker exec -it lucid-ai-ai_engine-1 python /app/tests/test_content_separation.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, "/app")

from app.services.content_schema import (
    derive_content_schema,
    derive_schema_for_page,
    write_content_schema,
)
from app.services.website_verification import (
    audit_content_separation,
    check_content_schema_matches,
    check_editable_paths_valid,
    check_no_hardcoded_copy,
)


# ── Fixtures ─────────────────────────────────────────────────────────────

_HOME_CONTENT = {
    "hero": {
        "title": "Welcome to Acme",
        "subtitle": "We build delightful things",
        "cta_primary": {"label": "Get started", "href": "/signup"},
    },
    "features": [
        {"title": "Fast", "body": "Sub-millisecond responses every time."},
        {"title": "Reliable", "body": "99.99% uptime guaranteed."},
    ],
}

_CLEAN_JSX = '''import content from "@/content/pages/home.json";
import { Editable } from "@/lib/editable";

export default function HomeHero() {
  return (
    <section className="py-24">
      <Editable path="hero.title" type="text">
        <h1 className="text-5xl">{content.hero.title}</h1>
      </Editable>
      <Editable path="hero.subtitle" type="text">
        <p>{content.hero.subtitle}</p>
      </Editable>
      <Editable path="hero.cta_primary.label" type="button">
        <a href={content.hero.cta_primary.href}>{content.hero.cta_primary.label}</a>
      </Editable>
    </section>
  );
}
'''

_HARDCODED_JSX = '''import content from "@/content/pages/home.json";
import { Editable } from "@/lib/editable";

export default function HomeHero() {
  return (
    <section>
      <h1>Welcome to Acme Corporation</h1>
      <p>{content.hero.subtitle}</p>
    </section>
  );
}
'''

_BAD_PATH_JSX = '''import content from "@/content/pages/home.json";
import { Editable } from "@/lib/editable";

export default function HomeHero() {
  return (
    <Editable path="hero.does_not_exist" type="text">
      <h1>{content.hero.title}</h1>
    </Editable>
  );
}
'''

_BAD_TYPE_JSX = '''import content from "@/content/pages/home.json";
import { Editable } from "@/lib/editable";

export default function HomeHero() {
  return (
    <Editable path="hero.title" type="markdown">
      <h1>{content.hero.title}</h1>
    </Editable>
  );
}
'''


def _make_workspace(files: dict[str, str]) -> str:
    ws = tempfile.mkdtemp(prefix="lucid_content_sep_")
    for rel, body in files.items():
        abs_p = os.path.join(ws, rel)
        os.makedirs(os.path.dirname(abs_p), exist_ok=True)
        with open(abs_p, "w", encoding="utf-8") as f:
            f.write(body)
    return ws


# ── Case 1 — clean JSX has no hardcoded copy ─────────────────────────────

def test_clean_jsx_no_hardcoded_copy() -> None:
    ws = _make_workspace({
        "src/content/pages/home.json": json.dumps(_HOME_CONTENT),
        "src/components/pages/home/Hero.jsx": _CLEAN_JSX,
    })
    try:
        flagged = check_no_hardcoded_copy(
            os.path.join(ws, "src/components/pages/home/Hero.jsx")
        )
        assert flagged == [], f"clean JSX should not flag, got: {flagged}"
        print("✓ case 1 — clean JSX has no hardcoded copy")
    finally:
        shutil.rmtree(ws, ignore_errors=True)


# ── Case 2 — hardcoded copy is caught ────────────────────────────────────

def test_hardcoded_copy_flagged() -> None:
    ws = _make_workspace({
        "src/content/pages/home.json": json.dumps(_HOME_CONTENT),
        "src/components/pages/home/Hero.jsx": _HARDCODED_JSX,
    })
    try:
        flagged = check_no_hardcoded_copy(
            os.path.join(ws, "src/components/pages/home/Hero.jsx")
        )
        assert flagged, "hardcoded JSX should be flagged"
        joined = " ".join(flagged)
        assert "Acme Corporation" in joined or "Welcome" in joined, (
            f"flagged lines should mention hardcoded text, got: {flagged}"
        )
        print(f"✓ case 2 — hardcoded copy flagged ({len(flagged)} line(s))")
    finally:
        shutil.rmtree(ws, ignore_errors=True)


# ── Case 3 — Editable path validation ────────────────────────────────────

def test_editable_paths_valid_and_invalid() -> None:
    ws = _make_workspace({
        "src/content/pages/home.json": json.dumps(_HOME_CONTENT),
        "src/components/pages/home/Hero.jsx": _CLEAN_JSX,
        "src/components/pages/home/Bad.jsx": _BAD_PATH_JSX,
        "src/components/pages/home/BadType.jsx": _BAD_TYPE_JSX,
    })
    try:
        jsx_files = [
            os.path.join(ws, "src/components/pages/home/Hero.jsx"),
            os.path.join(ws, "src/components/pages/home/Bad.jsx"),
            os.path.join(ws, "src/components/pages/home/BadType.jsx"),
        ]
        content_files = {"home": _HOME_CONTENT}
        issues = check_editable_paths_valid(jsx_files, content_files)
        joined = " ".join(issues)
        assert "does_not_exist" in joined, (
            f"bad path should be reported, got: {issues}"
        )
        assert "markdown" in joined, (
            f"bad type should be reported, got: {issues}"
        )
        # Hero.jsx paths are all valid — none of its lines should appear.
        assert "Hero.jsx" not in joined or "does_not_exist" not in joined.replace("Hero.jsx", ""), \
            "valid Editable paths should not be flagged"
        print(f"✓ case 3 — Editable paths validated ({len(issues)} issue(s))")
    finally:
        shutil.rmtree(ws, ignore_errors=True)


# ── Case 4 — schema derivation matches the JSON shape ────────────────────

def test_schema_matches_content() -> None:
    page_schema = derive_schema_for_page(_HOME_CONTENT)
    # Required leaf paths
    for path in (
        "hero.title",
        "hero.subtitle",
        "hero.cta_primary.label",
        "hero.cta_primary.href",
        "features",
        "features[].title",
        "features[].body",
    ):
        assert path in page_schema, (
            f"derived schema missing {path!r}; got keys: {sorted(page_schema)}"
        )
    # Type inference sanity
    assert page_schema["hero.cta_primary.href"]["type"] == "url"
    assert page_schema["features"]["type"] == "array"
    assert page_schema["hero.title"]["type"] == "text"
    print(f"✓ case 4 — derived schema covers {len(page_schema)} path(s)")


# ── Case 5 — audit_content_separation catches every violation type ───────

def test_audit_catches_all_violations() -> None:
    bad_workspace = {
        "src/content/pages/home.json": json.dumps(_HOME_CONTENT),
        "src/components/pages/home/Hero.jsx": _HARDCODED_JSX,
        "src/components/pages/home/Bad.jsx":  _BAD_PATH_JSX,
        # An extra path in the schema that does NOT exist in content
        ".lucid/content-schema.json": json.dumps({
            "version": "1.0",
            "pages": {
                "home": {
                    "fields": {
                        "hero.title": {"type": "text"},
                        "hero.ghost_field": {"type": "text"},
                    }
                }
            },
        }),
    }
    ws = _make_workspace(bad_workspace)
    try:
        os.environ.setdefault("CONTENT_SEPARATION_ENABLED", "1")
        result = audit_content_separation(ws)
        assert not result["ok"], "audit must fail on dirty workspace"
        issues = result["issues"]
        assert issues["hardcoded_copy"], "should detect hardcoded copy"
        assert issues["bad_editable_paths"], "should detect bad <Editable path>"
        assert issues["schema_mismatches"], "should detect schema↔content mismatch"
        print(
            "✓ case 5 — audit caught: "
            f"{len(issues['hardcoded_copy'])} hardcoded, "
            f"{len(issues['bad_editable_paths'])} bad path(s), "
            f"{len(issues['schema_mismatches'])} schema mismatch(es)"
        )
    finally:
        shutil.rmtree(ws, ignore_errors=True)


# ── Bonus — clean workspace audit passes ─────────────────────────────────

def test_audit_passes_on_clean_workspace() -> None:
    ws = _make_workspace({
        "src/content/pages/home.json": json.dumps(_HOME_CONTENT),
        "src/components/pages/home/Hero.jsx": _CLEAN_JSX,
    })
    try:
        # Derive a real schema for this workspace so the cross-check passes.
        write_content_schema(ws)
        result = audit_content_separation(ws)
        assert result["ok"], (
            f"clean workspace should pass, got issues: {result['issues']}"
        )
        print(f"✓ bonus — clean workspace audit PASS ({result['summary']})")
    finally:
        shutil.rmtree(ws, ignore_errors=True)


# ── Runner ───────────────────────────────────────────────────────────────

def main() -> int:
    cases = [
        test_clean_jsx_no_hardcoded_copy,
        test_hardcoded_copy_flagged,
        test_editable_paths_valid_and_invalid,
        test_schema_matches_content,
        test_audit_catches_all_violations,
        test_audit_passes_on_clean_workspace,
    ]
    failures: list[str] = []
    for fn in cases:
        try:
            fn()
        except AssertionError as exc:
            failures.append(f"{fn.__name__}: {exc}")
            print(f"✗ {fn.__name__} — {exc}")
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{fn.__name__}: unexpected {type(exc).__name__}: {exc}")
            print(f"✗ {fn.__name__} — unexpected {type(exc).__name__}: {exc}")
    print()
    if failures:
        print(f"FAILED: {len(failures)}/{len(cases)}")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"PASSED: {len(cases)}/{len(cases)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
