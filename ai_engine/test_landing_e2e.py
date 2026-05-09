"""End-to-end live test for the new landing pipeline.

Runs run_landing_pipeline() against /tmp/lucid_landing_test (a fresh copy
of skeletons/nextjs) using REAL Gemini + Claude calls. Prints WS messages
to stdout so progress is visible.

Run: cd ai_engine && python3 test_landing_e2e.py
"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))


def _load_env(path: str) -> None:
    if not os.path.exists(path):
        return
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env(os.path.join(os.path.dirname(__file__), "..", ".env"))

WORKSPACE = "/tmp/lucid_landing_test"
DESCRIPTION = (
    "A landing page for an organic farm-to-table restaurant called "
    "Verdant Table in Brooklyn — seasonal tasting menus, private events, "
    "online reservations."
)
CLASSIFICATION = {
    "app_type": "landing_page",
    "layout_archetype": "single_page_landing",
    "domain": "food_restaurant",
    "is_single_page": True,
}


class FakeWS:
    async def send_json(self, data):
        msg = data.get("message", data.get("content", ""))
        kind = data.get("type", "")
        if msg:
            print(f"  [{kind}] {msg[:120]}")


async def main() -> int:
    if not os.path.exists(WORKSPACE):
        print(f"ERROR: workspace {WORKSPACE} missing — copy nextjs skeleton first")
        return 1

    validated = {
        "anthropic_api_key": os.environ.get("ANTHROPIC_API_KEY", ""),
        "gemini_api_key": os.environ.get("GOOGLE_API_KEY", ""),
    }
    if not validated["anthropic_api_key"] or not validated["gemini_api_key"]:
        print("ERROR: ANTHROPIC_API_KEY or GOOGLE_API_KEY missing")
        return 1

    from app.services.landing_pipeline import run_landing_pipeline

    ws = FakeWS()
    t0 = time.monotonic()
    print(f"\n=== LANDING PIPELINE E2E TEST ===")
    print(f"workspace: {WORKSPACE}")
    print(f"description: {DESCRIPTION[:80]}...")
    print(f"classification: {CLASSIFICATION['layout_archetype']} / {CLASSIFICATION['domain']}\n")

    ok = await run_landing_pipeline(
        description=DESCRIPTION,
        classification=CLASSIFICATION,
        workspace_path=WORKSPACE,
        validated=validated,
        websocket=ws,
    )
    elapsed = time.monotonic() - t0
    print(f"\n=== RESULT: ok={ok}  total={elapsed:.1f}s ===")

    if ok:
        sections_dir = os.path.join(WORKSPACE, "src", "components", "sections")
        if os.path.isdir(sections_dir):
            files = sorted(os.listdir(sections_dir))
            print(f"\nGenerated section files ({len(files)}):")
            for f in files:
                p = os.path.join(sections_dir, f)
                print(f"  • {f}  ({os.path.getsize(p)} bytes)")

        landing_json = os.path.join(WORKSPACE, "src", "content", "landing.json")
        if os.path.exists(landing_json):
            print(f"\nlanding.json: {os.path.getsize(landing_json)} bytes")

    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
