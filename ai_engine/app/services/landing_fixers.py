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
    # Run apostrophe-in-JS-string FIRST so the JSX-text fixer below
    # doesn't double-escape strings that already became valid double-
    # quoted literals.
    _run("fix_jsx_apostrophe_in_js_string", F.fix_jsx_apostrophe_in_js_string, workspace_path)
    _run("fix_unescaped_entities", F.fix_unescaped_entities, workspace_path)
    _run("fix_img_tags", F.fix_img_tags, workspace_path)
    _run("fix_next_config_image_domains", F.fix_next_config_image_domains, workspace_path, count_via_len=False)
    _run("fix_missing_tailwind_directives", F.fix_missing_tailwind_directives, workspace_path)
    _run("fix_unresolved_imports", F.fix_unresolved_imports, workspace_path)

    total = sum(counts.values())
    logger.info("landing_fixers: %d total fixes — %s", total, counts)

    if websocket is not None:
        try:
            await websocket.send_json({
                "type": "progress",
                "message": f"✅ Landing fixers: {total} fixes",
            })
        except Exception:
            pass

    return counts
