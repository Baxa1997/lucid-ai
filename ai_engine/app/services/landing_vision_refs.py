"""
landing_vision_refs.py — Reference screenshot fetcher for the landing pipeline.

Isolated, opt-in module that:
  1. Extracts reference URLs from the brief's `references` list.
  2. Fetches 2-3 screenshots via APIflash / ScreenshotOne.
  3. Caches them on disk + returns bytes for in-memory use during section codegen.
  4. Always fail-soft: any failure returns an empty result so the pipeline
     continues with text-only research, unchanged from before.

Activation: set SCREENSHOT_API_KEY env var. With the var empty (the default),
this module is a no-op and the pipeline behaves exactly as it did before.

The Claude-direct multimodal path is wired in landing_section_codegen.py
(`_generate_one_section`): when this module returns bytes, they get passed
to `call_claude_for_json` as image content blocks. When this module returns
an empty list, codegen falls back to text-only prompts.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# ── Cache layout: /tmp/lucid_screenshots/<project_id>/<idx>.jpg ──────────
_CACHE_ROOT = "/tmp/lucid_screenshots"


def _cache_dir(project_id: str) -> str:
    safe_pid = "".join(c for c in (project_id or "anon") if c.isalnum() or c in "-_")[:48]
    return os.path.join(_CACHE_ROOT, safe_pid or "anon")


def _load_cached(project_id: str) -> list[bytes]:
    """Return cached screenshot bytes for this project (in stable index order).

    Empty list if cache miss / dir missing / unreadable. Never raises.
    """
    cache_d = _cache_dir(project_id)
    if not os.path.isdir(cache_d):
        return []
    out: list[bytes] = []
    try:
        for fname in sorted(os.listdir(cache_d)):
            if not fname.endswith((".jpg", ".jpeg", ".png")):
                continue
            fpath = os.path.join(cache_d, fname)
            try:
                with open(fpath, "rb") as f:
                    data = f.read()
                if data and len(data) > 2000:
                    out.append(data)
            except OSError:
                continue
    except OSError:
        return []
    return out


def _save_cache(project_id: str, refs: list[bytes]) -> None:
    """Persist screenshot bytes under /tmp so repeat runs reuse them."""
    cache_d = _cache_dir(project_id)
    try:
        os.makedirs(cache_d, exist_ok=True)
        for i, data in enumerate(refs):
            fpath = os.path.join(cache_d, f"{i:02d}.jpg")
            with open(fpath, "wb") as f:
                f.write(data)
    except OSError as exc:
        logger.debug("landing_vision_refs: cache write skipped: %s", exc)


def _extract_reference_urls(brief: dict[str, Any], max_urls: int = 4) -> list[str]:
    """Pull URLs from `brief["references"]`. Falls back to visual_dna research."""
    urls: list[str] = []
    seen_hosts: set[str] = set()

    for ref in (brief.get("references") or [])[: max_urls + 3]:
        if not isinstance(ref, dict):
            continue
        url = (ref.get("url") or "").strip()
        if not url or not url.startswith(("http://", "https://")):
            continue
        try:
            host = url.split("/")[2].lower()
        except IndexError:
            continue
        if host in seen_hosts:
            continue
        # Skip aggregators / search results.
        if any(bad in host for bad in (
            "google.com", "youtube.com", "wikipedia.org",
            "awwwards.com", "godly.website", "siteinspire.com",
            "reddit.com", "medium.com", "dev.to", "behance.net",
            "dribbble.com", "instagram.com", "twitter.com", "x.com",
        )):
            continue
        seen_hosts.add(host)
        urls.append(url)
        if len(urls) >= max_urls:
            break

    return urls


async def fetch_landing_reference_screenshots(
    brief: dict[str, Any],
    project_id: str,
    websocket: Any = None,
) -> list[bytes]:
    """Fetch (or load cached) reference screenshots for this project.

    Returns a list of JPEG bytes (typically 2-3 images). Returns an empty
    list if SCREENSHOT_API_KEY is unset, no usable reference URLs, or
    every fetch failed.

    SAFETY: this function NEVER raises. The landing pipeline must continue
    to work unchanged when this returns [].
    """
    try:
        api_key = (os.environ.get("SCREENSHOT_API_KEY") or "").strip()
        if not api_key:
            logger.debug("landing_vision_refs: SCREENSHOT_API_KEY not set — skipping")
            return []

        # Cache hit — reuse without re-fetching.
        cached = _load_cached(project_id)
        if cached:
            logger.info(
                "landing_vision_refs: reused %d cached screenshots for project %s",
                len(cached), project_id,
            )
            return cached

        urls = _extract_reference_urls(brief, max_urls=int(os.environ.get("VISION_MAX_REFS", "3")))
        if not urls:
            logger.info("landing_vision_refs: no reference URLs in brief — skipping")
            return []

        if websocket is not None:
            try:
                await websocket.send_json({
                    "type": "progress",
                    "message": f"📸 Capturing {len(urls)} reference site screenshots for visual grounding…",
                })
            except Exception:
                pass

        # Reuse the existing fetcher from vision_research.py (same APIflash /
        # ScreenshotOne helpers, just used for a different downstream consumer).
        from app.services.vision_research import fetch_screenshots
        service = (os.environ.get("SCREENSHOT_SERVICE") or "apiflash").strip().lower()
        pairs = await fetch_screenshots(urls, api_key, service)
        refs = [data for _url, data in pairs if data]

        if not refs:
            logger.warning(
                "landing_vision_refs: 0/%d screenshots fetched — skipping multimodal",
                len(urls),
            )
            return []

        _save_cache(project_id, refs)
        logger.info(
            "landing_vision_refs: fetched %d/%d screenshots for project %s",
            len(refs), len(urls), project_id,
        )
        return refs

    except Exception as exc:
        # Truly belt-and-braces: any unforeseen error → empty result, pipeline continues.
        logger.warning("landing_vision_refs: unexpected error (non-fatal): %s", exc)
        return []


def load_cached_reference_screenshots(project_id: str) -> list[bytes]:
    """Synchronous cache-only read for use inside Claude codegen.

    Doesn't fetch — only returns what's already on disk. Used by section
    codegen which needs to attach images to many parallel Claude calls
    without re-running the fetch logic for each.
    """
    if not (os.environ.get("SCREENSHOT_API_KEY") or "").strip():
        return []
    return _load_cached(project_id)
