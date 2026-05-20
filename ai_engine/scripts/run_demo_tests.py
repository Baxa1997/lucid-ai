"""Demo readiness test runner — runs a single test by id.

Usage from ai_engine/:
    venv/bin/python scripts/run_demo_tests.py L1
    venv/bin/python scripts/run_demo_tests.py L2
    ADMIN_CODEGEN_MOCK=false venv/bin/python scripts/run_demo_tests.py A1
    ADMIN_CODEGEN_MOCK=false venv/bin/python scripts/run_demo_tests.py A2

Each run:
  - Clones the right template (nextjs-website or react-admin)
  - Drives generate_new_project with the demo prompt
  - Auto-confirms the plan gate
  - Saves: events.json (full event log), summary.json (key metrics),
    workspace.txt (path to generated workspace), stdout.log
  - Returns 0 on pipeline success, 1 on failure

All artifacts go to /tmp/demo_<test_id>/*. The workspace path is
preserved (not cleaned) so the report can inspect generated code.
"""
from __future__ import annotations

import asyncio
import functools
import json
import os
import subprocess
import sys
import tempfile
import time
import traceback

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
    sys.exit(2)


TESTS = {
    "L1": {
        "type": "landing",
        "prompt": (
            "Modern landing page for AI-powered email marketing tool called MailFlow "
            "with hero, features, pricing, and testimonials"
        ),
        "template": "https://github.com/LucidSoftware-tech/lucid-template-nextjs-website.git",
        "stack": "nextjs",
        "is_admin": False,
    },
    "L2": {
        "type": "landing",
        "prompt": (
            "Professional landing page for boutique dental clinic in Brooklyn with "
            "services, doctors, reviews, and appointment booking"
        ),
        "template": "https://github.com/LucidSoftware-tech/lucid-template-nextjs-website.git",
        "stack": "nextjs",
        "is_admin": False,
    },
    "A1": {
        "type": "admin",
        "prompt": (
            "Admin panel for fitness studio to manage members, class schedules, "
            "instructors, and membership payments"
        ),
        "template": "https://github.com/LucidSoftware-tech/lucid-template-react-admin.git",
        "stack": "react-admin",
        "is_admin": True,
    },
    "A2": {
        "type": "admin",
        "prompt": (
            "Admin panel for medical clinic to manage patient appointments, doctors, "
            "prescriptions, and billing records"
        ),
        "template": "https://github.com/LucidSoftware-tech/lucid-template-react-admin.git",
        "stack": "react-admin",
        "is_admin": True,
    },
}


class CaptureWS:
    def __init__(self, chat_session_id: str):
        self.events: list[dict] = []
        self.chat_session_id = chat_session_id
        self._auto_confirmed = False
        self._first_progress_t = None
        self._last_progress_t = None

    async def send_json(self, payload):
        self.events.append(payload)
        kind = (payload or {}).get("type", "?") if isinstance(payload, dict) else "?"
        now = time.perf_counter()
        if self._first_progress_t is None:
            self._first_progress_t = now
        self._last_progress_t = now

        if kind == "progress":
            print(f"  [progress] {payload.get('message','')[:180]}")
        elif kind == "task_phase":
            print(f"  [phase] {payload.get('phase')}/{payload.get('title')} → {payload.get('status')}")
        elif kind == "error":
            print(f"  [ERROR] {payload.get('message','')[:300]}")
        elif kind == "warning":
            print(f"  [warn] {payload.get('message','')[:180]}")
        elif kind == "phase2_batch_started":
            print(f"  [batch {payload.get('batch_index')}/{payload.get('total_batches')}] started")
        elif kind == "phase2_batch_complete":
            ok = payload.get('ok')
            print(f"  [batch {payload.get('batch_index')}/{payload.get('total_batches')}] done ok={ok} files={payload.get('files', 0)}")
        elif kind == "chat_message":
            mtype = payload.get("messageType", "")
            if mtype == "plan":
                print(f"  [PLAN-CARD] received planData (intro={bool(payload.get('planData', {}).get('intro'))})")
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
        # Try ws-id key first (website / landing pipelines), then fall back
        # to "any pending key" (admin V2 keys by project slug, not ws-id).
        ws_key = _confirmation_key(self, self.chat_session_id)
        winning_key = None
        for _ in range(400):  # 20s budget
            if ws_key in pending_plan_confirmations:
                winning_key = ws_key
                break
            # Admin V2 registers under project_id (the slug); grab whatever's
            # waiting since this is a single-test harness.
            if pending_plan_confirmations:
                winning_key = next(iter(pending_plan_confirmations.keys()))
                break
            await asyncio.sleep(0.05)
        if winning_key is None:
            print(f"  [HARNESS] WARNING: no confirmation key appeared (looked for {ws_key} and any)")
            return
        print(f"  [HARNESS] confirming key={winning_key}")
        resolve_plan_confirmation(winning_key, {"confirmed": True})


def _walk_files(root: str) -> list[str]:
    out = []
    for r, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in {"node_modules", ".next", ".git", ".pnpm-store"}]
        for f in files:
            out.append(os.path.relpath(os.path.join(r, f), root))
    return out


async def run_one(test_id: str) -> int:
    spec = TESTS[test_id]
    test_type = spec["type"]
    prompt = spec["prompt"]

    out_dir = f"/tmp/demo_{test_id}"
    os.makedirs(out_dir, exist_ok=True)

    # Save generated projects locally (under the repo) so they survive
    # /tmp eviction and are easy to inspect.
    _slug = {
        "L1": "L1_mailflow", "L2": "L2_dental",
        "A1": "A1_fitness", "A2": "A2_medical",
    }.get(test_id, test_id)
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    projects_root = os.path.join(repo_root, "generated_projects")
    os.makedirs(projects_root, exist_ok=True)
    tmp = os.path.join(projects_root, f"demo_{_slug}")
    if os.path.isdir(tmp):
        import shutil
        shutil.rmtree(tmp)
    os.makedirs(tmp)
    print(f"\n{'='*70}\nTEST {test_id} — {test_type.upper()}\n{'='*70}")
    print(f"Workspace: {tmp}")
    print(f"Prompt:    {prompt}")
    print(f"Mock?      ADMIN_CODEGEN_MOCK={os.environ.get('ADMIN_CODEGEN_MOCK','(unset)')}")
    print(f"V2?        WEBSITE_PIPELINE_V2_ENABLED={os.environ.get('WEBSITE_PIPELINE_V2_ENABLED','(unset)')}")

    # Clone template
    print(f"\nCloning template…")
    t0 = time.perf_counter()
    clone = subprocess.run(
        ["git", "clone", "--depth", "1", "--branch", "main",
         "--single-branch", spec["template"], "."],
        cwd=tmp, capture_output=True, text=True, timeout=180,
    )
    if clone.returncode != 0:
        print(f"❌ Template clone failed:\n{clone.stderr[:300]}")
        with open(os.path.join(out_dir, "summary.json"), "w") as f:
            json.dump({
                "test_id": test_id, "status": "clone_failed",
                "stderr": clone.stderr[:500], "workspace": tmp,
            }, f, indent=2)
        return 1
    print(f"  cloned in {time.perf_counter() - t0:.1f}s")

    # Save workspace path
    with open(os.path.join(out_dir, "workspace.txt"), "w") as f:
        f.write(tmp + "\n")

    chat_session_id = ""
    ws = CaptureWS(chat_session_id)

    validated = {
        "anthropic_api_key": ANTHROPIC_KEY,
        "gemini_api_key": GEMINI_KEY,
        "project_stack": spec["stack"],
        "skeleton_stack": spec["stack"],
        "skeleton_name": spec["stack"],
        "is_admin": spec["is_admin"],
        "scratch_mode": False,
        "new_project_mode": True,
        "template_clone_url": spec["template"],
    }

    from app.services import project_generator as pg

    t_start = time.perf_counter()
    success = False
    pipeline_exc = None
    try:
        success = await pg.generate_new_project(
            description=prompt,
            workspace_path=tmp,
            validated=validated,
            websocket=ws,
            chat_session_id=chat_session_id,
            user_jwt="",
            user_id="",
        )
    except Exception as e:
        pipeline_exc = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
        print(f"\n❌ Pipeline raised: {pipeline_exc[:600]}")
    elapsed = time.perf_counter() - t_start

    print(f"\nFINISHED — success={success}  wall_clock={elapsed:.1f}s")

    # ── Capture event log
    with open(os.path.join(out_dir, "events.json"), "w") as f:
        json.dump(ws.events, f, indent=2, default=str)

    # ── Event-type histogram
    type_counts: dict[str, int] = {}
    error_msgs: list[str] = []
    warning_msgs: list[str] = []
    plan_card = None
    for e in ws.events:
        t = (e or {}).get("type", "?")
        type_counts[t] = type_counts.get(t, 0) + 1
        if t == "error":
            error_msgs.append((e.get("message") or "")[:300])
        elif t == "warning":
            warning_msgs.append((e.get("message") or "")[:300])
        elif t == "chat_message" and e.get("messageType") == "plan":
            plan_card = e.get("planData")

    # ── File listing
    files = _walk_files(tmp)

    # ── Look for key paths per product type
    key_files_found: dict[str, bool] = {}
    if test_type == "landing":
        for p in [
            "src/app/page.jsx", "src/app/page.js",
            "src/components/layout/MarketingHeader.jsx",
            "src/components/layout/MarketingFooter.jsx",
            "src/config/site.js",
            "src/app/globals.css",
        ]:
            key_files_found[p] = os.path.isfile(os.path.join(tmp, p))
    else:  # admin
        for p in [
            "src/App.jsx", "src/App.js",
            "src/lib/db_admin.js", "src/lib/db_admin.ts",
            "src/components/layout/AdminLayout.jsx",
            "src/components/AuthGuard.jsx",
        ]:
            key_files_found[p] = os.path.isfile(os.path.join(tmp, p))

    summary = {
        "test_id": test_id,
        "type": test_type,
        "prompt": prompt,
        "status": "ok" if success else "failed",
        "pipeline_exception": pipeline_exc,
        "wall_clock_s": round(elapsed, 1),
        "workspace": tmp,
        "file_count": len(files),
        "event_type_counts": type_counts,
        "error_count": len(error_msgs),
        "errors": error_msgs[:10],
        "warnings": warning_msgs[:10],
        "plan_card_received": plan_card is not None,
        "plan_card_intro": (plan_card or {}).get("intro") if plan_card else None,
        "plan_pages_count": len((plan_card or {}).get("pages") or []) if plan_card else None,
        "plan_entities_count": len((plan_card or {}).get("entities") or []) if plan_card else None,
        "key_files_found": key_files_found,
        "env_mock_admin": os.environ.get("ADMIN_CODEGEN_MOCK", "(unset)"),
        "env_v2_website": os.environ.get("WEBSITE_PIPELINE_V2_ENABLED", "(unset)"),
    }
    with open(os.path.join(out_dir, "summary.json"), "w") as f:
        json.dump(summary, f, indent=2, default=str)

    # ── Save file listing (cap at 200 lines)
    with open(os.path.join(out_dir, "files.txt"), "w") as f:
        for p in files[:500]:
            f.write(p + "\n")

    print(f"\n📨 Top event types:")
    for t, c in sorted(type_counts.items(), key=lambda x: -x[1])[:10]:
        print(f"  {c:>4} × {t}")
    print(f"\n🗂  Workspace files: {len(files)}")
    print(f"📂 Artifacts in:    {out_dir}/")
    print(f"📂 Workspace path:  {tmp}")

    return 0 if success else 1


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in TESTS:
        print(f"Usage: python {sys.argv[0]} <{'|'.join(TESTS.keys())}>")
        sys.exit(2)
    rc = asyncio.run(run_one(sys.argv[1]))
    sys.exit(rc)


if __name__ == "__main__":
    main()
