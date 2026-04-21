"""
End-to-end flow test for the 3 optimizations:
  1. _classify_static() replaces classify_project_type_ai() — no Gemini call
  2. build_project_schema() Python fast-path for consumer/landing
  3. Verify Gemini research still runs and corrects classification

Run: cd ai_engine && python3 test_flow.py
"""
import asyncio
import os
import sys
import time

# Load env from root .env
sys.path.insert(0, os.path.dirname(__file__))
def _load_env(path):
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            val = v.strip().strip('"').strip("'")
            os.environ.setdefault(k.strip(), val)

_load_env(os.path.join(os.path.dirname(__file__), "..", ".env"))

GEMINI_KEY = os.environ.get("GOOGLE_API_KEY", "")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

TESTS = [
    ("acca landing",        "single_page_landing", "finance"),
    ("restaurant website",  "consumer_website",    "food_restaurant"),
    ("crm for real estate", "crm",                 "real_estate"),
    ("saas task manager",   "saas_dashboard",      "general"),
    ("medical clinic website", "consumer_website",  "healthcare"),
]


class FakeWS:
    async def send_json(self, data):
        msg = data.get("message", data.get("content", ""))
        if msg:
            print(f"   [ws] {msg[:80]}")


async def test_static_classifier():
    print("\n" + "="*60)
    print("TEST 1: Static classifier (_classify_static)")
    print("="*60)
    from knowledge.loader import _classify_static
    for desc, expected_archetype, expected_domain in TESTS:
        t0 = time.monotonic()
        result = _classify_static(desc)
        elapsed = time.monotonic() - t0
        archetype = result["layout_archetype"]
        domain = result["domain"]
        ok_a = "✓" if archetype == expected_archetype else f"✗ (want {expected_archetype})"
        ok_d = "✓" if domain == expected_domain else f"✗ (want {expected_domain})"
        print(f"  '{desc}'")
        print(f"    archetype: {archetype} {ok_a}")
        print(f"    domain:    {domain} {ok_d}")
        print(f"    time:      {elapsed*1000:.1f}ms")


async def test_schema_fast_path():
    print("\n" + "="*60)
    print("TEST 2: build_project_schema() fast path (no Claude call)")
    print("="*60)
    from app.services.project_schema import build_project_schema

    # Minimal stub research text with the blocks the Python parser reads
    stub_research = """===CLASSIFICATION===
layout_archetype: single_page_landing
domain: finance
is_single_page: yes
nav_style: top_header
has_admin_features: no

===CSS_VARIABLES===
--primary: 221 83% 53% | --primary-foreground: 0 0% 100%
--background: 0 0% 100% | --foreground: 222 47% 11%
--card: 0 0% 100% | --card-foreground: 222 47% 11%
--muted: 210 40% 96% | --muted-foreground: 215 16% 47%
--border: 214 32% 91% | --ring: 221 83% 53%
--radius: 0.5rem

===FONTS===
heading: Inter (https://fonts.googleapis.com/css2?family=Inter:wght@600;700;800&display=swap)
body: Inter (https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap)
overall_vibe: professional minimal

===HEADER===
logo: ACCA Finance
nav_items: Features | Pricing | About | Contact

===SECTIONS===
[section: hero]
headline: Pass Your ACCA Exams First Time
subheadline: Expert tutors, proven study materials.
layout: split-left
background: bg-gradient-to-br from-blue-50 to-white

[section: features]
headline: Everything You Need
layout: grid-3
items: Study materials | Mock exams | Live tutoring | Progress tracking | Community | Results analytics

[section: testimonials]
layout: grid
items: 3 student testimonials

[section: pricing]
headline: Simple Pricing
plans: Basic £29/mo | Pro £49/mo | Enterprise £99/mo

[section: faq]
items: 5 questions

[section: cta_final]
headline: Start Your ACCA Journey Today
cta: Get Started Free

===FOOTER===
columns: Product | Company | Legal
bottom: © 2025 ACCA Finance. All rights reserved.
"""

    ws = FakeWS()
    t0 = time.monotonic()
    schema = await build_project_schema(
        research=stub_research,
        description="acca landing page for finance exam preparation",
        stack="nextjs",
        app_type="single_page_landing",
        api_key=ANTHROPIC_KEY,
        websocket=ws,
    )
    elapsed = time.monotonic() - t0

    sections = schema.get("sections", [])
    nav = schema.get("navigation", [])
    theme = schema.get("theme", {})
    print(f"  time: {elapsed:.2f}s (should be <0.5s — Python only)")
    print(f"  sections: {len(sections)} {['✓' if len(sections) >= 3 else '✗'][0]}")
    print(f"  nav items: {sum(len(g.get('items',[])) for g in nav)}")
    print(f"  theme.primary: {theme.get('primary','MISSING')} {'✓' if theme.get('primary') else '✗'}")
    print(f"  brand.name: {schema.get('brand',{}).get('name','MISSING')}")
    if elapsed > 2:
        print("  ⚠ SLOW — Claude was called unexpectedly")
    else:
        print("  ✓ Fast path used correctly (no API call)")


async def test_consumer_fast_path():
    print("\n" + "="*60)
    print("TEST 3: Consumer website fast path")
    print("="*60)
    from app.services.project_schema import build_project_schema

    stub_research = """===CLASSIFICATION===
layout_archetype: consumer_website
domain: food_restaurant
is_single_page: no
nav_style: top_header
has_admin_features: no

===CSS_VARIABLES===
--primary: 30 90% 50% | --primary-foreground: 0 0% 100%
--background: 0 0% 98% | --foreground: 20 14% 15%
--card: 0 0% 100% | --card-foreground: 20 14% 15%
--muted: 30 10% 94% | --muted-foreground: 20 10% 50%
--border: 30 15% 88% | --ring: 30 90% 50%
--radius: 0.75rem

===FONTS===
heading: Playfair Display (https://fonts.googleapis.com/css2?family=Playfair+Display:wght@600;700;800&display=swap)
body: Inter (https://fonts.googleapis.com/css2?family=Inter:wght@400;500&display=swap)
overall_vibe: warm culinary upscale

===HEADER===
logo: Casa Mia
nav_items: Menu | Reservations | Our Story | Gallery | Contact

===PAGES===
[page: home]
path: /
hero_headline: Authentic Italian Cuisine
hero_subheadline: Fresh pasta made daily. Reservations recommended.
sections: hero, featured_dishes, about_preview, gallery, testimonials, hours

[page: menu]
path: /menu
sections: menu_categories, dish_grid, dietary_legend
purpose: Full menu with photos and prices

[page: reservations]
path: /reservations
sections: booking_form, availability_calendar, policies
purpose: Online reservation system

[page: about]
path: /about
sections: story, team, kitchen_philosophy, awards
purpose: Our story and chef bio

[page: gallery]
path: /gallery
sections: masonry_grid, video_tour
purpose: Food and ambiance photography

[page: contact]
path: /contact
sections: contact_form, location_map, hours, social
purpose: Location, hours, contact

===FOOTER===
columns: Explore | Visit | Connect
bottom: © 2025 Casa Mia. All rights reserved.
"""

    ws = FakeWS()
    t0 = time.monotonic()
    schema = await build_project_schema(
        research=stub_research,
        description="Italian restaurant website Casa Mia",
        stack="nextjs",
        app_type="consumer_website",
        api_key=ANTHROPIC_KEY,
        websocket=ws,
    )
    elapsed = time.monotonic() - t0

    pages = schema.get("pages", [])
    theme = schema.get("theme", {})
    print(f"  time: {elapsed:.2f}s (should be <0.5s — Python only)")
    print(f"  pages: {len(pages)} {['✓' if len(pages) >= 3 else '✗'][0]}")
    print(f"  page paths: {[p.get('path') for p in pages]}")
    print(f"  theme.primary: {theme.get('primary','MISSING')} {'✓' if theme.get('primary') else '✗'}")
    print(f"  brand.name: {schema.get('brand',{}).get('name','MISSING')}")
    if elapsed > 2:
        print("  ⚠ SLOW — Claude was called unexpectedly")
    else:
        print("  ✓ Consumer fast path used (no API call)")


async def test_gemini_research():
    """Test that deep research still works and corrects the initial classification."""
    print("\n" + "="*60)
    print("TEST 4: Gemini deep research (live API call)")
    print("="*60)
    if not GEMINI_KEY:
        print("  SKIP — no GOOGLE_API_KEY")
        return

    from knowledge.loader import _classify_static
    from app.services.project_generator import gemini_deep_research, _extract_layout_archetype

    description = "acca landing"
    classification = _classify_static(description)
    print(f"  Static classification: {classification['layout_archetype']} / {classification['domain']}")

    ws = FakeWS()
    t0 = time.monotonic()
    try:
        research = await gemini_deep_research(description, classification, "nextjs", GEMINI_KEY, ws)
        elapsed = time.monotonic() - t0
        print(f"  Research returned: {len(research)} chars in {elapsed:.1f}s")

        # Check that key blocks are present
        for block in ["===CLASSIFICATION===", "===CSS_VARIABLES===", "===FONTS===", "===SECTIONS==="]:
            present = "✓" if block in research else "✗ MISSING"
            print(f"  {block}: {present}")

        # Check if classification was confirmed/corrected
        confirmed = _extract_layout_archetype(research, classification)
        print(f"  Post-research archetype: {confirmed['layout_archetype']} / {confirmed['domain']}")
    except Exception as e:
        print(f"  ERROR: {e}")


async def test_phase1_generation():
    """Test that Phase 1 actually completes without stop=max_tokens."""
    print("\n" + "="*60)
    print("TEST 5: Phase 1 real Claude generation (live API call)")
    print("="*60)
    if not ANTHROPIC_KEY:
        print("  SKIP — no ANTHROPIC_API_KEY")
        return

    from app.services.project_generator import call_claude_for_json, _phase_token_budget

    # Minimal landing page schema to test Phase 1 budget
    schema = {
        "app_name": "Test Landing",
        "pages": [],
        "sections": [
            {"id": "hero", "headline": "Test Hero"},
            {"id": "features", "headline": "Features"},
            {"id": "cta_final", "headline": "Get Started"},
        ],
        "entities": [],
        "features": [],
    }

    max_tokens, extended = _phase_token_budget(schema, 1, "single_page_landing")
    print(f"  Budget: max_tokens={max_tokens}, extended={extended}")

    system_prompt = "You are a Next.js code generator. Generate complete React components."
    user_prompt = """Generate a minimal Next.js landing page.
Call write_project_files with exactly 2 files:
1. src/app/page.jsx — a hero section with a headline "Hello World"
2. src/components/Hero.jsx — the Hero component

Both files must be complete and valid JSX."""

    ws = FakeWS()
    import time as _t
    t0 = _t.monotonic()
    result = await call_claude_for_json(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        api_key=ANTHROPIC_KEY,
        websocket=ws,
        max_tokens=max_tokens,
    )
    elapsed = _t.monotonic() - t0

    if result and result.get("files"):
        files = result["files"]
        print(f"  ✓ Phase 1 PASSED — {len(files)} files in {elapsed:.1f}s")
        for f in files:
            path = f.get("path", "?")
            size = len(f.get("content", ""))
            print(f"    {path} ({size} chars)")
    else:
        print(f"  ✗ Phase 1 FAILED — no files returned after {elapsed:.1f}s")
        print("    This means stop=max_tokens or API error — check logs above")


async def main():
    await test_static_classifier()
    await test_schema_fast_path()
    await test_consumer_fast_path()
    await test_gemini_research()
    await test_phase1_generation()
    print("\n" + "="*60)
    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
