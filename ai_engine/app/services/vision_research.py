"""
Vision-grounded design research.

After text-based Gemini research returns a list of reference URLs in the
===SITES_ANALYZED=== block, this module:
  1. Parses the URLs.
  2. Fetches 2-4 screenshots via a pluggable screenshot service.
  3. Sends the images to Gemini Pro vision for a design-critic analysis.
  4. Returns a ===VISUAL_DNA=== text block that the downstream distiller
     appends to the research dump (so Claude sees concrete visual patterns,
     not just verbal descriptions).

Designed to be FAIL-SOFT: any failure (no API key, bad URL, screenshot
timeout, Gemini error) logs a warning and returns an empty string. The
calling pipeline proceeds with text-only research unchanged.

Config (env vars):
  SCREENSHOT_API_KEY      — API key for the screenshot service. If unset,
                            vision enrichment is skipped entirely.
  SCREENSHOT_SERVICE      — "apiflash" (default) | "screenshotone"
  VISION_RESEARCH_MODEL   — Gemini model for vision (default gemini-3.1-pro-preview)
  VISION_MAX_REFS         — max reference screenshots to include (default 3)
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import re
from typing import Optional
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)


# ─── URL extraction ──────────────────────────────────────────────────────────

# Matches `(https://example.com/path)` — the URL form the prompt asks Gemini
# to emit inside the ===SITES_ANALYZED=== block. Forgiving about trailing
# punctuation (`.,)]`) that sometimes trails a URL in prose.
_URL_RE = re.compile(r"https?://[^\s\)\]\>\<\"\']+", re.IGNORECASE)


def extract_reference_urls(research_text: str, max_urls: int = 5) -> list[str]:
    """Pull reference URLs out of the ===SITES_ANALYZED=== block.

    Falls back to the full text if the block is missing, then filters to
    reputable-looking domains. Dedupes by host.
    """
    if not research_text:
        return []

    block = research_text
    for header in ("===SITES_ANALYZED===", "===PRODUCTS_ANALYZED===", "===PLATFORMS_ANALYZED==="):
        if header in research_text:
            start = research_text.index(header) + len(header)
            rest = research_text[start:]
            end_match = rest.find("===")
            end = start + end_match if end_match != -1 else start + 4000
            block = research_text[start:end]
            break

    seen_hosts: set[str] = set()
    urls: list[str] = []
    for match in _URL_RE.finditer(block):
        url = match.group(0).rstrip(".,);:]")
        # Skip Google/search result URLs — we want the actual reference sites
        host = url.split("/")[2].lower() if "://" in url else ""
        if not host:
            continue
        if any(bad in host for bad in (
            "google.com", "youtube.com", "wikipedia.org",
            "awwwards.com", "godly.website", "siteinspire.com",
            "reddit.com", "medium.com", "dev.to",
        )):
            continue
        if host in seen_hosts:
            continue
        seen_hosts.add(host)
        urls.append(url)
        if len(urls) >= max_urls:
            break

    return urls


# ─── Screenshot fetching ─────────────────────────────────────────────────────

async def _fetch_screenshot(url: str, api_key: str, service: str, timeout: float = 20.0) -> Optional[bytes]:
    """Fetch a single screenshot as PNG bytes. Returns None on failure."""
    if service == "apiflash":
        endpoint = (
            "https://api.apiflash.com/v1/urltoimage"
            f"?access_key={api_key}"
            f"&url={quote(url, safe='')}"
            "&format=jpeg"
            "&quality=70"
            "&width=1280"
            "&height=1600"
            "&full_page=false"
            "&fresh=false"
            "&response_type=image"
            "&wait_until=page_loaded"
            "&no_cookie_banners=true"
        )
    elif service == "screenshotone":
        endpoint = (
            "https://api.screenshotone.com/take"
            f"?access_key={api_key}"
            f"&url={quote(url, safe='')}"
            "&viewport_width=1280"
            "&viewport_height=1600"
            "&format=jpg"
            "&image_quality=70"
            "&block_cookie_banners=true"
            "&full_page=false"
        )
    else:
        logger.warning("Unknown SCREENSHOT_SERVICE=%s — skipping", service)
        return None

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(endpoint)
        if resp.status_code != 200:
            logger.warning("Screenshot fetch failed for %s: HTTP %d", url, resp.status_code)
            return None
        if len(resp.content) < 2000:
            logger.warning("Screenshot too small (%d bytes) for %s — likely error image", len(resp.content), url)
            return None
        return resp.content
    except Exception as exc:
        logger.warning("Screenshot exception for %s: %s", url, exc)
        return None


async def fetch_screenshots(urls: list[str], api_key: str, service: str) -> list[tuple[str, bytes]]:
    """Fetch multiple screenshots in parallel. Returns list of (url, bytes) for successful fetches."""
    tasks = [_fetch_screenshot(u, api_key, service) for u in urls]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    pairs: list[tuple[str, bytes]] = []
    for url, result in zip(urls, results):
        if isinstance(result, (bytes, bytearray)) and result:
            pairs.append((url, bytes(result)))
    return pairs


# ─── Vision Gemini call ──────────────────────────────────────────────────────

_VISION_SYSTEM = (
    "You are a senior UI/UX design critic. You read real website screenshots "
    "like a designer, not a marketer — you name specific typographic choices, "
    "spatial relationships, color ratios, card surface language, and motion "
    "cues. You never write vague praise. Your output is structured, terse, "
    "and directly usable as design-system specification text."
)


def _build_vision_prompt(description: str, domain: str, refs: list[tuple[str, bytes]]) -> str:
    """Build the text part of the vision call — images are added separately as inlineData parts."""
    ref_lines = "\n".join(f"  {i+1}. {url}" for i, (url, _) in enumerate(refs))
    return f"""I am designing a new {domain} website for: "{description}"

Below are {len(refs)} screenshots of top {domain} sites in 2024-2025:
{ref_lines}

Analyze each screenshot as a design critic. Then SYNTHESIZE a single
VISUAL DNA block that a junior designer could use to build a new site in the
same aesthetic family.

FOR EACH screenshot, write 2-3 crisp bullets covering:
  - Hero composition (imagery type, text position, layering, scale contrast)
  - Color application (dominant hue, background temp, accent placement + ratio)
  - Typography (serif/sans mix, weight contrast, size jumps, italic usage)
  - Card / content block language (radius, border, shadow, internal padding rhythm)
  - Visible motion/interaction cues (hover elevation, scroll reveals, sticky elements)

Be SPECIFIC. "Warm ivory background" not "light colors". "Display serif ~120px
italic weight 400 overlaid on duotone photo" not "bold headings".

Then output the synthesis in EXACTLY this format (keep each line under 180 chars):

===VISUAL_DNA===
hero_composition: [one specific, borrowable pattern — e.g. "cinematic duotone
  hero photo full-bleed, oversized italic display serif bottom-left, small
  meta text top-right corner"]
color_application: [dominant palette + accent strategy — e.g. "warm ivory
  #f5efe6 background, espresso brown #2c1d14 text, single burnt-orange
  #c85a3c accent used only for CTAs + dates, 90/8/2 ratio"]
typography_system: [heading font family + body font family + size contrast —
  e.g. "display serif (Fraunces or similar) 600-weight italic for hero at
  8vw, neutral sans (Inter-like) for body at 16px, 4:1 size ratio"]
card_language: [shape + border + shadow + internal spacing — e.g. "rectangular
  cards with 8px radius, 1px border-border, no shadow, 32px internal padding,
  subtle ivory fill #f9f6f1"]
spacing_rhythm: [section padding + vertical rhythm — e.g. "160px vertical
  section padding, 80px between H2 and content, 32px between cards"]
motion_language: [2-3 observed cues — e.g. "scroll-triggered fade-up with
  stagger on card rows, hover: card lifts 4px with shadow deepening, no
  parallax, no auto-playing video"]
distinctive_moves: [3 specific things to BORROW for the new site — each move
  must reference a concrete pattern above. e.g. "1) oversized italic serif
  hero, 2) asymmetric timeline with dotted connector in the story section,
  3) monochrome photo + color-accent pairing in feature cards"]
patterns_to_avoid: [2 dated/generic patterns seen in weaker sites to skip —
  e.g. "symmetric 3-column icon grid with centered text, flat pastel
  gradient hero"]
===END_VISUAL_DNA===

Return ONLY the per-screenshot analysis followed by the VISUAL_DNA block. No
preamble, no closing commentary.
"""


async def _call_gemini_vision(
    prompt: str,
    refs: list[tuple[str, bytes]],
    gemini_key: str,
    model: str,
    timeout: float = 90.0,
) -> Optional[str]:
    """Call Gemini with text + inline image parts. Returns the raw text or None."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={gemini_key}"

    parts: list[dict] = [{"text": prompt}]
    for _url, img_bytes in refs:
        parts.append({
            "inlineData": {
                "mimeType": "image/jpeg",
                "data": base64.b64encode(img_bytes).decode("ascii"),
            }
        })

    is_pro = "pro" in model.lower()
    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "maxOutputTokens": 4000,
            "temperature": 0.35,
            # Pro rejects thinkingBudget=0 → give it a small budget. Flash skips.
            "thinkingConfig": {"thinkingBudget": 1024 if is_pro else 0},
        },
        "systemInstruction": {"parts": [{"text": _VISION_SYSTEM}]},
    }

    from app.services.llm_retry import (
        call_with_retry, classify_http_error, LLMPermanentError,
    )

    async def _do_call() -> dict:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=payload)
        if resp.status_code != 200:
            raise classify_http_error(resp.status_code, resp.text)
        return resp.json()

    try:
        data = await call_with_retry(_do_call, label="gemini_vision")
    except LLMPermanentError as exc:
        logger.warning("Gemini vision permanent failure: %s", exc)
        return None
    except Exception as exc:
        logger.warning("Gemini vision failed after retries: %s", exc)
        return None

    try:
        from knowledge.loader import safe_gemini_text
        text = safe_gemini_text(data)
    except Exception as exc:
        logger.warning("Gemini vision response parse failed: %s", exc)
        return None

    return text.strip() if text else None


# ─── Public orchestrator ─────────────────────────────────────────────────────

async def vision_enrich_research(
    research_text: str,
    description: str,
    domain: str,
    gemini_key: str,
    websocket=None,
) -> str:
    """Return a ===VISUAL_DNA=== block to append to `research_text`, or "" on any failure.

    FAIL-SOFT: any error path returns "" so the caller can proceed with
    text-only research. Never raises.
    """
    api_key = (os.environ.get("SCREENSHOT_API_KEY") or "").strip()
    if not api_key:
        logger.info("Vision enrichment skipped — SCREENSHOT_API_KEY not set")
        return ""

    service = (os.environ.get("SCREENSHOT_SERVICE") or "apiflash").strip().lower()
    max_refs = int(os.environ.get("VISION_MAX_REFS", "3"))
    model = (os.environ.get("VISION_RESEARCH_MODEL") or "gemini-3.1-pro-preview").strip()

    urls = extract_reference_urls(research_text, max_urls=max_refs + 2)
    if not urls:
        logger.info("Vision enrichment skipped — no reference URLs in research")
        return ""

    if websocket is not None:
        try:
            await websocket.send_json({
                "type": "progress",
                "content": f"📸 Capturing {min(len(urls), max_refs)} reference screenshots...",
            })
        except Exception:
            pass

    refs = await fetch_screenshots(urls[:max_refs + 1], api_key, service)
    refs = refs[:max_refs]  # hard cap even if extras succeeded

    if len(refs) < 2:
        logger.warning("Vision enrichment skipped — only %d screenshot(s) fetched", len(refs))
        return ""

    if websocket is not None:
        try:
            await websocket.send_json({
                "type": "progress",
                "content": f"👁️ {model} analyzing {len(refs)} reference designs...",
            })
        except Exception:
            pass

    prompt = _build_vision_prompt(description, domain, refs)
    vision_text = await _call_gemini_vision(prompt, refs, gemini_key, model)

    if not vision_text:
        return ""

    # Keep just the VISUAL_DNA block. The per-screenshot analysis is useful
    # context for the model but bloats the distilled research block. If the
    # model emitted the whole thing with the block delimiters, we extract.
    if "===VISUAL_DNA===" in vision_text:
        start = vision_text.index("===VISUAL_DNA===")
        end_idx = vision_text.find("===END_VISUAL_DNA===", start)
        if end_idx != -1:
            block = vision_text[start:end_idx].strip()
        else:
            block = vision_text[start:].strip()
    else:
        # Model didn't emit the delimiter — wrap the whole response.
        block = f"===VISUAL_DNA===\n{vision_text.strip()}"

    # Ensure block ends cleanly without an END marker (downstream extractor
    # finds the next `===` boundary on its own).
    block = block.replace("===END_VISUAL_DNA===", "").rstrip()

    if websocket is not None:
        try:
            await websocket.send_json({
                "type": "progress",
                "content": "✅ Visual DNA extracted from reference sites",
            })
        except Exception:
            pass

    logger.info("Vision enrichment success — %d chars VISUAL_DNA", len(block))
    return "\n\n" + block + "\n"
