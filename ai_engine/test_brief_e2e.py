"""Quick end-to-end probe of build_landing_brief.

Run:  docker exec lucid-ai-ai_engine-1 python /app/test_brief_e2e.py
"""
import asyncio
import json
import os
import sys
import time

sys.path.insert(0, "/app")

from app.services.landing_brief import build_landing_brief


PROMPT = "modern Italian restaurant in Brooklyn with handmade pasta and natural wine"
CLASSIFICATION = {"domain": "restaurant"}


async def main() -> None:
    key = os.environ.get("GOOGLE_API_KEY", "").strip()
    print(f"GOOGLE_API_KEY present: {bool(key)} (len={len(key)})")
    print(f"Prompt: {PROMPT!r}")
    print(f"Classification: {CLASSIFICATION}")
    print("─" * 70)

    t0 = time.time()
    brief = await build_landing_brief(
        PROMPT,
        CLASSIFICATION,
        gemini_key=key or None,
        websocket=None,
        timeout_s=240.0,
    )
    elapsed = time.time() - t0
    print(f"\n⏱  build_landing_brief returned in {elapsed:.1f}s")
    print("─" * 70)

    # ── 1. Brand
    brand = brief.get("brand", {})
    print("\n=== 1. BRAND ===")
    print(f"  name:     {brand.get('name')!r}")
    print(f"  tagline:  {brand.get('tagline')!r}")
    print(f"  desc:     {brand.get('description')!r}")
    print(f"  domain:   {brand.get('domain')!r}")
    if brand.get("business_info"):
        print(f"  bizinfo:  {brand['business_info']}")
    if brand.get("social"):
        print(f"  social:   {brand['social']}")

    # ── 2. Palette
    print("\n=== 2. PALETTE (HSL) ===")
    for k, v in (brief.get("palette") or {}).items():
        print(f"  --{k:11s}: {v}")

    # ── 3. Typography
    print("\n=== 3. TYPOGRAPHY ===")
    for k, v in (brief.get("typography") or {}).items():
        print(f"  {k}: {v}")

    # ── 4. Motif + design_system
    print(f"\n=== 4. MOTIF: {brief.get('motif')!r} ===")

    print("\n=== 5. DESIGN_SYSTEM ===")
    for k, v in (brief.get("design_system") or {}).items():
        print(f"  {k:18s}: {v}")

    # ── 5. design_tokens (the new pre-computed Tailwind classes)
    print("\n=== 6. DESIGN_TOKENS (pre-computed Tailwind classes) ===")
    for k, v in (brief.get("design_tokens") or {}).items():
        if k.startswith("_"):
            continue
        print(f"  {k:24s}: {v}")

    # ── 6. Personality
    print("\n=== 7. PERSONALITY ===")
    for k, v in (brief.get("personality") or {}).items():
        print(f"  {k:8s}: {v}")

    # ── 7. Header + footer archetypes
    print("\n=== 8. LAYOUT ARCHETYPES ===")
    print(f"  header_archetype: {brief.get('header_archetype')}")
    print(f"  footer_archetype: {brief.get('footer_archetype')}")

    # ── 8. References (proves grounded research worked)
    refs = brief.get("references") or []
    print(f"\n=== 9. REFERENCES — research-grounded (count={len(refs)}) ===")
    for i, r in enumerate(refs, 1):
        print(f"  {i}. {r.get('name')!r}")
        print(f"     url: {r.get('url')}")
        if r.get("why"):
            print(f"     why: {r['why']}")
        if r.get("section_order"):
            print(f"     section_order: {r['section_order']}")
        if r.get("notable_features"):
            print(f"     features: {r['notable_features']}")

    # ── 9. Sections (the full blueprint)
    sections = brief.get("sections") or []
    print(f"\n=== 10. SECTIONS BLUEPRINT (count={len(sections)}) ===")
    for i, s in enumerate(sections, 1):
        print(f"\n  ── Section {i} ─────────────────────────────")
        print(f"  id:            {s.get('id')!r}")
        print(f"  type:          {s.get('type')!r}")
        print(f"  role:          {s.get('role')!r}")
        print(f"  nav_label:     {s.get('nav_label')!r}")
        print(f"  archetype:     {s.get('archetype')!r}")
        print(f"  layout_hint:   {s.get('layout_hint')!r}")
        print(f"  interactivity: {s.get('interactivity')!r}")
        print(f"  headline:      {s.get('headline')!r}")
        if s.get("subheadline"):
            print(f"  subheadline:   {s.get('subheadline')!r}")
        items = s.get("items") or []
        if items:
            print(f"  items ({len(items)}):")
            for j, it in enumerate(items[:3], 1):
                print(f"    {j}. title={it.get('title')!r} desc={(it.get('description') or '')[:80]!r}")
                if it.get("image_query"):
                    print(f"       image_query={it['image_query']!r}")
            if len(items) > 3:
                print(f"    … and {len(items) - 3} more")
        iqs = s.get("image_queries") or []
        if iqs:
            print(f"  image_queries ({len(iqs)}): {iqs}")
        if s.get("cta"):
            print(f"  cta: {s['cta']}")

    # ── 10. CTAs + domain keywords
    print(f"\n=== 11. CTAS ===")
    for k, v in (brief.get("ctas") or {}).items():
        print(f"  {k}: {v}")
    print(f"\n=== 12. DOMAIN_KEYWORDS ===")
    print(f"  {brief.get('domain_keywords')}")

    # ── Summary
    print("\n" + "═" * 70)
    print("SUMMARY")
    print("═" * 70)
    print(f"  ⏱  total time:           {elapsed:.1f}s")
    print(f"  📋 sections:             {len(sections)}")
    print(f"  🌐 references found:     {len(refs)}")
    print(f"  🎨 palette resolved:     {bool(brief.get('palette'))}")
    print(f"  🔤 typography resolved:  {brief.get('typography', {}).get('heading_font')!r} / {brief.get('typography', {}).get('body_font')!r}")
    print(f"  🧩 design_tokens baked:  {bool(brief.get('design_tokens'))}")

    # Dump full brief to /tmp for inspection
    out = "/tmp/test_brief_e2e_output.json"
    with open(out, "w") as fh:
        json.dump(brief, fh, indent=2, ensure_ascii=False)
    print(f"\n  Full brief saved to {out}")


if __name__ == "__main__":
    asyncio.run(main())
