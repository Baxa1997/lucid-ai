"""telemetry.py — append-only event log for generation pipeline diagnostics.

Why this exists:
  After enough generations we want data-driven answers to questions like
  "which post-gen fixers actually fire" and "what % of image slots get
  bound" — so we can refactor / shrink the codegen prompt without guessing.
  Each call to :func:`emit` appends one JSON line to a local JSONL file
  (default ``/tmp/lucid_telemetry.jsonl``, override with ``TELEMETRY_FILE``).

Design rules:
  • Fail-soft: telemetry never blocks or raises into generation code.
    If the file is unwritable, the event is dropped silently.
  • One call site = one event. No buffering, no batching — simplicity over
    throughput. The volume is tiny (~50-200 events per generation).
  • JSONL keeps the file ``tail -f``-friendly and ``jq``-queryable.
  • Schema is loose: every event has ``ts`` + ``event`` + arbitrary fields.

Summarize with ``ai_engine/scripts/telemetry_summary.py``.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_PATH = "/tmp/lucid_telemetry.jsonl"
_LOCK = threading.Lock()


def _path() -> str:
    return os.environ.get("TELEMETRY_FILE", _DEFAULT_PATH).strip() or _DEFAULT_PATH


def emit(event: str, **fields: Any) -> None:
    """Append one event to the telemetry JSONL log. Never raises.

    ``event`` is a dotted name (``pipeline.start``, ``fixer.fire``, etc.) so
    consumers can group by prefix. ``fields`` is anything JSON-serializable —
    keep payloads small (under ~1KB per event) so the log stays scannable.
    """
    try:
        record = {"ts": time.time(), "event": event, **fields}
        line = json.dumps(record, default=str, ensure_ascii=False)
        path = _path()
        parent = os.path.dirname(path)
        with _LOCK:
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line)
                fh.write("\n")
    except Exception as exc:
        # Telemetry must never break generation — log + drop.
        logger.debug("telemetry.emit dropped event=%s err=%s", event, exc)
