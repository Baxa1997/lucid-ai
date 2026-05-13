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

# Concurrency cap — Anthropic rate limits + provider backpressure. 8 is the
# sweet spot for a 6-7 page site: all pages run in parallel without throttling.
_DEFAULT_CONCURRENCY = 8


async def generate_website(
    *,
    plan: dict[str, Any],
    visual_dna: dict[str, Any],
    api_key: str,
    websocket: Any = None,
    concurrency: int = _DEFAULT_CONCURRENCY,
    skip_header: bool = False,
    skip_footer: bool = False,
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
    from app.services.page_generator import generate_one_page
    from app.services.header_footer_generator import (
        generate_header, generate_footer,
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

    async def _bounded_page(page: dict) -> list[dict] | None:
        async with sem:
            return await generate_one_page(
                page=page, visual_dna=visual_dna,
                brand_name=brand_name, tagline=tagline, domain=domain,
                api_key=api_key, websocket=websocket,
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
