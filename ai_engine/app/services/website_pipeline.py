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


def _content_separation_enabled() -> bool:
    """Shared feature flag (default ON). Mirrored from page_generator so
    Stage 5 foundation builders can branch on the same flag."""
    raw = os.environ.get("CONTENT_SEPARATION_ENABLED", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


_EDITABLE_COMPONENT_JSX = '''"use client";
/* AUTO-GENERATED — Phase 4 editable wrapper. Do not edit by hand. */
import { Children, cloneElement, isValidElement } from "react";

/**
 * Editable — wraps an inline element with data-* attributes the dashboard
 * editor scans for. Renders the child element directly (no wrapper) when
 * possible so the resulting HTML stays valid (no <span> around <h1>).
 *
 *   <Editable path="hero.title" type="text">
 *     <h1>{content.hero.title}</h1>
 *   </Editable>
 */
export function Editable({ path, type = "text", children }) {
  const attrs = {
    "data-editable": "true",
    "data-editable-path": path,
    "data-editable-type": type,
  };
  // When there is exactly one element child we inject the data-attrs
  // directly onto it — keeps HTML valid (no span wrapping a block element).
  if (Children.count(children) === 1 && isValidElement(children)) {
    return cloneElement(children, attrs);
  }
  // Text nodes or multiple children → wrap in a span. Span is invalid
  // around block elements; the editor authoring rule is to pass a single
  // element child, which lands on the fast path above.
  return <span {...attrs}>{children}</span>;
}

export default Editable;
'''


def _build_editable_component() -> str:
    return _EDITABLE_COMPONENT_JSX


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

    # ── Stage 0.5: Purpose classification ───────────────────────────
    # Runs BEFORE intent so downstream stages know what the site is FOR
    # (recruitment vs ecommerce vs lead-gen), not just what industry it's
    # in. Pure additive — does not modify any existing stage's inputs.
    await _phase(websocket, 1, "Preparing workspace", "Workspace ready", "done")

    from app.services.purpose_classifier import classify_purpose
    from knowledge.loader import extract_clarify_context

    clarity_answers, clean_description = extract_clarify_context(description)
    gemini_key = validated.get("gemini_api_key") or os.environ.get("GOOGLE_API_KEY", "")

    purpose_data = await classify_purpose(
        user_prompt=clean_description,
        clarity_answers=clarity_answers or {},
        gemini_key=gemini_key,
    )
    logger.info(
        "Purpose classified: %s (%d%%) — industry=%r audience=%s named_roles=%s",
        purpose_data["primary_purpose"], purpose_data["confidence"],
        purpose_data["industry"], purpose_data["target_audience"],
        purpose_data["named_roles"],
    )
    await _send(
        websocket, "progress",
        f"🎯 Purpose: {purpose_data['primary_purpose']} ({purpose_data['confidence']}% conf)",
    )

    # ── Stage 1: Intent — CACHED ────────────────────────────────────
    # Cached not just to save the ~$0.01 Flash call but because the
    # downstream Stage 3 cache key includes `intent`. Without caching
    # intent, Gemini non-determinism produces a slightly different
    # `intent` dict every run, which would force a Stage 3 miss even
    # when Stage 2 is a hit. Caching here keeps the whole tail stable.
    from app.services.pipeline_cache import pipeline_cache
    project_id = chat_session_id or "_session_none_"

    await _phase(websocket, 3, "Researching project", "Analyzing intent + culture…", "active")
    await _send(websocket, "progress", "🧠 Stage 1/6 — Analyzing intent…")

    from app.services.landing_intent import analyze_intent

    cached_intent = pipeline_cache.get(
        project_id, "intent", clean_description, classification,
    )
    if cached_intent is not None:
        intent = cached_intent
        await _send(websocket, "progress", "♻️  Stage 1 — using cached intent")
    else:
        try:
            intent = await analyze_intent(clean_description, classification, timeout_s=60.0)
        except Exception as exc:
            logger.error("website_pipeline: intent failed — %s", exc, exc_info=True)
            await _send(websocket, "error", f"❌ Intent analysis failed: {exc}")
            return False
        pipeline_cache.set(
            project_id, "intent", intent, clean_description, classification,
        )

    logger.info(
        "website_pipeline: intent ok — category=%s geo=%s personality=%s",
        intent.get("business_category"), intent.get("geographic_specifics"),
        intent.get("brand_personality"),
    )

    # ── Stage 2: Research (parallel) — CACHED per project ───────────
    # Cache key is (prompt + clarity + purpose). Anything that changes
    # the research question changes the hash → automatic invalidation.
    await _send(websocket, "progress", "🔎 Stage 2/6 — Researching domain + design (parallel)…")

    from app.services.landing_domain_research import run_domain_research
    from app.services.landing_design_research import run_design_research

    cached_research = pipeline_cache.get(
        project_id, "research",
        clean_description, clarity_answers, purpose_data,
    )
    if cached_research is not None:
        await _send(websocket, "progress", "♻️  Stage 2 — using cached research")
        domain_res, design_res = cached_research
    else:
        try:
            domain_res, design_res = await asyncio.gather(
                run_domain_research(intent, timeout_s=120.0, purpose_data=purpose_data),
                run_design_research(intent, timeout_s=120.0, purpose_data=purpose_data),
            )
        except Exception as exc:
            logger.error("website_pipeline: research failed — %s", exc, exc_info=True)
            await _send(websocket, "error", f"❌ Research failed: {exc}")
            return False
        pipeline_cache.set(
            project_id, "research", (domain_res, design_res),
            clean_description, clarity_answers, purpose_data,
        )

    # ── Stage 3: Visual_DNA + Voice — CACHED per project ────────────
    # Cache key is (research + intent + purpose). When research is a
    # cache hit, the signals cache will be a hit too — saving the full
    # Pro extract call (the most expensive single step in the pipeline).
    await _send(websocket, "progress", "✨ Stage 3/6 — Extracting visual DNA (typography, palette, motifs)…")

    from app.services.landing_research_extract import extract_research_signals

    cached_signals = pipeline_cache.get(
        project_id, "signals",
        domain_res, intent, purpose_data,
    )
    if cached_signals is not None:
        await _send(websocket, "progress", "♻️  Stage 3 — using cached visual DNA + voice signature")
        signals = cached_signals
    else:
        try:
            signals = await extract_research_signals(
                intent, domain_res, design_res, timeout_s=180.0,
                purpose_data=purpose_data,
            )
        except Exception as exc:
            logger.error("website_pipeline: visual_dna extract failed — %s", exc, exc_info=True)
            await _send(websocket, "error", f"❌ Visual DNA extraction failed: {exc}")
            return False
        pipeline_cache.set(
            project_id, "signals", signals,
            domain_res, intent, purpose_data,
        )

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
            purpose_data=purpose_data,
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
    design_signal = signals.get("design") or {}
    foundation_files = _build_foundation_files(plan, visual_dna, design_signal=design_signal)
    # globals.css gets its own builder because it has Tailwind directives
    # and template-shaped HSL var blocks that aren't a plain key=value dict
    try:
        globals_css = _build_globals_css(design_signal)
        if globals_css:
            foundation_files["src/app/globals.css"] = globals_css
    except Exception as exc:
        logger.warning("website_pipeline: globals.css build failed (non-fatal) — %s", exc)
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

    # ── Stage 5.5: Image binding ────────────────────────────────────
    # Resolve every section that needs imagery to a real Unsplash URL
    # BEFORE Claude sees the page — prevents hallucinated /images/ paths.
    await _send(websocket, "progress", "🖼️  Stage 5.5 — Binding images (Unsplash)…")
    from app.services.image_binding import (
        bind_page_images, clear_image_cache,
    )
    clear_image_cache()  # don't leak across pipeline runs
    industry = (purpose_data.get("industry") or intent.get("business_category") or "general").strip()
    try:
        image_bindings = await asyncio.gather(*[
            bind_page_images(page, industry, purpose_data, visual_dna)
            for page in pages
        ])
    except Exception as exc:
        logger.warning("website_pipeline: image binding threw (non-fatal) — %s", exc)
        image_bindings = [{} for _ in pages]
    page_images = {
        (p.get("route") or "/").strip(): img
        for p, img in zip(pages, image_bindings)
    }
    total_imgs = sum(
        sum(len(v) for v in page_imgs.values())
        for page_imgs in page_images.values()
    )
    logger.info(
        "Stage 5.5: Binding images — %d pages bound, %d images total",
        len(page_images), total_imgs,
    )
    await _send(
        websocket, "progress",
        f"🖼️  Images bound: {total_imgs} across {len(page_images)} pages",
    )

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
            purpose_data=purpose_data,
            page_images=page_images,
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

    # ── Stage 6.5: Derive content schema (editor metadata) ─────────
    # Walks src/content/pages/*.json that Claude just wrote and
    # produces .lucid/content-schema.json — the manifest the
    # dashboard editor uses to know what's editable. Pure derivation,
    # no LLM. Skip cleanly when content separation is disabled.
    if _content_separation_enabled():
        try:
            from app.services.content_schema import write_content_schema
            schema_path, field_count = write_content_schema(workspace_path)
            logger.info(
                "website_pipeline: content-schema written — %s (%d fields)",
                schema_path, field_count,
            )
            await _send(
                websocket, "progress",
                f"📝 Stage 6.5 — Content schema: {field_count} editable fields",
            )
        except Exception as exc:
            logger.warning(
                "website_pipeline: content schema derivation threw (non-fatal) — %s", exc,
            )

    # ── Stage 7: Post-generation verification ──────────────────────
    # Static audit of file structure + imports. Catches Claude
    # contract violations before the dev server starts.
    await _send(websocket, "progress", "🔍 Stage 7/7 — Verifying file structure + imports…")

    from app.services.website_verification import audit_generated_website
    try:
        audit = audit_generated_website(
            workspace_path, plan,
            expect_header=result.get("header_ok") is True,
            expect_footer=result.get("footer_ok") is True,
        )
        logger.info("website_pipeline: %s", audit["summary"])
        if audit["ok"]:
            await _send(websocket, "progress", f"✅ {audit['summary']}")
        else:
            issue_lines: list[str] = []
            for key, val in audit["issues"].items():
                if val:
                    sample = val[:3]
                    issue_lines.append(f"  • {key}: {len(val)} (e.g. {sample})")
            await _send(
                websocket, "warning",
                "⚠️ Verification found issues — site may still run but has gaps:\n" + "\n".join(issue_lines),
            )
    except Exception as exc:
        logger.warning("website_pipeline: verification threw (non-fatal) — %s", exc)

    # ── Stage 7b: Content / code separation audit ──────────────────
    # Validates Phase-4 editing contract — no-op when feature flag off.
    if _content_separation_enabled():
        try:
            from app.services.website_verification import audit_content_separation
            sep_audit = audit_content_separation(workspace_path)
            logger.info("website_pipeline: %s", sep_audit["summary"])
            if not sep_audit["ok"]:
                detail = ", ".join(
                    f"{k}={len(v)}" for k, v in sep_audit["issues"].items() if v
                )
                await _send(
                    websocket, "warning",
                    f"⚠️ Content/code-separation issues: {detail}",
                )
        except Exception as exc:
            logger.warning(
                "website_pipeline: content separation audit threw (non-fatal) — %s", exc,
            )

    # ── Stage 7.5: Content quality audit ───────────────────────────
    # Catches bad CONTENT (placeholders, fake addresses, broken /routes,
    # missing required sections, voice violations) — NEVER blocks the
    # pipeline. The score lands on the websocket so the frontend can
    # display it; the issues are logged for diagnostics.
    await _send(websocket, "progress", "🧪 Stage 7.5 — Auditing content quality…")
    from app.services.website_verification import audit_content
    voice_signature = (signals.get("voice") or {}) if isinstance(signals, dict) else {}
    try:
        content_audit = audit_content(
            project_dir=workspace_path,
            purpose_data=purpose_data,
            voice_signature=voice_signature,
        )
        logger.info("website_pipeline: %s", content_audit["summary"])
        if not content_audit["ok"] and content_audit["score"] < 70:
            logger.warning(
                "Content quality below threshold: %d", content_audit["score"],
            )
            logger.warning("Issues: %s", {
                k: len(v) for k, v in content_audit["issues"].items() if v
            })
        # Surface score + categorized issue counts so the frontend can render a badge
        try:
            await websocket.send_json({
                "type": "content_audit",
                "score": content_audit["score"],
                "ok": content_audit["ok"],
                "summary": content_audit["summary"],
                "issue_counts": {
                    k: len(v) for k, v in content_audit["issues"].items()
                },
                "warnings": content_audit.get("warnings", []),
            }) if websocket is not None else None
        except Exception:
            pass
    except Exception as exc:
        logger.warning("website_pipeline: content audit threw (non-fatal) — %s", exc)

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

    # editable.jsx — runtime wrapper for in-place editing (Phase 4)
    # See _build_editable_component() for the React implementation.
    if _content_separation_enabled():
        files["src/lib/editable.jsx"] = _build_editable_component()

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


def _build_globals_css(design_signal: dict[str, Any]) -> str:
    """Build src/app/globals.css from design signals.

    Reuses landing_phase0's palette renderer for HSL var blocks. Adds Tailwind
    directives + Google Fonts imports + heading/body font-family overrides.
    """
    palette = (design_signal or {}).get("chosen_palette") or {}
    typo = (design_signal or {}).get("chosen_typography") or {}
    if not palette and not typo:
        return ""  # no design data → skip writing (page generators will still work but use default colors)

    # Build the palette {token: HSL} dict in landing_phase0's expected shape
    palette_for_css = {
        "background": palette.get("background", ""),
        "foreground": palette.get("foreground", ""),
        "primary":    palette.get("primary", ""),
        "secondary":  palette.get("secondary", ""),
        "accent":     palette.get("accent", ""),
        "muted":      palette.get("muted", ""),
        "border":     palette.get("border", ""),
        "card":       palette.get("card", ""),
    }
    # Drop empties
    palette_for_css = {k: v.strip() for k, v in palette_for_css.items() if v and v.strip()}

    heading_font = (typo.get("heading_font") or "Inter").strip()
    body_font = (typo.get("body_font") or "Inter").strip()

    from app.services.landing_phase0 import _palette_vars, _GLOBALS_TEMPLATE
    return _GLOBALS_TEMPLATE.format(
        palette_vars=_palette_vars(palette_for_css),
        heading_font=heading_font,
        body_font=body_font,
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
