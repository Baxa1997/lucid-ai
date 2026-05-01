"""Verify _distill_research includes the newly-allowlisted blocks.

Run inside the ai_engine container:
    docker exec lucid-ai-ai_engine-1 python /app/scripts/test_distill_blocks.py
"""

import sys
sys.path.insert(0, "/app")

from app.services.project_generator import _distill_research, _DISTILL_SECTIONS


# Build a synthetic research string with all blocks Gemini emits.
def make_research():
    blocks = []
    for name, cap in _DISTILL_SECTIONS:
        body = (f"line for {name} -- " * 200)[:cap + 200]  # exceed cap so truncation triggers
        blocks.append(f"==={name}===\n{body}\n")
    # Also add a block NOT in the allowlist (should be dropped)
    blocks.append("===UNRELATED===\nshould not appear\n")
    return "\n".join(blocks)


def main():
    research = make_research()
    distilled = _distill_research(research)

    print(f"Input research: {len(research)} chars, {len(_DISTILL_SECTIONS)} allowlisted blocks")
    print(f"Distilled:      {len(distilled)} chars")
    print()

    # Blocks that MUST reach Claude via research_distilled.
    # Note: PAGES/SECTIONS/ENTITIES are intentionally low-priority here because
    # they flow to Claude through project_schema in the Phase 1 prompt
    # (project_generator.py:6844 etc.) — the research-block versions are
    # narrative descriptions that get truncated when budget is tight.
    must_include = [
        "VISUAL_DISTINCTIVENESS",
        "CULTURAL_ATMOSPHERE",
        "ERA_CALIBRATION",
        "LAYOUT_BLUEPRINT",
    ]
    must_exclude = ["UNRELATED"]

    failures = []
    for name in must_include:
        marker = f"==={name}==="
        if marker in distilled:
            print(f"  ✅ {name} present")
        else:
            print(f"  ❌ {name} MISSING")
            failures.append(name)

    for name in must_exclude:
        marker = f"==={name}==="
        if marker in distilled:
            print(f"  ❌ {name} should NOT be present")
            failures.append(f"unexpected:{name}")
        else:
            print(f"  ✅ {name} correctly excluded")

    print()
    if failures:
        print(f"FAILURES: {failures}")
        sys.exit(1)
    print("All blocks OK.")


if __name__ == "__main__":
    main()
