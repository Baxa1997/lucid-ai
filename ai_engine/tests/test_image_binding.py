"""Tests for image_binding — Stage 5.5 of the website pipeline.

Cases:
  1. Unsplash search returns valid URLs (live network, skipped if no key)
  2. Fallback works when UNSPLASH_ACCESS_KEY is missing
  3. Hero section gets exactly 1 image
  4. Gallery section gets 4-8 images
  5. Cache: same query within single project does not re-search Unsplash

Run inside the ai_engine container:
    docker exec -it lucid-ai-ai_engine-1 python /app/tests/test_image_binding.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import traceback
from urllib.parse import urlparse

sys.path.insert(0, "/app")

from app.services.image_binding import (
    bind_section_images,
    bind_page_images,
    cache_size,
    clear_image_cache,
    search_unsplash,
)


def _valid_unsplash_url(url: str) -> bool:
    """Loose check: must parse, scheme=https, host on unsplash domain."""
    if not isinstance(url, str) or not url:
        return False
    try:
        p = urlparse(url)
    except Exception:
        return False
    return (
        p.scheme == "https"
        and ("unsplash.com" in p.netloc)
        and bool(p.path)
    )


async def test_search_unsplash_returns_valid_urls() -> bool:
    """Live Unsplash search returns http(s) URLs with alt + photographer."""
    clear_image_cache()
    if not os.environ.get("UNSPLASH_ACCESS_KEY"):
        print("  ⊘ skipped — no UNSPLASH_ACCESS_KEY")
        return True  # not a failure; gate not available

    results = await search_unsplash("logistics warehouse", count=3)
    if len(results) != 3:
        print(f"  ✗ expected 3 results, got {len(results)}")
        return False
    for r in results:
        if not _valid_unsplash_url(r["url"]):
            print(f"  ✗ invalid url: {r['url']!r}")
            return False
        if not r.get("alt"):
            print(f"  ✗ empty alt: {r}")
            return False
        if not r.get("photographer"):
            print(f"  ✗ missing photographer: {r}")
            return False
    print(f"  ✓ live search returned 3 valid urls — sample: {results[0]['url'][:80]}…")
    return True


async def test_fallback_when_key_missing() -> bool:
    """Yank UNSPLASH_ACCESS_KEY → fallback URLs returned (no exception)."""
    clear_image_cache()
    saved = os.environ.pop("UNSPLASH_ACCESS_KEY", None)
    try:
        results = await search_unsplash("anything at all", count=2)
        if len(results) != 2:
            print(f"  ✗ expected 2 fallback results, got {len(results)}")
            return False
        for r in results:
            if not _valid_unsplash_url(r["url"]):
                print(f"  ✗ fallback url not valid: {r['url']!r}")
                return False
        print(f"  ✓ fallback returned 2 valid urls — sample: {results[0]['url'][:80]}…")
        return True
    finally:
        if saved is not None:
            os.environ["UNSPLASH_ACCESS_KEY"] = saved


async def test_hero_gets_one_image() -> bool:
    """bind_section_images(hero, ...) → exactly 1 image."""
    clear_image_cache()
    results = await bind_section_images(
        section_type="hero",
        section_purpose="open with seasonal headline",
        industry="italian restaurant",
        visual_dna={},
        count_needed=1,
    )
    if len(results) != 1:
        print(f"  ✗ expected 1 hero image, got {len(results)}")
        return False
    if not _valid_unsplash_url(results[0]["url"]):
        print(f"  ✗ invalid hero url: {results[0]['url']!r}")
        return False
    print(f"  ✓ hero got 1 image — {results[0]['url'][:80]}…")
    return True


async def test_gallery_gets_multiple() -> bool:
    """bind_section_images(gallery, ..., count_needed=6) → 4-8 images."""
    clear_image_cache()
    results = await bind_section_images(
        section_type="gallery",
        section_purpose="seasonal gallery",
        industry="italian restaurant",
        visual_dna={"photography_style": "warm rustic"},
        count_needed=6,
    )
    if not (4 <= len(results) <= 8):
        print(f"  ✗ expected 4-8 gallery images, got {len(results)}")
        return False
    for r in results:
        if not _valid_unsplash_url(r["url"]):
            print(f"  ✗ invalid gallery url: {r['url']!r}")
            return False
    print(f"  ✓ gallery got {len(results)} images")
    return True


async def test_features_gets_zero_images() -> bool:
    """features sections use Lucide icons — image_binding must return []."""
    clear_image_cache()
    results = await bind_section_images(
        section_type="features",
        section_purpose="three core features",
        industry="saas",
        visual_dna={},
        count_needed=3,  # honored only when policy permits
    )
    if results != []:
        print(f"  ✗ features should return [], got {len(results)}")
        return False
    print(f"  ✓ features correctly skipped (icons handled by Claude)")
    return True


async def test_cache_dedupes_within_project() -> bool:
    """Same query within a single project run is served from cache.

    Verified by counting underlying ``search_photos`` calls via monkeypatch:
    the first request hits Unsplash, subsequent identical requests don't.
    """
    clear_image_cache()
    import app.services.unsplash as up_mod

    original = up_mod.search_photos
    call_count = 0

    async def counting_search_photos(query, *, count=5, orientation="landscape"):
        nonlocal call_count
        call_count += 1
        # Return a minimal valid shape so search_unsplash maps it correctly
        return [{
            "id": f"id_{i}",
            "url_hero":  "https://images.unsplash.com/photo-1606857521015-7f9fcf423740?w=1600&h=900",
            "url_card":  "https://images.unsplash.com/photo-1606857521015-7f9fcf423740?w=800&h=600",
            "url_thumb": "https://images.unsplash.com/photo-1606857521015-7f9fcf423740?w=400&h=300",
            "photographer": "Test",
            "photo_page":   "https://unsplash.com/photos/test",
        } for i in range(count)]

    # Ensure UNSPLASH_ACCESS_KEY is set so we exercise the live path, not fallback.
    saved_key = os.environ.get("UNSPLASH_ACCESS_KEY")
    os.environ["UNSPLASH_ACCESS_KEY"] = saved_key or "test-key"
    up_mod.search_photos = counting_search_photos

    try:
        r1 = await search_unsplash("the same query", count=2)
        r2 = await search_unsplash("the same query", count=2)
        r3 = await search_unsplash("the same query", count=2)
        if call_count != 1:
            print(f"  ✗ expected 1 underlying search call, got {call_count}")
            return False
        if r1[0]["url"] != r2[0]["url"] != r3[0]["url"]:
            print(f"  ✗ cached results didn't match across calls")
            return False
        print(f"  ✓ cache: 3 calls to search_unsplash → 1 actual Unsplash request (cache_size={cache_size()})")
        return True
    finally:
        up_mod.search_photos = original
        if saved_key is None:
            os.environ.pop("UNSPLASH_ACCESS_KEY", None)


async def test_bind_page_images_shape() -> bool:
    """End-to-end: bind_page_images returns dict keyed by section type
    with image lists, skipping zero-image sections."""
    clear_image_cache()
    page_plan = {
        "route": "/",
        "title": "Home",
        "sections": [
            {"type": "hero",         "purpose": "headline"},
            {"type": "features",     "purpose": "three pillars"},  # skipped
            {"type": "gallery",      "purpose": "samples"},
            {"type": "testimonials", "purpose": "happy customers",
             "items": [{"title": "Anna"}, {"title": "Bob"}]},
            {"type": "footer",       "purpose": "links"},          # skipped
        ],
    }
    out = await bind_page_images(
        page_plan=page_plan,
        industry="italian restaurant",
        purpose_data={"primary_purpose": "brand_awareness"},
        visual_dna={"photography_style": "warm rustic"},
    )
    if "features" in out or "footer" in out:
        print(f"  ✗ zero-image sections should be omitted; got keys={list(out.keys())}")
        return False
    if "hero" not in out or len(out["hero"]) != 1:
        print(f"  ✗ hero missing or wrong count: {out.get('hero')}")
        return False
    if "gallery" not in out or not (4 <= len(out["gallery"]) <= 8):
        print(f"  ✗ gallery count off: {len(out.get('gallery') or [])}")
        return False
    if "testimonials" not in out or len(out["testimonials"]) < 1:
        print(f"  ✗ testimonials missing: {out.get('testimonials')}")
        return False
    print(f"  ✓ bind_page_images keys={list(out.keys())}, counts={ {k: len(v) for k, v in out.items()} }")
    return True


async def main():
    print("=" * 72)
    print("IMAGE BINDING — TESTS")
    print("=" * 72)

    tests = [
        ("search_unsplash returns valid urls",   test_search_unsplash_returns_valid_urls),
        ("fallback when key missing",            test_fallback_when_key_missing),
        ("hero gets 1 image",                    test_hero_gets_one_image),
        ("gallery gets 4-8 images",              test_gallery_gets_multiple),
        ("features gets 0 images (icons)",       test_features_gets_zero_images),
        ("cache dedupes within project",         test_cache_dedupes_within_project),
        ("bind_page_images shape",               test_bind_page_images_shape),
    ]

    passed = 0
    for name, fn in tests:
        print(f"\n── {name} ──")
        try:
            ok = await fn()
            if ok:
                passed += 1
            else:
                print(f"  ✗ FAILED")
        except Exception as exc:
            print(f"  ✗ THREW: {exc}")
            traceback.print_exc()

    print("\n" + "=" * 72)
    print(f"SCORE: {passed}/{len(tests)}")
    print("=" * 72)
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
