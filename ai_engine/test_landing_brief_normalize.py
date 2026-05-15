"""Regression tests for landing_brief normalisation.

Run from ai_engine/:
    python3 test_landing_brief_normalize.py
"""
from __future__ import annotations

import importlib.util
import os

_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
_MODULE_PATH = os.path.join(_ENGINE_DIR, "app", "services", "landing_brief.py")
_spec = importlib.util.spec_from_file_location("landing_brief", _MODULE_PATH)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

_normalize_brief = _mod._normalize_brief


def test_unwraps_single_element_array():
    brief = _normalize_brief(
        [{
            "brand": {"name": "Verdant Table", "tagline": "Seasonal Brooklyn dining"},
            "sections": [{"id": "hero", "type": "hero", "headline": "Fresh every night"}],
        }],
        "Verdant Table restaurant",
        "restaurant",
    )
    assert brief["brand"]["name"] == "Verdant Table"
    assert brief["sections"][0]["type"] == "hero"


def test_top_level_string_array_falls_back_without_dict_update_error():
    brief = _normalize_brief(["not-a-brief"], "ACME consulting", "consulting")
    assert brief["brand"]["name"]
    assert brief["sections"]


def test_malformed_nested_shapes_are_coerced():
    brief = _normalize_brief(
        {
            "brand": "bad",
            "palette": None,
            "personality": "bad",
            "domain_keywords": "restaurant",
            "sections": [
                "bad section",
                {
                    "id": "features",
                    "type": "features",
                    "items": {"title": "not a list"},
                    "image_queries": "farm restaurant interior",
                },
            ],
        },
        "A farm restaurant",
        "restaurant",
    )
    assert brief["domain_keywords"] == ["restaurant"]
    assert any(s["type"] == "features" for s in brief["sections"])
    assert isinstance(next(s for s in brief["sections"] if s["type"] == "features")["items"], list)


if __name__ == "__main__":
    for test in (
        test_unwraps_single_element_array,
        test_top_level_string_array_falls_back_without_dict_update_error,
        test_malformed_nested_shapes_are_coerced,
    ):
        test()
    print("landing_brief normalizer tests passed")
