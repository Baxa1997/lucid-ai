"""End-to-end probe of analyze_intent (Stage 1).

Run:  docker exec lucid-ai-ai_engine-1 python /app/test_intent_e2e.py
"""
import asyncio
import json
import os
import sys
import time

sys.path.insert(0, "/app")

from app.services.landing_intent import analyze_intent


# Mix of cases — specific, vague, ambiguous, hiring, ecommerce.
CASES: list[tuple[str, dict]] = [
    (
        "modern Italian restaurant in Brooklyn with handmade pasta and natural wine",
        {"domain": "restaurant"},
    ),
    (
        "agricultural landing page",
        {"domain": "general"},
    ),
    (
        "we are hiring senior backend engineers for our Berlin fintech team",
        {"domain": "fintech"},
    ),
    (
        "small-batch sourdough bakery in the Mission",
        {"domain": "bakery"},
    ),
    (
        "B2B SaaS platform for warehouse inventory management",
        {"domain": "saas"},
    ),
]


async def run_one(prompt: str, classification: dict) -> dict:
    print("─" * 78)
    print(f"PROMPT: {prompt!r}")
    print(f"CLASSIFICATION: {classification}")

    t0 = time.time()
    intent = await analyze_intent(
        prompt,
        classification,
        websocket=None,
        timeout_s=60.0,
    )
    elapsed = time.time() - t0
    print(f"⏱  {elapsed:.1f}s")

    print(f"  business_category:     {intent['business_category']!r}")
    print(f"  business_subcategory:  {intent['business_subcategory']!r}")
    print(f"  primary_purpose:       {intent['primary_purpose']}")
    if intent.get("secondary_purposes"):
        print(f"  secondary_purposes:    {intent['secondary_purposes']}")
    print(f"  geographic_scope:      {intent['geographic_scope']}")
    print(f"  geographic_specifics:  {intent['geographic_specifics']!r}")
    aud = intent["target_audience"]
    print(f"  audience.primary:      {aud['primary']!r}")
    if aud.get("secondary"):
        print(f"  audience.secondary:    {aud['secondary']!r}")
    print(f"  audience.psychographic:{aud['psychographic']!r}")
    print(f"  brand_personality:     {intent['brand_personality']}")
    print(f"  tone:                  {intent['tone']}")
    print(f"  language:              {intent['language']}")
    print(f"  must_have_sections ({len(intent['must_have_sections'])}):")
    print(f"    {intent['must_have_sections']}")
    if intent.get("ambiguity_flags"):
        print(f"  ambiguity_flags:       {intent['ambiguity_flags']}")
    return intent


async def main() -> None:
    project = os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()
    location = os.environ.get("GOOGLE_CLOUD_LOCATION", "").strip() or "global"
    print(f"Vertex project: {project or '(unset)'} location={location}")
    print(f"Running {len(CASES)} cases\n")

    results = []
    t0 = time.time()
    for prompt, cls in CASES:
        intent = await run_one(prompt, cls)
        results.append({"prompt": prompt, "classification": cls, "intent": intent})
        print()
    total = time.time() - t0

    print("═" * 78)
    print(f"DONE — {len(CASES)} cases in {total:.1f}s ({total/len(CASES):.1f}s/case avg)")

    out_path = "/tmp/test_intent_e2e_output.json"
    with open(out_path, "w") as fh:
        json.dump(results, fh, indent=2, ensure_ascii=False)
    print(f"Full output saved to {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
