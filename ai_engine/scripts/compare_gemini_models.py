"""N-way Gemini model comparison for the data-model planner.

Generalization of `compare_data_model_models.py` — instead of hard-coded
Flash-vs-Pro, this script accepts an arbitrary list of model variants
and produces a side-by-side comparison across all of them.

Default comparison (set via VARIANTS at the bottom):
  • gemini-2.5-pro              (current Pro tier)
  • gemini-3.1-pro-preview      (Gemini 3 reasoning tier)
  • gemini-3-flash-preview      (Gemini 3 cheap sibling — context floor)

Default fixtures: all 7 (restaurant, ecommerce, blog, portfolio, saas,
service_agency, multi_location_gym).

Outputs:
  • stdout — compact per-fixture diff vs first variant + final SUMMARY
  • docs/MODEL_COMPARISON_GEMINI_3WAY.md — full JSON for each (model, fixture)

Usage:
    docker exec -e OPENHANDS_SUPPRESS_BANNER=1 lucid-ai-ai_engine-1 \\
        python scripts/compare_gemini_models.py

Cost: ~$0.30-0.50. Pro 3.1 Preview pricing isn't published publicly so
the estimate uses a conservative placeholder (see _PRICING_USD_PER_1M).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _load_env_file(path: str) -> None:
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env_file(str(Path(__file__).resolve().parents[2] / ".env"))

from app.services.data_model import DataModel  # noqa: E402
from app.services.data_model_planner import plan_data_model  # noqa: E402
from app.services.landing_gemini import structured_distill as _original_distill  # noqa: E402


FIXTURES_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "website_plans"
OUTPUT_DOC = Path(__file__).resolve().parents[1] / "docs" / "MODEL_COMPARISON_GEMINI_3WAY.md"


# Approximate Vertex pricing per 1M tokens. Pro 3.1 Preview pricing is
# not publicly listed at the time of this run — I'm using "between 2.5
# Pro and the rumored 3 Pro GA pricing" as a conservative estimate.
# Re-check Google's pricing page when 3.1 GA's; update this dict.
_PRICING_USD_PER_1M = {
    "flash":    {"input": 0.075, "output": 0.30},
    "pro":      {"input": 1.25,  "output": 10.00},
    "flash-3":  {"input": 0.30,  "output": 2.50},   # preview-tier estimate
    "pro-3.1":  {"input": 2.00,  "output": 15.00},  # preview-tier estimate
}


PROFILES = [
    {
        "label": "Restaurant — Trattoria Bianca",
        "key":   "restaurant",
        "plan_file": "restaurant_plan.json",
        "intent": {"business_category": "restaurant",
                   "geographic_specifics": "Brooklyn, NY", "tone": "warm"},
        "purpose": {"primary_purpose": "brand_awareness",
                    "target_audience": "b2c_consumers"},
    },
    {
        "label": "E-commerce — Lens & Iron (vintage cameras)",
        "key":   "ecommerce",
        "plan_file": "ecommerce_plan.json",
        "intent": {"business_category": "ecommerce", "tone": "curated"},
        "purpose": {"primary_purpose": "conversion",
                    "target_audience": "b2c_consumers"},
    },
    {
        "label": "Blog — Slow Compile (tech blog)",
        "key":   "blog",
        "plan_file": "blog_plan.json",
        "intent": {"business_category": "content", "tone": "personal"},
        "purpose": {"primary_purpose": "brand_awareness",
                    "target_audience": "developers"},
    },
    {
        "label": "Portfolio — Marta Reis (photographer)",
        "key":   "portfolio",
        "plan_file": "portfolio_plan.json",
        "intent": {"business_category": "creative", "tone": "minimalist"},
        "purpose": {"primary_purpose": "brand_awareness",
                    "target_audience": "b2b_clients"},
    },
    {
        "label": "SaaS landing — Cortex Analytics",
        "key":   "saas",
        "plan_file": "saas_landing_plan.json",
        "intent": {"business_category": "saas", "tone": "professional"},
        "purpose": {"primary_purpose": "conversion",
                    "target_audience": "b2b_decision_makers"},
    },
    {
        "label": "Service business — Cornerstone Law Group",
        "key":   "service_agency",
        "plan_file": "service_agency_plan.json",
        "intent": {"business_category": "legal_services", "tone": "trustworthy"},
        "purpose": {"primary_purpose": "lead_generation",
                    "target_audience": "founders_and_operators"},
    },
    {
        "label": "Multi-location — Iron Loft Fitness",
        "key":   "multi_location_gym",
        "plan_file": "multi_location_gym_plan.json",
        "intent": {"business_category": "fitness", "tone": "energetic"},
        "purpose": {"primary_purpose": "conversion",
                    "target_audience": "b2c_consumers"},
    },
]


# Variants to compare. Order matters: the first variant is the baseline
# that the others are diff'd against in the doc.
VARIANTS = ["pro", "pro-3.1", "flash-3"]


# ── Per-run instrumentation ──────────────────────────────────────────

class _CallTracker:
    """Wraps `structured_distill` to count attempts + tokens."""
    def __init__(self) -> None:
        self.calls = 0
        self.in_tokens = 0
        self.out_tokens = 0

    async def __call__(self, *args, **kwargs):
        self.calls += 1
        prompt = kwargs.get("prompt") or (args[0] if args else "")
        result = await _original_distill(*args, **kwargs)
        self.in_tokens += max(1, len(prompt) // 4)
        self.out_tokens += max(1, len(result or "") // 4)
        return result


def _estimate_cost(in_tokens: int, out_tokens: int, variant: str) -> float:
    rates = _PRICING_USD_PER_1M[variant]
    return (in_tokens * rates["input"] + out_tokens * rates["output"]) / 1_000_000


async def _run_one(profile: dict, variant: str) -> dict[str, Any]:
    plan = json.loads((FIXTURES_DIR / profile["plan_file"]).read_text())
    tracker = _CallTracker()
    with patch(
        "app.services.data_model_planner.structured_distill",
        new=tracker,
    ):
        t0 = time.perf_counter()
        model = await plan_data_model(
            website_plan=plan,
            intent=profile["intent"],
            purpose_data=profile["purpose"],
            gemini_key=os.environ.get("GOOGLE_API_KEY", ""),
            project_id=f"compare3-{profile['key']}-{variant}",
            model_variant=variant,  # type: ignore[arg-type]
        )
        latency = time.perf_counter() - t0
    return {
        "model":      model,
        "attempts":   tracker.calls,
        "latency_s":  latency,
        "in_tokens":  tracker.in_tokens,
        "out_tokens": tracker.out_tokens,
        "cost_usd":   _estimate_cost(tracker.in_tokens, tracker.out_tokens, variant),
    }


# ── Diffing (vs baseline variant) ────────────────────────────────────

def _diff_models(baseline: DataModel, other: DataModel) -> dict[str, Any]:
    b_tables = {t.name: t for t in baseline.tables}
    o_tables = {t.name: t for t in other.tables}

    added_tables   = sorted(set(o_tables) - set(b_tables))
    removed_tables = sorted(set(b_tables) - set(o_tables))
    common         = sorted(set(b_tables) & set(o_tables))

    field_diffs: dict[str, dict[str, list[str]]] = {}
    for name in common:
        b_fields = {f.name for f in b_tables[name].fields}
        o_fields = {f.name for f in o_tables[name].fields}
        added = sorted(o_fields - b_fields)
        removed = sorted(b_fields - o_fields)
        if added or removed:
            field_diffs[name] = {"added": added, "removed": removed}

    return {
        "added_tables":       added_tables,
        "removed_tables":     removed_tables,
        "field_diffs":        field_diffs,
        "added_singletons":   sorted(set(other.singletons) - set(baseline.singletons)),
        "removed_singletons": sorted(set(baseline.singletons) - set(other.singletons)),
        "total_added_fields": sum(len(d["added"]) for d in field_diffs.values()),
    }


# ── Rendering ────────────────────────────────────────────────────────

def _format_tables(model: DataModel) -> str:
    return ", ".join(t.name for t in model.tables) or "_none_"


def _format_singletons(model: DataModel) -> str:
    return ", ".join(model.singletons.keys()) or "_none_"


def _stdout_block(profile: dict, results_by_variant: dict[str, dict]) -> None:
    print(f"\n=== {profile['key']} ===\n")
    for v in VARIANTS:
        r = results_by_variant[v]
        m = r["model"]
        flag = "" if (m.tables or m.singletons) else "  ← EMPTY FALLBACK"
        print(
            f"{v:<10} (${r['cost_usd']:.5f}, {r['latency_s']:.1f}s, "
            f"{r['attempts']} att): tables={len(m.tables)} singletons={len(m.singletons)}{flag}"
        )
        print(f"           tables=[{_format_tables(m)}]")
        print(f"           singletons=[{_format_singletons(m)}]")
    # Pairwise diffs vs first variant (baseline)
    baseline_v = VARIANTS[0]
    baseline_m = results_by_variant[baseline_v]["model"]
    for v in VARIANTS[1:]:
        diff = _diff_models(baseline_m, results_by_variant[v]["model"])
        any_diff = (diff["added_tables"] or diff["removed_tables"]
                    or diff["added_singletons"] or diff["removed_singletons"]
                    or diff["field_diffs"])
        if not any_diff:
            print(f"\n  {v} vs {baseline_v}: identical")
            continue
        print(f"\n  {v} vs {baseline_v}:")
        if diff["added_tables"]:
            print(f"    + {v} added tables: {', '.join(diff['added_tables'])}")
        if diff["removed_tables"]:
            print(f"    - {v} removed tables: {', '.join(diff['removed_tables'])}")
        if diff["added_singletons"]:
            print(f"    + {v} added singletons: {', '.join(diff['added_singletons'])}")
        if diff["removed_singletons"]:
            print(f"    - {v} removed singletons: {', '.join(diff['removed_singletons'])}")
        for tname, fd in diff["field_diffs"].items():
            if fd["added"]:
                print(f"    + {v} added fields to {tname}: {', '.join(fd['added'])}")
            if fd["removed"]:
                print(f"    - {v} removed fields from {tname}: {', '.join(fd['removed'])}")


def _render_doc(records: list[dict]) -> str:
    out: list[str] = []
    out.append(f"# Gemini model comparison — {', '.join(VARIANTS)}")
    out.append("")
    out.append(
        "Auto-generated by `scripts/compare_gemini_models.py`. Each "
        f"fixture is run once per variant. {len(VARIANTS)} variants × "
        f"{len(records)} fixtures = {len(VARIANTS) * len(records)} total "
        "Gemini calls."
    )
    out.append("")
    out.append("**Pricing caveat**: Gemini 3 family preview pricing isn't "
               "publicly listed. Cost columns for `pro-3.1` and `flash-3` "
               "use conservative estimates — see "
               "`_PRICING_USD_PER_1M` in the script. Token counts are "
               "exact (4-chars-per-token approximation).")
    out.append("")

    # ── Per-variant aggregate ─────────────────────────────────────
    out.append("## Aggregate per variant")
    out.append("")
    out.append("| Variant | Total cost | Total in/out tokens | Avg latency | Empty fallbacks |")
    out.append("|---|---|---|---|---|")
    for v in VARIANTS:
        total_cost   = sum(r["results"][v]["cost_usd"]   for r in records)
        total_in     = sum(r["results"][v]["in_tokens"]  for r in records)
        total_out    = sum(r["results"][v]["out_tokens"] for r in records)
        avg_latency  = sum(r["results"][v]["latency_s"]  for r in records) / len(records)
        empties = sum(
            1 for r in records
            if not r["results"][v]["model"].tables
            and not r["results"][v]["model"].singletons
        )
        out.append(
            f"| `{v}` | ${total_cost:.4f} | {total_in:,} / {total_out:,} | "
            f"{avg_latency:.1f}s | {empties} / {len(records)} |"
        )
    out.append("")

    # ── Per-fixture detail ────────────────────────────────────────
    for r in records:
        profile = r["profile"]
        results = r["results"]

        out.append(f"## {profile['label']}")
        out.append("")

        # Per-variant summary
        out.append("| Variant | Tables | Singletons | Cost | Latency | Attempts |")
        out.append("|---|---|---|---|---|---|")
        for v in VARIANTS:
            rr = results[v]
            m = rr["model"]
            flag = " ⚠️" if (not m.tables and not m.singletons) else ""
            out.append(
                f"| `{v}`{flag} | "
                f"{len(m.tables)} ({_format_tables(m)}) | "
                f"{len(m.singletons)} ({_format_singletons(m)}) | "
                f"${rr['cost_usd']:.5f} | "
                f"{rr['latency_s']:.1f}s | "
                f"{rr['attempts']} |"
            )
        out.append("")

        # Pairwise diffs vs first variant
        baseline_v = VARIANTS[0]
        baseline_m = results[baseline_v]["model"]
        for v in VARIANTS[1:]:
            diff = _diff_models(baseline_m, results[v]["model"])
            out.append(f"### Diff: `{v}` vs `{baseline_v}`")
            out.append("")
            any_diff = (diff["added_tables"] or diff["removed_tables"]
                        or diff["added_singletons"] or diff["removed_singletons"]
                        or diff["field_diffs"])
            if not any_diff:
                out.append("_(identical structure)_")
                out.append("")
                continue
            if diff["added_tables"]:
                out.append(f"- **+** tables: `{', '.join(diff['added_tables'])}`")
            if diff["removed_tables"]:
                out.append(f"- **−** tables: `{', '.join(diff['removed_tables'])}`")
            if diff["added_singletons"]:
                out.append(f"- **+** singletons: `{', '.join(diff['added_singletons'])}`")
            if diff["removed_singletons"]:
                out.append(f"- **−** singletons: `{', '.join(diff['removed_singletons'])}`")
            for tname, fd in diff["field_diffs"].items():
                if fd["added"]:
                    out.append(f"- **+** fields → `{tname}`: `{', '.join(fd['added'])}`")
                if fd["removed"]:
                    out.append(f"- **−** fields ← `{tname}`: `{', '.join(fd['removed'])}`")
            out.append("")

        # Full JSON per variant
        for v in VARIANTS:
            out.append(f"### `{v}` output")
            out.append("```json")
            out.append(results[v]["model"].model_dump_json(indent=2))
            out.append("```")
            out.append("")

    return "\n".join(out) + "\n"


# ── Entry point ──────────────────────────────────────────────────────

async def main() -> int:
    records: list[dict] = []

    for profile in PROFILES:
        print(f"\n--- running {profile['key']} ---")
        results: dict[str, dict] = {}
        for v in VARIANTS:
            print(f"   variant={v} …", flush=True)
            results[v] = await _run_one(profile, v)
        records.append({"profile": profile, "results": results})
        _stdout_block(profile, results)

    # SUMMARY
    print("\n=== SUMMARY ===\n")
    for v in VARIANTS:
        total_cost  = sum(r["results"][v]["cost_usd"]  for r in records)
        avg_latency = sum(r["results"][v]["latency_s"] for r in records) / len(records)
        empties = sum(
            1 for r in records
            if not r["results"][v]["model"].tables
            and not r["results"][v]["model"].singletons
        )
        print(f"  {v:<10}  cost=${total_cost:.4f}  avg_latency={avg_latency:.1f}s  "
              f"empty_fallbacks={empties}/{len(records)}")

    OUTPUT_DOC.write_text(_render_doc(records), encoding="utf-8")
    print(f"\nFull comparison doc → {OUTPUT_DOC}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
