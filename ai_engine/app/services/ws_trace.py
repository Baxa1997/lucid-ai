"""ws_trace.py — capture WebSocket message streams as JSONL traces.

Enabled by the ``WS_TRACE_FILE`` env var. When set, every WebSocket message
(outbound ``send_json``, inbound ``receive_json`` / ``receive_text``) is
appended to the trace file alongside its direction and a timestamp.

These traces become the regression corpus for the status/error refactor:
after a refactor lands, the replay test feeds the trace's INPUTS through
the new code and asserts the OUTPUTS match the trace's recorded outputs.

Design rules:
  • Fail-soft. Trace failures NEVER raise into the WS handler.
  • Wrapper preserves the WebSocket API surface — anything that worked
    on the raw object works on the wrapped one.
  • No-op when ``WS_TRACE_FILE`` is unset — production paths see zero overhead.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()


def _trace_path() -> str | None:
    raw = (os.environ.get("WS_TRACE_FILE") or "").strip()
    return raw or None


def _write(record: dict) -> None:
    path = _trace_path()
    if not path:
        return
    try:
        line = json.dumps(record, default=str, ensure_ascii=False)
        with _LOCK:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line)
                fh.write("\n")
    except Exception as exc:
        logger.debug("ws_trace write dropped: %s", exc)


class TracingWebSocket:
    """Transparent delegating wrapper around starlette's WebSocket.

    Only the methods the codebase actually calls are intercepted; everything
    else is forwarded via ``__getattr__`` so the wrapper is a drop-in.
    """

    __slots__ = ("_ws", "_project_id", "_session_id")

    def __init__(self, ws: Any, *, project_id: str = "", session_id: str = "") -> None:
        self._ws = ws
        self._project_id = project_id
        self._session_id = session_id

    # ── Intercept outbound ──────────────────────────────────────────
    async def send_json(self, data: Any, *args: Any, **kwargs: Any) -> Any:
        _write({
            "ts": time.time(),
            "dir": "out",
            "kind": "send_json",
            "project_id": self._project_id,
            "session_id": self._session_id,
            "payload": data,
        })
        return await self._ws.send_json(data, *args, **kwargs)

    async def send_text(self, data: str, *args: Any, **kwargs: Any) -> Any:
        _write({
            "ts": time.time(),
            "dir": "out",
            "kind": "send_text",
            "project_id": self._project_id,
            "session_id": self._session_id,
            "payload": data,
        })
        return await self._ws.send_text(data, *args, **kwargs)

    async def send_bytes(self, data: bytes, *args: Any, **kwargs: Any) -> Any:
        _write({
            "ts": time.time(),
            "dir": "out",
            "kind": "send_bytes",
            "project_id": self._project_id,
            "session_id": self._session_id,
            "size": len(data),
        })
        return await self._ws.send_bytes(data, *args, **kwargs)

    # ── Intercept inbound ───────────────────────────────────────────
    async def receive_json(self, *args: Any, **kwargs: Any) -> Any:
        data = await self._ws.receive_json(*args, **kwargs)
        _write({
            "ts": time.time(),
            "dir": "in",
            "kind": "receive_json",
            "project_id": self._project_id,
            "session_id": self._session_id,
            "payload": data,
        })
        return data

    async def receive_text(self, *args: Any, **kwargs: Any) -> Any:
        data = await self._ws.receive_text(*args, **kwargs)
        _write({
            "ts": time.time(),
            "dir": "in",
            "kind": "receive_text",
            "project_id": self._project_id,
            "session_id": self._session_id,
            "payload": data,
        })
        return data

    # ── Delegate everything else ────────────────────────────────────
    def __getattr__(self, name: str) -> Any:
        # Triggered only when an attribute is NOT found on the wrapper itself
        # (i.e. not in __slots__ and not one of the methods above).
        return getattr(self._ws, name)

    # Some callers introspect via `hasattr` / `isinstance` — preserve those.
    def __repr__(self) -> str:
        return f"<TracingWebSocket project={self._project_id} session={self._session_id}>"


def maybe_wrap(ws: Any, *, project_id: str = "", session_id: str = "") -> Any:
    """Return a tracing wrapper if WS_TRACE_FILE is set, else the raw ws."""
    if _trace_path() is None:
        return ws
    return TracingWebSocket(ws, project_id=project_id, session_id=session_id)


def update_trace_ids(ws: Any, *, project_id: str | None = None, session_id: str | None = None) -> None:
    """Backfill IDs onto an already-wrapped ws once they're known.

    The ws handshake establishes the conversation/session IDs AFTER the
    socket is accepted. Without this, the first ~5 events have empty IDs.
    Safe no-op when ``ws`` is the raw WebSocket (wrapping disabled).
    """
    if not isinstance(ws, TracingWebSocket):
        return
    if project_id is not None:
        object.__setattr__(ws, "_project_id", project_id)
    if session_id is not None:
        object.__setattr__(ws, "_session_id", session_id)
