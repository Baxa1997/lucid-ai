"""Tests for the Design Director → landing-brief bridge (landing_locked_design).

The landing pipeline historically used a Gemini-typed palette + lossy
foreground derivation in globals.css. This module maps the Director's locked,
contrast-validated design onto the brief. These tests pin the mapping, the
enum derivation onto the landing vocabularies, the radius class mapping, the
globals foreground preference, and the fail-soft no-op.
"""
import pytest

from app.services.landing_locked_design import (
    apply_locked_design,
    locked_design_prompt_block,
    _rem_to_rounded,
    _derive_design_system_enums,
)
from app.services.landing_phase0 import _palette_vars, _font_var, _font_weights


def _design(**overrides):
    base = {
        "design_system_name": "Cecilia Light",
        "archetype": "luxury_minimal + editorial_classic",
        "palette": {
            "background": "40 30% 97%", "foreground": "25 20% 14%",
            "primary": "30 45% 38%", "primary_foreground": "40 30% 97%",
            "secondary": "150 12% 35%", "secondary_foreground": "40 30% 97%",
            "accent": "38 60% 52%", "accent_foreground": "25 25% 12%",
            "muted": "38 22% 92%", "muted_foreground": "28 12% 42%",
            "card": "40 33% 99%", "card_foreground": "25 20% 14%",
            "border": "34 18% 86%", "ring": "30 45% 38%",
            "destructive": "0 70% 45%", "destructive_foreground": "0 0% 100%",
        },
        "radius": "0.5rem",
        "radius_tokens": {"button": "9999px", "input": "0.5rem", "card": "0.75rem",
                          "badge": "9999px", "image": "1rem", "modal": "0.75rem"},
        "typography": {
            "font_pairing_id": "instrument-serif-instrument-sans",
            "heading_font": "PLACEHOLDER", "body_font": "PLACEHOLDER",
            "heading_font_url": "", "body_font_url": "",
            "heading_weight": "", "body_weight": "",
            "type_scale": [13, 14, 16, 20, 25, 31, 39, 49, 61, 76],
            "overall_vibe": "editorial italic",
        },
        "spacing": {"base": 8, "section_padding_y": 112, "section_padding_x": 24, "rhythm": "airy-luxury"},
        "card_language": {"radius": "0.75rem", "border": "1px solid hsl(var(--border))",
                          "shadow": "sm", "padding": "p-8", "description": "Hairline cards."},
        "motion_language": {"duration": "450ms", "easing_signature": "quint-out",
                            "durations": {"fast": "180ms", "base": "520ms", "slow": "1000ms"},
                            "signature_transition": "duotone-fade", "cursor_treatment": "default"},
        "image_composition": {"overlay_pattern": "card_lift", "overlay_scrim_classes": "",
                              "overlay_text_color": "text-foreground",
                              "image_container_mode": "split_half", "form_treatment": "card_lift_solid"},
        "signature_motif": "thin gold hairline rule with a centered diamond",
        "border_radius_language": "soft", "color_application_strategy": "photographic-neutral",
    }
    base.update(overrides)
    return base


class TestRemToRounded:
    @pytest.mark.parametrize("value,expected", [
        ("0", "rounded-none"), ("0rem", "rounded-none"),
        ("0.375rem", "rounded-md"), ("0.5rem", "rounded-lg"),
        ("0.75rem", "rounded-xl"), ("1rem", "rounded-2xl"),
        ("1.5rem", "rounded-3xl"), ("9999px", "rounded-full"),
        ("12px", "rounded-xl"), ("", "rounded-lg"), ("garbage", "rounded-lg"),
    ])
    def test_buckets(self, value, expected):
        assert _rem_to_rounded(value) == expected


class TestEnumDerivation:
    def test_maps_onto_landing_vocab(self):
        from app.services.landing_brief import _ACCENT_SHAPE, _SURFACE, _MOTION, _RHYTHM
        e = _derive_design_system_enums(_design())
        assert e["accent_shape"] in _ACCENT_SHAPE
        assert e["surface"] in _SURFACE
        assert e["motion"] in _MOTION
        assert e["section_rhythm"] in _RHYTHM

    def test_pill_button_radius_becomes_pill_shape(self):
        assert _derive_design_system_enums(_design())["accent_shape"] == "pill"

    def test_square_button_radius_becomes_squared(self):
        d = _design(radius_tokens={"button": "0", "card": "0", "image": "0"})
        assert _derive_design_system_enums(d)["accent_shape"] == "squared"

    def test_shadow_means_elevated_surface(self):
        assert _derive_design_system_enums(_design())["surface"] == "elevated"

    def test_borderless_shadowless_means_flat(self):
        d = _design(card_language={"shadow": "none", "border": "none"})
        assert _derive_design_system_enums(d)["surface"] == "flat"

    def test_airy_rhythm_from_padding_when_name_unknown(self):
        d = _design(spacing={"base": 8, "section_padding_y": 128, "rhythm": "weird"})
        assert _derive_design_system_enums(d)["section_rhythm"] == "airy"


class TestApplyLockedDesign:
    def test_none_is_noop(self):
        brief = {"palette": {"primary": "x"}}
        assert apply_locked_design(brief, None) is False
        assert brief == {"palette": {"primary": "x"}}

    def test_palette_carries_validated_foregrounds(self):
        brief = {}
        assert apply_locked_design(brief, _design()) is True
        assert brief["palette"]["primary_foreground"] == "40 30% 97%"
        assert brief["palette"]["muted_foreground"] == "28 12% 42%"
        assert brief["palette"]["ring"] == "30 45% 38%"

    def test_typography_applies_curated_pairing(self):
        brief = {}
        apply_locked_design(brief, _design())
        assert brief["typography"]["heading_font"] == "Instrument Serif"
        assert brief["typography"]["body_font"] == "Instrument Sans"
        # placeholder values from the raw dict are overridden, not carried
        assert "PLACEHOLDER" not in brief["typography"].values()

    def test_tokens_use_director_radius(self):
        brief = {}
        apply_locked_design(brief, _design())
        assert brief["design_tokens"]["button_radius_class"] == "rounded-full"
        assert brief["design_tokens"]["image_radius_class"] == "rounded-2xl"
        assert "rounded-xl" in brief["design_tokens"]["card_class"]
        # card_class keeps exactly one radius token
        radii = [w for w in brief["design_tokens"]["card_class"].split() if w.startswith("rounded")]
        assert radii == ["rounded-xl"]

    def test_locked_design_stored_for_codegen(self):
        brief = {}
        d = _design()
        apply_locked_design(brief, d)
        assert brief["locked_design"] is d

    def test_partial_palette_is_skipped(self):
        # a design missing background/foreground must not half-write the palette
        brief = {"palette": {"primary": "keep"}}
        apply_locked_design(brief, _design(palette={"primary": "30 45% 38%"}))
        assert brief["palette"] == {"primary": "keep"}


class TestGlobalsForegroundPreference:
    def test_explicit_foregrounds_win_over_mirror(self):
        # the Director's validated muted_foreground must survive, not be
        # replaced by the legacy mirror (= foreground).
        palette = _design()["palette"]
        out = _palette_vars(palette)
        assert "--primary-foreground: 40 30% 97%;" in out
        assert "--muted-foreground: 28 12% 42%;" in out
        assert "--ring: 30 45% 38%;" in out

    def test_legacy_8key_palette_still_derives(self):
        # an old Gemini palette with no foregrounds falls back to mirroring
        legacy = {"background": "0 0% 100%", "foreground": "0 0% 10%",
                  "primary": "220 90% 56%", "muted": "0 0% 96%", "border": "0 0% 90%",
                  "card": "0 0% 100%", "secondary": "0 0% 40%", "accent": "30 80% 50%"}
        out = _palette_vars(legacy)
        assert "--primary-foreground: 0 0% 100%;" in out  # mirrors background
        assert "--muted-foreground: 0 0% 10%;" in out      # mirrors foreground


class TestFontAllowlist:
    @pytest.mark.parametrize("name,resolved", [
        ("Instrument Serif", "Instrument_Serif"),
        ("Instrument Sans", "Instrument_Sans"),
        ("Unbounded", "Unbounded"),
        ("Young Serif", "Young_Serif"),
        ("Bodoni Moda", "Bodoni_Moda"),
        ("Source Sans 3", "Source_Sans_3"),
        ("Archivo Black", "Archivo_Black"),
    ])
    def test_curated_pairing_fonts_resolve_not_inter(self, name, resolved):
        assert _font_var(name) == resolved

    def test_single_weight_fonts_constrained(self):
        assert _font_weights("Instrument_Serif") == ["400"]
        assert _font_weights("Young_Serif") == ["400"]
        assert _font_weights("Archivo_Black") == ["400"]


class TestPromptBlock:
    def test_empty_is_blank(self):
        assert locked_design_prompt_block(None) == ""
        assert locked_design_prompt_block({}) == ""

    def test_renders_radii_card_motion_image_motif(self):
        block = locked_design_prompt_block(_design())
        assert "LOCKED DESIGN SYSTEM" in block
        assert "rounded-xl" in block and "rounded-full" in block
        assert "card_lift" in block and "NEVER backdrop-blur" in block
        assert "thin gold hairline" in block
        assert "quint-out" in block


class TestRhythmPaletteCoupling:
    def _design(self, bg):
        return {"palette": {"background": bg, "foreground": "220 15% 12%",
                            "primary": "220 15% 12%", "primary_foreground": "0 0% 100%"}}

    def test_light_palette_keeps_dark_editorial(self):
        # dark-editorial on a LIGHT palette = the intentional dark treatment
        # (Pitch & Pavilion athletic look) — coherent, keep it.
        brief = {"visual_dna": {"surface_rhythm": "dark-editorial"}}
        apply_locked_design(brief, self._design("40 20% 97%"))
        assert brief["visual_dna"]["surface_rhythm"] == "dark-editorial"

    def test_dark_palette_demotes_dark_editorial(self):
        # dark-editorial inverts into a mostly-LIGHT page on a dark palette —
        # demote to alternating for a coherent dark-dominant page.
        brief = {"visual_dna": {"surface_rhythm": "dark-editorial"}}
        apply_locked_design(brief, self._design("220 18% 8%"))
        assert brief["visual_dna"]["surface_rhythm"] == "alternating"

    def test_light_rhythm_untouched(self):
        brief = {"visual_dna": {"surface_rhythm": "light"}}
        apply_locked_design(brief, self._design("40 20% 97%"))
        assert brief["visual_dna"]["surface_rhythm"] == "light"

    def test_no_visual_dna_is_safe(self):
        brief = {}
        assert apply_locked_design(brief, self._design("40 20% 97%")) is True
