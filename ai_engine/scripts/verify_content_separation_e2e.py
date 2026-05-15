"""End-to-end verification of the content/code-separation contract.

Exercises Stage 5 (foundation) → Stage 6 (mocked Claude page output) →
Stage 6.5 (derive .lucid/content-schema.json) → Stage 7b
(audit_content_separation) without spending Claude credits.

Demonstrates:
  1. content/pages/home.json exists and parses
  2. section .jsx imports content from "@/content/pages/home.json"
  3. <Editable> wraps editable elements
  4. .lucid/content-schema.json is derived from the content
  5. Manually editing home.json picks up new fields on re-derive

Run:  docker exec lucid-ai-ai_engine-1 python /app/scripts/verify_content_separation_e2e.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, "/app")

# Force flag ON for this verification.
os.environ["CONTENT_SEPARATION_ENABLED"] = "1"

from app.services.content_schema import write_content_schema
from app.services.website_pipeline import _build_editable_component
from app.services.website_verification import audit_content_separation


# ── Mocked Claude output (what page_generator.generate_one_page would return) ──

_HOME_CONTENT = {
    "hero": {
        "title": "Brewed in Florence since 1923",
        "subtitle": "Family-run espresso bar, three generations strong.",
        "cta_primary": {"label": "Find us", "href": "/visit"},
    },
    "story": {
        "heading": "Four generations, one ristretto",
        "body": "We started on Via Tornabuoni with a single La Pavoni lever machine. "
                "Today the lever stays — and so does the family.",
    },
    "highlights": [
        {"title": "Single-origin beans", "body": "Roasted Tuesdays in our basement.",
         "image": "/images/highlights/beans.jpg", "image_alt": "Roasted coffee beans"},
        {"title": "Hand-pulled shots",   "body": "30-second extractions, every time.",
         "image": "/images/highlights/shot.jpg",  "image_alt": "Espresso pull"},
    ],
}

_HERO_JSX = '''import content from "@/content/pages/home.json";
import { Editable } from "@/lib/editable";

export default function HomeHero() {
  return (
    <section className="py-24 text-center">
      <Editable path="hero.title" type="text">
        <h1 className="text-5xl font-serif">{content.hero.title}</h1>
      </Editable>
      <Editable path="hero.subtitle" type="text">
        <p className="mt-6 text-lg">{content.hero.subtitle}</p>
      </Editable>
      <Editable path="hero.cta_primary.label" type="button">
        <a className="mt-8 inline-block" href={content.hero.cta_primary.href}>
          {content.hero.cta_primary.label}
        </a>
      </Editable>
    </section>
  );
}
'''

_STORY_JSX = '''import content from "@/content/pages/home.json";
import { Editable } from "@/lib/editable";

export default function HomeStory() {
  return (
    <section className="py-20">
      <Editable path="story.heading" type="text">
        <h2 className="text-3xl">{content.story.heading}</h2>
      </Editable>
      <Editable path="story.body" type="rich_text">
        <p className="mt-4 max-w-prose">{content.story.body}</p>
      </Editable>
    </section>
  );
}
'''

_HIGHLIGHTS_JSX = '''import content from "@/content/pages/home.json";
import { Editable } from "@/lib/editable";

export default function HomeHighlights() {
  return (
    <section className="grid gap-8 md:grid-cols-2">
      {content.highlights.map((item, i) => (
        <article key={i}>
          <Editable path="highlights[].title" type="text">
            <h3>{item.title}</h3>
          </Editable>
          <Editable path="highlights[].body" type="text">
            <p>{item.body}</p>
          </Editable>
          <Editable path="highlights[].image" type="image">
            <img src={item.image} alt={item.image_alt} />
          </Editable>
        </article>
      ))}
    </section>
  );
}
'''

_PAGE_JS = '''import HomeHero from "@/components/pages/home/Hero";
import HomeStory from "@/components/pages/home/Story";
import HomeHighlights from "@/components/pages/home/Highlights";

export default function HomePage() {
  return (
    <main>
      <HomeHero />
      <HomeStory />
      <HomeHighlights />
    </main>
  );
}
'''


def _write_files(ws: str, files: dict[str, str]) -> None:
    for rel, body in files.items():
        abs_p = os.path.join(ws, rel)
        os.makedirs(os.path.dirname(abs_p), exist_ok=True)
        with open(abs_p, "w", encoding="utf-8") as f:
            f.write(body)


def main() -> int:
    ws = tempfile.mkdtemp(prefix="lucid_e2e_content_")
    print("=" * 72)
    print("CONTENT/CODE SEPARATION — END-TO-END VERIFICATION (mocked Claude)")
    print("=" * 72)
    print(f"workspace: {ws}\n")

    failures: list[str] = []

    try:
        # ── Stage 5 — foundation file written by _build_foundation_files()
        _write_files(ws, {
            "src/lib/editable.jsx": _build_editable_component(),
        })

        # ── Stage 6 — mocked Claude output for the home page
        _write_files(ws, {
            "src/content/pages/home.json":            json.dumps(_HOME_CONTENT, indent=2),
            "src/components/pages/home/Hero.jsx":      _HERO_JSX,
            "src/components/pages/home/Story.jsx":     _STORY_JSX,
            "src/components/pages/home/Highlights.jsx":_HIGHLIGHTS_JSX,
            "src/app/page.js":                         _PAGE_JS,
        })

        files_written = []
        for root, _, names in os.walk(ws):
            for n in names:
                rel = os.path.relpath(os.path.join(root, n), ws)
                files_written.append(rel)
        print("Files generated:")
        for f in sorted(files_written):
            print(f"  • {f}")
        print()

        # ── Stage 6.5 — derive content schema
        schema_path, field_count = write_content_schema(ws)
        rel_schema = os.path.relpath(schema_path, ws)
        print(f"✓ Stage 6.5  derived {rel_schema} — {field_count} editable field(s)")

        # ── Check 1 — content JSON exists & is valid
        home_json = os.path.join(ws, "src/content/pages/home.json")
        try:
            with open(home_json) as f:
                parsed = json.load(f)
            assert isinstance(parsed, dict) and parsed
            print(f"✓ check 1    content JSON parses ({len(parsed)} top-level keys)")
        except (OSError, json.JSONDecodeError, AssertionError) as exc:
            failures.append(f"home.json invalid: {exc}")
            print(f"✗ check 1    home.json invalid: {exc}")

        # ── Check 2 — every section .jsx imports from @/content/pages/home.json
        sections = [
            "src/components/pages/home/Hero.jsx",
            "src/components/pages/home/Story.jsx",
            "src/components/pages/home/Highlights.jsx",
        ]
        for rel in sections:
            with open(os.path.join(ws, rel)) as f:
                body = f.read()
            if '@/content/pages/home.json' not in body:
                failures.append(f"{rel} missing content import")
                print(f"✗ check 2    {rel} missing content import")
            else:
                print(f"✓ check 2    {rel} imports content/pages/home.json")

        # ── Check 3 — Editable wraps editable elements
        for rel in sections:
            with open(os.path.join(ws, rel)) as f:
                body = f.read()
            if '<Editable' not in body:
                failures.append(f"{rel} has no <Editable> wrapper")
                print(f"✗ check 3    {rel} has no <Editable> wrapper")
            else:
                count = body.count('<Editable')
                print(f"✓ check 3    {rel} contains {count} <Editable> wrapper(s)")

        # ── Check 4 — Stage 7b audit clean
        audit = audit_content_separation(ws)
        if audit["ok"]:
            print(f"✓ check 4    {audit['summary']}")
        else:
            failures.append(f"audit failed: {audit['summary']}")
            print(f"✗ check 4    {audit['summary']}")
            for cat, items in audit["issues"].items():
                for it in items:
                    print(f"             [{cat}] {it}")

        # ── Check 5 — manual edit to home.json reflects on next derive ──
        edited = dict(_HOME_CONTENT)
        edited["hero"] = {**edited["hero"], "title": "Espresso al banco, da 100 anni"}
        edited["newsletter"] = {"heading": "Stay in the loop", "placeholder": "you@example.com"}
        with open(home_json, "w") as f:
            json.dump(edited, f, indent=2)

        _, new_field_count = write_content_schema(ws)
        with open(os.path.join(ws, ".lucid/content-schema.json")) as f:
            new_schema = json.load(f)
        home_fields = (new_schema.get("pages") or {}).get("home", {}).get("fields", {})

        if "newsletter.heading" in home_fields and home_fields.get("hero.title"):
            print(
                f"✓ check 5    manual edit reflected — schema grew from "
                f"{field_count} → {new_field_count} fields, includes newsletter.heading"
            )
        else:
            failures.append("manual edit not reflected in re-derived schema")
            print("✗ check 5    re-derived schema missing newly-added newsletter.heading")

        # ── Summary ──
        print()
        print("─" * 72)
        if failures:
            print(f"RESULT: FAIL — {len(failures)} issue(s)")
            for f in failures:
                print(f"  - {f}")
            return 1
        print("RESULT: PASS — all 5 verification checks succeeded")
        return 0
    finally:
        shutil.rmtree(ws, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
