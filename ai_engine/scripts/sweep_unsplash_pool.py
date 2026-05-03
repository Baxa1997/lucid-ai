"""HEAD-check every Unsplash ID in _UNSPLASH_POOLS and report 404s.

Run from the ai_engine dir:
  python3 scripts/sweep_unsplash_pool.py

Prints a per-category report and a Python-edit suggestion for each dead ID.
"""
from __future__ import annotations

import ast
import asyncio
import re
from pathlib import Path

import httpx


def _load_pools() -> dict[str, list[str]]:
    """Parse _UNSPLASH_POOLS literal out of project_generator.py without importing FastAPI."""
    src = (
        Path(__file__).resolve().parents[1]
        / "app" / "services" / "project_generator.py"
    ).read_text()
    m = re.search(r"_UNSPLASH_POOLS:\s*dict\[str,\s*list\[str\]\]\s*=\s*(\{.*?\n\})", src, re.DOTALL)
    if not m:
        raise RuntimeError("could not locate _UNSPLASH_POOLS literal")
    return ast.literal_eval(m.group(1))


_UNSPLASH_POOLS = _load_pools()


URL_TMPL = "https://images.unsplash.com/{pid}?auto=format&fit=crop&w=400&q=60"


async def check(pid: str, client: httpx.AsyncClient) -> tuple[str, int]:
    try:
        r = await client.head(URL_TMPL.format(pid=pid), follow_redirects=True, timeout=10.0)
        return pid, r.status_code
    except Exception as exc:
        return pid, -1


async def main() -> None:
    dead: dict[str, list[str]] = {}
    total = 0
    async with httpx.AsyncClient() as client:
        for label, ids in _UNSPLASH_POOLS.items():
            unique = list(dict.fromkeys(ids))  # preserve order, drop dupes
            results = await asyncio.gather(*(check(p, client) for p in unique))
            bad = [p for p, code in results if code != 200]
            total += len(unique)
            if bad:
                dead[label] = bad
                print(f"{label}: {len(bad)}/{len(unique)} dead")
                for p in bad:
                    print(f"   ✗  {p}")

    print()
    print(f"Total IDs checked: {total}")
    print(f"Dead IDs: {sum(len(v) for v in dead.values())}")
    if dead:
        print()
        print("Strings to remove from project_generator.py _UNSPLASH_POOLS:")
        for label, bads in dead.items():
            print(f"# {label}")
            for p in bads:
                print(f'    "{p}",')


if __name__ == "__main__":
    asyncio.run(main())
