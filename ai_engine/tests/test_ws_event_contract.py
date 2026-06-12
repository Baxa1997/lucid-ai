"""BE↔FE WebSocket event contract test.

The backend streams typed events ({"type": "..."}) over the agent
WebSocket; the frontend dispatches on `msg.type === '<name>'` in
frontend/src/hooks/agentMessageHandlers.js (plus `_cursor` in
agentWSManager.js). There is no shared schema, so a new backend emit
without a frontend handler silently falls through to the raw
`pushLog(JSON.stringify(msg))` fallback — the user sees a JSON blob in
the log panel instead of a rendered message, and nobody notices in
review.

This test statically extracts both sides and fails when they drift:

  * every backend-emitted literal event type must either be handled by
    the frontend or listed in ALLOWED_UNHANDLED with a reason;
  * ALLOWED_UNHANDLED entries must stay accurate — if the frontend
    gains a handler, or the backend stops emitting the type, the stale
    entry fails the test so the list cannot rot.

Limits (by design):
  * Only literal types are extracted. Emits like {"type": msg_type}
    are invisible to this test — the frontend-handled set is therefore
    a superset of what we can verify in reverse, and the FE→BE
    direction is intentionally NOT asserted.
  * Extraction anchors on real send call-sites (websocket.send_json,
    _ws_send, _safe_send, _send, …) so payload-internal "type" keys
    (section types, JSON-schema field types) are not picked up.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

AI_ENGINE_ROOT = Path(__file__).resolve().parents[1]
BACKEND_APP = AI_ENGINE_ROOT / "app"
FRONTEND_SRC = AI_ENGINE_ROOT.parent / "frontend" / "src"

# Frontend files that dispatch on WS message types. Deliberately narrow:
# scanning all of src/ would pick up unrelated `.type ===` checks (Figma
# node kinds, form field types) and weaken the assertion.
FRONTEND_HANDLER_PATHS = [
    FRONTEND_SRC / "hooks",                      # agentMessageHandlers.js et al.
    FRONTEND_SRC / "lib" / "agentWSManager.js",  # handles `_cursor`
]

# Backend event types the frontend intentionally does not handle.
# Each entry needs a reason. An entry here still reaches the browser and
# lands in the raw-JSON pushLog fallback — add a real handler instead if
# the event carries user-facing content.
ALLOWED_UNHANDLED: dict[str, str] = {
    "build": "step5b build-verify telemetry; build outcome is surfaced via build.result",
    "content_audit": "website-pipeline content QA telemetry; chat warning carries the user-facing text",
    "generation_audit": "artifact-coverage telemetry (additive); chat warning names the missing artifacts",
    "info": "informational pipeline notes; log-panel fallback rendering is acceptable",
    "phase2_batch_complete": "legacy project_generator batch telemetry",
    "phase2_batch_started": "legacy project_generator batch telemetry",
    "quality_score": "legacy project_generator quality telemetry; quality_report is the handled event",
    "stop_ack": "protocol ack for the stop command; FE updates state on its own when it sends stop",
}

# ── Extraction ────────────────────────────────────────────────────────

# Send call-sites: direct websocket sends + the wrapper helpers used by
# the pipelines. `(?<![\w.])_send\(` avoids matching e.g. `proc.send(`.
_SEND_CALL = re.compile(
    r"(?:\bwebsocket|\bws|\bout_ws|\b_ws_out|self\._ws|self\.websocket)\.send_json\s*\("
    r"|\b_ws_send\s*\("
    r"|\b_safe_send\s*\("
    r"|(?<![\w.])_send\s*\("
    r"|self\._send\s*\("
)
_TYPE_LITERAL = re.compile(
    r"""["']type["']\s*:\s*["']([A-Za-z_][A-Za-z0-9_.]*)["']"""
)
_FE_HANDLED = re.compile(
    r"""\.type\s*===?\s*['"]([A-Za-z_][A-Za-z0-9_.]*)['"]"""
)


def _call_argument_text(source: str, open_paren: int, limit: int = 4000) -> str:
    """Return the text of a call's arguments by balancing parentheses."""
    depth = 0
    end = min(len(source), open_paren + limit)
    for i in range(open_paren, end):
        if source[i] == "(":
            depth += 1
        elif source[i] == ")":
            depth -= 1
            if depth == 0:
                return source[open_paren:i]
    return source[open_paren:end]


def backend_emitted_types() -> dict[str, set[str]]:
    """Map of event type -> set of backend files that emit it (literals only)."""
    emitted: dict[str, set[str]] = {}
    for path in sorted(BACKEND_APP.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        for match in _SEND_CALL.finditer(source):
            open_paren = source.index("(", match.start())
            args = _call_argument_text(source, open_paren)
            type_match = _TYPE_LITERAL.search(args)
            if type_match:
                rel = str(path.relative_to(AI_ENGINE_ROOT))
                emitted.setdefault(type_match.group(1), set()).add(rel)
    return emitted


def frontend_handled_types() -> set[str]:
    handled: set[str] = set()
    for root in FRONTEND_HANDLER_PATHS:
        files = root.rglob("*.js") if root.is_dir() else [root]
        for path in files:
            if ".test." in path.name:
                continue
            for match in _FE_HANDLED.finditer(path.read_text(encoding="utf-8")):
                handled.add(match.group(1))
    return handled


# ── Fixtures ──────────────────────────────────────────────────────────

requires_frontend = pytest.mark.skipif(
    not FRONTEND_SRC.is_dir(),
    reason="frontend/src not present (backend-only checkout)",
)


@pytest.fixture(scope="module")
def emitted() -> dict[str, set[str]]:
    return backend_emitted_types()


@pytest.fixture(scope="module")
def handled() -> set[str]:
    return frontend_handled_types()


# ── Tests ─────────────────────────────────────────────────────────────


def test_extractor_finds_backend_events(emitted):
    """Guard against regex rot making the contract test vacuous.

    61 literal types existed when this was written; a refactor of the
    send helpers that drops extraction below the floor must update the
    _SEND_CALL pattern, not weaken this test.
    """
    assert len(emitted) >= 40, (
        f"Backend extractor only found {len(emitted)} event types — "
        "the send-call pattern in _SEND_CALL is probably out of date "
        "with how events are emitted."
    )


@requires_frontend
def test_extractor_finds_frontend_handlers(handled):
    assert len(handled) >= 40, (
        f"Frontend extractor only found {len(handled)} handled types — "
        "agentMessageHandlers.js may have moved or changed its dispatch "
        "pattern; update FRONTEND_HANDLER_PATHS / _FE_HANDLED."
    )


@requires_frontend
def test_every_backend_event_is_handled_or_allowlisted(emitted, handled):
    unhandled = {
        event: sorted(files)
        for event, files in emitted.items()
        if event not in handled and event not in ALLOWED_UNHANDLED
    }
    assert not unhandled, (
        "Backend emits WS event types the frontend does not handle:\n"
        + "\n".join(
            f"  {event!r}  (emitted from {', '.join(files)})"
            for event, files in sorted(unhandled.items())
        )
        + "\nEither add a `msg.type === '<name>'` handler in "
        "frontend/src/hooks/agentMessageHandlers.js, or add the type to "
        "ALLOWED_UNHANDLED in this test with a reason."
    )


@requires_frontend
def test_allowlist_entries_are_not_stale(emitted, handled):
    now_handled = sorted(t for t in ALLOWED_UNHANDLED if t in handled)
    assert not now_handled, (
        f"ALLOWED_UNHANDLED entries now have frontend handlers: {now_handled}. "
        "Remove them from the allowlist."
    )

    no_longer_emitted = sorted(t for t in ALLOWED_UNHANDLED if t not in emitted)
    assert not no_longer_emitted, (
        f"ALLOWED_UNHANDLED entries are no longer emitted by the backend: "
        f"{no_longer_emitted}. Remove them from the allowlist."
    )
