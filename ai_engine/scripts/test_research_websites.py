"""E2E research-only test for full multi-page websites.

Runs Gemini deep research (no Claude, no schema build, no codegen) for 4
different website types, then rates the ===PAGES=== output on:
  • Page count vs the archetype floor
  • Section count per page (Home target ≥6, non-home ≥4)
  • Per-section detail (headline, layout, imagery, content fields populated)
  • Cross-page section uniqueness (same section list on every page = bad)

Goal: verify the upgraded ===PAGES=== prompt makes Gemini emit per-page
section detail with the same depth as a landing page's sections.

Run from ai_engine/:
    venv/bin/python scripts/test_research_websites.py
    venv/bin/python scripts/test_research_websites.py --only restaurant
    venv/bin/python scripts/test_research_websites.py --save-dir /tmp/research_dump
"""
from __future__ import annotations

import argparse
import asyncio
import functools
import json
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


# ── Test cases — one per multi-page archetype ────────────────────────────
CASES = {
    "restaurant": {
        "prompt": "a website for a southern-italian trattoria in the West Village called Da Marco — menu, reservations, private events, story",
        "archetype": "consumer_website",
        "page_floor": 5,
    },
    "portfolio": {
        "prompt": "a portfolio site for a minimalist architecture studio called North/Field that designs cabins and small homes in the Pacific Northwest",
        "archetype": "portfolio",
        "page_floor": 5,
    },
    "blog": {
        "prompt": "a long-form magazine and blog site about urbanism and public-space design called The Common, with categories, author profiles, and a weekly newsletter",
        "archetype": "blog",
        "page_floor": 6,
    },
    "marketplace": {
        "prompt": "a peer-to-peer marketplace for vintage analog cameras called Shutter — buyers browse listings by format, sellers list their gear with photos and condition notes",
        "archetype": "marketplace",
        "page_floor": 6,
    },
}


# ── Capture WS events silently ────────────────────────────────────────────
class CaptureWS:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def send_json(self, payload: dict) -> None:
        self.events.append(payload)


# ── Scorer ────────────────────────────────────────────────────────────────
def score_pages(pages: list[dict], archetype: str, page_floor: int) -> dict:
    """Score one parsed pages list. Returns dict with sub-scores + total /100."""
    n_pages = len(pages)

    # 1. Page-count score (max 20)
    if n_pages >= page_floor + 2:
        page_count_score = 20
    elif n_pages >= page_floor:
        page_count_score = 16
    elif n_pages >= max(2, page_floor - 2):
        page_count_score = 8
    else:
        page_count_score = 0

    # 2. Section count per page (max 25)
    home_sections = 0
    non_home_section_counts: list[int] = []
    for p in pages:
        n_sec = len(p.get("sections", []))
        if (p.get("path") or "").rstrip("/") in ("", "/"):
            home_sections = n_sec
        else:
            non_home_section_counts.append(n_sec)

    home_score = 0
    if home_sections >= 6:
        home_score = 12
    elif home_sections >= 4:
        home_score = 8
    elif home_sections >= 2:
        home_score = 4

    if non_home_section_counts:
        avg_non_home = sum(non_home_section_counts) / len(non_home_section_counts)
    else:
        avg_non_home = 0
    if avg_non_home >= 4:
        non_home_score = 13
    elif avg_non_home >= 3:
        non_home_score = 9
    elif avg_non_home >= 2:
        non_home_score = 5
    else:
        non_home_score = 0
    section_count_score = home_score + non_home_score

    # 3. Per-section detail (max 30) — fields populated across all sections
    total_sections = 0
    field_hits = {"headline": 0, "layout": 0, "imagery": 0, "content": 0, "animation": 0}
    for p in pages:
        for s in p.get("sections", []):
            if not isinstance(s, dict):
                continue
            total_sections += 1
            if s.get("headline"):
                field_hits["headline"] += 1
            content = s.get("content") or {}
            if content.get("layout"):
                field_hits["layout"] += 1
            if content.get("imagery"):
                field_hits["imagery"] += 1
            if content.get("items"):
                field_hits["content"] += 1
            if s.get("animation"):
                field_hits["animation"] += 1

    if total_sections == 0:
        detail_score = 0
        avg_fill_pct = 0.0
    else:
        # 5 fields × total_sections = max possible hits
        hit_ratio = sum(field_hits.values()) / (5 * total_sections)
        avg_fill_pct = hit_ratio * 100
        detail_score = round(hit_ratio * 30)

    # 4. Cross-page section uniqueness (max 15)
    page_section_signatures: list[tuple[str, ...]] = []
    for p in pages:
        types = tuple(
            sorted(
                s.get("type", "") for s in p.get("sections", []) if isinstance(s, dict)
            )
        )
        if types:
            page_section_signatures.append(types)
    distinct_sigs = len(set(page_section_signatures))
    if not page_section_signatures:
        uniqueness_score = 0
    else:
        ratio = distinct_sigs / len(page_section_signatures)
        uniqueness_score = round(ratio * 15)

    # 5. Hero copy populated per page (max 10)
    hero_filled = sum(1 for p in pages if p.get("hero_headline"))
    if not pages:
        hero_score = 0
    else:
        hero_score = round((hero_filled / len(pages)) * 10)

    total = (
        page_count_score
        + section_count_score
        + detail_score
        + uniqueness_score
        + hero_score
    )

    return {
        "n_pages": n_pages,
        "page_floor": page_floor,
        "home_sections": home_sections,
        "avg_non_home_sections": round(avg_non_home, 1),
        "total_sections": total_sections,
        "field_fill_pct": round(avg_fill_pct, 1),
        "field_hits": field_hits,
        "distinct_sig_ratio": round(
            distinct_sigs / max(1, len(page_section_signatures)), 2
        ),
        "hero_filled": hero_filled,
        "scores": {
            "page_count": page_count_score,
            "section_counts": section_count_score,
            "per_section_detail": detail_score,
            "section_uniqueness": uniqueness_score,
            "hero_copy": hero_score,
        },
        "total": total,
    }


def grade(total: int) -> str:
    if total >= 85:
        return "A — production-ready depth"
    if total >= 70:
        return "B — solid, minor gaps"
    if total >= 55:
        return "C — usable but thin in places"
    if total >= 40:
        return "D — prompt is not landing"
    return "F — research did not produce structured pages"


# ── Per-case runner ───────────────────────────────────────────────────────
async def run_one(name: str, case: dict, stack: str, save_dir: str | None) -> dict:
    prompt = case["prompt"]
    expected_archetype = case["archetype"]
    page_floor = case["page_floor"]

    print(f"\n{'═'*72}")
    print(f"CASE: {name}")
    print(f"  Prompt: {prompt[:90]}…" if len(prompt) > 90 else f"  Prompt: {prompt}")
    print(f"  Expected archetype: {expected_archetype}  (page floor: {page_floor})")
    print(f"{'═'*72}")

    ws = CaptureWS()
    from app.services.project_generator import _expand_short_prompt, gemini_deep_research
    from app.services.project_schema import _parse_pages_block, _extract_block
    from knowledge.loader import classify_project_type_ai

    t_class = time.perf_counter()
    classification = await classify_project_type_ai(prompt, GEMINI_KEY)
    t_class = time.perf_counter() - t_class
    archetype = classification.get("layout_archetype")
    domain = classification.get("domain")
    print(f"  Classified: archetype={archetype} domain={domain} ({t_class:.1f}s)")

    if archetype != expected_archetype:
        print(
            f"  ⚠ Classification mismatch (got {archetype!r}, expected "
            f"{expected_archetype!r}) — overriding to keep test consistent"
        )
        classification["layout_archetype"] = expected_archetype
        archetype = expected_archetype

    t_expand = time.perf_counter()
    expanded = await _expand_short_prompt(prompt, archetype, domain, GEMINI_KEY, ws)
    t_expand = time.perf_counter() - t_expand
    print(f"  Expanded prompt ({t_expand:.1f}s) → {len(expanded)} chars")

    t_research = time.perf_counter()
    research = await gemini_deep_research(expanded, classification, stack, GEMINI_KEY, ws)
    t_research = time.perf_counter() - t_research
    print(f"  Research done ({t_research:.1f}s) → {len(research)} chars total")

    pages_block = _extract_block(research, "===PAGES===", max_chars=20000) or ""
    print(f"  ===PAGES=== block: {len(pages_block)} chars")

    parsed_pages = _parse_pages_block(pages_block)
    score = score_pages(parsed_pages, archetype, page_floor)
    score["name"] = name
    score["archetype"] = archetype
    score["timing"] = {
        "classify_s": round(t_class, 1),
        "expand_s": round(t_expand, 1),
        "research_s": round(t_research, 1),
    }
    score["grade"] = grade(score["total"])

    if save_dir:
        os.makedirs(save_dir, exist_ok=True)
        with open(os.path.join(save_dir, f"{name}_research.txt"), "w") as f:
            f.write(research)
        with open(os.path.join(save_dir, f"{name}_pages_block.txt"), "w") as f:
            f.write(pages_block)
        with open(os.path.join(save_dir, f"{name}_parsed.json"), "w") as f:
            json.dump(parsed_pages, f, indent=2, ensure_ascii=False)
        with open(os.path.join(save_dir, f"{name}_score.json"), "w") as f:
            json.dump(score, f, indent=2, ensure_ascii=False)
        print(f"  Saved → {save_dir}/{name}_*")

    print()
    print(f"  ── Per-page breakdown ──")
    for p in parsed_pages:
        title = p.get("title", "?")
        path = p.get("path", "?")
        sections = p.get("sections", [])
        section_types = [
            s.get("type", "?") for s in sections if isinstance(s, dict)
        ]
        print(
            f"    • {title:<14s} {path:<22s}  "
            f"{len(sections):>2d} sections  "
            f"hero={'Y' if p.get('hero_headline') else 'n'}  "
            f"types=[{', '.join(section_types[:6])}{'…' if len(section_types) > 6 else ''}]"
        )

    print()
    print(f"  ── Score: {score['total']}/100 → {score['grade']} ──")
    for k, v in score["scores"].items():
        print(f"    {k:<22s} {v:>3d}")
    print(
        f"    fields populated: {score['field_fill_pct']}%  "
        f"unique-sig ratio: {score['distinct_sig_ratio']}  "
        f"hero-filled: {score['hero_filled']}/{score['n_pages']}"
    )

    return score


# ── Summary table ─────────────────────────────────────────────────────────
def print_summary(results: list[dict]) -> None:
    print(f"\n{'═'*72}")
    print("FINAL RATINGS")
    print(f"{'═'*72}")
    print(f"  {'case':<14s} {'pages':>6s} {'home_sec':>9s} {'avg_other':>10s} "
          f"{'fill%':>7s} {'uniq':>6s} {'total':>6s}  grade")
    print(f"  {'-'*14} {'-'*6} {'-'*9} {'-'*10} {'-'*7} {'-'*6} {'-'*6}  ─────")
    for r in results:
        print(
            f"  {r['name']:<14s} "
            f"{r['n_pages']:>6d} "
            f"{r['home_sections']:>9d} "
            f"{r['avg_non_home_sections']:>10.1f} "
            f"{r['field_fill_pct']:>6.1f}% "
            f"{r['distinct_sig_ratio']:>6.2f} "
            f"{r['total']:>6d}  {r['grade']}"
        )

    avg_total = sum(r["total"] for r in results) / max(1, len(results))
    print(f"\n  Average total: {avg_total:.1f}/100")
    print(f"{'═'*72}")


# ── Main ──────────────────────────────────────────────────────────────────
async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--only", default=None,
        help="Run only one case by name (restaurant/portfolio/blog/marketplace)",
    )
    parser.add_argument("--stack", default="nextjs")
    parser.add_argument(
        "--save-dir", default="/tmp/research_test_dump",
        help="Where to dump full research blobs + parsed JSON for inspection",
    )
    args = parser.parse_args()

    selected = (
        {args.only: CASES[args.only]} if args.only and args.only in CASES else CASES
    )
    if args.only and args.only not in CASES:
        print(f"Unknown case: {args.only}. Available: {list(CASES)}")
        return 1

    print(f"\n{'#'*72}")
    print(f"# E2E research test — {len(selected)} multi-page website case(s)")
    print(f"# Save dir: {args.save_dir}")
    print(f"{'#'*72}")

    t0 = time.perf_counter()
    results: list[dict] = []
    for name, case in selected.items():
        try:
            score = await run_one(name, case, args.stack, args.save_dir)
            results.append(score)
        except Exception as exc:
            print(f"\n❌ Case {name!r} crashed: {exc}")
            import traceback
            traceback.print_exc()
    total_dt = time.perf_counter() - t0

    if results:
        print_summary(results)
    print(f"\n  Total wall time: {total_dt:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
