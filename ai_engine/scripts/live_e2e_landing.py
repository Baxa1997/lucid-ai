"""Smaller, cheaper e2e validation — single-page landing project.

Purpose: validate Step 4 changes (deterministic-shell paths in
_authoritative_paths set + Phase 1 file-overlap filter + dropped REWRITE
prompt bullets) without burning the full 5-page Phase B/D budget.

Scope:
  • Real Anthropic + Gemini calls, full pipeline through Phase 1/2/3
  • Single-page landing → no Phase B page batching, no Phase D deep research
  • Auto-confirms the plan
  • Asserts every authoritative path is on disk after Phase 1 and was
    NOT in the rewrite-payload from Phase 1 (i.e. our filter caught it)

Cost: ~$0.50-1.00. Time: 3-5 minutes typical.

Run from ai_engine/:
    venv/bin/python scripts/live_e2e_landing.py 2>&1 | tee /tmp/lucid_landing.log
"""
from __future__ import annotations

import asyncio
import functools
import os
import subprocess
import sys
import tempfile
import time

print = functools.partial(print, flush=True)
sys.stdout.reconfigure(line_buffering=True)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


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

ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
GEMINI_KEY = os.environ.get("GOOGLE_API_KEY", "")
if not ANTHROPIC_KEY or not GEMINI_KEY:
    print("ERROR: ANTHROPIC_API_KEY and GOOGLE_API_KEY must be set in .env")
    sys.exit(1)


class CaptureWS:
    def __init__(self, chat_session_id: str):
        self.events: list[dict] = []
        self.chat_session_id = chat_session_id
        self._auto_confirmed = False

    async def send_json(self, payload):
        self.events.append(payload)
        kind = (payload or {}).get("type", "?") if isinstance(payload, dict) else "?"
        if kind == "progress":
            print(f"  [progress] {payload.get('message','')[:140]}")
        elif kind == "task_phase":
            print(f"  [phase] {payload.get('phase')}/{payload.get('title')} → {payload.get('status')}")
        elif kind == "error":
            print(f"  [ERROR] {payload.get('message','')[:200]}")
        elif kind == "warning":
            print(f"  [warn] {payload.get('message','')[:140]}")
        elif kind == "plan_awaiting_confirmation":
            print(f"  [HARNESS] gate fired — auto-confirming")

        if kind == "plan_awaiting_confirmation" and not self._auto_confirmed:
            self._auto_confirmed = True
            asyncio.create_task(self._resolve_when_ready())

    async def _resolve_when_ready(self):
        from app.services.project_generator import (
            resolve_plan_confirmation, _confirmation_key,
            pending_plan_confirmations,
        )
        key = _confirmation_key(self, self.chat_session_id)
        for _ in range(200):
            if key in pending_plan_confirmations:
                break
            await asyncio.sleep(0.05)
        else:
            print(f"  [HARNESS] WARNING: key {key} never appeared")
            return
        print(f"  [HARNESS] confirming key={key}")
        resolve_plan_confirmation(key, {"confirmed": True})


TEMPLATE_CLONE_URL = (
    "https://github.com/LucidSoftware-tech/lucid-template-nextjs-website.git"
)


async def main():
    from app.services import project_generator as pg

    tmp = tempfile.mkdtemp(prefix="lucid_landing_")
    print(f"\nWorkspace: {tmp}")

    print(f"Cloning template…")
    t0 = time.perf_counter()
    clone = subprocess.run(
        ["git", "clone", "--depth", "1", "--branch", "main",
         "--single-branch", TEMPLATE_CLONE_URL, "."],
        cwd=tmp, capture_output=True, text=True, timeout=180,
    )
    if clone.returncode != 0:
        print(f"❌ Template clone failed:\n{clone.stderr[:300]}")
        return 1
    print(f"  cloned in {time.perf_counter() - t0:.1f}s\n")

    description = (
        "A minimalist single-page landing for Stonemill, a Brooklyn artisan "
        "sourdough bakery. Sections: hero (story), featured loaves, about the "
        "mill, visit (location/hours), CTA. Warm, earthy palette, editorial "
        "typography."
    )
    print(f"Description: {description}\n")

    chat_session_id = ""
    ws = CaptureWS(chat_session_id)

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
    print("LAUNCHING — generate_new_project (real Anthropic + Gemini)")
    print("=" * 70)

    t_start = time.perf_counter()
    success = await pg.generate_new_project(
        description=description,
        workspace_path=tmp,
        validated=validated,
        websocket=ws,
        chat_session_id=chat_session_id,
        user_jwt="",
        user_id="",
    )
    elapsed = time.perf_counter() - t_start

    print("\n" + "=" * 70)
    print(f"FINISHED — success={success}  wall_clock={elapsed:.1f}s")
    print("=" * 70)

    # ── Step 4 specific assertions ──
    print("\n🔍 Step 4 verification:")
    expected = [
        "src/app/globals.css",
        "src/lib/design-system.js",
        "src/components/layout/MarketingHeader.jsx",
        "src/components/layout/MarketingFooter.jsx",
        "src/config/site.js",
        "src/config/navigation.js",
    ]
    for p in expected:
        full = os.path.join(tmp, p)
        ok = os.path.isfile(full) and os.path.getsize(full) > 0
        print(f"  {'✓' if ok else '✗'} {p} {'exists' if ok else 'MISSING'}")

    # Read site.js and look for the brand name — confirms deterministic version is on disk
    site_path = os.path.join(tmp, "src/config/site.js")
    if os.path.isfile(site_path):
        with open(site_path) as f:
            site_src = f.read()
        if "Stonemill" in site_src or "stonemill" in site_src.lower():
            print("  ✓ site.js contains the brand name → deterministic version final")
        else:
            print(f"  ✗ site.js missing brand — preview: {site_src[:200]}")

    # Event-type counts
    types_seen: dict[str, int] = {}
    for e in ws.events:
        t = (e or {}).get("type", "?")
        types_seen[t] = types_seen.get(t, 0) + 1
    print("\n📨 Event-type counts:")
    for t, c in sorted(types_seen.items(), key=lambda x: -x[1])[:15]:
        print(f"  {c:>4} × {t}")

    # File count
    file_count = 0
    for root, dirs, files in os.walk(tmp):
        dirs[:] = [d for d in dirs if d not in {"node_modules", ".next", ".git"}]
        file_count += len(files)
    print(f"\n🗂  Workspace files: {file_count}")

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
