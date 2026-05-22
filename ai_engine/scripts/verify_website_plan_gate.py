"""Verify the website V2 plan-card gate.

Runs the full website pipeline up to the new Stage 4 plan gate, declines
the plan, and asserts the pipeline aborts cleanly BEFORE any Claude
codegen happens. This costs ~$0.30 in Gemini (purpose / intent / research
/ visual_dna / plan) and $0 in Claude (the whole point of the gate is to
exit before spending Claude).

Pass criteria:
  1. Pipeline reached and emitted a plan card (`messageType: plan`).
  2. Plan card has the website's pages (not generic fallback).
  3. After declining, pipeline returned False.
  4. NO page_generator events fired (no Claude spend).

Run from ai_engine/:
    venv/bin/python scripts/verify_website_plan_gate.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
import traceback


# Make `app.*` imports work.
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))


def _load_env(path: str) -> None:
    if not os.path.isfile(path):
        return
    with open(path) as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env(os.path.join(os.path.dirname(HERE), ".env"))
_load_env(os.path.join(os.path.dirname(os.path.dirname(HERE)), ".env"))

ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
GEMINI_KEY = os.environ.get("GOOGLE_API_KEY", "")
if not GEMINI_KEY:
    print("ERROR: GOOGLE_API_KEY missing (need Gemini for Stages 0.5–4)")
    sys.exit(2)
if not ANTHROPIC_KEY:
    # Anthropic key required by the pipeline guard even though we won't
    # actually call Claude (we'll bail at the plan gate first).
    os.environ["ANTHROPIC_API_KEY"] = "test-fake-not-used"
    ANTHROPIC_KEY = "test-fake-not-used"

# Supabase fakes — pipeline reads these at import; we abort before any
# real Supabase call happens.
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "x")
os.environ.setdefault("SUPABASE_JWT_SECRET", "x")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "x")
os.environ.setdefault("ENCRYPTION_KEY", "x" * 32)
os.environ.setdefault("INTERNAL_API_KEY", "x")
os.environ.setdefault("WEBSITE_PIPELINE_V2_ENABLED", "true")
os.environ.setdefault("WEBSITE_PLAN_CONFIRM_ENABLED", "true")
# Skip Stage 4.5 data_model & 4.6 tenant provision — those need real
# Supabase. We'll never reach them (we exit at Stage 4 gate).
os.environ.setdefault("TENANT_PROVISION_ENABLED", "0")


PROMPT = (
    "Multi-page website for an Arabic language school called Bayt Al-Lughah. "
    "Home, courses, instructors, schedule, pricing, blog, contact."
)


class DeclineWS:
    """Mock WebSocket: prints every event; declines the plan when the
    gate fires; counts page_generator activity to detect Claude spend.
    """
    def __init__(self):
        self.events: list[dict] = []
        self.plan_card = None
        self.plan_gate_fired = False
        self._declined = False
        # Used by pipeline to read corrections back.
        self._plan_correction = ""

    async def send_json(self, payload):
        self.events.append(payload)
        if not isinstance(payload, dict):
            return
        kind = payload.get("type", "?")
        msg = (payload.get("message") or "")[:160]
        if kind == "progress":
            print(f"  [progress] {msg}")
        elif kind == "task_phase":
            print(f"  [phase] {payload.get('phase')}/{payload.get('title')} → {payload.get('status')}")
        elif kind == "warning":
            print(f"  [warn] {msg}")
        elif kind == "error":
            print(f"  [ERROR] {msg}")
        elif kind == "chat_message" and payload.get("messageType") == "plan":
            self.plan_card = payload.get("planData") or {}
            pages = self.plan_card.get("pages") or []
            print(
                f"  [PLAN-CARD] received — {len(pages)} pages: "
                + ", ".join((p.get("name") or "") for p in pages[:8])
            )
        elif kind == "plan_awaiting_confirmation":
            self.plan_gate_fired = True
            print(f"  [GATE FIRED] declining (no correction)…")
            asyncio.create_task(self._decline_after_register())

    async def _decline_after_register(self):
        from app.services.project_generator import (
            resolve_plan_confirmation,
            pending_plan_confirmations,
            _confirmation_key,
        )
        ws_key = _confirmation_key(self, "")
        winner = None
        for _ in range(400):  # 20s
            if ws_key in pending_plan_confirmations:
                winner = ws_key
                break
            if pending_plan_confirmations:
                winner = next(iter(pending_plan_confirmations.keys()))
                break
            await asyncio.sleep(0.05)
        if winner is None:
            print("  [WARN] no confirmation key appeared — gate may be misconfigured")
            return
        print(f"  [HARNESS] declining key={winner}")
        # confirmed=False with no correction → pipeline aborts cleanly
        resolve_plan_confirmation(winner, {"confirmed": False})
        self._declined = True


async def main() -> int:
    print("=" * 70)
    print("verify_website_plan_gate — confirms V2 gate works without Claude spend")
    print("=" * 70)
    print(f"Prompt:    {PROMPT}")
    print(f"V2 flag:   WEBSITE_PIPELINE_V2_ENABLED={os.environ.get('WEBSITE_PIPELINE_V2_ENABLED')}")
    print(f"Gate flag: WEBSITE_PLAN_CONFIRM_ENABLED={os.environ.get('WEBSITE_PLAN_CONFIRM_ENABLED')}")

    workspace = tempfile.mkdtemp(prefix="verify_gate_")
    print(f"Workspace: {workspace}")

    ws = DeclineWS()

    classification = {
        "layout_archetype": "consumer_website",
        "domain": "education",
        "is_single_page": False,
        "nav_style": "top_header",
        "has_admin": False,
    }

    validated = {
        "anthropic_api_key": ANTHROPIC_KEY,
        "gemini_api_key":    GEMINI_KEY,
        "project_stack":     "nextjs",
        "skeleton_stack":    "nextjs",
        "skeleton_name":     "nextjs",
        "is_admin":          False,
        "scratch_mode":      False,
        "new_project_mode":  True,
        "template_clone_url": "https://github.com/LucidSoftware-tech/lucid-template-nextjs-website.git",
    }

    from app.services.website_pipeline import run_website_pipeline

    t0 = time.perf_counter()
    success = None
    exc_str = None
    try:
        success = await run_website_pipeline(
            description=PROMPT,
            classification=classification,
            workspace_path=workspace,
            validated=validated,
            websocket=ws,
            chat_session_id="",
        )
    except Exception as e:
        exc_str = f"{type(e).__name__}: {e}"
        traceback.print_exc()

    elapsed = time.perf_counter() - t0

    print()
    print("=" * 70)
    print(f"PIPELINE RETURN: success={success}  elapsed={elapsed:.1f}s")
    if exc_str:
        print(f"PIPELINE EXCEPTION: {exc_str}")

    # ── Assertions
    page_gen_count = sum(
        1 for e in ws.events
        if isinstance(e, dict) and (
            "page_generator" in str(e.get("message", ""))
            or e.get("type") in {"phase2_batch_started", "phase2_batch_complete"}
        )
    )

    checks = [
        ("Plan card emitted",            ws.plan_card is not None),
        ("Plan gate fired",              ws.plan_gate_fired),
        ("Plan has at least 3 pages",    ws.plan_card and len(ws.plan_card.get("pages") or []) >= 3),
        ("Pipeline returned False",      success is False),
        ("No Claude page_generator events", page_gen_count == 0),
        ("No pipeline exception",        exc_str is None),
    ]

    print()
    print("CHECKS:")
    all_pass = True
    for label, ok in checks:
        mark = "✓" if ok else "✗"
        print(f"  {mark} {label}")
        if not ok:
            all_pass = False

    if ws.plan_card:
        print()
        print("PLAN CARD CONTENTS:")
        print(f"  intro:       {ws.plan_card.get('intro','')[:120]}")
        print(f"  description: {ws.plan_card.get('description','')[:120]}")
        print(f"  design:      {ws.plan_card.get('design','')[:120]}")
        pages = ws.plan_card.get("pages") or []
        print(f"  pages ({len(pages)}):")
        for p in pages[:12]:
            print(f"    - {p.get('name','')}  —  {p.get('desc','')[:80]}")

    print()
    print("RESULT:", "✅ PASS" if all_pass else "❌ FAIL")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
