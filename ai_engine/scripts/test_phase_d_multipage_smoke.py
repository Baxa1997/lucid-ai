"""End-to-end smoke for Phase D deep research on the multi-page consumer path.

Mirrors test_phase_d_smoke.py but for consumer_website / marketplace / blog
archetypes — verifies ===PAGE_DEEP::Name=== blocks emit, attach to schema.pages,
and render in schema_to_pages_spec.

Run from ai_engine/:
    venv/bin/python scripts/test_phase_d_multipage_smoke.py
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


# Marketing site for a developer-tools SaaS — should classify as
# consumer_website and yield 5+ pages: home, product, pricing, docs, about,
# blog, contact, etc.
PROMPT = (
    "marketing website for a developer-tools SaaS that helps engineering "
    "teams ship and monitor microservices — needs home, product features, "
    "pricing, docs, about, blog, and contact pages"
)


class WS:
    async def send_json(self, p: dict) -> None:
        msg = p.get("message") if isinstance(p, dict) else None
        if msg and ("Deep research" in msg or "🔬" in msg):
            print(f"  [ws] {msg}")


async def main() -> int:
    from app.services.project_generator import (
        _expand_short_prompt,
        enrich_research_with_deep_dives,
        gemini_deep_research,
    )
    from app.services.project_schema import (
        _extract_block,
        _parse_pages_block,
        attach_deep_research_to_schema,
        schema_to_pages_spec,
    )
    from knowledge.loader import classify_project_type_ai

    save_dir = "/tmp/phase_d_multipage_smoke"
    os.makedirs(save_dir, exist_ok=True)
    ws = WS()

    print(f"\n{'═'*72}\nPHASE D MULTIPAGE SMOKE\n  prompt: {PROMPT}\n{'═'*72}\n")

    t0 = time.perf_counter()
    cls = await classify_project_type_ai(PROMPT, GEMINI_KEY)
    print(f"  classified → {cls.get('layout_archetype')} / {cls.get('domain')}")

    consumer_archetypes = {"consumer_website", "marketplace", "portfolio", "blog"}
    if cls.get("layout_archetype") not in consumer_archetypes:
        print(f"  ⚠ overriding archetype → consumer_website")
        cls["layout_archetype"] = "consumer_website"
        cls["is_single_page"] = False

    expanded = await _expand_short_prompt(
        PROMPT, cls["layout_archetype"], cls.get("domain", "developer_tools"),
        GEMINI_KEY, ws,
    )
    research = await gemini_deep_research(expanded, cls, "nextjs", GEMINI_KEY, ws)
    t_research = time.perf_counter() - t0
    print(f"\n  base research: {len(research)} chars, {t_research:.1f}s")
    open(os.path.join(save_dir, "01_base_research.txt"), "w").write(research)

    pages_block = _extract_block(research, "===PAGES===", max_chars=20000) or ""
    open(os.path.join(save_dir, "02_pages_block.txt"), "w").write(pages_block)
    parsed_pages = _parse_pages_block(pages_block)

    if not parsed_pages:
        print("\n❌ FAIL: no pages parsed from ===PAGES=== block")
        return 1

    schema = {"pages": parsed_pages}
    page_names = [(p.get("name") or p.get("title") or p.get("path") or "?") for p in parsed_pages]
    print(f"  schema: {len(parsed_pages)} pages → {page_names}")

    if len(parsed_pages) < 5:
        print(f"  ⚠ only {len(parsed_pages)} pages — Phase D threshold is 5")

    # Phase D fan-out
    t1 = time.perf_counter()
    research = await enrich_research_with_deep_dives(
        research,
        schema=schema,
        layout_archetype=cls["layout_archetype"],
        domain=cls.get("domain", "developer_tools"),
        brand_name="DevFlow",
        websocket=ws,
        gemini_key=GEMINI_KEY,
    )
    t_phase_d = time.perf_counter() - t1
    print(f"\n  Phase D: {t_phase_d:.1f}s")
    open(os.path.join(save_dir, "03_research_with_phase_d.txt"), "w").write(research)

    deep_count = research.count("===PAGE_DEEP::")
    print(f"  ===PAGE_DEEP:: blocks in research: {deep_count} (expected: {len(parsed_pages)})")

    if deep_count == 0:
        print("\n❌ FAIL: no PAGE_DEEP blocks emitted")
        return 1

    # Attach to schema
    attach_deep_research_to_schema(schema, research)

    attached = [p for p in schema["pages"] if p.get("deep_research")]
    print(f"\n  Pages with deep_research attached: {len(attached)}/{len(parsed_pages)}")
    for pg in schema["pages"]:
        deep = pg.get("deep_research") or ""
        flag = "✅" if deep else "❌"
        ident = pg.get("name") or pg.get("title") or pg.get("path") or "?"
        print(f"    {flag} {ident:<20s}  {len(deep)} chars")

    if not attached:
        print("\n❌ FAIL: deep_research did not attach to any page")
        return 1

    # Render spec
    spec = schema_to_pages_spec(schema)
    open(os.path.join(save_dir, "04_rendered_spec.txt"), "w").write(spec)
    print(f"\n  Rendered spec: {len(spec)} chars")

    needle = "from focused research"
    occurrences = spec.count(needle)
    print(f"  '{needle}' sections in spec: {occurrences} (expected: {len(attached)})")

    if occurrences == 0:
        print("\n❌ FAIL: deep_research not rendered in pages spec")
        return 1

    # Print first page that got deep_research
    for pg in schema["pages"]:
        if pg.get("deep_research"):
            ident = pg.get("name") or pg.get("title") or pg.get("path") or "?"
            head = "### Page:"
            body = spec.split(head, 2)
            if len(body) >= 2:
                first = head + body[1].split(head)[0]
                print("\n  --- FIRST PAGE WITH DEEP RESEARCH (excerpt) ---")
                print(first[:1600])
                break

    print(f"\n✅ PASS — Phase D multipage end-to-end OK ({len(attached)}/{len(parsed_pages)} pages)")
    print(f"  Total wall: {time.perf_counter() - t0:.1f}s")
    print(f"  Artifacts: {save_dir}/")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
