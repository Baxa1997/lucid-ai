"""In-memory cache for expensive pipeline stages.

Caches Stage 2 (research) and Stage 3 (visual_dna + voice_signature)
outputs keyed by `(project_id, stage, hash_of_inputs)`. When the user
iterates on later stages (plan, generation) we skip the ~$0.10–0.20
worth of Gemini calls these stages cost.

Design rules (per spec):
  • In-memory only. Redis is a later migration path; this stays simple.
  • TTL on every entry — default 30 min — so stale data ages out.
  • Cache is keyed by project_id so two projects can't see each other's
    research even if their prompts hash identically.
  • Stage 4 (plan) and Stage 6 (page generation) are NEVER cached:
    plan must re-run because the user might iterate on structure;
    generation must always be fresh.
  • Hash is computed over the cache-relevant inputs (prompt, clarity,
    purpose) so changing any of them invalidates automatically.

Module-level singleton: `pipeline_cache`. Import and use directly.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections import defaultdict
from typing import Any

logger = logging.getLogger(__name__)

# Rough cost-per-stage estimate used only for logging "savings" messages.
# Source: typical Gemini Flash+Pro mix for a 4-call research stage + a
# 2-call extract stage on May 2026 pricing.
_STAGE_COST_USD: dict[str, float] = {
    "research":         0.12,   # 4 grounded Pro calls + 4 design research calls
    "visual_dna":       0.05,   # one Pro extract call
    "voice_signature":  0.02,   # one Flash call (folded into signals today)
    "signals":          0.07,   # combined visual_dna + voice when fetched together
}


class PipelineCache:
    """Per-project, TTL'd, in-memory cache."""

    def __init__(self, ttl_seconds: int = 1800):
        self._cache: dict[str, Any] = {}
        self._timestamps: dict[str, float] = {}
        self._lock = threading.Lock()
        self.ttl = ttl_seconds
        # Stats
        self._hits: int = 0
        self._misses: int = 0
        self._hits_by_stage: dict[str, int] = defaultdict(int)
        self._misses_by_stage: dict[str, int] = defaultdict(int)
        self._saved_usd: float = 0.0

    # ── Internals ────────────────────────────────────────────────────

    def _make_key(self, project_id: str, stage: str, content_hash: str) -> str:
        return f"{project_id}:{stage}:{content_hash}"

    def _hash_content(self, *args: Any) -> str:
        """Stable 16-char MD5 hash of the serialized inputs.

        Uses ``sort_keys=True`` and ``default=str`` so dicts hash the
        same regardless of insertion order and any non-JSON-serializable
        type (e.g. datetime, Path) falls back to its string form.
        """
        try:
            content = json.dumps(args, sort_keys=True, default=str)
        except TypeError:
            # Defensive — should not hit because of default=str
            content = repr(args)
        return hashlib.md5(content.encode("utf-8")).hexdigest()[:16]

    # ── Public API ───────────────────────────────────────────────────

    def get(self, project_id: str, stage: str, *cache_inputs: Any) -> Any | None:
        """Return cached value or None on miss / expiry.

        Expired entries are evicted on access; the cache never returns
        stale data.
        """
        key = self._make_key(project_id, stage, self._hash_content(*cache_inputs))
        with self._lock:
            if key not in self._cache:
                self._misses += 1
                self._misses_by_stage[stage] += 1
                logger.debug("pipeline_cache MISS  stage=%s project=%s", stage, project_id)
                return None
            if time.time() - self._timestamps[key] > self.ttl:
                del self._cache[key]
                del self._timestamps[key]
                self._misses += 1
                self._misses_by_stage[stage] += 1
                logger.debug("pipeline_cache EXPIRED stage=%s project=%s", stage, project_id)
                return None
            self._hits += 1
            self._hits_by_stage[stage] += 1
            saved = _STAGE_COST_USD.get(stage, 0.0)
            self._saved_usd += saved
            logger.info(
                "pipeline_cache HIT   stage=%s project=%s — saved ~$%.3f (total saved: $%.2f)",
                stage, project_id, saved, self._saved_usd,
            )
            return self._cache[key]

    def set(self, project_id: str, stage: str, value: Any, *cache_inputs: Any) -> None:
        key = self._make_key(project_id, stage, self._hash_content(*cache_inputs))
        with self._lock:
            self._cache[key] = value
            self._timestamps[key] = time.time()
        logger.info(
            "pipeline_cache SET   stage=%s project=%s key=%s",
            stage, project_id, key.split(":", 2)[-1],
        )

    def invalidate(self, project_id: str) -> int:
        """Drop every entry for this project. Returns number of evictions."""
        prefix = f"{project_id}:"
        with self._lock:
            keys_to_delete = [k for k in self._cache if k.startswith(prefix)]
            for k in keys_to_delete:
                del self._cache[k]
                del self._timestamps[k]
        if keys_to_delete:
            logger.info(
                "pipeline_cache INVALIDATE project=%s — dropped %d entry/entries",
                project_id, len(keys_to_delete),
            )
        return len(keys_to_delete)

    def clear(self) -> None:
        """Wipe everything. Test-only helper."""
        with self._lock:
            self._cache.clear()
            self._timestamps.clear()
            self._hits = 0
            self._misses = 0
            self._hits_by_stage.clear()
            self._misses_by_stage.clear()
            self._saved_usd = 0.0

    def stats(self) -> dict[str, Any]:
        """Snapshot of hit / miss counters and current size."""
        with self._lock:
            total = self._hits + self._misses
            hit_rate = (self._hits / total) if total else 0.0
            return {
                "size":            len(self._cache),
                "hits":            self._hits,
                "misses":          self._misses,
                "hit_rate":        round(hit_rate, 3),
                "hits_by_stage":   dict(self._hits_by_stage),
                "misses_by_stage": dict(self._misses_by_stage),
                "saved_usd":       round(self._saved_usd, 3),
                "ttl_seconds":     self.ttl,
            }


# Module-level singleton — import this from anywhere.
pipeline_cache = PipelineCache()
