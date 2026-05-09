"""End-to-end probe: full research pipeline → enriched Brief.

Pipeline:
  Stage 1  analyze_intent
  Stage 2  run_domain_research  ┐
  Stage 3  run_design_research  ┘ in parallel
  Stage 4  extract_research_signals (2 Flash calls in parallel)
  Stage 5  enrich_brief_with_signals (mutation, no API call)
  + legacy build_landing_brief on the side, for diff comparison

Run:  docker exec lucid-ai-ai_engine-1 python /app/test_research_to_brief_e2e.py
"""
import asyncio
import json
import os
import sys
import time

sys.path.insert(0, "/app")

from app.services.landing_intent             import analyze_intent
from app.services.landing_domain_research    import run_domain_research
from app.services.landing_design_research    import run_design_research
from app.services.landing_research_extract   import (
    extract_research_signals,
    enrich_brief_with_signals,
)
from app.services.landing_brief              import build_landing_brief


PROMPT = "modern Italian restaurant in Brooklyn with handmade pasta and natural wine"
CLASSIFICATION = {"domain": "restaurant"}


def _show_palette(label: str, palette: dict) -> None:
    print(f"  {label}:")
    for k in ("primary", "secondary", "accent", "background", "foreground", "muted", "border", "card"):
        print(f"    --{k:11s}: {palette.get(k, '?')}")


def _show_typography(label: str, typo: dict) -> None:
    print(f"  {label}: heading={typo.get('heading_font')!r}, body={typo.get('body_font')!r}, scale={typo.get('scale', '?')}")


async def main() -> None:
    key = os.environ.get("GOOGLE_API_KEY", "").strip()
    print(f"GOOGLE_API_KEY present: {bool(key)} (len={len(key)})")
    print(f"PROMPT: {PROMPT!r}")
    print(f"CLASSIFICATION: {CLASSIFICATION}\n")

    # ── Stage 1
    print("═" * 78)
    print("STAGE 1 — analyze_intent")
    print("═" * 78)
    t0 = time.time()
    intent = await analyze_intent(PROMPT, CLASSIFICATION, gemini_key=key or None, timeout_s=60.0)
    t_intent = time.time() - t0
    print(f"⏱  {t_intent:.1f}s")
    print(f"  category={intent['business_category']!r} subcat={intent['business_subcategory']!r}")
    print(f"  geo={intent['geographic_specifics']!r} purpose={intent['primary_purpose']}")
    print(f"  personality={intent['brand_personality']}")

    # ── Stages 2 + 3 in parallel + legacy brief in parallel for diff
    print()
    print("═" * 78)
    print("STAGES 2 + 3 — domain + design research (parallel) + LEGACY brief (parallel)")
    print("═" * 78)
    t1 = time.time()
    domain_research, design_research, legacy_brief = await asyncio.gather(
        run_domain_research(intent, gemini_key=key or None, timeout_s=240.0),
        run_design_research(intent, gemini_key=key or None, timeout_s=240.0),
        build_landing_brief(PROMPT, CLASSIFICATION, gemini_key=key or None, timeout_s=240.0),
    )
    t_research = time.time() - t1
    ds = domain_research.get("_summary", {})
    eds = design_research.get("_summary", {})
    print(f"⏱  {t_research:.1f}s (research) — domain: succeeded={ds.get('calls_succeeded')}/4 grounded={ds.get('calls_grounded')}/4 sources={ds.get('total_sources')}")
    print(f"                              design: succeeded={eds.get('calls_succeeded')}/4 grounded={eds.get('calls_grounded')}/4 sources={eds.get('total_sources')}")

    # ── Stage 4 — extract signals
    print()
    print("═" * 78)
    print("STAGE 4 — extract_research_signals (2 parallel Flash calls)")
    print("═" * 78)
    t2 = time.time()
    signals = await extract_research_signals(
        intent, domain_research, design_research,
        gemini_key=key or None, timeout_s=90.0,
    )
    t_extract = time.time() - t2
    domain_sig = signals.get("domain") or {}
    design_sig = signals.get("design") or {}
    print(f"⏱  {t_extract:.1f}s")

    print(f"\n  ── DOMAIN SIGNALS ──")
    print(f"  industry_terms ({len(domain_sig.get('industry_terms', []))}):")
    for t in (domain_sig.get("industry_terms") or [])[:8]:
        print(f"    • {t}")
    print(f"  audience_phrases ({len(domain_sig.get('audience_phrases', []))}):")
    for q in (domain_sig.get("audience_phrases") or [])[:5]:
        print(f"    • {q[:120]!r}")
    print(f"  regional_touchpoints ({len(domain_sig.get('regional_touchpoints', []))}):")
    for r in (domain_sig.get("regional_touchpoints") or [])[:5]:
        print(f"    • {r.get('name')!r} — {r.get('context', '')[:80]}")
    print(f"  competitor_section_orders ({len(domain_sig.get('competitor_section_orders', []))}):")
    for o in (domain_sig.get("competitor_section_orders") or [])[:3]:
        print(f"    • {o}")
    print(f"  top_competitors ({len(domain_sig.get('top_competitors', []))}):")
    for c in (domain_sig.get("top_competitors") or [])[:5]:
        print(f"    • {c.get('name'):<24} {c.get('tier', '?'):<8} {c.get('url', '')}")
    print(f"  white_space ({len(domain_sig.get('white_space', []))}):")
    for w in (domain_sig.get("white_space") or [])[:5]:
        print(f"    • {w[:120]}")

    print(f"\n  ── DESIGN SIGNALS ──")
    typo = design_sig.get("chosen_typography") or {}
    print(f"  chosen_typography: {typo.get('heading_font')!r} + {typo.get('body_font')!r}")
    print(f"    rationale: {typo.get('rationale', '')[:140]}")
    pal = design_sig.get("chosen_palette") or {}
    print(f"  chosen_palette: {pal.get('name', '?')!r}")
    _show_palette("    HSL", pal)
    print(f"  dominant_paradigm: {design_sig.get('dominant_paradigm')!r}")
    hero = design_sig.get("hero_pattern") or {}
    print(f"  hero_pattern: {hero.get('name')!r}  rationale: {hero.get('rationale', '')[:120]}")
    print(f"  per_section_approaches:")
    for k, v in (design_sig.get("per_section_approaches") or {}).items():
        print(f"    • {k:14s}: {v}")
    print(f"  reference_urls ({len(design_sig.get('reference_urls', []))}):")
    for u in (design_sig.get("reference_urls") or [])[:6]:
        print(f"    • {u}")

    # ── Stage 5 — enrich
    print()
    print("═" * 78)
    print("STAGE 5 — enrich_brief_with_signals (no API)")
    print("═" * 78)
    enriched_brief = enrich_brief_with_signals(
        # Operate on a deep copy of legacy_brief so the diff is meaningful
        json.loads(json.dumps(legacy_brief)),
        signals,
    )

    print("\n  ── LEGACY BRIEF ──")
    _show_typography("typography", legacy_brief.get("typography", {}))
    _show_palette("palette HSL", legacy_brief.get("palette", {}))
    print(f"  motif: {legacy_brief.get('motif')!r}")
    print(f"  hero archetype: {next((s.get('archetype') for s in legacy_brief.get('sections', []) if s.get('type')=='hero'), None)!r}")
    print(f"  references count: {len(legacy_brief.get('references', []))}")

    print("\n  ── ENRICHED BRIEF ──")
    _show_typography("typography", enriched_brief.get("typography", {}))
    _show_palette("palette HSL", enriched_brief.get("palette", {}))
    print(f"  motif: {enriched_brief.get('motif')!r}")
    print(f"  hero archetype: {next((s.get('archetype') for s in enriched_brief.get('sections', []) if s.get('type')=='hero'), None)!r}")
    print(f"  references count: {len(enriched_brief.get('references', []))}")
    print(f"  voice_phrases:    {len(enriched_brief.get('voice_phrases', []))} items")
    print(f"  regional_refs:    {len(enriched_brief.get('regional_refs', []))} items")
    print(f"  industry_terms:   {len(enriched_brief.get('industry_terms', []))} items")
    print(f"  white_space:      {len(enriched_brief.get('white_space', []))} items")
    rmeta = enriched_brief.get("_research", {})
    if rmeta:
        print(f"  _research breadcrumbs:")
        for k, v in rmeta.items():
            print(f"    {k}: {str(v)[:120]}")

    # Save full output
    out = "/tmp/test_research_to_brief_output.json"
    with open(out, "w") as fh:
        json.dump({
            "intent": intent,
            "domain_research_summary": ds,
            "design_research_summary": eds,
            "signals": signals,
            "legacy_brief":   legacy_brief,
            "enriched_brief": enriched_brief,
        }, fh, indent=2, ensure_ascii=False)

    total = time.time() - t0
    print()
    print("═" * 78)
    print(f"TOTAL: {total:.1f}s   (intent {t_intent:.1f}s + research {t_research:.1f}s + extract {t_extract:.1f}s)")
    print("═" * 78)
    print(f"\nFull output saved to {out}")


if __name__ == "__main__":
    asyncio.run(main())
