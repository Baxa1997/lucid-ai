"""End-to-end probe of run_domain_research (Stage 2).

Runs Stage 1 (analyze_intent) → Stage 2 (4 parallel grounded calls) for
ONE test prompt and dumps every research output to stdout + /tmp.

Run:  docker exec lucid-ai-ai_engine-1 python /app/test_domain_research_e2e.py
"""
import asyncio
import json
import os
import sys
import time

sys.path.insert(0, "/app")

from app.services.landing_intent import analyze_intent
from app.services.landing_domain_research import run_domain_research


PROMPT = "modern Italian restaurant in Brooklyn with handmade pasta and natural wine"
CLASSIFICATION = {"domain": "restaurant"}


def _print_block(label: str, block: dict) -> None:
    text = block.get("text") or ""
    sources = block.get("sources") or 0
    urls = block.get("urls") or []
    print()
    print("─" * 78)
    print(f"=== {label} ===")
    print(f"  sources (groundingChunks): {sources}")
    print(f"  text length:               {len(text)} chars")
    if urls:
        print(f"  cited URLs ({len(urls)}):")
        for u in urls[:10]:
            print(f"    • {u}")
        if len(urls) > 10:
            print(f"    … and {len(urls) - 10} more")
    print()
    head = text[:1800]
    print(head)
    if len(text) > 1800:
        print(f"… [truncated {len(text) - 1800} more chars]")


async def main() -> None:
    key = os.environ.get("GOOGLE_API_KEY", "").strip()
    print(f"GOOGLE_API_KEY present: {bool(key)} (len={len(key)})")
    print(f"PROMPT: {PROMPT!r}")
    print(f"CLASSIFICATION: {CLASSIFICATION}")

    # ── Stage 1
    print("\n" + "═" * 78)
    print("STAGE 1 — analyze_intent")
    print("═" * 78)
    t0 = time.time()
    intent = await analyze_intent(
        PROMPT, CLASSIFICATION,
        gemini_key=key or None, websocket=None, timeout_s=60.0,
    )
    t1 = time.time()
    print(f"⏱  intent stage: {t1 - t0:.1f}s")
    print(f"  business_category:  {intent['business_category']!r}")
    print(f"  business_subcategory: {intent['business_subcategory']!r}")
    print(f"  primary_purpose:    {intent['primary_purpose']}")
    print(f"  geographic_scope:   {intent['geographic_scope']}")
    print(f"  geographic_specifics: {intent['geographic_specifics']!r}")
    print(f"  audience.primary:   {intent['target_audience']['primary']!r}")
    print(f"  brand_personality:  {intent['brand_personality']}")
    print(f"  must_have_sections: {intent['must_have_sections']}")
    print(f"  ambiguity_flags:    {intent['ambiguity_flags']}")

    # ── Stage 2
    print("\n" + "═" * 78)
    print("STAGE 2 — run_domain_research (4 parallel grounded calls)")
    print("═" * 78)
    t2 = time.time()
    research = await run_domain_research(
        intent,
        gemini_key=key or None, websocket=None, timeout_s=240.0,
    )
    t3 = time.time()
    print(f"⏱  domain research stage: {t3 - t2:.1f}s")

    summary = research["_summary"]
    print(f"\nSUMMARY:")
    print(f"  calls_succeeded:  {summary['calls_succeeded']} / 4")
    print(f"  calls_grounded:   {summary['calls_grounded']} / 4")
    print(f"  calls_degenerate: {summary.get('calls_degenerate', 0)} / 4")
    print(f"  total_sources:    {summary['total_sources']}")

    _print_block("BUSINESS RESEARCH",    research["business"])
    _print_block("AUDIENCE RESEARCH",    research["audience"])
    _print_block("REGIONAL RESEARCH",    research["regional"])
    _print_block("COMPETITIVE RESEARCH", research["competitive"])

    print("\n" + "═" * 78)
    print(f"TOTAL: {time.time() - t0:.1f}s "
          f"(intent {t1 - t0:.1f}s + research {t3 - t2:.1f}s)")
    print("═" * 78)

    out_path = "/tmp/test_domain_research_e2e_output.json"
    with open(out_path, "w") as fh:
        json.dump({"intent": intent, "research": research}, fh, indent=2, ensure_ascii=False)
    print(f"\nFull output saved to {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
