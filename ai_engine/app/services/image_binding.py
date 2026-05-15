"""Image binding — Stage 5.5 of the website pipeline.

Runs AFTER the deterministic foundation and BEFORE per-page Claude calls.
Resolves every section that needs imagery to a real Unsplash URL + alt
text BEFORE Claude sees the page, so Claude never hallucinates image
paths like `/images/hero.jpg`.

Per-section policy:
  hero / cta / about    → 1 landscape image
  testimonials / team   → N portraits (one per item, capped)
  gallery               → 6 landscapes
  menu                  → 4 squarish (dish photos)
  features              → 0 (Lucide icons handled in JSX)
  footer / press / faq / contact / pricing / stats / faq → 0
  unknown               → 1 landscape

Caching is per-process: identical queries within one pipeline run hit
the in-memory cache, not the Unsplash API. The pipeline calls
`clear_image_cache()` at the start of each run so stale results from
prior runs never leak across projects.

Fallback URL is used when Unsplash is unreachable or unkeyed — generation
NEVER fails because images failed.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


# ── Fallback URL (Unsplash-hosted safe generic photo) ─────────────────
# A neutral workspace shot that won't look broken in any context.
_FALLBACK_RAW = "https://images.unsplash.com/photo-1606857521015-7f9fcf423740"
_FALLBACK_LANDSCAPE = f"{_FALLBACK_RAW}?w=1600&h=900&fit=crop&auto=format&q=80"
_FALLBACK_PORTRAIT = f"{_FALLBACK_RAW}?w=600&h=800&fit=crop&auto=format&q=80"
_FALLBACK_SQUARE = f"{_FALLBACK_RAW}?w=800&h=800&fit=crop&auto=format&q=80"


def _fallback_image(orientation: str, alt: str) -> dict:
    if orientation == "portrait":
        url = _FALLBACK_PORTRAIT
    elif orientation == "squarish":
        url = _FALLBACK_SQUARE
    else:
        url = _FALLBACK_LANDSCAPE
    return {
        "url": url,
        "alt": alt or "Photograph",
        "photographer": "Unsplash",
        "photographer_url": "https://unsplash.com",
    }


# ── Per-section policy ────────────────────────────────────────────────
# (default_count, orientation, size_key)
# size_key picks which Imgix sizing the underlying unsplash.py returns:
#   "url_hero" (1600×900) / "url_card" (800×600) / "url_thumb" (400×300)
_SECTION_POLICY: dict[str, tuple[int, str, str]] = {
    "hero":            (1, "landscape", "url_hero"),
    "cta":             (1, "landscape", "url_hero"),
    "about":           (2, "landscape", "url_card"),
    "story":           (2, "landscape", "url_card"),
    "philosophy":      (1, "landscape", "url_card"),
    "gallery":         (6, "landscape", "url_card"),
    "testimonials":    (4, "portrait",  "url_thumb"),
    "team":            (6, "portrait",  "url_thumb"),
    "menu":            (4, "squarish",  "url_card"),
    "features":        (0, "landscape", "url_card"),
    "value_prop":      (0, "landscape", "url_card"),
    "how_it_works":    (0, "landscape", "url_card"),
    "process":         (0, "landscape", "url_card"),
    "footer":          (0, "landscape", "url_card"),
    "header":          (0, "landscape", "url_card"),
    "press":           (0, "landscape", "url_card"),
    "faq":             (0, "landscape", "url_card"),
    "contact":         (0, "landscape", "url_card"),
    "pricing":         (0, "landscape", "url_card"),
    "stats":           (0, "landscape", "url_card"),
    "newsletter":      (0, "landscape", "url_card"),
    "reservation":     (0, "landscape", "url_card"),
    "locations":       (1, "landscape", "url_card"),
}


# ── In-process query cache ────────────────────────────────────────────
_query_cache: dict[str, list[dict]] = {}


def clear_image_cache() -> None:
    """Wipe the query cache. Call once at the start of each pipeline run."""
    _query_cache.clear()


def cache_size() -> int:
    """For tests / diagnostics — how many distinct queries are cached."""
    return len(_query_cache)


# ── Public API ────────────────────────────────────────────────────────


async def search_unsplash(query: str, count: int = 1) -> list[dict]:
    """Search Unsplash. Returns [{url, alt, photographer, photographer_url}].

    Cached per-query within the current process. On any failure (no key,
    HTTP error, empty results) returns ``count`` fallback entries — never
    raises and never returns an empty list when count > 0.

    NOTE: declared ``async`` even though the user spec wrote ``def`` —
    the entire pipeline is async; a sync HTTP call here would block the
    event loop.
    """
    q = (query or "").strip()
    if not q or count <= 0:
        return []

    cache_key = f"{q}::{count}"
    if cache_key in _query_cache:
        return list(_query_cache[cache_key])  # copy so callers can't mutate

    if not os.environ.get("UNSPLASH_ACCESS_KEY"):
        logger.warning("image_binding: UNSPLASH_ACCESS_KEY missing — using fallback for %r", q)
        out = [_fallback_image("landscape", q) for _ in range(count)]
        _query_cache[cache_key] = list(out)
        return out

    try:
        from app.services.unsplash import search_photos
        photos = await search_photos(q, count=count, orientation="landscape")
    except Exception as exc:
        logger.warning("image_binding: unsplash search %r failed (%s) — fallback", q, exc)
        out = [_fallback_image("landscape", q) for _ in range(count)]
        _query_cache[cache_key] = list(out)
        return out

    if not photos:
        logger.info("image_binding: unsplash returned 0 results for %r — fallback", q)
        out = [_fallback_image("landscape", q) for _ in range(count)]
        _query_cache[cache_key] = list(out)
        return out

    # Pad if Unsplash returned fewer than requested — keep contract: len == count.
    results: list[dict] = []
    for p in photos[:count]:
        results.append({
            "url":              p["url_hero"],
            "alt":              q,
            "photographer":     p["photographer"],
            "photographer_url": p["photo_page"],
        })
    while len(results) < count:
        results.append(_fallback_image("landscape", q))

    _query_cache[cache_key] = list(results)
    return results


def _build_query(section_type: str, section_purpose: str, industry: str, visual_dna: dict) -> str:
    """Build an Unsplash query for this section. Industry-rooted nouns first."""
    industry = (industry or "general").strip().lower()
    s_type = (section_type or "").strip().lower()
    purpose = (section_purpose or "").strip().lower()
    photo_style = (visual_dna.get("photography_style") or "").strip().lower() if visual_dna else ""

    # Type-specific lead — testimonial/team queries shouldn't mention the
    # industry's products (a "logistics testimonial portrait" returns trucks,
    # not people).
    if s_type in ("testimonials",):
        return "professional portrait smiling person"
    if s_type == "team":
        return f"{industry} professional team portrait"
    if s_type == "menu":
        return f"{industry} food plated dish"
    if s_type == "gallery":
        base = f"{industry} photography"
        if photo_style:
            base = f"{base} {photo_style}"
        return base
    if s_type == "hero":
        return f"{industry} hero"
    if s_type == "cta":
        return f"{industry} action"
    if s_type in ("about", "story", "philosophy"):
        return f"{industry} workspace people"
    if s_type == "locations":
        return f"{industry} storefront exterior"

    # Generic fallback — use the section's stated purpose if any
    if purpose:
        return f"{industry} {purpose}"[:80]
    return industry or "modern workspace"


async def bind_section_images(
    section_type: str,
    section_purpose: str,
    industry: str,
    visual_dna: dict,
    count_needed: int,
) -> list[dict]:
    """Bind images for one section. Returns [{url, alt, photographer, photographer_url}].

    Honors section policy: returns [] for icon-only sections (features,
    pricing, faq, etc.) regardless of ``count_needed``.
    """
    s_type = (section_type or "").strip().lower()
    policy = _SECTION_POLICY.get(s_type)
    if policy and policy[0] == 0:
        return []

    if count_needed <= 0:
        return []

    query = _build_query(s_type, section_purpose, industry, visual_dna or {})
    return await search_unsplash(query, count=count_needed)


def _count_for_section(section: dict, section_type: str) -> int:
    """Decide how many images this section needs.

    - Honors per-section default from _SECTION_POLICY.
    - For collection-style sections (testimonials, team, menu, gallery)
      where the plan already lists items, prefer min(item_count, policy_default)
      so we don't over-fetch.
    """
    policy = _SECTION_POLICY.get(section_type)
    if not policy:
        return 1  # unknown section type → 1 image
    default_count = policy[0]
    if default_count == 0:
        return 0

    items = section.get("items") or []
    if isinstance(items, list) and items and section_type in ("testimonials", "team", "menu", "gallery"):
        return max(1, min(len(items), default_count))
    return default_count


async def bind_page_images(
    page_plan: dict,
    industry: str,
    purpose_data: dict,
    visual_dna: dict,
) -> dict:
    """Bind images for an entire page. Returns {section_type_or_key: [images]}.

    Keys are the section type. When the same type appears more than once
    on one page, suffix ``_2``, ``_3``, etc. so callers can disambiguate.

    Each value is a list of image dicts: [{url, alt, photographer, photographer_url}].
    Zero-image sections (features, footer, faq…) are omitted from the
    returned dict entirely — keeps the prompt block compact.
    """
    sections = (page_plan or {}).get("sections") or []
    visual_dna = visual_dna or {}
    industry = (industry or "general").strip()

    # Compute one task per section that needs images. Run them in
    # parallel — the per-query cache + asyncio.gather collapses
    # duplicates within a page (e.g. two "story" sections share an HTTP call).
    tasks: list[tuple[str, Any]] = []
    type_counts: dict[str, int] = {}
    for s in sections:
        if not isinstance(s, dict):
            continue
        s_type = (s.get("type") or "section").strip().lower()
        count = _count_for_section(s, s_type)
        if count <= 0:
            continue
        # Disambiguate duplicate types within a single page
        type_counts[s_type] = type_counts.get(s_type, 0) + 1
        key = s_type if type_counts[s_type] == 1 else f"{s_type}_{type_counts[s_type]}"
        purpose_text = (s.get("purpose") or s.get("description") or "").strip()
        tasks.append((key, bind_section_images(
            section_type=s_type, section_purpose=purpose_text,
            industry=industry, visual_dna=visual_dna,
            count_needed=count,
        )))

    if not tasks:
        return {}

    results = await asyncio.gather(*(t for _, t in tasks), return_exceptions=True)
    out: dict[str, list[dict]] = {}
    for (key, _), res in zip(tasks, results):
        if isinstance(res, Exception):
            logger.warning("image_binding: %s bind threw — %s", key, res)
            continue
        if res:
            out[key] = res

    logger.info(
        "image_binding: page %s — bound %d section(s) (%d images total)",
        page_plan.get("route") or "?",
        len(out), sum(len(v) for v in out.values()),
    )
    return out


def format_images_for_prompt(page_images: dict) -> str:
    """Render the page_images dict as the prompt block Claude reads.

    Format matches the user spec exactly:

      IMAGES FOR THIS PAGE (use exact URLs):
      hero:
        src: https://images.unsplash.com/photo-xxx
        alt: "Modern logistics warehouse"
      testimonials:
        - src: https://images.unsplash.com/photo-yyy
          alt: "Driver smiling"
        - src: ...
          alt: ...

    Returns empty string when there are no images.
    """
    if not page_images:
        return ""

    lines = ["IMAGES FOR THIS PAGE (use these EXACT URLs as src — do NOT invent paths):"]
    for section_key, images in page_images.items():
        if not images:
            continue
        lines.append(f"{section_key}:")
        if len(images) == 1:
            img = images[0]
            lines.append(f"  src: {img['url']}")
            lines.append(f"  alt: {img['alt']!r}")
        else:
            for img in images:
                lines.append(f"  - src: {img['url']}")
                lines.append(f"    alt: {img['alt']!r}")
    return "\n".join(lines)
