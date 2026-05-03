"""image_binder — post-generation per-product image rebinding.

Phase 2 fetches a generic pool of Unsplash photos via category keywords
("luxury car", "espresso") and lets Claude scatter them across product cards.
Claude has no way to know that the URL it bound to a "Bugatti Chiron" card
is actually a photo of a house from the same pool — the URLs are opaque.

This module runs AFTER all phases have written files. It walks every JSX/TSX
file, finds `<img alt="…">` tags whose alt text looks like a *named entity*
(proper-noun, multi-word, not a generic word like "Hero" or "Logo"), and
swaps the src for a focused Unsplash search on that name + a domain qualifier.

Design choices:
  • **Fail-soft**: any error (missing key, search miss, write failure) is
    swallowed. The site still has whatever Claude originally bound.
  • **Cached**: each unique alt is searched at most once per run.
  • **Bounded concurrency**: parallel searches capped to avoid Unsplash rate
    limits (50 req/hour on the free demo tier; 5000/hour on production).
  • **Idempotent**: re-running on already-rebound files is safe (alt unchanged).
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Optional

logger = logging.getLogger("lucid.image_binder")


_JSX_GLOBS = (".jsx", ".tsx", ".js", ".ts")

# Generic alt strings that almost certainly aren't named entities. We skip
# these to keep the search budget for actual product/offering names.
_GENERIC_ALTS: frozenset[str] = frozenset({
    "hero", "logo", "background", "image", "photo", "picture", "icon",
    "banner", "thumbnail", "thumb", "avatar", "profile", "user", "team",
    "header", "footer", "card", "card image", "section image", "gallery image",
    "feature", "feature image", "illustration", "graphic", "decoration",
    "placeholder", "default", "preview", "screenshot", "mockup",
    "video thumbnail", "play button", "arrow", "chevron",
})

# `<img ... alt="X" ... src="Y" ... />` — same brace/quote-aware tokenizer
# we use in project_writer, but here we extract attrs instead of injecting.
_TAG_OPEN_RE = re.compile(r"<img\b", re.IGNORECASE)
_ALT_ATTR_RE = re.compile(r'alt\s*=\s*"([^"]*)"', re.IGNORECASE)
_SRC_ATTR_RE = re.compile(r'src\s*=\s*"([^"]*)"', re.IGNORECASE)


def _is_named_entity(alt: str) -> bool:
    """Heuristic: does this alt text look like a specific product / offering name?

    Yes if: ≥2 words, ≥2 of those words are capitalized, not in generic set.
    No if: empty, single word, all lowercase, or matches a generic label.
    """
    text = (alt or "").strip()
    if not text or len(text) < 4:
        return False
    if text.lower() in _GENERIC_ALTS:
        return False
    words = text.split()
    if len(words) < 2:
        # Single-word alts are rarely product names — usually "Hero" or "Logo"
        return False
    # Count "specific" words: capitalized first letter OR contains a digit.
    # The digit clause catches model numbers — "720S", "F8", "iX", "RX-7" —
    # which are strong signals of a named SKU even though their first char
    # isn't a capital letter.
    specific_words = sum(
        1 for w in words
        if (w[:1].isupper() and w[:1].isalpha()) or any(ch.isdigit() for ch in w)
    )
    if specific_words < 2:
        return False
    # Skip phrases that contain only generic words even when title-cased
    non_generic = [w for w in words if w.lower() not in _GENERIC_ALTS]
    if len(non_generic) < 2:
        return False
    return True


def _scan_file_for_named_imgs(content: str) -> list[tuple[int, int, str, str]]:
    """Walk content and yield (start, end, alt, src) for each <img> with a
    named-entity alt attribute. start/end span the full tag (including `<` `>`).
    """
    out: list[tuple[int, int, str, str]] = []
    i, n = 0, len(content)
    while True:
        m = _TAG_OPEN_RE.search(content, i)
        if not m:
            break
        start = m.start()
        # Walk to end-of-tag with quote/brace tracking
        j = m.end()
        depth = 0
        in_str: Optional[str] = None
        while j < n:
            c = content[j]
            if in_str:
                if c == "\\" and j + 1 < n:
                    j += 2
                    continue
                if c == in_str:
                    in_str = None
                j += 1
                continue
            if c in ("'", '"', "`"):
                in_str = c
                j += 1
                continue
            if c == "{":
                depth += 1
                j += 1
                continue
            if c == "}":
                depth -= 1
                j += 1
                continue
            if c == ">" and depth <= 0:
                break
            j += 1
        if j >= n:
            break
        tag = content[start:j + 1]
        alt_m = _ALT_ATTR_RE.search(tag)
        src_m = _SRC_ATTR_RE.search(tag)
        if alt_m and src_m and _is_named_entity(alt_m.group(1)):
            out.append((start, j + 1, alt_m.group(1).strip(), src_m.group(1)))
        i = j + 1
    return out


def _walk_jsx_files(workspace_path: str) -> list[str]:
    """Return all .jsx/.tsx/.js/.ts file paths under workspace_path/src."""
    src_root = os.path.join(workspace_path, "src")
    if not os.path.isdir(src_root):
        # Some templates put files at root — fall back to whole workspace
        src_root = workspace_path
    found: list[str] = []
    for root, dirs, files in os.walk(src_root):
        # Skip vendored dirs
        dirs[:] = [d for d in dirs if d not in {"node_modules", ".next", "dist", ".git"}]
        for f in files:
            if f.endswith(_JSX_GLOBS):
                found.append(os.path.join(root, f))
    return found


def _atomic_write(path: str, content: str) -> None:
    tmp = path + ".lucid.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, path)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        raise


# ── Cross-project cache ──────────────────────────────────────────────────
# Unsplash demo-tier allows only 50 requests/hour. Each named-entity search
# costs 1 request, and projects often re-use the same names ("Bugatti
# Chiron" appears in every luxury-car generation). We cache (alt, qualifier)
# → url across calls so repeat names are free for the lifetime of the
# process. In-memory only — fine because the process restart cost is
# small and we don't want stale URLs hanging around forever.
_SEARCH_CACHE: dict[tuple[str, str], Optional[str]] = {}
_SEARCH_CACHE_MAX = 512


async def _search_one(alt: str, qualifier: str, sem: asyncio.Semaphore) -> Optional[str]:
    """Search Unsplash for a single named entity. Returns the best card-sized URL.

    Cached: a second call for the same (alt, qualifier) is free. Stores
    misses too so we don't re-query an alt Unsplash genuinely has nothing for.
    """
    cache_key = (alt.strip().lower(), qualifier.strip().lower())
    if cache_key in _SEARCH_CACHE:
        return _SEARCH_CACHE[cache_key]

    from app.services.unsplash import search_photos
    query = f"{alt} {qualifier}".strip() if qualifier else alt
    url: Optional[str] = None
    try:
        async with sem:
            photos = await search_photos(query, count=1)
        if photos:
            url = photos[0]["url_card"]
    except Exception as exc:
        logger.debug("image_binder: search %r failed: %s", query, exc)

    if len(_SEARCH_CACHE) < _SEARCH_CACHE_MAX:
        _SEARCH_CACHE[cache_key] = url
    return url


async def rebind_named_images(
    workspace_path: str,
    *,
    domain_qualifier: str = "",
    # Solo-testing mode: no cap. Every named entity gets its own search.
    # If/when we open to real users, drop this to ~8 to fit the Unsplash
    # demo-tier 50-req/hour budget — the in-process cache makes 8 cover
    # most landing pages. Failures past the rate limit are swallowed by
    # `_search_one`, so an over-budget run degrades gracefully.
    max_searches: int = 1000,
) -> int:
    """Walk generated JSX files, search Unsplash per named-entity alt, rewrite src.

    Args:
        workspace_path:    Generated project root.
        domain_qualifier:  e.g. "luxury car", "restaurant dish" — appended to
                           the alt text to bias search toward the right domain.
        max_searches:      Cap on unique Unsplash queries this run (rate-limit guard).

    Returns the number of <img> tags that had their src rewritten.
    """
    if not os.environ.get("UNSPLASH_ACCESS_KEY"):
        return 0  # No key → silently skip (matches the Phase 2 fail-soft style)

    files = _walk_jsx_files(workspace_path)
    if not files:
        return 0

    # 1) Read every file once; collect named-entity alts and per-file occurrence list.
    file_contents: dict[str, str] = {}
    file_hits: dict[str, list[tuple[int, int, str, str]]] = {}
    unique_alts: dict[str, None] = {}  # ordered set
    for path in files:
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
        except OSError as exc:
            logger.debug("image_binder: read %s failed: %s", path, exc)
            continue
        hits = _scan_file_for_named_imgs(content)
        if not hits:
            continue
        file_contents[path] = content
        file_hits[path] = hits
        for _, _, alt, _ in hits:
            unique_alts.setdefault(alt, None)

    if not unique_alts:
        return 0

    alts_to_search = list(unique_alts.keys())[:max_searches]
    logger.info(
        "image_binder: %d named-entity alts across %d files (searching up to %d)",
        len(unique_alts), len(file_hits), len(alts_to_search),
    )

    # 2) Parallel Unsplash searches with capped concurrency.
    sem = asyncio.Semaphore(4)
    results = await asyncio.gather(
        *(_search_one(alt, domain_qualifier, sem) for alt in alts_to_search),
        return_exceptions=False,
    )
    alt_to_url: dict[str, str] = {}
    for alt, url in zip(alts_to_search, results):
        if url:
            alt_to_url[alt] = url

    if not alt_to_url:
        logger.info("image_binder: no Unsplash matches found, skipping rewrites")
        return 0

    # 3) Rewrite files. Process hits in reverse so earlier offsets stay valid.
    rewrites = 0
    for path, hits in file_hits.items():
        content = file_contents[path]
        new_content = content
        # Reverse so slice replacements don't shift later offsets
        for start, end, alt, old_src in sorted(hits, key=lambda h: -h[0]):
            new_url = alt_to_url.get(alt)
            if not new_url or new_url == old_src:
                continue
            tag = new_content[start:end]
            # Replace the first src="..." in this tag specifically (tags can
            # only have one src; the regex anchors to the tag slice)
            new_tag = _SRC_ATTR_RE.sub(f'src="{new_url}"', tag, count=1)
            if new_tag == tag:
                continue
            new_content = new_content[:start] + new_tag + new_content[end:]
            rewrites += 1
        if new_content != content:
            try:
                _atomic_write(path, new_content)
            except OSError as exc:
                logger.warning("image_binder: write %s failed: %s", path, exc)

    logger.info("image_binder: rewrote %d <img> srcs", rewrites)
    return rewrites
