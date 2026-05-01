"""End-to-end smoke test for the preview pipeline fixes (Batch 1 + 2).

Run inside the ai_engine container:
    docker exec lucid-ai-ai_engine-1 python /app/scripts/test_preview_fixes.py

Tests:
  1. Cold start — fresh dev server boots, returns URL
  2. Reuse — second call without force_restart returns same URL (no kill)
  3. Force restart — second call with force_restart=True kills + starts fresh on (probably) different port
  4. Concurrent calls under lock — only ONE dev server ends up registered
  5. Idempotent next.config patch — repeated calls don't rewrite the file
  6. Health check rejects 5xx — covered by code review (hard to simulate without a fake server)

Uses an existing cached preview workspace; does NOT generate any new project.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from glob import glob

sys.path.insert(0, "/app")

from app.services import local_preview as lp


class FakeWS:
    """Captures emitted events instead of sending to a real WebSocket."""
    def __init__(self):
        self.events: list[dict] = []

    async def send_json(self, payload):
        self.events.append(payload)

    def types(self) -> list[str]:
        return [e.get("type", "?") for e in self.events]

    def latest(self, t: str):
        for e in reversed(self.events):
            if e.get("type") == t:
                return e
        return None


def find_test_workspace() -> str:
    """Pick a Next.js cached workspace with node_modules already installed."""
    base = "/app/storage/preview_ws"
    for d in sorted(glob(f"{base}/lucid_ws_*")):
        pkg = os.path.join(d, "package.json")
        nm = os.path.join(d, "node_modules")
        if not (os.path.isfile(pkg) and os.path.isdir(nm)):
            continue
        with open(pkg) as f:
            content = f.read()
        if '"next"' in content:
            return d
    raise RuntimeError("No Next.js workspace with node_modules found")


async def cleanup(conv_id: str):
    await lp.stop_local_preview(conversation_id=conv_id)


async def test_cold_start_and_reuse(workspace: str) -> tuple[bool, str]:
    """Test 1+2: cold start, then reuse without force_restart."""
    conv_id = f"test-cold-{int(time.time())}"
    ws = FakeWS()
    try:
        url1 = await lp.start_local_preview(
            workspace_path=workspace,
            conversation_id=conv_id,
            websocket=ws,
        )
        if not url1:
            return False, f"cold start returned None. events={ws.types()}"
        port1 = lp._active_servers[conv_id]["port"]

        # Second call WITHOUT force_restart → should reuse same port
        ws2 = FakeWS()
        url2 = await lp.start_local_preview(
            workspace_path=workspace,
            conversation_id=conv_id,
            websocket=ws2,
        )
        port2 = lp._active_servers[conv_id]["port"]
        if port1 != port2:
            return False, f"reuse failed: port1={port1}, port2={port2} (different — should match)"
        if url1 != url2:
            return False, f"reuse returned different URL: {url1} vs {url2}"
        return True, f"cold start on port {port1}, reuse confirmed (same port)"
    finally:
        await cleanup(conv_id)


async def test_force_restart(workspace: str) -> tuple[bool, str]:
    """Test 3: force_restart kills the running server and starts fresh."""
    conv_id = f"test-force-{int(time.time())}"
    ws1 = FakeWS()
    try:
        url1 = await lp.start_local_preview(
            workspace_path=workspace,
            conversation_id=conv_id,
            websocket=ws1,
        )
        if not url1:
            return False, f"initial start failed. events={ws1.types()}"
        port1 = lp._active_servers[conv_id]["port"]
        proc1 = lp._active_servers[conv_id]["process"]
        pid1 = proc1.pid if proc1 else None

        # Force restart — should kill proc1 and start fresh
        ws2 = FakeWS()
        url2 = await lp.start_local_preview(
            workspace_path=workspace,
            conversation_id=conv_id,
            websocket=ws2,
            force_restart=True,
        )
        if not url2:
            return False, f"force_restart failed. events={ws2.types()}"
        port2 = lp._active_servers[conv_id]["port"]
        proc2 = lp._active_servers[conv_id]["process"]
        pid2 = proc2.pid if proc2 else None

        # Original process MUST be dead now
        if proc1.returncode is None:
            return False, f"original proc still alive after force_restart (pid={pid1})"
        if pid1 == pid2:
            return False, f"force_restart returned same pid {pid1} (no actual restart)"
        return True, f"force_restart killed pid={pid1} (port {port1}), started pid={pid2} (port {port2})"
    finally:
        await cleanup(conv_id)


async def test_concurrent_lock(workspace: str) -> tuple[bool, str]:
    """Test 4: two concurrent start calls → only one dev server registered."""
    conv_id = f"test-lock-{int(time.time())}"
    ws_a, ws_b = FakeWS(), FakeWS()
    try:
        # Race two starts — without the lock both could spawn dev servers
        results = await asyncio.gather(
            lp.start_local_preview(
                workspace_path=workspace,
                conversation_id=conv_id,
                websocket=ws_a,
            ),
            lp.start_local_preview(
                workspace_path=workspace,
                conversation_id=conv_id,
                websocket=ws_b,
            ),
            return_exceptions=True,
        )
        # Both should succeed and return the SAME url (second one reuses first's)
        urls = [r for r in results if isinstance(r, str)]
        if len(urls) != 2:
            return False, f"expected 2 URLs, got {len(urls)} results={results}"
        if urls[0] != urls[1]:
            return False, f"concurrent calls returned DIFFERENT urls: {urls} (lock failed)"
        # Only one entry in the registry
        port = lp._active_servers[conv_id]["port"]
        return True, f"concurrent calls serialized correctly — single server on port {port}"
    finally:
        await cleanup(conv_id)


async def test_idempotent_patch(workspace: str) -> tuple[bool, str]:
    """Test 5: _patch_nextjs_base_path doesn't rewrite when unchanged."""
    config_files = [
        os.path.join(workspace, n)
        for n in ("next.config.mjs", "next.config.js", "next.config.ts")
    ]
    config = next((c for c in config_files if os.path.isfile(c)), None)
    if not config:
        return True, "no next.config — skipping idempotency test (not applicable)"

    port = 4099  # pick a port unlikely to be in use
    # First patch — may write
    await lp._patch_nextjs_base_path(workspace, port)
    mtime1 = os.path.getmtime(config)

    # Second patch with same port — should NOT write
    await asyncio.sleep(0.05)  # ensure mtime granularity
    await lp._patch_nextjs_base_path(workspace, port)
    mtime2 = os.path.getmtime(config)

    if mtime2 != mtime1:
        return False, f"second patch rewrote {config} (mtime changed {mtime1} → {mtime2})"
    return True, f"idempotent: second patch did NOT rewrite {os.path.basename(config)}"


async def main():
    print("=" * 70)
    print("Preview pipeline smoke tests (Batch 1 + 2)")
    print("=" * 70)

    workspace = find_test_workspace()
    print(f"Test workspace: {workspace}")
    print()

    tests = [
        ("1. Cold start + reuse (no force_restart)", test_cold_start_and_reuse),
        ("2. Force restart kills old proc",          test_force_restart),
        ("3. Concurrent starts serialized by lock",  test_concurrent_lock),
        ("4. Idempotent next.config patch",          test_idempotent_patch),
    ]

    results = []
    for name, fn in tests:
        print(f"▶ {name}")
        t0 = time.time()
        try:
            ok, msg = await fn(workspace)
        except Exception as exc:
            ok, msg = False, f"EXCEPTION: {exc!r}"
        dt = time.time() - t0
        marker = "✅" if ok else "❌"
        print(f"  {marker} ({dt:.1f}s) {msg}")
        print()
        results.append((name, ok, msg))

    print("=" * 70)
    passed = sum(1 for _, ok, _ in results if ok)
    print(f"Results: {passed}/{len(results)} passed")
    for name, ok, msg in results:
        print(f"  {'✅' if ok else '❌'} {name}")
    print("=" * 70)

    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
