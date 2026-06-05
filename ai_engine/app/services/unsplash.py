"""Unsplash API integration for fetching contextual images.

Used to replace hardcoded _UNSPLASH_POOLS in project_generator.py with
live searches matched to the project's actual content keywords.
"""

import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger("lucid.unsplash")

_BASE_URL = "https://api.unsplash.com"
_TIMEOUT = 10.0


def _access_key() -> str:
    key = os.environ.get("UNSPLASH_ACCESS_KEY", "")
    if not key:
        raise RuntimeError("UNSPLASH_ACCESS_KEY env var is not set")
    return key


def _imgix(raw_url: str, *, w: int, h: int, q: int = 80) -> str:
    """Append Imgix sizing params to an Unsplash raw URL.

    The raw URL may already have query params (ixid, ixlib), so we join
    with & when a ? is already present.
    """
    sep = "&" if "?" in raw_url else "?"
    return f"{raw_url}{sep}w={w}&h={h}&fit=crop&auto=format&q={q}"


async def search_photos(
    query: str,
    *,
    count: int = 5,
    orientation: str = "landscape",  # landscape | portrait | squarish
) -> list[dict]:
    """Search Unsplash and return a list of photo dicts.

    Each dict contains:
      - url_hero:       1600×900  (hero / full-bleed)
      - url_card:       800×600   (content cards)
      - url_thumb:      400×300   (small thumbnails)
      - photographer:   display name
      - photo_page:     Unsplash attribution link (required by API guidelines)
      - id:             Unsplash photo ID
    """
    params = {
        "query": query,
        "per_page": min(count, 30),
        "orientation": orientation,
        "content_filter": "high",
    }
    headers = {"Authorization": f"Client-ID {_access_key()}"}

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        response = await client.get(
            f"{_BASE_URL}/search/photos",
            params=params,
            headers=headers,
        )
        response.raise_for_status()
        data = response.json()

    results = []
    for photo in data.get("results", []):
        raw = photo["urls"]["raw"]
        # Tags + alt_description let the binder soft-filter results whose
        # metadata clashes with the brief's geography (e.g. a "smash burger"
        # query landing on a photo of an Estonian café front).
        tags = [
            (t.get("title") or "").lower().strip()
            for t in (photo.get("tags") or [])
            if isinstance(t, dict)
        ]
        location = (photo.get("location") or {}) if isinstance(photo.get("location"), dict) else {}
        results.append(
            {
                "id": photo["id"],
                "url_hero": _imgix(raw, w=1600, h=900),
                "url_card": _imgix(raw, w=800, h=600),
                "url_thumb": _imgix(raw, w=400, h=300),
                "photographer": photo["user"]["name"],
                "photo_page": photo["links"]["html"],
                "alt_description": (photo.get("alt_description") or "").lower(),
                "description": (photo.get("description") or "").lower(),
                "tags": [t for t in tags if t],
                "location_country": (location.get("country") or "").lower(),
                "location_city": (location.get("city") or "").lower(),
            }
        )

    logger.info("unsplash search '%s' → %d results", query, len(results))
    return results


async def fetch_project_images(
    keywords: list[str],
    *,
    hero_count: int = 1,
    supporting_count: int = 4,
) -> dict:
    """Fetch hero + supporting images for a generated project.

    Args:
        keywords:          Search terms extracted from the project description
                           (e.g. ["coffee shop", "latte art", "cozy interior"])
        hero_count:        How many hero candidates to return
        supporting_count:  How many supporting/card image URLs to return

    Returns a dict with:
        hero_urls:       list of url_hero strings (length ≤ hero_count)
        supporting_urls: list of url_card strings (length ≤ supporting_count)
        photos:          raw photo dicts for attribution if needed
    """
    if not keywords:
        raise ValueError("At least one keyword is required")

    # Primary search uses all keywords joined; fallback uses the first keyword alone
    primary_query = " ".join(keywords[:3])
    needed = hero_count + supporting_count

    photos = await search_photos(primary_query, count=needed)

    if len(photos) < needed and len(keywords) > 1:
        fallback = await search_photos(keywords[0], count=needed - len(photos))
        photos.extend(fallback)

    hero_urls = [p["url_hero"] for p in photos[:hero_count]]
    supporting_urls = [p["url_card"] for p in photos[hero_count: hero_count + supporting_count]]

    return {
        "hero_urls": hero_urls,
        "supporting_urls": supporting_urls,
        "photos": photos,
    }
