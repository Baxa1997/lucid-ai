"""Gemini deep-research engine.

Extracted from project_generator.py (god-module split). Owns the entire
research stage of project generation:

  gemini_deep_research()           — single-pass ultra-deep product research
  enrich_research_with_deep_dives() — Phase D: parallel per-entity/per-page
                                      focused research calls
  _validate_project_intent() / _expand_short_prompt() — pre-research gates
  _extract_research_section() / _distill_research() /
  _extract_layout_archetype()      — research-blob parsing used by the
                                      generation phases downstream

Plus the Phase-D tunables, archetype routing sets, the shared Gemini
semaphore and the cultural-anchor pool. No external module imported any
of these directly — project_generator re-exports them for its internal
call sites.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re

from app.services.ws_emit import _ws_send

logger = logging.getLogger("lucid.project_generator")


# ── Phase D: parallel deep-research tunables ────────────────────────────
# Off by default. When enabled, projects with many entities or many pages
# get a *second* research pass: one focused Gemini call per entity (admin)
# or per page (multi-page consumer/portfolio/blog). Output is appended to
# the existing research blob as ``===ENTITY_DEEP::X===`` / ``===PAGE_DEEP::Y===``
# blocks, which Phase 2 prompts can pluck for richer per-unit content.
#
# Why optional + thresholded:
#   • Each call costs a Gemini API hit and ~10–60 s wall time.
#   • Small projects (3 pages, 2 entities) already get enough depth from
#     the single original research pass — extra calls are pure cost.
#   • Landing pages NEVER trigger this (already gated by archetype).
# Default ON: the wiring gap is closed. build_project_schema now plucks each
# ===ENTITY_DEEP::Name=== / ===PAGE_DEEP::Name=== block from the raw research
# blob and attaches it to the matching schema entity/page as
# ``deep_research``, which schema_to_entity_screens_spec /
# schema_to_pages_spec render directly into Phase 2 prompts. Set
# PHASE_D_DEEP_RESEARCH=0 to disable (e.g. when debugging cost).
_PHASE_D_DEEP_RESEARCH_ENABLED = os.environ.get("PHASE_D_DEEP_RESEARCH", "1") == "1"
# Below these counts, the original single-pass research is enough depth.
_PHASE_D_MIN_ENTITIES = 4
_PHASE_D_MIN_PAGES = 5
# Hard cap on concurrent Gemini calls inside a single project. The shared
# ``_gemini_semaphore`` (= 2) is the global ceiling; this is the per-project
# ceiling so one big project can't starve other in-flight projects.
_PHASE_D_MAX_PARALLEL = 4
# One focused per-unit research call should never take longer than this.
# A timeout here just means that unit falls back to the base research blob,
# which is fail-soft (the Phase 2 prompt simply gets less per-unit context).
_PHASE_D_PER_CALL_TIMEOUT = 60.0


# Layout archetypes that route through the entity-driven admin code path.
# Used by Phase D, Phase 2 admin batching, and _is_admin checks downstream.
_ADMIN_LAYOUT_ARCHETYPES = frozenset({
    "admin_dashboard", "crm", "tms", "saas_dashboard", "ecommerce",
})
_MULTIPAGE_CONSUMER_ARCHETYPES = frozenset({
    "consumer_website", "marketplace", "portfolio", "blog",
})


def _should_run_deep_research(
    layout_archetype: str,
    entities: list | None,
    pages: list | None,
) -> bool:
    """Gate for Phase D parallel per-unit deep research.

    True only when ALL hold:
      • PHASE_D_DEEP_RESEARCH != "0" (default ON)
      • Archetype is a multi-unit one (admin OR multi-page consumer/blog/portfolio)
      • Unit count meets threshold (≥ entities for admin, ≥ pages for consumer)

    Single-page landings always return False — they are one cohesive narrative
    and the original research already has full depth for that one page.
    """
    if not _PHASE_D_DEEP_RESEARCH_ENABLED:
        return False
    archetype = (layout_archetype or "").lower()
    if archetype in {"single_page_landing", "landing"}:
        return False
    if archetype in _ADMIN_LAYOUT_ARCHETYPES:
        return isinstance(entities, list) and len(entities) >= _PHASE_D_MIN_ENTITIES
    if archetype in _MULTIPAGE_CONSUMER_ARCHETYPES:
        return isinstance(pages, list) and len(pages) >= _PHASE_D_MIN_PAGES
    return False


def _extract_research_section(text: str, header: str, max_chars: int = 2000) -> str:
    """Extract the content of a ===HEADER=== block from Gemini research output."""
    if header not in text:
        return ""
    start = text.index(header) + len(header)
    rest  = text[start:]
    end_match = rest.find("===")
    end = start + end_match if end_match != -1 else start + max_chars
    return text[start:end].strip()


# Per-section caps for research distillation — tuned so the sum stays under ~8K.
# Order matters: the most actionable bits (palette, typography, pages, entities)
# come first so if the model only reads the head of the block, it still gets
# the highest-signal content.
_DISTILL_SECTIONS: tuple[tuple[str, int], ...] = (
    ("DESIGN_SYSTEM_NAME", 100),
    ("CLASSIFICATION", 300),
    # User-stated requirements (loader, cursor, animations, copy specifics, …) —
    # MUST survive distillation; Phase 1/2/3 prompts treat this as non-negotiable.
    # Placed early so even an extreme truncation keeps it.
    ("USER_REQUIREMENTS", 1500),
    ("DOMAIN", 250),
    ("VIBE", 300),
    ("PALETTE", 700),
    ("TYPOGRAPHY", 400),
    # Director blocks — always keep. BRAND_MARK guarantees the logo is built.
    # RADIUS_TOKENS locks border-radius consistency across elements.
    # IMAGE_COMPOSITION prevents low-contrast text-over-image + glass forms.
    # COPY_DECK provides every hero/section/feature/microcopy string so
    # Phase 1-3 don't fall back to generic "Learn More / Our Services" filler.
    ("BRAND_MARK", 400),
    ("RADIUS_TOKENS", 300),
    ("IMAGE_COMPOSITION", 700),
    ("COPY_DECK", 3000),
    ("COPY_TONE", 500),
    # Vision-grounded DNA from actual screenshots of reference sites.
    # Placed BEFORE LAYOUT_BLUEPRINT so if the model truncates, the concrete
    # visual observations survive — Claude leans on them heavily for hero
    # composition, card language, and motion cues.
    ("VISUAL_DNA", 1800),
    ("LIVE_UI_RESEARCH", 1200),  # scroll effects, counters, marquee, hover depth, ambient — from actual 2025-2026 site research
    # The system prompt at l.4705 declares VISUAL_DISTINCTIVENESS contents
    # MANDATORY (anti_generic + visual_surprise + section_card_matrix). If the
    # block is missing from the distilled research, the mandate silently
    # becomes a no-op and the page comes out generic. Keep it in the allowlist.
    ("VISUAL_DISTINCTIVENESS", 1500),
    # CULTURAL_ATMOSPHERE.signature_imagery is consumed deterministically for
    # Unsplash keywords, but the surrounding mood / sensory / spatial cues
    # help Claude write copy and pick visual language that fits the domain.
    ("CULTURAL_ATMOSPHERE", 1000),
    # ERA_CALIBRATION carries era-specific tokens (deco, neon, brutalist…)
    # that downstream styling rules reference.
    ("ERA_CALIBRATION", 600),
    ("LAYOUT_BLUEPRINT", 7000),  # Design DNA: 25 creative variables per project (hero/features/rhythm/motif/mood/cards/type/motion/pattern/radius/color/hover/spacing + live_ui_recipe/scroll_reveal/counter/marquee/ambient)
    # Admin/CRM/TMS visual language — table/form/sidebar/status/density recipe.
    # Only present when layout_archetype is admin-family.
    ("ADMIN_UI_LANGUAGE", 2200),
    ("PAGES", 1400),
    ("SECTIONS", 1400),
    ("ENTITIES", 1200),
    # ENTITY_SCREENS holds per-entity list/detail/create UI spec (admin only).
    # 4000 cap fits ~4 entities of full detail; remaining entities still flow
    # through schema_to_entity_screens_spec which reads parsed schema, not the
    # truncated distilled blob. Symmetric to how PAGES is rendered.
    ("ENTITY_SCREENS", 4000),
    ("KEY_COMPONENTS", 1400),
    ("DOMAIN_MUST_HAVES", 900),
    ("UI_PATTERNS", 500),
)


def _distill_research(research: str, max_total: int = 16000) -> str:
    """Compress the raw Gemini research dump into a compact bullet plan.

    The raw research is typically 10–20K chars with many ===SECTION=== blocks
    (and often long prose inside each). This helper:
      - Picks out the sections known to be load-bearing for UI generation.
      - Truncates each to a per-section cap, total bounded by max_total.
      - Re-emits the same ===HEADER=== markers so downstream extractors still
        find what they need.

    If research is already short or lacks ===HEADERS=== (fallback path),
    return it head-truncated. This keeps the helper robust over messy input.
    """
    if not research:
        return ""

    # No structured blocks → just head-truncate.
    if "===" not in research:
        return research[:max_total]

    parts: list[str] = []
    total = 0
    for name, cap in _DISTILL_SECTIONS:
        body = _extract_research_section(research, f"==={name}===", max_chars=cap)
        if not body:
            continue
        if len(body) > cap:
            body = body[:cap].rstrip() + "…"
        block = f"==={name}===\n{body.strip()}\n"
        if total + len(block) > max_total:
            # Leave room for at least the header of the cut block so callers
            # can tell it existed.
            remaining = max_total - total - len(f"==={name}===\n…\n")
            if remaining > 200:
                parts.append(f"==={name}===\n{body.strip()[:remaining]}…\n")
            break
        parts.append(block)
        total += len(block)

    distilled = "\n".join(parts).strip()
    return distilled or research[:max_total]


def _extract_layout_archetype(research: str, fallback_classification: dict) -> dict:
    """Extract the confirmed layout archetype from Gemini's ===CLASSIFICATION=== section.

    Gemini may refine the initial classification after research. This reads its
    decision and builds an updated classification dict — subject to two constraints:

    1. classification_locked=True means the initial classification came from an explicit
       user keyword (e.g. "landing page", "CRM") and must not be overridden.
    2. Gemini may only refine WITHIN the same structural family
       (single / consumer / admin). Cross-family changes are rejected.
    """
    from knowledge.loader import LAYOUT_ARCHETYPES, _build_rich_classification, get_structural_family

    # If the initial classification was locked by keyword matching, honour the user's intent.
    if fallback_classification.get("classification_locked"):
        logger.info(
            "Classification locked at %s — ignoring Gemini research override",
            fallback_classification["layout_archetype"],
        )
        return fallback_classification

    section = ""
    if "===CLASSIFICATION===" in research:
        _cs = research.index("===CLASSIFICATION===") + len("===CLASSIFICATION===")
        _ce_match = research[_cs:].find("===")
        _ce = _cs + _ce_match if _ce_match != -1 else _cs + 800
        section = research[_cs:_ce].strip()

    if not section:
        return fallback_classification

    # Parse layout_archetype line
    layout = fallback_classification["layout_archetype"]
    domain = fallback_classification["domain"]

    for line in section.splitlines():
        line = line.strip()
        if line.startswith("layout_archetype:"):
            val = line.split(":", 1)[1].strip().lower()
            if val in LAYOUT_ARCHETYPES:
                layout = val
        elif line.startswith("domain:"):
            val = line.split(":", 1)[1].strip().lower()
            if val:
                domain = val

    # Reject cross-family changes — Gemini can refine within a family
    # (e.g. admin_dashboard → crm) but cannot cross structural boundaries.
    initial_family = get_structural_family(fallback_classification["layout_archetype"])
    proposed_family = get_structural_family(layout)
    if initial_family != proposed_family:
        logger.warning(
            "Gemini tried to cross structural family boundary %s→%s (%s→%s) — keeping initial",
            initial_family, proposed_family,
            fallback_classification["layout_archetype"], layout,
        )
        return fallback_classification

    result = _build_rich_classification(layout, domain)
    logger.info("Post-research classification: layout=%s domain=%s", layout, domain)
    return result


# ╔══════════════════════════════════════════════════════════════╗
# ║  STEP 2.0 — _validate_project_intent()                       ║
# ║  Cheap pre-flight check. Catches "asdfasdf", "hi", "test"   ║
# ║  before we burn 3-5 minutes generating nonsense.            ║
# ╚══════════════════════════════════════════════════════════════╝

async def _validate_project_intent(description: str) -> dict:
    """Score whether the input is a real project description.

    Auth handled by gemini_post via Vertex ADC. Returns
    ``{"is_project": bool, "score": int, "ask_user": str}``.
      • ``score`` 0–10. Below 5 = clearly not a project.
      • ``ask_user`` is a one-sentence clarifying prompt the caller can
        send straight to the chat when ``is_project`` is False.

    Fail-soft: any error (Gemini down, malformed reply) returns
    ``is_project=True`` so the existing pipeline keeps working — we only
    BLOCK on a confident "no", never on uncertainty.
    """
    text = (description or "").strip()
    # Trivial fast path — anything below 6 chars after strip is almost
    # certainly not a project description and saves an API call.
    if len(text) < 6:
        return {
            "is_project": False,
            "score": 0,
            "ask_user": "Could you describe what you'd like to build? "
                        "Try: \"a [type of site/app] for [audience] that [main feature]\".",
        }

    prompt = f"""You are a triage assistant for a website-generation tool.

Input from the user: \"\"\"{text[:2000]}\"\"\"

Decide whether this is a coherent description of a website / app / admin tool the user
wants built. Score 0–10 where:
  10 = clear project ("a CRM for solo realtors with kanban deals and email logs")
  6–9 = workable but thin ("yoga studio", "coffee landing page")
  3–5 = ambiguous ("something cool", "make a thing")
  0–2 = noise ("asdfasdf", "hello", "test", "what can you do")

Return ONLY this JSON, no markdown:
{{
  "is_project": <true if score ≥ 5 else false>,
  "score": <int 0-10>,
  "ask_user": "<one short sentence asking what they want — empty string if score ≥ 5>"
}}"""

    try:
        import json as _json, re as _re
        from app.services.gemini_http import gemini_post

        _status, _resp_json, _ = await gemini_post(
            model="gemini-3.5-flash",
            payload={"contents": [{"parts": [{"text": prompt}]}]},
            timeout_s=10.0,
            label="intent_gate",
        )
        if _status != 200 or _resp_json is None:
            logger.debug("intent_gate: HTTP %d — passing input through", _status)
            return {"is_project": True, "score": 10, "ask_user": ""}
        try:
            _u = _resp_json.get("usageMetadata") or {}
            _in = int(_u.get("promptTokenCount", 0) or 0)
            _out = int(_u.get("candidatesTokenCount", 0) or 0) + int(_u.get("thoughtsTokenCount", 0) or 0)
            if _in or _out:
                from app.services.billing_meter import report_token_usage
                report_token_usage(None, _in, _out, source="gemini_intent_gate")
        except Exception:
            pass
        from knowledge.loader import safe_gemini_text
        raw = safe_gemini_text(_resp_json).strip()
        if "```" in raw:
            raw = _re.sub(r"```(?:json)?", "", raw).strip("`").strip()
        parsed = _json.loads(raw)
        score = int(parsed.get("score", 10))
        is_project = bool(parsed.get("is_project", score >= 5))
        ask_user = str(parsed.get("ask_user", "") or "")
        if not is_project and not ask_user:
            ask_user = (
                "I couldn't tell what you'd like to build — could you describe it like "
                "\"a [type of site/app] for [audience] that [main feature]\"?"
            )
        logger.info("intent_gate: score=%d is_project=%s", score, is_project)
        return {"is_project": is_project, "score": score, "ask_user": ask_user}
    except Exception as exc:
        logger.debug("intent_gate: skipped due to error (%s) — passing input through", exc)
        return {"is_project": True, "score": 10, "ask_user": ""}


# ╔══════════════════════════════════════════════════════════════╗
# ║  STEP 2.5 — _expand_short_prompt()                          ║
# ║  Expand 1-3 word prompts via Gemini Flash before research   ║
# ╚══════════════════════════════════════════════════════════════╝

async def _expand_short_prompt(
    description: str,
    layout_archetype: str,
    domain: str,
    websocket=None,
) -> str:
    """Expand a very short user prompt into a richer brief for downstream stages.

    Short prompts ("ACCA website", "yoga studio app") give Gemini deep-research
    nothing concrete to anchor on, so its ===PAGES===, ===ENTITIES===, ===HEADER===
    blocks come back empty and the plan + schema render with bare fallbacks.

    This helper calls Gemini Flash (cheap, sub-second) to flesh the prompt out
    with: real organization name when applicable, audience, 5-7 likely pages,
    and 1-2 visual/tone qualities. Result feeds the cache key, deep research,
    schema parsing, and plan rendering — one upstream fix, multiple downstream
    wins. Fail-soft: returns the original description on any error.
    """
    _clean = description.split("\n\n---\n\n")[0].strip()
    # Skip expansion if prompt already has substance
    if len(_clean.split()) > 4 or len(_clean) > 50:
        return description

    # Locale hint derived from the ORIGINAL short input — must be captured
    # before expansion. The expansion that follows would otherwise silently
    # translate "kino" / Cyrillic / etc. into English and we'd lose the
    # market signal. We feed the hint back into the expansion prompt so the
    # resulting brief stays anchored to the user's actual market.
    _origin_locale = _detect_user_locale_hint(_clean)
    _locale_clause = ""
    if _origin_locale:
        _locale_clause = (
            f"\n\nLOCALE NOTE: The user wrote in a {_origin_locale} context. "
            "The expanded brief MUST reflect that market — pick a brand/concept "
            "name that would feel native there (NOT a Western default), reference "
            "the local audience by name, and mention 1-2 market-native peers (e.g. "
            "Russian/CIS movies → Kinopoisk/IVI/Okko; CIS commerce → Wildberries/"
            "Ozon/Uzum; Arabic commerce → Noon/Talabat; Chinese platforms → "
            "Tmall/JD/Douyin) as the design reference. Do NOT translate the "
            "concept into a Western/English-speaking equivalent."
        )

    try:
        # Flash is plenty for prompt expansion; thinkingBudget=0 keeps it sub-second.
        _model = "gemini-3.5-flash"
        prompt = (
            f"The user gave a very short product brief: \"{_clean}\".\n"
            f"Project type: {layout_archetype.replace('_', ' ')} in the {domain} domain.\n\n"
            "Expand this into a 3-4 sentence brief that includes:\n"
            "1. The actual organization or concept (e.g. \"ACCA\" → "
            "\"Association of Chartered Certified Accountants — global professional accounting body\").\n"
            "2. The audience and what they want from the site/app.\n"
            "3. The most likely 5-7 pages or sections (use real, domain-specific names).\n"
            "4. One or two visual/tone qualities (e.g. \"authoritative and trustworthy\", "
            "\"playful and energetic\").\n\n"
            "STRICT FORMAT — the FIRST WORD must be the brand or concept name itself.\n"
            "GOOD opening: \"Maplewood Grove is a small-batch candle studio that…\"\n"
            "GOOD opening: \"ACCA (Association of Chartered Certified Accountants) is a…\"\n"
            "BAD opening:  \"This project is for a landing page for Maplewood Grove…\"\n"
            "BAD opening:  \"A landing page for Maplewood Grove that…\"\n"
            "BAD opening:  \"The website is about Maplewood Grove…\"\n"
            "Never start with: 'This project', 'A landing page', 'A website', 'The website', "
            "'The app', 'A modern', 'Build', 'Create'.\n\n"
            "Output ONLY the expanded brief as a single paragraph. "
            "No preamble, no headers, no quotes, no markdown, no bullet lists."
            f"{_locale_clause}"
        )
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0,
                "maxOutputTokens": 600,
                "thinkingConfig": {"thinkingBudget": 0},
            },
        }
        from app.services.gemini_http import gemini_post

        _r_status, data, _r_raw = await gemini_post(
            model=_model,
            payload=payload,
            timeout_s=20.0,
            label="expand_prompt",
        )
        if _r_status != 200 or data is None:
            logger.warning("Prompt expansion API %d: %s", _r_status, _r_raw[:200])
            return description
        try:
            _u = data.get("usageMetadata") or {}
            _in = int(_u.get("promptTokenCount", 0) or 0)
            _out = int(_u.get("candidatesTokenCount", 0) or 0) + int(_u.get("thoughtsTokenCount", 0) or 0)
            if _in or _out:
                from app.services.billing_meter import report_token_usage
                report_token_usage(None, _in, _out, source="gemini_expand_prompt")
        except Exception:
            pass
        expanded = ""
        for cand in data.get("candidates", []):
            for part in cand.get("content", {}).get("parts", []):
                if "text" in part:
                    expanded += part["text"]
        expanded = expanded.strip().strip('"').strip("'")
        if not expanded or len(expanded) < 80:
            return description

        suffix = ""
        if "\n\n---\n\n" in description:
            suffix = "\n\n---\n\n" + description.split("\n\n---\n\n", 1)[1]

        await _ws_send(
            websocket, "progress",
            f"📝 Expanded brief: {expanded[:90]}{'…' if len(expanded) > 90 else ''}",
        )
        logger.info("Expanded short prompt %r → %d chars", _clean, len(expanded))
        return expanded + suffix
    except Exception as exc:
        logger.warning("Prompt expansion failed (non-fatal): %s", exc)
        return description


# ╔══════════════════════════════════════════════════════════════╗
# ║  STEP 3 — gemini_deep_research()                            ║
# ║  Ultra-deep product research via Gemini with internet search ║
# ╚══════════════════════════════════════════════════════════════╝

# Limit concurrent Gemini research calls to avoid rate-limit 429s.
# Gemini 2.5 Pro paid-tier quotas are tighter than Flash (typically 2-5 RPM
# depending on billing plan). Keep the semaphore at 2 to be safe; existing
# retry/backoff logic below handles occasional 429s gracefully.
_gemini_semaphore = asyncio.Semaphore(2)


# Section names produced by the design prompt. Used to defensively rewrite
# Gemini's markdown headers (### NAME / **NAME** / NAME:) back to the literal
# ===NAME=== form that _extract_research_section expects.
_DESIGN_SECTION_NAMES: tuple[str, ...] = (
    "ERA_CALIBRATION",
    "LIVE_UI_RESEARCH",
    "LAYOUT_BLUEPRINT",
)


def _normalize_research_headers(text: str, section_names: tuple[str, ...]) -> str:
    """Rewrite markdown-style section headers to ===NAME=== form.

    Gemini sometimes ignores the requested literal `===NAME===` format and
    emits `### NAME` or `**NAME**` instead. _extract_research_section needs
    the exact `===NAME===` literal, so this helper canonicalizes the output.
    Only the listed section names are rewritten — unrelated `### Heading`
    text is left alone.
    """
    if not text:
        return text
    for name in section_names:
        canonical = f"==={name}==="
        if canonical in text:
            continue
        # Match: "### NAME", "**NAME**", "**NAME:**", "NAME:" (line start),
        # optionally with leading bold markers and trailing colons.
        # Whole-line replacement to avoid touching prose mentions.
        pattern = re.compile(
            rf"^\s*(?:#{{1,6}}\s*)?(?:\*\*)?{re.escape(name)}(?:\*\*)?\s*:?\s*$",
            re.MULTILINE,
        )
        text = pattern.sub(canonical, text)
    return text


async def _call_gemini_single(
    prompt: str,
    model: str,
    is_pro: bool,
    websocket,
    label: str,
    max_tokens: int = 10000,
) -> str:
    """Single Gemini REST call with retry+backoff. Returns response text.

    Auth handled by gemini_post via Vertex ADC. Each call acquires its own
    _gemini_semaphore slot so two parallel calls from the same project
    both proceed concurrently (semaphore value=2) while a third concurrent
    project waits, keeping us inside Gemini rate limits.
    """
    from app.services.gemini_http import gemini_post

    # Pro thinkingBudget bumped from 2048 → 8192 for deeper reasoning on
    # ENTITY_SCREENS structure, slug-rename judgment, and architectural
    # decisions. Flash stays at 0 (used for short structured calls).
    _thinking_config = (
        {"thinkingBudget": 8192} if is_pro else {"thinkingBudget": 0}
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "maxOutputTokens": max_tokens,
            "temperature": 0.3,
            "thinkingConfig": _thinking_config,
        },
        "tools": [{"google_search": {}}],
        "systemInstruction": {
            "parts": [{
                "text": (
                    "You are a senior product researcher and UX strategist. "
                    "Return precise, factual, structured output only. "
                    "Use real-world product references and industry-standard design patterns. "
                    "Prioritize specificity over generality — name actual colors (HSL values), "
                    "real font pairings, and concrete UI patterns used by top products in the domain. "
                    "Never hallucinate — if unsure about a specific value, use the most common industry default."
                )
            }]
        },
    }

    status: int = 0
    data: dict | None = None
    raw: str = ""
    _max_attempts = 3
    async with _gemini_semaphore:
        for _attempt in range(1, _max_attempts + 1):
            try:
                # 280s timeout — Pro with 8192 thinkingBudget + big
                # maxOutputTokens can legitimately need 200+ s.
                status, data, raw = await asyncio.wait_for(
                    gemini_post(
                        model=model,
                        payload=payload,
                        timeout_s=280.0,
                        label=label,
                    ),
                    timeout=300.0,
                )
            except asyncio.TimeoutError:
                logger.warning("Gemini %s timed out (attempt %d/%d)", label, _attempt, _max_attempts)
                if _attempt < _max_attempts:
                    await asyncio.sleep(5 * _attempt)
                    continue
                raise RuntimeError(f"Gemini {label} API timed out after all retry attempts")

            if status == 429:
                _backoff = 10 * _attempt
                logger.warning("Gemini rate limit (429) on %s attempt %d — retrying in %ds", label, _attempt, _backoff)
                await _ws_send(websocket, "progress", f"⚠️ Gemini rate limit ({label}) — retrying in {_backoff}s...")
                if _attempt < _max_attempts:
                    await asyncio.sleep(_backoff)
                    continue
            break

    if status != 200 or data is None:
        _api_msg = raw[:300] if raw else ""
        if status == 403:
            await _ws_send(websocket, "error", "❌ Google API rejected request (403). Check credentials / project billing.")
        elif status == 400:
            await _ws_send(websocket, "error", f"❌ Gemini rejected the request (400): {_api_msg or 'bad request'}")
        elif status == 404:
            await _ws_send(websocket, "error", f"❌ Gemini model not found. Set GEMINI_RESEARCH_MODEL to a valid model.")
        elif status == 429:
            await _ws_send(websocket, "error", "⚠️ Gemini rate limit hit after retries. Try again in a minute.")
        else:
            await _ws_send(websocket, "error", f"❌ Gemini API error {status}: {_api_msg or 'unknown'}")
        raise RuntimeError(f"Gemini {label} API error: {status}")

    # Report token usage to the billing meter (fire-and-forget).
    # user_id resolves from the ambient contextvar set at the entry point.
    # thoughtsTokenCount is reasoning tokens emitted by thinking-enabled models
    # (e.g. gemini-3.1-pro with thinkingBudget>0) — Google bills these at the
    # OUTPUT rate, so they must be added to _out_tok or we under-bill the user.
    try:
        _usage = data.get("usageMetadata") or {}
        _in_tok = int(_usage.get("promptTokenCount", 0) or 0)
        _cand_tok = int(_usage.get("candidatesTokenCount", 0) or 0)
        _think_tok = int(_usage.get("thoughtsTokenCount", 0) or 0)
        _out_tok = _cand_tok + _think_tok
        if _in_tok > 0 or _out_tok > 0:
            from app.services.billing_meter import report_token_usage
            report_token_usage(
                None,
                _in_tok,
                _out_tok,
                source=f"gemini_{label}",
            )
    except Exception:
        pass

    from knowledge.loader import safe_gemini_text
    text = safe_gemini_text(data)
    if not text:
        raise RuntimeError(f"Gemini {label} returned empty text")
    return text


# ╔══════════════════════════════════════════════════════════════╗
# ║  PHASE D — per-unit deep research                            ║
# ║                                                              ║
# ║  After schema build, fan out one focused Gemini call per     ║
# ║  entity (admin) or per page (multi-page consumer). Each call ║
# ║  returns a tight block (3–5 paragraphs of concrete UX/visual ║
# ║  detail for that one unit) which we append to the research   ║
# ║  blob as ===ENTITY_DEEP::Name=== / ===PAGE_DEEP::Name===.    ║
# ║  Phase 2 prompts can then pull these blocks for richer       ║
# ║  per-unit content without enlarging the original research    ║
# ║  prompt's output budget.                                     ║
# ║                                                              ║
# ║  Fail-soft: any per-unit failure just leaves that block out  ║
# ║  of the blob; a total failure returns the original research  ║
# ║  blob unchanged. Phase 2 prompts never *require* these       ║
# ║  blocks — they are pure depth bonus.                         ║
# ╚══════════════════════════════════════════════════════════════╝


def _build_entity_deep_research_prompt(
    entity: dict,
    *,
    domain: str,
    brand_name: str,
) -> str:
    """Focused per-entity research prompt for an admin-panel CRUD entity.

    Output is a tight block (3–5 paragraphs, ~600-1000 tokens) covering:
      • Real-world domain field semantics (what each field means in this industry)
      • Standard validation rules and edge cases
      • Common workflow / status transitions
      • Industry-leader reference patterns for this entity's CRUD UI
    """
    name = (entity or {}).get("name") or "Entity"
    fields = (entity or {}).get("fields") or []
    field_names = ", ".join(
        f.get("name", "?") for f in fields if isinstance(f, dict)
    )[:600]
    return f"""You are researching ONE specific admin-panel entity for a {domain} product called "{brand_name}".

ENTITY: {name}
FIELDS: {field_names or '(unspecified)'}

Output a tight, concrete research block. Use real industry references (Stripe Dashboard, Linear, Shopify Admin, Salesforce, Notion, etc. — pick whichever is most relevant for this entity in this domain).

Cover, in 3–5 short paragraphs:

1. DOMAIN SEMANTICS — What does each field actually mean to a {domain} operator? Which fields are user-facing vs. internal? Which are sensitive?

2. VALIDATION & EDGE CASES — Real-world validation rules (formats, ranges, uniqueness), edge cases that bite in production (timezone, currency, soft-delete, archival).

3. WORKFLOW & STATUS — Typical lifecycle transitions for this entity. What states does it move through? What triggers each transition?

4. UI PATTERNS — How does the industry-leading {domain} admin tool present this entity's list view, detail view, and create/edit form? Name the actual product and pattern.

5. ACTIONS & BULK OPS — Most-used row actions and bulk operations for this entity in real {domain} admin tools.

Be specific. Name real products, real field formats, real status values. No platitudes ("important to consider…"). No bullet outlines — flowing paragraphs.
"""


def _build_page_deep_research_prompt(
    page: dict,
    *,
    domain: str,
    brand_name: str,
) -> str:
    """Focused per-page research prompt for a multi-page consumer/portfolio/blog site.

    Output is a tight block (3–5 paragraphs, ~600-1000 tokens) covering:
      • What this specific page exists to do (job-to-be-done)
      • Industry-leader reference patterns for this page type in this domain
      • Section order and content beats unique to this page
      • Conversion / engagement levers specific to this page
    """
    name = (page or {}).get("name") or (page or {}).get("path") or "Page"
    purpose = (page or {}).get("purpose") or (page or {}).get("description") or ""
    sections_hint = ", ".join(
        (s.get("type") or s.get("name") or "?")
        for s in (page or {}).get("sections", []) or []
        if isinstance(s, dict)
    )[:400]
    return f"""You are researching ONE specific page of a {domain} website for a brand called "{brand_name}".

PAGE: {name}
STATED PURPOSE: {purpose or '(unspecified)'}
PLANNED SECTIONS: {sections_hint or '(open)'}

Output a tight, concrete research block. Reference real {domain} websites by name (e.g. Apple, Patagonia, The Wirecutter, Linear, Stripe — pick whichever is most relevant for THIS page type in THIS domain).

Cover, in 3–5 short paragraphs:

1. JOB-TO-BE-DONE — Why does this page exist for a real {domain} visitor? What question or task brings them here? What state are they in (cold/warm/hot)?

2. CONTENT BEATS — Specific section order industry leaders use for this page type, and what unique content lives in each. Be concrete: "above the fold: hero with [specific element]; then [specific section] because…".

3. VISUAL & MOTION CUES — Distinctive visual or interaction patterns that signal quality on this page in this domain (e.g. sticky comparison table, scroll-driven product reveal, embedded video case study).

4. CONVERSION / ENGAGEMENT — The 1–2 actions this page must drive, and the proven patterns industry leaders use to drive them.

5. PITFALLS — Common mistakes that make this page feel generic in {domain}.

Be specific. Name real sites, real section orderings, real interactions. No platitudes. Flowing paragraphs, no bullet lists.
"""


async def enrich_research_with_deep_dives(
    research: str,
    *,
    schema: dict,
    layout_archetype: str,
    domain: str,
    brand_name: str,
    websocket,
) -> str:
    """Fan out per-unit Gemini calls; append ===ENTITY_DEEP=== / ===PAGE_DEEP=== blocks.

    Auth handled by gemini_post via Vertex ADC. Returns the enriched
    research blob (or the original blob unchanged on any catastrophic
    failure). Per-unit failures just omit that one block — the rest of
    the blob is unaffected.

    Why a flat ``===NAME===`` append rather than mutating the schema:
      • Phase 2 prompts already read research with `_extract_research_section`.
      • Schema stays a clean structural artifact; research stays the source of
        narrative depth. No new schema field to migrate.
      • Frontend code that pretty-prints the schema doesn't need to handle
        a new "deep_research" property.
    """
    if not research or not isinstance(schema, dict):
        return research or ""

    archetype = (layout_archetype or "").lower()
    units: list[tuple[str, str, str]] = []  # (kind, name, prompt)

    if archetype in _ADMIN_LAYOUT_ARCHETYPES:
        entities = schema.get("entities") or []
        for ent in entities:
            if not isinstance(ent, dict):
                continue
            name = (ent.get("name") or "").strip()
            if not name:
                continue
            units.append((
                "ENTITY_DEEP",
                name,
                _build_entity_deep_research_prompt(ent, domain=domain, brand_name=brand_name),
            ))
    elif archetype in _MULTIPAGE_CONSUMER_ARCHETYPES:
        pages = schema.get("pages") or []
        for pg in pages:
            if not isinstance(pg, dict):
                continue
            name = (pg.get("name") or pg.get("path") or "").strip()
            if not name:
                continue
            units.append((
                "PAGE_DEEP",
                name,
                _build_page_deep_research_prompt(pg, domain=domain, brand_name=brand_name),
            ))
    else:
        return research

    if not units:
        return research

    _research_model = os.environ.get("GEMINI_RESEARCH_MODEL", "gemini-3.1-pro-preview")
    _is_pro = "pro" in _research_model.lower()

    # Per-project parallelism cap. The shared _gemini_semaphore (=2) is the
    # global ceiling; this throttle ensures one big project can't queue 30
    # calls and starve concurrent projects sitting behind it.
    _project_sem = asyncio.Semaphore(_PHASE_D_MAX_PARALLEL)

    async def _run_one(kind: str, name: str, prompt: str) -> tuple[str, str, str | None]:
        async with _project_sem:
            try:
                text = await asyncio.wait_for(
                    _call_gemini_single(
                        prompt,
                        _research_model,
                        _is_pro,
                        websocket,
                        f"deep_{kind.lower()}_{name[:24]}",
                        max_tokens=2000,
                    ),
                    timeout=_PHASE_D_PER_CALL_TIMEOUT,
                )
                return kind, name, text.strip() if text else None
            except Exception as exc:
                logger.warning("Phase D deep-research %s/%s failed: %s", kind, name, exc)
                return kind, name, None

    await _ws_send(
        websocket,
        "progress",
        f"🔬 Deep research — {len(units)} focused passes in parallel...",
    )

    results = await asyncio.gather(
        *(_run_one(k, n, p) for k, n, p in units),
        return_exceptions=False,
    )

    blocks: list[str] = []
    succeeded = 0
    for kind, name, text in results:
        if not text:
            continue
        # Defensive: strip any accidental ===… markers in the body so they
        # can't confuse _extract_research_section's first-occurrence scan.
        body = text.replace("===", "==")
        blocks.append(f"==={kind}::{name}===\n{body}")
        succeeded += 1

    if not blocks:
        await _ws_send(websocket, "progress", "⚠️ Deep research yielded nothing — continuing with base research.")
        return research

    await _ws_send(
        websocket,
        "progress",
        f"✅ Deep research — {succeeded}/{len(units)} units enriched.",
    )
    return research.rstrip() + "\n\n" + "\n\n".join(blocks) + "\n"


# ── Cultural anchor pool — used when prompt is ambiguous ───────────────────
# Each pool entry = a regional anchor that ships with its own palette / motifs /
# language phrases via the cultural_atmosphere block. Curated to be visually
# DISTINCT from each other — picking from this pool guarantees that two runs
# of "a restaurant" produce structurally different sites.
_CULTURAL_ANCHOR_POOLS: dict[str, list[str]] = {
    "restaurant": [
        "USA → Brooklyn deli", "France → Lyonnaise bistro",
        "Italy → Roman trattoria", "Italy → Sicilian seafood osteria",
        "Mexico → Mexico City taquería", "Spain → Andalusian taberna",
        "Japan → Tokyo izakaya", "Korea → Seoul gastropub",
        "Vietnam → Hanoi pho house", "Thailand → Bangkok night-market",
        "USA → Pacific NW farm-to-table", "Greece → Athenian psarotaverna",
        "Argentina → Buenos Aires asador", "Lebanon → Beirut mezze house",
        "Morocco → Marrakech tagine room", "USA → New Orleans Creole",
        "Peru → Lima cevichería", "Ethiopia → Addis injera house",
    ],
    "cafe": [
        "USA → Portland third-wave coffee", "Australia → Melbourne specialty",
        "Italy → Roman espresso bar", "Vietnam → Hanoi cà phê sữa đá",
        "Sweden → Stockholm fika kafé", "Austria → Vienna kaffeehaus",
        "Japan → Tokyo kissaten", "Türkiye → Istanbul kahvehane",
        "France → Parisian zinc-bar café", "USA → Brooklyn pour-over shop",
    ],
    "hotel": [
        "Greece → Cycladic minimalism", "Morocco → Marrakech riad",
        "Mexico → Tulum coastal", "Japan → Kyoto ryokan",
        "Italy → Tuscan agriturismo", "Iceland → Reykjavik design hotel",
        "Indonesia → Bali jungle villa", "Switzerland → Alpine chalet",
        "USA → Joshua Tree desert lodge", "Portugal → Lisbon townhouse hotel",
    ],
    "fashion": [
        "France → Parisian minimalism", "Japan → Tokyo avant-garde",
        "Italy → Milan tailoring", "Denmark → Copenhagen utilitarian",
        "USA → New York streetwear", "UK → London punk-tailoring",
        "Sweden → Stockholm Scandi-clean", "Korea → Seoul gender-fluid",
    ],
    "bakery": [
        "France → Parisian boulangerie", "Italy → Roman pasticceria",
        "USA → Brooklyn artisan bakery", "Denmark → Copenhagen smørrebrød",
        "Japan → Tokyo neo-patisserie", "Portugal → Lisbon pastel de nata shop",
        "Germany → Berlin bread house",
    ],
    "salon": [
        "France → Parisian atelier-salon", "USA → LA West Hollywood blowout bar",
        "Japan → Tokyo precision-cut studio", "UK → London Soho colour bar",
        "Korea → Seoul K-beauty parlour",
    ],
    "spa": [
        "Indonesia → Bali jungle wellness", "Japan → onsen ryokan",
        "Iceland → geothermal lagoon spa", "Türkiye → Istanbul hammam",
        "Mexico → Tulum cenote spa", "Switzerland → Alpine wellness retreat",
    ],
    "fitness": [
        "USA → Brooklyn boxing studio", "Japan → Tokyo precision pilates",
        "Sweden → Stockholm minimalist gym", "Australia → Bondi beach fitness",
        "USA → LA hot yoga studio", "Germany → Berlin functional training box",
    ],
    "portfolio": [
        "Denmark → Copenhagen design studio", "Japan → Tokyo design firm",
        "USA → Brooklyn creative agency", "France → Parisian atelier",
        "Switzerland → Zurich Swiss-grid studio", "UK → London design house",
    ],
    "travel": [
        "Greece → Aegean island hopping", "Japan → Kyoto cultural travel",
        "Iceland → ring-road expedition", "Morocco → Atlas mountains trek",
        "Peru → Sacred Valley trail", "Mexico → Yucatán cenote tours",
    ],
}

# Substrings that reveal the user already specified a culture / region — when
# any of these appear in the prompt we leave the anchor decision to Gemini
# (the user has already given a strong cue).
_CULTURE_KEYWORDS_IN_PROMPT = (
    "italian", "italy", "italia", "naples", "rome", "milan", "tuscany", "sicilian",
    "japanese", "japan", "tokyo", "kyoto", "osaka", "izakaya", "ramen",
    "mexican", "mexico", "oaxaca", "yucatan", "taqueria", "mezcal",
    "french", "france", "paris", "parisian", "lyon", "provence", "bistro",
    "spanish", "spain", "madrid", "barcelona", "andalusian", "tapas",
    "korean", "korea", "seoul", "k-beauty",
    "vietnamese", "vietnam", "hanoi", "saigon", "pho",
    "thai", "thailand", "bangkok", "chiang mai",
    "indian", "india", "mumbai", "delhi", "kerala",
    "chinese", "china", "shanghai", "beijing", "dim sum",
    "greek", "greece", "athens", "cycladic", "santorini",
    "moroccan", "morocco", "marrakech", "fez", "riad",
    "turkish", "türkiye", "istanbul", "anatolian",
    "scandinavian", "swedish", "danish", "norwegian", "stockholm", "copenhagen",
    "german", "germany", "berlin", "munich",
    "argentine", "argentina", "buenos aires", "asador",
    "peruvian", "peru", "lima", "ceviche",
    "ethiopian", "ethiopia", "addis", "injera",
    "lebanese", "lebanon", "beirut",
    "australian", "melbourne", "sydney",
    "brazilian", "brazil", "rio",
    "english", "british", "uk", "london",
    "irish", "ireland", "dublin",
    "icelandic", "iceland", "reykjavik",
    "balinese", "bali", "indonesia",
    "portuguese", "portugal", "lisbon",
    "brooklyn", "manhattan", "queens", "portland", "austin", "nashville",
    "los angeles", "san francisco", "chicago",
)


# ──────────────────────────────────────────────────────────────────────
#  Locale detection — hint Gemini to use market-native references
#
#  The deep-research prompt asks Gemini to enumerate reference sites
#  ("MUBI", "Netflix", "Amazon"). Gemini's training is heavily English-
#  weighted, so prompts written in Cyrillic / Arabic / CJK — or using
#  non-English Latin terms native to a region like "kino" (Slavic) or
#  "magazin" (Slavic/Turkic for store) — produce *wrong* references.
#  A Russian user wanting a movie site wants Kinopoisk / IVI / Okko,
#  not Criterion Channel.
#
#  We add a small locale hint based on the prompt's script + a short
#  keyword list. When set, the hint is injected into the research prompt
#  to bias references toward the user's market.
# ──────────────────────────────────────────────────────────────────────


# Non-English Latin terms whose presence strongly indicates a CIS-market user
# even when the rest of the prompt is romanised. Compiled here so the same
# list can be reused by other locale-aware logic in the future.
_CIS_LATIN_TERMS = frozenset({
    "kino", "kopiyasi", "saytim", "saytni", "kerak", "magazin", "magazinim",
    "uzum", "wildberries", "ozon", "yandex", "kinopoisk", "ivi",
    "okko", "megogo", "vkontakte", "vk", "rutube", "dzen",
})


def _detect_user_locale_hint(description: str) -> str:
    """Detect the user's market from script + native keywords.

    Returns a short locale label (used to anchor research references) or
    "" when the prompt is plain English with no non-English signal. English
    is the default path and gets no special treatment.
    """
    text = (description or "").strip().lower()
    if not text:
        return ""

    # ── Script detection (highest confidence) ──
    for ch in text:
        cp = ord(ch)
        if 0x0400 <= cp <= 0x04FF:  # Cyrillic
            return "Russian / CIS (Russia, Ukraine, Belarus, Uzbekistan, Kazakhstan)"
        if 0x0600 <= cp <= 0x06FF:  # Arabic
            return "Arabic-speaking market (UAE, Saudi Arabia, Egypt, Morocco)"
        if 0x3040 <= cp <= 0x30FF:  # Hiragana / Katakana
            return "Japanese market"
        if 0xAC00 <= cp <= 0xD7AF:  # Hangul
            return "South Korean market"
        if 0x4E00 <= cp <= 0x9FFF:  # CJK Unified Ideographs
            return "Chinese market (mainland China, Hong Kong, Taiwan)"

    # ── Latin-script keyword fallback ──
    # Romanised CIS terms — common when a Russian/Uzbek user types on an
    # English keyboard. Tokens are 3+ chars matched against the term list.
    import re as _re_locale
    words = set(_re_locale.findall(r"[a-z]{3,}", text))
    if _CIS_LATIN_TERMS & words:
        return "Russian / CIS (Russia, Uzbekistan, Kazakhstan, Ukraine)"

    return ""


def _pick_cultural_anchor(description: str, domain: str) -> str:
    """Pre-pick a regional anchor for ambiguous prompts; return "" otherwise.

    The user types "a restaurant" → Python rolls the dice and returns
    "Spain → Andalusian taberna". This anchor is then injected as a HARD
    constraint into the Gemini research prompt so country_or_region is locked
    to the picked value, guaranteeing variety across consecutive runs of the
    same ambiguous prompt.

    If the user already wrote a culture-specific prompt ("Italian restaurant"
    / "Tokyo izakaya") we return "" — Gemini handles those cases well on its
    own from explicit keywords.
    """
    desc_lc = (description or "").lower()
    # Already culture-specific → let Gemini handle it.
    if any(kw in desc_lc for kw in _CULTURE_KEYWORDS_IN_PROMPT):
        return ""

    # Map domain → pool key.
    domain_lc = (domain or "").lower()
    pool_key: str | None = None
    if any(s in domain_lc for s in ("restaurant", "food", "dining", "tapas", "tavern", "trattoria", "bistro")):
        pool_key = "restaurant"
    elif any(s in domain_lc for s in ("café", "cafe", "coffee", "espresso", "roastery", "roaster")):
        pool_key = "cafe"
    elif any(s in domain_lc for s in ("hotel", "resort", "inn", "bnb", "lodging", "hostel", "boutique_hotel")):
        pool_key = "hotel"
    elif any(s in domain_lc for s in ("fashion", "apparel", "clothing", "boutique")):
        pool_key = "fashion"
    elif any(s in domain_lc for s in ("bakery", "patisserie", "boulangerie")):
        pool_key = "bakery"
    elif any(s in domain_lc for s in ("salon", "barber", "hair")):
        pool_key = "salon"
    elif any(s in domain_lc for s in ("spa", "wellness", "massage")):
        pool_key = "spa"
    elif any(s in domain_lc for s in ("fitness", "gym", "yoga", "pilates", "crossfit")):
        pool_key = "fitness"
    elif any(s in domain_lc for s in ("portfolio", "studio", "agency", "designer")):
        pool_key = "portfolio"
    elif any(s in domain_lc for s in ("travel", "tour", "trip", "expedition")):
        pool_key = "travel"

    # Domain doesn't match a hospitality / lifestyle pool → Gemini decides
    # (most likely "none — modern global" for B2B SaaS / dev tools).
    if not pool_key:
        return ""

    import random as _rand
    return _rand.choice(_CULTURAL_ANCHOR_POOLS[pool_key])


async def gemini_deep_research(
    description: str,
    classification: dict,   # rich dict from classify_project_type_ai
    stack: str,
    websocket,
    *,
    locale_hint_override: str = "",
) -> str:
    """Ultra-deep product research via Gemini with internet search.

    Analyzes 3-5 real products in the user's domain and extracts a
    CODE-READY BLUEPRINT — not a report. Returns the raw text blueprint.
    """
    await _ws_send(websocket, "progress", "🔬 Researching top products in this domain...")

    layout_archetype = classification.get("layout_archetype", "consumer_website")
    domain = classification.get("domain", "general")
    is_single_page = classification.get("is_single_page", False)
    has_admin = classification.get("has_admin_features", False)
    is_locked = classification.get("classification_locked", False)

    # ── Locale hint (Python-side, derived from prompt script + keywords) ──
    # Anchors reference brands to the user's actual market when the prompt
    # is in a non-English script or uses native non-English terms. Without
    # this hint Gemini defaults to English-speaking references for every
    # non-English prompt — wrong for Russian/CIS/Arabic/CJK users.
    #
    # `locale_hint_override` is the caller's chance to pass a locale derived
    # from the ORIGINAL (pre-expansion) prompt — `_expand_short_prompt` will
    # otherwise translate non-English short prompts into English, erasing
    # the signal _detect_user_locale_hint reads. When the override is set
    # we prefer it; the per-description detection is the fallback.
    _locale_hint = locale_hint_override or _detect_user_locale_hint(description)

    # ── Cultural anchor pre-selection (Python-side, hard constraint) ────
    # When the user's prompt is ambiguous ("a restaurant", "a coffee shop"),
    # Gemini left to its own devices keeps converging on the same regional
    # anchor (Brooklyn deli / Parisian minimal / etc.) — the model has training
    # bias toward whatever's most documented. Rolling the anchor in Python
    # guarantees real variety across N consecutive runs of the same prompt.
    _cultural_anchor_override = _pick_cultural_anchor(description, domain)

    # Human-readable archetype label for the prompt
    _archetype_label = {
        "single_page_landing": "single-page landing page (ONE scrollable page, no sub-routes)",
        "consumer_website": "multi-page consumer website (public-facing, top nav)",
        "admin_dashboard": "admin dashboard (sidebar + CRUD + KPI cards)",
        "crm": "CRM system (sidebar + customer pipeline + contacts + deals)",
        "tms": "Transportation Management System (sidebar + shipments + fleet + routes)",
        "saas_dashboard": "SaaS workspace dashboard (sidebar + project/task management)",
        "ecommerce": "e-commerce platform (online store + order management)",
        "blog": "blog / content publishing platform (articles + authors + categories)",
        "portfolio": "portfolio / showcase site (work samples + bio + contact)",
        "marketplace": "marketplace platform (buyers + sellers + listings)",
    }.get(layout_archetype, layout_archetype.replace("_", " "))

    # Locale block — inserted near the top of the research prompt so it
    # influences EVERY downstream step (search queries + sites picked +
    # nav vocabulary + copy language). Empty when prompt is plain English.
    _locale_block = ""
    if _locale_hint:
        _locale_block = f"""

╔══════════════════════════════════════════════════════════════════════════╗
║  LOCALE CONTEXT — HARD CONSTRAINT                                        ║
╠══════════════════════════════════════════════════════════════════════════╣
║  The user's prompt is written for the {_locale_hint} market.
║                                                                          ║
║  In ===SITES_ANALYZED===, you MUST prioritise references NATIVE to this  ║
║  market over English-language defaults. The structure (page layouts,     ║
║  nav vocabulary), the copy, AND the visual language often differ from    ║
║  the English-speaking default — research the actual native market.       ║
║                                                                          ║
║  Hint references by domain (find more via search, do NOT stop at these): ║
║   • Russian/CIS movies/streaming → Kinopoisk, IVI, Okko, START, Megogo   ║
║   • Russian/CIS e-commerce → Wildberries, Ozon, Yandex.Market, Uzum      ║
║   • Russian/CIS social/media → VK, Telegram, Dzen, Rutube                ║
║   • Arabic e-commerce → Noon, Talabat, Jarir, Carrefour KSA              ║
║   • Chinese platforms → Tmall, JD, Douyin, Bilibili, Weibo, Xiaohongshu  ║
║   • Japanese marketplaces → Rakuten, Mercari, Yahoo Shopping             ║
║   • Korean platforms → Coupang, 11Street, Naver Shopping                 ║
║                                                                          ║
║  At least 2 of your 4 search queries MUST use the native language —      ║
║  English-only searches return the wrong references for this market.      ║
║  Copy (taglines, section headings) must be authored in the user's        ║
║  language, not translated from English defaults.                         ║
╚══════════════════════════════════════════════════════════════════════════╝
"""

    _cultural_anchor_block = ""
    if _cultural_anchor_override:
        _cultural_anchor_block = f"""

╔══════════════════════════════════════════════════════════════════════════╗
║  CULTURAL ANCHOR — HARD CONSTRAINT (do not override, do not negotiate)   ║
╠══════════════════════════════════════════════════════════════════════════╣
║  The user gave an AMBIGUOUS prompt with no specific country / cuisine.   ║
║  To guarantee variety across runs, an anchor was pre-selected by the     ║
║  pipeline (random, evenly-weighted across cultures).                     ║
║                                                                          ║
║  >>>  country_or_region = "{_cultural_anchor_override}"
║                                                                          ║
║  Apply this anchor to the ===CULTURAL_ATMOSPHERE=== block, the imagery,  ║
║  the palette, the typography, and the language phrases. Do NOT pick a    ║
║  different region. Do NOT output "none — modern global". Do NOT default  ║
║  to a generic upscale style — make this site feel authentically FROM     ║
║  that place to a designer who knows it.                                  ║
╚══════════════════════════════════════════════════════════════════════════╝
"""

    research_prompt = f"""You are an AUTONOMOUS PRODUCT RESEARCHER and UI/UX ARCHITECT with full internet search access.
Research and blueprint a production-quality web application.

PROJECT: "{description}"
INITIAL TYPE: {_archetype_label} | DOMAIN: {domain}
TECH STACK: {stack}
{_locale_block}
{_cultural_anchor_block}

═══════════════════════════════════════════════════════════════
STEP 1 — CONFIRM CLASSIFICATION
═══════════════════════════════════════════════════════════════
Review the initial type. Correct it ONLY if the description clearly implies something different.

LAYOUT OPTIONS:
• single_page_landing  — ONE scrollable page, anchor links. "landing page for X"
• consumer_website     — Multi-page public site, top header nav. "X website"
• admin_dashboard      — Sidebar + CRUD tables + KPIs. Management/operations tools
• crm                  — Sidebar + pipeline + contacts + deals. Customer management
• tms                  — Sidebar + shipments + fleet + routes. Transport/logistics
• saas_dashboard       — Sidebar + workspace. Project/task/team management
• ecommerce            — Products + cart + orders. Online store
• blog                 — Articles + authors + categories. Content platform
• portfolio            — Work samples + bio + contact. Personal/agency showcase
• marketplace          — Buyers + sellers + listings. Two-sided platform

===CLASSIFICATION===
layout_archetype: {layout_archetype}
domain: {domain}
is_single_page: {"yes" if is_single_page else "no"}
nav_style: {"top_header" if not has_admin else "sidebar_left"}
has_admin_features: {"yes" if has_admin else "no"}
reasoning: [confirm or explain any correction in 1 sentence]

{"⚠️ LOCKED: The user explicitly requested this layout type. Output layout_archetype EXACTLY as shown above — do NOT change it." if is_locked else "(Correct the layout_archetype line above ONLY if clearly wrong — keep others matching)"}

═══════════════════════════════════════════════════════════════

===USER_REQUIREMENTS===
Re-read the original PROJECT description above. Extract every CONCRETE, USER-STATED
requirement — anything the user explicitly named or asked for. These are NON-NEGOTIABLE
and must be implemented by the code generator EXACTLY, not summarised away by your
research output.

Cover all of these axes when relevant:
  • Loader / preloader specifics (e.g. "noise-overlay loader", "rotating logo loader",
    "skeleton loader with shimmer")
  • Cursor / pointer behaviour ("magnetic cursor", "custom dot cursor", "trail cursor")
  • Specific animations / transitions ("hero text scrambles in", "image stack flips on
    scroll", "marquee logo strip")
  • Layout / structural choices ("split-screen hero", "horizontal scroll testimonials",
    "magazine-style features grid")
  • Brand / copy specifics (exact taglines, named sections, named features, mascots,
    icons, named colours, named fonts)
  • Interaction quirks ("no scroll", "single-page reveal-on-scroll", "no header on
    scroll", "sticky CTA")
  • Integrations or external services the user named ("Stripe checkout", "Calendly
    embed", "MapBox map")
  • Accessibility / language ("RTL Arabic version", "Spanish copy", "high-contrast
    mode")
  • Performance / mobile constraints ("mobile-first", "no JS animations", "60fps")

Output as a JSON array of strings, one short imperative per item. Empty array `[]` if
the user gave NO specific requirements (just described the type of site).

Example for input "fitness landing page with magnetic cursor and a rotating donut
loader, dark mode default, swipe-card testimonials":
[
  "magnetic cursor effect on all interactive elements (buttons, nav, CTA)",
  "rotating donut loader on initial page load (svg circle stroke animation, 1.2s loop)",
  "dark mode is the DEFAULT theme (light mode optional, controlled by toggle)",
  "testimonials section uses swipe-card stack, not a static grid (drag to reveal next)"
]

⚠️  CRITICAL — DO NOT invent requirements the user did not state. Only extract what is
literally implied or named by the description. Empty array is the correct answer for
generic requests.

═══════════════════════════════════════════════════════════════
STEP 2 — INTERNET RESEARCH (MUST use google_search grounding)
═══════════════════════════════════════════════════════════════

YOU HAVE google_search AVAILABLE. USE IT. Do NOT rely on training memory —
training data cutoff is mid-2024 and will produce dated design. The goal of this step
is to anchor the design in ACTUAL 2025-2026 reality.

Run AT LEAST these searches before answering:
  1. "{domain} best website navigation structure pages 2026"
  2. "top {domain} website homepage sections content features 2025 2026"
  3. "best {domain} website user experience must-haves 2025 2026"
  4. (if "{description}" names a specific brand) "{description}" official website pages and structure

Focus on STRUCTURE: what pages exist, what navigation labels are used, what sections appear
on the homepage, what features every top {domain} site must have. Visual design is handled
separately — do NOT analyze motion, animations, or visual design here.

If "{description}" names a REAL brand → study THAT site FIRST as primary reference.

===SITES_ANALYZED===
1. [Name] ([URL]) — pages: [...] | nav_items: [...] | homepage_sections: [...] | notable_features: [...]
2. [Name] ([URL]) — pages: [...] | nav_items: [...] | homepage_sections: [...] | notable_features: [...]
3. [Name] ([URL]) — pages: [...] | nav_items: [...] | homepage_sections: [...] | notable_features: [...]
4. [Name] ([URL]) — pages: [...] | nav_items: [...] | homepage_sections: [...] | notable_features: [...]

═══════════════════════════════════════════════════════════════
STEP 3 — DESIGN SYSTEM (always from research — no generic defaults)
═══════════════════════════════════════════════════════════════

===CSS_VARIABLES===
--primary: [hsl] | --primary-foreground: [hsl]
--secondary: [hsl] | --secondary-foreground: [hsl]
--accent: [hsl] | --accent-foreground: [hsl]
--background: [hsl] | --foreground: [hsl]
--card: [hsl] | --card-foreground: [hsl]
--muted: [hsl] | --muted-foreground: [hsl]
--border: [hsl] | --ring: [hsl]
--destructive: [hsl] | --destructive-foreground: [hsl]
--radius: [value]rem

===FONTS===
heading: [Font Name] ([Google Fonts URL + weights 600;700;800;900])
body: [Font Name] ([Google Fonts URL + weights 400;500;600])
hero_size: [px] / [line-height] / [letter-spacing]
h2_size: [px] / [weight]
body_size: [px] / [line-height]
overall_vibe: [2-3 descriptive words — e.g. "bold minimal dark"]

═══════════════════════════════════════════════════════════════
STEP 4 — STRUCTURE (adapts to the confirmed layout_archetype)
═══════════════════════════════════════════════════════════════

[OUTPUT THIS BLOCK IF layout_archetype = single_page_landing]
===HEADER===
logo: [brand name from description]
nav_items: [4-6 DOMAIN-APPROPRIATE anchor labels — NOT the generic SaaS stack]

  CRITICAL: Pick nav labels the real top sites in THIS domain actually use.
  Study bluebottlecoffee.com, noma.dk, equinox.com, airbnb.com, tesla.com etc. for
  their domain — copy their navigation vocabulary, not a SaaS template.

  Domain-specific examples (use the pattern, not the exact list):
    Coffee / café        → Our Coffee, Menu, Locations, Subscription, Our Story, Wholesale
    Restaurant           → Menu, Reservations, Private Events, Locations, About, Press
    Bakery / pâtisserie  → Menu, Order Online, Custom Cakes, Visit, Our Story
    Bar / cocktail       → Drinks, Events, Reservations, Visit, About
    Fitness / gym        → Classes, Trainers, Schedule, Membership, Locations, Community
    Yoga / pilates       → Classes, Teachers, Schedule, Retreats, Pricing
    Salon / barber       → Services, Book Now, Stylists, Locations, Gift Cards
    Spa / wellness       → Treatments, Book Now, Memberships, Facilities, Our Story
    Hotel / travel       → Rooms, Experiences, Dining, Location, Offers, Book
    Airbnb-style rental  → Listings, Experiences, Hosts, Trust & Safety, Help
    Real estate          → Buy, Sell, Rent, Neighborhoods, Agents, Market Insights
    Automotive dealer    → Inventory, New, Pre-owned, Finance, Service, About
    Wedding / event      → Packages, Venue, Gallery, Pricing, Contact
    Pet / vet            → Services, Book Visit, Our Team, Shop, Resources
    Portfolio / agency   → Work, Services, Process, About, Journal, Contact
    Blog / magazine      → Latest, Topics, Newsletter, Authors, About
    Nonprofit            → Mission, Programs, Impact, Get Involved, Donate
    B2B SaaS (only!)     → Features, How It Works, Pricing, Docs, Changelog, Log In

  HARD BAN for non-SaaS domains (coffee, restaurant, fitness, hotel, salon, spa,
  retail, wedding, pet, real estate, automotive, event, portfolio, nonprofit):
    ✗ NEVER output "Features", "Pricing", "How It Works", "Sign In", "Log In",
      "Get Started", "Dashboard", "Integrations", "Changelog", "API".
    These are SaaS-tool vocabulary and look like a template on a physical business.

  ADDITIONAL RULES:
    - Each label 1-3 words, Title Case.
    - First item is usually the primary content (Menu / Rooms / Classes / Work).
    - Last item is usually a CTA-adjacent action (Book Now / Reservations / Visit).

cta: "[CTA text — use the domain-appropriate verb: Reserve a Table / Book a Class
       / Order Now / Plan Your Stay / View Menu / Book Appointment. For SaaS only:
       Start Free / Get Demo / Try It Free]"
     | classes: [Tailwind button classes]
sticky: yes | blur_bg: yes

===SECTIONS===
Invent the section list that THIS domain actually needs — do NOT default to the
generic "hero / features / pricing / faq / cta" stack. Study what real top
{domain} sites put on their landing page and pick 7-12 sections that flow in a
domain-appropriate order. The list below is a menu of POSSIBLE sections; pick
what fits this domain, skip what doesn't, and INVENT sections unique to the
domain if needed.

Possible section types (not all apply — pick what THIS domain needs):
  Universal: hero, social_proof (logos / ratings / user count), cta_final, footer
  Product/SaaS: features, how_it_works, integrations, pricing, faq, changelog
  Coffee/Restaurant/Bakery: menu, story, chef_bio, hours, locations, gallery, reservations, press_mentions
  Fitness/Gym: classes_schedule, trainers, membership_tiers, transformations, facilities_tour
  Salon/Spa: services_menu, practitioners, booking, gift_cards, before_after_gallery
  Hotel/Travel: rooms_showcase, amenities, nearby_attractions, booking_widget, reviews_from_agoda
  Real estate: featured_listings, agents, neighborhoods, recent_sales, mortgage_calculator
  Portfolio: selected_work, case_study_preview, about, clients_list, awards, process
  Blog/Content: latest_posts, categories, featured_author, newsletter_signup, trending_topics
  Event/Wedding: venue_gallery, packages, couple_story, guest_book, rsvp
  INVENT NEW ones if THIS domain calls for it (e.g. "coffee_of_the_month_feature",
  "live_cam_of_the_roastery", "farm_partners_map", "seasonal_ritual_calendar").

For EACH section you pick, specify:

[section: <your_section_name>]
headline: "[ORIGINAL copy specific to the domain — not placeholder]"
subheadline: "[1-2 supporting sentences with real specificity — if applicable]"
layout: [describe in 1 sentence — e.g. "bento 3×2 with the center tile featuring
         a latte close-up and a floating '4.9★' stat card, surrounded by 5
         small feature cards with Lucide icons"]
background: [describe in research Tailwind-friendly terms — e.g. "bg-muted/40
             with a faint dot-matrix SVG pattern at 4% opacity behind H2"]
imagery: [what photos/icons/illustrations appear and where]
content: [actual items/rows/cards — real domain-specific copy, no lorem ipsum]
animation: [how content enters as user scrolls]

Hero MUST match the hero_description from LAYOUT_BLUEPRINT. Other sections MUST
respect the section_rhythm mapping from LAYOUT_BLUEPRINT.

IMPORTANT: section ORDER matters and should be chosen for THIS domain's conversion
psychology — a coffee shop leads with ambiance+menu, not "features + pricing".
A B2B SaaS leads with problem-solution + social proof, not "menu".

===FOOTER===
columns: [3-4 columns with domain-appropriate links]
bottom: "[copyright + tagline]"

[OUTPUT THIS BLOCK IF layout_archetype = consumer_website OR marketplace OR portfolio OR blog]
===HEADER===
logo: [brand name]
nav_items: [5-7 DOMAIN-APPROPRIATE page labels from research — Title Case, 1-3 words each]

  CRITICAL: Use labels the real top sites in THIS domain actually use.
  HARD BAN on non-SaaS domains: NEVER output "Features", "Pricing",
  "How It Works", "Sign In", "Log In", "Get Started", "Dashboard",
  "Integrations", "Changelog" on a physical/consumer/portfolio site.

  Pattern examples (copy the vocabulary, not the exact list):
    Restaurant chain → Menu, Locations, Reservations, Private Events, Gift Cards, About
    Coffee roaster   → Our Coffee, Subscription, Wholesale, Cafés, Our Story, Blog
    Hotel            → Rooms, Experiences, Dining, Spa, Location, Offers, Book
    Real estate      → Buy, Sell, Rent, Agents, Neighborhoods, Insights
    Agency/portfolio → Work, Services, Process, About, Journal, Contact
    Magazine/blog    → Latest, Topics, Newsletter, Authors, About, Shop

cta: "[optional primary CTA text — domain verb: Reserve / Book Now / Visit / Order / Donate]"
sticky: yes | blur_bg: yes

===PAGES===
Study REAL {domain} sites and list EVERY page they have (MINIMUM 5-6 pages, most have 7-9).

⚠️  CRITICAL — DEPTH REQUIREMENT (this is the #1 failure mode of past runs):
Every page MUST be enumerated section-by-section with the SAME depth as the
single-page landing format. A page with only `sections: hero, services, cta`
is NOT acceptable — it gives the codegen nothing to render and produces
identical-looking pages across the site.

For EACH page, output this EXACT structure (DO NOT collapse, DO NOT abbreviate,
DO NOT use "etc." — list every section as a [section: name] block):

[page: <page_slug>]
path: <route — / for home, /about, /services, /work/:slug, /articles/:slug, etc.>
purpose: <what this page achieves for the user — 1 sentence>
hero_headline: "<page-specific headline, NOT the homepage headline>"
hero_subheadline: "<1-2 supporting sentences>"
hero_imagery: <what the page hero shows — be specific about subject + treatment>
sections:
  [section: <section_slug>]
    headline: "<ORIGINAL copy specific to THIS page on THIS domain — no placeholders>"
    subheadline: "<1-2 supporting sentences with real specificity>"
    layout: <1 sentence describing spatial structure — e.g. "asymmetric 2-col with tall portrait left, stacked stats right, divider line at 60% width">
    background: <Tailwind treatment — e.g. "bg-muted/40 with faint dot-matrix overlay at 4%">
    imagery: <what photos/icons/illustrations appear and where — be specific, NOT "an image of services">
    content: <real domain-specific items the section contains — list rows/cards/copy as concrete strings, no lorem ipsum>
    animation: <how content enters on scroll — e.g. "stagger-fade-up at 80ms intervals">
  [section: <next_section_slug>]
    headline: "..."
    ...
  [section: <…>]
    ...

PAGE-LEVEL REQUIREMENTS:
  • Home (/) MUST have 6-9 sections — same depth as a landing page; this is
    where most visitors land. Do NOT make Home thinner than the rest of the site.
  • Every NON-HOME page must have 4-6 distinct sections — never just hero + cta.
  • Section TYPES across pages must NOT all be the same. Do not put a generic
    "hero / features-grid / testimonials / cta" stack on every single page —
    each page exists for a different reason and needs a section list that
    serves THAT reason. About has team/story/values; Services has process/
    outcomes/case-studies; Contact has form/locations/hours/socials.
  • Section CONTENT must be page-relevant. The "team" section on /about lists
    real role names + bios; on /services it does NOT appear at all.

REQUIRED PAGES BY ARCHETYPE (build EVERY page listed for your archetype, plus
any additional ones the domain calls for):

  consumer_website (5-7 pages):
    /, /about, /services (or /menu, /rooms, /classes — domain noun for the offering),
    /contact, plus any of: /pricing, /locations, /gallery, /testimonials, /faq,
    /careers, /press. Pick what THIS domain truly needs.

  portfolio (5-6 pages):
    /, /work, /work/:slug, /about, /contact, plus optional: /journal, /services,
    /process, /clients.

  blog (6-8 pages):
    /, /articles, /articles/:slug, /categories, /categories/:slug, /authors/:username,
    /about, /search.

  marketplace (6-8 pages):
    /, /browse (or /listings), /listings/:id, /sell (or /list-your-X), /categories/:slug,
    /profile/:id, /about, /how-it-works.

DOMAIN-SPECIFIC SECTION VOCABULARIES — pick from these for relevant pages, do
NOT reuse the SaaS "features / pricing / faq" stack on physical-business pages:

  Restaurant /menu      → menu_categories, dish_grid, chef_specials,
                          dietary_filters, wine_pairings, private_dining_cta
  Restaurant /about     → chef_bio, restaurant_story, sourcing_philosophy,
                          press_mentions, awards
  Hotel /rooms          → room_categories, room_carousel, amenities_grid,
                          floor_plan, rate_cards, check_availability
  Hotel /experiences    → curated_packages, seasonal_offers, partner_excursions
  Real estate /buy      → search_form, featured_listings, neighborhood_picks,
                          mortgage_calculator, recent_sales
  Real estate /agents   → agent_grid, agent_specialties, contact_an_agent_cta
  Agency /work          → project_grid, case_study_filter, before_after_strip,
                          client_logo_marquee, awards_strip
  Agency /process       → numbered_phase_list, deliverables_per_phase,
                          tool_stack_strip, sample_timeline
  Blog /articles        → featured_post, category_pills, post_grid,
                          editor_picks, popular_tags, newsletter_signup
  Blog /:slug           → article_hero, table_of_contents, body_with_pullquotes,
                          author_bio_card, related_posts, comments_or_cta
  Portfolio /work/:slug → project_hero, problem_brief, process_journey,
                          outcome_stats, gallery_strip, next_project_link
  Marketplace /browse   → filter_sidebar, sort_bar, listing_grid, pagination,
                          recommended_strip, recent_searches
  Marketplace /:id      → listing_gallery, key_specs, seller_card, location_map,
                          similar_listings, contact_seller_cta

CROSS-PAGE COHESION RULES (enforce these — do not output a site that violates them):
  • The header nav_items from ===HEADER=== MUST match a real page in this list
    (or a section anchor on /). No nav link with no page behind it.
  • Every CTA verb used on Home (`Reserve a Table`, `Book a Stay`, `View Listing`,
    `Read More`) MUST land on a real page that supports that action.
  • Footer columns from ===FOOTER=== should reuse this page list — do not invent
    footer-only pages that don't exist here.

HARD BANS:
  ✗ Pages with only `sections: hero, cta` or `sections: hero, content, cta`.
  ✗ Generic copy like "Our amazing services" / "Welcome to our company".
  ✗ Lorem ipsum or "[placeholder]" anywhere in headline/subheadline/content.
  ✗ Same section list on /about and /services and /contact.
  ✗ Pages whose only difference from Home is a header swap.

===FOOTER===
columns: [3-4 columns]
bottom: "[copyright + tagline]"

[OUTPUT THIS BLOCK IF layout_archetype = admin_dashboard OR crm OR tms OR saas_dashboard OR ecommerce]
===HEADER===
logo: [tool/product name from description]
topbar: notifications icon | help icon | user avatar with dropdown

===SIDEBAR===
Study REAL {layout_archetype.replace("_", " ")} products to determine the EXACT sidebar structure for this domain.
Name groups after what this specific tool manages.

[group: Overview]
items:
  - label: Dashboard | path: /dashboard | icon: LayoutDashboard
  [add other overview items this domain needs]

[group: [Primary Entity Group]] \u2190 e.g. "Shipments", "Customers", "Projects", "Products"
items:
  - [ALL items this primary group needs]

[group: [Secondary Group]] \u2190 if needed
items:
  - [items]

[group: Settings]
items:
  - label: Settings | path: /settings | icon: Settings
  - label: Help | path: /help | icon: HelpCircle

===DASHBOARD_KPIS===
4-6 KPIs that matter for THIS specific {domain} {layout_archetype.replace("_", " ")}:
1. label: [KPI name] | value: [realistic example value] | change: [\u00b1X%] | trend: [up|down] | icon: [LucideIcon] | color: [tailwind color class]

===ENTITIES===
Data entities this tool manages. Base on research of REAL {layout_archetype.replace("_", " ")} products.

COVERAGE RULE — non-negotiable:
EVERY user-facing CRUD page in your ===SIDEBAR=== above (Shipments, Routes,
Carriers, Customers, Invoices, Drivers, Vehicles, etc.) MUST have a matching
entity defined here. If the sidebar has /shipments and /carriers and /routes
and /invoices, you MUST emit Shipment, Carrier, Route, AND Invoice entities.
A sidebar item without a backing entity is a broken page.

In addition, include any noun the user explicitly named in the project
description even if it didn't reach the sidebar (referenced foreign keys
like driver_id, vehicle_id, route_id, carrier_id all imply entities).

Minimum 5 entities for an admin/CRM/TMS/ecommerce panel. Most real tools
have 7-12. Only landing-style admin tools may have fewer. Do not stop at
the minimum — list everything the tool actually manages.

[entity: EntityName]
purpose: [what this entity represents in the {domain} domain]
fields:
  - name: [field] | type: [string|number|boolean|enum|date|email|url|textarea|select|file] | required: [yes|no] | inList: [yes|no] | inForm: [yes|no] | options: [if enum: value1,value2,value3]
  [LIST ALL FIELDS from research — minimum 8-10 fields per entity]
mock_data: [12-15 rows of realistic domain-specific data — real names, real statuses, real values]

[REPEAT for every sidebar CRUD item AND every domain noun]

===ENTITY_SCREENS===
For EACH entity in ===ENTITIES===, specify the THREE screens operators
will use: LIST, DETAIL, and CREATE/EDIT. This is the admin equivalent of
"per-page section spec" for multi-page sites — without explicit per-screen
structure, Phase 2 falls back to generic CRUD scaffolding (Name, Created,
Actions) and the product feels like a Bootstrap admin template.

Reference REAL {domain} {layout_archetype.replace("_", " ")} products
(Stripe Dashboard, Linear, Shopify Admin, Salesforce, Notion, Retool,
HubSpot, Intercom — pick the closest analogue for THIS domain) to
determine each screen's actual structure.

For EACH entity, output:

[entity: EntityName]

  [screen: list]
  layout: [one sentence — e.g. "left filter sidebar + sortable data table
           right + sticky bulk-action toolbar at top of table"]
  filter_bar: [actual filter chips/dropdowns this domain needs — e.g.
               "Status (Pending, In Transit, Delivered) | Carrier
               (multi-select) | Date range | Origin city"]
  table_columns: [the 6-9 columns operators ACTUALLY scan — not every
                  field, just the high-signal ones with their display
                  format. Domain-specific. e.g. "Tracking # (mono),
                  Customer (avatar+name), Origin → Destination, ETA
                  (relative), Status (badge), Carrier (logo), Value
                  ($USD)"]
  row_actions: [per-row quick actions — e.g. "View, Edit, Print Label,
                Mark Delivered, Cancel"]
  bulk_actions: [actions on selected rows — e.g. "Assign Carrier,
                 Export CSV, Send Status Update"]
  empty_state: [what to show when there are no results — e.g.
                "illustration + 'No shipments yet' + 'Create your first
                shipment' CTA"]
  pagination: [pattern — e.g. "numbered + page-size selector (25/50/100),
               showing 1-25 of 1,432"]

  [screen: detail]
  layout: [one sentence — e.g. "two-column split: 65% main content + 35%
           activity sidebar; tabs at top for Overview/Timeline/Documents/
           Notes"]
  hero_strip: [top status/identity strip — e.g. "tracking # + status
               badge + key timestamps + primary action button"]
  primary_panels: [3-5 named panels with what they contain — e.g.
                   "Customer Info: name/email/phone/address; Shipment
                   Details: dimensions/weight/value/insurance; Route:
                   origin/destination/waypoints with map embed;
                   Documents: BOL/invoice/POD as downloadable cards"]
  side_rails: [what's in the activity sidebar — e.g. "activity timeline
               (status changes, comments, system events) with author +
               timestamp; Notes tab for internal team comments"]
  contextual_actions: [domain-specific header buttons — e.g. "Print
                       Label, Email Customer, Add Note, Cancel
                       Shipment, Reroute"]

  [screen: create]
  layout: [one sentence — e.g. "single-page form, NOT modal — 2-column
           grid with logical field groups; sticky save bar at bottom"]
  field_groups: [name + ordered fields per group — e.g. "Customer Info:
                 customer (combobox), pickup_address, delivery_address,
                 contact_phone; Shipment Details: weight, dimensions,
                 declared_value, insurance_required; Routing:
                 origin_terminal, destination_terminal, carrier,
                 expected_pickup, expected_delivery"]
  smart_defaults: [what the form pre-fills — e.g. "pickup address from
                   customer's default; carrier from last 3 used;
                   terminal from current user's region"]
  validation_quirks: [domain-specific rules — e.g. "hazmat cargo blocks
                      ground carriers; weight > 150lb requires LTL flag;
                      international destinations require customs forms"]
  primary_cta: [submit button + what happens — e.g. "Create Shipment &
                Generate Label → opens label preview"]

[REPEAT for every entity from ===ENTITIES===]

HARD BANS for ENTITY_SCREENS:
  ✗ Generic table_columns like "Name, Created, Updated, Actions" — every
    column MUST be domain-specific.
  ✗ Empty filter_bar — even simple entities have 2-3 filters.
  ✗ Detail views with no side_rails or contextual_actions — that is a
    glorified read-only form, not a real admin screen.
  ✗ Create forms with all fields in one ungrouped flat list — operators
    expect logical groups.
  ✗ Placeholder text like "[field]" or "TBD" anywhere in this block.

===STATUS_BADGES===
[status_value]: bg-[color]-100 text-[color]-800 dark:bg-[color]-900/30 dark:text-[color]-400
(all status values from all entities)

═══════════════════════════════════════════════════════════════
STEP 5 — DOMAIN INTELLIGENCE (always output)
═══════════════════════════════════════════════════════════════

===DOMAIN_MUST_HAVES===
5-8 features ALL top {domain} {layout_archetype.replace("_", " ")}s have — missing = product feels incomplete:
1. feature_name: [name] | type: [section|component|interaction] | implementation: [how to build in React/Next.js] | why_essential: [1 sentence]

===KEY_COMPONENTS===
Domain-specific reusable components from research (make it feel like the REAL thing):

[component: ComponentName]
purpose: [what it does]
props: [data it receives]
layout: [key Tailwind structure]

(4-8 components unique to this domain — NOT generic Button/Card)

===UI_PATTERNS===
card: [full card Tailwind class string]
card_hover: [hover transition classes]
button_primary: [full Tailwind — size, color, radius, hover]
button_ghost: [exact Tailwind]
section_spacing: [padding pattern e.g. "py-20 md:py-28 px-4 sm:px-6 lg:px-8"]
max_width: [max-width + margin e.g. "max-w-6xl mx-auto"]
badge: [pill badge classes]
input: [form input classes]
overall_vibe: [2-3 descriptive words from research]

===CULTURAL_ATMOSPHERE===
country_or_region:
  [Identify the specific country / region / sub-culture this brand should evoke.
   Examples: "Italy → Campania (Naples)", "Japan → Tokyo izakaya", "Mexico → Oaxaca",
   "France → Provence", "USA → Brooklyn deli", "Korea → Seoul minimalist".

   STRICT RULES — WHEN "none" IS / IS NOT ALLOWED:
   ✓ "none — modern global" is ALLOWED only for genuinely culture-neutral verticals:
       B2B SaaS, dev tools, AI infrastructure, generic agency, internal admin tools,
       crypto/web3, fintech dashboards. These have no inherent national identity.

   ✗ "none" is FORBIDDEN for any of these — pick a region even if the user didn't say:
       restaurants / cafés / bakeries / bars (food + hospitality)
       hotels / B&Bs / travel agencies / tour operators (travel + hospitality)
       fashion / apparel / shoes / accessories (consumer goods)
       beauty / salons / spas (lifestyle services)
       wellness / yoga / fitness studios (lifestyle services)
       ecommerce for consumer products
       portfolio sites / personal brands

   When the user prompt is ambiguous (e.g. "a restaurant", "a coffee shop",
   "a fitness studio") and gives no cuisine / region / style cue, YOU MUST PICK
   a regional anchor by inferring from context or rolling a tasteful default.
   Be deliberate — don't always default to NYC/Brooklyn or Paris/France. Examples
   of valid auto-anchors when the prompt is generic:

     "a restaurant"       → "USA → Brooklyn deli", "France → Lyonnaise bistro",
                            "Mexico → Mexico City taquería", "USA → Pacific NW
                            farm-to-table", "Japan → Tokyo izakaya", "Spain →
                            Andalusian taberna" — pick ONE deliberately.
     "a coffee shop"      → "USA → Portland third-wave", "Australia → Melbourne
                            specialty", "Italy → Roman espresso bar", "Vietnam
                            → Hanoi cà phê" — pick ONE.
     "a fashion brand"    → "France → Parisian minimalism", "Japan → Tokyo
                            avant-garde", "Italy → Milan tailoring", "Denmark →
                            Copenhagen utilitarian" — pick ONE.
     "a hotel"            → "Greece → Cycladic", "Morocco → Marrakech riad",
                            "Mexico → Tulum coastal", "Japan → Kyoto ryokan" —
                            pick ONE.

   Rotate variety_seed-style — for ambiguous prompts, pick a culturally distinct
   anchor each run so two "a restaurant" generations don't both default to the
   same Brooklyn-deli look. The point is to ALWAYS be evocative and specific —
   ambiguity in user prompt is opportunity for the system to pick richly, not
   an excuse to fall through to generic.]

authenticity_cues:
  [5-8 specific cultural/visual elements that signal "this is genuinely FROM that place,"
   not a tourist's idea. Be concrete and unfamiliar — avoid Eiffel Tower / pizza-hat
   clichés. For Italian trattoria, NOT "checkered tablecloths" but "hand-rolled pasta
   on a flour-dusted wooden board, copper pots above an open kitchen, vintage Faema
   espresso machine, hand-painted Vietri ceramic plates, family photos in mismatched
   frames, candlelit Tuscan-stone walls". Each cue: 1 short concrete phrase.]

signature_imagery:
  [6-10 specific image search terms / scene descriptions that would feel authentic to
   the culture, NOT generic stock photography. Each as: "search_term: [terms] | mood: [mood]".
   For Italian: "fresh tagliatelle on floured board | rustic warm", "wood-fired pizza
   oven flames | dramatic close-up", "espresso crema pour | quiet morning", "antique
   copper saucepan on stove | lived-in", "italian nonna hands rolling dough | hands-only".
   Use these EXACT search terms in hero_image_url and supporting_image_urls — the
   default picks ("modern restaurant interior" / "elegant dining table") are forbidden.]

cultural_palette:
  [Palette grounded in cultural authenticity, not generic "premium dark + gold".
   For Italian trattoria: "warm cream + sun-dried tomato red + olive green + aged
   copper accent" — reasoning: "matches a traditional southern-Italian trattoria
   palette, not a corporate steakhouse". Output as: "values: [4-5 named colors] |
   reasoning: [why these and not navy+gold]". If country_or_region is "none",
   output "use design system default" and skip.]

typographic_signature:
  [ONE typographic move that feels CULTURALLY authentic to the place, not the
   industry default. For Italian: "vintage hand-painted Italian café signage style
   heading (Cooper, Beth Ellen, or Reenie Beanie for accents) + warm humanist sans
   for body (Source Sans, Mulish)". For Japanese: "thin elegant serif (Shippori
   Mincho) + clean geometric sans (Noto Sans JP) with extra leading". Avoid the
   generic Playfair/Fraunces+Inter pairing if a culturally-specific pairing fits.]

language_phrases:
  [3-6 untranslated source-language words or short phrases to weave into UI copy as
   section headers, eyebrow tags, accent words, or microcopy. For Italian:
   "Antipasti / Primi / Secondi / Dolci" (menu sections), "La Famiglia" (about),
   "Benvenuti" (welcome eyebrow), "Buon Appetito" (CTA accent).
   For Japanese: "おもてなし (omotenashi)" as a values eyebrow, "本日のおすすめ (today's
   recommendation)" for a feature label. NEVER translate these — they are atmospheric
   anchors. If country_or_region is "none", output "n/a" and skip.]

section_label_overrides:
  [If the cultural mapping suggests renaming standard sections, list as
   "default → cultural" pairs. For Italian restaurant:
   "Menu → La Carta", "Our Story → La Nostra Storia", "Reservations → Prenotazioni",
   "Hours → Orari". Codegen will use these renamed labels in headers and section
   eyebrows. If country_or_region is "none", skip.]

banned_generics:
  [5-8 design choices that would make THIS specific cultural site feel generic /
   AI-generated. For Italian trattoria: "navy + gold palette (looks corporate, not
   trattoria)", "blurry restaurant tablescape stock photo (every AI site)", "Playfair
   italic at 8rem (overused on every restaurant template)", "'Reserve a Table' CTA
   without Italian flavor — use 'Prenota' or 'Riserva il tuo Tavolo'".
   These are FORBIDDEN for this run. If country_or_region is "none", list cliches
   for the industry instead (SaaS: "blue gradient hero", "abstract dashboard mockup
   floating in the void").]

motif_inventory:
  [4-6 small recurring decorative motifs to use as signature_motif and supporting
   decorations across sections. For Italian: "olive branch SVG", "hand-drawn pasta
   shape outline (penne, fusilli silhouette)", "vintage Italian postage stamp border",
   "cracked terracotta texture overlay at 8% opacity", "small espresso cup icon".
   Codegen will scatter these 2-3× across sections per signature_motif spec.]

===COPY_TONE===
hero_headline_style: [punchy|formal|warm|bold — max chars and style description]
body_copy_style: [tone description]
cta_style: [verb style]
cultural_voice_overlay: [if cultural_atmosphere is set, override default copy tone
  with a culturally-specific voice. Italian trattoria: "warm, familial, slightly
  exclamatory — like a host welcoming you in. Use occasional Italian phrases as
  accents, not translation". Japanese izakaya: "quiet, precise, respectful, with
  small 'お' honorific touches in microcopy." If "none", output "n/a".]

===VISUAL_DISTINCTIVENESS===
anti_generic:
  [3 SPECIFIC layout/visual choices that ensure this site looks NOTHING like a
   generic AI-generated template. Be concrete and actionable for a developer.
   BAD: "use unique colors" or "add animations" (too vague)
   GOOD examples:
     "hero: stagger headline words on 3 separate lines each offset-x by +40px,
      creating a diagonal reading path instead of a left-aligned block"
     "features: use a magazine-editorial numbered list (01. 02. 03.) with
      text-8xl font-black numbers bleeding behind the card border"
     "testimonials: single rotating full-bleed pull-quote with 5rem italic
      serif text, no avatar cards — one voice at a time, auto-scrolling"
     "menu section: horizontal scrolling film strip of dish photos with
      parallax offset, not a static 3-col grid"]

visual_surprise:
  [ONE unexpected detail a senior designer would notice and appreciate.
   This MUST appear somewhere on the rendered page — not be skipped.
   Examples:
     "a vintage receipt-paper SVG texture overlaid on the menu section at 6% opacity"
     "hero stat cards (4.9★ / 2,400 subs / 12 origins) that count up with
      IntersectionObserver when they scroll into view"
     "a slowly rotating SVG quote mark (360° / 30s, opacity 8%) centered
      behind the testimonials headline"
     "the CTA button has a subtle shimmer sweep animation on hover
      (background-position: 200% 0 → 0 0 over 600ms)"
     "section dividers are hand-drawn wavy SVG paths instead of straight lines"
   This is NOT optional — it must be implemented in the generated code.]

section_card_matrix:
  [For EACH section in ===SECTIONS===, assign which card style to use and WHY.
   Rule: No two consecutive sections may use the same card style.
   Rule: At most 2 sections total may use the same card style on the whole page.
   Styles: soft | glass | featured | no-card (full-bleed layout, no card wrapper)
   Format: section_name → style | reason (1 phrase)
   Example:
     hero         → no-card   | split layout, no wrapper needed
     features     → featured  | bento hero tile needs premium gradient
     story        → no-card   | full-bleed image section
     menu         → soft      | clean item cards on light bg
     testimonials → glass     | cards float on dark branded background
     cta_final    → no-card   | full-width branded band, no card]

section_spacing_rhythm:
  [Override default py-20 for sections where it would create identical visual weight.
   Only list sections that need a different rhythm. Format: section_name → padding | reason
   Example:
     hero         → min-h-[90vh] flex items-center | needs full viewport presence
     menu         → py-32 | rich imagery needs breathing room
     hours        → py-12 | compact info section, tight is intentional
     cta_final    → py-24 | standard CTA band height]

===HEADER_DESIGN===
Study the top sites in this domain. Pick a header that feels native to THIS brand —
not the generic SaaS sticky-nav template. Every field drives the actual rendered JSX.

structure:
  [ONE of:
    logo_left_nav_right  — brand left, nav items right, CTA far right (classic horizontal)
    centered_logo        — brand centered, nav splits evenly left and right of it
    logo_left_hamburger  — brand left, single "Menu" trigger right, fullscreen overlay nav
    floating_pill        — pill-shaped capsule floating 16px from top, max-w-3xl centered
    two_row              — tall header: brand + tagline top row, nav bottom row (fashion/retail)
  reasoning: [why this structure fits the brand — 1 sentence referencing real site]

surface:
  [ONE of:
    light_blur           — bg-background/92 backdrop-blur border-b border-border (default)
    dark_solid           — bg-foreground text-background (high-contrast editorial)
    transparent_scroll   — transparent over hero, morphs to light_blur after 60px scroll
    primary_tinted       — bg-primary/8 border-b border-primary/20 (subtle brand wash)
    glass_dark           — bg-black/30 backdrop-blur-xl text-white (over dark/photo hero)
  reasoning: [why — 1 sentence]

nav_link_style:
  [ONE of:
    plain                — text-foreground/70 hover:text-foreground transition-colors
    underline_slide      — underline animates left→right on hover (h-px bg-primary absolute)
    uppercase_track      — text-[11px] uppercase tracking-[0.18em] text-foreground/55
    pill_hover           — hover:bg-muted rounded-full px-3 py-1 (subtle pill on hover)
    dot_left             — small dot (h-1.5 w-1.5 bg-primary rounded-full) appears left on hover

cta_style:
  [ONE of:
    filled_pill          — rounded-full bg-primary px-6 shadow-sm (modern, friendly)
    filled_sharp         — rounded-md bg-primary px-5 (standard, clean)
    ghost_pill           — rounded-full border border-foreground/30 px-5 (elegant, minimal)
    ghost_sharp          — rounded-md border border-border px-5 (subtle, serious)
    text_arrow           — plain text + " →" no button wrapper (ultra-minimal)
    inverted_pill        — rounded-full bg-background text-foreground px-5 (for dark surface)

height:
  [ONE of: slim (h-14) | standard (h-16) | tall (h-20) | masthead (h-24)]

top_accent:
  [ONE of:
    none                 — no decorative top element
    primary_bar          — 3px border-t border-primary above the header (signature stripe)
    gradient_wash        — bg-gradient-to-r from-primary/20 via-transparent to-accent/20 as top strip

mobile_menu:
  [ONE of: slide_drawer | fullscreen_overlay | simple_dropdown]
  reasoning: [why this fits the brand UX — 1 phrase]

===IMAGE_SOURCES===
hero: [treatment from research]
content_images: https://picsum.photos/seed/[descriptive_seed]/800/600
avatars: https://i.pravatar.cc/150?u=[unique_string]
icons: Lucide React

CRITICAL: Every value from REAL internet research. Original copy. Domain-specific. Production-quality.
"""

    # ── Design prompt: visual DNA, motion, LAYOUT_BLUEPRINT ──────────────────
    # Builds ERA_CALIBRATION + LIVE_UI_RESEARCH + LAYOUT_BLUEPRINT sections.
    # Runs concurrently with the structure prompt above.
    _is_sidebar_layout = layout_archetype in ("admin_dashboard", "crm", "tms", "saas_dashboard")
    _layout_bp_block = (
        "SKIP the ===LAYOUT_BLUEPRINT=== block — this layout_archetype uses a fixed sidebar design."
        if _is_sidebar_layout else
        """You are the creative director of an award-winning design studio.
DESCRIBE the visual design DNA for this site. Every field needs 2-4 specific, concrete sentences.

MANDATORY:
  • Name at least ONE real award-winning site as inspiration (from your research above — not from memory)
  • Be specific: "diagonal split with tilted polaroid on warm beige" beats "split-screen with image"
  • NOVELTY COMMITMENT: your design must be visibly different from a generic version of this site

SKIP this entire ===LAYOUT_BLUEPRINT=== block ONLY if layout_archetype is
admin_dashboard / crm / tms / saas_dashboard (fixed sidebar layouts).

===LAYOUT_BLUEPRINT===

hero_description:
  [2-4 sentences: spatial arrangement, imagery treatment, decorative elements, eye travel path.]
  inspiration_site: [real URL from your research]
  why_this_fits: [1 sentence]

features_description:
  [2-4 sentences. Must be spatially DIFFERENT from hero_description — no same grid used twice.]
  inspiration_site: [real URL]

secondary_sections_description:
  [1-2 sentences per remaining section. Include at least one "surprising" section layout.]

section_rhythm:
  [Every section in order → background treatment. Max 2 consecutive sections same treatment.]

signature_motif:
  [ONE repeated decorative element appearing 2-3× across the page. Specific: shape, color, opacity, placement.]

design_mood:
  [2-3 sentences: visual personality, ONE-WORD adjective, how it translates to type/cards/color.]
  reasoning: [1 sentence citing a research finding or target-user insight]

hero_image_strategy:
  [ONE of these — picks how the hero uses imagery. Coupled to hero_archetype below:
    • background_full      — image is full-bleed backdrop with a scrim. ONLY valid for
                             hero_archetype = cinematic-parallax / full-bleed-dark.
    • structural_half      — image is a HALF or THIRD of the hero (one column/quadrant).
                             Use for: split / editorial-offset / product-showcase.
    • single_feature_card  — ONE small image inside a card or framed panel, NOT a backdrop.
                             Use for: magazine / bento (image as one tile).
    • decorative_scatter   — 3-5 SMALL images scattered around the headline at different
                             sizes/rotations/positions (a wine bottle, a tapas plate,
                             a small portrait — each rotated/offset distinctly). NO single
                             dominant photo. Use for: bento / diagonal / playful brands.
    • illustration_3d      — request a 3D-style render or SVG illustration instead of a
                             photo (Spline-style geometry, abstract gradient blobs,
                             isometric scenes). Use for: tech, SaaS, abstract brands,
                             agency sites, anywhere a photo would feel literal/cliched.
    • typographic_only     — NO hero image at all. Pure typography hero. ONLY valid for
                             hero_archetype = typographic-hero.
    PICK based on what would look LEAST like a generic AI-generated landing page. If the
    domain is "boring" (consulting, legal, finance, education) prefer illustration_3d or
    decorative_scatter over a stock photo. If the project is product-focused (e-bike,
    audio gear, fashion) prefer structural_half with a real product photo.
  ]
  reasoning: [1 sentence on why this strategy fits the domain + chosen hero_archetype]

hero_image_url:
  [Provide an Unsplash URL ONLY when hero_image_strategy ∈ {background_full, structural_half,
   single_feature_card}. For decorative_scatter list 3-5 URLs separated by " | ".
   For illustration_3d output the literal string "GENERATE_3D" (codegen will substitute
   an SVG/Spline placeholder). For typographic_only output the literal string "NONE".
   When provided as URL: https://images.unsplash.com/photo-PHOTO_ID?auto=format&fit=crop&w=1600&q=80]

supporting_image_urls:
  [3-6 Unsplash URLs: "section_name: https://images.unsplash.com/photo-PHOTO_ID?auto=format&fit=crop&w=1200&q=80"]

accent_detail:
  [ONE signature micro-detail a tired designer would skip. Specific to THIS domain.]
  placement: [exact Tailwind position — e.g. "absolute -bottom-4 -left-4 z-20"]

hero_archetype:
  [ONE of: split / bento / diagonal / magazine / layered-scroll / cinematic-parallax /
   editorial-offset / full-bleed-dark / product-showcase / typographic-hero / INVENT one]
  reasoning: [1 sentence why this fits the domain mood]

features_archetype:
  [ONE of: bento-mixed / zigzag / vertical-tabs / horizontal-scroll / masonry /
   tilt-stack / showcase / timeline / numbered-editorial / INVENT.
   These EXACT names map to wrapper JSX templates — do not invent variants
   like `zigzag-split` or `interactive-showcase`; pick the canonical name.]
  reasoning: [1 sentence]

card_language:
  [1-2 sentences beyond "rounded-xl shadow-md". Include: radius style, border, shadow/glow, micro-element.]

typography_pairing:
  [Heading font + body font (real Google Fonts, different from each other). Weight/tracking/italic rules.]

motion_language:
  [2-3 complementary behaviors. MUST specify: enter animation, hover state, scroll-linked behavior, emotional register.]

decorative_pattern:
  [ONE pattern/texture across multiple sections at low opacity. Include how it's applied in Tailwind/CSS.]

border_radius_language:
  [Philosophy + EXACT Tailwind values for: buttons, cards, images, inputs.]

color_application_strategy:
  [ONE of: mono-accent / duotone-photos / gradient-mesh / inverted-dark / polychrome /
   photographic-neutral / brand-flood. Specify which sections get which treatment.]

hover_interaction_style:
  [1-2 consistent behaviors for: primary cards, CTAs, image cards, nav links.]

spacing_rhythm:
  [ONE of: tight-editorial / standard-modern / airy-luxury / asymmetric / dense-information.
   Container max-width + gap values.]

live_ui_recipe:
  [4-6 sentences: what is the ONE signature motion moment that makes visitors stop?
   Plus supporting micro-interactions throughout the page. Domain-specific — not generic.]

scroll_reveal_style:
  [Exact framer-motion values: y-distance, duration, easing, stagger interval, viewport amount trigger.]

counter_animation:
  [Which stats animate as counters, which section they're in, end value + format, animation duration.
   Pattern: framer-motion useMotionValue + useTransform + animate on inView.]

marquee_strip:
  [logo-strip / quote-ticker / stat-ticker / none. Which section, exact content, CSS keyframe approach.
   Preferred: two identical UL sets side by side, overflow-hidden parent, translate-left 50% animation.]

ambient_motion:
  [gradient-mesh / floating-orbs / grain-noise / subtle-scan / none.
   Include CSS keyframe snippet and JSX element placement.]

design_interactions:
  [CRITICAL self-audit. Your fields above are independent decisions, but they
   COLLIDE in the rendered DOM. Walk through these dependencies and commit to
   resolutions. Output as 7 numbered lines — one decision per line. Do NOT
   write paragraphs; write enforceable rules Claude will follow verbatim.]

  1. NAV-OVER-HERO CONTRAST — given your hero_archetype + hero_image_strategy:
     does the global header sit OVER an image at any horizontal slice (full-bleed
     OR split-hero where image touches viewport edge)? If yes, commit to ONE:
     (a) backdrop-blur-md + bg-background/70 + border-b border-border/30,
     (b) constrain header max-width to text-side column only,
     (c) solid-bg header with subtle shadow.
     ✗ NEVER: raw transparent header over imagery. State your pick.

  2. EDGE-TO-EDGE BLEED — list every section/card/hero that touches viewport
     edge with no padding. For each: what visually separates it from the
     element above (border, shadow, gradient, height step)?

  3. CARD INTERNAL LAYOUT — if cards carry MULTIPLE badges/tags/floating
     labels: they MUST live in ONE flex container (`flex gap-2 items-center`),
     never as independent `absolute` siblings. State this for product/listing/
     vehicle cards if your domain uses them. ✗ Two `absolute top-4 left-4`
     siblings will overlap — banned.

  4. SECTION TRANSITIONS — given section_rhythm: where do two adjacent sections
     share the same background color? Each such pair needs an explicit visual
     separator (top border, gradient transition, shape divider, or anchor band).

  5. FORM TREATMENT — if any page contains a form with a select/dropdown/combobox:
     declare "USE shadcn Select primitive (SelectTrigger/SelectContent/SelectItem),
     NEVER native <select>." State which pages have forms.

  6. TYPOGRAPHY CEILING — given your typography_pairing + hero_archetype: state
     max display size on desktop. Hero h1 ≤ text-6xl unless typographic-hero
     archetype (then ≤ text-7xl). No text-8xl/9xl.

  7. OVERLAP & Z-LAYERS — list every `absolute` / `fixed` / `sticky` element
     across the page. For each: what sits beneath it, what guarantees contrast,
     what is its z-index? If two elements overlap, the rule must be written here.

novelty_check:
  [One sentence: what makes YOUR design visibly different from any other designer's version of this site?]

design_dna_summary:
  [ONE sentence, 15-25 words: the unique design recipe. Example: "Editorial-luxe Fraunces-italic headlines
   over cinematic full-bleed photography, paper-fold cards, airy 40vh spacing, brand-flood CTA finale."]

CRITICAL: Concrete, code-translatable language. No one-word answers. No generic phrases like "modern clean design"."""
    )

    _design_prompt = f"""You are an AWARD-WINNING VISUAL DESIGN DIRECTOR with full internet search access.
Your sole task: research 2025-2026 visual design trends for the project below, then output ONLY these three
sections: ERA_CALIBRATION, LIVE_UI_RESEARCH, LAYOUT_BLUEPRINT.
Do NOT output CLASSIFICATION, CSS_VARIABLES, FONTS, PAGES, ENTITIES, or any structural blocks.

⚠️ OUTPUT FORMAT — STRICT:
  Section headers MUST be the LITERAL string `===NAME===` on their own line.
  ✓ CORRECT:   ===ERA_CALIBRATION===
  ✗ WRONG:     ### ERA_CALIBRATION
  ✗ WRONG:     **ERA_CALIBRATION**
  ✗ WRONG:     ERA_CALIBRATION:
  Sub-fields are plain `key: value` lines (no markdown bold). A downstream parser
  searches for the literal `===HEADER===` markers — anything else is dropped silently.

PROJECT: "{description}"
DOMAIN: {domain}
LAYOUT TYPE: {_archetype_label}
CLASSIFICATION (locked — do NOT change):
  layout_archetype: {layout_archetype}
  is_single_page: {"yes" if is_single_page else "no"}

════════════════════════════════════════════════════════════
DESIGN RESEARCH — MUST use google_search (do NOT rely on training memory)
════════════════════════════════════════════════════════════

Training data cutoff is mid-2024. The current year is 2026. YOU MUST USE google_search
to find what's actually trending in 2025-2026 — do not rely on training memory alone.

Run AT LEAST these searches:
  1. "awwwards {domain} site of the year 2025"
  2. "awwwards {domain} site of the year 2026"
  3. "best {domain} website visual design typography 2026"
  4. "2026 web design trends {domain} scroll-driven animations variable fonts"
  5. "{domain} website hover interactions micro-animations 2025 2026"
  6. "best {domain} website design inspiration 2025 2026"
  7. (if "{description}" names a specific brand) "{description}" official site visual design

For EACH site discovered, note:
  a) Layout: H1 position, split/bento/full-bleed, asymmetry, grid structure
  b) Typography: font names, weight contrast, italic/caps, oversized type treatment
  c) Motion: scroll animations, hover effects, stagger reveals, parallax depth
  d) "Live" feel: counters, marquees, ambient animations, video, looping elements

════════════════════════════════════════════════════════════
DESIGN ERA CALIBRATION
════════════════════════════════════════════════════════════

FORBIDDEN — NEVER USE THESE (dated / generic signal of AI-generated templates):
  ✗ [2020-2022 era] Flat pastel gradient hero with centered text stack and no motion
  ✗ [2020-2022 era] Symmetric 3-column feature grid with icon-over-title-over-description, no hover effect
  ✗ [2020-2022 era] Generic rounded-xl cards with small shadow and nothing else distinctive
  ✗ [2020-2022 era] "From $X/month" pricing cards all identical shape
  ✗ [2020-2022 era] Stock photos of smiling office workers / diverse-team-around-laptop
  ✗ [2020-2022 era] Hero H1 with two dead-centered CTAs and no imagery breaking the grid
  ✗ [2020-2022 era] Hero with bg-background/95 washing out a photo (use dark gradient overlays)
  ✗ [2020-2022 era] Nav with "About / Features / Pricing / Sign In / Get Started" on a non-SaaS site
  ✗ [2020-2022 era] Static stat counters that don't animate when scrolled into view
  ✗ [2020-2022 era] Logo rows with no scroll or motion
  ✗ [2020-2022 era] Hero that has zero motion — everything must have at least an entry animation
  ✗ [2023-2024 era — now overused by AI tools, avoid these as defaults]:
    - Generic bento grid with uniform-sized tiles and no visual hierarchy or content strategy
    - Two blurred gradient orbs as the ONLY hero background decoration
    - Glassmorphism (backdrop-blur) cards on non-photo backgrounds
    - Inter + Geist font pairing (2024 AI default, recognizable from a mile away)
    - Purple/indigo gradient as the go-to SaaS accent color
    - "Frosted glass card on white background" navbar that blurs nothing
    - Dark mode with generic #18181b background + white text and no real palette work

REQUIRED 2025-2026 moves (blueprint MUST include at least 5 of these):
  ✓ Oversized editorial type — at least one H1 using clamp(4rem,10vw,9rem) with
    leading-[0.88] and tight tracking. Type as the primary design element, not decoration.
  ✓ Asymmetric layout — at least one section breaks the symmetric grid: editorial
    magazine offset, staggered columns, intentional negative space, or content
    bleeding out of the container.
  ✓ Depth via material contrast — NOT heavy drop-shadows; instead: hairline borders
    (border-foreground/10), one dramatically inverted section (bg-foreground text-background),
    or a full-bleed dark photo. Restraint IS the luxury signal.
  ✓ Signature motif applied 2-3x — grain noise (opacity-[0.04]), topographic lines,
    hand-drawn squiggle, dot grid, or a brand-specific shape. Must be specific to
    this domain, not a generic choice.
  ✓ Scroll-linked stagger reveals — each card enters 60-80ms after the previous
    (framer-motion staggerChildren or CSS animation-delay).
  ✓ Sticky header scroll transition — transparent → frosted-glass bg-background/90
    backdrop-blur-md on scroll (still essential in 2026, still differentiates premium sites).
  ✓ Horizontal marquee strip for logos / testimonials / stats — CSS keyframe, no JS library.
  ✓ Type-first section — at least one section where oversized text + spacing IS the full
    design (no icons, no photos, just letterforms + tight grid).
  ✓ Animated stat counters — numbers count up from 0 when scrolled into view.
  ✓ Hero entry sequence — badge → H1 → subtitle → CTA → image, cascading 100-600ms delays,
    NOT all at once. Use framer-motion initial/animate (not whileInView for hero).
  ✓ Ambient motion in hero — at minimum one of: CSS scroll-driven background shift,
    grain texture overlay, slow-drifting orbs COMBINED WITH grain/topographic lines
    (orbs alone is the 2024 cliché — layer at least one other texture).
  ✓ Variable font personality — if the heading font supports variable axes, animate
    font-weight on hover (100→700) or use clamp() to vary weight with viewport width.

===ERA_CALIBRATION===
moves_borrowed: [List the 5+ 2025-2026 moves you will use and WHY — 1 line each]
patterns_avoided: [List the 3-5 dated patterns you rejected and why — include both 2020-2022 and 2023-2024 era clichés]

════════════════════════════════════════════════════════════
LIVE UI RESEARCH
════════════════════════════════════════════════════════════

Based on your research above, answer: what makes top {domain} websites feel "live" and
premium in 2025-2026?

===LIVE_UI_RESEARCH===
scroll_effects: [stagger? parallax? clip-path reveals? opacity wipes? Be specific with values]
counter_animations: [which stats animate, what section, format: currency/%/number+?]
ticker_or_marquee: [logo-strip / testimonial-ticker / stat-ticker / none + description]
hover_depth: [flat-lift / tilt-3D / glow-ring / reveal / morph? Specify per element type]
video_or_loop: [hero video / looping animation / none?]
sticky_behavior: [nav color/blur change? any sticky panels?]
text_animation: [character reveal / word reveal / number odometer / typing effect?]
ambient_motion: [gradient-mesh shift / floating-orbs / grain-noise / subtle-scan / none?]
interaction_richness: [1-5 scale]

════════════════════════════════════════════════════════════
LAYOUT BLUEPRINT
════════════════════════════════════════════════════════════

{_layout_bp_block}

CRITICAL: Every value from your research above. Concrete, code-translatable language.
Output ONLY ERA_CALIBRATION, LIVE_UI_RESEARCH, and LAYOUT_BLUEPRINT blocks.
"""

    # ── Parallel Gemini calls ─────────────────────────────────────────────────
    _research_model = os.environ.get("GEMINI_RESEARCH_MODEL", "gemini-3.1-pro-preview")
    await _ws_send(websocket, "progress", f"🔬 Calling {_research_model} — structure & design in parallel...")

    _is_pro = "pro" in _research_model.lower()

    # Run structure research (content/pages/entities) and design research
    # (LAYOUT_BLUEPRINT/LIVE_UI/ERA_CALIBRATION) concurrently.
    # _gemini_semaphore(2) lets both slots proceed in parallel for a single project
    # while a second concurrent project waits, keeping inside Gemini rate limits.
    _results = await asyncio.gather(
        # Structure call bumped 14K → 32K so admin projects with 6-10
        # entities × 3 screens × rich ENTITY_SCREENS spec don't get
        # truncated mid-block. Multi-page sites with 6-8 pages × rich
        # per-section spec also benefit. We pay only for what's used —
        # this is a CEILING, not a target.
        _call_gemini_single(research_prompt, _research_model, _is_pro, websocket, "structure", max_tokens=32000),
        _call_gemini_single(_design_prompt, _research_model, _is_pro, websocket, "design", max_tokens=12000),
        return_exceptions=True,
    )

    structure_text = _results[0] if not isinstance(_results[0], Exception) else ""
    design_text = _results[1] if not isinstance(_results[1], Exception) else ""

    if not structure_text:
        # Structure is mandatory — propagate the failure
        _exc = _results[0] if isinstance(_results[0], Exception) else RuntimeError("Gemini structure returned empty")
        raise _exc

    if isinstance(_results[1], Exception):
        logger.warning("Gemini design research failed (non-fatal): %s", _results[1])
        await _ws_send(websocket, "progress", "⚠️ Design research partial — continuing with structure only...")

    # Normalize any markdown headers Gemini emitted (### NAME / **NAME**) back
    # to the literal ===NAME=== form. Live testing showed Gemini occasionally
    # ignores the format directive on the smaller design prompt, which silently
    # drops every design-only section downstream.
    if design_text:
        design_text = _normalize_research_headers(design_text, _DESIGN_SECTION_NAMES)

    # Merge: structure sections come first so _extract_research_section (which
    # returns the FIRST occurrence) finds structural CLASSIFICATION/PAGES/ENTITIES
    # correctly; design-only sections (LAYOUT_BLUEPRINT, LIVE_UI_RESEARCH,
    # ERA_CALIBRATION) appear only in design_text and are appended after.
    text = structure_text + ("\n\n" + design_text if design_text else "")

    await _ws_send(websocket, "progress", "✅ Research complete — building project blueprint...")

    # Send research summary to chat panel
    try:
        analyzed_section = ""
        for _header in ("===PRODUCTS_ANALYZED===", "===SITES_ANALYZED===", "===PLATFORMS_ANALYZED==="):
            if _header in text:
                _start = text.index(_header) + len(_header)
                _rest = text[_start:]
                _end_match = _rest.find("===")
                _end = _start + _end_match if _end_match != -1 else _start + 500
                analyzed_section = text[_start:_end].strip()
                break

        _classification = ""
        if "===APP_CLASSIFICATION===" in text:
            _cs = text.index("===APP_CLASSIFICATION===") + len("===APP_CLASSIFICATION===")
            _ce_match = text[_cs:].find("===")
            _ce = _cs + _ce_match if _ce_match != -1 else _cs + 600
            _classification = text[_cs:_ce].strip()

        if analyzed_section:
            summary = f"🔍 **Research Complete**\n\n**Analyzed:**\n{analyzed_section[:600]}"
        elif _classification:
            summary = f"🔍 **Research Complete**\n\n**App classified:**\n{_classification[:600]}"
        else:
            summary = f"🔍 **Research Complete** — Analyzed top {layout_archetype.replace('_', ' ')} ({domain}) products and synthesized blueprint"

        await websocket.send_json({
            "type": "chat_message",
            "role": "agent",
            "content": summary,
        })
    except Exception:
        pass

    return text
