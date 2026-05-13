"""Test that the existing research pipeline produces solid visual_dna for
full-WEBSITE prompts (not just landing pages).

This validates Step 1 of the website-pipeline rebuild: we can reuse
landing_intent + landing_domain_research + landing_design_research +
landing_research_extract verbatim — no refactor needed.

Run: docker exec lucid-ai-ai_engine-1 python /app/test_website_research.py
"""
import asyncio
import sys
sys.path.insert(0, "/app")

from app.services.landing_intent import analyze_intent
from app.services.landing_domain_research import run_domain_research
from app.services.landing_design_research import run_design_research
from app.services.landing_research_extract import extract_research_signals

# Diverse full-WEBSITE prompts (note: contains the word "website")
PROMPTS = [
    "Italian coffee shop website in Florence",
    "Korean BBQ restaurant website in Manhattan",
    "Yoga studio website in Bali",
    "Luxury skincare brand website for women 40+",
    "SaaS invoicing tool website for freelance developers",
]


async def test_one(prompt: str) -> dict:
    print(f"\n{'═'*72}")
    print(f"PROMPT: {prompt}")
    print('═'*72)

    # Stage 1: Intent
    print("[1/3] Analyzing intent...")
    try:
        intent = await analyze_intent(prompt, {}, timeout_s=60.0)
    except Exception as e:
        print(f"  ✗ intent FAILED: {e}")
        return {"prompt": prompt, "ok": False, "step": "intent", "err": str(e)}

    category = intent.get("business_category", "?")
    sub = intent.get("business_subcategory", "?")
    geo = intent.get("geographic_specifics", "?")
    personality = intent.get("brand_personality", [])
    print(f"  category={category}/{sub}, geo={geo}")
    print(f"  personality={personality[:3]}")

    # Stage 2: Research (parallel)
    print("[2/3] Running domain + design research in parallel...")
    try:
        domain_t = asyncio.create_task(run_domain_research(intent, timeout_s=90.0))
        design_t = asyncio.create_task(run_design_research(intent, timeout_s=90.0))
        domain_res, design_res = await asyncio.gather(domain_t, design_t)
    except Exception as e:
        print(f"  ✗ research FAILED: {e}")
        return {"prompt": prompt, "ok": False, "step": "research", "err": str(e)}

    print(f"  domain_res: {len(domain_res)} keys")
    print(f"  design_res: {len(design_res)} keys")

    # Stage 3: Extract visual_dna
    print("[3/3] Extracting visual_dna...")
    try:
        signals = await extract_research_signals(
            intent, domain_res, design_res, timeout_s=120.0,
        )
    except Exception as e:
        print(f"  ✗ extract FAILED: {e}")
        return {"prompt": prompt, "ok": False, "step": "extract", "err": str(e)}

    vd = signals.get("visual_dna") or {}
    intensity = vd.get("cultural_intensity", "?")
    motifs = vd.get("decorative_motifs") or []
    textures = vd.get("signature_textures") or []
    icons = vd.get("iconography_anchors") or []
    anatomies = vd.get("section_anatomies") or {}

    print(f"  intensity={intensity}")
    print(f"  motifs={len(motifs)} | textures={len(textures)} | icons={len(icons)}")
    print(f"  section_anatomies: {sorted(anatomies.keys())}")

    # Quality check: visual_dna must have at least:
    #   - 3+ section anatomies (header, hero, footer at minimum)
    #   - cultural_intensity set
    ok = (
        len(anatomies) >= 3
        and intensity in ("bold", "moderate", "subtle")
    )
    print(f"  {'✓ PASS' if ok else '✗ WEAK'}")
    return {
        "prompt": prompt,
        "ok": ok,
        "intensity": intensity,
        "motifs": len(motifs),
        "anatomies": list(anatomies.keys()),
    }


async def main():
    print("="*72)
    print("FULL-WEBSITE RESEARCH PIPELINE TEST")
    print("="*72)
    print(f"Testing {len(PROMPTS)} prompts → intent → research → visual_dna")

    results = []
    for prompt in PROMPTS:
        try:
            r = await test_one(prompt)
        except Exception as e:
            r = {"prompt": prompt, "ok": False, "err": str(e)}
        results.append(r)

    print(f"\n{'═'*72}")
    print("SUMMARY")
    print('═'*72)
    ok_count = sum(1 for r in results if r.get("ok"))
    for r in results:
        if r.get("ok"):
            print(f"  ✓ {r['prompt']}")
            print(f"    intensity={r['intensity']}  motifs={r['motifs']}  anatomies={r['anatomies']}")
        else:
            print(f"  ✗ {r['prompt']}  ({r.get('step', '?')}: {r.get('err', 'weak')[:60]})")
    print(f"\nSCORE: {ok_count}/{len(results)}")
    print('═'*72)


if __name__ == "__main__":
    asyncio.run(main())
