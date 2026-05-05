"""Deep static + unit tests for Phase A/B/C/D parallel-pipeline changes.

Covers:
  • A — _emit_file_writes batch_index plumbing, _run_phase2_parallel_batches
        success/failure/retry/dedupe semantics
  • B — _should_batch_pages gating across archetypes & flag states
  • C — _compute_batch_plan boundary tables (n=0..20, all branches)
  • D — _should_run_deep_research gating, prompt builders shape,
        enrich_research_with_deep_dives end-to-end with a mocked
        _call_gemini_single (verifies fan-out, append format, fail-soft)

Run from ai_engine/:
    python3 scripts/test_phase_abcd.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import types

# Make `app.*` importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Set the Phase B/D flags to "1" before import so flag-gated branches behave
os.environ.setdefault("PHASE2_PAGE_BATCHING", "1")
os.environ.setdefault("PHASE_D_DEEP_RESEARCH", "1")
# Avoid pulling real config validation — these are only needed by other modules
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "x")
os.environ.setdefault("SUPABASE_JWT_SECRET", "x")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "x")
os.environ.setdefault("ENCRYPTION_KEY", "x" * 32)
os.environ.setdefault("INTERNAL_API_KEY", "x")


# ── Stub heavy optional deps so import never explodes ──────────────────────
def _install_stub_modules() -> None:
    if "openhands" not in sys.modules:
        oh = types.ModuleType("openhands")
        sys.modules["openhands"] = oh
        sys.modules["openhands.sdk"] = types.ModuleType("openhands.sdk")


_install_stub_modules()

import importlib

pg = importlib.import_module("app.services.project_generator")


# ── Tiny test harness ──────────────────────────────────────────────────────
PASS = 0
FAIL = 0
FAILURES: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {label}")
    else:
        FAIL += 1
        FAILURES.append(f"{label}{(' — ' + detail) if detail else ''}")
        print(f"  ✗ {label}{(' — ' + detail) if detail else ''}")


def section(title: str) -> None:
    print(f"\n=== {title} ===")


# ╔══════════════════════════════════════════════════════════╗
# ║ Phase C — _compute_batch_plan                            ║
# ╚══════════════════════════════════════════════════════════╝
section("Phase C — _compute_batch_plan")

cases = [
    # (n_items, max_per_batch, expected_batch_sizes)
    (0,  4, []),
    (1,  4, [1]),
    (2,  4, [2]),
    (3,  4, [3]),
    (4,  4, [2, 2]),
    (5,  4, [3, 2]),
    (6,  4, [2, 2, 2]),
    (7,  4, [3, 3, 1]),
    (8,  4, [2, 2, 2, 2]),
    (12, 4, [3, 3, 3, 3]),
    (16, 4, [4, 4, 4, 4]),
    (20, 4, [4, 4, 4, 4, 4]),
    # admin path uses max_per_batch=3
    (4,  3, [2, 2]),
    (5,  3, [3, 2]),
    (9,  3, [3, 3, 3]),
]
for n, mpb, expected in cases:
    items = list(range(n))
    out = pg._compute_batch_plan(items, max_per_batch=mpb)
    sizes = [len(b) for b in out]
    check(f"n={n} max_per_batch={mpb} → {sizes}", sizes == expected, f"got {sizes} want {expected}")
    # Also verify no item dropped or duplicated
    if out:
        flat = [x for b in out for x in b]
        check(f"n={n} items roundtrip", flat == items)


# ╔══════════════════════════════════════════════════════════╗
# ║ Phase B — _should_batch_pages                            ║
# ╚══════════════════════════════════════════════════════════╝
section("Phase B — _should_batch_pages gate")

pages5 = [{"name": f"p{i}"} for i in range(5)]
pages4 = [{"name": f"p{i}"} for i in range(4)]
pages8 = [{"name": f"p{i}"} for i in range(8)]

check("consumer_website 5 pages → True", pg._should_batch_pages("consumer_website", pages5) is True)
check("consumer_website 4 pages (under threshold) → False", pg._should_batch_pages("consumer_website", pages4) is False)
check("portfolio 8 pages → True", pg._should_batch_pages("portfolio", pages8) is True)
check("blog 5 pages → True", pg._should_batch_pages("blog", pages5) is True)
check("marketplace 5 pages → True", pg._should_batch_pages("marketplace", pages5) is True)
check("single_page_landing 5 pages → False (landings excluded)", pg._should_batch_pages("single_page_landing", pages5) is False)
check("landing 8 pages → False (landings excluded)", pg._should_batch_pages("landing", pages8) is False)
check("admin_panel 8 pages → False (admin uses entity batching)", pg._should_batch_pages("admin_panel", pages8) is False)
check("admin_dashboard 8 pages → False", pg._should_batch_pages("admin_dashboard", pages8) is False)
check("crm 8 pages → False", pg._should_batch_pages("crm", pages8) is False)
check("saas_dashboard 8 pages → False", pg._should_batch_pages("saas_dashboard", pages8) is False)
check("None pages → False", pg._should_batch_pages("consumer_website", None) is False)
check("empty list → False", pg._should_batch_pages("consumer_website", []) is False)

# Flag-off behavior (rebind the module constant directly)
_orig_b_flag = pg._PHASE2_PAGE_BATCHING_ENABLED
pg._PHASE2_PAGE_BATCHING_ENABLED = False
check("flag off → False even for 8 pages", pg._should_batch_pages("consumer_website", pages8) is False)
pg._PHASE2_PAGE_BATCHING_ENABLED = _orig_b_flag


# ╔══════════════════════════════════════════════════════════╗
# ║ Phase D — _should_run_deep_research                      ║
# ╚══════════════════════════════════════════════════════════╝
section("Phase D — _should_run_deep_research gate")

ent4 = [{"name": f"E{i}"} for i in range(4)]
ent3 = [{"name": f"E{i}"} for i in range(3)]
ent10 = [{"name": f"E{i}"} for i in range(10)]

check("admin_panel 4 entities → True", pg._should_run_deep_research("admin_panel", ent4, []) is True)
check("admin_panel 3 entities (under threshold) → False", pg._should_run_deep_research("admin_panel", ent3, []) is False)
check("admin_panel 10 entities → True", pg._should_run_deep_research("admin_panel", ent10, []) is True)
check("consumer_website 5 pages → True", pg._should_run_deep_research("consumer_website", [], pages5) is True)
check("consumer_website 4 pages (under threshold) → False", pg._should_run_deep_research("consumer_website", [], pages4) is False)
check("portfolio 8 pages → True", pg._should_run_deep_research("portfolio", [], pages8) is True)
check("blog 5 pages → True", pg._should_run_deep_research("blog", [], pages5) is True)
check("marketplace 5 pages → True", pg._should_run_deep_research("marketplace", [], pages5) is True)
check("single_page_landing 8 pages → False (landings excluded)", pg._should_run_deep_research("single_page_landing", [], pages8) is False)
check("landing 8 pages → False", pg._should_run_deep_research("landing", [], pages8) is False)
check("unknown archetype → False", pg._should_run_deep_research("foo_archetype", ent10, pages8) is False)
check("None entities + admin → False", pg._should_run_deep_research("admin_panel", None, None) is False)

# Flag-off behavior
_orig_d_flag = pg._PHASE_D_DEEP_RESEARCH_ENABLED
pg._PHASE_D_DEEP_RESEARCH_ENABLED = False
check("flag off → False (admin 10 entities)", pg._should_run_deep_research("admin_panel", ent10, []) is False)
pg._PHASE_D_DEEP_RESEARCH_ENABLED = _orig_d_flag


# ╔══════════════════════════════════════════════════════════╗
# ║ Phase D — prompt builders                                ║
# ╚══════════════════════════════════════════════════════════╝
section("Phase D — prompt builders")

ent_invoice = {
    "name": "Invoice",
    "fields": [
        {"name": "id"}, {"name": "amount"}, {"name": "currency"},
        {"name": "status"}, {"name": "customer_id"},
    ],
}
p = pg._build_entity_deep_research_prompt(ent_invoice, domain="b2b_saas", brand_name="Acme")
check("entity prompt mentions ENTITY name", "Invoice" in p)
check("entity prompt mentions domain", "b2b_saas" in p)
check("entity prompt mentions brand name", "Acme" in p)
check("entity prompt enumerates fields", "amount" in p and "currency" in p and "status" in p)
check("entity prompt requires concrete sections", "DOMAIN SEMANTICS" in p and "VALIDATION" in p and "WORKFLOW" in p)
check("entity prompt forbids platitudes", "platitudes" in p.lower() or "no platitudes" in p.lower())

# Field overflow safety
big_ent = {"name": "Big", "fields": [{"name": f"field_{i:03d}"} for i in range(500)]}
p_big = pg._build_entity_deep_research_prompt(big_ent, domain="x", brand_name="Y")
check("entity prompt truncates massive field lists", len(p_big) < 5000, f"len={len(p_big)}")

# Non-dict fields don't crash
p_weird = pg._build_entity_deep_research_prompt(
    {"name": "Weird", "fields": ["bad_string", None, {"name": "ok"}]},
    domain="x", brand_name="Y",
)
check("entity prompt handles non-dict field entries", "Weird" in p_weird and "ok" in p_weird)

# Page prompt
page_pricing = {
    "name": "Pricing",
    "purpose": "Convert evaluators to paid tiers",
    "sections": [{"name": "tier_grid"}, {"name": "faq"}, {"name": "cta"}],
}
pp = pg._build_page_deep_research_prompt(page_pricing, domain="b2b_saas", brand_name="Acme")
check("page prompt mentions PAGE name", "Pricing" in pp)
check("page prompt mentions domain", "b2b_saas" in pp)
check("page prompt mentions brand name", "Acme" in pp)
check("page prompt mentions purpose", "Convert evaluators" in pp)
check("page prompt enumerates section hints", "tier_grid" in pp and "faq" in pp)
check("page prompt requires concrete sections", "JOB-TO-BE-DONE" in pp and "CONTENT BEATS" in pp and "CONVERSION" in pp)

# Empty-ish inputs
p_empty = pg._build_page_deep_research_prompt({}, domain="d", brand_name="b")
check("page prompt safe with empty input", "Page" in p_empty and "(unspecified)" in p_empty)

p_path_only = pg._build_page_deep_research_prompt({"path": "/about"}, domain="d", brand_name="b")
check("page prompt falls back to path when no name", "/about" in p_path_only)


# ╔══════════════════════════════════════════════════════════╗
# ║ Phase D — enrich_research_with_deep_dives (mocked)       ║
# ╚══════════════════════════════════════════════════════════╝
section("Phase D — enrich_research_with_deep_dives orchestrator")

# Stub _call_gemini_single so we don't hit the network
_call_log: list[tuple[str, str]] = []  # (label, prompt[:80])


async def _fake_gemini_ok(prompt, gemini_url, is_pro, websocket, label, max_tokens=10000):
    _call_log.append((label, prompt[:80]))
    # Echo something that contains the unit name so we can verify it ends up in the right block
    # Pull the unit name out of the prompt heuristically
    unit_marker = "ENTITY:" if "ENTITY:" in prompt else "PAGE:"
    name_line = next((ln for ln in prompt.splitlines() if ln.startswith(unit_marker)), "")
    name = name_line.split(":", 1)[-1].strip() if name_line else "?"
    return f"DEEP_RESULT_FOR::{name}\nIndustry-leader pattern paragraph for {name}.\nNo === markers in body."


pg._call_gemini_single = _fake_gemini_ok  # monkeypatch on the module


class FakeWS:
    def __init__(self):
        self.events: list[tuple[str, str]] = []

    async def send_json(self, payload):
        # Mirror what _ws_send writes
        if isinstance(payload, dict) and "type" in payload:
            self.events.append((payload.get("type", ""), str(payload.get("message", ""))[:120]))


# ── Admin-with-entities path ──
schema_admin = {
    "entities": [
        {"name": "Invoice", "fields": [{"name": "amount"}]},
        {"name": "Customer", "fields": [{"name": "email"}]},
        {"name": "Subscription", "fields": [{"name": "plan"}]},
        {"name": "RefundRequest", "fields": [{"name": "reason"}]},
    ],
    "pages": [],
}
ws = FakeWS()
_call_log.clear()
out = asyncio.run(pg.enrich_research_with_deep_dives(
    "ORIGINAL_RESEARCH_BLOB",
    schema=schema_admin,
    layout_archetype="admin_panel",
    domain="b2b_saas",
    brand_name="Acme",
    websocket=ws,
    gemini_key="fake",
))
check("admin: original blob preserved at start", out.startswith("ORIGINAL_RESEARCH_BLOB"))
check("admin: 4 Gemini calls dispatched", len(_call_log) == 4, f"got {len(_call_log)}")
for ent_name in ("Invoice", "Customer", "Subscription", "RefundRequest"):
    check(f"admin: ===ENTITY_DEEP::{ent_name}=== block appended", f"===ENTITY_DEEP::{ent_name}===" in out)
check("admin: progress event emitted", any("Deep research" in m for _, m in ws.events))

# ── Multi-page consumer path ──
schema_consumer = {
    "entities": [],
    "pages": [
        {"name": "Home", "purpose": "Primary entry"},
        {"name": "Pricing"},
        {"name": "Features"},
        {"name": "About"},
        {"name": "Contact"},
    ],
}
ws = FakeWS()
_call_log.clear()
out = asyncio.run(pg.enrich_research_with_deep_dives(
    "BASE",
    schema=schema_consumer,
    layout_archetype="consumer_website",
    domain="finance",
    brand_name="Lumen",
    websocket=ws,
    gemini_key="fake",
))
check("consumer: 5 page calls dispatched", len(_call_log) == 5, f"got {len(_call_log)}")
for pg_name in ("Home", "Pricing", "Features", "About", "Contact"):
    check(f"consumer: ===PAGE_DEEP::{pg_name}=== block appended", f"===PAGE_DEEP::{pg_name}===" in out)

# ── Landing archetype is excluded entirely ──
ws = FakeWS()
_call_log.clear()
out = asyncio.run(pg.enrich_research_with_deep_dives(
    "BASE",
    schema={"pages": [{"name": "Hero"}], "entities": []},
    layout_archetype="single_page_landing",
    domain="x",
    brand_name="y",
    websocket=ws,
    gemini_key="fake",
))
check("landing: zero Gemini calls (excluded)", len(_call_log) == 0)
check("landing: blob unchanged", out == "BASE")

# ── Missing key is fail-soft ──
out = asyncio.run(pg.enrich_research_with_deep_dives(
    "BASE",
    schema=schema_admin,
    layout_archetype="admin_panel",
    domain="x",
    brand_name="y",
    websocket=FakeWS(),
    gemini_key="",
))
check("missing gemini_key: blob unchanged", out == "BASE")

# ── Per-unit Gemini failure is fail-soft ──
async def _fake_gemini_one_fails(prompt, gemini_url, is_pro, websocket, label, max_tokens=10000):
    _call_log.append((label, prompt[:80]))
    if "Customer" in prompt:
        raise RuntimeError("simulated Gemini 500")
    return "OK_DEEP\nSome paragraph.\n"


pg._call_gemini_single = _fake_gemini_one_fails
ws = FakeWS()
_call_log.clear()
out = asyncio.run(pg.enrich_research_with_deep_dives(
    "BASE",
    schema=schema_admin,
    layout_archetype="admin_panel",
    domain="x",
    brand_name="y",
    websocket=ws,
    gemini_key="fake",
))
check("partial failure: 4 calls attempted", len(_call_log) == 4)
check("partial failure: surviving 3 blocks present", out.count("===ENTITY_DEEP::") == 3, f"counted {out.count('===ENTITY_DEEP::')}")
check("partial failure: failed entity has NO block", "===ENTITY_DEEP::Customer===" not in out)

# ── Body sanitization: triple-= in response body must be defanged ──
async def _fake_gemini_dangerous(prompt, gemini_url, is_pro, websocket, label, max_tokens=10000):
    return "Some text ===EVIL_HEADER=== that could break section parsing."


pg._call_gemini_single = _fake_gemini_dangerous
out = asyncio.run(pg.enrich_research_with_deep_dives(
    "BASE",
    schema={"entities": [{"name": "X", "fields": []}], "pages": []},
    layout_archetype="admin_panel",
    domain="x",
    brand_name="y",
    websocket=FakeWS(),
    gemini_key="fake",
))
# Block header (prefix ===) must exist exactly once; body's === must be neutralized to ==
check("sanitization: only one ENTITY_DEEP header", out.count("===ENTITY_DEEP::X===") == 1)
check("sanitization: no stray === in body", "===EVIL_HEADER===" not in out)
check("sanitization: body's === downgraded to ==", "==EVIL_HEADER==" in out)


# ╔══════════════════════════════════════════════════════════╗
# ║ Phase A — _emit_file_writes batch_index plumbing         ║
# ╚══════════════════════════════════════════════════════════╝
section("Phase A — _emit_file_writes batch_index")

import inspect

sig = inspect.signature(pg._emit_file_writes)
check("_emit_file_writes accepts batch_index kwarg", "batch_index" in sig.parameters)

# Build a tiny temp workspace and emit one file with batch_index
import tempfile, json

ws = FakeWS()
with tempfile.TemporaryDirectory() as tmp:
    fpath = os.path.join(tmp, "src", "App.jsx")
    os.makedirs(os.path.dirname(fpath), exist_ok=True)
    with open(fpath, "w") as f:
        f.write("export default function App(){return null}")
    asyncio.run(pg._emit_file_writes(
        ws,
        ["src/App.jsx"],
        action="write",
        workspace_dir=tmp,
        phase="phase2",
        phase_elapsed_ms=123,
        batch_index=2,
    ))

evt = next((e for e in ws.events if e[0] == ""), None) or (ws.events[0] if ws.events else None)
# Above doesn't capture the batch_index because FakeWS only stores type+message. Re-test with a richer fake:


class RichFakeWS:
    def __init__(self):
        self.payloads: list[dict] = []

    async def send_json(self, payload):
        self.payloads.append(payload)


rws = RichFakeWS()
with tempfile.TemporaryDirectory() as tmp:
    fpath = os.path.join(tmp, "x.tsx")
    with open(fpath, "w") as f:
        f.write("// hi")
    asyncio.run(pg._emit_file_writes(
        rws, ["x.tsx"],
        action="write", workspace_dir=tmp,
        phase="phase2", phase_elapsed_ms=42, batch_index=3,
    ))

p = rws.payloads[0] if rws.payloads else {}
check("emitted file_write_event", p.get("type") == "file_write_event")
check("includes filename", p.get("filename") == "x.tsx")
check("includes phase", p.get("phase") == "phase2")
check("includes phase_elapsed_ms", p.get("phase_elapsed_ms") == 42)
check("includes batch_index when provided", p.get("batch_index") == 3)
check("includes content for small file", p.get("content") == "// hi")

# batch_index None should NOT include the key (avoids polluting non-parallel events)
rws = RichFakeWS()
with tempfile.TemporaryDirectory() as tmp:
    fpath = os.path.join(tmp, "y.tsx")
    with open(fpath, "w") as f:
        f.write("// y")
    asyncio.run(pg._emit_file_writes(
        rws, ["y.tsx"], workspace_dir=tmp, phase="phase1",
    ))
p = rws.payloads[0]
check("batch_index omitted when None", "batch_index" not in p)


# ╔══════════════════════════════════════════════════════════╗
# ║ Phase A — _run_phase2_parallel_batches semantics         ║
# ╚══════════════════════════════════════════════════════════╝
section("Phase A — _run_phase2_parallel_batches")

# Patch write_files_from_json so the helper writes files under the temp workspace
from app.services import project_writer as _pw
_orig_writer = _pw.write_files_from_json


def _fake_writer(payload, workspace_path):
    """Minimal version that writes content to disk and returns the list of paths.

    Note: matches the real signature `(json_response, workspace_path)`.
    """
    written: list[str] = []
    files = (payload or {}).get("files") or []
    for f in files:
        rel = f.get("path") or f.get("filename")
        content = f.get("content", "")
        if not rel:
            continue
        full = os.path.join(workspace_path, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as h:
            h.write(content)
        written.append(rel)
    return written


_pw.write_files_from_json = _fake_writer
# also rebind the local symbol inside project_generator that was star-imported
pg.write_files_from_json = _fake_writer


async def _runner_two_batches_unique(idx, payload, total):
    # Each batch returns 2 unique files
    return {"files": [
        {"path": f"src/b{idx}_a.jsx", "content": f"// batch{idx}-a"},
        {"path": f"src/b{idx}_b.jsx", "content": f"// batch{idx}-b"},
    ]}


with tempfile.TemporaryDirectory() as tmp:
    written, failed, total = asyncio.run(pg._run_phase2_parallel_batches(
        websocket=RichFakeWS(),
        workspace_path=tmp,
        batches=["b1", "b2", "b3"],
        batch_runner=_runner_two_batches_unique,
    ))
    check("3 batches all succeed → failed=0", failed == 0)
    check("3 batches → total=3", total == 3)
    check("3×2 unique files = 6 written", len(written) == 6, f"got {written}")
    check("all batch dirs exist on disk", all(os.path.exists(os.path.join(tmp, p)) for p in written))


# Dedupe: two batches return the SAME path — first-batch-wins
async def _runner_with_dup(idx, payload, total):
    return {"files": [
        {"path": "src/shared.jsx", "content": f"// from-batch-{idx}"},
        {"path": f"src/unique_{idx}.jsx", "content": f"// uniq-{idx}"},
    ]}


with tempfile.TemporaryDirectory() as tmp:
    written, failed, total = asyncio.run(pg._run_phase2_parallel_batches(
        websocket=RichFakeWS(),
        workspace_path=tmp,
        batches=["b1", "b2"],
        batch_runner=_runner_with_dup,
    ))
    check("dedupe: shared path appears at most once in returned list", written.count("src/shared.jsx") <= 1, f"written={written}")
    # Two unique batch files should still be there
    check("dedupe: both unique files present", "src/unique_0.jsx" in written and "src/unique_1.jsx" in written)


# Failure + retry path
attempts: dict[int, int] = {}


async def _runner_one_fails_then_retries(idx, payload, total):
    attempts[idx] = attempts.get(idx, 0) + 1
    if idx == 1 and attempts[idx] == 1:
        raise RuntimeError("simulated failure")
    return {"files": [
        {"path": f"src/r{idx}.jsx", "content": f"// r{idx}"},
    ]}


with tempfile.TemporaryDirectory() as tmp:
    written, failed, total = asyncio.run(pg._run_phase2_parallel_batches(
        websocket=RichFakeWS(),
        workspace_path=tmp,
        batches=["b0", "b1", "b2"],
        batch_runner=_runner_one_fails_then_retries,
    ))
    check("retry: batch 1 attempted twice", attempts.get(1, 0) == 2)
    check("retry: ultimately 3 files written", len(written) == 3, f"written={written}")
    check("retry: failed_batches==0 after successful retry", failed == 0)


# Total failure (retry also fails)
async def _runner_always_fails(idx, payload, total):
    if idx == 0:
        raise RuntimeError("nope")
    return {"files": [{"path": f"src/k{idx}.jsx", "content": "ok"}]}


with tempfile.TemporaryDirectory() as tmp:
    written, failed, total = asyncio.run(pg._run_phase2_parallel_batches(
        websocket=RichFakeWS(),
        workspace_path=tmp,
        batches=["a", "b"],
        batch_runner=_runner_always_fails,
    ))
    check("permanent failure: failed_batches==1", failed == 1)
    check("permanent failure: surviving batch's files written", len(written) == 1, f"written={written}")


# Restore the writer
_pw.write_files_from_json = _orig_writer
pg.write_files_from_json = _orig_writer


# ╔══════════════════════════════════════════════════════════╗
# ║ Phase A static — schema _index stamping                  ║
# ╚══════════════════════════════════════════════════════════╝
section("Phase A — project_schema._validate_schema stamps _index")

ps = importlib.import_module("app.services.project_schema")
# Two demos: one landing-style (sections only) for sections _index, one
# multi-page-with-entities (so _validate_schema doesn't strip pages) for pages _index.
demo_landing = {
    "sections": [{"type": "hero"}, {"type": "features"}, {"type": "cta"}],
    "pages": [],
    "brand": {"name": "X"},
    "theme": {}, "navigation": [], "entities": [], "tech": {},
}
demo_admin = {
    "sections": [],
    "pages": [
        {"path": "/users", "title": "Users", "component": "UsersListPage", "type": "crud_list", "entity": "users"},
        {"path": "/users/new", "title": "New", "component": "UsersFormPage", "type": "crud_form", "entity": "users"},
    ],
    "brand": {"name": "X"},
    "theme": {}, "navigation": [],
    "entities": [{"name": "User", "slug": "users", "fields": [{"name": "id"}], "mockData": []}],
    "tech": {},
}
try:
    ps._validate_schema(demo_landing)
    sec_idx = [s.get("_index") for s in demo_landing["sections"]]
    check("sections stamped with sequential _index", sec_idx == [0, 1, 2], f"got {sec_idx}")
except Exception as exc:
    check("sections _index stamping (landing demo)", False, f"raised: {exc}")

try:
    ps._validate_schema(demo_admin, layout_archetype="admin_dashboard")
    page_idx = [p.get("_index") for p in demo_admin["pages"]]
    # The validator can also auto-add CRUD pages for entities w/o existing pages,
    # so just assert all pages have a numeric _index in declaration order.
    check(
        "pages stamped with sequential _index",
        len(page_idx) >= 2 and page_idx == list(range(len(page_idx))),
        f"got {page_idx}",
    )
except Exception as exc:
    check("pages _index stamping (admin demo)", False, f"raised: {exc}")


# ── Final report ───────────────────────────────────────────
print()
print("=" * 60)
print(f"PASS: {PASS}    FAIL: {FAIL}")
if FAIL:
    print()
    print("Failures:")
    for f in FAILURES:
        print(f"  - {f}")
    sys.exit(1)
print("ALL GREEN")
