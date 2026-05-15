"""A/B test: Gemini summarizer ON vs. raw-markdown bypass — PROMPT-CAPTURE MODE.

Test only the research + summarizer stages. Capture what WOULD be sent to
Claude in Stage 6 codegen. NO Anthropic calls. Pure input-quality analysis.

Pipeline:
  1. Research run ONCE, cached to /tmp/summarizer_ab/research_cache.json
  2. Version A — extract_research_signals + enrich_brief_with_signals
  3. Version B — skip summarizer; mechanically extract palette + typography
     from raw research; leave the rest of the brief empty
  4. For 3 representative sections (hero, about, footer) build the EXACT
     prompts the production pipeline would send to Claude. Write them to:
       /tmp/summarizer_ab/{A_summarized,B_bypassed}/prompts/{section}_{system,user}.txt
  5. Generate /tmp/summarizer_ab/report.md with:
       a) tiktoken token counts (total / research-brief / instructions)
       b) information density (specific terms vs generic AI phrases; ratio)
       c) structured-data presence (which brief fields are populated, samples)
       d) cost projection (Sonnet input $3/M, assumed 4K output @ $15/M)
       e) "What Claude would likely produce" — what's known vs missing
  6. Print summary + recommendation to console.

Run:
  docker exec lucid-ai-ai_engine-1 python /app/scripts/test_summarizer_bypass.py --yes
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, "/app")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger("summarizer_bypass")

_DEFAULT_PROMPT = (
    "A family-run Italian trattoria in Florence, three generations of "
    "Tuscan home cooking, fresh pasta made daily, intimate 30-seat room "
    "with terrace overlooking Piazza Santo Spirito."
)
_DEFAULT_OUT = "/tmp/summarizer_ab"

_CLASSIFICATION = {
    "type": "new",
    "project_type": "landing_page",
    "framework": "next.js",
    "language": "javascript",
}

# Sonnet pricing — $3/M input, $15/M output. Assumed 4K output per section.
_SONNET_IN_PER_M = 3.0
_SONNET_OUT_PER_M = 15.0
_ASSUMED_OUTPUT_TOKENS = 4000
_GEMINI_SUMMARIZER_COST = 0.001  # ~1 mil-cent for the 3-call summarizer chain


# ── Tokenizer ────────────────────────────────────────────────────────────

def _get_encoding():
    """Return a tiktoken encoding. Claude uses its own tokenizer; tiktoken's
    cl100k_base is a close proxy used for ballpark accounting."""
    import tiktoken
    try:
        return tiktoken.encoding_for_model("gpt-4")  # cl100k_base
    except Exception:
        return tiktoken.get_encoding("cl100k_base")


def _count_tokens(enc, text: str) -> int:
    if not text:
        return 0
    return len(enc.encode(text))


# ── Information-density vocabulary ───────────────────────────────────────

_GENERIC_PHRASES = [
    "modern", "premium", "sleek", "elegant", "world-class", "leverage",
    "innovative", "beautiful", "amazing", "cutting-edge", "seamless",
    "robust", "powerful", "delightful", "intuitive",
]

_HEX_RE = re.compile(r"#[0-9a-fA-F]{6}\b")
_HSL_RE = re.compile(r"\b\d{1,3}\s+\d{1,3}%\s+\d{1,3}%\b")
# Capitalized words (proper nouns / place names / brand names)
_PROPER_RE = re.compile(r"\b[A-Z][a-zà-ÿ]{3,}(?:[''][a-zà-ÿ]+)?\b")
_FONT_RE = re.compile(
    r"\b(?:Inter|Roboto|Helvetica|Arial|Georgia|Garamond|"
    r"Playfair(?:\s+Display)?|Cormorant(?:\s+Garamond)?|EB\s+Garamond|"
    r"Libre\s+Caslon|Libre\s+Bodoni|Bodoni|Lora|Merriweather|"
    r"Source\s+Serif|Source\s+Sans|DM\s+Serif|DM\s+Sans|"
    r"Manrope|Work\s+Sans|Nunito|Lato|Open\s+Sans|Poppins|Karla|"
    r"Crimson(?:\s+Pro|\s+Text)?|Spectral|Fraunces|Tinos|Cardo|"
    r"Italiana|Cinzel|Marcellus|Prata|Cormorant|Yeseva\s+One)\b"
)

_BOILERPLATE_DROP = {
    "The", "This", "That", "These", "Those", "And", "But", "For", "With",
    "From", "Into", "Their", "Your", "Many", "Some", "All", "Each", "Every",
    "When", "While", "Where", "What", "Which", "Who", "Why", "How",
    "Make", "Use", "Used", "Using", "Set", "Get", "Add", "Show", "Use",
    "Section", "Page", "Component", "Brand", "Color", "Font", "Image",
    "Output", "Input", "Render", "Return", "Like", "Such",
    "ONLY", "MUST", "DO", "NOT", "JSX", "HTML", "CSS", "Tailwind", "React",
}


def _count_density(text: str) -> dict:
    """Count specific vs generic markers in a prompt body."""
    if not text:
        return {
            "hex_codes": 0, "hsl_codes": 0, "font_names": 0,
            "proper_nouns": 0, "generic_hits": 0,
            "examples": {"hex": [], "hsl": [], "fonts": [], "proper": [], "generic": []},
        }

    hexes = _HEX_RE.findall(text)
    hsls = _HSL_RE.findall(text)
    fonts = _FONT_RE.findall(text)
    propers_raw = _PROPER_RE.findall(text)
    propers = [p for p in propers_raw if p not in _BOILERPLATE_DROP]

    lower = text.lower()
    generic_hits = 0
    generic_examples: list[str] = []
    for g in _GENERIC_PHRASES:
        c = len(re.findall(r"\b" + re.escape(g) + r"\b", lower))
        if c:
            generic_hits += c
            generic_examples.append(f"{g}×{c}")

    return {
        "hex_codes":   len(hexes),
        "hsl_codes":   len(hsls),
        "font_names":  len(set(fonts)),
        "proper_nouns": len(set(propers)),
        "generic_hits": generic_hits,
        "examples": {
            "hex":     list(dict.fromkeys(hexes))[:6],
            "hsl":     list(dict.fromkeys(hsls))[:6],
            "fonts":   list(dict.fromkeys(fonts))[:6],
            "proper":  list(dict.fromkeys(propers))[:10],
            "generic": generic_examples[:6],
        },
    }


def _specific_total(d: dict) -> int:
    return d["hex_codes"] + d["hsl_codes"] + d["font_names"] + d["proper_nouns"]


# ── Bypass injection — raw markdown block ────────────────────────────────

def _format_raw_markdown_block(domain_research: dict, design_research: dict) -> str:
    """Concatenate all 8 grounded markdown dumps into one block."""
    sections: list[str] = ["===RAW_RESEARCH_BLOCK (summarizer bypassed) ==="]

    def _add(label: str, text: str) -> None:
        if not text:
            return
        sections.append(f"\n## {label}\n")
        sections.append(text.strip())

    for k in ("business", "audience", "regional", "competitive"):
        _add(f"DOMAIN / {k.upper()}", (domain_research.get(k) or {}).get("text", ""))
    for k in ("visual", "typography", "color", "layout"):
        _add(f"DESIGN / {k.upper()}", (design_research.get(k) or {}).get("text", ""))

    sections.append("\n===END_RAW_RESEARCH_BLOCK===\n")
    return "\n".join(sections)


# ── Mechanical palette/typography extraction from raw research ───────────

def _extract_palette_from_raw(design_research: dict) -> dict:
    """Pull HSL or hex codes from the color research markdown, build a
    minimal palette dict with the slots the production system expects.
    """
    color_text = (design_research.get("color") or {}).get("text", "") or ""
    visual_text = (design_research.get("visual") or {}).get("text", "") or ""
    combined = color_text + "\n" + visual_text

    hsls = _HSL_RE.findall(combined)
    hexes = _HEX_RE.findall(combined)

    palette: dict[str, str] = {}
    slots = ["primary", "secondary", "accent", "background", "foreground",
             "muted", "card", "border"]

    pool: list[str] = []
    for h in hsls:
        pool.append(h.strip())
    for hx in hexes:
        # Convert hex to a rough HSL-ish string — keep hex as a string
        # since the brief structure accepts arbitrary CSS-color strings.
        pool.append(hx)

    seen: set[str] = set()
    for slot in slots:
        # Take the next unseen value
        for v in pool:
            if v not in seen:
                palette[slot] = v
                seen.add(v)
                break

    return palette


def _extract_typography_from_raw(design_research: dict) -> dict:
    """Pull font names from typography research."""
    typ_text = (design_research.get("typography") or {}).get("text", "") or ""
    visual_text = (design_research.get("visual") or {}).get("text", "") or ""
    combined = typ_text + "\n" + visual_text
    fonts = _FONT_RE.findall(combined)
    # De-dupe preserving order
    fonts = list(dict.fromkeys(fonts))
    typography: dict[str, str] = {}
    if fonts:
        typography["heading"] = fonts[0]
    if len(fonts) > 1:
        typography["body"] = fonts[1]
    elif fonts:
        typography["body"] = fonts[0]
    return typography


# ── Section selection ───────────────────────────────────────────────────

def _pick_three_sections(brief: dict) -> list[tuple[str, dict]]:
    """Return [(label, section_dict), ...] for hero/about/footer.

    Footer is filtered out of brief.sections in production codegen (it's a
    layout component), so we synthesize a footer section dict here.
    """
    sections = brief.get("sections") or []
    out: list[tuple[str, dict]] = []

    # hero — first section of type 'hero', else first section
    hero = next((s for s in sections if (s.get("type") or "").lower() == "hero"), None)
    if not hero and sections:
        hero = sections[0]
    if not hero:
        hero = {"id": "hero", "type": "hero", "headline": "Welcome", "layout_hint": "centered"}
    out.append(("hero", hero))

    # about — first section that smells "about"
    about_types = {"about", "story", "value_prop", "philosophy", "features"}
    about = next(
        (s for s in sections if (s.get("type") or "").lower() in about_types),
        None,
    )
    if not about:
        # Pick the second-non-hero if present
        non_hero = [s for s in sections if s is not hero]
        about = non_hero[0] if non_hero else {
            "id": "about", "type": "about",
            "headline": "Our story", "purpose": "Brand story",
        }
    out.append(("about", about))

    # footer — synthesize, since production filters it
    footer = {
        "id":          "footer",
        "type":        "footer",
        "layout_hint": "split_columns",
        "purpose":     "Site footer with navigation, social, and contact info",
        "headline":    "",
    }
    out.append(("footer", footer))

    return out


# ── Cached research bootstrap ────────────────────────────────────────────

async def _build_or_load_research(
    *,
    prompt: str,
    cache_path: str,
    force_fresh: bool,
) -> tuple[dict, dict, dict, dict]:
    """Return (intent, domain_research, design_research, legacy_brief)."""
    if not force_fresh and os.path.exists(cache_path):
        logger.info("Loading cached research bundle from %s", cache_path)
        with open(cache_path, "r", encoding="utf-8") as f:
            cache = json.load(f)
        return (
            cache["intent"],
            cache["domain_research"],
            cache["design_research"],
            cache["legacy_brief"],
        )

    logger.info("Running fresh research stage for prompt: %r", prompt[:80])
    from app.services.landing_intent import analyze_intent
    from app.services.landing_domain_research import run_domain_research
    from app.services.landing_design_research import run_design_research
    from app.services.landing_brief import build_landing_brief

    intent = await analyze_intent(prompt, _CLASSIFICATION, websocket=None, timeout_s=60.0)
    if not intent:
        raise RuntimeError("analyze_intent returned None — cannot proceed")
    if intent.get("clarity_level") == "low" and (intent.get("clarification_questions") or []):
        logger.warning("intent clarity=low — overriding to medium for the test")
        intent["clarity_level"] = "medium"

    domain_research, design_research, legacy_brief = await asyncio.gather(
        run_domain_research(intent, websocket=None, timeout_s=240.0),
        run_design_research(intent, websocket=None, timeout_s=240.0),
        build_landing_brief(prompt, _CLASSIFICATION, websocket=None),
    )

    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump({
            "intent":          intent,
            "domain_research": domain_research,
            "design_research": design_research,
            "legacy_brief":    legacy_brief,
        }, f, indent=2, ensure_ascii=False)
    logger.info("Saved research cache → %s", cache_path)
    return intent, domain_research, design_research, legacy_brief


# ── Prompt capture for one version × one section ─────────────────────────

def _capture_prompts_for_version(
    *,
    brief: dict,
    chosen: list[tuple[str, dict]],
    out_dir: Path,
    raw_block_suffix: str = "",
) -> dict[str, dict]:
    """Build the exact prompts production codegen would send to Claude.

    Returns {section_label: {"system": str, "user": str, ...}}.
    """
    from app.services.landing_section_codegen import (
        _system_prompt,
        _user_prompt,
        _section_filename,
        _component_name,
        _pick_siblings,
    )
    from app.services.landing_brief import _build_design_tokens

    prompts_dir = out_dir / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)

    sections_all = brief.get("sections") or []
    brand_name = (brief.get("brand") or {}).get("name", "")
    motif = (brief.get("motif") or "minimal").strip().lower()
    palette = dict(brief.get("palette") or {})
    typography = dict(brief.get("typography") or {})
    design_system = dict(brief.get("design_system") or {})
    personality = dict(brief.get("personality") or {})
    references = list(brief.get("references") or [])
    design_tokens = dict(brief.get("design_tokens") or {}) or _build_design_tokens(design_system)
    visual_dna = dict(brief.get("visual_dna") or {})
    voice_context = {
        "voice_phrases":     brief.get("voice_phrases") or [],
        "industry_terms":    brief.get("industry_terms") or [],
        "regional_refs":     brief.get("regional_refs") or [],
        "white_space":       brief.get("white_space") or [],
        "purpose_directive": brief.get("purpose_directive") or "",
    }

    captured: dict[str, dict] = {}

    for label, section in chosen:
        # Find index in brief.sections if present; otherwise treat as new tail.
        try:
            idx = sections_all.index(section)
        except ValueError:
            idx = len(sections_all)
        siblings = _pick_siblings(sections_all, idx) if sections_all else []
        filename = _section_filename(section)
        component = _component_name(filename)
        file_path = f"src/components/sections/{filename}"

        sys_p = _system_prompt(
            brand_name, motif, palette, typography, design_system,
            personality=personality, references=references,
            design_tokens=design_tokens,
            visual_dna=visual_dna,
        )
        usr_p = _user_prompt(
            section, siblings, component, file_path,
            section_index=idx,
            section_count=max(len(sections_all), idx + 1),
            voice_context=voice_context,
            visual_dna=visual_dna,
        )

        if raw_block_suffix:
            sys_p = sys_p + "\n\n" + raw_block_suffix

        (prompts_dir / f"{label}_system.txt").write_text(sys_p, encoding="utf-8")
        (prompts_dir / f"{label}_user.txt").write_text(usr_p, encoding="utf-8")

        captured[label] = {
            "system":    sys_p,
            "user":      usr_p,
            "component": component,
            "file_path": file_path,
        }

    return captured


# ── Structured data presence checks ──────────────────────────────────────

def _structured_presence(brief: dict) -> dict:
    """Return a snapshot of which fields the brief populates + samples."""
    palette = brief.get("palette") or {}
    typography = brief.get("typography") or {}
    voice_phrases = brief.get("voice_phrases") or []
    industry_terms = brief.get("industry_terms") or []
    regional_refs = brief.get("regional_refs") or []
    visual_dna = brief.get("visual_dna") or {}
    anatomies = (visual_dna or {}).get("section_anatomies") or {}
    motifs = (visual_dna or {}).get("decorative_motifs") or []

    def _sample(v, n=3):
        if isinstance(v, dict):
            return dict(list(v.items())[:n])
        if isinstance(v, list):
            return v[:n]
        return v

    return {
        "palette_filled":        bool(palette),
        "palette_slots":         len(palette),
        "palette_sample":        _sample(palette, 4),
        "typography_filled":     bool(typography),
        "typography_sample":     _sample(typography, 4),
        "voice_phrases_count":   len(voice_phrases),
        "voice_phrases_sample":  _sample(voice_phrases, 3),
        "industry_terms_count":  len(industry_terms),
        "industry_terms_sample": _sample(industry_terms, 4),
        "regional_refs_count":   len(regional_refs),
        "regional_refs_sample":  [
            r.get("name", "") if isinstance(r, dict) else r
            for r in regional_refs[:4]
        ],
        "visual_dna_filled":     bool(visual_dna),
        "section_anatomies":     sorted(list(anatomies.keys())),
        "motifs":                motifs[:6],
        "cultural_intensity":    visual_dna.get("cultural_intensity") or "",
    }


# ── Report builder ───────────────────────────────────────────────────────

def _build_report_md(
    *,
    prompt_text: str,
    research_meta: dict,
    versions: dict[str, dict],
    intent_check: dict | None = None,
    coherence: dict | None = None,
    design_status: dict | None = None,
) -> str:
    """versions = {
        "A_summarized": {
            "captured": {hero: {system, user}, ...},
            "token_counts": {...},
            "density": {...},
            "presence": {...},
            "summarizer_wall_s": float,
        },
        "B_bypassed": {...},
    }
    """
    lines: list[str] = []
    lines.append("# Summarizer Bypass A/B — Prompt-Capture Report")
    lines.append("")
    lines.append(f"**Test prompt:** {prompt_text}")
    lines.append("")
    lines.append("**Mode:** prompt capture only — NO Anthropic calls made.")
    lines.append("")
    lines.append("## 0. Research stage (shared input)")
    lines.append("")
    lines.append(f"- Cached research: `{research_meta['cache_path']}`")
    lines.append(f"- Domain markdown total size: {research_meta['domain_bytes']:,} chars")
    lines.append(f"- Design markdown total size: {research_meta['design_bytes']:,} chars")
    lines.append(f"- Summarizer wall time (A only): {research_meta['summarizer_wall_s']}s")
    lines.append("")

    # ── 0a. Intent grounding ────────────────────────────────────────────
    if intent_check is not None:
        lines.append("## 0a. Intent grounding (NEW — post ADC fix)")
        lines.append("")
        ok = intent_check.get("ok", False)
        is_fb = intent_check.get("is_fallback", False)
        lines.append(f"- **Result:** {'✓ PASS' if ok else '✗ FAIL'}")
        lines.append(f"- is_fallback: `{is_fb}`")
        d = intent_check.get("details", {})
        lines.append(f"- business_category: `{d.get('business_category')!r}`")
        lines.append(f"- business_subcategory: `{d.get('business_subcategory')!r}`")
        lines.append(f"- primary_purpose: `{d.get('primary_purpose')!r}`")
        lines.append(f"- geographic_scope: `{d.get('geographic_scope')!r}`")
        lines.append(f"- geographic_specifics: `{d.get('geographic_specifics')!r}`")
        lines.append(f"- tone: `{d.get('tone')!r}`")
        lines.append(f"- brand_personality: `{d.get('brand_personality')!r}`")
        ta = d.get("target_audience") or {}
        if isinstance(ta, dict):
            lines.append(f"- target_audience.primary: `{ta.get('primary')!r}`")
        if intent_check.get("reasons"):
            lines.append("")
            lines.append("**Issues:**")
            for r in intent_check["reasons"]:
                lines.append(f"- {r}")
        lines.append("")

    # ── 0b. Domain coherence (side-by-side with previous broken run) ────
    if coherence is not None:
        lines.append("## 0b. Domain coherence (NEW — Version A vs prior broken run)")
        lines.append("")
        lines.append(
            "Scans the summarizer's Version-A `signals.domain` (voice_phrases, "
            "industry_terms, regional_touchpoints) for known SaaS contamination "
            "signals seen in the **pre-fix run**, plus expected-vocab presence."
        )
        lines.append("")
        lines.append("| Metric | Prior broken run | Current run |")
        lines.append("|---|---|---|")
        lines.append(
            "| voice_phrases sample | `people cancel when they feel trapped...`, "
            "`pay once and be done with it` (SaaS subscription talk) | "
            f"{coherence['samples']['voice_phrases']} |"
        )
        lines.append(
            "| industry_terms sample | `Gross Profit Margin`, `Customer Acquisition Cost`, "
            "`Customer Lifetime Value`, `Cash Runway` (SaaS finance) | "
            f"{coherence['samples']['industry_terms']} |"
        )
        lines.append(
            "| regional_refs sample | `Atlantic Avenue`, `DUMBO's cobblestone streets`, "
            "`Park Slope brownstones`, `Red Hook's maritime isolation` (Brooklyn — but "
            "for a Florence prompt!) | "
            f"{coherence['samples']['regional_refs']} |"
        )
        lines.append(
            f"| SaaS contamination hits | many (churn / CAC / Cash Runway / Customer Lifetime Value) | "
            f"{coherence['saas_count']} {coherence['saas_hits']} |"
        )
        lines.append(
            f"| Expected geo vocab hits | 0 (the prompt was Florence but signals returned Brooklyn) | "
            f"{coherence['geo_count']} (sample: {coherence['geo_hits'][:6]}) |"
        )
        lines.append(
            f"| Expected topic vocab hits | ~0 (no restaurant terms — all SaaS talk) | "
            f"{coherence['topic_count']} (sample: {coherence['topic_hits'][:6]}) |"
        )
        if coherence.get("off_geo_hits"):
            lines.append(
                f"| Off-geo references (Florence-style for Brooklyn) | n/a | "
                f"{coherence['off_geo_count']} {coherence['off_geo_hits']} |"
            )
        lines.append("")
        lines.append(f"**Coherence verdict:** {'✓ PASS — domain-coherent' if coherence['ok'] else '✗ FAIL — see issues below'}")
        if coherence.get("reasons"):
            for r in coherence["reasons"]:
                lines.append(f"- {r}")
        lines.append("")

    # ── 0c. Design summarizer status ────────────────────────────────────
    if design_status is not None:
        lines.append("## 0c. Design summarizer status (NEW)")
        lines.append("")
        lines.append(
            "The design-Flash call previously returned empty (`finishReason=MAX_TOKENS`), "
            "leaving Version A's brief with no design-signals payload. This section "
            "tracks whether that's still the case."
        )
        lines.append("")
        lines.append(f"- design_keys returned: **{design_status['design_keys']}**")
        lines.append(f"- empty (MAX_TOKENS / blocked / failed): **{design_status['empty']}**")
        lines.append(f"- palette_filled: {design_status['palette_filled']}")
        lines.append(f"- typography_filled: {design_status['typography_filled']}")
        lines.append(f"- visual_dna_filled: {design_status['visual_dna_filled']}")
        lines.append(f"- non-boilerplate hex codes in palette: {design_status['non_boilerplate_hexes']}")
        lines.append(f"- HSL codes in palette: {design_status['hsl_codes']}")
        lines.append(f"- chosen_palette sample: `{design_status['chosen_palette_sample']}`")
        lines.append(f"- chosen_typography sample: `{design_status['chosen_typo_sample']}`")
        if design_status["empty"]:
            lines.append("")
            lines.append("**⚠️ Design summarizer is still empty — this is a separate unfixed bug.** "
                         "Visual DNA may still be coming through via the Pro call, but the Flash "
                         "design-signals call needs investigation.")
        elif not design_status["non_boilerplate_hexes"] and not design_status["hsl_codes"]:
            lines.append("")
            lines.append("**⚠️ Palette only contains the `#2563eb` boilerplate — no real hex codes.** "
                         "Color signal is degenerate even though `design_keys > 0`.")
        else:
            lines.append("")
            lines.append("**✓ Design summarizer returned real color/typography data.**")
        lines.append("")

    # ── Per-section token counts ────────────────────────────────────────
    lines.append("## 1. Token counts (tiktoken `gpt-4` / cl100k_base proxy for Sonnet)")
    lines.append("")
    lines.append("Each version × section captures the EXACT system + user prompt the production"
                 " pipeline would send. Token counts use cl100k_base as a Sonnet proxy.")
    lines.append("")

    for label in ("hero", "about", "footer"):
        lines.append(f"### {label}")
        lines.append("")
        lines.append("| Metric | A (summarized) | B (bypassed) | Δ (B − A) |")
        lines.append("|---|---:|---:|---:|")
        a_tc = versions["A_summarized"]["token_counts"][label]
        b_tc = versions["B_bypassed"]["token_counts"][label]
        for metric in ("system_tokens", "user_tokens", "total_tokens",
                       "research_brief_tokens", "instructions_tokens"):
            a_v = a_tc[metric]
            b_v = b_tc[metric]
            delta = b_v - a_v
            lines.append(f"| {metric.replace('_', ' ')} | {a_v:,} | {b_v:,} | {delta:+,} |")
        lines.append("")

    # ── Information density ─────────────────────────────────────────────
    lines.append("## 2. Information density (specifics vs generic AI phrases)")
    lines.append("")
    lines.append("Specifics: hex codes, HSL triples, font names, proper nouns (filtered).")
    lines.append("Generics: " + ", ".join(_GENERIC_PHRASES))
    lines.append("")

    for label in ("hero", "about", "footer"):
        lines.append(f"### {label}")
        lines.append("")
        lines.append("| Metric | A (summarized) | B (bypassed) |")
        lines.append("|---|---:|---:|")
        a_d = versions["A_summarized"]["density"][label]
        b_d = versions["B_bypassed"]["density"][label]
        for k in ("hex_codes", "hsl_codes", "font_names", "proper_nouns", "generic_hits"):
            lines.append(f"| {k.replace('_', ' ')} | {a_d[k]} | {b_d[k]} |")
        a_spec = _specific_total(a_d)
        b_spec = _specific_total(b_d)
        a_ratio = (a_spec / a_d["generic_hits"]) if a_d["generic_hits"] else float("inf")
        b_ratio = (b_spec / b_d["generic_hits"]) if b_d["generic_hits"] else float("inf")
        a_ratio_s = f"{a_ratio:.2f}" if a_ratio != float("inf") else "∞"
        b_ratio_s = f"{b_ratio:.2f}" if b_ratio != float("inf") else "∞"
        lines.append(f"| **specifics total** | **{a_spec}** | **{b_spec}** |")
        lines.append(f"| **specifics:generics ratio** | **{a_ratio_s}** | **{b_ratio_s}** |")
        lines.append("")
        lines.append(f"  - A example specifics: hex={a_d['examples']['hex']}, "
                     f"fonts={a_d['examples']['fonts']}, proper={a_d['examples']['proper'][:6]}")
        lines.append(f"  - B example specifics: hex={b_d['examples']['hex']}, "
                     f"fonts={b_d['examples']['fonts']}, proper={b_d['examples']['proper'][:6]}")
        lines.append(f"  - A generic hits: {a_d['examples']['generic']}")
        lines.append(f"  - B generic hits: {b_d['examples']['generic']}")
        lines.append("")

    # ── Structured data presence ────────────────────────────────────────
    lines.append("## 3. Structured-data presence in the brief")
    lines.append("")
    lines.append("These are the fields the production codegen reads off the brief. Empty = Claude must invent.")
    lines.append("")
    lines.append("| Field | A (summarized) | B (bypassed) |")
    lines.append("|---|---|---|")
    pa = versions["A_summarized"]["presence"]
    pb = versions["B_bypassed"]["presence"]

    def _bool(b: bool) -> str:
        return "✓" if b else "✗"

    rows = [
        ("palette filled",          _bool(pa['palette_filled']) + f" ({pa['palette_slots']} slots)",
                                    _bool(pb['palette_filled']) + f" ({pb['palette_slots']} slots)"),
        ("palette sample",          str(pa['palette_sample']),  str(pb['palette_sample'])),
        ("typography filled",       _bool(pa['typography_filled']), _bool(pb['typography_filled'])),
        ("typography sample",       str(pa['typography_sample']), str(pb['typography_sample'])),
        ("voice_phrases",           str(pa['voice_phrases_count']) + f" — {pa['voice_phrases_sample']}",
                                    str(pb['voice_phrases_count']) + f" — {pb['voice_phrases_sample']}"),
        ("industry_terms",          str(pa['industry_terms_count']) + f" — {pa['industry_terms_sample']}",
                                    str(pb['industry_terms_count']) + f" — {pb['industry_terms_sample']}"),
        ("regional_refs",           str(pa['regional_refs_count']) + f" — {pa['regional_refs_sample']}",
                                    str(pb['regional_refs_count']) + f" — {pb['regional_refs_sample']}"),
        ("visual_dna filled",       _bool(pa['visual_dna_filled']),  _bool(pb['visual_dna_filled'])),
        ("section_anatomies",       f"{len(pa['section_anatomies'])} — {pa['section_anatomies'][:6]}",
                                    f"{len(pb['section_anatomies'])} — {pb['section_anatomies'][:6]}"),
        ("decorative motifs",       str(pa['motifs']),  str(pb['motifs'])),
        ("cultural_intensity",      pa['cultural_intensity'] or "—",
                                    pb['cultural_intensity'] or "—"),
    ]
    for r in rows:
        lines.append(f"| {r[0]} | {r[1]} | {r[2]} |")
    lines.append("")

    # ── Cost projection ─────────────────────────────────────────────────
    lines.append("## 4. Cost projection (per section, Sonnet pricing)")
    lines.append("")
    lines.append(f"Input ${_SONNET_IN_PER_M}/M, output ${_SONNET_OUT_PER_M}/M, "
                 f"assumed {_ASSUMED_OUTPUT_TOKENS} output tokens per section.")
    lines.append(f"Gemini summarizer cost (one-shot, amortized over all sections): "
                 f"~${_GEMINI_SUMMARIZER_COST:.4f}")
    lines.append("")
    lines.append("| Section | A total tokens | A $ | B total tokens | B $ | Δ $ |")
    lines.append("|---|---:|---:|---:|---:|---:|")

    a_cost_total = 0.0
    b_cost_total = 0.0
    for label in ("hero", "about", "footer"):
        a_in = versions["A_summarized"]["token_counts"][label]["total_tokens"]
        b_in = versions["B_bypassed"]["token_counts"][label]["total_tokens"]
        a_cost = (a_in / 1_000_000.0) * _SONNET_IN_PER_M + \
                 (_ASSUMED_OUTPUT_TOKENS / 1_000_000.0) * _SONNET_OUT_PER_M
        b_cost = (b_in / 1_000_000.0) * _SONNET_IN_PER_M + \
                 (_ASSUMED_OUTPUT_TOKENS / 1_000_000.0) * _SONNET_OUT_PER_M
        a_cost_total += a_cost
        b_cost_total += b_cost
        lines.append(f"| {label} | {a_in:,} | ${a_cost:.4f} | {b_in:,} | ${b_cost:.4f} | ${b_cost - a_cost:+.4f} |")

    lines.append(f"| **TOTAL (3 sections)** | — | **${a_cost_total:.4f}** | — | **${b_cost_total:.4f}** | **${b_cost_total - a_cost_total:+.4f}** |")
    lines.append("")
    lines.append(f"With summarizer: ${a_cost_total:.4f} + ${_GEMINI_SUMMARIZER_COST:.4f} (Gemini) "
                 f"= **${a_cost_total + _GEMINI_SUMMARIZER_COST:.4f}**")
    lines.append(f"Without summarizer: **${b_cost_total:.4f}**")
    net = (a_cost_total + _GEMINI_SUMMARIZER_COST) - b_cost_total
    lines.append(f"Net difference (summarizer − bypass): **${net:+.4f}** per generation "
                 f"({'summarizer cheaper' if net < 0 else 'bypass cheaper'})")
    lines.append("")

    # ── What Claude would likely produce ────────────────────────────────
    lines.append("## 5. What Claude would likely produce")
    lines.append("")
    lines.append("This section reads each version's brief and reports concrete vs missing fields. "
                 "We do not simulate Claude — we just enumerate what input quality is.")
    lines.append("")

    def _audit(presence: dict, label: str) -> list[str]:
        has: list[str] = []
        miss: list[str] = []
        invent: list[str] = []
        if presence['palette_filled']:
            has.append(f"palette with {presence['palette_slots']} slots: {presence['palette_sample']}")
        else:
            miss.append("palette (Claude falls back to default HSL token slots)")
            invent.append("color choices from style memory — no cultural fit signal")
        if presence['typography_filled']:
            has.append(f"typography pair: {presence['typography_sample']}")
        else:
            miss.append("typography (Claude picks generic system fonts)")
            invent.append("font pairings from priors — usually Inter/Playfair-ish defaults")
        if presence['voice_phrases_count']:
            has.append(f"{presence['voice_phrases_count']} voice phrases (e.g. {presence['voice_phrases_sample']})")
        else:
            miss.append("voice_phrases (audience-specific copy register)")
            invent.append("copy tone defaults to marketing-pop — premium/innovative/elegant")
        if presence['industry_terms_count']:
            has.append(f"{presence['industry_terms_count']} industry terms (e.g. {presence['industry_terms_sample']})")
        else:
            miss.append("industry_terms (domain-specific nouns)")
            invent.append("domain language from training data — risk of stale/generic terms")
        if presence['regional_refs_count']:
            has.append(f"{presence['regional_refs_count']} regional refs (e.g. {presence['regional_refs_sample']})")
        else:
            miss.append("regional_refs (place-grounded touchpoints)")
            invent.append("location flavor missing — no Piazza Santo Spirito, no Via Maggio, etc.")
        if presence['visual_dna_filled']:
            has.append(f"visual_dna: intensity={presence['cultural_intensity']}, motifs={presence['motifs']}")
            has.append(f"section_anatomies populated for: {presence['section_anatomies']}")
        else:
            miss.append("visual_dna (cultural intensity, motifs, textures)")
            miss.append("section_anatomies (per-section layout DNA)")
            invent.append("section layouts from generic 'modern luxe' fallback skeleton")
            invent.append("decorative motifs absent — no cultural specificity")

        out: list[str] = [f"#### {label}", ""]
        out.append("**What Claude HAS:**")
        if has:
            out.extend(f"- {h}" for h in has)
        else:
            out.append("- (nothing structured)")
        out.append("")
        out.append("**What Claude is MISSING:**")
        if miss:
            out.extend(f"- {m}" for m in miss)
        else:
            out.append("- (nothing — all fields populated)")
        out.append("")
        out.append("**What Claude must invent or fall back on:**")
        if invent:
            out.extend(f"- {i}" for i in invent)
        else:
            out.append("- (nothing — input is fully grounded)")
        out.append("")
        return out

    lines.extend(_audit(pa, "Version A — summarizer ON"))
    lines.extend(_audit(pb, "Version B — summarizer BYPASSED"))

    # ── Recommendation ──────────────────────────────────────────────────
    lines.append("## 6. Recommendation")
    lines.append("")

    # Heuristic: count meaningful brief fields each side fills
    def _fill_score(p: dict) -> int:
        return (
            (1 if p['palette_filled'] else 0)
            + (1 if p['typography_filled'] else 0)
            + (1 if p['voice_phrases_count'] else 0)
            + (1 if p['industry_terms_count'] else 0)
            + (1 if p['regional_refs_count'] else 0)
            + (1 if p['visual_dna_filled'] else 0)
            + (1 if p['section_anatomies'] else 0)
        )

    a_fill = _fill_score(pa)
    b_fill = _fill_score(pb)

    # Density verdict — sum across 3 sections
    a_spec_total = sum(_specific_total(versions["A_summarized"]["density"][s])
                       for s in ("hero", "about", "footer"))
    b_spec_total = sum(_specific_total(versions["B_bypassed"]["density"][s])
                       for s in ("hero", "about", "footer"))
    a_gen_total = sum(versions["A_summarized"]["density"][s]["generic_hits"]
                      for s in ("hero", "about", "footer"))
    b_gen_total = sum(versions["B_bypassed"]["density"][s]["generic_hits"]
                      for s in ("hero", "about", "footer"))

    coherent = bool(coherence and coherence.get("ok"))
    saas_hits = (coherence or {}).get("saas_count", 0)
    design_empty = bool(design_status and design_status.get("empty"))

    lines.append(f"- Domain coherent (A): **{'YES' if coherent else 'NO'}**"
                 + (f"  (SaaS contamination hits: {saas_hits})" if saas_hits else ""))
    lines.append(f"- Structured brief cheaper than raw-research dump: "
                 f"**{'YES' if (a_cost_total + _GEMINI_SUMMARIZER_COST) < b_cost_total else 'NO'}**")
    lines.append(f"- Version A content matches the prompt: "
                 f"**{'YES' if coherent and not design_empty else 'PARTIAL' if coherent else 'NO'}**")
    lines.append(f"- Brief fill score: A={a_fill}/7, B={b_fill}/7")
    lines.append(f"- Total specifics across 3 prompts: A={a_spec_total}, B={b_spec_total}")
    lines.append(f"- Total generics across 3 prompts: A={a_gen_total}, B={b_gen_total}")
    lines.append(f"- Per-generation cost: A=${a_cost_total + _GEMINI_SUMMARIZER_COST:.4f}, "
                 f"B=${b_cost_total:.4f}")
    lines.append("")

    # Verdict is gated on coherence FIRST — a cheaper-but-poisoned brief is
    # worse than a more expensive raw-research dump.
    if not coherent:
        verdict = "FIX the summarizer (still contaminated)"
        rationale = (
            "Version A's brief is not domain-coherent — the summarizer's domain Flash "
            "call is leaking SaaS / wrong-geo terms. Token counts and cost are moot "
            "until the grounding is fixed. Likely fix: tighten the domain-signals "
            "prompt so the Flash call cannot ignore the raw research and free-associate "
            "from priors."
        )
    elif design_empty:
        verdict = "IMPROVE the summarizer (design half is missing)"
        rationale = (
            "Domain side is grounded now, but the design-signals Flash call still "
            "returns empty (likely MAX_TOKENS). Visual DNA is still populated by the "
            "Pro call, so the brief isn't unusable, but the chosen_palette / "
            "chosen_typography are absent. Tune maxOutputTokens on the design call "
            "or split it into two smaller calls."
        )
    elif a_fill - b_fill >= 3 and a_spec_total >= b_spec_total:
        verdict = "KEEP the summarizer"
        rationale = (
            f"A fills {a_fill - b_fill} more structured brief fields than B, while delivering "
            f"at least as many specifics ({a_spec_total} vs {b_spec_total}) into the prompt. "
            "The summarizer is converting raw markdown into the structured signal Claude "
            "actually reads — without it, codegen falls back to generic 'modern luxe' defaults."
        )
    elif a_fill - b_fill >= 3 and a_spec_total < b_spec_total:
        verdict = "IMPROVE the summarizer"
        rationale = (
            f"A fills more brief fields ({a_fill} vs {b_fill}) but B's raw markdown actually "
            f"contains more specifics ({b_spec_total} vs {a_spec_total}). The summarizer is "
            "leaving specifics on the table — tune it to capture more proper nouns / hex codes."
        )
    elif a_fill <= b_fill:
        verdict = "REMOVE the summarizer"
        rationale = (
            f"A does not meaningfully out-fill B ({a_fill} vs {b_fill}) and the bypass route "
            "preserves more raw specifics. The summarizer call is taxing latency + tokens "
            "without producing a richer signal."
        )
    else:
        verdict = "MIXED — inspect side-by-side"
        rationale = (
            "Signal is mixed across density and brief-fill axes. Inspect the captured prompts "
            "in /tmp/summarizer_ab/{A,B}/prompts/ before deciding."
        )

    lines.append(f"**Verdict: {verdict}**")
    lines.append("")
    lines.append(rationale)
    lines.append("")
    return "\n".join(lines), verdict, {
        "a_fill": a_fill, "b_fill": b_fill,
        "a_spec_total": a_spec_total, "b_spec_total": b_spec_total,
        "a_gen_total": a_gen_total, "b_gen_total": b_gen_total,
        "a_cost_total": a_cost_total, "b_cost_total": b_cost_total,
    }


# ── Token-count helpers ─────────────────────────────────────────────────

# Boundary markers we use to split the system prompt into research-vs-instructions.
_RESEARCH_MARKERS = (
    "PALETTE", "VISUAL DNA", "VISUAL_DNA", "VOICE", "REGIONAL", "INDUSTRY",
    "PERSONALITY", "REFERENCES", "===RAW_RESEARCH_BLOCK",
)


def _split_research_vs_instructions(enc, prompt: str) -> tuple[int, int]:
    """Best-effort split: lines that look like data/research vs instructional prose.

    We classify each line as 'research' if it contains a brief-field marker
    OR is a structured-data shape (hex code, HSL triple, list bullet of
    proper nouns, JSON-ish). Everything else is treated as instructions.
    """
    if not prompt:
        return 0, 0
    research_chunks: list[str] = []
    instr_chunks: list[str] = []
    in_research_block = False
    for line in prompt.splitlines(keepends=True):
        stripped = line.strip()
        # Sticky region for explicit research blocks
        if "===RAW_RESEARCH_BLOCK" in stripped:
            in_research_block = True
        if in_research_block:
            research_chunks.append(line)
            if "===END_RAW_RESEARCH_BLOCK" in stripped:
                in_research_block = False
            continue

        is_research = False
        upper = stripped.upper()
        if any(m in upper for m in _RESEARCH_MARKERS):
            is_research = True
        elif _HEX_RE.search(line) or _HSL_RE.search(line):
            is_research = True
        elif stripped.startswith(("- ", "* ", "• ")) and len(_PROPER_RE.findall(line)) >= 1:
            is_research = True
        elif stripped.startswith("--") and ":" in stripped:
            # CSS-var style palette line: "  --primary: hsl(...);"
            is_research = True

        if is_research:
            research_chunks.append(line)
        else:
            instr_chunks.append(line)

    return _count_tokens(enc, "".join(research_chunks)), _count_tokens(enc, "".join(instr_chunks))


# ── NEW: Intent grounding check ──────────────────────────────────────────

def _intent_grounding_check(intent: dict, prompt: str) -> dict:
    """Compare an intent dict against landing_intent._fallback_intent to
    detect the SaaS-fallback contamination we saw before the ADC fix.

    Returns {"ok": bool, "is_fallback": bool, "reasons": [str, ...], "details": {...}}.
    """
    from app.services.landing_intent import _fallback_intent

    fb = _fallback_intent(prompt, "general")
    reasons: list[str] = []

    is_fb = (
        intent.get("business_category") == fb.get("business_category")
        and intent.get("primary_purpose") == fb.get("primary_purpose")
        and intent.get("clarity_level") == fb.get("clarity_level")
    )
    if is_fb:
        reasons.append("intent matches _fallback_intent() shape — Gemini intent call failed")

    cat = (intent.get("business_category") or "").strip().lower()
    if not cat or cat in {"", "general", "general business"}:
        reasons.append(f"business_category is empty/generic ({cat!r})")

    geo_spec = (intent.get("geographic_specifics") or "").strip()
    geo_scope = (intent.get("geographic_scope") or "").strip().lower()
    if not geo_spec:
        reasons.append("geographic_specifics is empty")

    return {
        "ok":          not is_fb and not reasons,
        "is_fallback": is_fb,
        "reasons":     reasons,
        "details": {
            "business_category":    intent.get("business_category"),
            "business_subcategory": intent.get("business_subcategory"),
            "primary_purpose":      intent.get("primary_purpose"),
            "geographic_scope":     geo_scope,
            "geographic_specifics": geo_spec,
            "tone":                 intent.get("tone"),
            "brand_personality":    intent.get("brand_personality"),
            "clarity_level":        intent.get("clarity_level"),
            "target_audience":      intent.get("target_audience"),
        },
    }


# ── NEW: Domain coherence check ─────────────────────────────────────────

# Known SaaS-contamination signals we saw in the pre-fix run.
_SAAS_CONTAMINATION_TERMS = [
    "churn", "cac", "ltv", "customer lifetime value", "customer acquisition cost",
    "cash runway", "arr", "mrr", "subscription", "saas", "gross profit margin",
    "burn rate", "freemium", "trial", "pricing tier", "feature flag",
    "stripe", "billing cycle", "month-to-month", "skip a month",
]

# Geo signals we'd expect for a restaurant prompt; these are NOT mandatory but
# their presence is informative.
_RESTAURANT_VOCAB = [
    "menu", "dish", "cuisine", "kitchen", "chef", "table", "reservation",
    "booking", "dining", "wine", "pasta", "pizza", "antipasti", "ingredient",
    "trattoria", "ristorante", "cafe", "bar", "tasting", "course",
]

_FAMILY_VOCAB = [
    "family", "generation", "tradition", "heritage", "founded", "since",
    "owner", "nonna", "father", "grandfather", "mother", "grandmother",
]

# Geo references we'd expect for "Brooklyn" — if A's regional_refs talk about
# Florence, that's a contamination indicator.
_BROOKLYN_VOCAB = [
    "brooklyn", "bk", "kings county", "nyc", "new york", "manhattan",
    "dumbo", "park slope", "williamsburg", "carroll gardens", "cobble hill",
    "red hook", "gowanus", "fort greene", "boerum hill", "bay ridge",
    "bensonhurst", "bushwick", "greenpoint", "prospect heights", "crown heights",
    "atlantic avenue", "smith street", "court street",
]


def _lower(x: Any) -> str:
    if isinstance(x, str):
        return x.lower()
    if isinstance(x, list):
        return " ".join(_lower(i) for i in x)
    if isinstance(x, dict):
        return " ".join(_lower(v) for v in x.values())
    return ""


def _count_vocab(text: str, vocab: list[str]) -> tuple[int, list[str]]:
    hits: list[str] = []
    total = 0
    for term in vocab:
        c = len(re.findall(r"\b" + re.escape(term) + r"\b", text))
        if c:
            hits.append(f"{term}×{c}")
            total += c
    return total, hits


def _domain_coherence_check(
    *,
    signals: dict,
    brief_a: dict,
    expected_geo_vocab: list[str],
    expected_topic_vocab: list[str],
) -> dict:
    """Scan Version-A summarizer output for wrong-domain contamination.

    Returns {
      "ok": bool,
      "saas_hits": [..],
      "geo_hits":  [..],   # expected geography terms (Brooklyn etc.)
      "topic_hits":[..],   # expected topic vocab (restaurant words)
      "off_geo_hits": [..],# alien geography (Florence-style for a Brooklyn brand)
      "samples": {voice_phrases, industry_terms, regional_refs},
    }
    """
    domain_sig = (signals or {}).get("domain") or {}
    voice_phrases = domain_sig.get("audience_phrases") or brief_a.get("voice_phrases") or []
    industry_terms = domain_sig.get("industry_terms") or brief_a.get("industry_terms") or []
    regional_touchpoints = domain_sig.get("regional_touchpoints") or []
    regional_refs = brief_a.get("regional_refs") or regional_touchpoints

    combined = " ".join([
        _lower(voice_phrases), _lower(industry_terms),
        _lower(regional_refs), _lower(regional_touchpoints),
    ])

    saas_count, saas_hits = _count_vocab(combined, _SAAS_CONTAMINATION_TERMS)
    geo_count, geo_hits = _count_vocab(combined, expected_geo_vocab)
    topic_count, topic_hits = _count_vocab(combined, expected_topic_vocab)

    # Off-geography: Florence-style terms when we expected Brooklyn-style
    _OFF_GEO_FOR_BROOKLYN = [
        "florence", "tuscany", "tuscan", "piazza", "santo spirito",
        "via tornabuoni", "duomo", "uffizi", "arno", "milan", "rome",
        "naples", "sicily",  # sicily is allowed when the prompt mentions it
    ]
    # Only flag off-geo when our expected geo is Brooklyn-ish; topic vocab
    # (e.g. "sicily" in dishes) is allowed if user mentioned it.
    off_geo_count, off_geo_hits = (0, [])
    if any("brooklyn" in v.lower() or "york" in v.lower() for v in expected_geo_vocab):
        off_geo_count, off_geo_hits = _count_vocab(combined, _OFF_GEO_FOR_BROOKLYN)

    reasons: list[str] = []
    if saas_count:
        reasons.append(f"SaaS contamination: {saas_hits}")
    if not topic_count:
        reasons.append(
            f"no expected topic vocab found (looked for {expected_topic_vocab[:6]}...)"
        )
    if not geo_count and expected_geo_vocab:
        reasons.append(
            f"no expected geo vocab found (looked for {expected_geo_vocab[:4]}...)"
        )
    if off_geo_count:
        reasons.append(f"off-geo references: {off_geo_hits}")

    return {
        "ok":           not reasons,
        "saas_count":   saas_count,
        "saas_hits":    saas_hits,
        "geo_count":    geo_count,
        "geo_hits":     geo_hits,
        "topic_count":  topic_count,
        "topic_hits":   topic_hits,
        "off_geo_count": off_geo_count,
        "off_geo_hits": off_geo_hits,
        "reasons":      reasons,
        "samples": {
            "voice_phrases":  voice_phrases[:4],
            "industry_terms": industry_terms[:6],
            "regional_refs":  [
                r.get("name") if isinstance(r, dict) else r
                for r in regional_refs[:6]
            ],
        },
    }


# ── NEW: Design summarizer status ───────────────────────────────────────

def _design_summarizer_status(signals: dict) -> dict:
    """Did the design-Flash call return real data, or hit MAX_TOKENS?"""
    design = (signals or {}).get("design") or {}
    visual_dna = (signals or {}).get("visual_dna") or {}
    chosen_palette = design.get("chosen_palette") or {}
    chosen_typography = design.get("chosen_typography") or {}

    n_keys = len(design)
    palette_filled = bool(chosen_palette)
    typo_filled = bool(chosen_typography)
    has_visual_dna = bool(visual_dna)

    # Did palette contain real hex/HSL, or just the #2563eb boilerplate?
    pal_values_str = " ".join(str(v) for v in chosen_palette.values())
    non_boilerplate_hexes = [
        h for h in _HEX_RE.findall(pal_values_str)
        if h.lower() != "#2563eb"
    ]
    hsl_codes = _HSL_RE.findall(pal_values_str)

    empty = (n_keys == 0)
    return {
        "ok":                     not empty,
        "empty":                  empty,
        "design_keys":            n_keys,
        "palette_filled":         palette_filled,
        "typography_filled":      typo_filled,
        "visual_dna_filled":      has_visual_dna,
        "non_boilerplate_hexes":  non_boilerplate_hexes[:6],
        "hsl_codes":              hsl_codes[:6],
        "chosen_palette_sample":  dict(list(chosen_palette.items())[:4]),
        "chosen_typo_sample":     dict(list(chosen_typography.items())[:4]),
    }


# ── Main A/B ─────────────────────────────────────────────────────────────

async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt", default=os.environ.get("BYPASS_TEST_PROMPT", _DEFAULT_PROMPT))
    parser.add_argument("--output-dir", default=_DEFAULT_OUT)
    parser.add_argument("--force-fresh-research", action="store_true",
                        help="Ignore cached research and re-run the Gemini research stage")
    parser.add_argument("--yes", action="store_true",
                        help="Skip confirmation prompt (kept for compat — no Claude calls now)")
    args = parser.parse_args()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_path = str(out_dir / "research_cache.json")

    print("=" * 72)
    print("SUMMARIZER A/B — PROMPT-CAPTURE MODE (no Claude calls)")
    print("=" * 72)
    print(f"prompt:     {args.prompt}")
    print(f"output:     {out_dir}")
    print(f"cache:      {cache_path} "
          f"({'exists' if os.path.exists(cache_path) else 'missing'})")

    # ── Phase 1 — research (cached if available)
    intent, domain_research, design_research, legacy_brief = (
        await _build_or_load_research(
            prompt=args.prompt,
            cache_path=cache_path,
            force_fresh=args.force_fresh_research,
        )
    )

    domain_bytes = sum(len((v or {}).get("text", "")) for v in (domain_research or {}).values()
                       if isinstance(v, dict))
    design_bytes = sum(len((v or {}).get("text", "")) for v in (design_research or {}).values()
                       if isinstance(v, dict))
    print(f"  domain markdown:    {domain_bytes:,} chars")
    print(f"  design markdown:    {design_bytes:,} chars")

    # ── Phase 1b — INTENT GROUNDING CHECK (abort on fallback) ─────────
    print("\n" + "─" * 72)
    print("Intent grounding check")
    print("─" * 72)
    intent_check = _intent_grounding_check(intent, args.prompt)
    print(f"  is_fallback:       {intent_check['is_fallback']}")
    print(f"  business_category: {intent_check['details']['business_category']!r}")
    print(f"  geographic_specifics: {intent_check['details']['geographic_specifics']!r}")
    print(f"  primary_purpose:   {intent_check['details']['primary_purpose']!r}")
    print(f"  tone:              {intent_check['details']['tone']!r}")
    print(f"  brand_personality: {intent_check['details']['brand_personality']!r}")
    if not intent_check["ok"]:
        print("\n✗ INTENT GROUNDING FAILED — refusing to run A/B on poisoned input.")
        for r in intent_check["reasons"]:
            print(f"    - {r}")
        # Persist diagnostic for inspection
        (out_dir / "intent_check.json").write_text(
            json.dumps(intent_check, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return 2
    print("  ✓ intent looks grounded — proceeding")

    # ── Phase 2 — Version A: summarizer ON + enrich
    print("\n" + "─" * 72)
    print("Version A — summarizer ENABLED")
    print("─" * 72)
    from app.services.landing_research_extract import (
        extract_research_signals,
        enrich_brief_with_signals,
    )
    t0 = time.time()
    signals = await extract_research_signals(
        intent, domain_research, design_research, timeout_s=180.0,
    )
    summarizer_wall = round(time.time() - t0, 1)
    print(f"  summarizer wall:   {summarizer_wall}s")
    anatomies = (signals.get("visual_dna") or {}).get("section_anatomies") or {}
    print(f"  section anatomies: {len(anatomies)} ({', '.join(sorted(anatomies))[:80]})")

    brief_a = copy.deepcopy(legacy_brief)
    enrich_brief_with_signals(brief_a, signals)

    # ── Phase 2b — DOMAIN COHERENCE + DESIGN-SUMMARIZER STATUS ────────
    # Pick expected vocab based on the prompt. We bias toward Brooklyn /
    # restaurant for the current test; if you change the prompt domain,
    # extend these lists or move them into config.
    expected_geo_vocab = list(_BROOKLYN_VOCAB)
    expected_topic_vocab = list(_RESTAURANT_VOCAB) + list(_FAMILY_VOCAB)
    coherence = _domain_coherence_check(
        signals=signals, brief_a=brief_a,
        expected_geo_vocab=expected_geo_vocab,
        expected_topic_vocab=expected_topic_vocab,
    )
    print("\n  domain coherence:")
    print(f"    SaaS contamination hits:  {coherence['saas_count']}  {coherence['saas_hits']}")
    print(f"    expected geo hits:        {coherence['geo_count']}  {coherence['geo_hits'][:6]}")
    print(f"    expected topic hits:      {coherence['topic_count']}  {coherence['topic_hits'][:6]}")
    print(f"    off-geo hits:             {coherence['off_geo_count']}  {coherence['off_geo_hits']}")
    if coherence["ok"]:
        print("    ✓ Version A is domain-coherent")
    else:
        print("    ✗ Version A shows contamination:")
        for r in coherence["reasons"]:
            print(f"      - {r}")

    design_status = _design_summarizer_status(signals)
    print("\n  design summarizer status:")
    print(f"    design_keys:           {design_status['design_keys']}")
    print(f"    empty (MAX_TOKENS?):   {design_status['empty']}")
    print(f"    palette_filled:        {design_status['palette_filled']}")
    print(f"    typography_filled:     {design_status['typography_filled']}")
    print(f"    non-boilerplate hexes: {design_status['non_boilerplate_hexes']}")
    print(f"    hsl codes:             {design_status['hsl_codes']}")

    # ── Phase 3 — Version B: bypass; mechanically extract palette/typography
    print("\n" + "─" * 72)
    print("Version B — summarizer BYPASSED (mechanical extraction only)")
    print("─" * 72)
    brief_b = copy.deepcopy(legacy_brief)
    extracted_palette = _extract_palette_from_raw(design_research)
    extracted_typography = _extract_typography_from_raw(design_research)
    if extracted_palette:
        brief_b["palette"] = extracted_palette
    if extracted_typography:
        brief_b["typography"] = extracted_typography
    # Explicitly clear summarizer-derived fields so we capture the difference
    for k in ("voice_phrases", "industry_terms", "regional_refs", "white_space",
              "visual_dna", "purpose_directive"):
        brief_b.pop(k, None)
    print(f"  extracted palette slots: {len(extracted_palette)} → {extracted_palette}")
    print(f"  extracted typography:    {extracted_typography}")
    raw_block = _format_raw_markdown_block(domain_research, design_research)
    print(f"  raw block size:     {len(raw_block):,} chars (appended to system prompt)")

    # ── Phase 4 — capture 3 section prompts for each version
    chosen_a = _pick_three_sections(brief_a)
    chosen_b = _pick_three_sections(brief_b)
    print("\nSections chosen:")
    for label, sec in chosen_a:
        print(f"  • {label:<8} → id={sec.get('id')!r} type={sec.get('type')!r}")

    captured_a = _capture_prompts_for_version(
        brief=brief_a, chosen=chosen_a,
        out_dir=out_dir / "A_summarized",
        raw_block_suffix="",
    )
    captured_b = _capture_prompts_for_version(
        brief=brief_b, chosen=chosen_b,
        out_dir=out_dir / "B_bypassed",
        raw_block_suffix=raw_block,
    )

    # ── Phase 5 — score each captured prompt
    enc = _get_encoding()

    def _score_version(captured: dict[str, dict]) -> dict:
        token_counts: dict[str, dict] = {}
        density: dict[str, dict] = {}
        for label, p in captured.items():
            sys_p = p["system"]
            usr_p = p["user"]
            sys_tok = _count_tokens(enc, sys_p)
            usr_tok = _count_tokens(enc, usr_p)
            total_tok = sys_tok + usr_tok
            # split research vs instructions across BOTH prompts
            sys_r, sys_i = _split_research_vs_instructions(enc, sys_p)
            usr_r, usr_i = _split_research_vs_instructions(enc, usr_p)
            token_counts[label] = {
                "system_tokens":         sys_tok,
                "user_tokens":           usr_tok,
                "total_tokens":          total_tok,
                "research_brief_tokens": sys_r + usr_r,
                "instructions_tokens":   sys_i + usr_i,
            }
            combined = sys_p + "\n" + usr_p
            density[label] = _count_density(combined)
        return {"token_counts": token_counts, "density": density}

    metrics_a = _score_version(captured_a)
    metrics_b = _score_version(captured_b)

    presence_a = _structured_presence(brief_a)
    presence_b = _structured_presence(brief_b)

    versions = {
        "A_summarized": {
            "captured":          captured_a,
            "token_counts":      metrics_a["token_counts"],
            "density":           metrics_a["density"],
            "presence":          presence_a,
            "summarizer_wall_s": summarizer_wall,
        },
        "B_bypassed": {
            "captured":          captured_b,
            "token_counts":      metrics_b["token_counts"],
            "density":           metrics_b["density"],
            "presence":          presence_b,
            "summarizer_wall_s": 0.0,
        },
    }

    research_meta = {
        "cache_path":         cache_path,
        "domain_bytes":       domain_bytes,
        "design_bytes":       design_bytes,
        "summarizer_wall_s":  summarizer_wall,
    }

    report_md, verdict, summary = _build_report_md(
        prompt_text=args.prompt,
        research_meta=research_meta,
        versions=versions,
        intent_check=intent_check,
        coherence=coherence,
        design_status=design_status,
    )

    (out_dir / "report.md").write_text(report_md, encoding="utf-8")
    with open(out_dir / "report.json", "w", encoding="utf-8") as f:
        json.dump({
            "prompt":     args.prompt,
            "verdict":    verdict,
            "summary":    summary,
            "presence_a": presence_a,
            "presence_b": presence_b,
            "tokens_a":   metrics_a["token_counts"],
            "tokens_b":   metrics_b["token_counts"],
        }, f, indent=2, ensure_ascii=False)

    # ── Phase 6 — console summary
    print()
    print("=" * 72)
    print("RESULTS — A vs B summary")
    print("=" * 72)
    print(f"{'Metric':<40}{'A':>15}{'B':>15}")
    print("-" * 70)
    for label in ("hero", "about", "footer"):
        a = metrics_a["token_counts"][label]
        b = metrics_b["token_counts"][label]
        print(f"  {label} total tokens".ljust(40) + f"{a['total_tokens']:>15,}{b['total_tokens']:>15,}")
        print(f"    research/brief tokens".ljust(40) + f"{a['research_brief_tokens']:>15,}{b['research_brief_tokens']:>15,}")
        print(f"    instruction tokens".ljust(40)   + f"{a['instructions_tokens']:>15,}{b['instructions_tokens']:>15,}")
    print()
    print(f"{'brief-field fill score (out of 7)':<40}{summary['a_fill']:>15}{summary['b_fill']:>15}")
    print(f"{'specifics total (across 3 sections)':<40}{summary['a_spec_total']:>15}{summary['b_spec_total']:>15}")
    print(f"{'generic hits (across 3 sections)':<40}{summary['a_gen_total']:>15}{summary['b_gen_total']:>15}")
    print(f"{'per-gen cost (3 sections, Sonnet)':<40}{'$' + format(summary['a_cost_total'] + _GEMINI_SUMMARIZER_COST, '.4f'):>15}"
          f"{'$' + format(summary['b_cost_total'], '.4f'):>15}")
    print()
    print(f"VERDICT: {verdict}")
    print(f"Report:  {out_dir / 'report.md'}")
    print(f"Prompts: {out_dir / 'A_summarized' / 'prompts'} vs {out_dir / 'B_bypassed' / 'prompts'}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
