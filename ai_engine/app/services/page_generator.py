"""Per-page Claude generator for the unified website pipeline.

Generates ONE complete page (route shell + all its section components) in
a single Claude call. Designed for parallel execution — pages have no
runtime dependency on each other, only on the deterministic foundation
files (globals.css, site.js, navigation.js, design-system.js).

Convention:
  Each page's components live under   src/components/pages/<slug>/
  Page composition file lives at      src/app/<route>/page.js
  Home page composition file is       src/app/page.js  (slug="home")

This isolates pages so a failure in one doesn't break others, and so each
page can have its own visual rhythm (e.g. /menu may use food-photography
heavy hero; /about may use editorial-asymmetric hero — both legitimate).

The visual_dna is the LEAD directive — every page receives the same DNA
so all pages share the same brand DNA, just expressed for that page's
purpose.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Per-page generation params. Each page emits 1 composition file + 3-8
# section components, totaling ~2-6k tokens — 16k gives plenty of headroom.
_PAGE_MAX_TOKENS = 16000
_PAGE_TIMEOUT_S = 120.0   # 1 call, 1 page, all its sections
_PAGE_MAX_ATTEMPTS = 2


_VALID_COMPONENT = re.compile(r"^[A-Z][A-Za-z0-9_]*$")


def _slug_from_route(route: str) -> str:
    """Convert '/menu' → 'menu', '/' → 'home', '/private-events' → 'private-events'."""
    r = (route or "").strip().strip("/")
    return r or "home"


def _slug_pascal(slug: str) -> str:
    """'private-events' → 'PrivateEvents'."""
    parts = re.split(r"[^A-Za-z0-9]+", slug)
    return "".join(p[:1].upper() + p[1:] for p in parts if p) or "Home"


def _section_component_name(slug: str, section_type: str) -> str:
    """Build a per-page section component name.

    Examples:
      slug='home', type='hero'      → 'HomeHero'
      slug='menu', type='showcase'  → 'MenuShowcase'
      slug='about', type='story'    → 'AboutStory'
    """
    page_pas = _slug_pascal(slug)
    sec_pas = _slug_pascal(section_type or "section")
    name = page_pas + sec_pas
    return name if _VALID_COMPONENT.match(name) else "PageSection"


def _section_anatomy_for(visual_dna: dict, section_type: str) -> str:
    """Pull the structural anatomy spec for a given section type from
    the extracted visual_dna. Returns empty string if missing."""
    anatomies = (visual_dna or {}).get("section_anatomies") or {}
    return (anatomies.get(section_type) or "").strip()


def _build_system_prompt(
    *,
    brand_name: str,
    tagline: str,
    domain: str,
    visual_dna: dict,
    foundation_imports: dict,
) -> str:
    """System prompt: brand DNA + foundation contract. Identical across all pages
    so every page receives the same design grammar — that's what creates
    cross-page visual cohesion."""
    vd = visual_dna or {}
    intensity = (vd.get("cultural_intensity") or "bold").strip().lower()
    motifs = vd.get("decorative_motifs") or []
    textures = vd.get("signature_textures") or []
    icons = vd.get("iconography_anchors") or []
    photo = (vd.get("photography_style") or "").strip()
    palette_emph = (vd.get("cultural_palette_emphasis") or "").strip()
    type_voice = (vd.get("typography_voice") or "").strip()
    layout_sig = (vd.get("layout_signature") or "").strip()

    intensity_note = (
        "Cultural cues sit in ACCENT POSITIONS only. The page reads "
        "modern-upscale with cultural FLAVOR — restraint stays."
        if intensity == "subtle"
        else "Cultural cues take a STRONGER role — larger textures, decorative "
             "motifs as dividers, iconography woven into headings, full-bleed "
             "cultural patterns where appropriate. UNMISTAKABLY of this category."
    )

    motifs_block = ""
    if motifs:
        motifs_block = "\n".join(f"  - {m}" for m in motifs[:6])
        motifs_block = f"\n\nDECORATIVE MOTIFS (use as accents — pick ones that fit each section):\n{motifs_block}"
    textures_block = ""
    if textures:
        textures_block = "\n".join(f"  - {t}" for t in textures[:4])
        textures_block = f"\n\nSIGNATURE TEXTURES:\n{textures_block}"
    icons_block = ""
    if icons:
        icons_block = ", ".join(icons[:6])
        icons_block = f"\n\nICONOGRAPHY ANCHORS: {icons_block}"

    foundation_block = "\n".join(
        f"  - {key} — {desc}" for key, desc in foundation_imports.items()
    )

    return f"""You are a senior frontend designer-engineer generating ONE COMPLETE PAGE for a brand-coherent multi-page website.

BRAND
  Name: {brand_name}
  Tagline: {tagline}
  Domain: {domain}

VISUAL DNA — THIS IS THE PRIMARY DESIGN DIRECTIVE
  Cultural intensity: {intensity}
    → {intensity_note}

  Cultural palette emphasis: {palette_emph or '(use foundation palette as-is)'}
  Typography voice: {type_voice or '(neutral)'}
  Photography style: {photo or '(use brand-appropriate stock)'}
  Layout signature: {layout_sig or '(modern responsive grid)'}{motifs_block}{textures_block}{icons_block}

FOUNDATION ALREADY EXISTS — IMPORT FROM THESE (do NOT regenerate):
{foundation_block}

OUTPUT CONTRACT
  Return JSON: {{"files": [{{"path": "src/...", "content": "..."}}, ...]}}
  Every file is a complete, self-contained JSX file.
  Use Tailwind CSS classes only — NO inline styles, NO css-in-js, NO css variable strings.
  Import design tokens from '@/lib/design-system' as `ds` for spacing/typography classes:
    {{ds.section}}     → section vertical padding
    {{ds.container}}   → max-width container
    {{ds.heading}}     → display font class
    {{ds.body}}        → body font class
    {{ds.card}}        → card styling
    {{ds.buttonPrimary}}, {{ds.buttonSecondary}}, {{ds.buttonGhost}}
    {{...ds.motion}}   → framer-motion in-view animation props

QUALITY BAR
  - Each section MUST visibly reflect VISUAL_DNA in ≥2 ways (color emphasis, motif, iconography, typography, layout signature).
  - A generic "centered text + 3-col grid + photo" output that could fit any brand is a FAILURE.
  - Make this page feel cohesive — section spacing, color flow, and motif placement should feel like ONE designed unit, not Lego.
  - No lorem ipsum. Write real content matching the brand voice.
"""


def _build_user_prompt(
    *,
    page: dict,
    slug: str,
    section_specs: list[dict],
    visual_dna: dict,
) -> str:
    """User prompt: what THIS specific page should produce."""
    component_pages_dir = f"src/components/pages/{slug}"
    route = (page.get("route") or page.get("path") or "/").strip()
    page_title = (page.get("title") or _slug_pascal(slug)).strip()
    page_purpose = (page.get("purpose") or page.get("description") or "").strip()

    section_lines: list[str] = []
    for spec in section_specs:
        comp = spec["component"]
        s_type = spec["type"]
        anatomy = spec.get("anatomy") or ""
        purpose = spec.get("purpose") or ""
        line = f"  • {comp}  (type={s_type})"
        if purpose:
            line += f"  — {purpose}"
        if anatomy:
            line += f"\n      anatomy: {anatomy}"
        section_lines.append(line)

    section_block = "\n".join(section_lines) if section_lines else "  (none)"

    # Compose the import + JSX skeleton hint for the page.js composition file
    page_file = "src/app/page.js" if slug == "home" else f"src/app/{slug}/page.js"

    return f"""GENERATE THE COMPLETE "{page_title}" PAGE FOR ROUTE {route}

PAGE PURPOSE
{page_purpose or '(general page for this route)'}

SECTIONS TO BUILD ({len(section_specs)} total) — generate each as its own .jsx file under {component_pages_dir}/:
{section_block}

REQUIRED OUTPUT FILES
1. {page_file}
   - Imports each section component from {component_pages_dir.replace('src/', '@/')}
   - Exports the page composition (Next.js app router pattern)
   - Imports {{ siteConfig }} from '@/config/site' if it sets metadata
   - Renders sections inside a <main> wrapper in the order listed above

2. One .jsx file per section under {component_pages_dir}/ — exact names listed above

CRITICAL
  - Stay strictly inside the page's design (the anatomies above ARE the spec).
  - Every section reflects VISUAL_DNA visibly (motifs/textures/iconography/typography).
  - All section components must be DEFAULT EXPORTS named exactly as listed.
  - Page composition file does ONLY imports + a HomePage/AboutPage/etc default export.

Return JSON with the files array — that's it.
"""


async def generate_one_page(
    *,
    page: dict,
    visual_dna: dict,
    brand_name: str,
    tagline: str,
    domain: str,
    api_key: str,
    websocket: Any = None,
    foundation_imports: dict[str, str] | None = None,
) -> list[dict] | None:
    """Generate one complete page (composition + sections) in a single Claude call.

    Returns a list of {path, content} dicts on success, or None on failure
    (all attempts exhausted). Caller writes the files to disk.

    `page` shape:
      {
        "route": "/menu",      # or "path"
        "title": "Our Menu",
        "purpose": "Show seasonal menu with cultural narrative",
        "sections": [
          {"type": "hero",      "purpose": "Open with seasonal headline"},
          {"type": "menu",      "purpose": "Display dishes with prices"},
          {"type": "story",     "purpose": "Chef and farm sourcing"},
        ],
      }
    """
    from app.services.project_generator import call_claude_for_json

    route = (page.get("route") or page.get("path") or "/").strip()
    slug = _slug_from_route(route)
    raw_sections = page.get("sections") or []

    # Build section specs with per-page component names + anatomy from DNA
    section_specs: list[dict] = []
    for s in raw_sections:
        if not isinstance(s, dict):
            continue
        s_type = (s.get("type") or s.get("name") or "section").strip().lower()
        comp = _section_component_name(slug, s_type)
        section_specs.append({
            "type": s_type,
            "component": comp,
            "purpose": (s.get("purpose") or s.get("description") or "").strip(),
            "anatomy": _section_anatomy_for(visual_dna, s_type),
        })

    if not section_specs:
        logger.warning("page_generator: page %s has no sections — skipping", route)
        return None

    foundation = foundation_imports or {
        "globals.css":          "Theme palette + Google Fonts (do NOT regenerate)",
        "design-system.js":     "ds tokens for spacing/typography (import { ds } from '@/lib/design-system')",
        "site.js":              "{ siteConfig } from '@/config/site' (brand name, tagline, URL)",
        "navigation.js":        "{ mainNav, footerNav } from '@/config/navigation'",
        "MarketingHeader.jsx":  "Wraps every page (rendered by layout.jsx)",
        "MarketingFooter.jsx":  "Wraps every page (rendered by layout.jsx)",
    }

    sys_p = _build_system_prompt(
        brand_name=brand_name, tagline=tagline, domain=domain,
        visual_dna=visual_dna, foundation_imports=foundation,
    )
    usr_p = _build_user_prompt(
        page=page, slug=slug,
        section_specs=section_specs, visual_dna=visual_dna,
    )

    last_failure = "unknown"
    for attempt in range(1, _PAGE_MAX_ATTEMPTS + 1):
        try:
            result = await asyncio.wait_for(
                call_claude_for_json(
                    system_prompt=sys_p,
                    user_prompt=usr_p,
                    api_key=api_key,
                    websocket=websocket,
                    max_tokens=_PAGE_MAX_TOKENS,
                ),
                timeout=_PAGE_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            last_failure = f"timeout after {_PAGE_TIMEOUT_S}s"
            logger.warning(
                "page_generator: page %s timeout on attempt %d/%d",
                route, attempt, _PAGE_MAX_ATTEMPTS,
            )
            continue
        except Exception as exc:
            last_failure = f"exception: {exc}"
            logger.warning(
                "page_generator: page %s threw on attempt %d/%d — %s",
                route, attempt, _PAGE_MAX_ATTEMPTS, exc,
            )
            continue

        if not result or "files" not in result:
            last_failure = "no files key in response"
            continue

        files = result.get("files") or []
        if not files:
            last_failure = "empty files array"
            continue

        # Validate: must contain the page.js composition + at least one section
        valid_files: list[dict] = []
        saw_page_js = False
        for f in files:
            if not isinstance(f, dict):
                continue
            path = (f.get("path") or "").strip()
            content = (f.get("content") or "")
            if not path or not content:
                continue
            if path.endswith("page.js") or path.endswith("page.jsx"):
                saw_page_js = True
            valid_files.append({"path": path, "content": content})

        if not saw_page_js or len(valid_files) < 2:
            last_failure = f"missing composition file or section files (got {len(valid_files)})"
            logger.warning(
                "page_generator: page %s attempt %d/%d — %s",
                route, attempt, _PAGE_MAX_ATTEMPTS, last_failure,
            )
            continue

        logger.info(
            "page_generator: page %s ok on attempt %d — %d files generated",
            route, attempt, len(valid_files),
        )
        return valid_files

    logger.error(
        "page_generator: page %s FAILED after %d attempts — last_failure=%s",
        route, _PAGE_MAX_ATTEMPTS, last_failure,
    )
    return None


def plan_section_components_for_page(
    page: dict,
    slug: str | None = None,
) -> list[str]:
    """Helper for callers (e.g. homepage builder) that need to know what
    component names a page WOULD generate, without running Claude.

    Returns the list of per-page component names in render order.
    """
    s = slug or _slug_from_route(page.get("route") or page.get("path") or "/")
    return [
        _section_component_name(s, sec.get("type") or sec.get("name") or "section")
        for sec in (page.get("sections") or [])
        if isinstance(sec, dict)
    ]
