"""Tests for the deterministic page-rhythm composer (the art-director step).

Global page decisions — surfaces, dark-band budget, flagships — are made
once in code before parallel codegen. These tests pin the designer rules.
"""
from app.services.landing_page_rhythm import (
    SURFACES,
    compose_page_rhythm,
    rhythm_prompt_block,
)


def _sections(*types):
    return [{"id": f"s{i}-{t}", "type": t} for i, t in enumerate(types)]


HOTEL = _sections(
    "hero", "trust_ticker", "rooms_showcase", "experience",
    "testimonials", "faq", "reservation", "newsletter",
)


class TestFixedRoles:
    def test_hero_is_media_and_conversion_is_band(self):
        plan = compose_page_rhythm(HOTEL, {}, seed="x")
        assert plan["s0-hero"]["surface"] == "media"
        assert plan["s7-newsletter"]["surface"] == "band"

    def test_first_section_is_media_even_if_mistyped(self):
        plan = compose_page_rhythm(_sections("intro", "story"), {}, seed="x")
        assert plan["s0-intro"]["surface"] == "media"


class TestLightAndAlternating:
    def test_no_two_adjacent_sections_share_a_surface(self):
        plan = compose_page_rhythm(HOTEL, {"surface_rhythm": "alternating"}, seed="hotel")
        ordered = [plan[s["id"]]["surface"] for s in HOTEL]
        for a, b in zip(ordered, ordered[1:]):
            assert a != b, f"adjacent surfaces equal: {ordered}"

    def test_light_mode_has_no_inverse_bands(self):
        plan = compose_page_rhythm(HOTEL, {"surface_rhythm": "light"}, seed="hotel")
        surfaces = {plan[s["id"]]["surface"] for s in HOTEL}
        assert "inverse" not in surfaces

    def test_alternating_mode_has_budgeted_inverse(self):
        plan = compose_page_rhythm(HOTEL, {"surface_rhythm": "alternating"}, seed="hotel")
        inverse_count = sum(1 for s in HOTEL if plan[s["id"]]["surface"] == "inverse")
        assert 1 <= inverse_count <= 2

    def test_unknown_rhythm_defaults_to_alternating(self):
        plan = compose_page_rhythm(HOTEL, {"surface_rhythm": "rainbow"}, seed="hotel")
        assert plan[HOTEL[1]["id"]]["mode"] == "alternating"


class TestDarkEditorial:
    def test_inverse_canvas_with_one_breather(self):
        dna = {"surface_rhythm": "dark-editorial"}
        plan = compose_page_rhythm(HOTEL, dna, seed="lux")
        surfaces = [plan[s["id"]]["surface"] for s in HOTEL]
        assert surfaces.count("base") == 1
        # everything else (minus hero media + conversion band) is inverse
        assert surfaces.count("inverse") == len(HOTEL) - 3


class TestFlagships:
    def test_flagship_gets_dark_band_and_airy_density(self):
        dna = {"surface_rhythm": "alternating", "flagship_sections": ["rooms_showcase"]}
        plan = compose_page_rhythm(HOTEL, dna, seed="hotel")
        flag = plan["s2-rooms_showcase"]
        assert flag["flagship"] is True
        assert flag["density"] == "airy"
        assert flag["surface"] == "inverse"

    def test_flagship_not_in_brief_is_ignored(self):
        dna = {"flagship_sections": ["pricing"]}
        plan = compose_page_rhythm(HOTEL, dna, seed="hotel")
        assert not any(plan[s["id"]]["flagship"] for s in HOTEL)


class TestDeterminism:
    def test_same_seed_same_plan(self):
        a = compose_page_rhythm(HOTEL, {"surface_rhythm": "alternating"}, seed="brand-a")
        b = compose_page_rhythm(HOTEL, {"surface_rhythm": "alternating"}, seed="brand-a")
        assert a == b

    def test_variety_hint_stable_and_bounded(self):
        plan = compose_page_rhythm(HOTEL, {}, seed="brand-a")
        for s in HOTEL:
            hint = plan[s["id"]]["variety_hint"]
            assert 0 <= hint <= 3

    def test_neighbors_recorded(self):
        plan = compose_page_rhythm(HOTEL, {}, seed="x")
        assert plan["s1-trust_ticker"]["prev_surface"] == "media"
        assert plan["s0-hero"]["prev_surface"] == ""
        assert plan["s7-newsletter"]["next_surface"] == ""


class TestPromptBlock:
    def test_block_quotes_surface_classes_and_neighbors(self):
        plan = compose_page_rhythm(HOTEL, {"surface_rhythm": "alternating"}, seed="x")
        block = rhythm_prompt_block(plan["s4-testimonials"])
        assert "ART DIRECTION" in block
        assert plan["s4-testimonials"]["surface_classes"] in block
        assert "Neighbors" in block and "Variety" in block

    def test_inverse_block_includes_contrast_warning(self):
        block = rhythm_prompt_block({
            "surface": "inverse",
            "surface_classes": SURFACES["inverse"],
            "density": "regular", "flagship": False,
            "variety_hint": 0, "prev_surface": "base", "next_surface": "tint",
        })
        assert "text-background" in block
        assert "NEVER text-foreground" in block

    def test_hero_block_defers_to_media_treatment(self):
        block = rhythm_prompt_block({
            "surface": "media", "surface_classes": SURFACES["media"],
            "density": "regular", "flagship": False,
            "variety_hint": 1, "prev_surface": "", "next_surface": "base",
        })
        assert "hero owns its media treatment" in block

    def test_empty_assignment_renders_nothing(self):
        assert rhythm_prompt_block(None) == ""
        assert rhythm_prompt_block({}) == ""


class TestEdgeCases:
    def test_empty_sections(self):
        assert compose_page_rhythm([], {}, seed="x") == {}

    def test_sections_without_ids_skipped(self):
        plan = compose_page_rhythm([{"type": "hero"}, {"id": "a", "type": "story"}], {}, seed="x")
        assert list(plan.keys()) == ["a"]
