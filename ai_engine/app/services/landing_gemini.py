"""Shared Gemini primitives for the landing pipeline.

Used by Stages 2-5 (domain research, design research, content/design
synthesis). ``landing_brief.py`` still uses its own copies of these
helpers — that's intentional: the legacy brief stays untouched until
Step 6 of the rebuild migrates everything to the new pipeline.

Public surface:
  • ``post_gemini``            — single POST + text/grounding extraction
  • ``grounded_research``      — Pro + google_search → markdown text
  • ``structured_distill``     — Flash + responseMimeType=application/json
  • ``grounding_source_count`` — count groundingChunks for telemetry
  • ``grounding_urls``         — extract canonical URLs from grounding metadata
  • ``looks_degenerate``       — detect "model dumped search snippets" failure
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)

# Model IDs — env-overridable so we can swap without code changes.
RESEARCH_MODEL = os.environ.get("LANDING_RESEARCH_MODEL", "gemini-2.5-pro")
DISTILL_MODEL  = os.environ.get("LANDING_BRIEF_MODEL",   "gemini-2.5-flash")


# ── Low-level POST ────────────────────────────────────────────────────

async def post_gemini(
    model: str, payload: dict, timeout_s: float, *, label: str, api_key: str = "",
) -> tuple[str, dict | None]:
    """POST to Gemini and extract text + grounding metadata.

    Returns ``(text, grounding_metadata)``. ``grounding_metadata`` is the
    raw ``candidates[0].groundingMetadata`` dict when present, else None.
    Routes via gemini_http shim — works against AI Studio or Vertex
    based on settings.USE_VERTEX_AI.
    """
    from app.services.gemini_http import gemini_post

    status, data, _ = await gemini_post(
        model=model,
        payload=payload,
        timeout_s=timeout_s,
        api_key=api_key,
        label=f"landing_{label}",
    )
    if status != 200 or data is None:
        return "", None

    # Token billing
    try:
        from app.services.billing_meter import report_token_usage
        u = data.get("usageMetadata") or {}
        _in = int(u.get("promptTokenCount", 0) or 0)
        _out = int(u.get("candidatesTokenCount", 0) or 0) + int(u.get("thoughtsTokenCount", 0) or 0)
        if _in or _out:
            report_token_usage(None, _in, _out, source=f"gemini_landing_{label}")
    except Exception:
        pass

    try:
        from knowledge.loader import safe_gemini_text
        text = safe_gemini_text(data).strip()
    except Exception:
        text = ""
        try:
            text = (
                ((data.get("candidates") or [{}])[0]).get("content", {}).get("parts", [{}])[0].get("text", "")
            ).strip()
        except Exception:
            text = ""

    grounding: dict | None = None
    try:
        cand0 = (data.get("candidates") or [{}])[0]
        if isinstance(cand0, dict):
            gm = cand0.get("groundingMetadata") or cand0.get("grounding_metadata")
            if isinstance(gm, dict):
                grounding = gm
    except Exception:
        grounding = None

    if not text:
        logger.warning("gemini %s: empty text in response", label)
        return "", grounding

    # Persist for diagnostics
    try:
        with open(f"/tmp/landing_{label}.txt", "w") as fh:
            fh.write(text)
    except Exception:
        pass

    logger.info("gemini %s: %d chars", label, len(text))
    return text, grounding


def grounding_source_count(grounding: dict | None) -> int:
    """Number of real web sources cited by the response.

    Gemini 2.5 returns ``groundingMetadata.groundingChunks`` only when
    ``google_search`` was actually invoked. Empty / missing means the
    model answered from training memory.
    """
    if not isinstance(grounding, dict):
        return 0
    chunks = grounding.get("groundingChunks") or grounding.get("grounding_chunks") or []
    if not isinstance(chunks, list):
        return 0
    return len(chunks)


def looks_degenerate(text: str) -> bool:
    """Detect 'model dumped search snippets' failure mode.

    Targets the bailout where Gemini's tool-use loop fed back the same
    80-char headline 80-100 times, accounting for ~95% of all output.
    Structured bullet-list research (10 items each with the same 6
    bullet labels) MUST pass — labels like "**Tagline:**" repeat 10x
    by design.

    Heuristic: a call is degenerate ONLY if a single long line
    (≥40 chars) accounts for ≥50% of total non-whitespace chars AND
    repeats more than 8 times. Both conditions required.
    """
    if not text or len(text) < 800:
        return False

    lines = [ln.strip() for ln in text.splitlines() if len(ln.strip()) >= 40]
    if not lines:
        return False

    from collections import Counter
    counts = Counter(lines)
    most_common_line, most_common_count = counts.most_common(1)[0]

    if most_common_count <= 8:
        return False

    repeat_chars = most_common_count * len(most_common_line)
    nonws_total = len(re.sub(r"\s+", "", text))
    if nonws_total == 0:
        return False

    return (repeat_chars / nonws_total) >= 0.5


def grounding_urls(grounding: dict | None) -> list[str]:
    """Pull canonical URLs from groundingChunks for downstream synthesis.

    Returns a deduped list preserving order. Empty when grounding didn't
    happen.
    """
    if not isinstance(grounding, dict):
        return []
    chunks = grounding.get("groundingChunks") or grounding.get("grounding_chunks") or []
    seen: set[str] = set()
    urls: list[str] = []
    for c in chunks:
        if not isinstance(c, dict):
            continue
        web = c.get("web") or {}
        url = (web.get("uri") or web.get("url") or "").strip()
        if url and url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


# ── Grounded research (Pro + google_search) ───────────────────────────

async def grounded_research(
    prompt: str,
    key: str,
    timeout_s: float,
    *,
    label: str,
    websocket: Any = None,
    max_tokens: int = 6144,
    thinking_budget: int = 1024,
    temperature: float = 0.4,
) -> tuple[str, int, list[str]]:
    """Run one grounded Gemini call. Returns ``(text, source_count, urls)``.

    Telemetry only — never discards text just because grounding metadata
    is absent. If the grounded call returns no text (HTTP error / safety
    block / timeout), retries once without the tool so the caller still
    gets useful output.
    """
    payload_grounded = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
            "thinkingConfig": {"thinkingBudget": thinking_budget},
        },
        "tools": [{"google_search": {}}],
    }

    text, grounding = await post_gemini(
        RESEARCH_MODEL, payload_grounded, timeout_s,
        label=f"{label}_grounded", api_key=key,
    )
    sources = grounding_source_count(grounding)
    urls = grounding_urls(grounding)

    if text:
        if sources > 0:
            logger.info("gemini %s_grounded: web-grounded with %d sources", label, sources)
            if websocket is not None:
                try:
                    await websocket.send_json({
                        "type": "progress",
                        "message": f"🌐 {label}: grounded with {sources} real web sources",
                    })
                except Exception:
                    pass
        else:
            logger.info(
                "gemini %s_grounded: %d chars without groundingMetadata "
                "(model may have answered without searching — keeping output)",
                label, len(text),
            )
        return text, sources, urls

    # Fallback — drop the tool, try ungrounded.
    payload_plain = dict(payload_grounded)
    payload_plain.pop("tools", None)
    text2, _ = await post_gemini(
        RESEARCH_MODEL, payload_plain, timeout_s,
        label=f"{label}_plain", api_key=key,
    )
    return text2 or "", 0, []


# ── Structured distill (Flash + JSON) ─────────────────────────────────

async def structured_distill(
    prompt: str,
    key: str,
    timeout_s: float,
    *,
    label: str,
    response_schema: dict | None = None,
    max_tokens: int = 8192,
    temperature: float = 0.2,
) -> str:
    """Flash + JSON output, no tools. Returns raw JSON text (caller parses)."""
    generation_config: dict[str, Any] = {
        "temperature": temperature,
        "maxOutputTokens": max_tokens,
        "responseMimeType": "application/json",
        "thinkingConfig": {"thinkingBudget": 0},
    }
    if response_schema is not None:
        generation_config["responseSchema"] = response_schema

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": generation_config,
    }
    text, _ = await post_gemini(
        DISTILL_MODEL, payload, timeout_s, label=label, api_key=key,
    )
    return text
