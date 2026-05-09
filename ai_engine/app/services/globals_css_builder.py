"""Deterministic globals.css builder.

Writes the project's globals.css from the schema's theme + fonts BEFORE
Phase 1 of code generation, instead of relying on Claude to remember to
swap the shadcn-blue placeholders. Result: every project's CSS variables
actually reflect that project's palette + Google Fonts, not the skeleton
defaults.

Mirrors the pattern in marketing_header_builder.py — pure function + a
caller-side write. Fail-soft: caller catches exceptions and falls back to
the LLM rewriting globals.css the old way.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


# ── Defaults used when the schema theme is missing a token ───────────────
# These are the shadcn defaults — same ones the skeleton ships with.
# Any token the schema provides overrides them; any token it omits keeps
# the default so the file is always complete + valid.
_DEFAULT_LIGHT: dict[str, str] = {
    "background": "0 0% 100%",
    "foreground": "222.2 84% 4.9%",
    "card": "0 0% 100%",
    "card-foreground": "222.2 84% 4.9%",
    "popover": "0 0% 100%",
    "popover-foreground": "222.2 84% 4.9%",
    "primary": "221.2 83.2% 53.3%",
    "primary-foreground": "210 40% 98%",
    "secondary": "210 40% 96.1%",
    "secondary-foreground": "222.2 47.4% 11.2%",
    "muted": "210 40% 96.1%",
    "muted-foreground": "215.4 16.3% 46.9%",
    "accent": "210 40% 96.1%",
    "accent-foreground": "222.2 47.4% 11.2%",
    "destructive": "0 84.2% 60.2%",
    "destructive-foreground": "210 40% 98%",
    "border": "214.3 31.8% 91.4%",
    "input": "214.3 31.8% 91.4%",
    "ring": "221.2 83.2% 53.3%",
}

# Easing curve lookup for the Director's `motion_language.easing_signature`.
# Mapped to concrete cubic-bezier() values written into globals.css as
# --ease-sig — every transition/keyframe uses var(--ease-sig) so the curve is
# globally consistent without any per-component opt-in.
_EASING_CURVE_MAP: dict[str, str] = {
    "quint-out":       "cubic-bezier(0.16, 1, 0.3, 1)",
    "expo-out":        "cubic-bezier(0.19, 1, 0.22, 1)",
    "circ-out":        "cubic-bezier(0, 0.55, 0.45, 1)",
    "back-out-subtle": "cubic-bezier(0.34, 1.2, 0.64, 1)",
    "linear-precise":  "linear",
    # spring curves can't render as cubic-bezier — fall back to quint-out for
    # the CSS variable; components that want the Motion spring use it directly.
    "spring-quiet":    "cubic-bezier(0.16, 1, 0.3, 1)",
}


def _motion_signature_block(theme: dict) -> str:
    """Emit the global motion variables consumed by .reveal-up + components.

    Reads the Director's motion_signature off the schema's theme.motion_*
    fields if present; otherwise emits sensible quint-out defaults so any
    component using var(--ease-sig) / var(--d-base) Just Works.
    """
    motion = (theme.get("motion") or {})
    easing_key = (motion.get("easing_signature") or "quint-out").strip().lower()
    durations = motion.get("durations") or {}
    fast = (durations.get("fast") or "180ms").strip()
    base = (durations.get("base") or "550ms").strip()
    slow = (durations.get("slow") or "1000ms").strip()
    curve = _EASING_CURVE_MAP.get(easing_key, _EASING_CURVE_MAP["quint-out"])
    return (
        f"    --d-fast: {fast};\n"
        f"    --d-base: {base};\n"
        f"    --d-slow: {slow};\n"
        f"    --ease-sig: {curve};\n"
    )


_DEFAULT_DARK: dict[str, str] = {
    "background": "222.2 84% 4.9%",
    "foreground": "210 40% 98%",
    "card": "222.2 84% 4.9%",
    "card-foreground": "210 40% 98%",
    "popover": "222.2 84% 4.9%",
    "popover-foreground": "210 40% 98%",
    "primary": "217.2 91.2% 59.8%",
    "primary-foreground": "222.2 47.4% 11.2%",
    "secondary": "217.2 32.6% 17.5%",
    "secondary-foreground": "210 40% 98%",
    "muted": "217.2 32.6% 17.5%",
    "muted-foreground": "215 20.2% 65.1%",
    "accent": "217.2 32.6% 17.5%",
    "accent-foreground": "210 40% 98%",
    "destructive": "0 62.8% 30.6%",
    "destructive-foreground": "210 40% 98%",
    "border": "217.2 32.6% 17.5%",
    "input": "217.2 32.6% 17.5%",
    "ring": "224.3 76.3% 48%",
}


# Tokens we render in the order shadcn/ui expects. Order matters for
# diffing legibility, not for correctness.
_LIGHT_TOKEN_ORDER: tuple[str, ...] = (
    "background", "foreground",
    "card", "card-foreground",
    "popover", "popover-foreground",
    "primary", "primary-foreground",
    "secondary", "secondary-foreground",
    "muted", "muted-foreground",
    "accent", "accent-foreground",
    "destructive", "destructive-foreground",
    "border", "input", "ring",
)


def _hsl_lightness(hsl: str) -> float | None:
    """Extract the lightness percentage (0-100) from an 'H S% L%' HSL string."""
    try:
        parts = hsl.strip().split()
        if len(parts) >= 3:
            return float(parts[2].rstrip("%"))
    except (ValueError, IndexError):
        pass
    return None


def _hsl_set_lightness(hsl: str, new_l: float) -> str:
    """Return the same hue+saturation with lightness replaced."""
    parts = hsl.strip().split()
    if len(parts) >= 3:
        return f"{parts[0]} {parts[1]} {new_l:.1f}%"
    return hsl


def _enforce_readable_pair(fg_val: str, bg_val: str) -> str:
    """Return a corrected foreground value that is readable against bg.

    If the contrast between fg and bg is acceptable (lightness difference ≥ 45
    percentage points), return fg unchanged.  Otherwise flip the foreground
    lightness to the opposite pole so text is always legible.

    This is a fast heuristic (lightness gap, not full WCAG luminance) — good
    enough to prevent the invisible-text bug where a dark bg gets a dark fg.
    """
    bg_l = _hsl_lightness(bg_val)
    fg_l = _hsl_lightness(fg_val)
    if bg_l is None or fg_l is None:
        return fg_val
    # Dark background → foreground must be light (≥ 85%)
    if bg_l < 40 and fg_l < 60:
        return _hsl_set_lightness(fg_val, 92.0)
    # Light background → foreground must be dark (≤ 20%)
    if bg_l >= 60 and fg_l > 40:
        return _hsl_set_lightness(fg_val, 8.0)
    return fg_val


def _fix_theme_contrast(theme: dict, defaults: dict[str, str]) -> dict:
    """Return a copy of theme with foreground tokens corrected for readability.

    Pairs checked: foreground/background, card-foreground/card,
    muted-foreground/muted.  Other foreground tokens are left to the LLM.
    """
    result = dict(theme)
    pairs = [
        ("foreground", "background"),
        ("card_foreground", "card"),
        ("muted_foreground", "muted"),
        ("popover_foreground", "popover"),
    ]
    for fg_key, bg_key in pairs:
        fg_val = (result.get(fg_key) or "").strip() or defaults.get(fg_key.replace("_", "-"), "")
        bg_val = (result.get(bg_key) or "").strip() or defaults.get(bg_key.replace("_", "-"), "")
        if fg_val and bg_val:
            fixed = _enforce_readable_pair(fg_val, bg_val)
            if fixed != fg_val:
                logger.info(
                    "globals_css_builder: auto-corrected %s lightness for readability "
                    "(bg=%s was %s, fg was %s → %s)",
                    fg_key, bg_key, bg_val, fg_val, fixed,
                )
                result[fg_key] = fixed
    return result


def _theme_value(theme: dict, css_key: str, defaults: dict[str, str]) -> str:
    """Look up a theme value by CSS key (e.g. 'card-foreground').

    The schema stores keys with underscores ('card_foreground'); CSS uses
    dashes. This translates and falls back to the default palette for any
    token the schema omits — guarantees the output file is always complete.
    """
    schema_key = css_key.replace("-", "_")
    val = (theme.get(schema_key) or "").strip()
    return val or defaults[css_key]


def _emit_var_block(theme: dict, defaults: dict[str, str], indent: str = "    ") -> str:
    lines = [
        f"{indent}--{key}: {_theme_value(theme, key, defaults)};"
        for key in _LIGHT_TOKEN_ORDER
    ]
    return "\n".join(lines)


def _emit_chart_vars(theme: dict, indent: str = "    ") -> str:
    """Emit --chart-1..N from theme.chart_colors. Empty string if absent."""
    chart = theme.get("chart_colors") or []
    if not chart:
        return ""
    lines = [f"{indent}--chart-{i}: {c};" for i, c in enumerate(chart, 1) if c]
    return "\n" + "\n".join(lines) if lines else ""


def _font_imports(theme: dict) -> list[str]:
    """Collect Google Fonts @import lines, deduped, in order: heading then body."""
    seen: set[str] = set()
    out: list[str] = []
    for url_key in ("heading_font_url", "body_font_url"):
        url = (theme.get(url_key) or "").strip()
        if url and url not in seen:
            seen.add(url)
            out.append(f"@import url('{url}');")
    return out


def _font_family_classes(theme: dict) -> str:
    """Emit `.font-heading` and `.font-body` utility classes when fonts are set.

    Components reference these via `font-heading` / `font-body` in className,
    which is friendlier than maintaining font-family in tailwind.config.js.
    """
    heading = (theme.get("heading_font") or "").strip()
    body = (theme.get("body_font") or "").strip()
    if not heading and not body:
        return ""
    lines = ["@layer utilities {"]
    if heading:
        lines.append(
            f"  .font-heading {{ font-family: '{heading}', ui-serif, Georgia, serif; }}"
        )
    if body:
        lines.append(
            f"  .font-body {{ font-family: '{body}', system-ui, -apple-system, sans-serif; }}"
        )
    lines.append("}")
    return "\n".join(lines) + "\n"


def build_globals_css(schema: dict[str, Any]) -> str:
    """Return a complete shadcn/ui-compatible globals.css for this project.

    Pure function. Caller writes the file.
    """
    theme = (schema or {}).get("theme") or {}
    radius = (theme.get("radius") or "").strip() or "0.5rem"

    # Auto-correct foreground tokens before writing — prevents invisible text
    # when Gemini/Claude returns a dark palette with a dark foreground.
    theme = _fix_theme_contrast(theme, _DEFAULT_LIGHT)
    dark_theme = _fix_theme_contrast(theme.get("dark_mode") or {}, _DEFAULT_DARK)

    font_imports = _font_imports(theme)
    font_imports_block = ("\n".join(font_imports) + "\n") if font_imports else ""

    light_vars = _emit_var_block(theme, _DEFAULT_LIGHT)
    dark_vars = _emit_var_block(dark_theme, _DEFAULT_DARK)
    chart_vars_light = _emit_chart_vars(theme)
    chart_vars_dark = _emit_chart_vars(theme.get("dark_mode") or {})

    motion_block = _motion_signature_block(theme)
    font_classes = _font_family_classes(theme)

    # Reveal-up keyframe + .reveal-up class with stagger variants. Browsers
    # without animation-timeline get a static fallback (no animation, content
    # visible). prefers-reduced-motion is always honoured.
    reveal_block = (
        "/* ── Scroll-driven reveal utilities (CSS-first, framer-motion fallback) ── */\n"
        "@keyframes reveal-up {\n"
        "  from { opacity: 0; transform: translateY(24px); }\n"
        "  to   { opacity: 1; transform: translateY(0); }\n"
        "}\n"
        ".reveal-up { opacity: 1; }\n"
        "@supports (animation-timeline: view()) {\n"
        "  .reveal-up {\n"
        "    animation: reveal-up var(--d-base) var(--ease-sig) both;\n"
        "    animation-timeline: view();\n"
        "    animation-range: entry 10% cover 35%;\n"
        "  }\n"
        "  .reveal-up.stagger-1 { animation-delay: 80ms; }\n"
        "  .reveal-up.stagger-2 { animation-delay: 160ms; }\n"
        "  .reveal-up.stagger-3 { animation-delay: 240ms; }\n"
        "  .reveal-up.stagger-4 { animation-delay: 320ms; }\n"
        "}\n"
        "@media (prefers-reduced-motion: reduce) {\n"
        "  .reveal-up { animation: none !important; opacity: 1; transform: none; }\n"
        "}\n"
    )

    return (
        "@tailwind base;\n"
        "@tailwind components;\n"
        "@tailwind utilities;\n"
        "@import \"tw-animate-css\";\n"
        f"{font_imports_block}"
        "\n"
        "/* ── shadcn/ui Design Tokens (deterministic — written by ai_engine) ── */\n"
        "@layer base {\n"
        "  :root {\n"
        f"{light_vars}{chart_vars_light}\n"
        f"    --radius: {radius};\n"
        f"{motion_block}"
        "  }\n"
        "\n"
        "  .dark {\n"
        f"{dark_vars}{chart_vars_dark}\n"
        "  }\n"
        "}\n"
        "\n"
        "@layer base {\n"
        "  * {\n"
        "    @apply border-border;\n"
        "  }\n"
        "  html, body {\n"
        "    overflow-x: hidden;\n"
        "  }\n"
        "  html {\n"
        "    scroll-behavior: smooth;\n"
        "  }\n"
        "  body {\n"
        "    @apply bg-background text-foreground;\n"
        "    font-feature-settings: \"rlig\" 1, \"calt\" 1;\n"
        "  }\n"
        "  /* Sticky/fixed headers cover the top of the viewport when an\n"
        "     anchor link scrolls a section into view. scroll-margin-top\n"
        "     pushes the target down so the headline isn't hidden behind\n"
        "     the header. ~5rem covers our standard 64-80px header heights. */\n"
        "  section[id], div[id] {\n"
        "    scroll-margin-top: 5rem;\n"
        "  }\n"
        "}\n"
        "\n"
        f"{reveal_block}"
        f"\n{font_classes}"
    )
