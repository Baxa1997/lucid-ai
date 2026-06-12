"""Page-per-call codegen for multi-page websites.

One Claude call per page. The call returns a structured response with all
files for that page in a single envelope:

  • src/app/<slug>/page.jsx          — route file (or src/app/page.jsx for home)
  • src/components/sections/<Slug><Type>Section.jsx — N section files

Why page-per-call (not section-per-call):
  • Coherence within a page comes from a single context (one rhythm, one
    type ramp, one icon style, one section-transition pattern).
  • Cross-page coherence comes from the shared design-system prompt prefix
    (cacheable, identical across calls).
  • Fewer calls overall: 14-page site → 14 calls instead of ~84 sections.
    Input tokens (= design-system context) are paid per call, so fewer
    calls means less redundancy → cheaper.
  • Output size per page (~22-28K tokens) sits comfortably inside Sonnet
    4.6's 64K native output cap.

This module is a SIBLING of landing_section_codegen, not a replacement.
The single-page landing path keeps using section-per-call (it works and
is well-tested). Multi-page sites use this module via project_pipeline.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)

# Per-page output budget. A page with 6-8 sections of moderate complexity
# fits in 24-28K tokens of JSX. 32K leaves headroom for thorough copy and
# the route file. Sonnet 4.6 native cap is 64K — never approach it.
_PAGE_MAX_TOKENS = 64000

# Hard timeout per page call. If exceeded, the orchestrator falls back to
# a template skeleton for this page only — other pages keep going.
_PAGE_TIMEOUT_SECONDS = 600.0  # 10 min — see page_generator.py for rationale


# ── helpers ─────────────────────────────────────────────────────────


def _route_path_for(slug: str) -> str:
    """Map a page slug to its Next.js App Router file path.

    "" or "home" or "/" → src/app/page.jsx
    "pricing"          → src/app/pricing/page.jsx
    "blog/post"        → src/app/blog/post/page.jsx (nested)
    """
    norm = (slug or "").strip().strip("/").lower()
    if norm in ("", "home", "index"):
        return "src/app/page.jsx"
    return f"src/app/{norm}/page.jsx"


def _section_component_name(slug: str, section: dict[str, Any]) -> str:
    """Build a unique PascalCase component name for a section file.

    Includes the page slug as a prefix so two pages with overlapping
    section types (both have a hero, both have an FAQ) don't collide
    on the filename. Dedupes if the section id already starts with
    the slug (common: page=pricing, id=pricing_hero → PricingHero).

    "pricing" + {id: "hero"}         → "PricingHeroSection"
    "pricing" + {id: "pricing_hero"} → "PricingHeroSection" (no double-prefix)
    ""        + {id: "faq"}          → "HomeFaqSection"
    """
    slug_norm = (slug or "home").strip().strip("/").lower() or "home"
    sec_id = (section.get("id") or section.get("type") or "section").strip().lower()

    # Dedupe: if section id already starts with the slug, drop the leading slug
    # token from the id so we don't produce "PricingPricingHero".
    sec_tokens = re.split(r"[\s_\-/]+", sec_id)
    slug_tokens = re.split(r"[\s_\-/]+", slug_norm)
    while sec_tokens and slug_tokens and sec_tokens[0] == slug_tokens[0]:
        sec_tokens.pop(0)
        slug_tokens.pop(0)
    if not sec_tokens:
        # id was identical to slug — fall back to the section type
        sec_tokens = re.split(r"[\s_\-/]+", (section.get("type") or "section").lower())

    all_parts = re.split(r"[\s_\-/]+", slug_norm) + sec_tokens
    pascal = "".join(p.capitalize() for p in all_parts if p)
    if not pascal:
        pascal = "PageSection"
    if not pascal.endswith("Section"):
        pascal = f"{pascal}Section"
    return pascal


def _section_file_path(slug: str, section: dict[str, Any]) -> str:
    component = _section_component_name(slug, section)
    return f"src/components/sections/{component}.jsx"


# ── prompt builders ────────────────────────────────────────────────


def _system_prompt_page(
    *,
    brand_name: str,
    motif: str,
    palette: dict,
    typography: dict,
    design_system: dict,
    personality: dict | None = None,
    references: list[dict] | None = None,
    design_tokens: dict | None = None,
) -> str:
    """System prompt — the shared prefix across all page calls.

    Contains design-system tokens + visual rules that are IDENTICAL across
    every page in the site. This is what gets prompt-cached so we pay
    full price for it once and a tenth of full price for the next 13
    page calls in the same site.
    """
    palette_lines = "\n".join(f"  --{k}: {v};" for k, v in (palette or {}).items())
    ds = design_system or {}
    dt = design_tokens or {}
    pers = personality or {}

    pers_block = ""
    if pers:
        vibe = ", ".join(pers.get("vibe_keywords") or [])
        pers_block = (
            f"\nPERSONALITY (tune copy tone, color usage, motion intensity):\n"
            f"  Tone: {pers.get('tone', 'confident')}\n"
            f"  Vibe keywords: {vibe or 'modern, clear'}\n"
            f"  Energy: {pers.get('energy', 'medium')}\n"
        )

    ref_block = ""
    if references:
        lines = []
        for r in references[:4]:
            name = r.get("name") or r.get("url", "")
            why = r.get("why") or ""
            lines.append(f"  • {name} — {why}")
        if lines:
            ref_block = (
                "\nREFERENCE SITES (research-grounded — emulate these patterns):\n"
                + "\n".join(lines) + "\n"
            )

    radius_token = (ds.get("radius") or dt.get("radius") or "0.5rem").strip()
    spacing_scale = (ds.get("spacing_scale") or dt.get("spacing_scale") or "default").strip()

    return f"""You are a senior frontend engineer writing production React (Next.js App Router) code.
You generate ONE complete page in a multi-page website. Output every file in a single response
via the write_project_files tool.

BRAND: {brand_name or '(unnamed)'}
MOTIF: {motif or 'minimal'}

DESIGN TOKENS (declared in globals.css; never override, only USE):
  Palette:
{palette_lines or '  (default)'}
  Border radius: var(--radius) = {radius_token}
  Spacing scale: {spacing_scale}
  Typography: heading={(typography or {}).get('heading', 'inherit')}, body={(typography or {}).get('body', 'inherit')}
{pers_block}{ref_block}
NON-NEGOTIABLE RULES:
  1. Tailwind ONLY for styling. Never inline CSS variables (no `style={{}}` with --color-*).
     Use Tailwind classes that map to tokens: bg-primary, text-foreground, bg-muted,
     border-border, rounded-md / rounded-lg / rounded-2xl (NEVER rounded-none for cards/buttons),
     shadow-sm/md/lg.
  2. Components are function components, default-exported, no TypeScript.
  3. shadcn/ui primitives live at @/components/ui/* — import from there
     (e.g. `import {{ Button }} from "@/components/ui/button"`).
     Section files MUST import from "@/components/ui/<lowercase>" not relative paths.
  4. Every page reads its copy from the per-page content JSON:
     `import content from "@/content/<slug>.json";`
     Section files receive `content` as a prop (they are NOT supposed to fetch JSON themselves).
  5. Every clickable affordance (buttons, CTAs, cards) must have a visible border-radius —
     match the design system token (rounded-md or rounded-lg, never sharp 90° corners).
  6. Use Image from "next/image" for every <img>; never raw <img>.
  7. Lucide-react for icons. Never reference brand-mark icons (Twitter, Instagram, etc.) —
     these are auto-fixed downstream.
  8. Sections must take a single `content` prop. The page route file imports the JSON once
     and threads it: `<HeroSection content={{content.sections.hero}} />`.
  9. CARD GRIDS MUST WRAP — never compress cards into a single non-wrapping row.
     Cards squeezed below natural content width get clipped letters (e.g. "V T" instead of
     "Walking Tours"). Required patterns:
       • PREFER responsive grid: `grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-6`.
       • OR auto-fit fluid grid: `grid grid-cols-[repeat(auto-fit,minmax(240px,1fr))] gap-6`.
       • IF you use flex for a card collection, you MUST add `flex-wrap` AND `min-w-[200px]`
         on each card. Never `flex` without `flex-wrap` for cards.
       • NEVER apply `overflow-hidden` to the card TEXT container (title + description div).
         `overflow-hidden` is only for image containers and decorative blobs.
       • NEVER apply `whitespace-nowrap` to card titles or descriptions — let titles wrap to 2 lines.
       • NEVER fix card widths with `w-24` / `w-32` / `w-40` / `w-48` for content cards. Use
         `w-full` inside a grid cell, or `min-w-[200px]` inside a flex row.
       • Use `line-clamp-2` / `line-clamp-3` only on long DESCRIPTIONS, never on titles.

OUTPUT CONTRACT:
  • Call write_project_files exactly once.
  • Include the route file (src/app/[<slug>/]page.jsx) PLUS one component file per section.
  • Each section file is self-contained — no cross-section imports beyond shared @/components/ui.
  • Total output across all files for this page MUST stay under ~28K tokens. If a section
    is unusually large, split it into smaller sub-components within the same file.
  • DO NOT write the content JSON file (e.g. src/content/<slug>.json) — that file is
    written by an upstream pipeline and will already exist when this page runs. You only
    write .jsx component code.
  • Every file's `content` field MUST be a STRING (full source code), never a JSON object.

Coherence within this page is your responsibility. Consistent type ramp, consistent
spacing rhythm, consistent icon weight, consistent corner radius across all sections.
"""


def _user_prompt_page(
    *,
    page_meta: dict[str, Any],
    sections: list[dict[str, Any]],
    voice_context: dict[str, Any] | None = None,
) -> str:
    """User prompt — the per-page specifics.

    Tells Claude:
      • which page this is (slug, goal, primary CTA)
      • which sections it contains, in order, with their content already populated
      • exactly which file paths to write
    """
    slug = (page_meta.get("slug") or "").strip("/")
    title = page_meta.get("title") or page_meta.get("nav_label") or slug or "Home"
    page_goal = page_meta.get("page_goal") or ""
    primary_cta = page_meta.get("primary_cta") or {}
    cta_label = primary_cta.get("label") or ""
    cta_href = primary_cta.get("href") or "#"

    route_path = _route_path_for(slug)
    content_slug = (slug or "home").lower() or "home"

    lines = [
        f"PAGE: {title}",
        f"  slug: {slug or '(home)'}",
        f"  route file: {route_path}",
        f"  content import: @/content/{content_slug}.json",
        f"  page goal: {page_goal}",
    ]
    if cta_label:
        lines.append(f"  primary CTA: \"{cta_label}\" → {cta_href}")

    if voice_context:
        tone = voice_context.get("tone") or ""
        sample = voice_context.get("sample") or ""
        if tone:
            lines.append(f"  voice tone: {tone}")
        if sample:
            lines.append(f"  voice sample: {sample[:160]}")

    lines.append("")
    lines.append(f"SECTIONS (in render order, {len(sections)} total).")
    lines.append(
        "Each section's CONTENT is shown verbatim below — your JSX must read these "
        "exact field names from the `content` prop (e.g. `content.headline`, "
        "`content.items[i].title`). Do not invent new field names."
    )
    import json as _json
    for i, sec in enumerate(sections, start=1):
        sid = sec.get("id") or sec.get("type") or f"section_{i}"
        stype = sec.get("type") or sid
        comp_name = _section_component_name(slug, sec)
        comp_path = _section_file_path(slug, sec)

        # Strip the id/type metadata before serializing — those are routing
        # fields, not content. Claude only needs the content shape.
        content_only = {k: v for k, v in sec.items() if k not in ("id", "type")}
        try:
            content_json = _json.dumps(content_only, indent=2, ensure_ascii=False)
        except Exception:
            content_json = "{}"

        lines.append("")
        lines.append(f"  [{i}] {comp_name}  →  {comp_path}")
        lines.append(f"      type: {stype}")
        lines.append(f"      content (read these EXACT fields from the `content` prop):")
        for cl in content_json.splitlines():
            lines.append(f"      {cl}")

    lines.append("")
    lines.append("REQUIRED FILES TO WRITE:")
    lines.append(f"  1. {route_path}")
    lines.append("     - default-exports a Page() component")
    lines.append(f"     - imports content from @/content/{content_slug}.json (single import)")
    lines.append("     - imports each Section component below")
    lines.append("     - renders sections in the listed order, threading content[\"<id>\"] as the `content` prop")
    for i, sec in enumerate(sections, start=2):
        comp_path = _section_file_path(slug, sec)
        lines.append(f"  {i}. {comp_path}")
        lines.append(f"     - default-exports a {_section_component_name(slug, sec)}({{ content }}) component")
        lines.append("     - reads ALL copy from `content.*` — never inlines strings")

    lines.append("")
    lines.append("Generate complete, runnable JSX. No placeholder TODO comments. No explanations outside the tool call.")

    return "\n".join(lines)


# ── core call ──────────────────────────────────────────────────────


async def generate_page(
    *,
    page_meta: dict[str, Any],
    sections: list[dict[str, Any]],
    brand_name: str,
    motif: str,
    palette: dict,
    typography: dict,
    design_system: dict,
    personality: dict | None = None,
    references: list[dict] | None = None,
    design_tokens: dict | None = None,
    voice_context: dict[str, Any] | None = None,
    api_key: str,
    websocket: Any = None,
    max_tokens: int = _PAGE_MAX_TOKENS,
) -> dict[str, Any] | None:
    """Generate one complete page (route + section components) in one Claude call.

    Returns:
        {
            "page_slug":   "<slug>",                # "" for home
            "route_path":  "src/app/[slug/]page.jsx",
            "files":       [{"path": "...", "content": "..."}, ...],
        }
        or None on failure.

    The first file in `files` is the route; the rest are section components.
    """
    from app.services.llm_json_client import call_claude_for_json

    slug = (page_meta.get("slug") or "").strip("/")

    sys_p = _system_prompt_page(
        brand_name=brand_name,
        motif=motif,
        palette=palette,
        typography=typography,
        design_system=design_system,
        personality=personality,
        references=references,
        design_tokens=design_tokens,
    )
    usr_p = _user_prompt_page(
        page_meta=page_meta,
        sections=sections,
        voice_context=voice_context,
    )

    label = f"page_codegen[{slug or 'home'}]"
    logger.info("%s: starting (sections=%d, max_tokens=%d)", label, len(sections), max_tokens)

    try:
        result = await asyncio.wait_for(
            call_claude_for_json(
                system_prompt=sys_p,
                user_prompt=usr_p,
                api_key=api_key,
                websocket=websocket,
                max_tokens=max_tokens,
            ),
            timeout=_PAGE_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        logger.warning("%s: TIMEOUT after %.0fs", label, _PAGE_TIMEOUT_SECONDS)
        return None
    except Exception as exc:
        logger.warning("%s: codegen exception — %s", label, exc)
        return None

    if not result or "files" not in result:
        logger.warning("%s: empty/invalid Claude result", label)
        return None

    files: list[dict[str, str]] = []
    for f in (result.get("files") or []):
        path = (f.get("path") or "").strip().lstrip("/")
        raw_content = f.get("content")
        if not path:
            continue
        # Reject content JSON files — those are written upstream, not by codegen.
        if path.startswith("src/content/") and path.endswith(".json"):
            logger.info("%s: skipping content file Claude tried to write: %s", label, path)
            continue
        # Enforce string content — Claude occasionally returns dicts here.
        if not isinstance(raw_content, str) or not raw_content.strip():
            logger.warning(
                "%s: dropping file with non-string or empty content: %s (type=%s)",
                label, path, type(raw_content).__name__,
            )
            continue
        files.append({"path": path, "content": raw_content})

    if not files:
        logger.warning("%s: Claude returned 0 valid files", label)
        return None

    route_path = _route_path_for(slug)
    has_route = any(f["path"] == route_path for f in files)
    if not has_route:
        logger.warning(
            "%s: Claude omitted the route file (%s) — got: %s",
            label, route_path, [f["path"] for f in files],
        )
        return None

    # Sort files so the route comes first. Caller convenience.
    files.sort(key=lambda f: (0 if f["path"] == route_path else 1, f["path"]))

    logger.info("%s: ok — %d files (route + %d sections)", label, len(files), len(files) - 1)
    return {
        "page_slug": slug,
        "route_path": route_path,
        "files": files,
    }


# ── fan-out ────────────────────────────────────────────────────────


async def generate_pages_many(
    *,
    pages: list[dict[str, Any]],          # list of {page_meta, sections} dicts
    brand_name: str,
    motif: str,
    palette: dict,
    typography: dict,
    design_system: dict,
    personality: dict | None = None,
    references: list[dict] | None = None,
    design_tokens: dict | None = None,
    api_key: str,
    websocket: Any = None,
    concurrency: int = 6,
) -> list[dict[str, Any] | None]:
    """Fan out generate_page across N pages with bounded concurrency.

    Returns a list aligned with `pages` — each entry is the generate_page
    result dict, or None for pages that failed. Caller decides how to
    handle Nones (skeleton fallback, retry, etc).
    """
    sem = asyncio.Semaphore(concurrency)

    async def _one(idx: int, page_input: dict[str, Any]) -> dict[str, Any] | None:
        page_meta = page_input.get("page_meta") or {}
        sections = page_input.get("sections") or []
        voice_context = page_input.get("voice_context")
        async with sem:
            # generate_page catches its own Claude-call errors, but its
            # prompt builders run before that try block — a malformed
            # page_meta used to raise straight through gather() and lose
            # EVERY page. The documented contract is "None for pages that
            # failed", so enforce it here.
            try:
                return await generate_page(
                    page_meta=page_meta,
                    sections=sections,
                    brand_name=brand_name,
                    motif=motif,
                    palette=palette,
                    typography=typography,
                    design_system=design_system,
                    personality=personality,
                    references=references,
                    design_tokens=design_tokens,
                    voice_context=voice_context,
                    api_key=api_key,
                    websocket=websocket,
                )
            except Exception as exc:
                logger.warning(
                    "page_codegen[%s]: unhandled exception — %s",
                    (page_meta.get("slug") or "home"), exc,
                )
                return None

    tasks = [_one(i, p) for i, p in enumerate(pages)]
    return await asyncio.gather(*tasks, return_exceptions=False)


# ── disk writer ────────────────────────────────────────────────────


def write_page_files(workspace_path: str, page_result: dict[str, Any]) -> list[str]:
    """Write the file map from generate_page() to disk.

    Returns the list of absolute paths written (for logging / diagnostics).
    """
    written: list[str] = []
    for f in page_result.get("files") or []:
        rel = f["path"].lstrip("/")
        abs_path = os.path.join(workspace_path, rel)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "w", encoding="utf-8") as fh:
            fh.write(f["content"])
        written.append(abs_path)
    return written
