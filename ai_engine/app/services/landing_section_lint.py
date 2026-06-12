"""Structural lint for generated section/layout JSX.

Pure string-level checks — no I/O, no LLM, hermetically testable. Two
consumers:

  • landing_section_codegen — lints each candidate BEFORE accepting it.
    Violations with severity "regen" turn the section's second attempt into
    a GUIDED retry (the violation list is appended to the user prompt)
    instead of a blind one. Violations with severity "fixable" never burn
    an API call — post_generation_fixer repairs them deterministically.

  • post_generation_fixer — imports the shared regexes so detection and
    repair can't drift apart.

Severity model:
  regen   — not safely auto-repairable (hardcoded copy, raw hex colors,
            inline style colors): the model must redo it.
  fixable — mechanical repairs exist (strip <br> in headings, downgrade
            overflow clipping, swap invisible same-element text colors,
            remove viewport heights on non-hero sections, cap padding).
"""
from __future__ import annotations

import re
from typing import Any

# ── shared regexes (post_generation_fixer imports these) ────────────────────

# <h1>/<h2> block (same-tag nesting impossible in valid JSX headings).
HEADING_BLOCK_RE = re.compile(r"<h([12])\b[^>]*>.*?</h\1>", re.DOTALL)
BR_TAG_RE = re.compile(r"\s*<br\b[^>]*/?>\s*")

# Interactive dropdown/popover panel anchored to its trigger.
DROPDOWN_PANEL_RE = re.compile(r"\b(?:top|bottom)-full\b")

# Section root opener: first `<section ... className="..."` in the file.
SECTION_ROOT_RE = re.compile(
    r'<section\b([^>]*?)className=(["\'])([^"\']*)\2',
    re.DOTALL,
)

# Every className="..." string in the file (single/double quoted, no
# template literals — those are linted via the raw source checks instead).
CLASSNAME_RE = re.compile(r'className=(["\'])([^"\']*)\1')

# Viewport-height tokens that belong on heroes only.
VIEWPORT_HEIGHT_RE = re.compile(r"\bmin-h-(?:screen|\[100[sld]?vh\])|\bh-screen\b")

# Section-root vertical padding above the py-24 contract cap.
OVERSIZED_PY_RE = re.compile(r"\bpy-(?:3[26]|4[048]|5[26]|6[04])\b")

# Raw hex colors inside class strings (`text-[#fff]`, `bg-[#1a1a2e]`).
RAW_HEX_CLASS_RE = re.compile(
    r"\b(?:text|bg|border|from|via|to|ring|fill|stroke)-\[#[0-9a-fA-F]{3,8}\]"
)

# Inline style colors: style={{ color: ..., background...: ... }}.
INLINE_STYLE_COLOR_RE = re.compile(
    r"style=\{\{[^}]*\b(?:color|background(?:Color)?)\s*:",
)

# Same-element invisible pairs: solid surface + same-hue text token in ONE
# className string. (`bg-foreground/10` is a tint on a light page — fine;
# only solid surfaces create the invisible pairing.)
CONTRAST_PAIRS: list[tuple[re.Pattern[str], re.Pattern[str], str, str]] = [
    (
        re.compile(r"\bbg-foreground\b(?!/)"),
        re.compile(r"\btext-foreground\b(/\d+)?"),
        "text-background",
        "text-foreground on solid bg-foreground is invisible — use text-background",
    ),
    (
        re.compile(r"\bbg-foreground\b(?!/)"),
        re.compile(r"\btext-muted-foreground\b(/\d+)?"),
        "text-background/70",
        "text-muted-foreground on solid bg-foreground is invisible — use text-background/70",
    ),
    (
        re.compile(r"\bbg-primary\b(?!/|-)"),
        re.compile(r"\btext-primary\b(?!-)(/\d+)?"),
        "text-primary-foreground",
        "text-primary on solid bg-primary is invisible — use text-primary-foreground",
    ),
    (
        re.compile(r"\bbg-muted\b(?!/|-)"),
        re.compile(r"\btext-muted\b(?!-)(/\d+)?"),
        "text-muted-foreground",
        "text-muted on bg-muted is invisible — use text-muted-foreground",
    ),
    (
        re.compile(r"\bbg-background\b(?!/)"),
        re.compile(r"\btext-background\b(/\d+)?"),
        "text-foreground",
        "text-background on solid bg-background is invisible — use text-foreground",
    ),
]


def _is_hero(section: dict[str, Any] | None, filename: str) -> bool:
    if section:
        stype = (section.get("type") or section.get("role") or "").strip().lower()
        if "hero" in stype:
            return True
        if "hero" in (section.get("id") or "").lower():
            return True
    return "hero" in (filename or "").lower()


def find_same_element_contrast(source: str) -> list[tuple[str, str, str]]:
    """Return (class_string, replacement_token, message) per invisible pair."""
    hits: list[tuple[str, str, str]] = []
    for m in CLASSNAME_RE.finditer(source):
        classes = m.group(2)
        for bg_re, text_re, replacement, message in CONTRAST_PAIRS:
            if bg_re.search(classes) and text_re.search(classes):
                hits.append((classes, replacement, message))
    return hits


def lint_section_source(
    source: str,
    *,
    section: dict[str, Any] | None = None,
    filename: str = "",
) -> list[dict[str, str]]:
    """Lint one generated component's source. Returns violation dicts:
    {"code", "severity" ("regen"|"fixable"), "message"}.
    """
    violations: list[dict[str, str]] = []

    def add(code: str, severity: str, message: str) -> None:
        violations.append({"code": code, "severity": severity, "message": message})

    # ── regen class — the model must redo these ─────────────────────────
    headline = ((section or {}).get("headline") or "").strip()
    if len(headline) >= 16 and headline in source:
        add(
            "hardcoded_headline", "regen",
            f"the headline text {headline[:60]!r} is retyped as a literal in the JSX — "
            "render {section.headline} (JS word-split for the accent span); retyped copy "
            "breaks the content editor",
        )

    if RAW_HEX_CLASS_RE.search(source):
        sample = RAW_HEX_CLASS_RE.search(source).group(0)  # type: ignore[union-attr]
        add(
            "raw_hex_color", "regen",
            f"raw hex color in a Tailwind class ({sample}) — use semantic tokens "
            "(bg-primary, text-foreground, bg-muted, ...) only",
        )

    if INLINE_STYLE_COLOR_RE.search(source):
        add(
            "inline_style_color", "regen",
            "style={{ color | background }} prop — colors come from Tailwind "
            "semantic classes, never inline styles",
        )

    if "var(--color-" in source:
        add(
            "css_var_color", "regen",
            "var(--color-*) does not exist in this stack — use Tailwind semantic classes",
        )

    # ── fixable class — deterministic fixers repair these ───────────────
    for block_match in HEADING_BLOCK_RE.finditer(source):
        if "<br" in block_match.group(0):
            add(
                "br_in_heading", "fixable",
                "manual <br> inside an h1/h2 — remove it and let CSS line-wrap decide",
            )
            break

    root = SECTION_ROOT_RE.search(source)
    root_classes = root.group(3) if root else ""
    if "overflow-hidden" in root_classes and DROPDOWN_PANEL_RE.search(source):
        add(
            "popover_overflow_clip", "fixable",
            "section root has overflow-hidden while a top-full/bottom-full panel exists — "
            "the panel gets clipped at the section boundary; use overflow-x-clip on the "
            "root and clip decor in its own absolute inset-0 overflow-hidden layer",
        )

    for _classes, _replacement, message in find_same_element_contrast(source):
        add("same_element_contrast", "fixable", message)

    if not _is_hero(section, filename) and VIEWPORT_HEIGHT_RE.search(root_classes):
        add(
            "nonhero_viewport_height", "fixable",
            "min-h-screen/h-screen on a non-hero section root — only the hero commands "
            "the viewport; other sections size to content",
        )

    if OVERSIZED_PY_RE.search(root_classes):
        add(
            "oversized_padding", "fixable",
            "section root vertical padding above the py-24 cap — use py-16 md:py-20 lg:py-24",
        )

    return violations


def regen_feedback(violations: list[dict[str, str]]) -> str:
    """Build the guided-retry prompt block from attempt-1 violations.

    Includes ALL violations (fixable ones too — free to mention since we're
    paying for the retry anyway), but only regen-class ones trigger it.
    """
    if not violations:
        return ""
    lines = [
        "\n\n── PREVIOUS ATTEMPT REJECTED — STRUCTURAL VIOLATIONS ──",
        "Your previous output for this exact task violated the contract. "
        "Produce the component again, fixing EVERY item below. Do not repeat them:",
    ]
    for v in violations:
        lines.append(f"  ✗ [{v['code']}] {v['message']}")
    lines.append("Everything else about the task is unchanged.")
    return "\n".join(lines)
