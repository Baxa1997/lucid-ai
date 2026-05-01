#!/usr/bin/env python3
"""
Self-contained Unsplash API test — no app dependencies needed.

Usage:
    cd ai_engine
    UNSPLASH_ACCESS_KEY="-WP2N6m1n1Tx0yml3LnLzks_Cgdd3Jh-a7i2vHRnyQ8" python3 scripts/test_unsplash.py

Or run inline:
    UNSPLASH_ACCESS_KEY="..." python3 -c "
        import subprocess, sys
        subprocess.run([sys.executable, 'scripts/test_unsplash.py'])
    "
"""

import asyncio
import os
import sys

# Install httpx if missing
try:
    import httpx
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "httpx", "--break-system-packages", "-q"])
    import httpx


BASE_URL = "https://api.unsplash.com"
ACCESS_KEY = os.environ.get("UNSPLASH_ACCESS_KEY", "")


def imgix(raw_url: str, *, w: int, h: int, q: int = 80) -> str:
    sep = "&" if "?" in raw_url else "?"
    return f"{raw_url}{sep}w={w}&h={h}&fit=crop&auto=format&q={q}"


async def search_photos(query: str, count: int = 3, orientation: str = "landscape") -> list[dict]:
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(
            f"{BASE_URL}/search/photos",
            params={"query": query, "per_page": count, "orientation": orientation, "content_filter": "high"},
            headers={"Authorization": f"Client-ID {ACCESS_KEY}"},
        )
        r.raise_for_status()
        data = r.json()

    results = []
    for photo in data.get("results", []):
        raw = photo["urls"]["raw"]
        results.append({
            "id": photo["id"],
            "url_hero": imgix(raw, w=1600, h=900),
            "url_card": imgix(raw, w=800, h=600),
            "url_thumb": imgix(raw, w=400, h=300),
            "photographer": photo["user"]["name"],
            "photo_page": photo["links"]["html"],
        })
    return results


async def fetch_project_images(keywords: list[str], hero_count=1, supporting_count=4) -> dict:
    primary = " ".join(keywords[:3])
    needed = hero_count + supporting_count
    photos = await search_photos(primary, count=needed)
    if len(photos) < needed and len(keywords) > 1:
        extra = await search_photos(keywords[0], count=needed - len(photos))
        photos.extend(extra)
    return {
        "hero_urls": [p["url_hero"] for p in photos[:hero_count]],
        "supporting_urls": [p["url_card"] for p in photos[hero_count: hero_count + supporting_count]],
        "photos": photos,
    }


async def main():
    if not ACCESS_KEY:
        print("ERROR: set UNSPLASH_ACCESS_KEY env var")
        sys.exit(1)

    print(f"Access key: {ACCESS_KEY[:8]}...\n")

    # ── Test 1: basic search ──────────────────────────────────
    print("── search_photos('coffee shop', count=3) ──")
    photos = await search_photos("coffee shop", count=3)
    for p in photos:
        print(f"  [{p['id']}] {p['photographer']}")
        print(f"    hero:  {p['url_hero']}")
        print(f"    card:  {p['url_card']}")
        print(f"    attr:  {p['photo_page']}")

    # ── Test 2: project images (coffee shop landing page) ─────
    print("\n── fetch_project_images — coffee shop landing page ──")
    result = await fetch_project_images(
        keywords=["coffee shop", "latte art", "cozy cafe interior"],
        hero_count=1,
        supporting_count=4,
    )
    print("Hero:", result["hero_urls"])
    print("Supporting:")
    for url in result["supporting_urls"]:
        print(f"  {url}")

    # ── Test 3: gym/fitness ───────────────────────────────────
    print("\n── fetch_project_images — gym / fitness app ──")
    result = await fetch_project_images(
        keywords=["gym workout", "fitness weights"],
        hero_count=1,
        supporting_count=3,
    )
    print("Hero:", result["hero_urls"])
    print("Supporting:", result["supporting_urls"])

    print("\nAll tests passed.")


asyncio.run(main())
