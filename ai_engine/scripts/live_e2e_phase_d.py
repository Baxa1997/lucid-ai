"""End-to-end live-API test for the smallest project that exercises Phase B + D.

What this does:
  1. Sets PHASE2_PAGE_BATCHING=1 and PHASE_D_DEEP_RESEARCH=1 so the new paths fire.
  2. Creates a temp workspace, copies the nextjs skeleton (the consumer/landing template).
  3. Builds a fake WebSocket that captures every emitted event and AUTO-CONFIRMS
     the plan-confirmation gate the moment it fires (otherwise the pipeline
     would block waiting for a UI click for up to 30 minutes).
  4. Runs `generate_new_project` against the real Anthropic + Gemini APIs.
  5. After completion, inspects the workspace + captured events and reports:
       • wall-clock timing per phase
       • whether Phase B (page batching) fired
       • whether Phase D (deep research) fired
       • whether ===PAGE_DEEP::*=== blocks made it into research
       • file count / file tree size

Run from ai_engine/:
    venv/bin/python scripts/live_e2e_phase_d.py

This will spend real money. Estimated: ~$1-3, ~3-5 minutes.
"""
from __future__ import annotations

import asyncio
import functools
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

# Force unbuffered stdout so the live tail of /tmp/lucid_e2e.log shows our
# print() output immediately (block-buffered when stdout is a pipe).
print = functools.partial(print, flush=True)
sys.stdout.reconfigure(line_buffering=True)

# ── Repo path setup ────────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Load .env from repo root ───────────────────────────────────────────────
def _load_env(path):
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

# ── Required app env (mostly placeholders; we don't talk to Supabase) ──────
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "x")
os.environ.setdefault("SUPABASE_JWT_SECRET", "x")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "x")
os.environ.setdefault("ENCRYPTION_KEY", "x" * 32)
os.environ.setdefault("INTERNAL_API_KEY", "x")

# ── Force the new code paths ON ────────────────────────────────────────────
os.environ["PHASE2_PAGE_BATCHING"] = "1"
os.environ["PHASE_D_DEEP_RESEARCH"] = "1"

ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
GEMINI_KEY = os.environ.get("GOOGLE_API_KEY", "")
if not ANTHROPIC_KEY or not GEMINI_KEY:
    print("ERROR: ANTHROPIC_API_KEY and GOOGLE_API_KEY must be set in .env")
    sys.exit(1)


# ── Captured-event WebSocket double ────────────────────────────────────────
class CaptureWS:
    """Fake WebSocket that captures every send_json payload.

    Auto-confirms the plan-confirmation gate as soon as the
    `plan_awaiting_confirmation` event arrives.
    """

    def __init__(self, chat_session_id: str):
        self.events: list[dict] = []
        self.chat_session_id = chat_session_id
        self._auto_confirmed = False

    async def send_json(self, payload):
        # Capture
        self.events.append(payload)
        kind = payload.get("type", "?") if isinstance(payload, dict) else "?"

        # One-line live tail so we can watch progress
        if kind == "progress":
            msg = payload.get("message", "")
            print(f"  [progress] {msg[:140]}")
        elif kind == "phase2_batch_started":
            print(f"  [batch] started idx={payload.get('batch_index')}/{payload.get('total_batches')}")
        elif kind == "phase2_batch_complete":
            print(f"  [batch] complete idx={payload.get('batch_index')} files={payload.get('files_written')}")
        elif kind == "task_phase":
            print(f"  [phase] {payload.get('phase')}/{payload.get('title')} → {payload.get('status')}")
        elif kind == "error":
            print(f"  [ERROR] {payload.get('message','')[:200]}")
        elif kind == "warning":
            print(f"  [warn] {payload.get('message','')[:140]}")
        elif kind == "chat_message" and payload.get("messageType") == "plan":
            pd = payload.get("planData") or {}
            print(f"  [HARNESS] received plan envelope (pages={len(pd.get('pages') or [])}, "
                  f"entities={len(pd.get('entities') or [])})")
        elif kind == "plan_awaiting_confirmation":
            print(f"  [HARNESS] gate fired — plan_awaiting_confirmation")

        # Auto-confirm the plan when the gate fires.
        # NB: project_generator emits `plan_awaiting_confirmation` BEFORE it calls
        # register_plan_confirmation(), so resolving immediately is a no-op (the
        # future map is still empty). Spawn a tiny poller that waits for the
        # key to appear and then resolves it.
        if kind == "plan_awaiting_confirmation" and not self._auto_confirmed:
            self._auto_confirmed = True
            asyncio.create_task(self._resolve_when_ready())

    async def _resolve_when_ready(self):
        from app.services.project_generator import (
            resolve_plan_confirmation, _confirmation_key,
            pending_plan_confirmations,
        )
        key = _confirmation_key(self, self.chat_session_id)
        # Poll briefly until project_generator registers the future.
        for _ in range(200):  # ~10s max
            if key in pending_plan_confirmations:
                break
            await asyncio.sleep(0.05)
        else:
            print(f"  [HARNESS] WARNING: key {key} never appeared in pending map")
            return
        print(f"  [HARNESS] auto-confirming key={key}")
        resolve_plan_confirmation(key, {"confirmed": True})
        print(f"  [HARNESS] resolve done; pending now={list(pending_plan_confirmations.keys())}")


# ── Run ─────────────────────────────────────────────────────────────────────
TEMPLATE_CLONE_URL = (
    "https://github.com/LucidSoftware-tech/lucid-template-nextjs-website.git"
)


async def main():
    from app.services import project_generator as pg

    # Sanity: confirm flags are visible inside the imported module
    print("Phase B page-batching enabled:", pg._PHASE2_PAGE_BATCHING_ENABLED)
    print("Phase D deep-research enabled:", pg._PHASE_D_DEEP_RESEARCH_ENABLED)
    assert pg._PHASE2_PAGE_BATCHING_ENABLED, "Phase B flag not picked up"
    assert pg._PHASE_D_DEEP_RESEARCH_ENABLED, "Phase D flag not picked up"

    # ── Workspace ──
    tmp = tempfile.mkdtemp(prefix="lucid_e2e_")
    print(f"\nWorkspace: {tmp}")

    # ── Clone the REAL GitHub template (production path) ──
    # Mirrors what the orchestrator does in new_project_mode (orchestrator.py:431).
    # The local skeleton in ai_engine/skeletons/nextjs/ is only 13 files — the
    # GitHub template is the production starting point and provides far more
    # pre-built scaffolding (UI primitives, layout components, theme wiring).
    print(f"Cloning template: {TEMPLATE_CLONE_URL}")
    t_clone = time.perf_counter()
    clone = subprocess.run(
        ["git", "clone", "--depth", "1", "--branch", "main",
         "--single-branch", TEMPLATE_CLONE_URL, "."],
        cwd=tmp, capture_output=True, text=True, timeout=180,
    )
    if clone.returncode != 0:
        print(f"❌ Template clone failed:\n{clone.stderr[:300]}")
        sys.exit(1)
    file_count_after_clone = sum(
        1 for _ in __import__("pathlib").Path(tmp).rglob("*")
        if _.is_file() and ".git" not in _.parts
    )
    print(f"Cloned {file_count_after_clone} template files in {time.perf_counter() - t_clone:.1f}s\n")

    # ── Description: 5-page consumer site (minimum threshold for Phase B + D) ──
    description = (
        "A 5-page artisan bakery website for a Brooklyn-based sourdough bakery "
        "called 'Stonemill'. Pages: Home (story + featured loaves), Menu (full "
        "bread and pastry list with prices), About (the bakers + the mill), "
        "Visit (location, hours, contact form), and Gallery (photos of the "
        "ovens, the team, and finished loaves). Use Tailwind + shadcn. Warm, "
        "earthy palette. Editorial typography."
    )
    print(f"Description: {description}\n")

    # IMPORTANT: empty chat_session_id so save/get/clear_persisted_plan all
    # early-return without touching Supabase (the test SUPABASE_URL is fake
    # and the supabase async client hangs forever on connection attempts).
    # _confirmation_key falls back to f"ws-{id(websocket)}" which is fine.
    chat_session_id = ""
    ws = CaptureWS(chat_session_id)

    # Production-path validated dict: new_project_mode (NOT scratch_mode), with a
    # template_clone_url set so the pipeline path matches what real users get.
    validated = {
        "anthropic_api_key": ANTHROPIC_KEY,
        "gemini_api_key": GEMINI_KEY,
        "project_stack": "nextjs",
        "skeleton_stack": "nextjs",
        "skeleton_name": "nextjs",
        "is_admin": False,
        "scratch_mode": False,
        "new_project_mode": True,
        "template_clone_url": TEMPLATE_CLONE_URL,
    }

    print("=" * 70)
    print("LAUNCHING — generate_new_project (real Anthropic + Gemini calls)")
    print("=" * 70)

    t0 = time.perf_counter()
    success = await pg.generate_new_project(
        description=description,
        workspace_path=tmp,
        validated=validated,
        websocket=ws,
        chat_session_id=chat_session_id,
        user_jwt="",
        user_id="",
    )
    elapsed = time.perf_counter() - t0

    print("\n" + "=" * 70)
    print(f"FINISHED — success={success}  wall_clock={elapsed:.1f}s")
    print("=" * 70)

    # ── Post-mortem ──
    types_seen: dict[str, int] = {}
    for e in ws.events:
        t = (e or {}).get("type", "?") if isinstance(e, dict) else "?"
        types_seen[t] = types_seen.get(t, 0) + 1

    print("\n📨 Event-type counts:")
    for t, c in sorted(types_seen.items(), key=lambda x: -x[1]):
        print(f"  {c:>4} × {t}")

    # File-tree summary
    file_count = 0
    extensions: dict[str, int] = {}
    for root, dirs, files in os.walk(tmp):
        dirs[:] = [d for d in dirs if d not in {"node_modules", ".next", ".git"}]
        for f in files:
            file_count += 1
            ext = os.path.splitext(f)[1] or "(none)"
            extensions[ext] = extensions.get(ext, 0) + 1
    print(f"\n🗂  Workspace files: {file_count}")
    for ext, c in sorted(extensions.items(), key=lambda x: -x[1])[:10]:
        print(f"  {c:>4} × {ext}")

    # Phase B detection
    b_started = types_seen.get("phase2_batch_started", 0)
    b_complete = types_seen.get("phase2_batch_complete", 0)
    print(f"\n🅱  Phase B (page batching): {b_started} batches started, {b_complete} completed")

    # Phase D detection — look for "Deep research" progress events
    d_progress = [e for e in ws.events
                  if isinstance(e, dict) and e.get("type") == "progress"
                  and "deep research" in str(e.get("message", "")).lower()]
    print(f"\n🅳  Phase D (deep research): {len(d_progress)} progress events")
    for e in d_progress:
        print(f"     · {e.get('message','')[:150]}")

    # Sample one or two pages from the workspace
    pages_dir = os.path.join(tmp, "src", "app")
    if os.path.isdir(pages_dir):
        print(f"\n📑 Top-level routes under src/app:")
        for entry in sorted(os.listdir(pages_dir))[:20]:
            print(f"     · {entry}")

    print(f"\nWorkspace preserved at: {tmp}")
    print("Inspect files there before deleting.")


if __name__ == "__main__":
    asyncio.run(main())
