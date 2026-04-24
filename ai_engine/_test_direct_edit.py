"""Integration test for the direct-edit path.

Creates a temporary workspace with one JSX file, asks Claude to apply a
surgical change via execute_direct_edit, then verifies the file was
edited and no .lucid.tmp leftovers remain.

Run inside the ai_engine container:
    docker compose exec -T ai_engine python /app/_test_direct_edit.py

Deletes itself-tested workspace on success. Safe to run repeatedly.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile


# Stub WebSocket just enough to accept send_json without actually sending
class _NoopWS:
    async def send_json(self, _msg):
        return None


_SAMPLE_HERO = '''\
import React from 'react';

export default function Hero() {
  return (
    <section className="py-20 text-center">
      <h1 className="text-5xl font-bold">Welcome to our platform</h1>
      <p className="mt-4 text-lg text-gray-600">Build amazing things faster.</p>
    </section>
  );
}
'''


async def main() -> int:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        print("FAIL: ANTHROPIC_API_KEY not set")
        return 1

    workspace = tempfile.mkdtemp(prefix="lucid_test_direct_")
    try:
        src_dir = os.path.join(workspace, "src", "components")
        os.makedirs(src_dir)
        hero_path = os.path.join(src_dir, "Hero.jsx")
        with open(hero_path, "w") as f:
            f.write(_SAMPLE_HERO)

        from app.services.pipeline.step5_direct import execute_direct_edit

        result = await execute_direct_edit(
            task="Change the hero headline text from 'Welcome to our platform' to 'Welcome home'.",
            workspace_path=workspace,
            api_key=api_key,
            classification={
                "model_id": "claude-sonnet-4-6",
                "task_type": "ui_simple",
                "files_estimate": 1,
            },
            plan=(
                "FILES TO CHANGE:\n"
                "src/components/Hero.jsx\n\n"
                "EXACT CHANGES:\n"
                "Change the <h1> text from 'Welcome to our platform' to 'Welcome home'."
            ),
            relevant_files=["src/components/Hero.jsx"],
            websocket=_NoopWS(),
            manifest="",
            timeout_s=60.0,
        )

        print(f"execute_direct_edit returned: {result}")

        if not result:
            print("FAIL: direct edit returned False")
            return 2

        with open(hero_path) as f:
            new_content = f.read()

        has_new = "Welcome home" in new_content
        has_old = "Welcome to our platform" in new_content

        # Check no .lucid.tmp leftovers anywhere in the workspace
        leftovers = []
        for root, _, files in os.walk(workspace):
            for fn in files:
                if fn.endswith(".lucid.tmp"):
                    leftovers.append(os.path.join(root, fn))

        print(f"  has 'Welcome home'          : {has_new}")
        print(f"  has 'Welcome to our platform': {has_old}")
        print(f"  .lucid.tmp leftovers        : {leftovers}")

        if has_new and not has_old and not leftovers:
            print("PASS")
            return 0
        print("FAIL: file content did not match expected edit")
        print("---- current file ----")
        print(new_content)
        return 3
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
