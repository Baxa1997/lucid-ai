"""Landing-page generation pipeline.

End-to-end orchestrator for the new landing flow. Replaces the legacy
3-phase Claude pipeline (foundation → content → polish) with:

  Step 1 — build_landing_brief()        single Gemini call (palette,
                                        typography, sections w/ layout
                                        hints + image queries)
  Step 2 — write_landing_content()      Brief → src/content/landing.json
  Step 3 — bind_landing_images()        Unsplash → patch urls into JSON
  Step 4 — run_landing_phase0()         globals.css, layout.jsx,
                                        MarketingHeader, MarketingFooter,
                                        design-system.js
  Step 5 — generate_landing_sections()  N parallel Claude calls
                                        (one component per section)
  Step 6 — write_landing_page_shell()   app/page.jsx imports + renders
  Step 7 — run_landing_fixers()         lean fixer chain

Compared to _generate_new_project_inner, this module:
  • Skips classify, schema build, intent gate (caller already did them)
  • Skips Phase 1 / Phase 2 / Phase 3 codegen (replaced by per-section)
  • Skips deep research stack (Brief is the only research artifact)
  • Skips legacy header/footer/page builders (Phase 0 deterministic)
"""
from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


async def run_landing_pipeline(
    *,
    description: str,
    classification: dict[str, Any],
    workspace_path: str,
    validated: dict[str, Any],
    websocket: Any = None,
    chat_session_id: str = "",
) -> bool:
    """Run all 7 steps of the landing pipeline. Returns True on success.

    Failures mid-pipeline are logged but don't abort — partial output is
    still useful (e.g. images can fail without blocking the build). The
    only hard failure is build_landing_brief returning empty sections.
    """
    anthropic_key = validated.get("anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY", "")
    gemini_key = validated.get("gemini_api_key") or os.environ.get("GOOGLE_API_KEY", "")

    if not anthropic_key or not gemini_key:
        await _send(websocket, "error", "❌ Missing API keys (anthropic + gemini required)")
        return False

    # Drive the UI's task-phase indicator from inside this pipeline.
    # The orchestrator only emits Phase 3 "active" before calling us, so
    # without these the indicator stays stuck on RESEARCHING for the
    # entire run — which is what the user just reported.
    async def _phase(phase: int, title: str, desc: str, status: str) -> None:
        if websocket is None:
            return
        try:
            await websocket.send_json({
                "type": "task_phase",
                "phase": phase,
                "title": title,
                "description": desc,
                "status": status,
            })
        except Exception:
            pass

    # ── Step 1: Brief + grounded research (parallel) ─────────────────
    # The legacy brief is the hard requirement; research is best-effort
    # extra signal that gets merged into the brief if it succeeds. Both
    # run in parallel — research ~60-70s, brief ~25s — so wall time is
    # gated by the research path.
    from app.services.landing_brief import build_landing_brief
    from app.services.landing_intent import analyze_intent
    from app.services.landing_domain_research import run_domain_research
    from app.services.landing_design_research import run_design_research
    from app.services.landing_research_extract import (
        extract_research_signals,
        enrich_brief_with_signals,
    )
    import asyncio

    await _phase(3, "Researching project", "Searching real reference sites + design DNA…", "active")
    await _send(websocket, "progress", "🧠 Researching brand & sections (Gemini)...")

    async def _research_signals() -> tuple[dict | None, dict | None, dict | None]:
        """intent → parallel(domain, design) → extract. Best-effort — any
        failure returns (None, None, None) so the legacy brief still ships."""
        try:
            intent = await analyze_intent(
                description, classification,
                gemini_key=gemini_key, websocket=websocket, timeout_s=60.0,
            )
        except Exception as exc:
            logger.warning("landing_pipeline: intent failed (non-fatal) — %s", exc)
            return None, None, None
        try:
            domain_res, design_res = await asyncio.gather(
                run_domain_research(intent, gemini_key=gemini_key, websocket=websocket, timeout_s=240.0),
                run_design_research(intent, gemini_key=gemini_key, websocket=websocket, timeout_s=240.0),
            )
        except Exception as exc:
            logger.warning("landing_pipeline: research failed (non-fatal) — %s", exc)
            return intent, None, None
        try:
            signals = await extract_research_signals(
                intent, domain_res, design_res,
                gemini_key=gemini_key, timeout_s=90.0,
            )
        except Exception as exc:
            logger.warning("landing_pipeline: signal extract failed (non-fatal) — %s", exc)
            return intent, domain_res, None
        return intent, {"domain": domain_res, "design": design_res}, signals

    try:
        brief, research_bundle = await asyncio.gather(
            build_landing_brief(
                description, classification,
                gemini_key=gemini_key, websocket=websocket,
            ),
            _research_signals(),
        )
    except Exception as exc:
        logger.error("landing_pipeline: brief failed — %s", exc, exc_info=True)
        await _send(websocket, "error", f"❌ Brief failed: {str(exc)[:160]}")
        return False

    sections = brief.get("sections") or []
    if not sections:
        await _send(websocket, "error", "❌ Brief returned 0 sections — aborting")
        return False

    # Enrich the brief with research signals (mutates in-place). No-ops if
    # signals came back empty.
    _intent, _research, _signals = research_bundle
    if _signals:
        try:
            enrich_brief_with_signals(brief, _signals)
            logger.info(
                "landing_pipeline: brief enriched — voice=%d regional=%d industry=%d",
                len(brief.get("voice_phrases") or []),
                len(brief.get("regional_refs") or []),
                len(brief.get("industry_terms") or []),
            )
        except Exception as exc:
            logger.warning("landing_pipeline: enrich failed (non-fatal) — %s", exc)

    brand_name = (brief.get("brand") or {}).get("name", "")
    await _send(
        websocket,
        "progress",
        f"📐 Brief: {brand_name or 'site'} — {len(sections)} sections",
    )
    await _phase(
        3,
        "Researching project",
        f"Brief ready — {len(sections)} sections, palette + typography",
        "done",
    )

    # Rename the chat session to the brand name so the project shows up as
    # "The Bali Haven" in Apps / sidebar instead of the truncated raw prompt.
    if chat_session_id and brand_name:
        try:
            from app.supabase_client import managed_admin_client
            async with managed_admin_client() as client:
                await (
                    client.table("chat_sessions")
                    .update({"title": brand_name[:255]})
                    .eq("id", chat_session_id)
                    .execute()
                )
        except Exception as exc:
            logger.warning("landing_pipeline: title update failed — %s", exc)

    # ── Step 1.5: Plan confirmation gate ─────────────────────────────
    # Show the user the proposed plan derived from the Brief and wait
    # for "Looks Good" before burning Claude tokens on codegen. The
    # legacy generator has this gate; the landing fast-path lost it,
    # so users couldn't redirect or correct before generation started.
    confirmed = await _emit_plan_and_wait(
        brief=brief,
        description=description,
        websocket=websocket,
        chat_session_id=chat_session_id,
    )
    if not confirmed:
        # User rejected, timed out, or set a correction. The orchestrator
        # checks for `_plan_correction` and re-runs with the new prompt.
        return False

    # ── Step 2: Runtime content JSON ─────────────────────────────────
    from app.services.landing_content import write_landing_content
    try:
        content_path, _content = write_landing_content(workspace_path, brief)
        logger.info("landing_pipeline: wrote %s", content_path)
    except Exception as exc:
        logger.error("landing_pipeline: content write failed — %s", exc, exc_info=True)
        await _send(websocket, "error", f"❌ Content write failed: {str(exc)[:160]}")
        return False

    # ── Step 3: Image binding (Unsplash) ─────────────────────────────
    from app.services.landing_image_binder import bind_landing_images
    fallback_keywords = list((brief.get("domain_keywords") or []))[:3]
    try:
        await bind_landing_images(
            workspace_path,
            fallback_keywords=fallback_keywords,
            websocket=websocket,
        )
    except Exception as exc:
        logger.warning("landing_pipeline: image binding failed (non-fatal) — %s", exc)

    # Flatten section.images to a plain array of URL strings. Section
    # components do `section.images[0]` and expect a string for <Image src>.
    # Object-of-{url,alt,query} was a footgun: components routinely passed
    # the whole object to <Image>, rendering empty. URL strings are the
    # simplest contract, alt-text comes from a section.image_alts parallel
    # array (kept for accessibility) when needed.
    try:
        import json as _json
        import os as _os
        _content_path = _os.path.join(workspace_path, "src", "content", "landing.json")
        if _os.path.exists(_content_path):
            with open(_content_path, "r", encoding="utf-8") as _fh:
                _content = _json.load(_fh)
            for _s in _content.get("sections") or []:
                _imgs = _s.get("images") or []
                # If already strings, leave as-is. Otherwise extract `.url`.
                if _imgs and isinstance(_imgs[0], dict):
                    _s["image_alts"] = [_i.get("alt", "") for _i in _imgs]
                    _s["images"] = [_i.get("url", "") for _i in _imgs if _i.get("url")]
            with open(_content_path, "w", encoding="utf-8") as _fh:
                _json.dump(_content, _fh, indent=2, ensure_ascii=False)
            logger.info("landing_pipeline: flattened section.images to URL strings")
    except Exception as _exc:
        logger.warning("landing_pipeline: image flatten failed (non-fatal) — %s", _exc)

    # ── Step 4: Phase-0 deterministic builders ───────────────────────
    from app.services.landing_phase0 import run_landing_phase0
    try:
        run_landing_phase0(workspace_path, brief)
    except Exception as exc:
        logger.error("landing_pipeline: phase0 failed — %s", exc, exc_info=True)
        await _send(websocket, "error", f"❌ Phase-0 failed: {str(exc)[:160]}")
        return False

    # ── Step 5: Parallel section + layout codegen ────────────────────
    # Sections AND header/footer all generate in parallel via Claude.
    # Phase-0 already wrote stub header/footer files; codegen overwrites
    # them with archetype-driven, real-interactivity versions.
    import asyncio
    from app.services.landing_section_codegen import (
        generate_landing_sections,
        generate_layout_components,
        write_landing_page_shell,
    )
    await _phase(
        5,
        "Writing code",
        f"Generating {len(sections)} sections + header/footer…",
        "active",
    )
    # Run sections first (3-wide), then layout (header+footer in parallel).
    # Running everything in one big gather pushed peak Anthropic concurrency
    # to 8+ streams, which the API rate-limits — streams come back with only
    # `message_start` and a 0-chunk body (the "Phase returned empty" symptom).
    # Serializing keeps total wall time similar (sections are the bottleneck)
    # while staying inside the per-org concurrent-stream budget.
    try:
        result = await generate_landing_sections(
            brief=brief,
            workspace_path=workspace_path,
            api_key=anthropic_key,
            websocket=websocket,
        )
    except Exception as exc:
        logger.error("landing_pipeline: section codegen failed — %s", exc, exc_info=True)
        await _send(websocket, "error", f"❌ Section codegen failed: {str(exc)[:160]}")
        return False

    try:
        await generate_layout_components(
            brief=brief,
            workspace_path=workspace_path,
            api_key=anthropic_key,
            websocket=websocket,
        )
    except Exception as exc:
        # Layout codegen failure is non-fatal — Phase-0 stub header/footer
        # remain in place so the page still renders.
        logger.warning("landing_pipeline: layout codegen failed (non-fatal) — %s", exc)

    page_imports = result.get("page_imports") or []
    page_renders = result.get("page_renders") or []
    if not page_renders:
        await _send(websocket, "error", "❌ All sections failed codegen — aborting")
        return False

    # ── Step 6: app/page.jsx shell ───────────────────────────────────
    try:
        write_landing_page_shell(workspace_path, page_imports, page_renders)
    except Exception as exc:
        logger.error("landing_pipeline: page shell failed — %s", exc, exc_info=True)
        await _send(websocket, "error", f"❌ Page shell failed: {str(exc)[:160]}")
        return False

    await _phase(
        5,
        "Writing code",
        f"{len(page_renders)} sections + layout written to disk",
        "done",
    )

    # ── Step 7: Lean fixer chain ─────────────────────────────────────
    from app.services.landing_fixers import run_landing_fixers
    try:
        await run_landing_fixers(workspace_path, websocket=websocket)
    except Exception as exc:
        logger.warning("landing_pipeline: fixers failed (non-fatal) — %s", exc)

    # ── Step 8: Build verification + auto-fix loop ───────────────────
    # Run `npm run build` and ask Claude to fix any errors it surfaces
    # before handing off to preview. Without this, build-time errors
    # (unresolved imports, JSX syntax, missing exports) only get caught
    # by the dev server, which leaves the user staring at a red overlay.
    await _send(websocket, "progress", "🏗️  Building application — checking for errors...")
    await _phase(6, "Building application", "Running production build to catch errors…", "active")
    try:
        from app.services.build_validator import BuildValidator
        validator = BuildValidator(
            api_key=anthropic_key,
            classification=classification or {},
            websocket=websocket,
            max_retries=2,
        )
        build_result = await validator.validate_and_fix(workspace_path)
        if build_result.get("success"):
            attempts = build_result.get("attempts", 0)
            fixed_n = len(build_result.get("fixed_files") or [])
            if fixed_n:
                await _send(
                    websocket, "progress",
                    f"✅ Build passed — fixed {fixed_n} file(s) across {attempts} attempt(s)",
                )
            await _phase(6, "Building application", "Build passed — preview ready", "done")
        else:
            err_count = build_result.get("error_count", 0)
            await _send(
                websocket, "warning",
                f"⚠️  Build still has {err_count} error(s) after auto-fix — preview may show issues.",
            )
            await _phase(6, "Building application", f"{err_count} error(s) remain", "done")
    except Exception as exc:
        logger.warning("landing_pipeline: build_validator failed (non-fatal) — %s", exc)
        await _phase(6, "Building application", "Build check skipped", "done")

    await _send(websocket, "progress", f"🎉 Landing generated — {len(page_renders)} sections")

    # ── Finalise chat_sessions row ─────────────────────────────────────
    # Three fields the rest of the system reads on reload / publish / list:
    #   • project_id           — keyed by all the lookups (ws.py prev-session,
    #                            /api/platform-repos, bootstrap-publish). If
    #                            it's NULL the row vanishes from "Recent
    #                            Projects" and the next ws connect creates a
    #                            fresh empty session instead of replaying
    #                            history. Fall back to chat_session_id when
    #                            the frontend handshake didn't supply one.
    #   • generation_complete  — flips ws.py reconnect into "skip pipeline,
    #                            replay history" mode.
    #   • title                — already updated post-Brief, kept as a guard.
    if chat_session_id:
        try:
            from app.supabase_client import managed_admin_client
            async with managed_admin_client() as client:
                # Read current project_id so we only fill it when NULL.
                row_res = await (
                    client.table("chat_sessions")
                    .select("project_id")
                    .eq("id", chat_session_id)
                    .maybe_single()
                    .execute()
                )
                current_pid = (row_res.data or {}).get("project_id") if row_res else None
                update_payload: dict[str, Any] = {"generation_complete": True}
                if not current_pid:
                    update_payload["project_id"] = chat_session_id
                if brand_name:
                    update_payload["title"] = brand_name[:255]
                await (
                    client.table("chat_sessions")
                    .update(update_payload)
                    .eq("id", chat_session_id)
                    .execute()
                )
                logger.info(
                    "landing_pipeline: finalised chat_session %s (project_id=%s, complete=True)",
                    chat_session_id, update_payload.get("project_id", current_pid),
                )
        except Exception as exc:
            logger.warning("landing_pipeline: finalisation update failed (non-fatal) — %s", exc)

    # Persist a final assistant chat message so reload/Apps re-entry shows a
    # natural conversation thread (user prompt → plan → "Built X with N
    # sections"). The proxy mirrors `chat_message` events to chat_messages,
    # so this writes to the DB on the way out.
    if websocket is not None and brand_name:
        try:
            await websocket.send_json({
                "type": "chat_message",
                "role": "agent",
                "content": (
                    f"✅ Built **{brand_name}** — {len(page_renders)} sections "
                    f"({', '.join((s.get('id') or s.get('type') or 'section') for s in (brief.get('sections') or [])[:4])}"
                    f"{'…' if len(brief.get('sections') or []) > 4 else ''})."
                ),
            })
        except Exception:
            pass

    return True


async def _send(websocket: Any, type_: str, message: str) -> None:
    if websocket is None:
        return
    try:
        await websocket.send_json({"type": type_, "message": message})
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────
#  Plan-confirmation gate (mirrors _generate_new_project_inner)
# ─────────────────────────────────────────────────────────────────────

async def _emit_plan_and_wait(
    *,
    brief: dict[str, Any],
    description: str,
    websocket: Any,
    chat_session_id: str,
) -> bool:
    """Show the plan derived from the Brief, then wait for user confirmation.

    Returns True if the user clicked "Looks Good" (or there's no UI to
    confirm with). Returns False on rejection / timeout — caller must
    abort the pipeline. Rejection-with-correction sets
    `websocket._plan_correction` so the orchestrator re-runs generation
    with the new prompt.
    """
    if websocket is None:
        return True

    import asyncio
    from app.services.project_generator import (
        register_plan_confirmation,
        pending_plan_confirmations,
        save_persisted_plan,
        clear_persisted_plan,
        _confirmation_key,
        PLAN_CONFIRM_TIMEOUT_SECONDS,
    )

    brand = dict(brief.get("brand") or {})
    typography = dict(brief.get("typography") or {})
    palette = dict(brief.get("palette") or {})
    motif = (brief.get("motif") or "").strip()
    personality = dict(brief.get("personality") or {})
    sections = list(brief.get("sections") or [])

    project_name = brand.get("name") or "your landing page"
    tagline = brand.get("tagline") or ""

    # Compose the section list as `pages` — frontend's PlanBubble renders
    # this as a structured list.
    page_items: list[dict[str, str]] = []
    for s in sections:
        stype = (s.get("type") or "").lower()
        if stype in {"footer"}:
            continue
        page_items.append({
            "name": (s.get("headline") or s.get("id") or stype or "section")[:80],
            "type": stype or "section",
        })

    design_bits: list[str] = []
    if motif:
        design_bits.append(f"{motif} motif")
    head_font = typography.get("heading_font") or ""
    body_font = typography.get("body_font") or ""
    if head_font or body_font:
        if head_font and body_font and head_font != body_font:
            design_bits.append(f"{head_font} + {body_font}")
        else:
            design_bits.append(head_font or body_font)
    primary = palette.get("primary") or ""
    if primary:
        design_bits.append(f"primary {primary}")
    vibe = personality.get("vibe_keywords") or []
    if vibe:
        design_bits.append(", ".join(vibe[:3]))
    design_line = " · ".join([b for b in design_bits if b])

    plan_data = {
        "intro": (
            f"I'll build **{project_name}** — {tagline}. "
            f"Here's my plan:" if tagline else
            f"I'll build **{project_name}**. Here's my plan:"
        ),
        "description": (brand.get("description") or description)[:280],
        "pages": page_items,
        "entities": [],  # landing pages don't have backend entities
        "design": design_line,
        "requiresConfirmation": True,
    }

    try:
        await websocket.send_json({
            "type": "chat_message",
            "role": "agent",
            "messageType": "plan",
            "planData": plan_data,
        })
        await save_persisted_plan(chat_session_id, plan_data, task=description)
    except Exception as exc:
        logger.warning("landing_pipeline: plan emit failed — %s", exc)
        # If we can't show the plan, skip the gate rather than block forever.
        return True

    try:
        await websocket.send_json({
            "type": "plan_awaiting_confirmation",
            "message": "Review your plan above and click 'Looks Good' to start building.",
        })
    except Exception:
        pass

    gate_key = _confirmation_key(websocket, chat_session_id)
    plan_future = register_plan_confirmation(gate_key)
    try:
        confirmation = await asyncio.wait_for(
            plan_future, timeout=PLAN_CONFIRM_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        logger.info("landing_pipeline: plan confirmation timed out")
        pending_plan_confirmations.pop(gate_key, None)
        await clear_persisted_plan(chat_session_id)
        await _send(
            websocket, "warning",
            "⏱️ Plan expired — send your message again to rebuild it.",
        )
        return False
    except Exception as exc:
        logger.warning("landing_pipeline: plan wait error — %s", exc)
        pending_plan_confirmations.pop(gate_key, None)
        await clear_persisted_plan(chat_session_id)
        return False

    if not confirmation.get("confirmed", True):
        correction = confirmation.get("correction", "") or ""
        await clear_persisted_plan(chat_session_id)
        if correction:
            websocket._plan_correction = correction
            await _send(websocket, "progress", f"🔄 Re-researching: {correction[:60]}…")
            logger.info("landing_pipeline: plan rejected with correction")
        else:
            await _send(websocket, "warning", "❌ Plan rejected — generation aborted.")
            logger.info("landing_pipeline: plan rejected without correction")
        return False

    await clear_persisted_plan(chat_session_id)
    await _send(websocket, "progress", "✅ Plan confirmed — starting code generation…")
    return True
