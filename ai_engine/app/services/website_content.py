"""Website runtime content writer — multi-page analogue of landing_content.

The website per-section codegen path reuses landing's section prompt, which
hardcodes `import landing from "@/content/landing.json"` and reads
`landing.sections.find(s => s.id === "<id>")`. Without that file, every
section component lookup returns `undefined` and the page renders with no
copy, no items, and no images.

This module builds and writes that file for a multi-page plan. Each page's
sections land under one global `sections[]` array with globally unique IDs
of the form `{slug}_{section_type}` (matching the IDs page_generator stamps
into the section dict before per-section codegen runs).

Shape mirrors `landing_content.build_landing_content` so the same section
component template works for both pipelines.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)

_NOT_IN_NAV = {"hero", "footer", "cta", "cta_band", "newsletter"}


def _slug_from_route(route: str) -> str:
    r = (route or "/").strip().lower()
    if r in ("", "/"):
        return "home"
    cleaned = re.sub(r"[^a-z0-9]+", "_", r.strip("/"))
    return cleaned.strip("_") or "home"


def _image_urls(raw_images: Any) -> list[str]:
    """Coerce bound image entries to a flat list of URL strings.

    bind_page_images returns [{url, alt, photographer, photographer_url}].
    The section component reads `section.images?.[i]` expecting a URL string,
    so we flatten here. Empty / malformed entries are dropped.
    """
    if not isinstance(raw_images, list):
        return []
    out: list[str] = []
    for img in raw_images:
        if isinstance(img, dict):
            url = (img.get("url") or "").strip()
            if url:
                out.append(url)
        elif isinstance(img, str) and img.strip():
            out.append(img.strip())
    return out


def _image_alts(raw_images: Any) -> list[str]:
    if not isinstance(raw_images, list):
        return []
    out: list[str] = []
    for img in raw_images:
        if isinstance(img, dict):
            out.append((img.get("alt") or "").strip())
        else:
            out.append("")
    return out


def _shape_section(slug: str, raw_section: dict, page_imgs: dict) -> dict | None:
    if not isinstance(raw_section, dict):
        return None
    s_type = (raw_section.get("type") or raw_section.get("name") or "").strip().lower()
    if not s_type:
        return None

    sec_id = (raw_section.get("id") or f"{slug}_{s_type}").strip()

    # page_imgs is the per-page output of bind_page_images, keyed by section
    # type with _2/_3 suffixes for repeats. The section dict carries its own
    # `images` field too (page_generator attaches it before codegen). Prefer
    # whichever has data; fall back to the type key.
    raw_imgs = (
        raw_section.get("images")
        or page_imgs.get(sec_id)
        or page_imgs.get(s_type)
        or []
    )

    shaped: dict = {
        "id": sec_id,
        "type": s_type,
        "page_slug": slug,
        "role": raw_section.get("role", ""),
        "nav_label": (raw_section.get("nav_label") or "").strip(),
        "archetype": raw_section.get("archetype", ""),
        "interactivity": raw_section.get("interactivity", ""),
        "headline": raw_section.get("headline", ""),
        "subheadline": raw_section.get("subheadline", ""),
        "body": raw_section.get("body", ""),
        "layout_hint": raw_section.get("layout_hint", "centered-stack"),
        "items": list(raw_section.get("items") or []),
        "images": _image_urls(raw_imgs),
        "image_alts": _image_alts(raw_imgs),
    }
    # Only emit `cta` when it carries real content. An empty `{}` is truthy in
    # JS, so `section.cta || landing.ctas.primary` stays `{}` and rendering
    # `{cta}` throws "Objects are not valid as a React child".
    raw_cta = raw_section.get("cta")
    if isinstance(raw_cta, dict) and (raw_cta.get("label") or raw_cta.get("href")):
        shaped["cta"] = {
            "label": raw_cta.get("label", ""),
            "href": raw_cta.get("href", "#"),
        }
    return shaped


def _derive_nav_from_pages(pages: list[dict]) -> list[dict[str, str]]:
    """Top-level marketing nav: one entry per page (not per section).

    Hero/footer/cta sections never appear in nav. Multi-page sites navigate
    between pages via routes, not section anchors.
    """
    nav: list[dict[str, str]] = []
    for p in pages:
        route = (p.get("route") or p.get("path") or "/").strip()
        title = (p.get("title") or "").strip()
        if not title:
            title = "Home" if route in ("", "/") else route.lstrip("/").replace("-", " ").title()
        nav.append({"label": title[:18], "href": route or "/"})
    return nav[:7]


def build_website_landing_content(
    plan: dict[str, Any],
    visual_dna: dict[str, Any],
    page_images: dict[str, dict],
    *,
    intent: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the dict that becomes `src/content/landing.json`.

    `page_images` is keyed by route (matches what website_pipeline builds);
    we re-key per page when shaping each section.
    """
    brand = dict(plan.get("brand") or {})
    pages = list(plan.get("pages") or [])
    intent = intent or {}

    palette = dict(visual_dna.get("palette") or {})
    typography = dict(visual_dna.get("typography") or {})
    motif = (visual_dna.get("motif") or visual_dna.get("primary_motif") or "modern").strip().lower()

    sections_out: list[dict[str, Any]] = []
    for page in pages:
        if not isinstance(page, dict):
            continue
        route = (page.get("route") or page.get("path") or "/").strip()
        slug = _slug_from_route(route)
        page_imgs = page_images.get(route) or {}
        for raw_section in page.get("sections") or []:
            shaped = _shape_section(slug, raw_section, page_imgs)
            if shaped:
                sections_out.append(shaped)

    nav = _derive_nav_from_pages(pages)

    return {
        "brand": {
            "name": brand.get("name", ""),
            "tagline": brand.get("tagline", ""),
            "description": brand.get("description", ""),
            "domain": brand.get("domain", intent.get("business_category", "")),
            "business_info": dict(brand.get("business_info") or {}),
            "social": list(brand.get("social") or []),
        },
        "theme": {
            "palette": palette,
            "typography": typography,
            "motif": motif,
            "design_system": dict(visual_dna.get("design_system") or {}),
            "personality": dict(visual_dna.get("personality") or {}),
            "header_archetype": (visual_dna.get("chosen_header_archetype")
                                 or visual_dna.get("header_archetype") or "").strip(),
            "footer_archetype": (visual_dna.get("chosen_footer_archetype")
                                 or visual_dna.get("footer_archetype") or "").strip(),
        },
        "references": list(visual_dna.get("references") or [])[:5],
        "visual_dna": dict(visual_dna or {}),
        "nav": nav,
        "ctas": {
            "primary": (plan.get("ctas") or {}).get("primary")
                       or {"label": "Get started", "href": "#contact"},
            "secondary": (plan.get("ctas") or {}).get("secondary") or {},
        },
        "sections": sections_out,
        "footer": {
            "brand": brand.get("name", ""),
            "links": [{"label": n["label"], "href": n["href"]} for n in nav],
        },
    }


def write_website_landing_content(
    workspace_path: str,
    plan: dict[str, Any],
    visual_dna: dict[str, Any],
    page_images: dict[str, dict],
    *,
    intent: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Write `<workspace>/src/content/landing.json` for the multi-page site.

    Returns (path, content_dict). Section components generated by the
    per-section codegen path import this file by name.
    """
    content = build_website_landing_content(
        plan, visual_dna, page_images, intent=intent,
    )
    target_dir = os.path.join(workspace_path, "src", "content")
    os.makedirs(target_dir, exist_ok=True)
    target_path = os.path.join(target_dir, "landing.json")
    with open(target_path, "w", encoding="utf-8") as fh:
        json.dump(content, fh, indent=2, ensure_ascii=False)

    img_count = sum(len(s.get("images") or []) for s in content["sections"])
    logger.info(
        "write_website_landing_content: wrote %s (sections=%d, images=%d, pages=%d)",
        target_path, len(content["sections"]), img_count, len(plan.get("pages") or []),
    )
    return target_path, content
