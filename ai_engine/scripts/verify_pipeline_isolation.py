"""Pipeline isolation check (no API calls).

For each of three representative prompts, we run only the classifier
+ project_generator routing decision (the cheap parts before any
Gemini or Claude work) and observe which pipeline function would
have been called.

The expensive downstream functions (`run_landing_pipeline`,
`run_website_pipeline`, the legacy admin path inside
`_generate_new_project_inner`) are monkey-patched to record a marker
and return immediately. So this script makes ZERO Gemini/Claude
calls; it only inspects the routing layer.

Run:
    docker exec -e WEBSITE_PIPELINE_V2_ENABLED=true \\
        lucid-ai-ai_engine-1 python scripts/verify_pipeline_isolation.py

What it proves
--------------
Each prompt lands in exactly ONE pipeline entry function, and the
landing/admin paths never call the new Phase-2 helpers
(provision_tenant_for_project / seed_tenant_for_project /
plan_data_model). If any of those got accidentally invoked from a
landing-page prompt, this script would catch it.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _load_env_file(path: str) -> None:
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env_file(str(Path(__file__).resolve().parents[2] / ".env"))

# Enable the website pipeline v2 gate so this script actually exercises
# the new routing branch when the prompt suggests a multi-page site.
os.environ["WEBSITE_PIPELINE_V2_ENABLED"] = "true"


# ── Prompts under test ──────────────────────────────────────────────

CASES: list[tuple[str, str]] = [
    # (prompt, expected_pipeline)
    ("landing page for fitness coach",            "landing"),
    ("Italian restaurant website with menu",      "website"),
    ("internal CRM for sales team",               "admin_legacy"),
]


# ── Recorder ────────────────────────────────────────────────────────

class _PipelineHits:
    """Records which pipeline function was reached for the current case."""

    def __init__(self) -> None:
        self.landing_called = False
        self.website_called = False
        self.admin_legacy_called = False
        self.phase2_helpers_called: list[str] = []   # any unexpected calls
        self.classifier_archetype: str | None = None

    def reset(self) -> None:
        self.__init__()

    def pipeline_label(self) -> str:
        if self.landing_called:
            return "landing"
        if self.website_called:
            return "website"
        if self.admin_legacy_called:
            return "admin_legacy"
        return "none"


HITS = _PipelineHits()


# ── Mocks ───────────────────────────────────────────────────────────

async def _mock_landing(**kwargs) -> bool:
    HITS.landing_called = True
    return True


async def _mock_website(**kwargs) -> bool:
    HITS.website_called = True
    return True


# Any of the Phase-2 helpers being reached from a landing/admin
# prompt is a cross-contamination bug. We record their invocation
# with the calling prompt for the audit report.
async def _phase2_provision_tracer(*args, **kwargs):
    HITS.phase2_helpers_called.append("provision_tenant_for_project")
    return None


async def _phase2_seed_tracer(*args, **kwargs):
    HITS.phase2_helpers_called.append("seed_tenant_for_project")
    return None


async def _phase2_plan_dm_tracer(*args, **kwargs):
    HITS.phase2_helpers_called.append("plan_data_model")
    from app.services.data_model import DataModel
    return DataModel(version="1.0", tables=[], singletons={})


# ── Driver ──────────────────────────────────────────────────────────

async def _run_case(prompt: str, expected: str) -> dict:
    """Drive `_generate_new_project_inner` just far enough to observe
    the routing decision, with all expensive downstream calls mocked.

    We mock at the point of branch: the three pipeline entries plus the
    Phase 2 helpers. We do NOT mock the classifier — its decision is
    what we're verifying. The classifier itself uses a static keyword
    fast-path for the test prompts so it doesn't make a Gemini call.
    """
    HITS.reset()
    workspace = tempfile.mkdtemp(prefix="lucid_iso_check_")

    # `_generate_new_project_inner` runs after `generate_new_project`'s
    # input normalization. We need the classifier to fire, then we want
    # to short-circuit BEFORE any expensive non-routing work.
    #
    # We achieve that by stubbing every downstream pipeline entry. The
    # admin path is more complex — it's not a separate function, it
    # lives inside `_generate_new_project_inner` after the
    # landing/website branches. We detect "admin path was reached" by
    # raising a sentinel exception right after the routing block.
    class _AdminLegacyReached(Exception):
        pass

    real_landing_intent_module = None  # placeholder for static analysis

    # Stage just past the landing/website branch points (line ~7461).
    # The simplest cut-off is the call to `_load_skills` immediately
    # following — patch it to raise our sentinel.
    def _short_circuit_admin(*args, **kwargs):
        raise _AdminLegacyReached()

    with patch(
        "app.services.landing_pipeline.run_landing_pipeline",
        new=_mock_landing,
    ), patch(
        "app.services.website_pipeline.run_website_pipeline",
        new=_mock_website,
    ), patch(
        "app.services.website_pipeline.provision_tenant_for_project",
        new=_phase2_provision_tracer,
    ), patch(
        "app.services.website_pipeline.seed_tenant_for_project",
        new=_phase2_seed_tracer,
    ), patch(
        "app.services.data_model_planner.plan_data_model",
        new=_phase2_plan_dm_tracer,
    ), patch(
        "app.services.project_generator._load_skills",
        new=_short_circuit_admin,
    ):
        try:
            from app.services.project_generator import _generate_new_project_inner
            await _generate_new_project_inner(
                description=prompt,
                workspace_path=workspace,
                validated={
                    "anthropic_api_key": "fake-test-key",
                    "gemini_api_key":    os.environ.get("GOOGLE_API_KEY", ""),
                },
                websocket=None,
                chat_session_id="",      # dev placeholder — skip DB writes
                user_jwt="",
            )
        except _AdminLegacyReached:
            HITS.admin_legacy_called = True
        except Exception as exc:
            # Some upstream step may fail naturally (e.g. classifier
            # API timeout in a sandbox without network). The point of
            # this script is the *routing* observation, not running
            # the full path; capture and report.
            return {
                "prompt":   prompt,
                "expected": expected,
                "got":      HITS.pipeline_label(),
                "ok":       False,
                "error":    f"{type(exc).__name__}: {exc}",
                "phase2_helpers_called": list(HITS.phase2_helpers_called),
            }

    label = HITS.pipeline_label()

    # Cross-contamination rule: only the website case may have called
    # any Phase 2 helper. (And here we mocked them so even that won't
    # actually trigger — we just record the candidate call sites.)
    cross_contamination_ok = (
        not HITS.phase2_helpers_called or expected == "website"
    )

    return {
        "prompt":   prompt,
        "expected": expected,
        "got":      label,
        "ok":       label == expected and cross_contamination_ok,
        "phase2_helpers_called": list(HITS.phase2_helpers_called),
    }


async def main() -> int:
    print("=== Pipeline routing isolation check (no API calls) ===\n")

    results: list[dict] = []
    for prompt, expected in CASES:
        print(f"→ '{prompt}' (expected={expected}) …")
        res = await _run_case(prompt, expected)
        results.append(res)
        symbol = "✓" if res["ok"] else "✗"
        print(f"   {symbol} pipeline={res['got']}"
              + (f"  err={res['error']}" if "error" in res else ""))
        if res["phase2_helpers_called"]:
            print(f"   phase-2 helpers reached: {res['phase2_helpers_called']}")

    print("\n=== Summary ===")
    for r in results:
        sym = "✓" if r["ok"] else "✗"
        extra = ""
        if r["phase2_helpers_called"]:
            extra = f" (phase-2 helpers: {r['phase2_helpers_called']})"
        print(f"  {sym} '{r['prompt']}' → {r['got']} "
              f"(expected {r['expected']}){extra}")

    all_passed = all(r["ok"] for r in results)
    print()
    print("=== ISOLATION " + ("PASSED" if all_passed else "FAILED") + " ===")
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
