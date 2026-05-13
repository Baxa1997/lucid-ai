"""Test that LUCID_CLARIFY::project_type overrides classifier routing.

Run: docker exec -it lucid-ai-ai_engine-1 python /app/test_routing_clarify.py
"""
import asyncio, sys
sys.path.insert(0, "/app")

from knowledge.loader import (
    classify_project_type_ai,
    _classify_static,
    map_project_type_to_archetype,
)

# (task_with_marker, expected_archetype, label)
CASES = [
    # Marker forces routing — overrides Gemini classification
    ("[LUCID_CLARIFY::project_type=landing_page] coffee shop website",
     "single_page_landing", "landing_page → single_page_landing"),
    ("[LUCID_CLARIFY::project_type=full_website] coffee shop in Florence",
     "consumer_website", "full_website → consumer_website"),
    ("[LUCID_CLARIFY::project_type=web_app] task tracker",
     "saas_dashboard", "web_app → saas_dashboard"),
    ("[LUCID_CLARIFY::project_type=ecommerce] online shop",
     "ecommerce", "ecommerce → ecommerce"),
    ("[LUCID_CLARIFY::project_type=portfolio] designer site",
     "portfolio", "portfolio → portfolio"),
    ("[LUCID_CLARIFY::project_type=blog] tech blog",
     "blog", "blog → blog"),
    # No marker — classifier decides
    ("coffee shop", None, "no marker — classifier decides"),
]


def test_mapping():
    """Test map_project_type_to_archetype directly."""
    print("\n══ MAPPING TESTS ══")
    cases = [
        ("landing_page", "single_page_landing"),
        ("single_page", "single_page_landing"),
        ("one_pager", "single_page_landing"),
        ("full_website", "consumer_website"),
        ("multi_page", "consumer_website"),
        ("website", "consumer_website"),
        ("web_app", "saas_dashboard"),
        ("webapp", "saas_dashboard"),
        ("dashboard", "saas_dashboard"),
        ("ecommerce", "ecommerce"),
        ("e_commerce", "ecommerce"),
        ("online_store", "ecommerce"),
        ("portfolio", "portfolio"),
        ("blog", "blog"),
        ("magazine", "blog"),
        ("marketplace", "marketplace"),
        ("nonsense_value", None),
    ]
    ok = 0
    for inp, expected in cases:
        actual = map_project_type_to_archetype(inp)
        match = "✓" if actual == expected else "✗"
        if match == "✓": ok += 1
        print(f"  {match} {inp!r:20s} → {actual!r}  (expected {expected!r})")
    print(f"  SCORE: {ok}/{len(cases)}")


async def test_classifier():
    """Test classifier with LUCID_CLARIFY markers."""
    print("\n══ CLASSIFIER OVERRIDE TESTS ══")
    ok = 0
    for task, expected, label in CASES:
        result = await classify_project_type_ai(task, force_archetype=None)
        actual = result.get("layout_archetype")
        if expected is None:
            match = "✓" if actual in ("single_page_landing", "consumer_website") else "?"
            ok += 1
        else:
            match = "✓" if actual == expected else "✗"
            if match == "✓": ok += 1
        print(f"  {match} {label}")
        print(f"     task: {task[:65]}")
        print(f"     archetype: {actual}  (expected: {expected})")
    print(f"  SCORE: {ok}/{len(CASES)}")


async def main():
    print("="*72)
    print("ROUTING + CLARIFY MARKER TEST")
    print("="*72)
    test_mapping()
    await test_classifier()
    print("="*72)


if __name__ == "__main__":
    asyncio.run(main())
