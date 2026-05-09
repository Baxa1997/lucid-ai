"""End-to-end Vertex smoke test for landing + multi-page paths.

Runs the Vertex-routed Gemini call sites for two representative prompts:
  • Landing — single_page_landing archetype
  • Full website — multi-page consumer site

Skips Claude codegen (most of the cost). Confirms:
  1. Classifier picks the right archetype (proves shim + JSON output)
  2. Intent analysis returns valid structure (proves Flash + responseSchema)
  3. Grounded research returns text + sources (proves Pro + google_search tool)

Run inside the ai_engine container:
  docker exec lucid-ai-ai_engine-1 python3 test_vertex_e2e_smoke.py
"""

import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.config import settings
from knowledge.loader import classify_project_type_ai
from app.services.landing_intent import analyze_intent
from app.services.landing_gemini import grounded_research


LANDING_PROMPT = (
    "A landing page for an organic farm-to-table restaurant called "
    "Verdant Table in Brooklyn — seasonal tasting menus, private events, "
    "online reservations."
)

WEBSITE_PROMPT = (
    "Build a full website for an Uzbek pottery studio called Loy Atelier — "
    "home page with hero, about page with the artisan's story, products "
    "catalog with 12 ceramic pieces, workshops/classes page, and contact form."
)


def banner(msg: str) -> None:
    print("\n" + "═" * 60)
    print(f" {msg}")
    print("═" * 60)


async def test_path(label: str, prompt: str) -> dict:
    banner(f"{label}: {prompt[:60]}...")
    results = {"label": label, "passes": 0, "fails": 0, "warnings": []}

    gemini_key = os.environ.get("GOOGLE_API_KEY", "")

    # ── Stage 1: Classification ─────────────────────────────
    print("\n[1/3] classify_project_type_ai (Flash + JSON)")
    t0 = time.monotonic()
    try:
        cls = await classify_project_type_ai(prompt, gemini_key)
        dt = time.monotonic() - t0
        archetype = cls.get("layout_archetype", "")
        domain = cls.get("domain", "")
        print(f"      ✅ {dt:.1f}s  archetype={archetype}  domain={domain}")
        results["passes"] += 1
        results["archetype"] = archetype
        results["domain"] = domain
    except Exception as e:
        print(f"      ❌ FAILED: {type(e).__name__}: {e}")
        results["fails"] += 1
        results["archetype"] = ""
        results["domain"] = ""

    # ── Stage 2: Intent ──────────────────────────────────────
    print("\n[2/3] analyze_intent (Flash + responseSchema)")
    t0 = time.monotonic()
    try:
        intent = await analyze_intent(
            prompt,
            classification={"domain": results["domain"]},
            gemini_key=gemini_key,
        )
        dt = time.monotonic() - t0
        cat = intent.get("business_category", "?")
        sections = len(intent.get("must_have_sections") or [])
        print(f"      ✅ {dt:.1f}s  category={cat}  sections={sections}")
        results["passes"] += 1
    except Exception as e:
        print(f"      ❌ FAILED: {type(e).__name__}: {e}")
        results["fails"] += 1

    # ── Stage 3: Grounded research (Pro + google_search) ────
    print("\n[3/3] grounded_research (Pro + google_search)")
    research_prompt = (
        f"Research 2 real-world reference sites for this brief:\n\n{prompt}\n\n"
        "List 2 actual sites by name with their URLs and what makes them notable. "
        "Concise — under 200 words."
    )
    t0 = time.monotonic()
    try:
        text, sources, urls = await grounded_research(
            research_prompt,
            gemini_key,
            timeout_s=120.0,
            label=f"{label.lower()}_smoke",
            max_tokens=2048,
            thinking_budget=512,
            temperature=0.3,
        )
        dt = time.monotonic() - t0
        if text:
            print(f"      ✅ {dt:.1f}s  text={len(text)} chars  sources={sources}")
            print(f"         preview: {text[:140].replace(chr(10), ' ')}...")
            if sources == 0:
                results["warnings"].append("grounded_research returned text but 0 sources")
            results["passes"] += 1
        else:
            print(f"      ❌ FAILED: empty text after {dt:.1f}s")
            results["fails"] += 1
    except Exception as e:
        print(f"      ❌ FAILED: {type(e).__name__}: {e}")
        results["fails"] += 1

    return results


async def main() -> int:
    banner("Vertex AI End-to-End Smoke Test")
    print(f"  USE_VERTEX_AI       = {settings.USE_VERTEX_AI}")
    print(f"  GOOGLE_CLOUD_PROJECT = {settings.GOOGLE_CLOUD_PROJECT}")
    print(f"  GOOGLE_CLOUD_LOCATION = {settings.GOOGLE_CLOUD_LOCATION}")

    if not settings.USE_VERTEX_AI:
        print("\n⚠️  USE_VERTEX_AI is false — these tests would hit AI Studio, not Vertex.")
        return 1

    landing = await test_path("LANDING", LANDING_PROMPT)
    website = await test_path("WEBSITE", WEBSITE_PROMPT)

    banner("FINAL RESULTS")
    for r in (landing, website):
        total = r["passes"] + r["fails"]
        status = "✅" if r["fails"] == 0 else "❌"
        print(f"  {status} {r['label']}: {r['passes']}/{total} passed  "
              f"(archetype={r.get('archetype', '?')})")
        for w in r["warnings"]:
            print(f"      ⚠️  {w}")

    # Sanity check — make sure classifier picked different archetypes
    a1 = landing.get("archetype", "")
    a2 = website.get("archetype", "")
    if a1 == "single_page_landing":
        print(f"  ✅ Landing classified correctly: {a1}")
    else:
        print(f"  ⚠️  Landing got archetype={a1!r}, expected single_page_landing")

    if a2 and a2 != "single_page_landing":
        print(f"  ✅ Website classified to multi-page archetype: {a2}")
    else:
        print(f"  ⚠️  Website got archetype={a2!r}, expected multi-page")

    return 0 if (landing["fails"] == 0 and website["fails"] == 0) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
