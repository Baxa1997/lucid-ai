"""End-to-end verification of Stage 7.5 content audit.

Builds two synthetic workspaces:
  1. CLEAN recruitment site — complete, real content, scores ≥90
  2. BROKEN version of that same site — Lorem ipsum injected into one
     section, broken /contact link added → audit must catch both

No Claude / no Gemini needed.

Run: docker exec lucid-ai-ai_engine-1 python /app/scripts/verify_content_audit.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

sys.path.insert(0, "/app")

from app.services.website_verification import audit_content


CLEAN_RECRUITMENT_FILES: dict[str, str] = {
    # Foundation
    "src/config/site.js": (
        'export const siteConfig = { name: "Northbound Freight", '
        'tagline: "Hiring CDL drivers across the Midwest" };\n'
    ),
    "src/lib/design-system.js": 'export const ds = { section: "py-24" };\n',
    "src/components/layout/MarketingHeader.jsx": (
        'export default function MarketingHeader() {\n'
        '  return (<header><a href="/">Northbound</a><a href="/about">About</a></header>);\n'
        "}\n"
    ),
    "src/components/layout/MarketingFooter.jsx": (
        'export default function MarketingFooter() {\n'
        '  return (<footer>© 2026 Northbound Freight, Chicago IL — Apply within</footer>);\n'
        "}\n"
    ),

    # Routes — only /, /about (so /contact would be a broken link)
    "src/app/page.js": (
        'import HomeHero from "@/components/pages/home/HomeHero";\n'
        'import HomeOpenRoles from "@/components/pages/home/HomeOpenRoles";\n'
        'import HomeCompensation from "@/components/pages/home/HomeCompensation";\n'
        'import HomeRequirements from "@/components/pages/home/HomeRequirements";\n'
        'import HomeApplicationForm from "@/components/pages/home/HomeApplicationForm";\n'
        "export default function P() {\n"
        "  return (<><HomeHero/><HomeOpenRoles/><HomeCompensation/><HomeRequirements/><HomeApplicationForm/></>);\n"
        "}\n"
    ),
    "src/app/about/page.js": (
        "export default function AboutPage() {\n"
        '  return <section><h1>About Northbound Freight</h1>'
        "<p>Family-run carrier hauling refrigerated freight across the upper Midwest since 1998.</p>"
        '<a href="/">Back home</a></section>;\n'
        "}\n"
    ),

    # Per-page sections — all clean, real copy
    "src/components/pages/home/HomeHero.jsx": (
        'export default function HomeHero() {\n'
        '  return (\n'
        '    <section className="py-24">\n'
        '      <h1 className="text-5xl font-bold">Drive with Northbound. Get home on weekends.</h1>\n'
        '      <p className="text-lg">CDL-A drivers running dedicated Midwest lanes — predictable schedules, modern Kenworths, and no-touch freight.</p>\n'
        '      <img src="https://images.unsplash.com/photo-1" alt="Northbound Kenworth at fueling stop at sunrise" />\n'
        '      <a href="/about">Why drivers stay</a>\n'
        '    </section>\n'
        '  );\n'
        "}\n"
    ),
    "src/components/pages/home/HomeOpenRoles.jsx": (
        'export default function HomeOpenRoles() {\n'
        '  return (\n'
        '    <section className="py-24">\n'
        '      <h2>Open positions for experienced CDL-A drivers</h2>\n'
        '      <article>\n'
        '        <h3>Dedicated Midwest Lanes</h3>\n'
        '        <p>Chicago to Minneapolis, four round trips weekly, home every weekend.</p>\n'
        '        <span>$0.68 CPM + safety bonus</span>\n'
        '      </article>\n'
        '      <article>\n'
        '        <h3>Regional Reefer</h3>\n'
        '        <p>Refrigerated runs across Iowa and Wisconsin with two scheduled days off each week.</p>\n'
        '        <span>$0.72 CPM + per diem</span>\n'
        '      </article>\n'
        '    </section>\n'
        '  );\n'
        "}\n"
    ),
    "src/components/pages/home/HomeCompensation.jsx": (
        'export default function HomeCompensation() {\n'
        '  return (\n'
        '    <section className="py-24">\n'
        '      <h2>Pay and benefits</h2>\n'
        '      <ul>\n'
        '        <li>Health, dental, and vision starting day 60</li>\n'
        '        <li>$3,000 sign-on bonus paid across the first six months</li>\n'
        '        <li>401(k) with 4% company match</li>\n'
        '      </ul>\n'
        '    </section>\n'
        '  );\n'
        "}\n"
    ),
    "src/components/pages/home/HomeRequirements.jsx": (
        'export default function HomeRequirements() {\n'
        '  return (\n'
        '    <section className="py-24">\n'
        '      <h2>What we look for</h2>\n'
        '      <ul>\n'
        '        <li>Valid CDL-A with at least 12 months OTR experience</li>\n'
        '        <li>Clean MVR within the last three years</li>\n'
        '        <li>DOT medical card current through hire date</li>\n'
        '      </ul>\n'
        '    </section>\n'
        '  );\n'
        "}\n"
    ),
    "src/components/pages/home/HomeApplicationForm.jsx": (
        'export default function HomeApplicationForm() {\n'
        '  return (\n'
        '    <section className="py-24">\n'
        '      <h2>Apply to drive with Northbound</h2>\n'
        '      <form>\n'
        '        <label>Full name<input name="name" type="text" required /></label>\n'
        '        <label>Email<input name="email" type="email" required /></label>\n'
        '        <label>Phone<input name="phone" type="tel" required /></label>\n'
        '        <label>Years CDL-A experience<input name="experience" type="number" /></label>\n'
        '        <button type="submit">Submit application</button>\n'
        '      </form>\n'
        '    </section>\n'
        '  );\n'
        "}\n"
    ),
}


def _write_workspace(files: dict[str, str]) -> str:
    ws = tempfile.mkdtemp(prefix="lucid_content_e2e_")
    for rel, content in files.items():
        abs_p = os.path.join(ws, rel)
        os.makedirs(os.path.dirname(abs_p), exist_ok=True)
        with open(abs_p, "w") as f:
            f.write(content)
    return ws


def _print_report(label: str, r: dict) -> None:
    print(f"\n── {label} ──")
    print(f"  ok            : {r['ok']}")
    print(f"  score         : {r['score']}/100")
    print(f"  summary       : {r['summary']}")
    if r["warnings"]:
        print(f"  warnings      : {r['warnings']}")
    for cat, hits in r["issues"].items():
        if hits:
            print(f"  {cat:<28} ({len(hits)}):")
            for h in hits[:3]:
                print(f"     · {h}")


def main():
    print("=" * 72)
    print("STAGE 7.5 — E2E VERIFICATION")
    print("=" * 72)

    purpose = {"primary_purpose": "recruitment", "industry": "logistics",
               "named_roles": ["CDL drivers"], "target_audience": "job_seekers"}

    # ── CLEAN site ────────────────────────────────────────────
    clean_ws = _write_workspace(CLEAN_RECRUITMENT_FILES)
    try:
        clean = audit_content(clean_ws, purpose, voice_signature={
            "forbidden_phrases": ["world-class", "synergy"],
        })
        _print_report("CLEAN recruitment site", clean)
        clean_ok = clean["score"] >= 90
    finally:
        shutil.rmtree(clean_ws, ignore_errors=True)

    # ── BROKEN site ───────────────────────────────────────────
    # Same files, but inject Lorem ipsum into HomeHero AND add a
    # broken /contact link.
    broken_files = dict(CLEAN_RECRUITMENT_FILES)
    broken_files["src/components/pages/home/HomeHero.jsx"] = (
        'export default function HomeHero() {\n'
        '  return (\n'
        '    <section className="py-24">\n'
        '      <h1 className="text-5xl font-bold">Lorem ipsum dolor sit amet</h1>\n'
        '      <p>Lorem ipsum sed do eiusmod tempor incididunt ut labore et dolore magna aliqua.</p>\n'
        '      <a href="/contact">Reach out</a>\n'
        '      <img src="https://images.unsplash.com/photo-1" alt="image" />\n'
        '    </section>\n'
        '  );\n'
        "}\n"
    )
    broken_ws = _write_workspace(broken_files)
    try:
        broken = audit_content(broken_ws, purpose, voice_signature={
            "forbidden_phrases": ["world-class", "synergy"],
        })
        _print_report("BROKEN recruitment site (Lorem + broken /contact + bad alt)", broken)
        broken_caught = (
            any("Lorem" in h for h in broken["issues"]["placeholders"])
            and any("/contact" in h for h in broken["issues"]["broken_internal_links"])
            and broken["score"] < clean["score"]
        )
    finally:
        shutil.rmtree(broken_ws, ignore_errors=True)

    print("\n" + "═" * 72)
    print(f"  CLEAN  score: {clean['score']}/100  (target ≥90)  →  {'✓' if clean_ok else '✗'}")
    print(f"  BROKEN score: {broken['score']}/100  (must be < clean)  →  {'✓' if broken_caught else '✗'}")
    print("═" * 72)

    return 0 if (clean_ok and broken_caught) else 1


if __name__ == "__main__":
    sys.exit(main())
