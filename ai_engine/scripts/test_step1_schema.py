"""Step-1 verification: schema completeness.

Asserts that project_schema is the single source of truth for:
  - archetype  (formerly _layout_archetype)
  - domain_kind  (formerly _domain)
  - design  (formerly _design / Design Director output)

Run from repo root:
    cd ai_engine && python3 scripts/test_step1_schema.py
"""
from __future__ import annotations

import sys
import copy
import importlib.util
from pathlib import Path

# Load project_schema.py as an isolated module — avoids app/__init__.py which
# pulls in FastAPI etc. Step-1 verification only needs the schema module.
_SCHEMA_PATH = (
    Path(__file__).resolve().parents[1] / "app" / "services" / "project_schema.py"
)
_spec = importlib.util.spec_from_file_location("project_schema_iso", _SCHEMA_PATH)
project_schema_mod = importlib.util.module_from_spec(_spec)
sys.modules["project_schema_iso"] = project_schema_mod
_spec.loader.exec_module(project_schema_mod)

EMPTY_SCHEMA = project_schema_mod.EMPTY_SCHEMA
_parse_schema_from_research = project_schema_mod._parse_schema_from_research


def test_empty_schema_has_new_fields():
    assert "archetype" in EMPTY_SCHEMA, "archetype missing from EMPTY_SCHEMA"
    assert "domain_kind" in EMPTY_SCHEMA, "domain_kind missing from EMPTY_SCHEMA"
    assert "design" in EMPTY_SCHEMA, "design missing from EMPTY_SCHEMA"
    assert EMPTY_SCHEMA["archetype"] == ""
    assert EMPTY_SCHEMA["domain_kind"] == ""
    assert EMPTY_SCHEMA["design"] == {}
    print("✓ EMPTY_SCHEMA has archetype, domain_kind, design")


def test_parser_populates_classification_fields():
    research = """
===HEADER===
brand: TestBrand
nav: home, about, pricing, contact
===CSS_VARIABLES===
primary: 220 90% 56%
background: 0 0% 100%
foreground: 222 47% 11%
===PAGES===
[page: home]
path: /
[page: about]
path: /about
"""
    classification = {"layout_archetype": "consumer_website", "domain": "saas"}
    schema = _parse_schema_from_research(
        research=research,
        description="A test SaaS landing site",
        classification=classification,
        stack="nextjs",
        original_description="A test SaaS landing site",
    )
    assert schema["archetype"] == "consumer_website", (
        f"archetype not set, got: {schema.get('archetype')!r}"
    )
    assert schema["domain_kind"] == "saas", (
        f"domain_kind not set, got: {schema.get('domain_kind')!r}"
    )
    print(f"✓ Parser sets archetype={schema['archetype']!r} "
          f"domain_kind={schema['domain_kind']!r}")


def test_parser_does_not_break_on_minimal_input():
    # Even with empty research and missing classification keys, the new fields
    # must default cleanly — never raise KeyError, never leave them missing.
    schema = _parse_schema_from_research(
        research="",
        description="",
        classification={},
        stack="nextjs",
        original_description="",
    )
    assert schema["archetype"] == "single_page_landing", (
        "default archetype should be single_page_landing"
    )
    assert schema["domain_kind"] == "general", (
        "default domain_kind should be general"
    )
    assert schema["design"] == {}, (
        "design must default to empty dict, not None"
    )
    print("✓ Parser defaults are sane on empty input")


def test_empty_schema_is_immutable_per_call():
    # Make sure deepcopy is used inside the parser so two parse calls don't
    # share the same nested dicts (would corrupt cross-test state).
    original = copy.deepcopy(EMPTY_SCHEMA)
    schema_a = _parse_schema_from_research(
        research="", description="", classification={"layout_archetype": "blog"},
        stack="nextjs",
    )
    schema_a["brand"]["name"] = "MUTATED"
    schema_a["design"]["foo"] = "bar"
    assert EMPTY_SCHEMA == original, (
        "EMPTY_SCHEMA was mutated by parser — deepcopy is broken"
    )
    print("✓ EMPTY_SCHEMA isolation holds (deepcopy works)")


if __name__ == "__main__":
    test_empty_schema_has_new_fields()
    test_parser_populates_classification_fields()
    test_parser_does_not_break_on_minimal_input()
    test_empty_schema_is_immutable_per_call()
    print("\nAll step-1 schema checks passed.")
