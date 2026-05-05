"""E2E research-only test for landing pages — 5 different conversion goals.

Runs Gemini deep research (no Claude) for 5 prompts spanning recruitment,
beta-signup, booking, donations, and lead-gen. Reports for each:
  • PAGE_INTENT extraction (goal, audience, must-haves, amplifiers)
  • SECTIONS list (type + headline)
  • Coverage check: did all must-haves land? did ≥2 amplifiers land?
  • Generic-flagging: any section_slug that looks template-shaped

Run from ai_engine/:
    venv/bin/python scripts/test_landing_5cases.py
"""
from __future__ import annotations

import asyncio
import functools
import json
import os
import re
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


CASES = [
    {
        "name": "dental_recruit",
        "prompt": "landing page for a high-end dental clinic in Austin looking to hire experienced dental hygienists",
        "goal_kind": "recruitment",
    },
    {
        "name": "vibe_coding_beta",
        "prompt": "beta signup landing page for a new AI vibe-coding tool aimed at indie hackers and solo founders",
        "goal_kind": "beta_signup",
    },
    {
        "name": "greek_charter",
        "prompt": "landing page to drive bookings for a luxury sailing charter business in the Greek islands, sunset sails and multi-day cruises",
        "goal_kind": "booking",
    },
    {
        "name": "amazon_reforest",
        "prompt": "fundraising landing page for a nonprofit reforesting the Brazilian Amazon — sponsor a tree, see your forest grow on a map",
        "goal_kind": "donation",
    },
    {
        "name": "luxe_interior",
        "prompt": "lead-generation landing page for a high-end interior design studio in Beverly Hills targeting luxury homeowners and developers",
        "goal_kind": "lead_gen",
    },
]


class WS:
    async def send_json(self, p: dict) -> None:
        pass


# ── Heuristics for "generic" detection ────────────────────────────────────
GENERIC_SECTION_SLUGS = {
    "features", "pricing", "how_it_works", "testimonials", "cta", "cta_final",
    "about", "about_us", "contact", "faq", "newsletter", "footer", "hero",
}
GENERIC_HEADLINE_PATTERNS = [
    r"^welcome to",
    r"^get started",
    r"^learn more",
    r"^our (services|features)",
    r"about us",
    r"^why choose",
    r"^our story",
    r"contact us",
]
GENERIC_HEADLINE_RE = re.compile("|".join(GENERIC_HEADLINE_PATTERNS), re.IGNORECASE)


def flag_generics(sections: list[dict]) -> list[str]:
    """Return human-readable warnings for sections that look generic."""
    warnings: list[str] = []
    for s in sections:
        stype = (s.get("type") or "").lower()
        head = s.get("headline") or ""
        if stype in GENERIC_SECTION_SLUGS and stype not in {"hero"}:
            warnings.append(
                f"  ⚠ generic slug `{stype}` — would expect goal-specific name"
            )
        if head and GENERIC_HEADLINE_RE.search(head):
            warnings.append(f"  ⚠ generic headline on `{stype}`: {head!r}")
    return warnings


def parse_intent(intent_block: str) -> dict:
    """Pull the four fields out of a PAGE_INTENT block as plain text."""
    out = {
        "primary_goal": "",
        "target_audience": "",
        "must_have_sections": [],
        "conversion_amplifiers": [],
        "intent_self_check": "",
    }
    if not intent_block:
        return out

    def grab(name: str, single_line: bool = False) -> str:
        m = re.search(rf"{name}:\s*(.+?)(?=\n[a-z_]+:|$)", intent_block, re.DOTALL)
        if not m:
            return ""
        v = m.group(1).strip()
        if single_line:
            v = v.split("\n", 1)[0].strip()
        return v

    out["primary_goal"] = grab("primary_goal").split("\n", 1)[0].strip()
    out["target_audience"] = grab("target_audience").split("\n", 1)[0].strip()
    out["intent_self_check"] = grab("intent_self_check").split("\n", 1)[0].strip()

    def parse_list(name: str) -> list[str]:
        body = grab(name)
        out_items: list[str] = []
        for line in body.splitlines():
            ls = line.strip()
            m = re.match(r"^-\s*([A-Za-z0-9_]+)\s*(?:—|-|:)?", ls)
            if m:
                out_items.append(m.group(1))
        return out_items

    out["must_have_sections"] = parse_list("must_have_sections")
    out["conversion_amplifiers"] = parse_list("conversion_amplifiers")
    return out


async def run_case(case: dict, save_dir: str) -> dict:
    name = case["name"]
    prompt = case["prompt"]
    print(f"\n{'═'*72}\nCASE: {name}\n  {prompt}\n{'═'*72}")

    from app.services.project_generator import (
        _expand_short_prompt, gemini_deep_research,
    )
    from app.services.project_schema import _extract_block, _parse_sections
    from knowledge.loader import classify_project_type_ai

    ws = WS()
    t0 = time.perf_counter()

    cls = await classify_project_type_ai(prompt, GEMINI_KEY)
    archetype = cls.get("layout_archetype")
    domain = cls.get("domain")
    print(f"  classified → {archetype} / {domain}")

    if archetype != "single_page_landing":
        print(f"  ⚠ classified as {archetype} not single_page_landing — overriding")
        cls["layout_archetype"] = "single_page_landing"
        cls["is_single_page"] = True

    expanded = await _expand_short_prompt(prompt, "single_page_landing", domain, GEMINI_KEY, ws)
    research = await gemini_deep_research(expanded, cls, "nextjs", GEMINI_KEY, ws)
    dt = time.perf_counter() - t0

    os.makedirs(save_dir, exist_ok=True)
    open(os.path.join(save_dir, f"{name}_full.txt"), "w").write(research)

    intent_block = _extract_block(research, "===PAGE_INTENT===", max_chars=6000) or ""
    sections_block = _extract_block(research, "===SECTIONS===", max_chars=20000) or ""
    open(os.path.join(save_dir, f"{name}_intent.txt"), "w").write(intent_block)
    open(os.path.join(save_dir, f"{name}_sections.txt"), "w").write(sections_block)

    intent = parse_intent(intent_block)
    sections = _parse_sections(sections_block)
    section_types = {(s.get("type") or "").lower() for s in sections}

    # Coverage checks
    must_have_present = [m for m in intent["must_have_sections"] if m.lower() in section_types]
    must_have_missing = [m for m in intent["must_have_sections"] if m.lower() not in section_types]
    amplifiers_present = [a for a in intent["conversion_amplifiers"] if a.lower() in section_types]

    score = {
        "name": name,
        "research_chars": len(research),
        "elapsed_s": round(dt, 1),
        "archetype": archetype,
        "domain": domain,
        "primary_goal": intent["primary_goal"],
        "target_audience": intent["target_audience"][:120],
        "must_have_count": len(intent["must_have_sections"]),
        "must_have_present_count": len(must_have_present),
        "must_have_missing": must_have_missing,
        "amplifier_count_in_intent": len(intent["conversion_amplifiers"]),
        "amplifier_present_count": len(amplifiers_present),
        "amplifier_present": amplifiers_present,
        "total_sections": len(sections),
        "section_types": [s.get("type") for s in sections],
        "section_headlines": [(s.get("type"), s.get("headline", "")[:70]) for s in sections],
        "intent_self_check": intent["intent_self_check"][:200],
    }
    score["generic_warnings"] = flag_generics(sections)

    # Print compact result
    print(f"\n  primary_goal: {intent['primary_goal'][:120]}")
    print(f"  target_audience: {intent['target_audience'][:120]}")
    print(
        f"\n  must-have coverage: {len(must_have_present)}/{len(intent['must_have_sections'])}"
        + (f"  ❌ missing: {must_have_missing}" if must_have_missing else "  ✅")
    )
    print(
        f"  amplifier coverage: {len(amplifiers_present)}/{len(intent['conversion_amplifiers'])} "
        f"(rule: ≥2)  "
        + ("✅" if len(amplifiers_present) >= 2 else "❌ FEWER than 2 amplifiers landed")
    )

    print(f"\n  SECTIONS ({len(sections)}):")
    for stype, head in score["section_headlines"]:
        marker = ""
        if stype in [m.lower() for m in intent["must_have_sections"]]:
            marker = " [MUST]"
        elif stype in [a.lower() for a in intent["conversion_amplifiers"]]:
            marker = " [AMP]"
        print(f"    • {stype:<32s}{marker:<7s}  {head}")

    if score["generic_warnings"]:
        print(f"\n  GENERIC FLAGS:")
        for w in score["generic_warnings"]:
            print(w)
    else:
        print(f"\n  GENERIC FLAGS: none ✅")

    print(f"\n  research time: {dt:.1f}s")
    return score


def print_summary(results: list[dict]) -> None:
    print(f"\n\n{'#'*72}\n# FINAL SUMMARY — 5 landing-page prompts\n{'#'*72}\n")
    for r in results:
        mh_ok = r["must_have_present_count"] == r["must_have_count"]
        amp_ok = r["amplifier_present_count"] >= 2
        gen_ok = len(r["generic_warnings"]) == 0
        flags = (
            ("✅" if mh_ok else "❌") + " must-have | "
            + ("✅" if amp_ok else "❌") + " ≥2 amp | "
            + ("✅" if gen_ok else "⚠ ") + " generics"
        )
        print(f"  {r['name']:<22s} {r['total_sections']:>2d} sec | {flags}")
        print(f"    goal: {r['primary_goal'][:100]}")
        if r["must_have_missing"]:
            print(f"    missing must-haves: {r['must_have_missing']}")
        if r["generic_warnings"]:
            for w in r["generic_warnings"]:
                print(f"    {w.strip()}")
        print()


async def main() -> int:
    save_dir = "/tmp/landing_5cases_dump"
    print(f"# Saving artifacts to {save_dir}/")
    results: list[dict] = []
    t0 = time.perf_counter()
    for case in CASES:
        try:
            r = await run_case(case, save_dir)
            results.append(r)
        except Exception as e:
            print(f"\n❌ case {case['name']!r} crashed: {e}")
            import traceback
            traceback.print_exc()
    dt = time.perf_counter() - t0
    if results:
        print_summary(results)
        with open(os.path.join(save_dir, "summary.json"), "w") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\n  Total wall time: {dt:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
