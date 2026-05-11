"""Landing content writer — converts a Landing Brief into src/content/landing.json.

This file is the single source of truth for the GENERATED landing project's
runtime content. Section components import from `@/content/landing.json` and
read their copy / items / images from there.

Why a separate runtime JSON instead of baking copy into JSX:
  • Editing copy / swapping images is a JSON patch, not a regeneration.
  • Section components become thin renderers — no string drift across regens.
  • Images are bound here from `image_queries`, set later in Step 4.

This module is dataflow only. No Gemini / Claude calls. Pure shaping +
file write. Called from the new landing pipeline after build_landing_brief.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# ── Sections that must NOT appear in the marketing nav ───────────────
# Header anchors are derived from sections, but a few section types
# don't make sense as nav entries (the hero is what loads first; the
# footer has its own role; CTAs are bands, not destinations).
_NOT_IN_NAV = {"hero", "footer", "cta", "cta_band", "newsletter"}


def build_landing_content(brief: dict[str, Any]) -> dict[str, Any]:
    """Shape a Landing Brief into the runtime content JSON.

    Returns the dict that will be written to `src/content/landing.json`.
    Image arrays are present but empty; Step 4 (image binder) populates
    them from `image_queries` after Unsplash lookup.
    """
    brand = dict(brief.get("brand") or {})
    palette = dict(brief.get("palette") or {})
    typography = dict(brief.get("typography") or {})
    motif = (brief.get("motif") or "minimal").strip().lower()
    ctas = dict(brief.get("ctas") or {})

    sections_out: list[dict[str, Any]] = []
    for s in brief.get("sections") or []:
        sec_id = (s.get("id") or s.get("type") or "section").strip()
        section: dict[str, Any] = {
            "id": sec_id,
            "type": (s.get("type") or sec_id).strip(),
            "role": s.get("role", ""),
            "nav_label": (s.get("nav_label") or "").strip(),
            "archetype": s.get("archetype", ""),
            "interactivity": s.get("interactivity", ""),
            "headline": s.get("headline", ""),
            "subheadline": s.get("subheadline", ""),
            "body": s.get("body", ""),
            "layout_hint": s.get("layout_hint", "centered-stack"),
            "items": list(s.get("items") or []),
            "images": [
                {"url": "", "alt": q, "query": q}
                for q in (s.get("image_queries") or [])
            ],
        }
        cta = s.get("cta")
        if isinstance(cta, dict) and (cta.get("label") or cta.get("href")):
            section["cta"] = {
                "label": cta.get("label", ""),
                "href": cta.get("href", "#"),
            }
        sections_out.append(section)

    nav = _derive_nav(sections_out)
    footer = _derive_footer(brand.get("name", ""), sections_out)

    return {
        "brand": {
            "name": brand.get("name", ""),
            "tagline": brand.get("tagline", ""),
            "description": brand.get("description", ""),
            "domain": brand.get("domain", ""),
            "business_info": dict(brand.get("business_info") or {}),
            "social": list(brand.get("social") or []),
        },
        "theme": {
            "palette": palette,
            "typography": typography,
            "motif": motif,
            "design_system": dict(brief.get("design_system") or {}),
            "personality": dict(brief.get("personality") or {}),
            "header_archetype": (brief.get("header_archetype") or "").strip(),
            "footer_archetype": (brief.get("footer_archetype") or "").strip(),
        },
        "references": list(brief.get("references") or [])[:5],
        # Persist visual_dna so the frontend / diagnostics can read it, and
        # so re-generating from this workspace later (without re-running
        # research) doesn't lose the per-section anatomies.
        "visual_dna": dict(brief.get("visual_dna") or {}),
        "nav": nav,
        "ctas": {
            "primary": ctas.get("primary") or {"label": "Get started", "href": "#contact"},
            "secondary": ctas.get("secondary") or {},
        },
        "sections": sections_out,
        "footer": footer,
    }


def write_landing_content(workspace_path: str, brief: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Build and write `<workspace>/src/content/landing.json`.

    Returns (path, content_dict). Creates parent dirs as needed.
    """
    content = build_landing_content(brief)
    target_dir = os.path.join(workspace_path, "src", "content")
    os.makedirs(target_dir, exist_ok=True)
    target_path = os.path.join(target_dir, "landing.json")
    with open(target_path, "w", encoding="utf-8") as fh:
        json.dump(content, fh, indent=2, ensure_ascii=False)
    logger.info(
        "write_landing_content: wrote %s (sections=%d, nav=%d)",
        target_path, len(content["sections"]), len(content["nav"]),
    )
    return target_path, content


# ── Derivations ──────────────────────────────────────────────────────

def _derive_nav(sections: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Build header nav from sections, skipping hero/footer/cta/newsletter."""
    nav: list[dict[str, str]] = []
    for s in sections:
        stype = (s.get("type") or "").lower()
        if stype in _NOT_IN_NAV:
            continue
        label = _nav_label(s)
        if not label:
            continue
        nav.append({"label": label, "href": f"#{s['id']}"})
    # Cap at 6 to keep the header from wrapping
    return nav[:6]


_NAV_LABEL_NOISE = {
    "section", "hook", "showcase", "incentive", "block", "band", "details",
    "info", "area", "panel", "module", "page", "view",
}

# Domain-aware single-word labels for common section types. Used to compress
# multi-word IDs ("accommodations-showcase" → "Stays") when the brief did not
# supply an explicit nav_label.
_TYPE_NAV_FALLBACK = {
    "accommodations": "Stays",
    "rooms": "Rooms",
    "properties": "Properties",
    "menu": "Menu",
    "experiences": "Experiences",
    "gallery": "Gallery",
    "philosophy": "About",
    "story": "Story",
    "about": "About",
    "testimonials": "Reviews",
    "social_proof": "Reviews",
    "reviews": "Reviews",
    "features": "Features",
    "pricing": "Pricing",
    "faq": "FAQ",
    "stats": "Numbers",
    "team": "Team",
    "press": "Press",
    "locations": "Locations",
    "hours": "Hours",
    "contact": "Contact",
    "contact_form": "Contact",
    "reservation": "Book",
    "booking_form": "Book",
    "booking": "Book",
    "how_it_works": "How",
    "process": "How",
    "value_prop": "Why",
    "benefits": "Benefits",
    "integrations": "Integrations",
    "comparison": "Compare",
}


def _nav_label(section: dict[str, Any]) -> str:
    """Pick a short human label (1-2 words, ≤14 chars) for the nav item.

    Order of preference:
      1. Brief-supplied `nav_label` (already short and curated).
      2. A canonical single-word fallback for the section's type.
      3. Cleaned-up section id with noise words ("section", "hook", …) removed.
    """
    explicit = (section.get("nav_label") or "").strip()
    if explicit:
        return explicit[:14]

    stype_raw = (section.get("type") or "").strip().lower()
    stype_key = stype_raw.replace("-", "_")
    if stype_key in _TYPE_NAV_FALLBACK:
        return _TYPE_NAV_FALLBACK[stype_key]

    sid_words = [
        w for w in (section.get("id") or "").replace("_", " ").replace("-", " ").split()
        if w and w.lower() not in _NAV_LABEL_NOISE
    ]
    if sid_words:
        # Prefer the LAST meaningful word ("about-philosophy" → "Philosophy",
        # "direct-booking-incentive" → "Booking"). One word, never two.
        return sid_words[-1].title()[:14]

    stype_words = [
        w for w in stype_raw.replace("_", " ").replace("-", " ").split()
        if w and w not in _NAV_LABEL_NOISE
    ]
    if stype_words:
        return stype_words[-1].title()[:14]

    head = (section.get("headline") or "").strip()
    return head[:14]


def _derive_footer(brand: str, sections: list[dict[str, Any]]) -> dict[str, Any]:
    """Derive footer link columns from non-hero/non-footer sections."""
    links = []
    for s in sections:
        stype = (s.get("type") or "").lower()
        if stype in {"hero", "footer"}:
            continue
        label = _nav_label(s)
        if label:
            links.append({"label": label, "href": f"#{s['id']}"})
    return {
        "brand": brand,
        "links": links[:8],
    }
