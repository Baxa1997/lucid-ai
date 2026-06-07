"""Per-page section-anatomy refiner — Option C from the codegen audit.

After Stage 3 produces a GLOBAL `visual_dna.section_anatomies` map (keyed
by section type), this module runs a small Gemini Flash call PER PAGE to
specialize each of that page's section anatomies for the page's specific
purpose. The output is a per-page anatomy override dict that the codegen
layer consults BEFORE falling back to the global map.

Why this matters:
  Without refinement, the hero on `/home` and the hero on `/about` both
  pull `visual_dna.section_anatomies["hero"]` — same anatomy paragraph,
  so Claude produces visually similar heroes on different pages. With
  refinement, each page gets a hero anatomy specialized for its purpose
  (home → conversion + product imagery, about → founder portrait + heritage).

Cost / speed:
  One Gemini Flash call per page (~$0.02, ~5s). 7-page site = ~$0.14,
  all pages refined in parallel via asyncio.gather (~5-8s wall time).

Failure mode:
  Refinement is BEST-EFFORT. If Gemini errors or returns an invalid
  shape, this function returns `None` for that page and the codegen
  layer falls back to the global anatomies. Never blocks the pipeline.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


_REFINER_MAX_TOKENS = 2048
_REFINER_TIMEOUT_S = 30.0


def refiner_enabled() -> bool:
    """Page-anatomy refiner toggle (default ON)."""
    raw = os.environ.get("WEBSITE_PAGE_ANATOMY_REFINER_ENABLED", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _refine_schema(section_types: list[str]) -> dict[str, Any]:
    """Gemini response schema — flat array keyed by section_type so
    Gemini's constrained decoder doesn't choke on dynamic-key OBJECTs."""
    return {
        "type": "OBJECT",
        "required": ["anatomies"],
        "properties": {
            "anatomies": {
                "type": "ARRAY",
                "minItems": 1,
                "maxItems": max(1, len(section_types) + 2),
                "items": {
                    "type": "OBJECT",
                    "required": ["section_type", "anatomy"],
                    "properties": {
                        "section_type": {"type": "STRING", "maxLength": 30},
                        "anatomy":      {"type": "STRING", "maxLength": 280},
                    },
                },
            },
        },
    }


_PROMPT = """You are a design strategist specializing the section anatomies of ONE specific page in a multi-page website.

BRAND
  Name:    {brand_name}
  Tagline: {brand_tagline}
  Domain:  {brand_domain}

PAGE BEING REFINED
  Route:   {page_route}
  Title:   {page_title}
  Purpose: {page_purpose}
  Sections on this page: {section_list}

GLOBAL SECTION ANATOMIES (apply to every page by default)
{global_anatomies_block}

YOUR JOB
For each section on THIS page, write a REFINED anatomy paragraph that:
  • Keeps the structural shape from the global anatomy
  • Specializes the content focus, imagery, and CTA for THIS PAGE's purpose
  • Stays ≤ 250 characters, written as a tight developer note (no marketing fluff)

EXAMPLES OF GOOD PAGE-LEVEL SPECIALIZATION
  Home hero      → conversion-focused, primary CTA prominent, product imagery
  About hero     → founder portrait or heritage imagery, minimal CTA, narrative emphasis
  Pricing hero   → value-prop headline, plan comparison teaser, two CTAs (start free / contact sales)
  Menu hero      → food photography hero with appetite-led headline
  Contact hero   → quick contact + map/locations teaser; minimal copy

OUTPUT RULES
  • Return JSON: {{"anatomies": [{{"section_type": "...", "anatomy": "..."}}, ...]}}
  • One entry per section listed above
  • Anatomy paragraph ≤ 250 chars
  • NO markdown, NO bullet points, NO repetition of the global anatomy verbatim
"""


def _format_global_anatomies_block(section_anatomies: dict[str, Any], wanted: list[str]) -> str:
    """Format the global anatomy map for the prompt, showing only the
    section types this page uses (keeps the prompt small)."""
    lines: list[str] = []
    for s in wanted:
        a = section_anatomies.get(s)
        if isinstance(a, str) and a.strip():
            lines.append(f"  {s}: {a.strip()[:200]}")
        elif a:
            # Defensive: anatomy could be a dict in legacy data.
            lines.append(f"  {s}: (no anatomy — generate from scratch)")
        else:
            lines.append(f"  {s}: (no anatomy — generate from scratch)")
    return "\n".join(lines) if lines else "  (none — derive anatomies from the section type alone)"


async def refine_anatomies_for_page(
    page: dict[str, Any],
    visual_dna: dict[str, Any],
    brand: dict[str, Any],
    *,
    timeout_s: float = _REFINER_TIMEOUT_S,
) -> dict[str, str] | None:
    """Run ONE Gemini Flash call to specialize the page's section anatomies.

    Returns:
        {section_type: refined_anatomy_paragraph}  on success
        None                                        on any failure (caller falls back)
    """
    from app.services.landing_gemini import structured_distill

    sections = page.get("sections") or []
    section_types = [
        (s.get("type") or "").strip().lower()
        for s in sections
        if isinstance(s, dict) and (s.get("type") or "").strip()
    ]
    if not section_types:
        return None

    # Dedup but preserve order — same section type can repeat (rare).
    seen: set[str] = set()
    unique_types: list[str] = []
    for t in section_types:
        if t in seen:
            continue
        seen.add(t)
        unique_types.append(t)

    global_anatomies = (visual_dna or {}).get("section_anatomies") or {}
    page_route = (page.get("route") or page.get("path") or "/").strip()
    page_title = (page.get("title") or page_route.lstrip("/").title() or "Page").strip()
    page_purpose = (page.get("purpose") or "").strip() or f"Standard {page_title} page content."

    prompt = _PROMPT.format(
        brand_name=brand.get("name") or "Brand",
        brand_tagline=brand.get("tagline") or "",
        brand_domain=brand.get("domain") or "general",
        page_route=page_route,
        page_title=page_title,
        page_purpose=page_purpose[:280],
        section_list=", ".join(unique_types),
        global_anatomies_block=_format_global_anatomies_block(global_anatomies, unique_types),
    )

    try:
        raw = await asyncio.wait_for(
            structured_distill(
                prompt,
                timeout_s,
                label=f"page_anatomy_refiner[{page_route}]",
                response_schema=_refine_schema(unique_types),
                max_tokens=_REFINER_MAX_TOKENS,
                model="gemini-3.5-flash",
            ),
            timeout=timeout_s + 5.0,
        )
    except asyncio.TimeoutError:
        logger.warning("page_anatomy_refiner: %s timed out", page_route)
        return None
    except Exception as exc:
        logger.warning("page_anatomy_refiner: %s failed — %s", page_route, exc)
        return None

    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except Exception as exc:
        logger.warning("page_anatomy_refiner: %s JSON parse failed — %s", page_route, exc)
        return None

    items = (parsed or {}).get("anatomies") if isinstance(parsed, dict) else None
    if not isinstance(items, list) or not items:
        logger.warning("page_anatomy_refiner: %s returned no anatomies", page_route)
        return None

    refined: dict[str, str] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        s_type = (item.get("section_type") or "").strip().lower()
        anatomy = (item.get("anatomy") or "").strip()
        if not s_type or not anatomy:
            continue
        refined[s_type] = anatomy[:280]

    if not refined:
        return None

    logger.info(
        "page_anatomy_refiner: %s refined %d/%d section anatomies",
        page_route, len(refined), len(unique_types),
    )
    return refined


async def refine_anatomies_for_all_pages(
    pages: list[dict[str, Any]],
    visual_dna: dict[str, Any],
    brand: dict[str, Any],
) -> None:
    """Mutate `pages` in place: attach `page["section_anatomies"]` to
    each page with the refined map. Pages whose refinement fails simply
    don't get the attribute, and the codegen layer falls back to the
    global anatomies for that page.

    Pages are refined in parallel via asyncio.gather. Total wall time
    ≈ slowest single page (~5-10s for Gemini Flash with a small prompt).
    """
    if not refiner_enabled():
        logger.info("page_anatomy_refiner: disabled by env flag — skipping")
        return
    if not pages:
        return

    async def _one(p: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str] | None]:
        return p, await refine_anatomies_for_page(p, visual_dna, brand)

    results = await asyncio.gather(
        *[_one(p) for p in pages if isinstance(p, dict)],
        return_exceptions=False,
    )

    success = 0
    for page, refined in results:
        if refined:
            page["section_anatomies"] = refined
            success += 1

    logger.info(
        "page_anatomy_refiner: refined %d/%d pages (fell back to global for the rest)",
        success, len(pages),
    )
