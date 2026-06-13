"""landing_locked_design.py — bridge the Design Director into the landing path.

The landing pipeline historically used a Gemini-typed palette + a 5-enum design
system, and globals.css DERIVED the shadcn foregrounds by mirroring
background/foreground. That derivation is lossy: a light-ish primary gets a
near-white `primary-foreground`, shipping a low-contrast button — exactly the
"color mismatch" class of defect. Meanwhile `design_system_builder.py` (the
Design Director) already produces a locked, CONTRAST-VALIDATED design system,
but it was only wired into the multi-page website path, never landing.

This module is the bridge. Given the Director's design dict, it maps the parts
the landing pipeline consumes onto the brief, so every downstream step (globals
writer, layout/font writer, parallel section codegen) sees ONE locked source of
truth instead of per-section drift:

  • palette        → brief["palette"] with the Director's validated foregrounds
                     (globals.css then uses them verbatim — no mirror-derivation)
  • typography     → brief["typography"] from the Director's curated font pairing
  • design_system  → brief["design_system"] 5-enum, derived from the Director's
                     radius / surface / motion language (codegen tokens stay
                     consistent with the locked radius language)
  • locked_design  → brief["locked_design"] = full design, surfaced to section
                     codegen as an authoritative card / motion / image recipe

Pure and fail-soft: `apply_locked_design(brief, None)` is a no-op returning
False, so a Director call that fails leaves the legacy flow untouched.
"""
from __future__ import annotations

from typing import Any


# ── radius rem/px → Tailwind rounded-* class ────────────────────────────────
# Buckets ordered by ascending size; we pick the nearest bucket so an
# off-scale Director value (0.625rem) still lands on a real Tailwind class.
_ROUNDED_BUCKETS: list[tuple[float, str]] = [
    (0.0,   "rounded-none"),
    (0.125, "rounded-sm"),
    (0.25,  "rounded"),
    (0.375, "rounded-md"),
    (0.5,   "rounded-lg"),
    (0.75,  "rounded-xl"),
    (1.0,   "rounded-2xl"),
    (1.5,   "rounded-3xl"),
]


def _rem_to_rounded(value: str) -> str:
    """Map a radius token ('0.375rem', '12px', '9999px') to a Tailwind class."""
    raw = (value or "").strip().lower()
    if not raw:
        return "rounded-lg"
    if raw in ("9999px", "999px", "100%", "full") or "9999" in raw:
        return "rounded-full"
    # Normalize px → rem (Tailwind base 16px).
    try:
        if raw.endswith("px"):
            rem = float(raw[:-2]) / 16.0
        elif raw.endswith("rem"):
            rem = float(raw[:-3])
        else:
            rem = float(raw)
    except ValueError:
        return "rounded-lg"
    if rem >= 2.0:
        return "rounded-full"
    return min(_ROUNDED_BUCKETS, key=lambda b: abs(b[0] - rem))[1]


# ── Director language → landing 5-enum design_system ────────────────────────
# These map onto the EXACT enum vocabularies in landing_brief (_ACCENT_SHAPE,
# _SURFACE, _MOTION, _RHYTHM) so the existing _build_design_tokens keeps
# producing valid class strings — now driven by the Director's choices.
_RADIUS_LANG_TO_SHAPE = {
    "sharp": "squared", "crisp": "rounded", "soft": "rounded",
    "pill": "pill", "organic": "blob", "mixed": "rounded",
}
_RHYTHM_TO_RHYTHM = {
    "tight-editorial": "tight", "dense-information": "tight",
    "standard-modern": "balanced", "asymmetric": "balanced",
    "airy-luxury": "airy",
}
# signature_transition / easing → landing motion enum
_MOTION_ENERGETIC = {"kinetic-typography", "chromatic-glitch", "magnetic-pull", "horizontal-rail"}
_MOTION_DRAMATIC = {"parallax-layered", "sticky-pin-scrub", "mask-reveal-diag"}
_MOTION_ORGANIC = {"duotone-fade", "weight-shift"}


def _derive_design_system_enums(design: dict[str, Any]) -> dict[str, str]:
    rt = design.get("radius_tokens") or {}
    card = design.get("card_language") or {}
    motion = design.get("motion_language") or {}
    spacing = design.get("spacing") or {}

    # accent_shape — button radius wins when it's an extreme (pill / square),
    # else the named radius language.
    button_radius = (rt.get("button") or "").strip().lower()
    if button_radius and _rem_to_rounded(button_radius) == "rounded-full":
        accent_shape = "pill"
    elif button_radius and _rem_to_rounded(button_radius) == "rounded-none":
        accent_shape = "squared"
    else:
        lang = (design.get("border_radius_language") or "").strip().lower()
        accent_shape = _RADIUS_LANG_TO_SHAPE.get(lang, "rounded")

    # surface — shadow present → elevated; border-only → bordered; neither → flat
    shadow = (card.get("shadow") or "").strip().lower()
    border = (card.get("border") or "").strip().lower()
    if shadow and shadow not in ("none", "0", ""):
        surface = "elevated"
    elif border and border not in ("none", "0", ""):
        surface = "bordered"
    else:
        surface = "flat"

    # motion — from the signature transition fingerprint
    sig = (motion.get("signature_transition") or "").strip().lower()
    if sig in _MOTION_ENERGETIC:
        motion_enum = "energetic"
    elif sig in _MOTION_DRAMATIC:
        motion_enum = "dramatic"
    elif sig in _MOTION_ORGANIC:
        motion_enum = "organic"
    else:
        motion_enum = "subtle"

    # section_rhythm — from spacing rhythm name, with a px backstop
    rhythm = _RHYTHM_TO_RHYTHM.get((spacing.get("rhythm") or "").strip().lower(), "")
    if not rhythm:
        spy = spacing.get("section_padding_y") or 0
        rhythm = "airy" if spy and spy >= 120 else "balanced"

    # image_treatment — only "duotone" is a meaningful non-default here
    strategy = (design.get("color_application_strategy") or "").strip().lower()
    image_treatment = "duotone" if "duotone" in strategy else "natural"

    return {
        "accent_shape": accent_shape,
        "surface": surface,
        "motion": motion_enum,
        "section_rhythm": rhythm,
        "image_treatment": image_treatment,
    }


# Palette keys the landing pipeline carries. The first 8 feed globals.css
# directly; the *_foreground / ring / destructive keys are the Director's
# VALIDATED values that replace globals.css's lossy mirror-derivation.
_PALETTE_KEYS = (
    "background", "foreground", "primary", "primary_foreground",
    "secondary", "secondary_foreground", "accent", "accent_foreground",
    "muted", "muted_foreground", "card", "card_foreground",
    "border", "ring", "destructive", "destructive_foreground",
)


def _hsl_lightness(hsl: str) -> float | None:
    """Parse the L (0-100) out of a bare HSL token '40 20% 97%'. None on miss."""
    parts = (hsl or "").strip().split()
    if len(parts) < 3:
        return None
    try:
        return float(parts[2].replace("%", ""))
    except ValueError:
        return None


def _couple_rhythm_to_palette(brief: dict[str, Any], background_hsl: str) -> None:
    """Keep the page's light/dark character COHERENT with the locked palette.

    surface_rhythm (light | alternating | dark-editorial) is chosen by research,
    independently of the Director's palette — so the two can disagree. This does
    NOT force a colour direction (the Director picks light or dark per domain);
    it only repairs the ONE genuinely-broken combination.

    `dark-editorial` paints most sections with the `inverse` surface
    (bg-foreground). On a LIGHT palette the foreground is dark → a coherent
    dark-dominant page (the intentional "dark editorial treatment" — e.g. the
    athletic-performance look; keep it). On a genuinely DARK palette the
    foreground is light, so dark-editorial inverts into a mostly-LIGHT page on a
    dark brand — incoherent. In that case demote to `alternating` (dark `base`
    canvas + a couple of light accent bands = a coherent dark-dominant page).
    Light palettes keep whatever rhythm research chose; all three are coherent
    there.
    """
    lightness = _hsl_lightness(background_hsl)
    if lightness is None or lightness >= 50:
        return  # light / unknown palette — every rhythm is coherent
    vd = brief.get("visual_dna")
    if not isinstance(vd, dict):
        return
    if (vd.get("surface_rhythm") or "").strip().lower() == "dark-editorial":
        vd["surface_rhythm"] = "alternating"


def apply_locked_design(brief: dict[str, Any], design: dict[str, Any] | None) -> bool:
    """Map the Director's locked design onto the brief. Mutates in place.

    Returns True when applied, False when `design` is empty (no-op). Each
    sub-mapping is guarded so a partial design dict still applies what it can.
    """
    if not design or not isinstance(design, dict):
        return False

    # ── palette: carry the Director's validated tokens verbatim ──────────
    src_palette = design.get("palette") or {}
    if src_palette.get("background") and src_palette.get("foreground"):
        brief["palette"] = {
            k: src_palette[k] for k in _PALETTE_KEYS if src_palette.get(k)
        }
        # Repair the one incoherent combo (dark palette + dark-editorial);
        # never forces a colour direction.
        _couple_rhythm_to_palette(brief, src_palette["background"])

    # ── typography: curated pairing (heading/body/urls/weights/scale) ────
    try:
        from app.services.design_system_builder import _apply_font_pairing
        t = _apply_font_pairing(design.get("typography") or {})
    except Exception:
        t = design.get("typography") or {}
    if t.get("heading_font") and t.get("body_font"):
        brief["typography"] = {
            "heading_font": t.get("heading_font", "Inter"),
            "body_font": t.get("body_font", "Inter"),
            "heading_font_url": t.get("heading_font_url", ""),
            "body_font_url": t.get("body_font_url", ""),
            "heading_weight": t.get("heading_weight", "700"),
            "body_weight": t.get("body_weight", "400"),
            "heading_style": t.get("heading_style", "normal"),
            "type_scale": t.get("type_scale") or [],
            "scale": "balanced",
        }

    # ── design_system enums + tokens (keep the two in lockstep) ──────────
    enums = _derive_design_system_enums(design)
    brief["design_system"] = enums
    try:
        from app.services.landing_brief import _build_design_tokens
        tokens = _build_design_tokens(enums)
        # Pin the precise per-element radii from the Director's radius_tokens
        # so cards/buttons/images share the locked radius language exactly,
        # instead of the coarse enum→class approximation.
        rt = design.get("radius_tokens") or {}
        if rt.get("button"):
            tokens["button_radius_class"] = _rem_to_rounded(rt["button"])
        if rt.get("image"):
            tokens["image_radius_class"] = _rem_to_rounded(rt["image"])
        if rt.get("card"):
            card_radius = _rem_to_rounded(rt["card"])
            # card_class is "<surface classes> <radius>" — swap the radius token.
            base = " ".join(
                w for w in (tokens.get("card_class") or "").split()
                if not w.startswith("rounded")
            )
            tokens["card_class"] = f"{base} {card_radius}".strip()
        brief["design_tokens"] = tokens
    except Exception:
        pass

    # ── full design for the codegen prompt block ─────────────────────────
    brief["locked_design"] = design
    return True


def locked_design_prompt_block(design: dict[str, Any] | None) -> str:
    """Render the locked design as an authoritative codegen recipe block.

    Surfaced in every section's system prompt so parallel codegen shares one
    card / radius / motion / image-composition language instead of each
    section reinventing it (the "not solid" drift).
    """
    if not design or not isinstance(design, dict):
        return ""
    rt = design.get("radius_tokens") or {}
    card = design.get("card_language") or {}
    motion = design.get("motion_language") or {}
    ic = design.get("image_composition") or {}
    spacing = design.get("spacing") or {}

    lines = ["", "LOCKED DESIGN SYSTEM (authoritative — every section shares this exact language):"]
    name = design.get("design_system_name") or ""
    archetype = design.get("archetype") or ""
    if name or archetype:
        lines.append(f"  System: {name} — {archetype}")

    # Radii — the single biggest consistency tell when it drifts.
    if rt:
        lines.append(
            f"  Radii (use these EXACT Tailwind classes, never improvise): "
            f"cards {_rem_to_rounded(rt.get('card', ''))}, "
            f"buttons {_rem_to_rounded(rt.get('button', ''))}, "
            f"inputs {_rem_to_rounded(rt.get('input', ''))}, "
            f"images {_rem_to_rounded(rt.get('image', ''))}."
        )

    # Card recipe — one card language across the whole page.
    if card:
        recipe = card.get("description") or ""
        shadow = (card.get("shadow") or "none").strip().lower()
        shadow_class = {"none": "no shadow", "sm": "shadow-sm", "md": "shadow-md"}.get(shadow, f"shadow-{shadow}")
        border = (card.get("border") or "").strip().lower()
        border_note = "hairline border-border" if border and border not in ("none", "0") else "no border"
        lines.append(
            f"  Cards: bg-card, {border_note}, {shadow_class}, padding {card.get('padding', 'p-6')}. "
            + (f"{recipe}" if recipe else "Every card on the page uses this — do not vary border/shadow per section.")
        )

    # Motion — one transition language.
    if motion:
        durs = motion.get("durations") or {}
        base_dur = durs.get("base") or motion.get("duration") or "450ms"
        lines.append(
            f"  Motion: standard reveal {base_dur}, easing {motion.get('easing_signature', 'quint-out')}. "
            "Reuse this one transition — no per-section animation styles."
        )

    # Spacing rhythm.
    spy = spacing.get("section_padding_y")
    if spy:
        lines.append(f"  Section vertical padding target: ~{spy}px ({spacing.get('rhythm', 'standard-modern')} rhythm).")

    # Image-over-text composition — the validated contrast rule.
    ovp = (ic.get("overlay_pattern") or "").strip()
    if ovp:
        scrim = ic.get("overlay_scrim_classes") or ""
        text = ic.get("overlay_text_color") or "text-foreground"
        form = ic.get("form_treatment") or "card_lift_solid"
        detail = f" scrim `{scrim}`," if scrim else ""
        lines.append(
            f"  Text-over-image: use `{ovp}` ({text}{detail} container "
            f"{ic.get('image_container_mode', 'centered')}). Forms over imagery: `{form}` — "
            "NEVER backdrop-blur glass over a photo (inputs become unreadable)."
        )

    motif = design.get("signature_motif") or ""
    if motif:
        lines.append(f"  Signature motif (recurs 2-3x across the page, subtle): {motif}.")

    lines.append(
        "  This locked system OVERRIDES any conflicting radius/shadow/motion instinct — "
        "consistency across sections is what makes the page read as professionally designed."
    )
    return "\n".join(lines) + "\n"
