"""End-to-end smoke for Phase D deep research wiring.

Pipeline exercised:
  1. Gemini deep research (admin / TMS prompt)
  2. _parse_schema_from_research → minimal entity list
  3. enrich_research_with_deep_dives → appends ===ENTITY_DEEP::Name=== blocks
  4. attach_deep_research_to_schema → puts each block on its entity
  5. schema_to_entity_screens_spec → must render "Industry context" per entity

Run from ai_engine/:
    venv/bin/python scripts/test_phase_d_smoke.py
"""
from __future__ import annotations

import asyncio
import functools
import os
import sys
import time
from pathlib import Path

print = functools.partial(print, flush=True)
sys.stdout.reconfigure(line_buffering=True)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _load_env(p: str) -> None:
    if not os.path.exists(p):
        return
    for line in open(p):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

for k, d in [
    ("SUPABASE_URL", "https://x.supabase.co"),
    ("SUPABASE_ANON_KEY", "x"),
    ("SUPABASE_JWT_SECRET", "x"),
    ("SUPABASE_SERVICE_KEY", "x"),
    ("ENCRYPTION_KEY", "x" * 32),
    ("INTERNAL_API_KEY", "x"),
    ("ANTHROPIC_API_KEY", "dummy"),
    ("PHASE_D_DEEP_RESEARCH", "1"),
]:
    os.environ.setdefault(k, d)

GEMINI_KEY = os.environ.get("GOOGLE_API_KEY", "")
if not GEMINI_KEY:
    print("ERROR: GOOGLE_API_KEY missing")
    sys.exit(1)


PROMPT = (
    "logistics TMS admin panel for managing shipments, carriers, drivers, "
    "customers, and invoices for a freight brokerage"
)


class WS:
    async def send_json(self, p: dict) -> None:
        # Surface progress messages so we can see Phase D firing.
        msg = p.get("message") if isinstance(p, dict) else None
        if msg and ("Deep research" in msg or "🔬" in msg or "focused" in msg):
            print(f"  [ws] {msg}")


async def main() -> int:
    from app.services.project_generator import (
        _expand_short_prompt,
        enrich_research_with_deep_dives,
        gemini_deep_research,
    )
    from app.services.project_schema import (
        _extract_block,
        _parse_entity_screens_block,
        _parse_schema_from_research,
        attach_deep_research_to_schema,
        schema_to_entity_screens_spec,
    )
    from knowledge.loader import classify_project_type_ai

    save_dir = "/tmp/phase_d_smoke"
    os.makedirs(save_dir, exist_ok=True)
    ws = WS()

    print(f"\n{'═'*72}\nPHASE D SMOKE\n  prompt: {PROMPT}\n{'═'*72}\n")

    t0 = time.perf_counter()
    cls = await classify_project_type_ai(PROMPT, GEMINI_KEY)
    print(f"  classified → {cls.get('layout_archetype')} / {cls.get('domain')}")

    if cls.get("layout_archetype") not in {
        "admin_dashboard", "crm", "tms", "saas_dashboard", "ecommerce",
    }:
        print("  ⚠ overriding archetype → tms")
        cls["layout_archetype"] = "tms"

    expanded = await _expand_short_prompt(
        PROMPT, cls["layout_archetype"], cls.get("domain", "logistics"), GEMINI_KEY, ws,
    )
    research = await gemini_deep_research(expanded, cls, "nextjs", GEMINI_KEY, ws)
    t_research = time.perf_counter() - t0
    print(f"\n  base research: {len(research)} chars, {t_research:.1f}s")
    open(os.path.join(save_dir, "01_base_research.txt"), "w").write(research)

    # Minimal schema: extract entities from ===ENTITY_SCREENS=== block.
    block = _extract_block(research, "===ENTITY_SCREENS===", max_chars=20000) or ""
    parsed = _parse_entity_screens_block(block)
    if not parsed:
        print("❌ FAIL: no entities parsed from ENTITY_SCREENS block")
        return 1

    schema = {
        "entities": [
            {"name": ent.title(), "slug": ent + "s", "screens": screens}
            for ent, screens in parsed.items()
        ],
    }
    print(f"  schema: {len(schema['entities'])} entities → " + ", ".join(
        e["name"] for e in schema["entities"]
    ))

    # Phase D fan-out
    t1 = time.perf_counter()
    research = await enrich_research_with_deep_dives(
        research,
        schema=schema,
        layout_archetype="tms",
        domain=cls.get("domain", "logistics"),
        brand_name="FreightFlow",
        websocket=ws,
        gemini_key=GEMINI_KEY,
    )
    t_phase_d = time.perf_counter() - t1
    print(f"\n  Phase D: {t_phase_d:.1f}s")
    open(os.path.join(save_dir, "02_research_with_phase_d.txt"), "w").write(research)

    # Count deep blocks present in research
    deep_count = research.count("===ENTITY_DEEP::")
    print(f"  ===ENTITY_DEEP:: blocks in research: {deep_count} (expected: {len(schema['entities'])})")

    if deep_count == 0:
        print("\n❌ FAIL: no ENTITY_DEEP blocks emitted")
        return 1

    # Attach to schema
    attach_deep_research_to_schema(schema, research)

    attached = [e for e in schema["entities"] if e.get("deep_research")]
    print(f"\n  Entities with deep_research attached: {len(attached)}/{len(schema['entities'])}")
    for e in schema["entities"]:
        deep = e.get("deep_research") or ""
        flag = "✅" if deep else "❌"
        print(f"    {flag} {e['name']:<14s}  {len(deep)} chars")

    if not attached:
        print("\n❌ FAIL: deep_research did not attach to any entity")
        return 1

    # Render spec
    spec = schema_to_entity_screens_spec(schema)
    open(os.path.join(save_dir, "03_rendered_spec.txt"), "w").write(spec)
    print(f"\n  Rendered spec: {len(spec)} chars")

    # Look for the deep_research surface in the rendered spec.
    needle = "Industry context"
    occurrences = spec.count(needle)
    print(f"  '{needle}' sections in spec: {occurrences} (expected: {len(attached)})")

    if occurrences == 0:
        print("\n❌ FAIL: deep_research not rendered in spec")
        return 1

    # Print a short slice from the first entity that has deep_research
    for e in schema["entities"]:
        if e.get("deep_research"):
            head = "### Entity:"
            body = spec.split(head, 2)
            if len(body) >= 2:
                first = head + body[1].split(head)[0]
                # Trim noise — show first 1600 chars
                print("\n  --- FIRST ENTITY WITH DEEP RESEARCH (excerpt) ---")
                print(first[:1600])
                break

    print(f"\n✅ PASS — Phase D end-to-end OK ({len(attached)}/{len(schema['entities'])} entities)")
    print(f"  Total wall: {time.perf_counter() - t0:.1f}s")
    print(f"  Artifacts: {save_dir}/")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
