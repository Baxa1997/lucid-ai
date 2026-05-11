"""Smoke test: page_codegen.generate_page().

Builds a hand-crafted "pricing page" brief and runs ONE Claude call
through generate_page(). Verifies:
  • the call returns a non-None dict
  • it contains the route file
  • it contains a section file per spec section
  • each section file is non-empty JSX

Run:  docker exec lucid-ai-ai_engine-1 python /app/test_page_codegen_smoke.py
"""
import asyncio
import os
import sys
import time

sys.path.insert(0, "/app")

from app.services.page_codegen import generate_page


# ── hand-crafted page brief ─────────────────────────────────────────

BRAND_NAME = "Aerial"
MOTIF = "minimal"
PALETTE = {
    "primary": "240 100% 60%",
    "primary-foreground": "0 0% 100%",
    "background": "0 0% 100%",
    "foreground": "240 10% 4%",
    "muted": "240 5% 96%",
    "muted-foreground": "240 4% 46%",
    "border": "240 6% 90%",
    "card": "0 0% 100%",
    "accent": "240 5% 96%",
    "accent-foreground": "240 6% 10%",
    "radius": "0.5rem",
}
TYPOGRAPHY = {"heading": "Inter", "body": "Inter"}
DESIGN_SYSTEM = {
    "radius": "0.5rem",
    "spacing_scale": "default",
    "shadow_intensity": "subtle",
}
PERSONALITY = {"tone": "confident", "vibe_keywords": ["modern", "clear"], "energy": "medium"}

PAGE_META = {
    "slug": "pricing",
    "title": "Pricing — Aerial",
    "nav_label": "Pricing",
    "page_goal": "Help SaaS buyers compare three plans and pick the right one for their team size.",
    "primary_cta": {"label": "Start free trial", "href": "/signup"},
}

SECTIONS = [
    {
        "id": "pricing_hero",
        "type": "hero",
        "headline": "Simple, transparent pricing.",
        "subheadline": "Pick the plan that fits your team. Switch anytime, no surprises.",
        "body": "All plans include unlimited users, 14-day free trial, no credit card required.",
        "cta": {"label": "Start free trial", "href": "/signup"},
    },
    {
        "id": "pricing_table",
        "type": "pricing_table",
        "headline": "Three plans, no add-ons.",
        "items": [
            {"title": "Starter", "label": "$0/mo", "body": "For solo builders. Up to 3 projects."},
            {"title": "Team", "label": "$29/mo", "body": "For small teams. Unlimited projects, priority support."},
            {"title": "Business", "label": "$99/mo", "body": "For scale. SSO, audit log, dedicated manager."},
        ],
    },
    {
        "id": "pricing_faq",
        "type": "faq",
        "headline": "Frequently asked questions.",
        "items": [
            {"title": "Can I switch plans?", "body": "Yes — upgrade or downgrade anytime. Pro-rated."},
            {"title": "Do you offer discounts?", "body": "Yes — 20% off annual billing, 30% for non-profits."},
            {"title": "Is there a free trial?", "body": "Yes — 14 days, no credit card needed."},
            {"title": "Can I cancel anytime?", "body": "Yes — cancel from your account, no questions asked."},
        ],
    },
]


async def main() -> None:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set")
        sys.exit(1)
    print(f"ANTHROPIC_API_KEY present: len={len(api_key)}")
    print(f"Page: {PAGE_META['title']!r}, sections={len(SECTIONS)}")

    t0 = time.time()
    result = await generate_page(
        page_meta=PAGE_META,
        sections=SECTIONS,
        brand_name=BRAND_NAME,
        motif=MOTIF,
        palette=PALETTE,
        typography=TYPOGRAPHY,
        design_system=DESIGN_SYSTEM,
        personality=PERSONALITY,
        references=None,
        design_tokens=None,
        voice_context=None,
        api_key=api_key,
        websocket=None,
    )
    elapsed = time.time() - t0
    print(f"⏱  {elapsed:.1f}s")

    if result is None:
        print("FAIL — generate_page returned None")
        sys.exit(1)

    files = result.get("files") or []
    print(f"page_slug:  {result['page_slug']!r}")
    print(f"route_path: {result['route_path']!r}")
    print(f"files:      {len(files)} total")

    expected_route = "src/app/pricing/page.jsx"
    if files[0]["path"] != expected_route:
        print(f"FAIL — first file is {files[0]['path']!r}, expected {expected_route!r}")
        sys.exit(1)

    if len(files) < 1 + len(SECTIONS):
        print(f"FAIL — got {len(files)} files, expected >= {1 + len(SECTIONS)} (1 route + {len(SECTIONS)} sections)")
        sys.exit(1)

    out_dir = "/tmp/test_page_codegen_smoke"
    os.makedirs(out_dir, exist_ok=True)
    print(f"\n── files ──")
    for f in files:
        path = f["path"]
        size = len(f["content"])
        ok_jsx = (
            ("export default" in f["content"])
            and ("function" in f["content"] or "=>" in f["content"])
        )
        print(f"  {'✓' if ok_jsx else '⚠'} {path} ({size:,} chars)")

        # Snapshot to /tmp for inspection
        rel = path.lstrip("/").replace("/", "__")
        with open(os.path.join(out_dir, rel), "w", encoding="utf-8") as fh:
            fh.write(f["content"])

    # Spot-check: does the route file import the expected content JSON?
    route = files[0]["content"]
    must_have = [
        "@/content/pricing.json",   # content import
    ]
    missing = [m for m in must_have if m not in route]
    if missing:
        print(f"\n⚠ route file missing expected refs: {missing}")
    else:
        print(f"\n✓ route imports @/content/pricing.json")

    print(f"\nFull output saved to {out_dir}")
    print("PASS")


if __name__ == "__main__":
    asyncio.run(main())
