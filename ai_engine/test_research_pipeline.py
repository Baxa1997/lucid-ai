"""End-to-end smoke test of the landing research pipeline.

Runs the REAL Gemini calls (domain research + design research + extract) on
two contrasting brand intents and prints:
  1. cultural_intensity value Gemini returned (verifies the "bold by default" change)
  2. decorative_motifs, signature_textures, iconography_anchors
  3. section_anatomies keys produced
  4. The VISUAL DNA prompt block that section codegen would feed to Claude

Run from inside the ai_engine container:
    docker exec -it lucid-ai-ai_engine-1 python /app/test_research_pipeline.py
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, "/app")

from app.services.landing_domain_research import run_domain_research
from app.services.landing_design_research import run_design_research
from app.services.landing_research_extract import (
    extract_research_signals,
    enrich_brief_with_signals,
)


# Four test intents picked to exercise the routing flip across diverse
# categories — two BOLD-expected (sensory/cultural), two SUBTLE-expected
# (restraint-required B2B).
TEST_INTENTS = [
    {
        "label": "BOLD-expected — Italian coffee shop in Florence",
        "intent": {
            "business_category": "coffee shop",
            "business_subcategory": "italian espresso bar",
            "geographic_specifics": "Florence, Italy",
            "target_audience": {"primary": "locals + tourists"},
            "brand_personality": ["warm", "traditional", "artisan", "convivial"],
            "tone": "warm",
        },
    },
    {
        "label": "BOLD-expected — Korean BBQ restaurant in NYC Koreatown",
        "intent": {
            "business_category": "restaurant",
            "business_subcategory": "korean BBQ",
            "geographic_specifics": "Koreatown, Manhattan, NYC",
            "target_audience": {"primary": "young professionals + groups"},
            "brand_personality": ["lively", "communal", "premium", "smoky"],
            "tone": "convivial",
        },
    },
    {
        "label": "SUBTLE-expected — Enterprise compliance SaaS",
        "intent": {
            "business_category": "enterprise SaaS",
            "business_subcategory": "compliance and audit automation",
            "geographic_specifics": "United States",
            "target_audience": {"primary": "CFOs and compliance officers"},
            "brand_personality": ["trustworthy", "rigorous", "modern", "restrained"],
            "tone": "professional",
        },
    },
    {
        "label": "SUBTLE-expected — Boutique law firm in Boston",
        "intent": {
            "business_category": "legal services",
            "business_subcategory": "corporate law and M&A",
            "geographic_specifics": "Boston, Massachusetts",
            "target_audience": {"primary": "founders and growth-stage CEOs"},
            "brand_personality": ["authoritative", "discreet", "trustworthy", "established"],
            "tone": "professional",
        },
    },
]


def _build_visual_dna_block(vd: dict) -> str:
    """Mirror of landing_section_codegen.py:629-696 — what Claude actually sees."""
    if not vd:
        return "(no visual_dna — fallback prompt path)"

    intensity = (vd.get("cultural_intensity") or "bold").strip().lower()
    motifs = vd.get("decorative_motifs") or []
    textures = vd.get("signature_textures") or []
    icons = vd.get("iconography_anchors") or []
    photo = (vd.get("photography_style") or "").strip()
    layout_sig = (vd.get("layout_signature") or "").strip()
    palette_emph = (vd.get("cultural_palette_emphasis") or "").strip()
    type_voice = (vd.get("typography_voice") or "").strip()
    flavors = vd.get("section_flavors") or {}

    intensity_note = (
        "Cultural cues sit in ACCENT POSITIONS only..."
        if intensity == "subtle"
        else "Cultural cues take a STRONGER role..."
    )

    parts = [
        "\nVISUAL DNA — THIS IS THE PRIMARY VISUAL DIRECTIVE FOR THIS BRAND.",
        f"Cultural intensity: {intensity}",
        f"  → {intensity_note}",
    ]
    if palette_emph:
        parts.append(f"\nCultural palette emphasis:\n  {palette_emph}")
    if type_voice:
        parts.append(f"\nTypography voice:\n  {type_voice}")
    if photo:
        parts.append(f"\nPhotography style:\n  {photo}")
    if layout_sig:
        parts.append(f"\nLayout signature:\n  {layout_sig}")
    if motifs:
        parts.append("\nDecorative motifs:")
        for m in motifs[:6]:
            parts.append(f"  • {m}")
    if textures:
        parts.append("\nSignature textures:")
        for t in textures[:3]:
            parts.append(f"  • {t}")
    if icons:
        parts.append("\nIconography anchors:")
        for ic in icons[:6]:
            parts.append(f"  • {ic}")
    if flavors:
        parts.append("\nPer-section visual flavor:")
        for stype, note in flavors.items():
            if note and isinstance(note, str):
                parts.append(f"  • {stype}: {note}")
    return "\n".join(parts)


async def test_one(label: str, intent: dict) -> dict:
    print("\n" + "=" * 80)
    print(label)
    print("=" * 80)
    print(f"category    = {intent['business_category']}")
    print(f"subcategory = {intent['business_subcategory']}")
    print(f"geo         = {intent['geographic_specifics']}")
    print(f"personality = {intent['brand_personality']}")
    print()

    print("[1/3] running domain + design research in parallel (Gemini grounded)...")
    domain_task = asyncio.create_task(
        run_domain_research(intent, timeout_s=90.0)
    )
    design_task = asyncio.create_task(
        run_design_research(intent, timeout_s=90.0)
    )
    domain_research, design_research = await asyncio.gather(
        domain_task, design_task
    )
    print(f"      domain calls: {len(domain_research)} keys, "
          f"design calls: {len(design_research)} keys")

    print("[2/3] extracting research signals (converter / summarizer)...")
    signals = await extract_research_signals(
        intent, domain_research, design_research, timeout_s=120.0
    )

    vd = signals.get("visual_dna") or {}
    intensity = vd.get("cultural_intensity")
    motifs = vd.get("decorative_motifs") or []
    textures = vd.get("signature_textures") or []
    icons = vd.get("iconography_anchors") or []
    anatomies = vd.get("section_anatomies") or {}

    print()
    print("---- RAW EXTRACT OUTPUT ----")
    print(f"cultural_intensity:  {intensity!r}")
    print(f"decorative_motifs:   {len(motifs)} items")
    for m in motifs[:6]:
        print(f"    • {m}")
    print(f"signature_textures:  {len(textures)} items")
    for t in textures[:3]:
        print(f"    • {t}")
    print(f"iconography_anchors: {len(icons)} items")
    for i in icons[:6]:
        print(f"    • {i}")
    print(f"section_anatomies:   {sorted(anatomies.keys())}")
    print(f"photography_style:   {(vd.get('photography_style') or '')[:120]!r}")
    print(f"palette_emphasis:    {(vd.get('cultural_palette_emphasis') or '')[:120]!r}")
    print(f"typography_voice:    {(vd.get('typography_voice') or '')[:120]!r}")
    print(f"layout_signature:    {(vd.get('layout_signature') or '')[:120]!r}")

    print()
    print("---- VISUAL DNA BLOCK CLAUDE WOULD SEE ----")
    print(_build_visual_dna_block(vd))

    return {
        "label": label,
        "cultural_intensity": intensity,
        "motifs_n": len(motifs),
        "textures_n": len(textures),
        "icons_n": len(icons),
        "anatomies": sorted(anatomies.keys()),
    }


async def main():
    if not os.environ.get("GOOGLE_GENAI_USE_VERTEXAI") and not os.environ.get(
        "GOOGLE_APPLICATION_CREDENTIALS"
    ):
        print("WARNING: Vertex ADC env not set; calls will likely fail.")

    results = []
    for t in TEST_INTENTS:
        try:
            r = await test_one(t["label"], t["intent"])
            results.append(r)
        except Exception as e:
            print(f"\nFAILED: {t['label']}: {e}")
            results.append({"label": t["label"], "error": str(e)})

    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    for r in results:
        if "error" in r:
            print(f"  ✗ {r['label']}: {r['error']}")
            continue
        intensity = r.get("cultural_intensity")
        expected_bold = "BOLD-expected" in r["label"]
        expected_subtle = "SUBTLE-expected" in r["label"]
        match = (
            (intensity == "bold" and expected_bold)
            or (intensity == "subtle" and expected_subtle)
        )
        flag = "✓" if match else "✗"
        print(
            f"  {flag} {r['label']}: intensity={intensity} "
            f"(motifs={r['motifs_n']}, textures={r['textures_n']}, "
            f"icons={r['icons_n']}, anatomies={len(r['anatomies'])})"
        )


if __name__ == "__main__":
    asyncio.run(main())
