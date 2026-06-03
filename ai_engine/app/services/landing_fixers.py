"""Lean fixers for the new landing flow.

Most legacy fixers exist to clean up Phase-1/Phase-2 hallucinations that the
new flow eliminates upfront. We keep only the guards that catch real bugs
even when codegen is well-behaved.

Kept (still useful):
  • strip_use_client_from_configs    — Claude sometimes adds 'use client' to JSON imports / configs
  • fix_use_client                   — missing directive on hooks/event-handler files
  • fix_banned_icons                 — lucide-react doesn't ship Instagram/Facebook/etc
  • fix_named_import_default_export_mismatch — wrong import style for default-exported components
  • fix_missing_default_export       — guard against incomplete sections
  • fix_unescaped_entities           — apostrophes/quotes in JSX text
  • fix_img_tags                     — <img> → next/image where safe
  • fix_next_config_image_domains    — Unsplash domain whitelist (CRITICAL — without it images 404)
  • fix_missing_tailwind_directives  — globals.css must have @tailwind directives
  • fix_unresolved_imports           — stub broken imports rather than failing the build
  • fix_low_contrast_text_on_image   — dark overlay over hero photos so light text stays readable
  • fix_dropdown_zindex              — z-50 on absolute/top-full panels so they don't sit behind buttons

Dropped (redundant in new flow):
  • fix_unicode_escapes_in_jsx       — content is JSON, not JSX strings
  • fix_section_ids                  — codegen system prompt mandates outermost <section id="…">
  • fix_marketing_header_nav_anchors — header reads landing.nav directly
  • fix_header_anchor_alignment      — section ids + nav hrefs share the same JSON
  • fix_dynamic_route_conflicts      — landing only has /
  • restore_template_ui_files        — template UI files are untouched in new flow
"""
from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)



async def run_landing_fixers(workspace_path: str, websocket: Any = None) -> dict[str, int]:
    """Run the landing-only fixer chain. Returns counts per fixer.

    All steps are wrapped in try/except — a single bad fixer never breaks
    the chain. Counts let the orchestrator emit a one-line summary.
    """
    from app.services import post_generation_fixer as F

    counts: dict[str, int] = {}

    def _run(label: str, fn, *args, count_via_len: bool = True) -> int:
        try:
            result = fn(*args)
        except Exception as exc:
            logger.warning("landing_fixer %s failed: %s", label, exc)
            counts[label] = 0
            return 0
        n = len(result) if (count_via_len and isinstance(result, list)) else int(bool(result))
        counts[label] = n
        return n

    if websocket is not None:
        try:
            await websocket.send_json({"type": "progress", "message": "🔧 Running landing fixers..."})
        except Exception:
            pass

    _run("strip_use_client_from_configs", F.strip_use_client_from_configs, workspace_path)
    _run("fix_use_client", F.fix_use_client, workspace_path)
    _run("fix_banned_icons", F.fix_banned_icons, workspace_path)
    _run("fix_named_import_default_export_mismatch", F.fix_named_import_default_export_mismatch, workspace_path)
    _run("fix_missing_default_export", F.fix_missing_default_export, workspace_path)
    # JSX <Reveal> tag balancer — catches Claude over-closing in complex
    # conditional JSX. Run BEFORE fix_unescaped_entities so balanced trees
    # are the assumption for everything downstream.
    _run("fix_jsx_reveal_imbalance", F.fix_jsx_reveal_imbalance, workspace_path)
    # Run apostrophe-in-JS-string FIRST so the JSX-text fixer below
    # doesn't double-escape strings that already became valid double-
    # quoted literals.
    _run("fix_jsx_apostrophe_in_js_string", F.fix_jsx_apostrophe_in_js_string, workspace_path)
    _run("fix_unescaped_entities", F.fix_unescaped_entities, workspace_path)
    _run("fix_img_tags", F.fix_img_tags, workspace_path)
    _run("fix_next_config_image_domains", F.fix_next_config_image_domains, workspace_path, count_via_len=False)
    _run("fix_missing_tailwind_directives", F.fix_missing_tailwind_directives, workspace_path)
    # Visibility / UX guards — run AFTER tailwind directives (no point upgrading
    # contrast if Tailwind itself isn't loading) and BEFORE unresolved-imports
    # (which only stubs missing modules and doesn't touch JSX class strings).
    _run("fix_low_contrast_text_on_image", F.fix_low_contrast_text_on_image, workspace_path)
    _run("fix_dropdown_zindex", F.fix_dropdown_zindex, workspace_path)
    # Carousel autoplay + scrollIntoView(block: 'nearest') was hijacking page
    # scroll every 4.5s, locking the user to the testimonials section. Convert
    # to parent-relative scrollLeft so only the carousel moves.
    _run("fix_carousel_scroll_hijack", F.fix_carousel_scroll_hijack, workspace_path)
    # `col-span-*` / `row-span-*` on the inner div instead of the <Reveal>
    # wrapper (CSS Grid ignores grid placement on non-direct children). Bento
    # grids collapsed to a single column with all cards overlapping —
    # TrustTickerSection 2026-06-03. Move tokens onto the Reveal wrapper.
    _run("fix_grid_positioning_on_wrapper", F.fix_grid_positioning_on_wrapper, workspace_path)
    # Footer rendering empty placeholder columns (em-dash ghosts) because
    # Claude hardcodes a 4-column structure even when the brief only fills
    # one group. Strip the placeholder branch + filter empty groups before
    # render so the footer scales to actual data.
    _run("fix_footer_empty_columns", F.fix_footer_empty_columns, workspace_path)
    # Hero "Score Guarantee" / score-badge clipping — negative top/right
    # offsets push the badge above the section while overflow-hidden clips
    # it. Clamp the offset back inside the section.
    _run("fix_badge_clipping_in_hero", F.fix_badge_clipping_in_hero, workspace_path)
    # FAQ "04" giant decorative number bleeding into the next section because
    # the section root forgot overflow-hidden. Add the clip when the section
    # carries any decorative absolute element (huge type, blob blur, big
    # negative offset).
    _run("fix_section_overflow_clip", F.fix_section_overflow_clip, workspace_path)
    # Telemetry only — log sections that hardcode data arrays despite the
    # prompt rule. Auto-fix isn't safe (would break the component) but the
    # counts let us measure how often Claude ignores the rule and prioritize
    # tightening the prompt vs enriching landing.json items.
    _run("audit_hardcoded_data_arrays", F.audit_hardcoded_data_arrays, workspace_path)
    # Telemetry only — log sections that render the same JSX expression as
    # both a giant absolute watermark AND a normal label (the Elena Rodriguez
    # ghost-name bug). Auto-removal would risk unbalanced JSX.
    _run("audit_duplicate_text_watermark", F.audit_duplicate_text_watermark, workspace_path)
    # Strip kebab/snake-case lucide imports BEFORE unresolved-imports stubs them.
    _run("fix_invalid_lucide_imports", F.fix_invalid_lucide_imports, workspace_path)
    # Strip Claude-emitted hardcoded UNSPLASH_IMAGES dicts so components fall
    # back to the runtime-bound landing.json + gradient placeholder.
    _run("fix_hardcoded_unsplash_dicts", F.fix_hardcoded_unsplash_dicts, workspace_path)
    _run("fix_unresolved_imports", F.fix_unresolved_imports, workspace_path)
    # Design-token drift: validate radius classes match brief, log telemetry
    # for hardcoded hex/rgb/numbered-palette colors. Returns a dict, not a list.
    color_warnings = 0
    try:
        drift = F.fix_design_token_drift(workspace_path)
        counts["fix_design_token_drift_radius"] = drift.get("drift_fixed", 0)
        color_warnings = drift.get("hardcoded_color_warnings", 0)
    except Exception as exc:
        logger.warning("landing_fixer fix_design_token_drift failed: %s", exc)
        counts["fix_design_token_drift_radius"] = 0

    # `total` reports actual file changes only — color warnings are info-level
    # telemetry (status colors and brand-fixed hex are legitimate).
    total = sum(counts.values())
    logger.info(
        "landing_fixers: %d total fixes — %s%s",
        total, counts,
        f" — {color_warnings} color drift signals (info)" if color_warnings else "",
    )

    if websocket is not None:
        try:
            await websocket.send_json({
                "type": "progress",
                "message": f"✅ Landing fixers: {total} fixes",
            })
        except Exception:
            pass

    return counts
