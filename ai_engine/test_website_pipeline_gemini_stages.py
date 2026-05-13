"""End-to-end test of website_pipeline Stages 1-4 (Gemini-only).

Validates that the full Gemini chain works for full-website prompts:
  Stage 1 — analyze_intent
  Stage 2 — domain + design research (parallel)
  Stage 3 — visual_dna extraction
  Stage 4 — build_website_plan

Stops before Stage 5 (foundation files) and Stage 6 (Claude per-page calls).
No Claude tokens needed.

Run: docker exec lucid-ai-ai_engine-1 python /app/test_website_pipeline_gemini_stages.py
"""
import asyncio
import sys
sys.path.insert(0, "/app")

from app.services.landing_intent import analyze_intent
from app.services.landing_domain_research import run_domain_research
from app.services.landing_design_research import run_design_research
from app.services.landing_research_extract import extract_research_signals
from app.services.website_plan import build_website_plan

PROMPTS = [
    "Italian coffee shop website in Florence",
    "Yoga studio website in Bali for digital nomads",
    "SaaS invoicing tool website for freelance developers",
]


async def run_one(prompt: str) -> dict:
    print(f"\n{'═'*72}\nPROMPT: {prompt}\n{'═'*72}")

    # Stage 1: Intent
    print("[1/4] Intent...")
    intent = await analyze_intent(prompt, {}, timeout_s=60.0)
    cat = intent.get("business_category", "?")
    sub = intent.get("business_subcategory", "?")
    geo = intent.get("geographic_specifics", "?")
    print(f"       category={cat}/{sub}, geo={geo}")

    # Stage 2: Research (parallel)
    print("[2/4] Research (domain + design parallel)...")
    domain_res, design_res = await asyncio.gather(
        run_domain_research(intent, timeout_s=120.0),
        run_design_research(intent, timeout_s=120.0),
    )
    print(f"       domain={len(domain_res)} keys, design={len(design_res)} keys")

    # Stage 3: Visual_DNA
    print("[3/4] Visual DNA extraction...")
    signals = await extract_research_signals(intent, domain_res, design_res, timeout_s=180.0)
    vd = signals.get("visual_dna") or {}
    intensity = vd.get("cultural_intensity", "?")
    motifs = len(vd.get("decorative_motifs") or [])
    anatomies = vd.get("section_anatomies") or {}
    print(f"       intensity={intensity}, motifs={motifs}, anatomies={list(anatomies.keys())}")

    # Stage 4: Plan
    print("[4/4] Website plan...")
    plan = await build_website_plan(prompt, intent, vd, timeout_s=60.0)
    pages = plan.get("pages", [])
    brand = plan.get("brand", {})
    print(f"       brand: {brand.get('name')} ({brand.get('domain')})")
    print(f"       pages: {[p.get('route') for p in pages]}")
    for p in pages:
        sec_types = [s.get('type') for s in (p.get('sections') or [])]
        print(f"         {p.get('route'):20s} {p.get('title'):20s} sections={sec_types}")

    # Pass criteria:
    #   - intent has category + geo
    #   - visual_dna has ≥3 anatomies
    #   - plan has ≥3 pages, home first
    pass_intent = bool(cat and cat != "?" and geo and geo != "?")
    pass_vd = len(anatomies) >= 3
    pass_plan = (
        len(pages) >= 3
        and pages[0].get("route") == "/"
        and all(p.get("sections") for p in pages)
    )
    overall = pass_intent and pass_vd and pass_plan

    print(f"\n       ✓ intent: {pass_intent}  ✓ visual_dna: {pass_vd}  ✓ plan: {pass_plan}  → {'PASS' if overall else 'FAIL'}")
    return {
        "prompt": prompt,
        "ok": overall,
        "intent_ok": pass_intent,
        "vd_ok": pass_vd,
        "plan_ok": pass_plan,
        "n_anatomies": len(anatomies),
        "n_pages": len(pages),
    }


async def main():
    print("="*72)
    print("WEBSITE PIPELINE — STAGES 1-4 (GEMINI-ONLY)")
    print("="*72)

    results = []
    for prompt in PROMPTS:
        try:
            r = await run_one(prompt)
        except Exception as e:
            print(f"  ✗ {prompt} threw: {e}")
            r = {"prompt": prompt, "ok": False, "err": str(e)}
        results.append(r)

    print(f"\n{'═'*72}\nSUMMARY\n{'═'*72}")
    passed = 0
    for r in results:
        if r.get("ok"):
            passed += 1
            print(f"  ✓ {r['prompt']}")
            print(f"     anatomies={r['n_anatomies']}, pages={r['n_pages']}")
        else:
            print(f"  ✗ {r['prompt']}")
            if "err" in r:
                print(f"     error: {r['err']}")
            else:
                print(f"     intent={r['intent_ok']} vd={r['vd_ok']} plan={r['plan_ok']}")

    print(f"\nSCORE: {passed}/{len(results)}")


if __name__ == "__main__":
    asyncio.run(main())
