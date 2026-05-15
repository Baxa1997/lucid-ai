"""Flag-toggle smoke test for USE_CLASSIFIER_AGENT.

Imports the ws.py branch logic in isolation by simulating what ws.py
does given a task string. Verifies:
  • flag=False: legacy ``check_prompt_clarity`` is called, NO marker
    is injected into the task.
  • flag=True:  ``resolve_classification`` is called. On resolved,
    the FORCE_ARCHETYPE marker is injected.
  • flag=True with ambiguous prompt: emits the same shape that the
    frontend's clarification_needed handler already understands.

This is a synthetic harness — no actual WS server is started.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import patch

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


async def _simulate_ws_gate(task: str, *, flag_on: bool) -> dict:
    """Replicates the relevant branch in ws.py:1580+ in isolation.

    Returns ``{task_after_gate, asked_question, archetype_in_task}``.
    """
    from knowledge.loader import (
        extract_clarify_context, ARCHETYPE_LOCK_PREFIX,
        force_archetype_from_task,
    )

    _existing_clarify, _ = extract_clarify_context(task)
    asked_question = None
    out_task = task

    if flag_on and len(_existing_clarify) < 3:
        from app.services.project_classifier_agent import resolve_classification
        resolution = await resolve_classification(task)
        if resolution.get("status") == "needs_clarification":
            asked_question = {
                "key": resolution.get("clarify_key"),
                "text": resolution.get("question"),
                "options": [o.get("id") for o in resolution.get("options", [])],
            }
        elif resolution.get("status") == "resolved":
            arch = resolution.get("archetype", "")
            if arch:
                out_task = f"{ARCHETYPE_LOCK_PREFIX}{arch}] {task}"
    elif not flag_on and len(_existing_clarify) < 3:
        from app.services.clarity_agent import check_prompt_clarity
        q = await check_prompt_clarity(task=task, already_clarified={}, timeout_s=12.0)
        if q is not None:
            asked_question = {
                "key": q.get("key"),
                "text": q.get("text"),
                "options": [o.get("id") for o in q.get("options", [])],
            }

    parsed_arch, _ = force_archetype_from_task(out_task)
    return {
        "task_in":           task,
        "task_after_gate":   out_task,
        "asked_question":    asked_question,
        "archetype_in_task": parsed_arch,
        "flag":              "on" if flag_on else "off",
    }


PROMPTS_TO_TEST = [
    ("clear-landing",  "landing page for fitness coach"),
    ("ambiguous-restaurant",  "Italian restaurant in Brooklyn"),
    ("admin-combo",   "italian restaurant website with admin panel"),
    ("clear-admin",   "internal tool to manage my team's tasks"),
]


async def main() -> int:
    print(f"\n{'='*72}\n  USE_CLASSIFIER_AGENT flag toggle smoke test\n{'='*72}\n")

    for name, prompt in PROMPTS_TO_TEST:
        print(f"\n── {name} ── {prompt!r}")
        for flag_on in (False, True):
            try:
                r = await _simulate_ws_gate(prompt, flag_on=flag_on)
                arch_str = r["archetype_in_task"] or "—"
                q = r["asked_question"]
                q_str = f"{q['key']} {q['options']}" if q else "(none)"
                injected = "yes" if r["task_after_gate"] != r["task_in"] else "no"
                print(f"   flag={r['flag']:<3}  injected_marker={injected:<3}  "
                      f"archetype={arch_str:<32}  asked={q_str}")
            except Exception as exc:
                print(f"   flag={'on' if flag_on else 'off'} ERROR: {exc}")

    # Final assertions: flag off never injects markers; flag on with
    # clear prompts injects a marker.
    print(f"\n{'='*72}\n  Verifying invariants\n{'='*72}")

    # Invariant 1: flag off NEVER injects markers
    for name, prompt in PROMPTS_TO_TEST:
        r = await _simulate_ws_gate(prompt, flag_on=False)
        assert r["archetype_in_task"] is None, (
            f"flag=off but task got marker injected for {name!r}!"
        )
    print("  ✓ flag=off NEVER injects archetype markers")

    # Invariant 2: flag on with clearest prompt injects single_page_landing
    r = await _simulate_ws_gate("landing page for fitness coach", flag_on=True)
    assert r["archetype_in_task"] == "single_page_landing", (
        f"expected single_page_landing, got {r['archetype_in_task']!r}"
    )
    print("  ✓ flag=on injects single_page_landing for 'landing page for X'")

    print("\nSmoke test PASSED.\n")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
