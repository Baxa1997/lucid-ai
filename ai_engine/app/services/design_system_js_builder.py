"""Deterministic src/lib/design-system.js builder.

Reads the project's specific design tokens — picked by the Design Director
LLM call per project — and translates them into concrete Tailwind classnames
+ framer-motion variants written directly into ``src/lib/design-system.js``.

No preset library. No hash-based picking. Every per-project visual decision
(card radius, shadow, padding, button radius, motion enter style, section
padding, container width) flows from Design Director's per-project spec
into the file. Two coffee shops with two different Design Director runs
get two different design-system.js files.

Components in Phase 2/3 are told to ``import { ds } from '@/lib/design-system'``
and use ``ds.card``, ``ds.buttonPrimary``, ``ds.section``, etc. instead of
inventing their own classnames.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


# ── Translation primitives ─────────────────────────────────────────────
# These are NOT design choices. They translate a per-project value (like
# "0.75rem" or "lg") into the canonical Tailwind class that represents it.
# Every value below is the deterministic mapping of one CSS unit to its
# Tailwind utility — no opinion about which radius/shadow is "best."

_RADIUS_REM_TO_CLASS: tuple[tuple[float, str], ...] = (
    (0.0,    "rounded-none"),
    (0.125,  "rounded-sm"),
    (0.25,   "rounded"),
    (0.375,  "rounded-md"),
    (0.5,    "rounded-lg"),
    (0.75,   "rounded-xl"),
    (1.0,    "rounded-2xl"),
    (1.5,    "rounded-3xl"),
)


def _radius_to_class(value: str) -> str:
    """Translate a CSS radius value ('0.75rem' / '12px' / '9999px' / 'full')
    into the closest Tailwind ``rounded-*`` class. Empty / unparseable input
    returns ``rounded-lg`` — the shadcn default — only as a last-resort floor.
    """
    v = (value or "").strip().lower()
    if not v:
        return "rounded-lg"
    if v in ("full", "pill", "9999px") or v.endswith(("9999px", "999rem")):
        return "rounded-full"
    if v in ("none", "0", "0rem", "0px"):
        return "rounded-none"
    # Already a Tailwind class — pass through
    if v.startswith("rounded"):
        return v
    # Parse rem / px → rem
    m = re.match(r"^([\d.]+)\s*(rem|px)?$", v)
    if not m:
        return "rounded-lg"
    n = float(m.group(1))
    unit = m.group(2) or "rem"
    rem = n if unit == "rem" else n / 16.0
    # Find nearest preset (linear scan; the table is tiny)
    nearest = min(_RADIUS_REM_TO_CLASS, key=lambda kv: abs(kv[0] - rem))
    return nearest[1]


def _shadow_to_class(value: str) -> str:
    """Translate a shadow spec ('none' / 'sm' / 'md' / 'lg' / 'xl' / explicit
    Tailwind class / CSS box-shadow) into a single Tailwind class. Returns
    empty string when no shadow."""
    v = (value or "").strip().lower()
    if not v or v == "none":
        return ""
    if v.startswith("shadow"):
        return v
    if v in ("sm", "md", "lg", "xl", "2xl", "inner"):
        return f"shadow-{v}"
    # Unknown CSS shadow string — render generic shadow-md, conservative.
    return "shadow-md"


def _border_to_class(value: str) -> str:
    """Translate a border spec into a Tailwind class. Empty string for 'none'."""
    v = (value or "").strip().lower()
    if not v or v == "none":
        return ""
    # If it includes a width hint
    if "2px" in v or "thick" in v:
        return "border-2 border-border"
    if "1px" in v or "hairline" in v or "border" in v:
        return "border border-border"
    # Anything else: assume hairline default
    return "border border-border"


def _padding_to_class(value: str) -> str:
    """Translate a padding spec into a Tailwind class."""
    v = (value or "").strip().lower()
    if not v:
        return "p-6"
    if v.startswith("p-"):
        return v
    if v.endswith("px"):
        try:
            px = int(float(v[:-2]))
            if px <= 12: return "p-3"
            if px <= 16: return "p-4"
            if px <= 20: return "p-5"
            if px <= 28: return "p-6"
            if px <= 36: return "p-8"
            return "p-10"
        except ValueError:
            return "p-6"
    return "p-6"


# ── Motion translation ─────────────────────────────────────────────────

def _parse_duration_to_seconds(value: str) -> float:
    """'300ms' / '0.45s' / '450' → seconds (float). Default 0.5."""
    v = (value or "").strip().lower()
    if not v:
        return 0.5
    m = re.match(r"^([\d.]+)\s*(ms|s)?$", v)
    if not m:
        return 0.5
    n = float(m.group(1))
    unit = m.group(2) or "ms"
    return n / 1000.0 if unit == "ms" else n


_EASING_MAP: dict[str, Any] = {
    "linear": "linear",
    "ease": "easeInOut",
    "ease-in": "easeIn",
    "ease-out": "easeOut",
    "ease-in-out": "easeInOut",
}


def _parse_easing(value: str) -> Any:
    """CSS easing → framer-motion easing (string or [x1,y1,x2,y2]). Default
    'easeOut'."""
    v = (value or "").strip().lower()
    if not v:
        return "easeOut"
    if v in _EASING_MAP:
        return _EASING_MAP[v]
    # cubic-bezier(...)
    m = re.match(r"^cubic-bezier\(\s*([^)]+)\)$", v)
    if m:
        try:
            parts = [float(x.strip()) for x in m.group(1).split(",")]
            if len(parts) == 4:
                return parts
        except ValueError:
            pass
    return "easeOut"


def _parse_enter_motion(enter: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Parse a per-project enter description ('fade-up stagger 80ms',
    'slide-in-from-left', 'scale-in', 'blur-in', 'fade') into framer-motion
    initial + animate variant dicts.

    The Design Director picks the descriptor; this function deterministically
    translates the descriptor into concrete coordinates. Same descriptor →
    same variant; different descriptor → different feel.
    """
    e = (enter or "").strip().lower()
    if not e:
        return ({"opacity": 0, "y": 16}, {"opacity": 1, "y": 0})

    initial: dict[str, Any] = {"opacity": 0}
    animate: dict[str, Any] = {"opacity": 1}

    if "fade-up" in e or "from-bottom" in e:
        initial["y"] = 24
        animate["y"] = 0
    elif "fade-down" in e or "from-top" in e:
        initial["y"] = -24
        animate["y"] = 0
    elif "from-left" in e or "slide-right" in e:
        initial["x"] = -32
        animate["x"] = 0
    elif "from-right" in e or "slide-left" in e:
        initial["x"] = 32
        animate["x"] = 0
    elif "scale" in e or "zoom" in e:
        initial["scale"] = 0.96
        animate["scale"] = 1
    elif "blur" in e:
        initial["filter"] = "blur(8px)"
        animate["filter"] = "blur(0px)"
    elif "fade" in e:
        # plain fade — no offset
        pass
    else:
        # Unknown descriptor — gentle fade-up default
        initial["y"] = 16
        animate["y"] = 0

    return (initial, animate)


def _build_motion_variant(motion_lang: dict[str, Any]) -> dict[str, Any]:
    """Build the full framer-motion props object from Design Director's
    motion_language dict. Always returns valid props; missing fields use
    sensible per-project-derived defaults."""
    enter = motion_lang.get("enter", "")
    duration = _parse_duration_to_seconds(motion_lang.get("duration", ""))
    ease = _parse_easing(motion_lang.get("easing", ""))

    initial, animate = _parse_enter_motion(enter)

    return {
        "initial": initial,
        "whileInView": animate,
        "viewport": {"once": True, "margin": "-80px"},
        "transition": {"duration": round(duration, 3), "ease": ease},
    }


# ── Section / container sizing from spacing spec ──────────────────────

# Tailwind's default padding scale only includes specific steps. Anything
# outside this set ('py-17', 'py-45') won't compile. We snap the
# Design-Director-provided pixel value to its nearest valid step.
_TAILWIND_PADDING_STEPS: tuple[int, ...] = (
    0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 14, 16, 20, 24, 28, 32, 36, 40, 44, 48, 52, 56, 60, 64, 72, 80, 96
)


def _snap_to_padding_step(px: int) -> int:
    """Snap an arbitrary px value to the nearest valid Tailwind padding step."""
    rem_step_target = max(0, px) / 4  # 1 step = 4px
    return min(_TAILWIND_PADDING_STEPS, key=lambda s: abs(s - rem_step_target))


def _section_padding_class(spacing: dict[str, Any]) -> str:
    """Build the section vertical-padding Tailwind class from Design Director's
    ``spacing.section_padding_y`` (px). Mobile uses the base value; md: bumps
    ~40% up to the next valid Tailwind step.
    """
    raw = spacing.get("section_padding_y", 0)
    try:
        py = int(raw)
    except (TypeError, ValueError):
        py = 96
    base_step = _snap_to_padding_step(py)
    md_step = _snap_to_padding_step(int(py * 1.4))
    if md_step <= base_step:
        return f"py-{base_step}"
    return f"py-{base_step} md:py-{md_step}"


def _container_class(layout_archetype: str, brand_placement: str) -> str:
    """Container max-width derived from the project's layout intent.
    Admin-family archetypes always get wide; portfolios narrow; everything
    else default to standard. Brand placement at navbar_center hints
    editorial → narrower."""
    a = (layout_archetype or "").lower()
    if a in {"admin_dashboard", "crm", "tms", "saas_dashboard", "ecommerce"}:
        return "max-w-7xl mx-auto px-4 sm:px-6 lg:px-8"
    if a == "portfolio":
        return "max-w-5xl mx-auto px-4 sm:px-6"
    if (brand_placement or "").lower() in ("navbar_center", "split_navbar_header"):
        return "max-w-6xl mx-auto px-4 sm:px-6 lg:px-8"
    return "max-w-7xl mx-auto px-4 sm:px-6 lg:px-8"


# ── Main entry ────────────────────────────────────────────────────────

_BUTTON_BASE_CLASSES = (
    "inline-flex items-center justify-center gap-2 px-6 py-3 "
    "text-sm font-semibold transition-all duration-200 "
    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
)

# Card hover: per-project derived from the project's hover_interaction_style
# field, which Design Director picks. Pure translation, not preset-pick.
def _card_hover_class(hover_interaction_style: str) -> str:
    h = (hover_interaction_style or "").lower()
    if "lift" in h or "shadow" in h:
        return "transition-all duration-300 hover:shadow-lg hover:-translate-y-0.5"
    if "tilt" in h or "3d" in h:
        return "transition-transform duration-300 hover:-rotate-1 hover:scale-[1.01]"
    if "glow" in h or "ring" in h:
        return "transition-shadow duration-300 hover:shadow-[0_0_0_4px_hsl(var(--ring)/0.18)]"
    if "invert" in h:
        return "transition-colors duration-200 hover:bg-foreground hover:text-background"
    if "morph" in h:
        return "transition-all duration-300 hover:scale-[1.02]"
    if "magnetic" in h:
        return "transition-transform duration-200 hover:scale-[1.015]"
    if "reveal" in h:
        return "transition-colors duration-200 hover:bg-muted/50"
    # No specific hint — gentle default. NOT a hardcoded design choice;
    # just a neutral baseline interaction so cards aren't dead.
    return "transition-colors duration-200 hover:border-primary/40"


def build_design_system_js(
    schema: dict[str, Any],
    description: str,
    archetype: str,
    design: dict[str, Any] | None = None,
) -> tuple[str, dict[str, str]]:
    """Return ``(file_contents, picks)`` where ``picks`` summarizes the
    actual translated values for logging / progress display.

    ``design`` is the Design Director's full output dict. When absent
    (cache hit, timeout), we fall back to schema's ``theme`` and
    ``design_system`` fields so the file is always written with at least
    minimal coherence — but those fallback paths are intentionally narrow:
    the goal is for Design Director to drive every decision.
    """
    design = design or {}
    theme = (schema or {}).get("theme") or {}

    # Per-project radius — Design Director provides per-element tokens.
    radius_tokens = design.get("radius_tokens") or {}
    base_radius = (design.get("radius") or theme.get("radius") or "0.5rem").strip()

    card_radius = _radius_to_class(radius_tokens.get("card") or base_radius)
    button_radius = _radius_to_class(radius_tokens.get("button") or base_radius)
    badge_radius = _radius_to_class(radius_tokens.get("badge") or "9999px")

    # Per-project card chrome
    card_lang = design.get("card_language") or {}
    card_shadow = _shadow_to_class(card_lang.get("shadow", ""))
    card_border = _border_to_class(card_lang.get("border", ""))
    card_padding = _padding_to_class(card_lang.get("padding", ""))

    card_classes = " ".join(
        c for c in (card_radius, "bg-card", card_border, card_shadow, card_padding) if c
    )

    card_hover = _card_hover_class(design.get("hover_interaction_style", ""))

    # Per-project motion variant
    motion = _build_motion_variant(design.get("motion_language") or {})

    # Per-project section + container sizing
    spacing = design.get("spacing") or {}
    section_padding = _section_padding_class(spacing)
    brand_placement = (design.get("brand_mark") or {}).get("placement", "")
    container = _container_class(archetype, brand_placement)

    # Buttons — base utilities + per-project radius
    button_primary = (
        f"{_BUTTON_BASE_CLASSES} {button_radius} bg-primary text-primary-foreground "
        "shadow-sm hover:bg-primary/90 hover:shadow-md"
    )
    button_secondary = (
        f"{_BUTTON_BASE_CLASSES} {button_radius} border border-border bg-background "
        "text-foreground hover:bg-muted"
    )
    button_ghost = (
        f"{_BUTTON_BASE_CLASSES} {button_radius} text-foreground hover:bg-muted"
    )

    # Status badges — pass through schema, with per-project radius
    badges = (schema.get("status_badges") or {}) if schema else {}
    badge_default = (
        f"inline-flex items-center gap-1 px-2.5 py-0.5 text-xs font-medium {badge_radius} "
        "bg-muted text-muted-foreground"
    )

    ds_name = (
        design.get("design_system_name")
        or ((schema.get("design_system") or {}).get("name") or "").strip()
        or "Project"
    )

    # Summary surfaced in logs / WS progress. Not used by the JS file itself.
    picks = {
        "card_radius": card_radius,
        "card_shadow": card_shadow or "none",
        "card_border": card_border or "none",
        "button_radius": button_radius,
        "motion_enter": (design.get("motion_language") or {}).get("enter", "default-fade-up"),
        "section_padding": section_padding,
        "brand_placement": brand_placement or "default",
    }

    motion_json = json.dumps(motion, indent=2).replace("\n", "\n  ")
    badge_js_pairs = ",\n    ".join(
        f"{json.dumps(k)}: {json.dumps(v)}" for k, v in badges.items()
    )
    badges_block = "{\n    " + badge_js_pairs + ",\n  }" if badge_js_pairs else "{}"

    contents = (
        f"// Design System — {ds_name} (deterministic; written by ai_engine)\n"
        f"// All values translated from Design Director's per-project spec.\n"
        f"// Card: {picks['card_radius']} + {picks['card_shadow']} + {picks['card_border']}; "
        f"button radius: {picks['button_radius']}; motion: {picks['motion_enter']}; "
        f"section: {picks['section_padding']}\n"
        "//\n"
        "// Import to keep visual identity consistent across components:\n"
        "//   import { ds } from '@/lib/design-system';\n"
        "//\n"
        "// Components MUST consume ds.card, ds.buttonPrimary, ds.section, ds.container,\n"
        "// ds.heading, ds.body, ds.motion. Inventing classnames per file breaks the\n"
        "// project's coherent design language.\n"
        "\n"
        "export const ds = {\n"
        f"  card: {json.dumps(card_classes)},\n"
        f"  cardHover: {json.dumps(card_hover)},\n"
        f"  cardInteractive: {json.dumps(card_classes + ' ' + card_hover)},\n"
        "\n"
        f"  buttonPrimary: {json.dumps(button_primary)},\n"
        f"  buttonSecondary: {json.dumps(button_secondary)},\n"
        f"  buttonGhost: {json.dumps(button_ghost)},\n"
        f"  buttonRadius: {json.dumps(button_radius)},\n"
        "\n"
        f"  section: {json.dumps(section_padding)},\n"
        f"  container: {json.dumps(container)},\n"
        "\n"
        "  heading: 'font-heading',\n"
        "  body: 'font-body',\n"
        "\n"
        f"  badge: {json.dumps(badge_default)},\n"
        f"  badgeVariants: {badges_block},\n"
        "\n"
        f"  motion: {motion_json},\n"
        "\n"
        "  motionStagger: {\n"
        "    container: { initial: 'hidden', whileInView: 'visible', viewport: { once: true, margin: '-80px' }, variants: { visible: { transition: { staggerChildren: 0.08 } } } },\n"
        "    item: { variants: { hidden: { opacity: 0, y: 16 }, visible: { opacity: 1, y: 0, transition: { duration: 0.4, ease: [0.22, 1, 0.36, 1] } } } },\n"
        "  },\n"
        "};\n"
        "\n"
        "export default ds;\n"
    )
    return contents, picks
