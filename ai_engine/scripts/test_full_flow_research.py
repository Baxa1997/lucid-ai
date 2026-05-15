"""3-prompt research validation across landing / website / admin modes.

For each prompt:
  1. classify_project_type_ai     — does it pick the right archetype?
  2. detect_scope_warnings        — does it trip the "out of scope" gate?
  3. analyze_intent               — does Gemini ground the prompt?
  4. (admin only) gemini_deep_research → check if ===ENTITIES=== block appears

Skips Anthropic calls. Captures artifacts to /tmp/full_flow_research/.
"""
from __future__ import annotations

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
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")

GEMINI_KEY = os.environ.get("GOOGLE_API_KEY", "")
if not GEMINI_KEY:
    print("ERROR: GOOGLE_API_KEY must be set in ../.env")
    sys.exit(1)


OUT_ROOT = Path("/tmp/full_flow_research")
OUT_ROOT.mkdir(parents=True, exist_ok=True)


class CaptureWS:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def send_json(self, payload: dict) -> None:
        self.events.append(payload)


TESTS = [
    {
        "id": "L1",
        "mode": "landing",
        "prompt": "Italian restaurant in Brooklyn called Trattoria del Sole, family-owned since 1985",
        "expected_archetype": "single_page_landing",
    },
    {
        "id": "W1",
        "mode": "website",
        "prompt": "Italian restaurant website with menu, about us, reservations, and contact pages",
        "expected_archetype": "consumer_website",
    },
    {
        "id": "A1",
        "mode": "admin",
        "prompt": "Admin dashboard for restaurant to manage menu, dishes, reservations, and customers",
        "expected_archetype": "admin_dashboard",
    },
]


async def run_one(test: dict) -> dict:
    print(f"\n{'='*72}\n  {test['id']} — {test['mode']} — {test['prompt']!r}\n{'='*72}")
    out_dir = OUT_ROOT / test["mode"] / test["id"]
    out_dir.mkdir(parents=True, exist_ok=True)

    ws = CaptureWS()
    summary: dict = {"id": test["id"], "mode": test["mode"], "prompt": test["prompt"]}

    # ── 1. Classifier ───────────────────────────────────────────────
    from knowledge.loader import (
        classify_project_type_ai, get_structural_family, LAYOUT_ARCHETYPES,
    )
    t0 = time.perf_counter()
    classification = await classify_project_type_ai(test["prompt"])
    dt_classify = time.perf_counter() - t0
    archetype = classification.get("layout_archetype")
    family = get_structural_family(archetype or "")
    print(f"  classify ({dt_classify:.1f}s): archetype={archetype} domain={classification.get('domain')} family={family}")
    print(f"  expected={test['expected_archetype']}  match={archetype == test['expected_archetype']}")

    (out_dir / "classification.json").write_text(json.dumps(classification, indent=2))
    summary["classify"] = {
        "archetype": archetype,
        "domain": classification.get("domain"),
        "family": family,
        "expected": test["expected_archetype"],
        "match": archetype == test["expected_archetype"],
        "is_single_page": classification.get("is_single_page"),
        "nav_style": classification.get("nav_style"),
        "has_admin_features": classification.get("has_admin_features"),
        "has_sidebar": classification.get("has_sidebar"),
        "seconds": round(dt_classify, 2),
    }

    # ── 2. Scope warnings (heuristic) ───────────────────────────────
    from app.services.prompt_guards import is_gibberish, detect_scope_warnings
    warnings = detect_scope_warnings(test["prompt"])
    gibberish = is_gibberish(test["prompt"])
    print(f"  scope_warnings: {warnings}  gibberish={gibberish}")
    summary["scope_warnings"] = warnings
    summary["gibberish"] = gibberish

    # ── 3. Intent (used by landing AND website pipelines) ───────────
    from app.services.landing_intent import analyze_intent
    t0 = time.perf_counter()
    try:
        intent = await analyze_intent(test["prompt"], classification, timeout_s=90.0)
        dt_intent = time.perf_counter() - t0
        print(f"  intent ({dt_intent:.1f}s): category={intent.get('business_category')!r}"
              f" purpose={intent.get('primary_purpose')!r}"
              f" geo={intent.get('geographic_scope')!r}"
              f" tone={intent.get('tone')!r}"
              f" clarity={intent.get('clarity_level')!r}"
              f" is_fallback={intent.get('is_fallback', False)}")
        (out_dir / "intent.json").write_text(json.dumps(intent, indent=2))
        summary["intent"] = {
            "business_category": intent.get("business_category"),
            "primary_purpose": intent.get("primary_purpose"),
            "geographic_scope": intent.get("geographic_scope"),
            "geographic_specifics": intent.get("geographic_specifics"),
            "tone": intent.get("tone"),
            "clarity_level": intent.get("clarity_level"),
            "must_have_sections": intent.get("must_have_sections", [])[:10],
            "ambiguity_flags": intent.get("ambiguity_flags", []),
            "is_fallback": intent.get("is_fallback", False),
            "seconds": round(dt_intent, 2),
        }
    except Exception as exc:
        print(f"  intent FAILED: {exc}")
        summary["intent"] = {"error": str(exc)}

    # ── 4. Mode-specific deep checks ────────────────────────────────
    if test["mode"] == "admin":
        # For admin: check what gemini_deep_research produces — does it have
        # ===ENTITIES===? That's what backend_schema needs.
        from app.services.project_generator import gemini_deep_research
        t0 = time.perf_counter()
        try:
            research = await asyncio.wait_for(
                gemini_deep_research(
                    description=test["prompt"],
                    classification=classification,
                    stack="nextjs",
                    websocket=ws,
                ),
                timeout=300.0,
            )
            dt_research = time.perf_counter() - t0
            print(f"  deep_research ({dt_research:.1f}s): {len(research)} chars")
            (out_dir / "deep_research.md").write_text(research or "")

            headers = [
                "===CLASSIFICATION===", "===SECTIONS===", "===PAGES===",
                "===ENTITIES===", "===VIBE===", "===LAYOUT_BLUEPRINT===",
                "===CULTURAL_ATMOSPHERE===", "===COPY_TONE===",
                "===CSS_VARIABLES===", "===FONTS===",
            ]
            found = [h for h in headers if h in (research or "")]
            print(f"  headers present: {[h.strip('=') for h in found]}")

            # Try to extract entities block for inspection
            entities_block = ""
            if "===ENTITIES===" in (research or ""):
                start = research.index("===ENTITIES===") + len("===ENTITIES===")
                # Find next ===HEADER===
                rest = research[start:]
                next_idx = rest.find("\n===")
                entities_block = rest[:next_idx] if next_idx > 0 else rest[:2000]
                (out_dir / "entities_extract.txt").write_text(entities_block)
                print(f"  ENTITIES block: {len(entities_block)} chars — first 200: {entities_block[:200]!r}")

            summary["deep_research"] = {
                "chars": len(research or ""),
                "headers_present": [h.strip("=") for h in found],
                "has_entities_block": "===ENTITIES===" in (research or ""),
                "entities_block_chars": len(entities_block),
                "seconds": round(dt_research, 2),
            }
        except asyncio.TimeoutError:
            print(f"  deep_research TIMED OUT after 300s")
            summary["deep_research"] = {"error": "timeout"}
        except Exception as exc:
            print(f"  deep_research FAILED: {exc}")
            summary["deep_research"] = {"error": str(exc)}

    elif test["mode"] == "landing":
        # For landing: also run domain + design research (parallel) so we have
        # real data to validate vs SaaS contamination.
        from app.services.landing_domain_research import run_domain_research
        from app.services.landing_design_research import run_design_research
        if "intent" in summary and "error" not in summary["intent"]:
            t0 = time.perf_counter()
            try:
                domain_res, design_res = await asyncio.gather(
                    run_domain_research(intent, timeout_s=180.0),
                    run_design_research(intent, timeout_s=180.0),
                )
                dt_res = time.perf_counter() - t0
                print(f"  domain+design research ({dt_res:.1f}s):")
                d_sum = domain_res.get("_summary", {})
                des_sum = design_res.get("_summary", {})
                print(f"    domain: calls_succeeded={d_sum.get('calls_succeeded', '?')}/4 sources={d_sum.get('total_sources', '?')}")
                print(f"    design: calls_succeeded={des_sum.get('calls_succeeded', '?')}/4 sources={des_sum.get('total_sources', '?')}")
                (out_dir / "domain_research.json").write_text(json.dumps(domain_res, indent=2)[:200_000])
                (out_dir / "design_research.json").write_text(json.dumps(design_res, indent=2)[:200_000])
                summary["domain_research_summary"] = d_sum
                summary["design_research_summary"] = des_sum
                summary["landing_research_seconds"] = round(dt_res, 2)
            except Exception as exc:
                print(f"  domain+design FAILED: {exc}")
                summary["landing_research_error"] = str(exc)

    elif test["mode"] == "website":
        # For website: also run purpose_classifier + intent confirmed.
        # Skip deep research to save budget.
        from app.services.purpose_classifier import classify_purpose
        t0 = time.perf_counter()
        try:
            purpose = await classify_purpose(
                user_prompt=test["prompt"],
                clarity_answers={},
                gemini_key=GEMINI_KEY,
            )
            dt_purpose = time.perf_counter() - t0
            print(f"  purpose ({dt_purpose:.1f}s): primary={purpose.get('primary_purpose')!r}"
                  f" industry={purpose.get('industry')!r} conf={purpose.get('confidence')}")
            (out_dir / "purpose.json").write_text(json.dumps(purpose, indent=2))
            summary["purpose"] = {
                "primary_purpose": purpose.get("primary_purpose"),
                "industry": purpose.get("industry"),
                "target_audience": purpose.get("target_audience"),
                "confidence": purpose.get("confidence"),
                "seconds": round(dt_purpose, 2),
            }
        except Exception as exc:
            print(f"  purpose FAILED: {exc}")
            summary["purpose"] = {"error": str(exc)}

    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    print(f"  → saved to {out_dir}")
    return summary


async def main() -> int:
    print(f"Output dir: {OUT_ROOT}\n")

    all_summaries: list[dict] = []
    for test in TESTS:
        try:
            s = await run_one(test)
            all_summaries.append(s)
        except Exception as exc:
            import traceback
            traceback.print_exc()
            all_summaries.append({"id": test["id"], "error": str(exc)})

    # Aggregate
    print(f"\n{'='*72}\n  AGGREGATE\n{'='*72}")
    for s in all_summaries:
        if "error" in s:
            print(f"  {s.get('id', '?')}: ERROR — {s['error']}")
            continue
        c = s.get("classify", {})
        i = s.get("intent", {})
        line = (f"  {s['id']} [{s['mode']:8}] archetype={c.get('archetype'):20} match={c.get('match')}"
                f" intent_fallback={i.get('is_fallback', '?')}")
        print(line)

    (OUT_ROOT / "aggregate.json").write_text(json.dumps(all_summaries, indent=2))
    print(f"\nAggregate saved to {OUT_ROOT}/aggregate.json")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
