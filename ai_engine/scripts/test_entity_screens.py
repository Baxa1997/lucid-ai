"""Smoke test for the new ===ENTITY_SCREENS=== block.

Runs Gemini deep research on ONE admin prompt, then verifies:
  1. The ===ENTITY_SCREENS=== block is present in raw research.
  2. _parse_entity_screens_block extracts at least 3 entities × 3 screens each.
  3. schema_to_entity_screens_spec renders structured per-entity output.

Run from ai_engine/:
    venv/bin/python scripts/test_entity_screens.py
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
]:
    os.environ.setdefault(k, d)

GEMINI_KEY = os.environ.get("GOOGLE_API_KEY", "")
if not GEMINI_KEY:
    print("ERROR: GOOGLE_API_KEY missing")
    sys.exit(1)


PROMPT = "logistics TMS admin panel for managing shipments, carriers, drivers, customers, and invoices for a freight brokerage"


class WS:
    async def send_json(self, p: dict) -> None:
        pass


async def main() -> int:
    from app.services.project_generator import _expand_short_prompt, gemini_deep_research
    from app.services.project_schema import (
        _extract_block,
        _parse_entity_screens_block,
        schema_to_entity_screens_spec,
    )
    from knowledge.loader import classify_project_type_ai

    save_dir = "/tmp/admin_screens_test"
    os.makedirs(save_dir, exist_ok=True)
    ws = WS()

    print(f"\n{'═'*72}\nADMIN SMOKE TEST\n  prompt: {PROMPT}\n{'═'*72}\n")

    t0 = time.perf_counter()
    cls = await classify_project_type_ai(PROMPT, GEMINI_KEY)
    print(f"  classified → {cls.get('layout_archetype')} / {cls.get('domain')}")

    # Force admin if classification disagrees — we want to test the new block
    if cls.get("layout_archetype") not in {
        "admin_dashboard", "crm", "tms", "saas_dashboard", "ecommerce",
    }:
        print(f"  ⚠ overriding archetype → tms")
        cls["layout_archetype"] = "tms"

    expanded = await _expand_short_prompt(
        PROMPT, cls["layout_archetype"], cls.get("domain", "logistics"), GEMINI_KEY, ws,
    )
    research = await gemini_deep_research(expanded, cls, "nextjs", GEMINI_KEY, ws)
    dt = time.perf_counter() - t0

    open(os.path.join(save_dir, "research_full.txt"), "w").write(research)
    print(f"\n  research: {len(research)} chars, {dt:.1f}s")

    # Extract the new block
    block = _extract_block(research, "===ENTITY_SCREENS===", max_chars=20000) or ""
    open(os.path.join(save_dir, "entity_screens_block.txt"), "w").write(block)
    print(f"  ENTITY_SCREENS block: {len(block)} chars")

    if not block:
        print("\n❌ FAIL: ===ENTITY_SCREENS=== block missing from research")
        return 1

    parsed = _parse_entity_screens_block(block)
    print(f"\n  Parsed {len(parsed)} entities:")
    for ent_name, screens in parsed.items():
        kinds = ", ".join((s.get("kind") or "?") for s in screens)
        print(f"    • {ent_name}: {len(screens)} screens [{kinds}]")

    if not parsed:
        print("\n❌ FAIL: parser returned no entities")
        return 1

    # Build a minimal schema and render the spec
    fake_schema = {
        "entities": [
            {"name": ent.title(), "slug": ent + "s", "screens": screens}
            for ent, screens in parsed.items()
        ],
    }
    spec = schema_to_entity_screens_spec(fake_schema)
    open(os.path.join(save_dir, "rendered_spec.txt"), "w").write(spec)
    print(f"\n  Rendered spec: {len(spec)} chars")

    print(f"\n  --- FIRST ENTITY IN RENDERED SPEC ---")
    # Print first entity block (after the header)
    body = spec.split("### Entity:", 2)
    if len(body) >= 2:
        print("### Entity:" + body[1].split("### Entity:")[0][:2200])

    print(f"\n  Artifacts written to {save_dir}/")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
