"""End-to-end timing verification of pipeline cache.

Reproduces the spec's three scenarios using real Gemini calls for
Stages 1-3 (Stage 6 needs Claude credits; skipped):

  1. First run for project A — full pipeline, cache misses.
  2. Second run for project A — Stage 2 + 3 served from cache.
  3. First run for project B — cache misses (separate project).

Reports wall-clock per stage + final cache stats.

Run: docker exec lucid-ai-ai_engine-1 python /app/scripts/verify_pipeline_cache.py
"""
from __future__ import annotations

import asyncio
import sys
import time

sys.path.insert(0, "/app")

from app.services.pipeline_cache import pipeline_cache
from app.services.landing_intent import analyze_intent
from app.services.landing_domain_research import run_domain_research
from app.services.landing_design_research import run_design_research
from app.services.landing_research_extract import extract_research_signals


PROJECT_A_PROMPT = "Italian coffee shop in Florence, family-run since 1923"
PROJECT_B_PROMPT = "SaaS invoicing tool for freelance designers"

CLASSIFICATION = {"layout_archetype": "consumer_website", "domain": "general"}
PURPOSE_A = {"primary_purpose": "brand_awareness", "industry": "italian cafe",
             "named_roles": [], "target_audience": "b2c_consumers"}
PURPOSE_B = {"primary_purpose": "lead_generation", "industry": "saas invoicing",
             "named_roles": [], "target_audience": "b2b_buyers"}
CLARITY: dict = {}


async def run_one(prompt: str, purpose: dict, project_id: str, label: str) -> dict:
    """Run Stages 1-3 with caching, returning per-stage timings."""
    print(f"\n{'═' * 72}\n{label}  project_id={project_id!r}\n{'═' * 72}")
    timings: dict[str, float] = {}

    # ── Stage 1 (intent) — CACHED (keeps Stage 3 hash stable) ──
    t0 = time.time()
    cached = pipeline_cache.get(project_id, "intent", prompt, {})
    if cached is not None:
        intent = cached
        stage1_source = "CACHE"
    else:
        intent = await analyze_intent(prompt, {}, timeout_s=60.0)
        pipeline_cache.set(project_id, "intent", intent, prompt, {})
        stage1_source = "LIVE"
    timings["stage1_intent"] = time.time() - t0
    print(f"  Stage 1 (intent)       — {timings['stage1_intent']:.2f}s  [{stage1_source}]")

    # ── Stage 2 (research) — CACHED ──
    t0 = time.time()
    cached = pipeline_cache.get(project_id, "research", prompt, CLARITY, purpose)
    if cached is not None:
        domain_res, design_res = cached
        stage2_source = "CACHE"
    else:
        domain_res, design_res = await asyncio.gather(
            run_domain_research(intent, timeout_s=120.0, purpose_data=purpose),
            run_design_research(intent, timeout_s=120.0, purpose_data=purpose),
        )
        pipeline_cache.set(project_id, "research", (domain_res, design_res),
                           prompt, CLARITY, purpose)
        stage2_source = "LIVE"
    timings["stage2_research"] = time.time() - t0
    print(f"  Stage 2 (research)     — {timings['stage2_research']:.2f}s  [{stage2_source}]")

    # ── Stage 3 (signals = visual_dna + voice) — CACHED ──
    t0 = time.time()
    cached = pipeline_cache.get(project_id, "signals", domain_res, intent, purpose)
    if cached is not None:
        signals = cached
        stage3_source = "CACHE"
    else:
        signals = await extract_research_signals(
            intent, domain_res, design_res, timeout_s=180.0, purpose_data=purpose,
        )
        pipeline_cache.set(project_id, "signals", signals, domain_res, intent, purpose)
        stage3_source = "LIVE"
    timings["stage3_signals"] = time.time() - t0
    print(f"  Stage 3 (signals)      — {timings['stage3_signals']:.2f}s  [{stage3_source}]")

    timings["_total"] = sum(v for k, v in timings.items() if not k.startswith("_"))
    print(f"  TOTAL Stage 1-3        — {timings['_total']:.2f}s")
    return timings


async def main():
    pipeline_cache.clear()  # fresh slate
    print("=" * 72)
    print("PIPELINE CACHE — END-TO-END TIMING VERIFICATION")
    print("=" * 72)
    print("Project A prompt:", PROJECT_A_PROMPT)
    print("Project B prompt:", PROJECT_B_PROMPT)

    # 1) First run for project A — full pipeline (all misses)
    run1 = await run_one(PROJECT_A_PROMPT, PURPOSE_A, "proj-A", "RUN 1 — project A (cold)")

    # 2) Second run for project A — Stage 2 + 3 should hit cache
    run2 = await run_one(PROJECT_A_PROMPT, PURPOSE_A, "proj-A", "RUN 2 — project A (warm)")

    # 3) First run for project B — different project_id → cache misses
    run3 = await run_one(PROJECT_B_PROMPT, PURPOSE_B, "proj-B", "RUN 3 — project B (cold, different project)")

    # ── Summary ──
    stats = pipeline_cache.stats()
    print("\n" + "═" * 72)
    print("FINAL SUMMARY")
    print("═" * 72)
    fmt = "{:<28} {:>8.2f}s"
    print(f"  RUN 1 (proj-A cold)        — Stage 2: {run1['stage2_research']:>5.2f}s  Stage 3: {run1['stage3_signals']:>5.2f}s")
    print(f"  RUN 2 (proj-A warm/cache)  — Stage 2: {run2['stage2_research']:>5.2f}s  Stage 3: {run2['stage3_signals']:>5.2f}s")
    print(f"  RUN 3 (proj-B cold)        — Stage 2: {run3['stage2_research']:>5.2f}s  Stage 3: {run3['stage3_signals']:>5.2f}s")

    saved_2 = run1['stage2_research'] - run2['stage2_research']
    saved_3 = run1['stage3_signals']  - run2['stage3_signals']
    saved_pct = ((run1['_total'] - run2['_total']) / max(run1['_total'], 0.001)) * 100
    print(f"\n  Stage 2 time saved on warm: {saved_2:>5.2f}s")
    print(f"  Stage 3 time saved on warm: {saved_3:>5.2f}s")
    print(f"  Total Stage 1-3 saved      : {saved_pct:>5.1f}% faster on warm run")

    print(f"\n  Cache stats:")
    for k, v in stats.items():
        print(f"    {k:<18} {v}")

    # Gates
    cache_size_ok    = stats["size"] >= 4   # at least 2 (proj-A research+signals) + 2 (proj-B)
    hits_on_warm_ok  = stats["hits"] >= 2   # proj-A run 2 should hit research+signals
    speedup_ok       = run2['_total'] < run1['_total'] * 0.5  # warm should be <50% of cold
    proj_b_isolated  = run3['stage2_research'] > 5  # proj-B research had to actually run

    print(f"\n  Gates:")
    print(f"    cache size >= 4         — {'✓' if cache_size_ok else '✗'}")
    print(f"    hits on warm run >= 2   — {'✓' if hits_on_warm_ok else '✗'}")
    print(f"    warm run < 50% of cold  — {'✓' if speedup_ok else '✗'}")
    print(f"    proj-B not cross-cached — {'✓' if proj_b_isolated else '✗'}")
    return 0 if (cache_size_ok and hits_on_warm_ok and speedup_ok and proj_b_isolated) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
