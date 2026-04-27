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

    font_imports = _font_imports(theme)
    font_imports_block = ("\n".join(font_imports) + "\n") if font_imports else ""

    light_vars = _emit_var_block(theme, _DEFAULT_LIGHT)
    dark_vars = _emit_var_block(theme.get("dark_mode") or {}, _DEFAULT_DARK)
    chart_vars_light = _emit_chart_vars(theme)
    chart_vars_dark = _emit_chart_vars(theme.get("dark_mode") or {})

    font_classes = _font_family_classes(theme)

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
        "  body {\n"
        "    @apply bg-background text-foreground;\n"
        "    font-feature-settings: \"rlig\" 1, \"calt\" 1;\n"
        "  }\n"
        "}\n"
        f"\n{font_classes}"
    )
