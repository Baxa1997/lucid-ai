"""Live test of the Gemini-driven research path (no Claude calls).

Runs the same sequence the real pipeline runs at the start of
_generate_new_project_inner, but stops BEFORE the design-system Claude call,
schema build, plan emission, and Phase 1/2/3 coding. Lets you validate
research timing + quality on real Gemini APIs without burning Claude tokens.

Phases timed:
  1. _validate_project_intent       — Gemini Flash, fast (<5s typical)
  2. classify_project_type_ai       — Gemini Flash, fast (<5s)
  3. _expand_short_prompt           — Gemini Flash, fast or skipped
  4. gemini_deep_research           — Gemini Pro, slow (this is the bottleneck)
  5. vision_enrich_research         — Gemini Vision, medium (fail-soft)

Cost: roughly $0.05-0.20 in Gemini calls per run.
Time: 60-180s typical depending on archetype + research depth.

Run from ai_engine/:
    venv/bin/python scripts/live_research_only.py
    venv/bin/python scripts/live_research_only.py --prompt "a yoga studio site"
"""
from __future__ import annotations

import argparse
import asyncio
import functools
import os
import sys
import time
from pathlib import Path

print = functools.partial(print, flush=True)
sys.stdout.reconfigure(line_buffering=True)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ── Load .env from repo root ──────────────────────────────────────────────
def _load_env(path: str) -> None:
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

# Required app env (placeholders for things we don't actually use)
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "x")
os.environ.setdefault("SUPABASE_JWT_SECRET", "x")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "x")
os.environ.setdefault("ENCRYPTION_KEY", "x" * 32)
os.environ.setdefault("INTERNAL_API_KEY", "x")
os.environ.setdefault("ANTHROPIC_API_KEY", os.environ.get("ANTHROPIC_API_KEY", "dummy"))

GEMINI_KEY = os.environ.get("GOOGLE_API_KEY", "")
if not GEMINI_KEY:
    print("ERROR: GOOGLE_API_KEY must be set in ai_engine/../.env")
    sys.exit(1)


# ── Capture every WS event the pipeline emits ──────────────────────────────
class CaptureWS:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def send_json(self, payload: dict) -> None:
        self.events.append(payload)
        # Echo only progress/error/warning lines for live tail readability
        t = (payload or {}).get("type", "")
        if t in ("progress", "error", "warning"):
            msg = str(payload.get("message", ""))[:140]
            print(f"  [{t:8s}] {msg}")


# ── Main ──────────────────────────────────────────────────────────────────
async def run(description: str, stack: str = "nextjs", skip_vision: bool = False,
              with_design: bool = False) -> int:
    print(f"\n{'='*72}")
    print(f"LIVE RESEARCH TEST — Gemini only, no Claude")
    print(f"{'='*72}")
    print(f"Prompt: {description}")
    print(f"Stack:  {stack}")
    print(f"{'='*72}\n")

    ws = CaptureWS()
    timings: dict[str, float] = {}

    def _phase(name: str) -> tuple[str, float]:
        return name, time.perf_counter()

    def _end(label: str, t0: float) -> None:
        dt = time.perf_counter() - t0
        timings[label] = dt
        print(f"  ✓ {label}: {dt:.1f}s")

    from app.services.project_generator import (
        _validate_project_intent, _expand_short_prompt, gemini_deep_research,
        _extract_research_section,
    )
    from knowledge.loader import classify_project_type_ai

    # ── 1. Intent gate ──────────────────────────────────────────────
    print("=== 1. Intent gate (Gemini Flash) ===")
    label, t0 = _phase("intent_gate")
    intent = await _validate_project_intent(description, GEMINI_KEY)
    _end(label, t0)
    print(f"  is_project={intent.get('is_project')} score={intent.get('score')}")
    if not intent.get("is_project", True):
        print(f"\n❌ Intent gate REJECTED the input. Pipeline would stop here.")
        print(f"   ask_user: {intent.get('ask_user')}")
        return 1

    # ── 2. Classify ─────────────────────────────────────────────────
    print("\n=== 2. Classify (Gemini Flash) ===")
    label, t0 = _phase("classify")
    classification = await classify_project_type_ai(description, GEMINI_KEY)
    _end(label, t0)
    app_type = classification.get("app_type")
    archetype = classification.get("layout_archetype")
    domain = classification.get("domain")
    print(f"  app_type={app_type}  archetype={archetype}  domain={domain}")

    # ── 3. Expand short prompt ──────────────────────────────────────
    print("\n=== 3. Expand short prompt (Gemini Flash) ===")
    label, t0 = _phase("expand")
    expanded = await _expand_short_prompt(description, archetype, domain, GEMINI_KEY, ws)
    _end(label, t0)
    expanded_short = expanded if expanded == description else expanded[:200] + "…"
    print(f"  expanded_len={len(expanded)} (orig={len(description)})")
    if expanded != description:
        print(f"  preview: {expanded_short}")

    # ── 4. Deep research (the slow one) ─────────────────────────────
    print("\n=== 4. Gemini deep research (Gemini Pro) — this is the slow phase ===")
    label, t0 = _phase("research")
    research = await gemini_deep_research(expanded, classification, stack, GEMINI_KEY, ws)
    _end(label, t0)
    print(f"  research_len={len(research)} chars")
    # Quick quality scan: count the structured headers we expect
    headers = [
        "===CLASSIFICATION===", "===SECTIONS===", "===PAGES===", "===ENTITIES===",
        "===VIBE===", "===LAYOUT_BLUEPRINT===", "===CULTURAL_ATMOSPHERE===",
        "===COPY_TONE===", "===CSS_VARIABLES===", "===FONTS===",
    ]
    found_headers = [h for h in headers if h in research]
    print(f"  structured headers present ({len(found_headers)}/{len(headers)}): {[h.strip('=') for h in found_headers]}")

    # ── 5. Vision enrichment (fail-soft) ────────────────────────────
    if skip_vision:
        print("\n=== 5. Vision enrichment — SKIPPED (--skip-vision) ===")
    else:
        print("\n=== 5. Vision enrichment (Gemini Vision, fail-soft) ===")
        try:
            from app.services.vision_research import vision_enrich_research
            label, t0 = _phase("vision")
            visual_dna = await vision_enrich_research(
                research_text=research,
                description=expanded,
                domain=domain,
                gemini_key=GEMINI_KEY,
                websocket=ws,
            )
            _end(label, t0)
            print(f"  visual_dna_len={len(visual_dna or '')} chars (empty = no enrichment)")
        except Exception as exc:
            print(f"  vision enrichment failed (non-fatal): {exc}")

    # ── 6. Design system (Claude — optional) ─────────────────────────
    if with_design:
        anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not anthropic_key or anthropic_key == "dummy":
            print("\n=== 6. Design system — SKIPPED (ANTHROPIC_API_KEY not set) ===")
        else:
            print("\n=== 6. Design system (Claude — measures Claude design call only) ===")
            from app.services.design_system_builder import build_design_system
            vibe = _extract_research_section(research, "===VIBE===", max_chars=400)
            copy_tone = _extract_research_section(research, "===COPY_TONE===", max_chars=400)
            cultural = _extract_research_section(research, "===CULTURAL_ATMOSPHERE===", max_chars=2400)
            label, t0 = _phase("design_system")
            try:
                design = await asyncio.wait_for(
                    build_design_system(
                        description=expanded, domain=domain, brand_name="",
                        copy_tone=copy_tone, layout_archetype=archetype,
                        vibe=vibe, cultural_atmosphere=cultural,
                        api_key=anthropic_key, websocket=ws,
                    ),
                    timeout=220.0,
                )
                _end(label, t0)
                if design:
                    print(f"  design_system_name: {design.get('design_system_name', '?')}")
                    print(f"  archetype:          {design.get('archetype', '?')}")
                    print(f"  keys:               {sorted(design.keys())[:8]}…")
                else:
                    print(f"  ⚠ build_design_system returned None (validation failure or fail-soft)")
            except asyncio.TimeoutError:
                _end(label, t0)
                print(f"  ⚠ design_system timed out at 220s")

    # ── Final report ────────────────────────────────────────────────
    print(f"\n{'='*72}")
    print("TIMING SUMMARY")
    print(f"{'='*72}")
    total = sum(timings.values())
    for label, dt in timings.items():
        pct = (dt / total * 100) if total else 0
        bar = "█" * int(pct / 2)
        print(f"  {label:14s} {dt:6.1f}s  {pct:5.1f}%  {bar}")
    print(f"  {'TOTAL':14s} {total:6.1f}s")
    print(f"{'='*72}\n")

    print("✅ Research path completed. STOPPED before Claude — no design system, no schema, no Phase 1/2/3.")
    print(f"   {len(ws.events)} WS events captured, {len(found_headers)}/{len(headers)} structured headers in research.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--prompt", default="a minimalist landing page for a Brooklyn bakery called Stonemill",
        help="Project description to test research with",
    )
    parser.add_argument("--stack", default="nextjs", help="Target stack (nextjs/react/vue)")
    parser.add_argument("--skip-vision", action="store_true",
                        help="Skip vision enrichment to save a few seconds + Gemini cost")
    parser.add_argument("--with-design", action="store_true",
                        help="Also run the design-system Claude call (after research) to time it")
    args = parser.parse_args()
    return asyncio.run(run(args.prompt, args.stack, args.skip_vision, args.with_design))


if __name__ == "__main__":
    sys.exit(main())
