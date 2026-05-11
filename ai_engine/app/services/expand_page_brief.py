"""Per-page content expansion via Gemini Flash.

Turns a sparse `project_brief.pages[i]` (with just `slug`, `title`, `page_goal`,
`section_types: [str, ...]`) into a fully-populated page brief — one Gemini
Flash call per page that fills the schema for every section type the page declares.

Design
======
- Schema-driven: section_schemas.py is the contract. Gemini is asked to fill
  the JSON shape for each section type; Claude (downstream) reads the same shape.
- Per-page call: one Gemini call covers all sections of one page in a single
  prompt. Cheaper than per-section (less context redundancy) and gives Gemini
  whole-page context (useful for tone consistency across sections).
- Resilient to drift: missing fields are filled with empty strings/arrays so
  Claude can render without crashing.

Public entry points
===================
  expand_page_brief(page, project_brief, ...) -> page_brief | None
  expand_pages_many(pages, project_brief, ..., concurrency=4) -> [page_brief|None, ...]
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from app.services.section_schemas import (
    canonical_type,
    schema_for,
    description_for,
    schema_to_prompt_block,
)

logger = logging.getLogger(__name__)


# Flash model (Vertex). Per-page calls are small structured-output tasks —
# cheap, fast, no grounding needed (Gemini already did grounding earlier
# during domain/design research; the resulting voice signals are passed in here).
_EXPAND_MODEL = "gemini-3-flash-preview"

# Per-call output budget. A page with 8 sections × ~600 tokens each = ~5K
# tokens of JSON. 8K leaves comfortable headroom.
_EXPAND_MAX_OUTPUT_TOKENS = 8192

# Hard timeout. Most calls complete in 5-15s.
_EXPAND_DEFAULT_TIMEOUT_S = 60.0


# ── Prompt builders ──────────────────────────────────────────────────


def _voice_block(project_brief: dict[str, Any]) -> str:
    """Render the voice/personality slice of the project brief into a prompt block."""
    pers = (project_brief.get("personality") or {})
    voice = (project_brief.get("voice") or {})
    tone = pers.get("tone") or "confident"
    vibe = ", ".join(pers.get("vibe_keywords") or []) or "modern, clear"
    energy = pers.get("energy") or "medium"
    sample = (voice.get("sample") or voice.get("audience_voice") or "").strip()

    parts = [
        f"BRAND VOICE",
        f"  Tone: {tone}",
        f"  Vibe keywords: {vibe}",
        f"  Energy: {energy}",
    ]
    if sample:
        parts.append(f"  Reference voice sample (emulate cadence/word choice):\n    {sample[:400]}")
    return "\n".join(parts)


def _build_prompt(page: dict[str, Any], project_brief: dict[str, Any]) -> tuple[str, list[str]]:
    """Build the Gemini prompt + return the canonical section_types order.

    Returns (prompt_text, ordered_canonical_types).
    """
    brand = (project_brief.get("brand") or {}).get("name") or ""
    if not brand:
        brand = (project_brief.get("brand_name") or "").strip()

    industry = project_brief.get("industry") or project_brief.get("business_category") or ""
    audience = (project_brief.get("audience") or {}).get("primary") or ""
    purpose = project_brief.get("primary_purpose") or page.get("purpose") or ""

    slug = (page.get("slug") or "").strip("/")
    title = page.get("title") or page.get("nav_label") or slug or "Home"
    nav_label = page.get("nav_label") or title
    page_goal = page.get("page_goal") or ""
    primary_cta = page.get("primary_cta") or {}
    cta_label = primary_cta.get("label") or ""
    cta_href = primary_cta.get("href") or ""

    raw_types: list[str] = list(page.get("section_types") or [])
    # Resolve aliases, drop unknown types, dedupe while preserving order.
    canon_types: list[str] = []
    seen: set[str] = set()
    for t in raw_types:
        c = canonical_type(t)
        if not schema_for(c):
            logger.info("expand_page_brief[%s]: dropping unknown section_type '%s'", slug or "home", t)
            continue
        if c in seen:
            continue
        seen.add(c)
        canon_types.append(c)

    # Render schemas for each requested section type.
    schema_blocks: list[str] = []
    for c in canon_types:
        block = schema_to_prompt_block(c)
        if block:
            schema_blocks.append(block)

    voice = _voice_block(project_brief)

    prompt = f"""You are a senior copywriter for {brand or 'the brand'}.
Write copy for the "{title}" page of a multi-page website.

PAGE CONTEXT
  Slug: {slug or '(home)'}
  Title: {title}
  Nav label: {nav_label}
  Page goal: {page_goal}
  Primary CTA: "{cta_label}" → {cta_href}
  Industry: {industry}
  Primary audience: {audience}
  Site purpose: {purpose}

{voice}

YOUR TASK
Fill the section content shapes below with real copy. Match the brand voice exactly.
The copy should:
  • Read like a human wrote it — never marketing-fluff or corporate-speak.
  • Stay on-page-goal — every line should serve the page's stated goal.
  • Be concrete — prefer specific nouns and numbers over abstract claims.
  • Match length hints (5-10 words means 5-10 words, not 20).
  • Use real lucide-react icon names (e.g. "Zap", "Shield", "ArrowRight") for `icon` fields.

SECTION SHAPES TO FILL ({len(schema_blocks)} total — return them IN THIS ORDER):

{chr(10).join(schema_blocks)}

OUTPUT FORMAT
Return ONLY valid JSON, no markdown fences, no commentary.
Top-level shape:
{{
  "sections": [
    {{ "id": "<canonical_type>", "type": "<canonical_type>", ...filled schema fields... }},
    ... one entry per section shape above, in the same order ...
  ]
}}

Replace each "STRING — ..." description with actual copy. Replace BOOLEAN with true/false.
Where a field's hint says it can be empty (e.g. "'' if not used"), use an empty string ""
rather than omitting the field. Arrays must have at least 2 items unless the schema
says otherwise (e.g. testimonials → 3-4 items, faq → 4-6 items, plans → 3 plans).
"""
    return prompt, canon_types


# ── Validation / coercion ────────────────────────────────────────────


def _coerce_to_schema(filled: Any, schema: Any) -> Any:
    """Best-effort coercion: ensure the filled value has the schema's shape.

    For dicts: ensures every schema key is present (fills missing with "").
    For lists with a sub-schema: keeps the items, coerces each element.
    For leaf values: passes through if string/bool/number; coerces to "" otherwise.
    """
    # Schema element is a list of one item — caller wants a list of objects.
    if isinstance(schema, list):
        if not isinstance(filled, list):
            return []
        item_schema = schema[0] if schema else None
        if item_schema is None:
            return filled
        return [_coerce_to_schema(item, item_schema) for item in filled]

    # Schema element is a dict — recurse field by field.
    if isinstance(schema, dict):
        if not isinstance(filled, dict):
            return {k: _coerce_to_schema({}, v) if isinstance(v, dict) else
                       (_coerce_to_schema([], v) if isinstance(v, list) else "")
                    for k, v in schema.items()}
        out: dict[str, Any] = {}
        for k, v in schema.items():
            out[k] = _coerce_to_schema(filled.get(k), v)
        return out

    # Schema is a leaf description string — return the filled value as-is
    # if it looks reasonable, else fall back to empty.
    if filled is None:
        return ""
    if isinstance(filled, (str, int, float, bool)):
        return filled
    # Unexpected: filled is dict/list where schema expected scalar.
    return ""


def _normalize_sections(
    raw_sections: list[Any], canonical_types: list[str]
) -> list[dict[str, Any]]:
    """Coerce each Gemini section to its declared schema shape.

    Aligns by order — the i-th returned section is matched to the i-th
    requested canonical type. Falls back to type-key matching if Gemini
    reordered things.
    """
    out: list[dict[str, Any]] = []
    by_type: dict[str, dict[str, Any]] = {}
    for s in raw_sections:
        if isinstance(s, dict):
            t = canonical_type(s.get("type") or s.get("id") or "")
            if t:
                by_type.setdefault(t, s)

    for i, canon in enumerate(canonical_types):
        # Prefer positional match first (Gemini was asked to keep order).
        candidate: dict[str, Any] | None = None
        if i < len(raw_sections) and isinstance(raw_sections[i], dict):
            cand_type = canonical_type(raw_sections[i].get("type") or raw_sections[i].get("id") or "")
            if cand_type == canon:
                candidate = raw_sections[i]
        if candidate is None:
            candidate = by_type.get(canon)
        if candidate is None:
            logger.warning("expand_page_brief: no section returned for type '%s' — using empty stub", canon)
            candidate = {}

        schema = schema_for(canon) or {}
        coerced = _coerce_to_schema(candidate, schema)
        if not isinstance(coerced, dict):
            coerced = {}
        coerced["id"] = canon
        coerced["type"] = canon
        out.append(coerced)
    return out


# ── Core call ────────────────────────────────────────────────────────


async def expand_page_brief(
    *,
    page: dict[str, Any],
    project_brief: dict[str, Any],
    websocket: Any = None,
    timeout_s: float = _EXPAND_DEFAULT_TIMEOUT_S,
) -> dict[str, Any] | None:
    """Expand one page from a project brief into a page_brief with sections-with-copy.

    Returns:
        {
            "page_meta": {slug, title, nav_label, page_goal, primary_cta, ...},
            "sections": [{id, type, ...filled schema...}, ...],
        }
        or None on failure.
    """
    from app.services.gemini_http import gemini_post

    slug = (page.get("slug") or "").strip("/")
    label = f"expand_page_brief[{slug or 'home'}]"

    prompt, canon_types = _build_prompt(page, project_brief)
    if not canon_types:
        logger.warning("%s: no recognized section_types in page — returning empty stub", label)
        return {
            "page_meta": {
                "slug": slug,
                "title": page.get("title") or page.get("nav_label") or slug or "Home",
                "nav_label": page.get("nav_label") or page.get("title") or slug or "Home",
                "page_goal": page.get("page_goal") or "",
                "primary_cta": page.get("primary_cta") or {},
            },
            "sections": [],
        }

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.6,
            "maxOutputTokens": _EXPAND_MAX_OUTPUT_TOKENS,
            "responseMimeType": "application/json",
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }

    logger.info(
        "%s: starting (%d section types: %s)",
        label, len(canon_types), ", ".join(canon_types),
    )

    try:
        status, data, raw = await gemini_post(
            model=_EXPAND_MODEL,
            payload=payload,
            timeout_s=timeout_s,
            label=label,
        )
    except Exception as exc:
        logger.warning("%s: gemini_post raised — %s", label, exc)
        return None

    if status != 200 or data is None:
        logger.warning("%s: HTTP %s — %s", label, status, (raw or "")[:200])
        return None

    # Token billing — best-effort, never fatal.
    try:
        from app.services.billing_meter import report_token_usage
        u = data.get("usageMetadata") or {}
        _in = int(u.get("promptTokenCount", 0) or 0)
        _out = int(u.get("candidatesTokenCount", 0) or 0) + int(u.get("thoughtsTokenCount", 0) or 0)
        if _in or _out:
            report_token_usage(None, _in, _out, source=f"gemini_{label}")
    except Exception:
        pass

    # Extract text from Gemini response.
    text = ""
    try:
        from knowledge.loader import safe_gemini_text
        text = (safe_gemini_text(data) or "").strip()
    except Exception:
        try:
            text = (
                ((data.get("candidates") or [{}])[0])
                .get("content", {}).get("parts", [{}])[0]
                .get("text", "")
            ).strip()
        except Exception:
            text = ""

    if not text:
        logger.warning("%s: empty response text", label)
        return None

    # Strip stray markdown fences if Gemini added any.
    if text.startswith("```"):
        text = text.split("```", 2)[1] if "```" in text else text
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
        if text.endswith("```"):
            text = text[:-3].strip()

    try:
        parsed = json.loads(text)
    except Exception as exc:
        logger.warning("%s: JSON parse failed (%s) — first 300 chars: %s", label, exc, text[:300])
        return None

    raw_sections = parsed.get("sections") if isinstance(parsed, dict) else None
    if not isinstance(raw_sections, list) or not raw_sections:
        logger.warning("%s: response missing 'sections' array", label)
        return None

    normalized = _normalize_sections(raw_sections, canon_types)

    page_meta = {
        "slug": slug,
        "title": page.get("title") or page.get("nav_label") or slug or "Home",
        "nav_label": page.get("nav_label") or page.get("title") or slug or "Home",
        "page_goal": page.get("page_goal") or "",
        "primary_cta": page.get("primary_cta") or {},
    }

    logger.info("%s: ok — %d sections", label, len(normalized))
    return {
        "page_meta": page_meta,
        "sections": normalized,
    }


# ── Fan-out ──────────────────────────────────────────────────────────


async def expand_pages_many(
    *,
    pages: list[dict[str, Any]],
    project_brief: dict[str, Any],
    websocket: Any = None,
    concurrency: int = 4,
    timeout_s: float = _EXPAND_DEFAULT_TIMEOUT_S,
) -> list[dict[str, Any] | None]:
    """Fan out expand_page_brief across all pages of a project.

    Returns a list aligned with `pages` — entry is None for pages that failed.
    """
    sem = asyncio.Semaphore(concurrency)

    async def _one(page: dict[str, Any]) -> dict[str, Any] | None:
        async with sem:
            return await expand_page_brief(
                page=page,
                project_brief=project_brief,
                websocket=websocket,
                timeout_s=timeout_s,
            )

    tasks = [_one(p) for p in pages]
    return await asyncio.gather(*tasks, return_exceptions=False)
