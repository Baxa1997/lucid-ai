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

from app.services.generation_build import run_generation_build_check
from app.services.generation_contract import GenerationResult
from app.services.agent_status import emit_task_phase, emit_agent_status

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
    generation = GenerationResult(pipeline="landing")
    anthropic_key = validated.get("anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY", "")

    if not anthropic_key:
        await _send(websocket, "error", "Service is missing its API key — please contact support.")
        return False

    # Pipeline-level telemetry. Wrapped fail-soft so telemetry can never
    # break generation — emit() itself is also fail-soft as a backstop.
    from app.services.telemetry import emit as _emit
    import time as _time
    _pipeline_t0 = _time.perf_counter()
    _emit(
        "pipeline.start",
        pipeline="landing",
        project_id=chat_session_id,
        description_len=len(description or ""),
        stack=(classification or {}).get("stack") or "",
    )

    # Pipeline mode (Phase 2 Step 3) — landing pipeline runs in "new" or
    # "edit" depending on whether the orchestrator handed us a fresh project
    # or a follow-up. Mirrors the same field on orchestrator's _send_phase
    # so the frontend can choose mode-aware copy without re-deriving from
    # sessionStorage.
    _landing_mode = "landing_generation" if (validated.get("new_project_mode") or validated.get("scratch_mode")) else "edit"

    # Drive the UI's task-phase indicator from inside this pipeline.
    # The orchestrator only emits Phase 3 "active" before calling us, so
    # without these the indicator stays stuck on RESEARCHING for the
    # entire run — which is what the user just reported.
    async def _phase(phase: int, title: str, desc: str, status: str) -> None:
        await emit_task_phase(
            websocket,
            phase=phase,
            title=title,
            description=desc,
            status=status,
            mode=_landing_mode,
        )

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

    # Close out Phase 1 (started by ws.py:got_task). Do NOT mark research
    # active yet: Stage 0 can still stop and ask a clarification question.
    # Showing "Researching..." before that question lands made the flow look
    # like it had started work and then changed its mind.
    # Phase 1 title kept in sync with orchestrator.py — renamed from
    # "Preparing workspace" (which collided with Phase 2's same title) to
    # "Validating inputs" so the progress chart shows distinct steps.
    await _phase(1, "Validating inputs", "Inputs validated", "done")
    await _send(websocket, "progress", "Checking whether I have enough detail…")

    # ── Stage 0: intent + clarifier gate ─────────────────────────────
    # analyze_intent runs FIRST (sequentially) so we can interrupt the
    # pipeline if the prompt is too vague to act on. Prior clarification
    # answers ride in via [LUCID_CLARIFY::key=value] markers prepended
    # by ws.py on the previous round; we strip them here, feed them as
    # already-known context to Gemini, and filter out matching questions.
    from knowledge.loader import extract_clarify_context
    from app.services.prompt_guards import is_gibberish, detect_scope_warnings
    prior_answers, clean_description = extract_clarify_context(description)

    # ── Gibberish gate (heuristic, free, no Gemini call) ─────────────
    # Catches keyboard mash like "asdasdas" before we spend a Gemini call
    # generating canned "what type of business is this?" disambiguation.
    # Skipped when the user has already started clarifying (prior_answers
    # non-empty) so we don't re-flag an in-progress conversation.
    SCOPE_ACK_KEY = "out_of_scope_ack"
    if not prior_answers and is_gibberish(clean_description):
        gibberish_payload = {
            "kind": "gibberish",
            "message": (
                "Looks like that came out as random characters — what would "
                "you like to build? A short description works best, e.g. "
                "“an Italian trattoria in Florence” or “a SaaS landing page”."
            ),
        }
        try:
            if chat_session_id:
                import json as _json
                from app.services.chat import ChatService
                await ChatService.add_message(
                    session_id=chat_session_id, role="agent",
                    content=gibberish_payload["message"],
                    event_type="GibberishDetected",
                    user_jwt=None,
                )
        except Exception as exc:
            logger.warning("landing_pipeline: gibberish persist failed — %s", exc)
        try:
            # Canonical `clarify` shape — matches step1_validate.py so the FE
            # dispatcher's single handler covers both paths and resets state
            # to 'ready'. Previously this emitted a one-off `gibberish_detected`
            # type with no FE handler, leaving the UI stuck on "Analyzing your
            # request…" until the user reloaded.
            await websocket.send_json({"type": "clarify", "message": gibberish_payload["message"]})
        except Exception:
            pass
        logger.info(
            "landing_pipeline: gibberish gate fired on prompt=%r — aborting pre-intent",
            clean_description[:60],
        )
        return False

    # ── Scope acknowledgment gate (heuristic, free) ──────────────────
    # Detect when the user mentioned features outside the landing-page
    # scope (auth, dashboards, billing, multi-page) and confirm with the
    # user before proceeding to research. Skipped once they've already
    # acknowledged (prior_answers[SCOPE_ACK_KEY] is set).
    scope_warnings = detect_scope_warnings(clean_description)
    if scope_warnings and SCOPE_ACK_KEY not in prior_answers:
        # Human-readable mapping so the chat message reads naturally.
        SCOPE_LABELS = {
            "auth_requested":          "login / signup / password reset",
            "dashboard_requested":     "a user dashboard or admin panel",
            "billing_requested":       "subscription billing / Stripe",
            "crud_requested":          "CRUD / record management",
            "multi_page_requested":    "a multi-page website",
            "multi_tenant_requested":  "multi-tenant / workspaces",
            "backend_requested":       "a custom backend / API",
        }
        items = [SCOPE_LABELS.get(k, k.replace("_", " ")) for k in scope_warnings]
        items_text = ", ".join(items[:-1]) + (" and " + items[-1] if len(items) > 1 else items[0])
        question_text = (
            f"I see you mentioned {items_text}. Right now I can only generate "
            "the landing page for this — full-app generation (multi-route, auth, "
            "dashboards, billing) is on the roadmap but not built yet. "
            "Should I proceed with just the landing page?"
        )
        clarify_payload = {
            "kind": "intent_clarify",
            "clarify_key": SCOPE_ACK_KEY,
            "question": question_text,
            "options": [
                {"id": "proceed_landing_only", "label": "Yes — generate the landing page only", "hint": "I'll skip the dashboard/auth parts for now."},
                {"id": "cancel",               "label": "No — cancel this generation",          "hint": "Stops here; nothing is generated."},
            ],
            "original_task": clean_description,
            "scope_warnings": scope_warnings,
        }
        try:
            if chat_session_id:
                import json as _json
                from app.services.chat import ChatService
                await ChatService.add_message(
                    session_id=chat_session_id, role="agent",
                    content=_json.dumps(clarify_payload),
                    event_type="ClarificationNeeded",
                    user_jwt=None,
                )
        except Exception as exc:
            logger.warning("landing_pipeline: scope-ack persist failed — %s", exc)
        try:
            await websocket.send_json({"type": "clarification_needed", **clarify_payload})
        except Exception:
            pass
        logger.info(
            "landing_pipeline: scope-ack gate fired — warnings=%s", scope_warnings,
        )
        return False

    # Honor a "cancel" answer from the scope-ack gate — exit cleanly.
    if prior_answers.get(SCOPE_ACK_KEY) == "cancel":
        try:
            await websocket.send_json({"type": "info", "message": "Generation canceled."})
        except Exception:
            pass
        logger.info("landing_pipeline: user canceled at scope-ack gate")
        return False

    intent_input = clean_description
    if prior_answers:
        # Append already-known disambiguations as plain prose so Gemini
        # treats them as constraints rather than re-asks them. Skip the
        # internal SCOPE_ACK_KEY — Gemini doesn't need to know about it.
        gemini_answers = {k: v for k, v in prior_answers.items() if k != SCOPE_ACK_KEY}
        if gemini_answers:
            hints = "\n".join(f"- {k.replace('_', ' ')}: {v.replace('_', ' ')}" for k, v in gemini_answers.items())
            intent_input = f"{clean_description}\n\nAlready clarified by the user:\n{hints}"
        logger.info("landing_pipeline: %d prior clarifications applied — %s",
                    len(prior_answers), list(prior_answers.keys()))

    try:
        intent = await analyze_intent(
            intent_input, classification,
            websocket=websocket, timeout_s=60.0,
        )
    except Exception as exc:
        logger.warning("landing_pipeline: intent failed — proceeding without gate: %s", exc)
        intent = None

    # Filter out questions whose key was already answered, then decide
    # whether to gate. We only block on clarity_level=low (low = the
    # page won't be coherent without input). Medium/high pass through.
    if intent:
        remaining_qs = [
            q for q in intent.get("clarification_questions") or []
            if q.get("key") and q["key"] not in prior_answers
        ]
        if intent.get("clarity_level") == "low" and remaining_qs:
            # Emit ONE question per round. The frontend bubble + WS
            # handler ping-pong with [LUCID_CLARIFY::...] markers; on
            # the next pass this code re-runs, finds the answer in
            # prior_answers, and either gates again on the next
            # remaining question or proceeds.
            q = remaining_qs[0]
            payload = {
                "kind": "intent_clarify",
                "clarify_key": q["key"],
                "question": q["question"],
                "options": q["options"],
                "original_task": clean_description,
            }
            try:
                if chat_session_id:
                    import json as _json
                    from app.services.chat import ChatService
                    await ChatService.add_message(
                        session_id=chat_session_id, role="agent",
                        content=_json.dumps(payload),
                        event_type="ClarificationNeeded",
                        user_jwt=None,
                    )
            except Exception as exc:
                logger.warning("landing_pipeline: clarify persist failed — %s", exc)
            try:
                await websocket.send_json({"type": "clarification_needed", **payload})
            except Exception:
                pass
            logger.info(
                "landing_pipeline: gating on clarification key=%s (%d remaining)",
                q["key"], len(remaining_qs),
            )
            return False

    await _phase(3, "Researching project", "Searching real reference sites + design DNA…", "active")
    await _send(websocket, "progress", "Researching your brand…")

    async def _research_signals() -> tuple[dict | None, dict | None, dict | None]:
        """parallel(domain, design) → extract. Best-effort — any failure
        returns (intent, partial, partial) so the legacy brief still
        ships. Reuses the intent we computed in Stage 0."""
        if not intent:
            return None, None, None
        try:
            domain_res, design_res = await asyncio.gather(
                run_domain_research(intent, websocket=websocket, timeout_s=240.0),
                run_design_research(intent, websocket=websocket, timeout_s=240.0),
            )
        except Exception as exc:
            logger.warning("landing_pipeline: research failed (non-fatal) — %s", exc)
            return intent, None, None

        # Research is the foundation for everything Claude writes. If one side
        # comes back weak (tool failed, no grounding, snippet-loop detected),
        # retry that side once before distilling. This keeps the pipeline stable
        # without paying for a second full research pass when the first pass was
        # already good.
        async def _retry_weak_research(
            label: str,
            data: dict | None,
            retry_fn,
        ) -> dict | None:
            if _research_summary_is_strong(data):
                return data
            summary = (data or {}).get("_summary") or {}
            logger.warning(
                "landing_pipeline: %s research weak — retrying once (summary=%s)",
                label, summary,
            )
            try:
                retried = await retry_fn(intent, websocket=websocket, timeout_s=240.0)
            except Exception as exc:
                logger.warning("landing_pipeline: %s research retry failed — %s", label, exc)
                return data
            if _research_summary_score(retried) >= _research_summary_score(data):
                logger.info(
                    "landing_pipeline: %s research retry accepted (old=%s new=%s)",
                    label, summary, (retried or {}).get("_summary") or {},
                )
                return retried
            logger.warning(
                "landing_pipeline: %s research retry did not improve — keeping first result",
                label,
            )
            return data

        domain_res, design_res = await asyncio.gather(
            _retry_weak_research("domain", domain_res, run_domain_research),
            _retry_weak_research("design", design_res, run_design_research),
        )

        try:
            signals = await extract_research_signals(
                intent, domain_res, design_res,
                timeout_s=90.0,
            )
        except Exception as exc:
            logger.warning("landing_pipeline: signal extract failed (non-fatal) — %s", exc)
            return intent, domain_res, None
        return intent, {"domain": domain_res, "design": design_res}, signals

    try:
        brief, research_bundle = await asyncio.gather(
            build_landing_brief(
                clean_description, classification,
                websocket=websocket,
            ),
            _research_signals(),
        )
    except Exception as exc:
        logger.error("landing_pipeline: brief failed — %s", exc, exc_info=True)
        await _send(websocket, "error", "Couldn't research your brand — please try again.")
        return False

    sections = brief.get("sections") or []
    if not sections:
        await _send(websocket, "error", "Couldn't plan your sections — try a more specific description.")
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

    # Inject the PURPOSE_DIRECTIVE into the brief so section codegen can
    # render it in every Claude prompt. No-op for purposes the directive
    # map doesn't cover. Built from the SAME analyze_intent dict the brief
    # was built from, so named_roles / urgency_signals stay consistent.
    if _intent:
        try:
            from app.services.purpose_research import format_purpose_directive_block
            _directive = format_purpose_directive_block(_intent)
            if _directive:
                brief["purpose_directive"] = _directive
                logger.info(
                    "landing_pipeline: brief tagged with purpose=%s roles=%d",
                    _intent.get("primary_purpose"),
                    len(_intent.get("named_roles") or []),
                )
        except Exception as exc:
            logger.warning("landing_pipeline: directive injection failed (non-fatal) — %s", exc)

    try:
        from app.services.plan_extras import summarize_grounded_research
        _research_meta = summarize_grounded_research(
            domain_res=(_research or {}).get("domain") if isinstance(_research, dict) else None,
            design_res=(_research or {}).get("design") if isinstance(_research, dict) else None,
        )
        await _send(
            websocket,
            "progress",
            f"Research ready — {_research_meta['confidence'].lower()} confidence, "
            f"{_research_meta['sources']} sources.",
        )
    except Exception:
        pass

    brand_name = (brief.get("brand") or {}).get("name", "")
    await _send(
        websocket,
        "progress",
        f"Designing {brand_name or 'your site'}…",
    )
    await _phase(
        3,
        "Researching project",
        f"Brief ready — {len(sections)} sections, palette + typography",
        "done",
    )

    # ── OPT-IN: Reference screenshots for multimodal grounding ───────
    # Fetches 2-3 screenshots of brief.references[*].url and stashes them
    # under /tmp/lucid_screenshots/<project_id>/ for downstream Claude
    # codegen to attach as image inputs. Entirely fail-soft: if
    # SCREENSHOT_API_KEY is unset OR any fetch fails, returns [] and the
    # pipeline proceeds with text-only prompts (unchanged behavior).
    reference_screenshots: list[bytes] = []
    try:
        from app.services.landing_vision_refs import fetch_landing_reference_screenshots
        reference_screenshots = await fetch_landing_reference_screenshots(
            brief=brief,
            project_id=chat_session_id or brand_name or "anon",
            websocket=websocket,
        )
    except Exception as _vision_exc:
        logger.warning(
            "landing_pipeline: reference screenshot fetch failed (non-fatal): %s",
            _vision_exc,
        )
        reference_screenshots = []

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
        research_bundle=research_bundle,
        websocket=websocket,
        chat_session_id=chat_session_id,
    )
    if not confirmed:
        # User rejected, timed out, or set a correction. The orchestrator
        # checks for `_plan_correction` and re-runs with the new prompt.
        return False

    # Phase 4 was never set active between plan-confirm and Stage 5 codegen,
    # so the status pill sat on "Research complete" while runtime content +
    # fixers ran. Flip phase 4 active here so the UI tracks the in-between
    # work; Stage 5 below marks it done before flipping to "Writing code".
    await _phase(4, "Planning code", "Writing runtime content + setup…", "active")

    # ── Step 2: Runtime content JSON ─────────────────────────────────
    await emit_agent_status(
        websocket,
        key="writing_content",
        label="Saving your content…",
        description="Writing brief into src/content/landing.json",
        state="active",
        source="landing_pipeline",
    )
    from app.services.landing_content import write_landing_content
    try:
        content_path, _content = write_landing_content(workspace_path, brief)
        logger.info("landing_pipeline: wrote %s", content_path)
    except Exception as exc:
        logger.error("landing_pipeline: content write failed — %s", exc, exc_info=True)
        await _send(websocket, "error", "Couldn't save your content — please try again.")
        return False

    # ── Step 3: Image binding (Unsplash) ─────────────────────────────
    await emit_agent_status(
        websocket,
        key="binding_images",
        label="Finding photography…",
        description="Searching Unsplash for hero + section imagery",
        state="active",
        source="landing_pipeline",
    )
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
    #
    # CRITICAL — preserve PARALLEL INDEX with section.items. The codegen
    # prompt promises Claude that `section.images[i]` corresponds 1:1 to
    # `section.items[i]`. If the binder failed to fetch image #2 (empty
    # url), we MUST keep an empty string at index 2 — dropping it shifts
    # every later image into the wrong slot and Claude's `<Image src=...>`
    # renders the wrong photo for every later card.
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
                    # KEEP empty strings for missing slots — preserves index.
                    _s["images"] = [_i.get("url") or "" for _i in _imgs]
            with open(_content_path, "w", encoding="utf-8") as _fh:
                _json.dump(_content, _fh, indent=2, ensure_ascii=False)
            logger.info("landing_pipeline: flattened section.images to URL strings")
    except Exception as _exc:
        logger.warning("landing_pipeline: image flatten failed (non-fatal) — %s", _exc)

    # ── Step 4: Phase-0 deterministic builders ───────────────────────
    await emit_agent_status(
        websocket,
        key="setting_up_theme",
        label="Setting up theme + layout…",
        description="globals.css, layout.jsx, design-system.js",
        state="active",
        source="landing_pipeline",
    )
    from app.services.landing_phase0 import run_landing_phase0
    try:
        run_landing_phase0(workspace_path, brief)
    except Exception as exc:
        logger.error("landing_pipeline: phase0 failed — %s", exc, exc_info=True)
        await _send(websocket, "error", "Couldn't set up the project — please try again.")
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
    await _phase(4, "Planning code", "Plan ready", "done")
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
            reference_images=reference_screenshots,
        )
        generation.add_files(result.get("files_written") or [])
    except Exception as exc:
        logger.error("landing_pipeline: section codegen failed — %s", exc, exc_info=True)
        await _send(websocket, "error", "Couldn't build your sections — please try again.")
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
        await _send(websocket, "error", "Couldn't build any sections — please try a different description.")
        return False

    # ── Step 6: app/page.jsx shell ───────────────────────────────────
    try:
        from app.services.landing_section_codegen import _resolve_anatomy as _resolve_header_anatomy
        header_anatomy_text, _ = _resolve_header_anatomy(
            "header",
            brief.get("visual_dna") or {},
            brief=brief,
        )
        page_shell_path = write_landing_page_shell(
            workspace_path,
            page_imports,
            page_renders,
            header_anatomy=header_anatomy_text,
        )
        try:
            generation.add_files([os.path.relpath(page_shell_path, workspace_path)])
        except Exception:
            generation.add_files([page_shell_path])
    except Exception as exc:
        logger.error("landing_pipeline: page shell failed — %s", exc, exc_info=True)
        await _send(websocket, "error", "Couldn't assemble your page — please try again.")
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

    # ── Step 8: Build verification — BACKGROUND TASK ─────────────────
    # Verification runs `next build` (~60-120s) + an optional Codex fix
    # loop. Previously this BLOCKED the pipeline before workspace promotion
    # and before the live dev-server preview could boot — easily adding
    # 2-3 min of perceived wait when the user's preview iframe could
    # have been showing instantly.
    #
    # Strategy: kick BV off as an asyncio.Task and let landing_pipeline
    # return immediately. The orchestrator's Phase 7 (publish) awaits the
    # task before deciding draft-only-vs-Vercel-deploy. Meanwhile:
    #   • Sandpack instant preview shows ~0s after this returns
    #   • Dev-server iframe preview boots in parallel (~30-60s)
    #   • BV finishes concurrently (~60-120s) — when it's done, Phase 7
    #     reads `_build_ok` and either promotes staging→main+Vercel, or
    #     emits the "saved to draft" banner.
    #
    # Provisional `_build_ok = True` lets gates that read it early (e.g.
    # quality_gate side-channel) treat the build as passing until proved
    # otherwise. Phase 7 always re-reads after awaiting the task.
    try:
        setattr(websocket, "_build_ok", True)
    except Exception:
        pass
    await _phase(6, "Verifying build", "Running build in background while preview boots…", "active")
    _build_task = asyncio.create_task(
        run_generation_build_check(
            pipeline="landing_pipeline",
            workspace_path=workspace_path,
            api_key=anthropic_key,
            classification=classification or {},
            websocket=websocket,
            # max_retries=0 means ONE build attempt, no auto-fix retry. The
            # fix loop was adding 2-5 min per generation for diminishing return
            # — most build failures here are install-time (handled separately)
            # or genuine bugs the user wants to see + edit, not silent fixes.
            max_retries=0,
            send=lambda kind, message: _send(websocket, kind, message),
            phase=lambda status, state: _phase(6, "Verifying build", status, state),
            progress_message="Verifying build in background…",
            generation=generation,
        )
    )
    try:
        setattr(websocket, "_build_task", _build_task)
    except Exception:
        pass

    # ── Step 9: Quality gate ─────────────────────────────────────────
    # Verify the built page actually fulfils its purpose contract
    # (hiring → form + pay numbers + named roles, etc.). Never blocks
    # completion — the report is surfaced to the workspace UI so the
    # user can trigger per-section regeneration if anything is missing.
    try:
        from app.services.landing_quality_gate import run_quality_gate
        await run_quality_gate(workspace_path, _intent, websocket=websocket)
    except Exception as exc:
        logger.warning("landing_pipeline: quality_gate failed (non-fatal) — %s", exc)

    await _send(websocket, "progress", "Your site is ready!")
    generation.ok = True
    generation.metadata.update({
        "sections_written": len(page_renders),
    })
    try:
        setattr(websocket, "_generation_result", generation.as_dict())
    except Exception:
        pass

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

                # Promote the generated workspace to PREVIEW_WS_ROOT so that
                # the next session-reconnect resolves to it via
                # `preview_workspace_path(project_id)`. Without this, every
                # re-entry creates a fresh empty workspace and the user sees
                # the bare template instead of their generated site. Use a
                # symlink so the live preview server keeps running off the
                # original lucid_new_* path with no file movement.
                _effective_pid = update_payload.get("project_id") or current_pid
                if _effective_pid:
                    try:
                        from app.paths import preview_workspace_path
                        _target = preview_workspace_path(_effective_pid)
                        if os.path.isdir(workspace_path) and not os.path.lexists(_target):
                            os.symlink(workspace_path, _target)
                            logger.info(
                                "landing_pipeline: promoted workspace to %s → %s",
                                _target, workspace_path,
                            )
                    except Exception as _sym_exc:
                        logger.warning(
                            "landing_pipeline: workspace promotion failed (non-fatal) — %s",
                            _sym_exc,
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
                    f"**{brand_name}** is ready. Type a message below to edit the design, "
                    "copy, or sections — or click Publish to share it."
                ),
            })
        except Exception:
            pass

    _emit(
        "pipeline.complete",
        pipeline="landing",
        project_id=chat_session_id,
        success=True,
        duration_sec=round(_time.perf_counter() - _pipeline_t0, 2),
        section_count=len((generation.sections if hasattr(generation, "sections") else []) or []),
        brand_name=brand_name or "",
    )
    return True


def _research_summary_score(data: dict | None) -> int:
    """Rank a research result for retry decisions.

    Weighted toward grounded calls and source count, then successful text. A
    degenerate snippet-loop call is penalized heavily because it actively
    poisons downstream distillation.
    """
    summary = (data or {}).get("_summary") or {}
    return (
        int(summary.get("calls_grounded", 0) or 0) * 20
        + int(summary.get("total_sources", 0) or 0)
        + int(summary.get("calls_succeeded", 0) or 0) * 5
        - int(summary.get("calls_degenerate", 0) or 0) * 30
    )


def _research_summary_is_strong(data: dict | None) -> bool:
    """True when a 4-call research bundle is good enough to distill."""
    summary = (data or {}).get("_summary") or {}
    return (
        int(summary.get("calls_succeeded", 0) or 0) >= 3
        and int(summary.get("calls_grounded", 0) or 0) >= 2
        and int(summary.get("total_sources", 0) or 0) >= 5
        and int(summary.get("calls_degenerate", 0) or 0) == 0
    )


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
    research_bundle: tuple[dict | None, dict | None, dict | None] | None = None,
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
    _intent, _research, _signals = research_bundle or (None, None, None)
    domain_res = (_research or {}).get("domain") if isinstance(_research, dict) else None
    design_res = (_research or {}).get("design") if isinstance(_research, dict) else None
    from app.services.plan_extras import (
        compact_count,
        summarize_grounded_research,
        summary_chip,
    )
    research_summary = summarize_grounded_research(
        domain_res=domain_res,
        design_res=design_res,
    )

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
        "planSummary": [
            summary_chip("Type", "Landing page"),
            summary_chip("Scope", compact_count("", len(page_items), "section")),
            summary_chip("Research", f"{research_summary['confidence']} · {research_summary['sources']} sources"),
        ],
        "research": research_summary,
        "pages": page_items,
        "entities": [],  # landing pages don't have backend entities
        "design": design_line,
        "buildSteps": [
            "Write editable page content into a structured content file",
            "Generate each section as its own component",
            "Run fixers and a production build check before preview",
        ],
        "assumptions": [
            "This is a conversion-focused single-page experience",
            "Copy and imagery should match the brand voice in the brief",
        ],
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
            await _send(websocket, "progress", "Re-researching with your changes…")
            logger.info("landing_pipeline: plan rejected with correction")
        else:
            await _send(websocket, "warning", "Generation canceled.")
            logger.info("landing_pipeline: plan rejected without correction")
        return False

    await clear_persisted_plan(chat_session_id)
    await _send(websocket, "progress", "Plan confirmed — building your site now…")
    return True
