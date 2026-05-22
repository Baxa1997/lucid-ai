"""Full end-to-end live test of V2 website pipeline.

Runs the actual `run_website_pipeline` with the Arabic language school
prompt, auto-confirms the plan card, and lets Stage 5 parallel Claude
codegen run to completion. Reports per-page success/fail + surfaces
the new diagnostic logs on any failure.

Cost: ~$2-4 in Claude (parallel page codegen) + ~$0.30 in Gemini.
Wall time: ~10 min.

Run from ai_engine/:
    venv/bin/python scripts/verify_website_full.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
import traceback


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
if not ANTHROPIC_KEY or not GEMINI_KEY:
    print("ERROR: ANTHROPIC_API_KEY and GOOGLE_API_KEY required")
    sys.exit(2)

# Fake Supabase — we never actually call it.
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "x")
os.environ.setdefault("SUPABASE_JWT_SECRET", "x")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "x")
os.environ.setdefault("ENCRYPTION_KEY", "x" * 32)
os.environ.setdefault("INTERNAL_API_KEY", "x")
os.environ.setdefault("WEBSITE_PIPELINE_V2_ENABLED", "true")
os.environ.setdefault("WEBSITE_PLAN_CONFIRM_ENABLED", "true")
# Skip Supabase tenant provisioning — we don't have a live tenant.
os.environ.setdefault("TENANT_PROVISION_ENABLED", "0")


PROMPT = (
    "Website for arabic language education center, "
    "letter, grammar teaching and application for applications"
)


class ConfirmWS:
    """Mock WebSocket that auto-confirms the plan card and tracks every event."""
    def __init__(self):
        self.events: list[dict] = []
        self.plan_card = None
        self.plan_confirmed = False
        self._auto_confirmed = False
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.phase_transitions: list[str] = []

    async def send_json(self, payload):
        self.events.append(payload)
        if not isinstance(payload, dict):
            return
        kind = payload.get("type", "?")
        msg = (payload.get("message") or "")[:160]
        if kind == "progress":
            print(f"  [progress] {msg}")
        elif kind == "task_phase":
            tag = f"phase{payload.get('phase')}/{payload.get('status')}"
            self.phase_transitions.append(tag)
            print(f"  [phase] {payload.get('phase')}/{payload.get('title')} → {payload.get('status')}")
        elif kind == "warning":
            self.warnings.append(msg)
            print(f"  [warn] {msg}")
        elif kind == "error":
            self.errors.append(msg)
            print(f"  [ERROR] {msg}")
        elif kind == "chat_message" and payload.get("messageType") == "plan":
            self.plan_card = payload.get("planData") or {}
            pages = self.plan_card.get("pages") or []
            print(
                f"  [PLAN-CARD] received — {len(pages)} pages: "
                + ", ".join((p.get("name") or "")[:30] for p in pages[:10])
            )
        elif kind == "plan_awaiting_confirmation":
            if not self._auto_confirmed:
                self._auto_confirmed = True
                print(f"  [GATE FIRED] auto-confirming…")
                asyncio.create_task(self._confirm_after_register())

    async def _confirm_after_register(self):
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
            print("  [WARN] no confirmation key appeared")
            return
        print(f"  [HARNESS] CONFIRMING key={winner}")
        resolve_plan_confirmation(winner, {"confirmed": True})
        self.plan_confirmed = True


async def main() -> int:
    print("=" * 78)
    print("verify_website_full — full E2E live test with auto-confirm")
    print("=" * 78)
    print(f"Prompt: {PROMPT}")
    print()

    workspace = tempfile.mkdtemp(prefix="verify_full_")
    print(f"Workspace: {workspace}")

    ws = ConfirmWS()

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

    t_start = time.perf_counter()
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
    elapsed = time.perf_counter() - t_start

    # Walk the workspace + count files.
    file_count = 0
    file_paths: list[str] = []
    for r, dirs, files in os.walk(workspace):
        dirs[:] = [d for d in dirs if d not in {"node_modules", ".next", ".git"}]
        for f in files:
            file_count += 1
            if file_count <= 200:
                file_paths.append(os.path.relpath(os.path.join(r, f), workspace))

    print()
    print("=" * 78)
    print(f"PIPELINE RETURN: success={success}  elapsed={elapsed:.1f}s")
    print("=" * 78)
    if exc_str:
        print(f"EXCEPTION: {exc_str}")

    # ── Phase transitions
    print()
    print("Phase transitions:")
    for t in ws.phase_transitions:
        print(f"  {t}")

    # ── Warning / error summary
    if ws.warnings:
        print(f"\nWarnings ({len(ws.warnings)}):")
        for w in ws.warnings[:10]:
            print(f"  - {w}")
    if ws.errors:
        print(f"\nErrors ({len(ws.errors)}):")
        for e in ws.errors[:10]:
            print(f"  - {e}")

    # ── File count
    print(f"\nTotal files written: {file_count}")
    if file_count > 0 and file_paths:
        print("Sample paths:")
        for p in file_paths[:25]:
            print(f"  {p}")

    # ── Plan card summary
    if ws.plan_card:
        pages = ws.plan_card.get("pages") or []
        print(f"\nPlan card: {len(pages)} pages")
        print(f"  brand intro: {(ws.plan_card.get('intro') or '')[:120]}")

    print()
    print(f"Workspace saved at: {workspace}")
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
