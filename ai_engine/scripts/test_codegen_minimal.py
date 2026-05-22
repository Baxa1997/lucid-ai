"""Minimal Stage 5 codegen smoke test.

Skips Stages 0.5–4 (purpose / intent / research / visual_dna / plan) by
fabricating their outputs, then calls `generate_website` directly with a
2-page plan (Home + About). Validates that the dict-as-content fix and
the concurrency change actually let Claude codegen complete.

Cost estimate: ~$0.30-0.50 in Claude (2 page calls + header + footer).
Zero Gemini spend.

Run from ai_engine/:
    venv/bin/python scripts/test_codegen_minimal.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time


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
if not ANTHROPIC_KEY:
    print("ERROR: ANTHROPIC_API_KEY not set")
    sys.exit(2)

# Per-section codegen mode for this smoke test (matches new default).
os.environ.setdefault("WEBSITE_PER_SECTION_CODEGEN_ENABLED", "1")

# Fake Supabase — pipeline reads these at module import.
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "x")
os.environ.setdefault("SUPABASE_JWT_SECRET", "x")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "x")
os.environ.setdefault("ENCRYPTION_KEY", "x" * 32)
os.environ.setdefault("INTERNAL_API_KEY", "x")


class CaptureWS:
    """Mock WebSocket that prints events and tracks per-page results."""
    def __init__(self):
        self.events: list[dict] = []
        self.page_started = 0
        self.page_done_ok = 0
        self.page_done_fail = 0
        self.errors: list[str] = []

    async def send_json(self, payload):
        self.events.append(payload)
        if not isinstance(payload, dict):
            return
        kind = payload.get("type", "?")
        msg = (payload.get("message") or "")[:160]
        if kind == "progress":
            print(f"  [progress] {msg}")
        elif kind == "warning":
            print(f"  [warn] {msg}")
        elif kind == "error":
            print(f"  [ERROR] {msg}")
            self.errors.append(msg)


def _build_minimal_plan() -> dict:
    """A 2-page plan for a fictional coffee shop. Just enough sections to
    exercise the codegen path without burning lots of Claude tokens."""
    return {
        "brand": {
            "name": "Aurum Coffee",
            "tagline": "Small-batch roasts, brewed with care.",
            "domain": "specialty coffee",
        },
        "pages": [
            {
                "route": "/",
                "title": "Home",
                "purpose": "Introduce the cafe, showcase signature drinks, and drive visits.",
                "sections": [
                    {"type": "hero"},
                    {"type": "value_prop"},
                    {"type": "features"},
                    {"type": "cta"},
                ],
            },
            {
                "route": "/about",
                "title": "About",
                "purpose": "Tell the founder's story and the sourcing philosophy.",
                "sections": [
                    {"type": "hero"},
                    {"type": "story"},
                    {"type": "stats"},
                    {"type": "cta"},
                ],
            },
        ],
    }


def _build_minimal_visual_dna() -> dict:
    """Minimal visual_dna — just enough so the codegen prompt has tokens
    to interpolate. Real runs have far richer dna; we keep it tight to
    minimize prompt size."""
    return {
        "primary_color": "#7c3a1d",
        "secondary_color": "#f3e9dc",
        "cultural_intensity": "calm",
        "layout_signature": "warm editorial grid with generous whitespace",
        "typography": {
            "heading": "Fraunces",
            "body": "Inter",
        },
        "heading_font": "Fraunces",
        "body_font": "Inter",
        # Per the extractor contract: section_anatomies values are
        # one-paragraph STRINGS (max 25 words / 150 chars), not dicts.
        "section_anatomies": {
            "hero": "Full-width warm hero, hand-lettered logo top-left, big serif headline center, "
                    "tagline below in calm sans, paired CTA buttons, signature drink photo right.",
            "story": "Two-column 60/40: founder portrait on left, serif heading + 3 paragraphs on "
                     "the right, soft cream background, decorative coffee leaf accent in the corner.",
            "features": "Three-column grid of feature cards, each card with line-icon at top, "
                        "serif heading, two-line description, warm muted dividers between cards.",
            "stats": "Centered band of three large stat tiles, oversized serif numbers, "
                     "small uppercase label under each, soft cream backdrop.",
            "cta": "Centered band, oversized serif headline, supporting paragraph, "
                   "single primary CTA button, secondary text link below.",
            "value_prop": "Centered three-up: each item is icon + short heading + one-line body, "
                          "minimalist editorial rhythm, warm typography.",
        },
        "section_flavors": {},
        "motifs": ["serif_warmth", "soft_grain"],
    }


def _build_minimal_purpose() -> dict:
    return {
        "primary_purpose": "brand_awareness",
        "industry": "specialty coffee",
        "audience": "b2c_consumers",
    }


async def main() -> int:
    print("=" * 70)
    print("test_codegen_minimal — direct generate_website call with 2-page plan")
    print("=" * 70)

    plan = _build_minimal_plan()
    vdna = _build_minimal_visual_dna()
    purpose = _build_minimal_purpose()

    print(f"Plan: {plan['brand']['name']} — {len(plan['pages'])} pages "
          f"({', '.join(p['route'] for p in plan['pages'])})")
    print(f"Concurrency: 4 (default after the rate-limit fix)")
    print(f"Per-page timeout: 600s (after the 64K-tokens fix)")

    workspace = tempfile.mkdtemp(prefix="codegen_minimal_")
    print(f"Workspace: {workspace}")

    ws = CaptureWS()

    from app.services.website_orchestrator import generate_website

    t0 = time.perf_counter()
    try:
        result = await generate_website(
            plan=plan,
            visual_dna=vdna,
            api_key=ANTHROPIC_KEY,
            websocket=ws,
            concurrency=4,
            purpose_data=purpose,
            page_images={},
            data_model=None,
        )
    except Exception as exc:
        import traceback
        print(f"\nORCHESTRATOR RAISED: {type(exc).__name__}: {exc}")
        traceback.print_exc()
        return 1
    elapsed = time.perf_counter() - t0

    print()
    print("=" * 70)
    print(f"ORCHESTRATOR DONE  elapsed={elapsed:.1f}s")
    print("=" * 70)

    # Result shape: {"files": [...], "page_results": {...}, "header_ok": bool, "footer_ok": bool}
    files = result.get("files") or []
    page_results = result.get("page_results") or {}
    header_ok = result.get("header_ok")
    footer_ok = result.get("footer_ok")

    pages_ok = sum(1 for v in page_results.values() if v)
    pages_total = len(page_results)

    print(f"Total files generated: {len(files)}")
    print(f"Pages OK: {pages_ok}/{pages_total}")
    print(f"  Per page: {page_results}")
    print(f"Header OK: {header_ok}")
    print(f"Footer OK: {footer_ok}")

    # Write files to workspace so user can inspect
    written = 0
    for f in files:
        if not isinstance(f, dict):
            continue
        path = f.get("path")
        content = f.get("content")
        if not path or content is None:
            continue
        full = os.path.join(workspace, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as fh:
            fh.write(str(content))
        written += 1
    print(f"\nFiles written to disk: {written}")

    # List the file paths grouped
    paths_by_page: dict[str, list[str]] = {}
    other: list[str] = []
    for f in files:
        if not isinstance(f, dict):
            continue
        path = f.get("path") or ""
        matched = False
        for p in plan["pages"]:
            route = (p.get("route") or "").strip("/") or "home"
            if route in path or (route == "home" and "(marketing)/page" in path):
                paths_by_page.setdefault(route, []).append(path)
                matched = True
                break
        if not matched:
            other.append(path)

    print("\nFile breakdown:")
    for route, ps in paths_by_page.items():
        print(f"  [{route}]")
        for p in ps:
            print(f"    {p}")
    if other:
        print("  [chrome / other]")
        for p in other:
            print(f"    {p}")

    print()
    if pages_ok == pages_total and header_ok and footer_ok:
        print(f"RESULT: ✅ PASS — all {pages_total} pages + header + footer generated cleanly")
        print(f"  Inspect output: {workspace}")
        return 0
    else:
        print(f"RESULT: ❌ FAIL — {pages_total - pages_ok} pages failed, header_ok={header_ok}, footer_ok={footer_ok}")
        if ws.errors:
            print("\nWS errors observed:")
            for e in ws.errors[:5]:
                print(f"  - {e}")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
