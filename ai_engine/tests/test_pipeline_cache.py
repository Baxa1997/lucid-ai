"""Tests for pipeline_cache (Stage 2 + 3 caching).

Five cases per spec:
  1. Set + get returns same value
  2. TTL expiry works
  3. Hash changes when inputs change (cache miss with different input)
  4. Hash stable for same inputs (cache hit on regen)
  5. Invalidate clears project's keys only (not other projects)

Plus stats sanity check.

Run inside the ai_engine container:
    docker exec -it lucid-ai-ai_engine-1 python /app/tests/test_pipeline_cache.py
"""
from __future__ import annotations

import sys
import time
import traceback

sys.path.insert(0, "/app")

from app.services.pipeline_cache import PipelineCache, pipeline_cache


# ── 1. Set + get returns same value ────────────────────────────────

def test_set_get_roundtrip():
    c = PipelineCache(ttl_seconds=60)
    payload = {"competitive": {"text": "real research dump", "sources": 7}}
    c.set("proj-A", "research", payload, "italian cafe prompt", {}, {"primary_purpose": "brand_awareness"})
    got = c.get("proj-A", "research", "italian cafe prompt", {}, {"primary_purpose": "brand_awareness"})
    assert got == payload, f"expected roundtrip, got {got!r}"
    assert got is c._cache[next(iter(c._cache))], "should return same object, not copy"
    print(f"  ✓ set + get roundtrip")


# ── 2. TTL expiry works ────────────────────────────────────────────

def test_ttl_expiry():
    c = PipelineCache(ttl_seconds=1)  # 1-second TTL
    c.set("proj-A", "research", "v1", "prompt")
    assert c.get("proj-A", "research", "prompt") == "v1", "should hit before TTL"
    time.sleep(1.1)
    assert c.get("proj-A", "research", "prompt") is None, "should miss after TTL"
    # And the key should be evicted from internal storage
    assert not any(k.startswith("proj-A:research:") for k in c._cache), "expired entry should be evicted"
    print(f"  ✓ TTL expiry evicts stale entries")


# ── 3. Different inputs → different hash → miss ────────────────────

def test_hash_differs_for_different_inputs():
    c = PipelineCache(ttl_seconds=60)
    c.set("proj-A", "research", "v1", "prompt A", {"location": "italy"})

    # Same project + stage but different prompt → MISS
    assert c.get("proj-A", "research", "prompt B", {"location": "italy"}) is None, \
        "different prompt should miss"

    # Same prompt + different clarity → MISS
    assert c.get("proj-A", "research", "prompt A", {"location": "japan"}) is None, \
        "different clarity should miss"

    # Same prompt + same clarity → HIT
    assert c.get("proj-A", "research", "prompt A", {"location": "italy"}) == "v1", \
        "identical inputs should hit"

    print(f"  ✓ hash sensitive to all input args")


# ── 4. Same inputs → stable hash → hit ─────────────────────────────

def test_hash_stable_for_same_inputs():
    c = PipelineCache(ttl_seconds=60)
    # Build the same dict in two different insertion orders — should hash the same
    inputs_v1 = ("prompt", {"a": 1, "b": 2}, {"primary_purpose": "recruitment"})
    inputs_v2 = ("prompt", {"b": 2, "a": 1}, {"primary_purpose": "recruitment"})  # reordered

    c.set("proj-A", "signals", "the_same_value", *inputs_v1)
    got = c.get("proj-A", "signals", *inputs_v2)
    assert got == "the_same_value", f"reordered dict should still hit, got {got!r}"

    # Re-asking the SAME inputs N times → all hits, no extra stored entries
    for _ in range(5):
        assert c.get("proj-A", "signals", *inputs_v1) == "the_same_value"

    keys_for_signals = [k for k in c._cache if k.startswith("proj-A:signals:")]
    assert len(keys_for_signals) == 1, f"should have exactly 1 signals key, got {keys_for_signals}"
    print(f"  ✓ hash stable across dict insertion order + repeated calls")


# ── 5. Invalidate scoped to one project ────────────────────────────

def test_invalidate_scoped_to_project():
    c = PipelineCache(ttl_seconds=60)
    c.set("proj-A", "research", "A1", "promptA")
    c.set("proj-A", "signals",  "A2", "promptA", "domain")
    c.set("proj-B", "research", "B1", "promptB")
    c.set("proj-C", "research", "C1", "promptC")

    evicted = c.invalidate("proj-A")
    assert evicted == 2, f"expected 2 evictions for proj-A, got {evicted}"

    # proj-A should now miss
    assert c.get("proj-A", "research", "promptA") is None
    assert c.get("proj-A", "signals",  "promptA", "domain") is None

    # proj-B and proj-C must still hit
    assert c.get("proj-B", "research", "promptB") == "B1"
    assert c.get("proj-C", "research", "promptC") == "C1"

    # Invalidating a project with no keys → 0 evictions, no error
    assert c.invalidate("proj-nonexistent") == 0

    print(f"  ✓ invalidate scoped to project (only proj-A cleared)")


# ── 6. Stats sanity check ──────────────────────────────────────────

def test_stats_track_hits_and_misses():
    c = PipelineCache(ttl_seconds=60)
    c.set("proj-A", "research", "v1", "p1")
    # 1 hit
    c.get("proj-A", "research", "p1")
    # 2 misses
    c.get("proj-A", "research", "missing-prompt")
    c.get("proj-A", "signals",  "p1")

    s = c.stats()
    assert s["hits"] == 1, f"expected 1 hit, got {s['hits']}"
    assert s["misses"] == 2, f"expected 2 misses, got {s['misses']}"
    assert 0.0 < s["hit_rate"] < 1.0
    assert s["size"] == 1
    assert s["hits_by_stage"].get("research") == 1
    assert s["saved_usd"] > 0
    print(f"  ✓ stats: hits={s['hits']} misses={s['misses']} hit_rate={s['hit_rate']} saved=${s['saved_usd']}")


# ── 7. Module-level singleton smoke ────────────────────────────────

def test_module_singleton_exists():
    pipeline_cache.clear()
    pipeline_cache.set("smoke", "research", "value", "key-input")
    assert pipeline_cache.get("smoke", "research", "key-input") == "value"
    pipeline_cache.invalidate("smoke")
    assert pipeline_cache.get("smoke", "research", "key-input") is None
    print(f"  ✓ module-level singleton pipeline_cache works")


def main():
    print("=" * 72)
    print("PIPELINE CACHE — TESTS")
    print("=" * 72)
    tests = [
        ("set+get roundtrip",              test_set_get_roundtrip),
        ("TTL expiry",                     test_ttl_expiry),
        ("hash differs for diff inputs",   test_hash_differs_for_different_inputs),
        ("hash stable for same inputs",    test_hash_stable_for_same_inputs),
        ("invalidate scoped to project",   test_invalidate_scoped_to_project),
        ("stats track hits/misses",        test_stats_track_hits_and_misses),
        ("module singleton smoke",         test_module_singleton_exists),
    ]
    passed = 0
    for name, fn in tests:
        print(f"\n── {name} ──")
        try:
            fn()
            passed += 1
        except AssertionError as e:
            print(f"  ✗ FAILED: {e}")
        except Exception as e:
            print(f"  ✗ THREW: {e}")
            traceback.print_exc()
    print(f"\n{'=' * 72}\nSCORE: {passed}/{len(tests)}\n{'=' * 72}")
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    sys.exit(main())
