"""Tests for purpose_classifier — Stage 0.5 of the website pipeline.

Six cases:
  1. "logistics company hiring CDL drivers"   → recruitment + CDL in named_roles
  2. "Italian restaurant in Brooklyn"          → brand_awareness
  3. "online shop for vintage cameras"         → ecommerce
  4. "book a cleaning service"                 → appointment_booking
  5. "dental clinic to get more patients"      → lead_generation
  6. Gemini failure → fallback to brand_awareness with confidence=0

Run inside the ai_engine container so Vertex ADC + GOOGLE_CLOUD_PROJECT
are available:

    docker exec -it lucid-ai-ai_engine-1 python /app/tests/test_purpose_classifier.py
"""
from __future__ import annotations

import asyncio
import sys
import traceback

sys.path.insert(0, "/app")

from app.services.purpose_classifier import classify_purpose


LIVE_CASES = [
    # (prompt, expected_purpose, extra_check or None)
    (
        "logistics company hiring CDL drivers",
        "recruitment",
        lambda r: any("cdl" in role.lower() for role in r["named_roles"]),
        "named_roles contains CDL",
    ),
    (
        "Italian restaurant in Brooklyn",
        "brand_awareness",
        None,
        None,
    ),
    (
        "online shop for vintage cameras",
        "ecommerce",
        None,
        None,
    ),
    (
        "book a cleaning service",
        "appointment_booking",
        None,
        None,
    ),
    (
        "dental clinic to get more patients",
        "lead_generation",
        None,
        None,
    ),
]


def _fmt_result(r: dict) -> str:
    return (
        f"purpose={r['primary_purpose']!s:<22} "
        f"conf={r['confidence']:>3}% "
        f"audience={r['target_audience']:<20} "
        f"roles={r['named_roles']} "
        f"industry={r['industry']!r}"
    )


async def run_live_cases() -> tuple[int, int, list[dict]]:
    passed = 0
    total = len(LIVE_CASES)
    outputs = []

    for prompt, expected_purpose, extra_check, extra_desc in LIVE_CASES:
        try:
            result = await classify_purpose(
                user_prompt=prompt,
                clarity_answers={},
                gemini_key="",
                timeout_s=30.0,
            )
        except Exception as exc:
            print(f"  ✗ {prompt!r} threw: {exc}")
            traceback.print_exc()
            outputs.append({"prompt": prompt, "ok": False, "result": None})
            continue

        actual = result.get("primary_purpose")
        purpose_ok = actual == expected_purpose
        extra_ok = extra_check(result) if extra_check else True

        case_ok = purpose_ok and extra_ok
        sigil = "✓" if case_ok else "✗"

        print(f"  {sigil} {prompt!r}")
        print(f"      {_fmt_result(result)}")
        if not purpose_ok:
            print(f"      EXPECTED purpose={expected_purpose}, got {actual}")
        if extra_check and not extra_ok:
            print(f"      EXPECTED extra: {extra_desc}")
        if result.get("reasoning"):
            print(f"      reasoning: {result['reasoning'][:120]}")

        if case_ok:
            passed += 1
        outputs.append({"prompt": prompt, "ok": case_ok, "result": result})

    return passed, total, outputs


async def run_fallback_case() -> bool:
    """Force a Gemini failure by monkey-patching structured_distill."""
    print("\n  Testing fallback on Gemini failure…")
    import app.services.purpose_classifier as pc_mod
    import app.services.landing_gemini as lg_mod

    original = lg_mod.structured_distill

    async def boom(*args, **kwargs):
        raise RuntimeError("simulated Gemini outage")

    lg_mod.structured_distill = boom
    try:
        result = await classify_purpose(
            user_prompt="anything at all",
            clarity_answers={},
            gemini_key="",
            timeout_s=5.0,
        )
    finally:
        lg_mod.structured_distill = original

    ok = (
        result["primary_purpose"] == "brand_awareness"
        and result["confidence"] == 0
        and isinstance(result["named_roles"], list)
        and isinstance(result["urgency_signals"], list)
    )
    sigil = "✓" if ok else "✗"
    print(f"  {sigil} fallback: {_fmt_result(result)}")
    return ok


async def main():
    print("=" * 72)
    print("PURPOSE CLASSIFIER — TESTS")
    print("=" * 72)

    print("\n── Live Gemini cases ──")
    passed, total, outputs = await run_live_cases()

    print("\n── Fallback case ──")
    fallback_ok = await run_fallback_case()

    total_all = total + 1
    passed_all = passed + (1 if fallback_ok else 0)

    print("\n" + "=" * 72)
    print(f"SCORE: {passed_all}/{total_all}")
    print("=" * 72)

    return 0 if passed_all == total_all else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
