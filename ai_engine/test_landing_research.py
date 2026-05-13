"""Test the landing research pipeline end-to-end (Gemini only, no Claude).

Tests: intent analysis → domain research → design research → signal extraction → visual_dna
Stops before Claude section codegen.

Run inside the container:
    docker exec -it lucid-ai-ai_engine-1 python /app/test_landing_research.py
"""
import asyncio
import sys
sys.path.insert(0, "/app")

from app.services.landing_intent import analyze_intent
from app.services.landing_domain_research import run_domain_research
from app.services.landing_design_research import run_design_research
from app.services.landing_research_extract import extract_research_signals, enrich_brief_with_signals

PROMPTS = [
    "Italian coffee shop in Florence",
    "Luxury skincare brand for women over 40",
    "SaaS tool for freelance developers to manage invoices",
    "Yoga studio in Bali for digital nomads",
    "Korean BBQ restaurant in Manhattan",
]

async def test_one(prompt: str) -> dict:
    print(f"\n{'='*70}")
    print(f"PROMPT: {prompt}")
    print('='*70)

    # Step 1: Intent
    print("[1/4] Analyzing intent...")
    try:
        intent = await analyze_intent(prompt, {}, timeout_s=60.0)
        category = intent.get("business_category", "?")
        subcategory = intent.get("business_subcategory", "?")
        personality = intent.get("brand_personality", [])
        print(f"      category={category}, sub={subcategory}, personality={personality[:3]}")
    except Exception as e:
        print(f"      FAILED: {e}")
        return {"prompt": prompt, "error": f"intent: {e}"}

    # Step 2: Research (parallel)
    print("[2/4] Running domain + design research in parallel...")
    try:
        domain_task = asyncio.create_task(run_domain_research(intent, timeout_s=90.0))
        design_task = asyncio.create_task(run_design_research(intent, timeout_s=90.0))
        domain_research, design_research = await asyncio.gather(domain_task, design_task)
        print(f"      domain={len(domain_research)} keys, design={len(design_research)} keys")
    except Exception as e:
        print(f"      FAILED: {e}")
        return {"prompt": prompt, "error": f"research: {e}"}

    # Step 3: Extract signals
    print("[3/4] Extracting research signals (visual DNA)...")
    try:
        signals = await extract_research_signals(intent, domain_research, design_research, timeout_s=120.0)
        vd = signals.get("visual_dna") or {}
        intensity = vd.get("cultural_intensity", "?")
        motifs = len(vd.get("decorative_motifs") or [])
        textures = len(vd.get("signature_textures") or [])
        icons = len(vd.get("iconography_anchors") or [])
        anatomies = sorted((vd.get("section_anatomies") or {}).keys())
        print(f"      intensity={intensity}, motifs={motifs}, textures={textures}, icons={icons}")
        print(f"      section_anatomies: {anatomies}")
    except Exception as e:
        print(f"      FAILED: {e}")
        return {"prompt": prompt, "error": f"extract: {e}"}

    # Step 4: Summary
    print("[4/4] ✅ Research complete — ready for Claude section codegen")
    return {
        "prompt": prompt,
        "category": category,
        "intensity": intensity,
        "motifs": motifs,
        "textures": textures,
        "icons": icons,
        "anatomies": len(anatomies),
        "ok": True,
    }


async def main():
    results = []
    for prompt in PROMPTS:
        try:
            r = await test_one(prompt)
            results.append(r)
        except Exception as e:
            results.append({"prompt": prompt, "error": str(e)})

    print(f"\n{'='*70}")
    print("SUMMARY")
    print('='*70)
    for r in results:
        if "error" in r:
            print(f"  ✗ {r['prompt']}: {r['error']}")
        else:
            print(f"  ✓ {r['prompt']}")
            print(f"    → intensity={r['intensity']}, motifs={r['motifs']}, textures={r['textures']}, anatomies={r['anatomies']}")

if __name__ == "__main__":
    asyncio.run(main())
