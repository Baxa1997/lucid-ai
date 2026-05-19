"""Tests for the admin brand-signal extractor.

Two layers (mirrors `test_admin_data_model_planner.py`):
  • Unit tests — mock `structured_distill`; cover the happy path, the
    partial/invalid response path, and the Gemini-failure fallback.
  • Live tests — gated on `@pytest.mark.live`. Run real Gemini Flash
    against three domain prompts (medical clinic, creative agency, B2B
    SaaS) and assert the extractor produces sensibly-typed signals.

Live cost is ~$0.005/call × 3 = ~$0.015 per pytest invocation.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _load_env_file(path: str) -> None:
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env_file(str(Path(__file__).resolve().parents[2] / ".env"))


from app.services.admin_brand_extractor import (  # noqa: E402
    _DEFAULT_SIGNALS,
    _VALID_DENSITY,
    _VALID_INTENSITY,
    _VALID_MOTIF,
    _VALID_TYPOGRAPHY,
    _coerce_signals,
    extract_admin_brand_signals,
)


def _have_gemini() -> bool:
    return bool(
        os.environ.get("GOOGLE_API_KEY", "").strip()
        or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    )


skip_no_gemini = pytest.mark.skipif(
    not _have_gemini(),
    reason="No Gemini credentials (set GOOGLE_API_KEY or ADC)",
)


# ── Helpers ──────────────────────────────────────────────────────────

_REQUIRED_KEYS = {
    "brand_name",
    "primary_color",
    "typography_voice",
    "cultural_intensity",
    "layout_density",
    "accent_motif",
}


def _gemini_json(signals: dict) -> str:
    """Wrap a dict into the JSON Gemini would return (no code fences)."""
    import json
    return json.dumps(signals)


# ── Unit tests (no live Gemini) ──────────────────────────────────────

class TestExtractorUnit:
    @pytest.mark.asyncio
    async def test_returns_required_fields(self):
        fake = _gemini_json({
            "primary_color":      "#3b82f6",
            "typography_voice":   "professional",
            "cultural_intensity": "calm",
            "layout_density":     "comfortable",
            "accent_motif":       "geometric",
        })
        with patch(
            "app.services.admin_brand_extractor.structured_distill",
            new=AsyncMock(return_value=fake),
        ):
            result = await extract_admin_brand_signals(
                intent={"brand": {"name": "Acme Ops"}, "business_category": "ops"},
                purpose_data={"industry": "logistics",
                              "target_audience": "fleet managers",
                              "primary_purpose": "ops"},
                gemini_key="dummy",
            )
        assert set(result.keys()) == _REQUIRED_KEYS, result
        assert result["brand_name"] == "Acme Ops"
        assert result["primary_color"] == "#3b82f6"
        assert result["typography_voice"] == "professional"

    @pytest.mark.asyncio
    async def test_handles_missing_purpose_data(self):
        """Empty purpose_data must not break extraction — defaults fill in."""
        fake = _gemini_json({
            "primary_color":      "#10b981",
            "typography_voice":   "minimal",
            "cultural_intensity": "calm",
            "layout_density":     "spacious",
            "accent_motif":       "minimal",
        })
        with patch(
            "app.services.admin_brand_extractor.structured_distill",
            new=AsyncMock(return_value=fake),
        ):
            result = await extract_admin_brand_signals(
                intent={"business_category": "internal"},
                purpose_data={},  # nothing here
                gemini_key="",
            )
        assert set(result.keys()) == _REQUIRED_KEYS
        assert result["brand_name"] == "internal"

    @pytest.mark.asyncio
    async def test_extracts_hex_color_format(self):
        """Coercer must normalize hex casing + reject malformed colors."""
        for raw, expected in [
            ({"primary_color": "#ABCDEF"}, "#abcdef"),
            ({"primary_color": "#aabbcc"}, "#aabbcc"),
            ({"primary_color": "not-a-color"}, _DEFAULT_SIGNALS["primary_color"]),
            ({"primary_color": "#abc"}, _DEFAULT_SIGNALS["primary_color"]),  # 3-char not accepted
            ({}, _DEFAULT_SIGNALS["primary_color"]),
        ]:
            out = _coerce_signals(raw)
            assert out["primary_color"] == expected, (raw, out)

    @pytest.mark.asyncio
    async def test_coerces_invalid_enums_to_defaults(self):
        """Unknown enum values fall back to defaults — no leak through."""
        raw = {
            "primary_color":      "#ff00ff",
            "typography_voice":   "WHACKY",         # not in vocab
            "cultural_intensity": "calm",
            "layout_density":     "ENORMOUS",       # not in vocab
            "accent_motif":       "geometric",
        }
        out = _coerce_signals(raw)
        assert out["typography_voice"] == _DEFAULT_SIGNALS["typography_voice"]
        assert out["layout_density"] == _DEFAULT_SIGNALS["layout_density"]
        assert out["cultural_intensity"] == "calm"   # this one was valid

    @pytest.mark.asyncio
    async def test_gemini_failure_returns_defaults(self):
        """Network/timeout errors must not propagate — caller gets defaults."""
        with patch(
            "app.services.admin_brand_extractor.structured_distill",
            new=AsyncMock(side_effect=Exception("timeout")),
        ):
            result = await extract_admin_brand_signals(
                intent={"brand": {"name": "Test"}},
                purpose_data={},
                gemini_key="x",
            )
        assert set(result.keys()) == _REQUIRED_KEYS
        assert result["brand_name"] == "Test"
        assert result["primary_color"] == _DEFAULT_SIGNALS["primary_color"]

    @pytest.mark.asyncio
    async def test_unparseable_response_returns_defaults(self):
        """Non-JSON garbage from Gemini → defaults, never raises."""
        with patch(
            "app.services.admin_brand_extractor.structured_distill",
            new=AsyncMock(return_value="not json at all"),
        ):
            result = await extract_admin_brand_signals(
                intent={"business_category": "general"},
                purpose_data={},
                gemini_key="",
            )
        assert result["brand_name"] == "general"
        assert result["primary_color"] == _DEFAULT_SIGNALS["primary_color"]

    def test_default_signals_vocab_self_consistent(self):
        """The DEFAULT must be in the allowed vocab — otherwise coerce
        would silently rewrite its own defaults."""
        assert _DEFAULT_SIGNALS["typography_voice"]   in _VALID_TYPOGRAPHY
        assert _DEFAULT_SIGNALS["cultural_intensity"] in _VALID_INTENSITY
        assert _DEFAULT_SIGNALS["layout_density"]     in _VALID_DENSITY
        assert _DEFAULT_SIGNALS["accent_motif"]       in _VALID_MOTIF


# ── Live tests (real Gemini Flash, ~$0.015 total) ────────────────────

@skip_no_gemini
@pytest.mark.live
class TestExtractorLive:
    """Three real-domain prompts. Asserts each signal is in its allowed
    vocab — we DON'T pin exact values because Flash output varies between
    runs. These tests verify the *shape* + *plausibility* of the answer,
    not deterministic content."""

    @pytest.mark.asyncio
    async def test_medical_clinic_produces_calm_professional_signals(self):
        result = await extract_admin_brand_signals(
            intent={
                "brand": {"name": "Northside Clinic"},
                "business_category": "healthcare",
                "brand_personality": "trustworthy",
            },
            purpose_data={
                "industry": "healthcare",
                "target_audience": "clinic staff and physicians",
                "primary_purpose": "patient and appointment management",
            },
            gemini_key=os.environ.get("GOOGLE_API_KEY", ""),
        )
        assert set(result.keys()) == _REQUIRED_KEYS
        assert result["primary_color"].startswith("#")
        assert len(result["primary_color"]) == 7
        assert result["typography_voice"] in _VALID_TYPOGRAPHY
        assert result["cultural_intensity"] in _VALID_INTENSITY
        assert result["layout_density"] in _VALID_DENSITY
        # Medical brands lean professional/minimal + calm. Not deterministic
        # but if Flash returns 'playful' + 'energetic' for a clinic, the
        # extractor isn't doing its job. Soft assert on at least ONE of
        # the calm/professional axes.
        soft = (
            result["typography_voice"] in ("professional", "minimal", "soft")
            or result["cultural_intensity"] == "calm"
        )
        assert soft, f"medical signals look wrong: {result}"

    @pytest.mark.asyncio
    async def test_creative_agency_produces_energetic_signals(self):
        result = await extract_admin_brand_signals(
            intent={
                "brand": {"name": "Studio Vibrant"},
                "business_category": "design agency",
                "brand_personality": "bold and creative",
            },
            purpose_data={
                "industry": "creative services",
                "target_audience": "designers and creative directors",
                "primary_purpose": "project and client management",
            },
            gemini_key=os.environ.get("GOOGLE_API_KEY", ""),
        )
        assert set(result.keys()) == _REQUIRED_KEYS
        assert result["typography_voice"] in _VALID_TYPOGRAPHY
        assert result["cultural_intensity"] in _VALID_INTENSITY
        # Agency should NOT come back as the most conservative possible
        # combo (professional + calm + compact + geometric, the literal
        # _DEFAULT_SIGNALS). Sanity check: at least ONE field is
        # non-default — proves Gemini is reasoning about the prompt.
        differs_from_defaults = any(
            result[k] != _DEFAULT_SIGNALS[k]
            for k in ("typography_voice", "cultural_intensity",
                      "layout_density", "accent_motif")
        )
        assert differs_from_defaults, f"agency got default signals: {result}"

    @pytest.mark.asyncio
    async def test_b2b_saas_produces_professional_compact_signals(self):
        result = await extract_admin_brand_signals(
            intent={
                "brand": {"name": "DataOps Inc"},
                "business_category": "B2B SaaS",
                "brand_personality": "professional and efficient",
            },
            purpose_data={
                "industry": "enterprise software",
                "target_audience": "data engineers and ops teams",
                "primary_purpose": "pipeline monitoring and incident management",
            },
            gemini_key=os.environ.get("GOOGLE_API_KEY", ""),
        )
        assert set(result.keys()) == _REQUIRED_KEYS
        assert result["typography_voice"] in _VALID_TYPOGRAPHY
        # B2B SaaS tools should not feel playful — that's the one
        # voice that clearly doesn't fit enterprise data ops.
        assert result["typography_voice"] != "playful", result
        # Sanity-check that the extractor is actually reasoning about
        # the prompt — at least one signal differs from the default
        # fixture (rather than the model returning the safe minimum).
        differs_from_defaults = any(
            result[k] != _DEFAULT_SIGNALS[k]
            for k in ("typography_voice", "cultural_intensity",
                      "layout_density", "accent_motif")
        )
        assert differs_from_defaults, f"B2B SaaS got default signals: {result}"
