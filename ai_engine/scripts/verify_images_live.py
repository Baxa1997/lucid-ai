"""Live end-to-end image binding verification.

Generates ONE real page through Claude with bound Unsplash images, then
audits the output JSX:
  - Counts <img> tags
  - Verifies every src is a real Unsplash URL (not /images/...)
  - Verifies every alt is present + non-trivial
  - HEAD-checks each unique URL → counts 404s

Run: docker exec lucid-ai-ai_engine-1 python /app/scripts/verify_images_live.py
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
from collections import Counter

import httpx

sys.path.insert(0, "/app")

from app.services.image_binding import bind_page_images, clear_image_cache
from app.services.page_generator import generate_one_page


_IMG_RX = re.compile(r"<(?:img|Image)\b([^>]*?)/?\s*>", re.IGNORECASE | re.DOTALL)
_SRC_RX = re.compile(r'\bsrc\s*=\s*"([^"]+)"|\bsrc\s*=\s*\{["\']([^"\']+)["\']\s*\}')
_ALT_RX = re.compile(r'\balt\s*=\s*"([^"]*)"|\balt\s*=\s*\{["\']([^"\']*)["\']\s*\}')


PAGE_PLAN = {
    "route": "/",
    "title": "Home",
    "purpose": "Welcome visitors to the cafe and showcase the menu and atmosphere.",
    "sections": [
        {"type": "hero",         "purpose": "Big espresso photography + warm headline"},
        {"type": "features",     "purpose": "Three reasons to visit (icon grid)"},
        {"type": "gallery",      "purpose": "Photos of the cafe interior and pastries"},
        {"type": "testimonials", "purpose": "Reviews from regulars",
         "items": [{"title": "Anna"}, {"title": "Marco"}, {"title": "Sofia"}]},
        {"type": "cta",          "purpose": "Visit us today"},
    ],
}


VISUAL_DNA = {
    "cultural_intensity":      "subtle",
    "photography_style":       "warm natural light italian cafe",
    "layout_signature":        "magazine-asymmetric",
    "section_anatomies":       {
        "hero":         "Full-bleed hero photo, headline overlay top-left, CTA below",
        "features":     "3 column icon grid, headline + 2 sentences per",
        "gallery":      "12-col asymmetric grid",
        "testimonials": "Glass cards on tinted backdrop, portrait + quote",
        "cta":          "Centered headline + button on accent background",
    },
}


async def head_ok(url: str, client: httpx.AsyncClient) -> int:
    """Return HTTP status of a HEAD request, or -1 on connection error."""
    try:
        r = await client.head(url, follow_redirects=True, timeout=10.0)
        return r.status_code
    except Exception:
        # Some CDNs reject HEAD; fall back to GET with stream + close
        try:
            async with client.stream("GET", url, follow_redirects=True, timeout=10.0) as r:
                return r.status_code
        except Exception:
            return -1


async def main():
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not anthropic_key:
        print("✗ ANTHROPIC_API_KEY missing")
        return 1

    print("=" * 72)
    print("LIVE IMAGE BINDING VERIFICATION — 1 page, real Claude + real Unsplash")
    print("=" * 72)

    # ── Stage 5.5: bind images ────────────────────────────────────
    clear_image_cache()
    page_images = await bind_page_images(
        page_plan=PAGE_PLAN,
        industry="italian cafe",
        purpose_data={"primary_purpose": "brand_awareness", "industry": "italian cafe"},
        visual_dna=VISUAL_DNA,
    )
    print(f"\n── Stage 5.5 result ──")
    for key, imgs in page_images.items():
        print(f"  {key}: {len(imgs)} image(s)")
        for img in imgs[:2]:
            print(f"     · {img['url'][:90]}…")

    # ── Stage 6: live Claude page generation ──────────────────────
    print(f"\n── Stage 6: live Claude page generation ──")
    files = await generate_one_page(
        page=PAGE_PLAN,
        visual_dna=VISUAL_DNA,
        brand_name="Caffè Verona",
        tagline="Florence's coffee since 1923",
        domain="food_and_beverage",
        api_key=anthropic_key,
        page_images=page_images,
    )
    if not files:
        print("✗ Claude returned no files")
        return 1
    print(f"  Generated {len(files)} files")

    # ── Audit: extract all <img> from JSX ─────────────────────────
    print(f"\n── Audit: <img> tags in generated JSX ──")
    img_tags: list[tuple[str, str, str]] = []  # (file, src, alt)
    per_file_count: Counter = Counter()
    for f in files:
        path = f["path"]
        if not path.endswith((".jsx", ".tsx", ".js")):
            continue
        content = f["content"]
        for m in _IMG_RX.finditer(content):
            attrs = m.group(1) or ""
            src_m = _SRC_RX.search(attrs)
            alt_m = _ALT_RX.search(attrs)
            src = (src_m.group(1) or src_m.group(2)) if src_m else ""
            alt = (alt_m.group(1) or alt_m.group(2)) if alt_m else ""
            img_tags.append((path, src.strip(), alt.strip()))
            per_file_count[path] += 1

    print(f"  Total <img> tags found: {len(img_tags)}")
    for path, count in per_file_count.most_common():
        print(f"     {count}× — {path}")

    # ── Classify URLs ─────────────────────────────────────────────
    real_unsplash = [t for t in img_tags if t[1].startswith("https://images.unsplash.com")]
    local_paths = [t for t in img_tags if t[1].startswith(("/images/", "/assets/", "/img/"))]
    empty = [t for t in img_tags if not t[1]]
    other = [t for t in img_tags if t not in real_unsplash and t not in local_paths and t not in empty]

    print(f"\n── URL classification ──")
    print(f"  real Unsplash URLs : {len(real_unsplash)}")
    print(f"  local /images/ etc : {len(local_paths)}")
    print(f"  empty src          : {len(empty)}")
    print(f"  other              : {len(other)}")
    for tag in local_paths[:5]:
        print(f"     ✗ LOCAL: {tag[0]}  src={tag[1]!r}")
    for tag in other[:5]:
        print(f"     ? OTHER: {tag[0]}  src={tag[1]!r}")

    # ── Alt text quality ──────────────────────────────────────────
    missing_alt = [t for t in img_tags if not t[2] or t[2].lower() in ("image", "photo", "img")]
    print(f"\n── Alt text ──")
    print(f"  with meaningful alt : {len(img_tags) - len(missing_alt)}")
    print(f"  missing / trivial   : {len(missing_alt)}")
    for tag in missing_alt[:5]:
        print(f"     ✗ {tag[0]}  alt={tag[2]!r}")

    # ── HEAD-check unique real Unsplash URLs ──────────────────────
    unique_urls = list({t[1] for t in real_unsplash})
    print(f"\n── HEAD-checking {len(unique_urls)} unique Unsplash URL(s) ──")
    not_found = []
    async with httpx.AsyncClient() as client:
        statuses = await asyncio.gather(*[head_ok(u, client) for u in unique_urls])
    for url, status in zip(unique_urls, statuses):
        marker = "✓" if 200 <= status < 400 else "✗"
        print(f"  {marker} {status}  {url[:100]}")
        if not (200 <= status < 400):
            not_found.append((url, status))

    # ── Final verdict ─────────────────────────────────────────────
    print(f"\n{'═' * 72}")
    print(f"FINAL VERDICT")
    print(f"{'═' * 72}")
    print(f"  Total <img> tags    : {len(img_tags)}")
    print(f"  Real Unsplash URLs  : {len(real_unsplash)}")
    print(f"  Local /images paths : {len(local_paths)}  (target: 0)")
    print(f"  Missing/trivial alt : {len(missing_alt)}  (target: 0)")
    print(f"  HTTP 404s on URLs   : {len(not_found)}  (target: 0)")

    ok = (
        len(local_paths) == 0
        and len(other) == 0
        and len(not_found) == 0
        and len(img_tags) > 0
    )
    print(f"\n  {'✓ PASS' if ok else '✗ FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
