"""Website orchestrator — runs Stage 5 of the website pipeline in parallel.

Takes the project plan (pages + sections per page) + visual_dna and runs:
  • N parallel page generators (one Claude call per page)
  • 1 parallel header generator
  • 1 parallel footer generator

All sharing the same visual_dna so the result is brand-cohesive across pages.

Concurrency cap (default 8) protects Anthropic rate limits on big sites.
Each generator has its own timeout + retry logic — a single failure does NOT
break the build; failed pages are returned in a `failed_routes` list so the
caller can decide whether to retry or skeleton-fall-back.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Concurrency cap — Anthropic rate limits + provider backpressure.
# Lowered from 8 → 4 after observing empty-stream / "no tool_use input"
# failures when 8 parallel Sonnet 4.6 calls with 64K max_tokens each
# slammed Anthropic's TPM ceiling. 4 parallel × 64K = ~256K output budget
# in-flight, well under tier-1 limits, and a 7-page site still completes
# in roughly the same wall time because each page is far below the cap.
_DEFAULT_CONCURRENCY = 4


async def generate_website(
    *,
    plan: dict[str, Any],
    visual_dna: dict[str, Any],
    api_key: str,
    websocket: Any = None,
    concurrency: int = _DEFAULT_CONCURRENCY,
    skip_header: bool = False,
    skip_footer: bool = False,
    purpose_data: dict | None = None,
    page_images: dict[str, dict] | None = None,
    data_model: Any = None,   # DataModel | None — Stage 4.5 output
    # Per-section codegen context that landing computes from the brief.
    # When omitted, per-section calls fall back to the same generic defaults
    # they used before — caller (website_pipeline) is expected to populate.
    section_voice_context: dict | None = None,
    section_design_system: dict | None = None,
    section_design_tokens: dict | None = None,
    section_personality: dict | None = None,
) -> dict[str, Any]:
    """Run Stage 5 — parallel creative generation for the whole website.

    `plan` shape:
      {
        "brand": {"name": "...", "tagline": "...", "domain": "..."},
        "pages": [
          {"route": "/", "title": "Home", "sections": [{"type": "hero"}, ...]},
          {"route": "/menu", "title": "Menu", "sections": [...]},
          ...
        ],
      }

    `visual_dna` is the dict returned by `extract_research_signals(...).get("visual_dna")`.

    Returns:
      {
        "files":         [{"path": "...", "content": "..."}, ...],  # all generated files
        "failed_routes": ["/about", ...],                            # pages that failed
        "header_ok":     True/False,
        "footer_ok":     True/False,
        "page_results":  {"/": True, "/menu": True, "/about": False, ...},
      }
    """
    from app.services.page_generator import (
        generate_one_page, generate_page_per_section, _per_section_codegen_enabled,
    )
    from app.services.header_footer_generator import (
        generate_header, generate_footer,
    )

    # Per-section codegen mode (default ON) splits each page into one
    # Claude call per section — matches landing-page quality. Flip OFF
    # with WEBSITE_PER_SECTION_CODEGEN_ENABLED=0 to revert to one call
    # per page (cheaper, lower quality).
    per_section = _per_section_codegen_enabled()
    logger.info(
        "website_orchestrator: per-section codegen %s",
        "ENABLED — landing-quality mode" if per_section else "disabled — page-level mode",
    )

    brand = plan.get("brand") or {}
    brand_name = (brand.get("name") or "Brand").strip()
    tagline = (brand.get("tagline") or "").strip()
    domain = (brand.get("domain") or "general").strip()

    pages = plan.get("pages") or []
    if not pages:
        logger.error("website_orchestrator: plan has no pages — nothing to generate")
        return {
            "files": [], "failed_routes": [], "header_ok": False,
            "footer_ok": False, "page_results": {},
        }

    sem = asyncio.Semaphore(concurrency)

    # ── Build the task list ──────────────────────────────────────────
    # Each entry: (label, awaitable_factory, kind)
    #   kind ∈ {"page","header","footer"} — used for result categorization
    tasks: list[tuple[str, Any, str, dict]] = []

    page_images = page_images or {}

    async def _bounded_page(page: dict) -> list[dict] | None:
        route = (page.get("route") or page.get("path") or "/").strip()
        images_for_page = page_images.get(route) or {}
        async with sem:
            if per_section:
                # Each page becomes N parallel Claude calls (one per section).
                # The outer semaphore still limits OVERALL in-flight pages,
                # but generate_page_per_section has its own internal sem for
                # sections so a single page doesn't hog all of Anthropic.
                return await generate_page_per_section(
                    page=page, visual_dna=visual_dna,
                    brand_name=brand_name, tagline=tagline, domain=domain,
                    api_key=api_key, websocket=websocket,
                    page_images=images_for_page,
                    data_model=data_model,
                    voice_context=section_voice_context,
                    design_system=section_design_system,
                    design_tokens=section_design_tokens,
                    personality=section_personality,
                )
            return await generate_one_page(
                page=page, visual_dna=visual_dna,
                brand_name=brand_name, tagline=tagline, domain=domain,
                api_key=api_key, websocket=websocket,
                page_images=images_for_page,
                data_model=data_model,
            )

    async def _bounded_header() -> dict | None:
        async with sem:
            return await generate_header(
                brand_name=brand_name, tagline=tagline,
                domain=domain, visual_dna=visual_dna,
                api_key=api_key, websocket=websocket,
            )

    async def _bounded_footer() -> dict | None:
        async with sem:
            return await generate_footer(
                brand_name=brand_name, tagline=tagline,
                domain=domain, visual_dna=visual_dna,
                api_key=api_key, websocket=websocket,
            )

    page_tasks: list[tuple[str, Any]] = []
    for page in pages:
        if not isinstance(page, dict):
            continue
        route = (page.get("route") or page.get("path") or "/").strip()
        page_tasks.append((route, _bounded_page(page)))

    chrome_tasks: list[tuple[str, Any]] = []
    if not skip_header:
        chrome_tasks.append(("__header__", _bounded_header()))
    if not skip_footer:
        chrome_tasks.append(("__footer__", _bounded_footer()))

    all_tasks = page_tasks + chrome_tasks
    logger.info(
        "website_orchestrator: launching %d parallel calls (%d pages + %d chrome) concurrency=%d",
        len(all_tasks), len(page_tasks), len(chrome_tasks), concurrency,
    )

    # ── Execute all in parallel ──────────────────────────────────────
    results = await asyncio.gather(
        *(t for _, t in all_tasks),
        return_exceptions=True,
    )

    # ── Collect ──────────────────────────────────────────────────────
    all_files: list[dict] = []
    failed_routes: list[str] = []
    page_results: dict[str, bool] = {}
    header_ok = False
    footer_ok = False

    for (label, _), res in zip(all_tasks, results):
        if isinstance(res, BaseException):
            logger.error(
                "website_orchestrator: %s raised — %s", label, res,
            )
            if label == "__header__":
                header_ok = False
            elif label == "__footer__":
                footer_ok = False
            else:
                failed_routes.append(label)
                page_results[label] = False
            continue

        if label == "__header__":
            if isinstance(res, dict) and res.get("path") and res.get("content"):
                all_files.append(res)
                header_ok = True
            else:
                header_ok = False
            continue

        if label == "__footer__":
            if isinstance(res, dict) and res.get("path") and res.get("content"):
                all_files.append(res)
                footer_ok = True
            else:
                footer_ok = False
            continue

        # Page result — list of files or None
        if isinstance(res, list) and res:
            all_files.extend(res)
            page_results[label] = True
        else:
            failed_routes.append(label)
            page_results[label] = False

    logger.info(
        "website_orchestrator: done — %d files, %d/%d pages ok, header=%s footer=%s",
        len(all_files), sum(1 for v in page_results.values() if v),
        len(page_results), header_ok, footer_ok,
    )

    return {
        "files": all_files,
        "failed_routes": failed_routes,
        "header_ok": header_ok,
        "footer_ok": footer_ok,
        "page_results": page_results,
    }
