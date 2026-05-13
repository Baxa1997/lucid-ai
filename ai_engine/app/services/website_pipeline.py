"""Unified multi-page website pipeline (v2).

End-to-end orchestrator replacing the legacy Phase 1/2/3 monolithic flow
for consumer_website and related multi-page archetypes:

  Stage 1 — analyze_intent           (Gemini Flash)
  Stage 2 — domain + design research (Gemini, parallel)
  Stage 3 — visual_dna extraction    (Gemini Pro, single source of design truth)
  Stage 4 — website plan             (Gemini Flash → pages + sections per page)
  Stage 5 — DETERMINISTIC FOUNDATION (no LLM)
              globals.css from palette + fonts
              design-system.js tokens
              site.js (brand + tagline)
              navigation.js (routes from plan)
              Route shells for inner pages
  Stage 6 — PARALLEL CREATIVE        (Claude × N pages + header + footer)
              Each call receives the same visual_dna → cross-page cohesion
  Stage 7 — VERIFICATION (build check + import audit)  [next step]

Returns True on success (the dev server should be runnable), False otherwise.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


async def _send(websocket, kind: str, message: str) -> None:
    if websocket is None:
        return
    try:
        await websocket.send_json({"type": kind, "message": message})
    except Exception:
        pass


async def _phase(websocket, phase: int, title: str, desc: str, status: str) -> None:
    if websocket is None:
        return
    try:
        await websocket.send_json({
            "type": "task_phase",
            "phase": phase, "title": title,
            "description": desc, "status": status,
        })
    except Exception:
        pass


async def run_website_pipeline(
    *,
    description: str,
    classification: dict[str, Any],
    workspace_path: str,
    validated: dict[str, Any],
    websocket: Any = None,
    chat_session_id: str = "",
) -> bool:
    """Run the full website pipeline. Returns True on success.

    Mirrors the API surface of `run_landing_pipeline` so the orchestrator
    can dispatch to either based on layout_archetype.
    """
    anthropic_key = validated.get("anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY", "")
    if not anthropic_key:
        await _send(websocket, "error", "❌ Missing ANTHROPIC_API_KEY")
        return False

    # ── Stage 1: Intent ─────────────────────────────────────────────
    await _phase(websocket, 1, "Preparing workspace", "Workspace ready", "done")
    await _phase(websocket, 3, "Researching project", "Analyzing intent + culture…", "active")
    await _send(websocket, "progress", "🧠 Stage 1/6 — Analyzing intent…")

    from app.services.landing_intent import analyze_intent
    from knowledge.loader import extract_clarify_context

    _, clean_description = extract_clarify_context(description)

    try:
        intent = await analyze_intent(clean_description, classification, timeout_s=60.0)
    except Exception as exc:
        logger.error("website_pipeline: intent failed — %s", exc, exc_info=True)
        await _send(websocket, "error", f"❌ Intent analysis failed: {exc}")
        return False

    logger.info(
        "website_pipeline: intent ok — category=%s geo=%s personality=%s",
        intent.get("business_category"), intent.get("geographic_specifics"),
        intent.get("brand_personality"),
    )

    # ── Stage 2: Research (parallel) ────────────────────────────────
    await _send(websocket, "progress", "🔎 Stage 2/6 — Researching domain + design (parallel)…")

    from app.services.landing_domain_research import run_domain_research
    from app.services.landing_design_research import run_design_research

    try:
        domain_res, design_res = await asyncio.gather(
            run_domain_research(intent, timeout_s=120.0),
            run_design_research(intent, timeout_s=120.0),
        )
    except Exception as exc:
        logger.error("website_pipeline: research failed — %s", exc, exc_info=True)
        await _send(websocket, "error", f"❌ Research failed: {exc}")
        return False

    # ── Stage 3: Visual_DNA extraction ──────────────────────────────
    await _send(websocket, "progress", "✨ Stage 3/6 — Extracting visual DNA (typography, palette, motifs)…")

    from app.services.landing_research_extract import extract_research_signals

    try:
        signals = await extract_research_signals(
            intent, domain_res, design_res, timeout_s=180.0,
        )
    except Exception as exc:
        logger.error("website_pipeline: visual_dna extract failed — %s", exc, exc_info=True)
        await _send(websocket, "error", f"❌ Visual DNA extraction failed: {exc}")
        return False

    visual_dna = signals.get("visual_dna") or {}
    anatomies = visual_dna.get("section_anatomies") or {}
    logger.info(
        "website_pipeline: visual_dna ok — intensity=%s anatomies=%d motifs=%d",
        visual_dna.get("cultural_intensity"),
        len(anatomies), len(visual_dna.get("decorative_motifs") or []),
    )
    if not anatomies:
        # Don't hard-fail — orchestrator will produce generic anatomies, still works
        logger.warning("website_pipeline: visual_dna has no anatomies — pages will be more generic")

    # ── Stage 4: Build plan ─────────────────────────────────────────
    await _send(websocket, "progress", "📋 Stage 4/6 — Planning pages + sections…")

    from app.services.website_plan import build_website_plan
    try:
        plan = await build_website_plan(
            clean_description, intent, visual_dna, timeout_s=60.0,
        )
    except Exception as exc:
        logger.error("website_pipeline: plan failed — %s", exc, exc_info=True)
        await _send(websocket, "error", f"❌ Plan build failed: {exc}")
        return False

    pages = plan.get("pages") or []
    if not pages:
        await _send(websocket, "error", "❌ Plan returned 0 pages")
        return False

    page_routes = [p.get("route") for p in pages]
    logger.info(
        "website_pipeline: plan ok — brand=%r pages=%d routes=%s",
        plan["brand"]["name"], len(pages), page_routes,
    )
    await _send(websocket, "progress", f"📋 Plan: {len(pages)} pages → {', '.join(page_routes)}")

    # ── Stage 5: Deterministic foundation ───────────────────────────
    await _send(websocket, "progress", "🛠️  Stage 5/6 — Building foundation (palette, tokens, nav)…")
    foundation_files = _build_foundation_files(plan, visual_dna, design_signal=signals.get("design") or {})
    foundation_written = 0
    for rel_path, content in foundation_files.items():
        try:
            abs_path = os.path.join(workspace_path, rel_path)
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
            with open(abs_path, "w", encoding="utf-8") as f:
                f.write(content)
            foundation_written += 1
        except Exception as exc:
            logger.warning("website_pipeline: failed to write %s — %s", rel_path, exc)
    logger.info("website_pipeline: foundation written — %d files", foundation_written)

    # ── Stage 6: Parallel creative (the big one) ────────────────────
    await _phase(websocket, 5, "Writing code",
                 f"Generating {len(pages)} pages + header + footer in parallel…", "active")
    await _send(websocket, "progress",
                f"⚡ Stage 6/6 — Generating {len(pages)} pages + chrome in parallel (Claude)…")

    from app.services.website_orchestrator import generate_website
    try:
        result = await generate_website(
            plan=plan, visual_dna=visual_dna,
            api_key=anthropic_key, websocket=websocket,
            concurrency=8,
        )
    except Exception as exc:
        logger.error("website_pipeline: orchestrator failed — %s", exc, exc_info=True)
        await _send(websocket, "error", f"❌ Page generation failed: {exc}")
        return False

    # Write all generated files to workspace
    files = result.get("files") or []
    written = 0
    for f in files:
        rel = f.get("path", "").strip()
        content = f.get("content", "")
        if not rel or not content:
            continue
        try:
            # Strip leading slash for safety
            rel = rel.lstrip("/")
            abs_path = os.path.join(workspace_path, rel)
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
            with open(abs_path, "w", encoding="utf-8") as fh:
                fh.write(content)
            written += 1
        except Exception as exc:
            logger.warning("website_pipeline: failed to write %s — %s", rel, exc)

    failed_routes = result.get("failed_routes") or []
    page_results = result.get("page_results") or {}
    successful_pages = sum(1 for v in page_results.values() if v)
    total_pages = len(page_results) or len(pages)

    logger.info(
        "website_pipeline: generation done — %d files written, %d/%d pages ok, "
        "header_ok=%s footer_ok=%s failed=%s",
        written, successful_pages, total_pages,
        result.get("header_ok"), result.get("footer_ok"), failed_routes,
    )

    if failed_routes:
        await _send(
            websocket, "warning",
            f"⚠️ {len(failed_routes)}/{total_pages} page(s) failed: {', '.join(failed_routes)}",
        )
    await _send(
        websocket, "progress",
        f"✅ Website generated — {written} files, {successful_pages}/{total_pages} pages ok",
    )

    # Success criteria: at least 1 page generated AND we wrote >0 files.
    # Partial failure is still a usable site (failed pages get 404 / can be retried).
    return successful_pages >= 1 and written > 0


# ── Stage 5 helpers — deterministic foundation builders ─────────────────

def _build_foundation_files(
    plan: dict[str, Any],
    visual_dna: dict[str, Any],
    design_signal: dict[str, Any],
) -> dict[str, str]:
    """Build the foundation file contents. Returns {rel_path: content}.

    Foundation files are PURE DATA — no creative decisions, all derivable
    from the plan + visual_dna + research output. They're written
    deterministically so the per-page Claude calls have a stable contract
    to import from.
    """
    brand = plan.get("brand") or {}
    brand_name = brand.get("name") or "Brand"
    tagline = brand.get("tagline") or ""
    pages = plan.get("pages") or []

    files: dict[str, str] = {}

    # site.js
    files["src/config/site.js"] = _build_site_config(brand_name, tagline)

    # navigation.js — derive from plan routes
    files["src/config/navigation.js"] = _build_navigation(pages)

    # design-system.js — token presets from visual_dna intensity
    intensity = (visual_dna.get("cultural_intensity") or "bold").lower()
    files["src/lib/design-system.js"] = _build_design_system(intensity)

    # Route shells for non-home pages
    for page in pages:
        route = (page.get("route") or "/").strip()
        if route in ("", "/"):
            continue
        rel = route.lstrip("/")
        if "[" in rel or "]" in rel:
            continue  # skip dynamic routes
        title = (page.get("title") or "Page").strip()
        component_name = "".join(
            part[:1].upper() + part[1:]
            for part in rel.replace("/", "-").split("-") if part
        ) + "Page"
        files[f"src/app/{rel}/page.js"] = (
            f'import {component_name} from "@/components/pages/{rel}/{component_name}";\n'
            f'\nexport const metadata = {{\n'
            f'  title: {json.dumps(title + " | " + brand_name, ensure_ascii=False)},\n'
            f'}};\n'
            f'\nexport default function Page() {{\n'
            f'  return <{component_name} />;\n'
            f'}}\n'
        )

    return files


def _build_site_config(brand_name: str, tagline: str) -> str:
    return (
        '/* AUTO-GENERATED — edit the schema, not this file. */\n'
        'export const siteConfig = {\n'
        f'  name: {json.dumps(brand_name, ensure_ascii=False)},\n'
        f'  tagline: {json.dumps(tagline, ensure_ascii=False)},\n'
        '  url: "https://example.com",\n'
        '  social: {\n'
        '    twitter: "",\n'
        '    instagram: "",\n'
        '  },\n'
        '};\n'
    )


def _build_navigation(pages: list[dict]) -> str:
    """Build mainNav + footerNav from the plan's pages list."""
    nav_items = []
    for p in pages:
        route = (p.get("route") or "/").strip()
        title = (p.get("title") or "").strip()
        if not route or route == "/":
            continue
        nav_items.append({"label": title or route, "href": route})
    items_js = "[\n" + ",\n".join(
        f'  {{ label: {json.dumps(i["label"], ensure_ascii=False)}, '
        f'href: {json.dumps(i["href"], ensure_ascii=False)} }}'
        for i in nav_items
    ) + "\n]"
    return (
        '/* AUTO-GENERATED — edit the schema, not this file. */\n'
        f'export const mainNav = {items_js};\n'
        '\n'
        f'export const footerNav = {items_js};\n'
    )


def _build_design_system(intensity: str) -> str:
    """Tailwind class tokens. Intensity-driven for now; can be expanded."""
    is_bold = intensity == "bold"
    spacing = '"py-24 md:py-32"' if is_bold else '"py-16 md:py-24"'
    container = '"max-w-7xl mx-auto px-4 md:px-8"'
    card = '"rounded-2xl border bg-card shadow-sm transition-all hover:shadow-md"' if is_bold else '"rounded-lg border bg-card transition-shadow hover:shadow-sm"'
    heading = '"font-heading tracking-tight"'
    body = '"font-body text-base leading-relaxed"'
    button_primary = '"inline-flex items-center justify-center rounded-full bg-primary text-primary-foreground px-6 py-3 text-sm font-medium hover:opacity-90 transition-opacity"'
    button_secondary = '"inline-flex items-center justify-center rounded-full border border-foreground/20 bg-transparent px-6 py-3 text-sm font-medium hover:bg-foreground/5 transition-colors"'
    button_ghost = '"inline-flex items-center justify-center rounded-full bg-transparent px-4 py-2 text-sm font-medium hover:bg-foreground/5 transition-colors"'
    motion = (
        'initial: { opacity: 0, y: 20 },\n'
        '    whileInView: { opacity: 1, y: 0 },\n'
        '    viewport: { once: true, margin: "-100px" },\n'
        '    transition: { duration: 0.6, ease: "easeOut" },'
    )

    return (
        '/* AUTO-GENERATED — edit the schema, not this file. */\n'
        'export const ds = {\n'
        f'  section: {spacing},\n'
        f'  container: {container},\n'
        f'  card: {card},\n'
        f'  cardInteractive: {card},\n'
        f'  heading: {heading},\n'
        f'  body: {body},\n'
        f'  buttonPrimary: {button_primary},\n'
        f'  buttonSecondary: {button_secondary},\n'
        f'  buttonGhost: {button_ghost},\n'
        '  motion: {\n'
        f'    {motion}\n'
        '  },\n'
        '};\n'
    )
