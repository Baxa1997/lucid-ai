"""Offline verification — no Claude needed.

1. Renders the actual prompt page_generator builds, showing the IMAGES
   block that Claude WOULD see.
2. HEAD-checks every bound Unsplash URL → confirms they load (no 404s).
3. Runs the Stage 7.5 image audit against a synthetic workspace containing
   both GOOD (real Unsplash URLs) and BAD (/images/...) JSX, to confirm
   the audit catches violations.

Run: docker exec lucid-ai-ai_engine-1 python /app/scripts/verify_images_offline.py
"""
from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile

import httpx

sys.path.insert(0, "/app")

from app.services.image_binding import (
    bind_page_images, clear_image_cache, format_images_for_prompt,
)
from app.services.page_generator import _build_user_prompt
from app.services.website_verification import audit_generated_website


PAGE_PLAN = {
    "route": "/",
    "title": "Home",
    "purpose": "Welcome visitors to the cafe.",
    "sections": [
        {"type": "hero",         "purpose": "Espresso photography + headline"},
        {"type": "features",     "purpose": "Three reasons to visit"},
        {"type": "gallery",      "purpose": "Photos of the cafe"},
        {"type": "testimonials", "purpose": "Reviews",
         "items": [{"title": "Anna"}, {"title": "Marco"}, {"title": "Sofia"}]},
        {"type": "cta",          "purpose": "Visit today"},
    ],
}


async def head_status(url: str, client: httpx.AsyncClient) -> int:
    try:
        r = await client.head(url, follow_redirects=True, timeout=10.0)
        return r.status_code
    except Exception:
        try:
            async with client.stream("GET", url, follow_redirects=True, timeout=10.0) as r:
                return r.status_code
        except Exception:
            return -1


async def step_1_render_prompt():
    print("=" * 72)
    print("STEP 1 — Stage 5.5 binds images, prompt renders with real URLs")
    print("=" * 72)
    clear_image_cache()
    images = await bind_page_images(
        page_plan=PAGE_PLAN,
        industry="italian cafe",
        purpose_data={"primary_purpose": "brand_awareness", "industry": "italian cafe"},
        visual_dna={"photography_style": "warm natural light"},
    )

    print(f"\n── Stage 5.5 bound {sum(len(v) for v in images.values())} images "
          f"across {len(images)} sections ──")
    for key, imgs in images.items():
        print(f"  {key:<15}  {len(imgs)} img(s)")

    # Build the user prompt EXACTLY as page_generator would, with our images
    from app.services.page_generator import _section_component_name, _section_anatomy_for
    visual_dna = {"photography_style": "warm natural light"}
    slug = "home"
    section_specs = [{
        "type":      (s.get("type") or "section").strip().lower(),
        "component": _section_component_name(slug, s.get("type") or "section"),
        "purpose":   s.get("purpose", ""),
        "anatomy":   _section_anatomy_for(visual_dna, s.get("type") or ""),
    } for s in PAGE_PLAN["sections"]]

    prompt = _build_user_prompt(
        page=PAGE_PLAN, slug=slug,
        section_specs=section_specs, visual_dna=visual_dna,
        page_images=images,
    )

    print(f"\n── Rendered IMAGES block (what Claude reads) ──")
    block = format_images_for_prompt(images)
    print(block)

    print(f"\n── Prompt size ──  {len(prompt)} chars total")
    return images


async def step_2_head_check_urls(images: dict):
    print("\n" + "=" * 72)
    print("STEP 2 — HEAD-check every bound Unsplash URL")
    print("=" * 72)
    urls = []
    for v in images.values():
        for img in v:
            urls.append(img["url"])
    urls = list(set(urls))
    print(f"\nUnique URLs to check: {len(urls)}")

    async with httpx.AsyncClient() as client:
        statuses = await asyncio.gather(*[head_status(u, client) for u in urls])

    bad = []
    for url, status in zip(urls, statuses):
        marker = "✓" if 200 <= status < 400 else "✗"
        print(f"  {marker} {status}  {url[:100]}")
        if not (200 <= status < 400):
            bad.append((url, status))

    return bad


def step_3_audit_synthetic_workspace():
    print("\n" + "=" * 72)
    print("STEP 3 — Stage 7.5 audit catches image violations")
    print("=" * 72)
    ws = tempfile.mkdtemp(prefix="lucid_image_audit_")
    try:
        # ── Good page: real Unsplash URLs + meaningful alt ──
        os.makedirs(f"{ws}/src/app", exist_ok=True)
        os.makedirs(f"{ws}/src/components/pages/home", exist_ok=True)
        os.makedirs(f"{ws}/src/components/layout", exist_ok=True)
        with open(f"{ws}/src/app/page.js", "w") as f:
            f.write(
                'import HomeHero from "@/components/pages/home/HomeHero";\n'
                'export default function P() { return <HomeHero/>; }\n'
            )
        with open(f"{ws}/src/components/pages/home/HomeHero.jsx", "w") as f:
            f.write(
                'export default function HomeHero() {\n'
                '  return (\n'
                '    <section>\n'
                '      <img src="https://images.unsplash.com/photo-abc" alt="Espresso pour" />\n'
                '      <img src="/images/local-broken.jpg" alt="Bad local path" />\n'
                '      <img src="https://images.unsplash.com/photo-xyz" alt="image" />\n'
                '      <img src="https://images.unsplash.com/photo-def" />\n'
                '    </section>\n'
                '  );\n'
                '}\n'
            )
        # Required chrome files
        for chrome in ("MarketingHeader", "MarketingFooter"):
            with open(f"{ws}/src/components/layout/{chrome}.jsx", "w") as f:
                f.write(f'export default function {chrome}() {{ return null; }}\n')

        plan = {"pages": [{"route": "/", "sections": [{"type": "hero"}]}]}
        report = audit_generated_website(ws, plan, expect_header=True, expect_footer=True)

        print(f"\n  Summary: {report['summary']}")
        print(f"\n  Issues found:")
        for key, val in report["issues"].items():
            if val:
                print(f"    {key}: {len(val)}")
                for item in val[:3]:
                    print(f"      · {item}")

        # Expectations
        local = report["issues"]["local_image_paths"]
        alt_issues = report["issues"]["missing_alt_text"]

        ok = (
            len(local) == 1                          # caught /images/local-broken.jpg
            and len(alt_issues) >= 2                  # alt="image" + missing alt
        )
        print(f"\n  Audit caught local /images/ : {len(local) >= 1}  (target ≥1)")
        print(f"  Audit caught bad/missing alt: {len(alt_issues) >= 2}  (target ≥2)")
        return ok
    finally:
        shutil.rmtree(ws, ignore_errors=True)


async def main():
    images = await step_1_render_prompt()
    bad_urls = await step_2_head_check_urls(images)
    audit_ok = step_3_audit_synthetic_workspace()

    print("\n" + "═" * 72)
    print("FINAL")
    print("═" * 72)
    print(f"  Step 1 (prompt rendering)        : ✓")
    print(f"  Step 2 (URLs reachable)          : {'✓' if not bad_urls else f'✗  {len(bad_urls)} 404(s)'}")
    print(f"  Step 3 (audit catches violations): {'✓' if audit_ok else '✗'}")
    if bad_urls:
        for u, s in bad_urls:
            print(f"     · {s}  {u}")

    return 0 if (not bad_urls and audit_ok) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
