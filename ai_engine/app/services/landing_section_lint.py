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

# Internal design rationale rendered as visible copy: the brief's
# `section.role` field ("Subtle social proof to reduce friction immediately")
# printed as an eyebrow/label (Luminary Austin 2026-06-12).
SECTION_ROLE_LEAK_RE = re.compile(r"\bsection\.role\b")

# Compact-strip section types: seams between sections, never destinations.
STRIP_SECTION_TYPES = frozenset({
    "trust_bar", "trust_ticker", "trust_strip",
    "logo_strip", "logo_bar", "logos", "press_bar", "press_strip",
    "ticker", "marquee", "social_proof_bar",
})

# Root vertical padding too tall for a strip section (py-16 and above).
STRIP_OVERSIZED_PY_RE = re.compile(r"\b(?:(?:sm|md|lg|xl|2xl):)?py-(?:1[6-9]|[2-9]\d)\b")

# Layout cross-embedding: the header file shipping its own footer (or vice
# versa) renders the embedded one in page flow at the WRONG position — a
# full footer above the hero, then again at the page end (double footer,
# Luminary Austin 2026-06-12).
FOOTER_MARKUP_RE = re.compile(r"<footer\b|\bMarketingFooter\b")
HEADER_MARKUP_RE = re.compile(r"<header\b|\bMarketingHeader\b")

# Native date inputs: the popup renders in the OS locale (Cyrillic month
# names, Chrome-blue highlights) and cannot be styled — Saint Cecilia
# booking bar 2026-06-13. The contract is a custom dayjs calendar.
NATIVE_DATE_INPUT_RE = re.compile(r"<input\b[^>]*type=[\"']date[\"']")

# A lucide icon NAME ("BatteryCharging") rendered as VISIBLE TEXT instead of
# as an <Icon/> component — Pitch & Pavilion stays bento 2026-06-13. `.icon`
# fields select a component; printed as a JSX text child they ship a
# PascalCase identifier as a label. Matches `>{x.icon}<` and the common
# `<span ...>{x.icon}</span>` chip. Excludes `<X.icon` (rendering the
# component) and `icon={...}` / `icon: ...` (prop/data usage).
ICON_NAME_AS_TEXT_RE = re.compile(r">\s*\{[\w$]+\.icon\}\s*<")
# Full single-purpose element whose only child is an `.icon` text node — the
# fixer removes these (the icon component is rendered separately).
ICON_TEXT_ELEMENT_RE = re.compile(
    r"<(span|p|div)\b[^>]*>\s*\{[\w$]+\.icon\}\s*</\1>"
)

# Bento tiles with mixed aspect-* utilities: grid rows size to the tallest
# tile, every shorter sibling leaves a void (suites bento 2026-06-13).
ASPECT_TOKEN_RE = re.compile(r"\baspect-(?:\[[^\]]+\]|square|video)")
ROW_SPAN_RE = re.compile(r"\brow-span-\d|\bauto-rows-")
_JS_COMMENT_RE = re.compile(r"//[^\n]*|/\*.*?\*/", re.DOTALL)


def strip_js_comments(source: str) -> str:
    """Drop // and /* */ comments so a PLANNED-but-never-applied class in a
    comment can't satisfy a structural check (the shipped suites bento had
    'row-span-2' in its layout comment and nowhere in the JSX)."""
    return _JS_COMMENT_RE.sub("", source)

TRANSPARENT_HEADER_RE = re.compile(r"\bbg-transparent\b")

# Over-frosted header: the wordmark, nav, AND social each wrapped in their own
# translucent `bg-background/NN backdrop-blur` chip stacks 3-4 frosted layers
# over the hero — the dated, muddy-grey-smear header (Pitch & Pavilion
# 2026-06-13). A translucent bg-background fill (opacity < 90) paired with
# backdrop-blur in ONE className is a "frosted chip"; 3+ of them is clutter.
_FROSTED_CHIP_RE = re.compile(r"bg-background/(?:[1-8]?\d)\b(?=[^\"']*backdrop-blur)")


# Any quoted string literal — headers build classes via `className={[ 'base',
# scrolled ? 'a' : 'b' ].join(' ')}`, so the frosted classes live in array
# strings that `className="..."` matching never sees.
_QUOTED_LITERAL_RE = re.compile(r"\"([^\"]*)\"|'([^']*)'")


def count_frosted_chips(source: str) -> int:
    """Number of class-string literals that are translucent frosted chips
    (bg-background/<90 + backdrop-blur in the same class list). Scans every
    quoted literal so it sees classes inside dynamic className={[...]} arrays,
    not only static className="..." attributes."""
    n = 0
    for m in _QUOTED_LITERAL_RE.finditer(source):
        classes = m.group(1) if m.group(1) is not None else m.group(2)
        if not classes or "backdrop-blur" not in classes:
            continue
        for tok in classes.split():
            if tok.startswith("bg-background/"):
                try:
                    if int(tok.split("/", 1)[1]) < 90:
                        n += 1
                        break
                except (ValueError, IndexError):
                    pass
    return n

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
    fname = (filename or "").strip()
    if fname.startswith("MarketingHeader") and FOOTER_MARKUP_RE.search(source):
        add(
            "header_embeds_footer", "regen",
            "MarketingHeader must contain ONLY the header — a separate MarketingFooter "
            "component is generated by another call and rendered by layout.jsx after the "
            "page content. Remove every <footer> element and MarketingFooter "
            "definition/render from this file; the component returns exactly one <header>",
        )
    if fname.startswith("MarketingFooter") and HEADER_MARKUP_RE.search(source):
        add(
            "footer_embeds_header", "regen",
            "MarketingFooter must contain ONLY the footer — a separate MarketingHeader "
            "component is generated by another call and rendered by layout.jsx before the "
            "page content. Remove every <header> element and MarketingHeader "
            "definition/render from this file; the component returns exactly one <footer>",
        )

    if fname.startswith("MarketingHeader") and count_frosted_chips(source) >= 3:
        add(
            "header_overfrosted", "regen",
            "the header stacks 3+ translucent frosted chips (wordmark, nav, and social each "
            "in their own bg-background/NN backdrop-blur pill) — it reads as a muddy grey "
            "smear over the hero. Rebuild as ONE clean row: over a dark/media hero use a "
            "transparent header with text-white links + a single top gradient scrim (no bar, "
            "no pills); over a light hero use ONE thin solid bar. The CTA is the only filled "
            "element. On scroll, flip the root to a solid bg-background surface",
        )

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

    if NATIVE_DATE_INPUT_RE.search(source):
        add(
            "native_date_input", "regen",
            "<input type=\"date\"> is banned — its popup renders in the OS locale "
            "(Cyrillic month names, unstylable Chrome blue) on the page's most "
            "conversion-critical control; build the custom dayjs calendar from the "
            "DATE FIELDS rules (month grid in a top-full panel, range selection)",
        )

    if ICON_NAME_AS_TEXT_RE.search(source):
        add(
            "icon_name_as_text", "fixable",
            "a lucide icon NAME is rendered as visible text ({x.icon} as a JSX child) — "
            "`.icon` selects an <Icon/> component, not copy; a PascalCase identifier "
            "like \"BatteryCharging\" ships as a label. Render the icon component "
            "(<IconComp/>) and use a real label field (item.label/tag/category) for any "
            "visible text",
        )

    code_only = strip_js_comments(source)
    if "grid-cols-12" in code_only and not ROW_SPAN_RE.search(code_only):
        aspects = set(ASPECT_TOKEN_RE.findall(code_only))
        if len(aspects) >= 2:
            add(
                "bento_aspect_mix", "regen",
                f"bento grid mixes {len(aspects)} different aspect-* utilities "
                "with no row-span/auto-rows — grid rows size to the tallest tile "
                "and every shorter sibling leaves an empty void; use the BENTO "
                "recipe: auto-rows-[*] + row-span-* + <Image fill> per tile",
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

    if SECTION_ROLE_LEAK_RE.search(source):
        add(
            "section_role_leak", "fixable",
            "section.role is rendered in the UI — it is internal design rationale "
            "('subtle social proof to reduce friction'), not brand copy; eyebrow labels "
            "use section.nav_label or a short label you write in brand voice",
        )

    stype = ((section or {}).get("type") or "").strip().lower()
    if stype in STRIP_SECTION_TYPES and STRIP_OVERSIZED_PY_RE.search(root_classes):
        add(
            "strip_section_oversized", "fixable",
            f"a {stype} section is a compact seam between sections, not a destination — "
            "root padding caps at py-10 md:py-12 (one slim row of logos/stats, "
            "never full-height cards)",
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
