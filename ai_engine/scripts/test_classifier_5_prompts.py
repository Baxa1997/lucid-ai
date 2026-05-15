"""Live 5-prompt validation for the project classifier agent.

Simulates the multi-turn clarification loop end-to-end against real
Gemini. For each prompt:
  1. Call resolve_classification with raw task.
  2. If it returns ``needs_clarification``, auto-pick a sensible answer
     (heuristic — see _pick_answer) and re-call with the marker glued on.
  3. Repeat until status=="resolved" or > 4 rounds.

Reports per prompt:
  • # of questions asked
  • Final archetype
  • Path taken (which clarify keys + values)
  • Total Gemini latency
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

if not os.environ.get("GOOGLE_API_KEY"):
    print("ERROR: GOOGLE_API_KEY must be set in ../.env")
    sys.exit(1)


PROMPTS = [
    # Original 5
    ("P1", "Italian restaurant in Brooklyn",                                    "1-2",  "?"),
    ("P2", "landing page for fitness coach",                                    "0",    "single_page_landing"),
    ("P3", "I need to manage my restaurant's bookings",                         "1+entities", "admin_dashboard"),
    ("P4", "build me an italian restaurant website with admin panel",           "1",    "consumer_website_with_admin"),
    ("P5", "help me with my business",                                          "<=3",  "?"),
    # 8 new from this round
    ("N1", "online store for vintage cameras with inventory management",        "1+",   "consumer_website_with_admin"),
    ("N2", "internal tool to manage my team's tasks",                           "0-1",  "admin_dashboard"),
    ("N3", "showcase my photography portfolio",                                 "1",    "portfolio_or_landing"),
    ("N4", "I need a way to track my customers and send invoices",              "0-1",  "admin_dashboard"),
    ("N5", "build a SaaS landing page for my analytics product",                "0",    "single_page_landing"),
    ("N6", "marketing site for my law firm with case management for staff",    "1",    "consumer_website_with_admin"),
    ("N7", "Tashkent yoga studio with booking",                                 "1+",   "?"),
    ("N8", "company website with employee portal",                              "1",    "consumer_website_with_admin"),
]


def _pick_answer(question_payload: dict, prompt_id: str) -> tuple[str, str]:
    """Heuristic auto-responder.

    Returns (clarify_key, value). The choice mirrors what a sensible user
    might pick — picks the first option that matches the prompt's intent.
    """
    key = question_payload["clarify_key"]
    options = question_payload["options"] or []
    if not options:
        return key, ""

    # For "Italian restaurant in Brooklyn" — pick landing_page or full_website
    # to test the marketing path. P1 picks full_website (more interesting).
    if key == "project_type":
        # Per-prompt: which option the simulated user would click.
        preferred_by_id = {
            "P1": ("full_website", "landing_page"),
            "P3": ("admin_dashboard",),
            "P4": ("marketing_with_admin",),
            "P5": ("full_website", "landing_page", "admin_dashboard"),
            "N1": ("marketing_with_admin", "ecommerce"),
            "N2": ("admin_dashboard",),
            "N3": ("portfolio", "landing_page"),
            "N4": ("admin_dashboard",),
            "N5": ("landing_page",),
            "N6": ("marketing_with_admin",),
            "N7": ("full_website",),
            "N8": ("marketing_with_admin",),
        }
        preferred = preferred_by_id.get(prompt_id, ())
        for want in preferred:
            for o in options:
                if o.get("id") == want:
                    return key, want
        # Fallback: first option
        return key, options[0].get("id", "")

    # Generic: pick first option
    return key, options[0].get("id", "")


def _format_marker(key: str, value: str) -> str:
    return f"[LUCID_CLARIFY::{key}={value}]"


async def _run_one(prompt_id: str, raw_prompt: str) -> dict:
    """Run a single prompt through the resolver, simulating the user
    side of the multi-turn conversation."""
    from app.services.project_classifier_agent import resolve_classification

    task = raw_prompt
    questions_asked: list[dict] = []
    answers_chosen: list[tuple[str, str]] = []
    t0 = time.perf_counter()

    for round_idx in range(5):
        result = await resolve_classification(task)
        if result["status"] == "resolved":
            elapsed = time.perf_counter() - t0
            return {
                "id": prompt_id,
                "prompt": raw_prompt,
                "status": "resolved",
                "archetype": result["archetype"],
                "needs_admin_followup": result.get("needs_admin_followup", False),
                "entities": result.get("entities"),
                "entity_confidence": result.get("entity_confidence"),
                "questions_asked": questions_asked,
                "answers_chosen": answers_chosen,
                "round_count": round_idx,
                "reasoning": result.get("reasoning", ""),
                "seconds": round(elapsed, 2),
            }

        # Need clarification — record it and auto-answer.
        questions_asked.append({
            "stage": result.get("stage"),
            "clarify_key": result.get("clarify_key"),
            "text": result.get("question"),
            "options": result.get("options"),
        })
        chosen_key, chosen_val = _pick_answer(result, prompt_id)
        answers_chosen.append((chosen_key, chosen_val))
        task = _format_marker(chosen_key, chosen_val) + " " + task

    elapsed = time.perf_counter() - t0
    return {
        "id": prompt_id,
        "prompt": raw_prompt,
        "status": "loop_exit",
        "questions_asked": questions_asked,
        "answers_chosen": answers_chosen,
        "round_count": 5,
        "seconds": round(elapsed, 2),
    }


async def main() -> int:
    print(f"{'='*72}\n  5-prompt classifier validation\n{'='*72}\n")

    results: list[dict] = []
    for prompt_id, prompt, exp_q, exp_arch in PROMPTS:
        print(f"\n── {prompt_id} ── {prompt!r}")
        print(f"   expected: ~{exp_q} questions, archetype={exp_arch}")
        try:
            r = await _run_one(prompt_id, prompt)
            results.append(r)
            print(f"   → {r['status']} archetype={r.get('archetype')} "
                  f"questions={len(r['questions_asked'])} "
                  f"followup={r.get('needs_admin_followup')} "
                  f"entities={r.get('entities')} "
                  f"time={r['seconds']}s")
            for i, q in enumerate(r["questions_asked"], 1):
                opts = [o.get("id") for o in q["options"]]
                print(f"     Q{i} [{q.get('clarify_key')}]: {q.get('text')!r:80}  options={opts}")
            for i, (k, v) in enumerate(r["answers_chosen"], 1):
                print(f"     A{i}: {k}={v}")
        except Exception as exc:
            import traceback
            traceback.print_exc()
            results.append({"id": prompt_id, "error": str(exc)})

    # Aggregate
    print(f"\n\n{'='*72}\n  AGGREGATE\n{'='*72}")
    print(f"{'ID':<4} {'questions':<10} {'archetype':<32} {'followup':<10} {'time':<6}")
    for r in results:
        if "error" in r:
            print(f"{r['id']:<4} ERROR: {r['error'][:60]}")
            continue
        print(f"{r['id']:<4} {len(r['questions_asked']):<10} "
              f"{str(r.get('archetype')):<32} "
              f"{str(r.get('needs_admin_followup')):<10} "
              f"{r['seconds']}s")

    import json as _json
    out_path = Path("/tmp/classifier_5_prompts.json")
    out_path.write_text(_json.dumps(results, indent=2))
    print(f"\nSaved to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
