"""A/B compare Gemini Flash vs Pro on the data-model planner.

For each of the 5 fixture website plans, runs `plan_data_model` twice —
once with `model_variant="flash"` and once with `model_variant="pro"` —
captures cost, latency, and retry count, then renders a side-by-side
diff to stdout AND writes a full markdown report to
`docs/MODEL_COMPARISON_FLASH_VS_PRO.md`.

Usage:
    docker exec -e OPENHANDS_SUPPRESS_BANNER=1 lucid-ai-ai_engine-1 \\
        python scripts/compare_data_model_models.py

Cost: ~$0.04-0.05 per full run (5 fixtures × ~$0.008 Pro + ~$0 Flash).
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
OUTPUT_DOC = Path(__file__).resolve().parents[1] / "docs" / "MODEL_COMPARISON_FLASH_VS_PRO.md"


# Vertex / Gemini pricing per 1M tokens. Approximate — Pro pricing in
# particular has tiered behavior; numbers below are the published list
# price for short contexts. Output tokens cost much more on Pro.
_PRICING_USD_PER_1M = {
    "flash": {"input": 0.075, "output": 0.30},
    "pro":   {"input": 1.25,  "output": 10.00},
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
]


# ── Per-run instrumentation ──────────────────────────────────────────

class _CallTracker:
    """Wraps `structured_distill` to count attempts and approximate
    token usage from prompt/response lengths (4 chars ≈ 1 token).
    Provides retry count (= calls) and a token-derived cost estimate
    without changing the planner's API.
    """
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
    """Run plan_data_model once for (profile, variant) and capture stats."""
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
            project_id=f"compare-{profile['key']}-{variant}",
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


# ── Diffing ──────────────────────────────────────────────────────────

def _diff_models(flash: DataModel, pro: DataModel) -> dict[str, Any]:
    """Compute table-, field-, and singleton-level differences."""
    f_tables = {t.name: t for t in flash.tables}
    p_tables = {t.name: t for t in pro.tables}

    added_tables   = sorted(set(p_tables) - set(f_tables))
    removed_tables = sorted(set(f_tables) - set(p_tables))
    common_tables  = sorted(set(f_tables) & set(p_tables))

    field_diffs: dict[str, dict[str, list[str]]] = {}
    for name in common_tables:
        f_fields = {f.name for f in f_tables[name].fields}
        p_fields = {f.name for f in p_tables[name].fields}
        added = sorted(p_fields - f_fields)
        removed = sorted(f_fields - p_fields)
        if added or removed:
            field_diffs[name] = {"added": added, "removed": removed}

    added_singletons   = sorted(set(pro.singletons) - set(flash.singletons))
    removed_singletons = sorted(set(flash.singletons) - set(pro.singletons))

    total_added_fields = sum(len(d["added"]) for d in field_diffs.values())

    return {
        "added_tables":       added_tables,
        "removed_tables":     removed_tables,
        "common_tables":      common_tables,
        "field_diffs":        field_diffs,
        "added_singletons":   added_singletons,
        "removed_singletons": removed_singletons,
        "total_added_fields": total_added_fields,
    }


def _qualitative_verdict(diff: dict[str, Any]) -> str:
    """Heuristic judgment per fixture — readable label backed by the diff.

    Rules of thumb:
      • Pro adds 0 tables AND 0 fields  → "Flash sufficient — identical structure"
      • Pro adds 0 tables AND ≤2 fields → "Flash sufficient — Pro adds minor extras"
      • Pro adds ≥1 useful table OR ≥3 fields → "Pro adds real value"
      • Pro adds ≥3 tables → "Pro overcomplicates"
    """
    added_t = len(diff["added_tables"])
    added_f = diff["total_added_fields"]
    removed_t = len(diff["removed_tables"])

    if added_t == 0 and added_f == 0 and removed_t == 0:
        return "Flash sufficient — identical structure"
    if added_t == 0 and added_f <= 2 and removed_t == 0:
        return "Flash sufficient — Pro adds minor extras"
    if added_t >= 3:
        return "Pro overcomplicates"
    return "Pro adds real value"


# ── Rendering ────────────────────────────────────────────────────────

def _format_table_summary(model: DataModel) -> str:
    return ", ".join(t.name for t in model.tables) or "_none_"


def _format_singletons_summary(model: DataModel) -> str:
    return ", ".join(model.singletons.keys()) or "_none_"


def _print_stdout_block(profile: dict, flash_r: dict, pro_r: dict, diff: dict) -> None:
    """Compact side-by-side for the terminal."""
    print(f"\n=== {profile['key']} ===\n")

    print(f"FLASH  (${flash_r['cost_usd']:.5f}, {flash_r['latency_s']:.1f}s, "
          f"{flash_r['attempts']} attempt{'s' if flash_r['attempts'] != 1 else ''}):")
    print(f"  Tables ({len(flash_r['model'].tables)}): "
          f"{_format_table_summary(flash_r['model'])}")
    print(f"  Singletons ({len(flash_r['model'].singletons)}): "
          f"{_format_singletons_summary(flash_r['model'])}")

    print()
    print(f"PRO    (${pro_r['cost_usd']:.5f}, {pro_r['latency_s']:.1f}s, "
          f"{pro_r['attempts']} attempt{'s' if pro_r['attempts'] != 1 else ''}):")
    print(f"  Tables ({len(pro_r['model'].tables)}): "
          f"{_format_table_summary(pro_r['model'])}")
    print(f"  Singletons ({len(pro_r['model'].singletons)}): "
          f"{_format_singletons_summary(pro_r['model'])}")

    print("\n  DIFFS:")
    if diff["added_tables"]:
        print(f"    + Pro added tables:     {', '.join(diff['added_tables'])}")
    if diff["removed_tables"]:
        print(f"    - Pro removed tables:   {', '.join(diff['removed_tables'])}")
    if diff["added_singletons"]:
        print(f"    + Pro added singletons: {', '.join(diff['added_singletons'])}")
    if diff["removed_singletons"]:
        print(f"    - Pro removed singletons: {', '.join(diff['removed_singletons'])}")
    for tname, fd in diff["field_diffs"].items():
        if fd["added"]:
            print(f"    + Pro added fields to {tname}: {', '.join(fd['added'])}")
        if fd["removed"]:
            print(f"    - Pro removed fields from {tname}: {', '.join(fd['removed'])}")
    if (not diff["added_tables"] and not diff["removed_tables"]
            and not diff["added_singletons"] and not diff["removed_singletons"]
            and not diff["field_diffs"]):
        print("    (no differences)")

    print(f"\n  Verdict: {_qualitative_verdict(diff)}")


def _render_doc(records: list[dict]) -> str:
    """Render the full markdown doc with JSON blocks per fixture."""
    out: list[str] = []
    out.append("# Model comparison — Flash vs Pro for data-model planning")
    out.append("")
    out.append(
        "Auto-generated by `scripts/compare_data_model_models.py`. Each "
        "fixture is run once per model — Flash and Pro — with the same "
        "prompt, validation, and retry logic. The only knob changed is "
        "`plan_data_model(model_variant=…)`."
    )
    out.append("")
    out.append("Regenerate:")
    out.append("```bash")
    out.append(
        "docker exec -e OPENHANDS_SUPPRESS_BANNER=1 lucid-ai-ai_engine-1 \\\n"
        "    python scripts/compare_data_model_models.py"
    )
    out.append("```")
    out.append("")

    # Aggregate stats first so summary lands above the per-fixture noise.
    total_flash_cost = sum(r["flash"]["cost_usd"] for r in records)
    total_pro_cost   = sum(r["pro"]["cost_usd"] for r in records)
    avg_flash_lat    = sum(r["flash"]["latency_s"] for r in records) / len(records)
    avg_pro_lat      = sum(r["pro"]["latency_s"] for r in records) / len(records)
    added_tables_total   = sum(len(r["diff"]["added_tables"]) for r in records)
    added_fields_total   = sum(r["diff"]["total_added_fields"] for r in records)
    common_tables_exact  = sum(
        1 for r in records
        if not r["diff"]["added_tables"] and not r["diff"]["removed_tables"]
    )

    out.append("## Summary")
    out.append("")
    out.append(f"- **Total Flash cost**: ${total_flash_cost:.5f}")
    out.append(f"- **Total Pro cost**:   ${total_pro_cost:.5f}")
    if total_flash_cost > 0:
        out.append(f"- **Cost ratio**: {total_pro_cost / total_flash_cost:.1f}x more expensive")
    out.append(f"- **Avg Flash latency**: {avg_flash_lat:.1f}s")
    out.append(f"- **Avg Pro latency**:   {avg_pro_lat:.1f}s")
    if avg_flash_lat > 0:
        out.append(f"- **Latency ratio**: {avg_pro_lat / avg_flash_lat:.1f}x slower")
    out.append(f"- **Pro added tables across all 5 fixtures**: {added_tables_total}")
    out.append(f"- **Pro added fields across all 5 fixtures**: {added_fields_total}")
    out.append(f"- **Fixtures where Flash and Pro produced identical table sets**: "
               f"{common_tables_exact} / {len(records)}")
    out.append("")
    out.append("**Per-fixture verdicts**:")
    out.append("")
    for r in records:
        out.append(f"- _{r['profile']['key']}_ — {_qualitative_verdict(r['diff'])}")
    out.append("")

    for r in records:
        profile = r["profile"]
        f_r, p_r = r["flash"], r["pro"]
        diff = r["diff"]

        out.append(f"## {profile['label']}")
        out.append("")
        out.append(
            f"| | Flash | Pro |\n"
            f"|---|---|---|\n"
            f"| Tables | {len(f_r['model'].tables)} ({_format_table_summary(f_r['model'])}) "
            f"| {len(p_r['model'].tables)} ({_format_table_summary(p_r['model'])}) |\n"
            f"| Singletons | {len(f_r['model'].singletons)} ({_format_singletons_summary(f_r['model'])}) "
            f"| {len(p_r['model'].singletons)} ({_format_singletons_summary(p_r['model'])}) |\n"
            f"| Cost | ${f_r['cost_usd']:.5f} | ${p_r['cost_usd']:.5f} |\n"
            f"| Latency | {f_r['latency_s']:.1f}s | {p_r['latency_s']:.1f}s |\n"
            f"| Attempts | {f_r['attempts']} | {p_r['attempts']} |"
        )
        out.append("")
        out.append(f"**Verdict**: {_qualitative_verdict(diff)}")
        out.append("")

        out.append("### Diff")
        out.append("")
        wrote_any = False
        if diff["added_tables"]:
            out.append(f"- **+ Pro added tables**: `{', '.join(diff['added_tables'])}`")
            wrote_any = True
        if diff["removed_tables"]:
            out.append(f"- **− Pro removed tables**: `{', '.join(diff['removed_tables'])}`")
            wrote_any = True
        if diff["added_singletons"]:
            out.append(f"- **+ Pro added singletons**: `{', '.join(diff['added_singletons'])}`")
            wrote_any = True
        if diff["removed_singletons"]:
            out.append(f"- **− Pro removed singletons**: `{', '.join(diff['removed_singletons'])}`")
            wrote_any = True
        for tname, fd in diff["field_diffs"].items():
            if fd["added"]:
                out.append(f"- **+ Pro added fields** to `{tname}`: `{', '.join(fd['added'])}`")
                wrote_any = True
            if fd["removed"]:
                out.append(f"- **− Pro removed fields** from `{tname}`: `{', '.join(fd['removed'])}`")
                wrote_any = True
        if not wrote_any:
            out.append("_(no differences in table/field/singleton structure)_")
        out.append("")

        out.append("### Flash output")
        out.append("```json")
        out.append(f_r["model"].model_dump_json(indent=2))
        out.append("```")
        out.append("")
        out.append("### Pro output")
        out.append("```json")
        out.append(p_r["model"].model_dump_json(indent=2))
        out.append("```")
        out.append("")

    return "\n".join(out) + "\n"


# ── Entry point ──────────────────────────────────────────────────────

async def main() -> int:
    records: list[dict] = []

    for profile in PROFILES:
        print(f"\n--- running {profile['key']} ---")
        flash_r = await _run_one(profile, "flash")
        pro_r   = await _run_one(profile, "pro")
        diff = _diff_models(flash_r["model"], pro_r["model"])
        records.append({
            "profile": profile,
            "flash":   flash_r,
            "pro":     pro_r,
            "diff":    diff,
        })
        _print_stdout_block(profile, flash_r, pro_r, diff)

    # Final aggregate to stdout.
    print("\n=== SUMMARY ===\n")
    total_flash_cost = sum(r["flash"]["cost_usd"] for r in records)
    total_pro_cost   = sum(r["pro"]["cost_usd"] for r in records)
    avg_flash_lat    = sum(r["flash"]["latency_s"] for r in records) / len(records)
    avg_pro_lat      = sum(r["pro"]["latency_s"] for r in records) / len(records)
    print(f"Total Flash cost: ${total_flash_cost:.5f}")
    print(f"Total Pro cost:   ${total_pro_cost:.5f}")
    if total_flash_cost > 0:
        print(f"Cost ratio:       {total_pro_cost / total_flash_cost:.1f}x more expensive")
    print()
    print(f"Avg Flash latency: {avg_flash_lat:.1f}s")
    print(f"Avg Pro latency:   {avg_pro_lat:.1f}s")
    if avg_flash_lat > 0:
        print(f"Latency ratio:     {avg_pro_lat / avg_flash_lat:.1f}x slower")
    print()
    added_t = sum(len(r["diff"]["added_tables"]) for r in records)
    added_f = sum(r["diff"]["total_added_fields"] for r in records)
    print(f"Pro added tables across all fixtures: {added_t}")
    print(f"Pro added fields across all fixtures: {added_f}")
    print()
    for r in records:
        print(f"  {r['profile']['key']:<11} → {_qualitative_verdict(r['diff'])}")

    OUTPUT_DOC.write_text(_render_doc(records), encoding="utf-8")
    print(f"\nFull comparison doc written to: {OUTPUT_DOC}")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
