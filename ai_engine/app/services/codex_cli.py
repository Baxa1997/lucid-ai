"""codex_cli.py — subprocess adapter for Codex non-interactive edits.

The product pipeline owns routing, validation, staging, and rollback behavior.
Codex is used here only as the code-edit executor for tasks that need an
agentic pass over a real workspace.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
import signal
from typing import Any, Optional

from fastapi import WebSocket

logger = logging.getLogger(__name__)

_STREAM_LIMIT_BYTES = 4 * 1024 * 1024
_MAX_SNAPSHOT_FILES = 5000
_MAX_EVENT_TEXT = 2000
_EXCLUDED_DIRS = {
    ".git",
    ".next",
    ".turbo",
    ".vercel",
    "node_modules",
    "dist",
    "build",
    "coverage",
    "__pycache__",
}
_TEXT_EXTS = {
    ".js", ".jsx", ".ts", ".tsx", ".css", ".scss", ".json", ".md", ".mdx",
    ".html", ".py", ".toml", ".yaml", ".yml", ".env", ".mjs", ".cjs",
}


class CodexCLIResult:
    """Outcome of a single Codex CLI session."""

    __slots__ = (
        "success",
        "result_text",
        "duration_ms",
        "files_written",
        "error",
        "returncode",
    )

    def __init__(self) -> None:
        self.success: bool = False
        self.result_text: str = ""
        self.duration_ms: int = 0
        self.files_written: list[str] = []
        self.error: Optional[str] = None
        self.returncode: Optional[int] = None


def _resolve_codex_binary() -> str:
    """Find the Codex CLI binary."""
    explicit = os.environ.get("CODEX_CLI_PATH", "").strip()
    if explicit and os.path.isfile(explicit):
        return explicit
    found = shutil.which("codex")
    if found:
        return found
    return "codex"


def codex_cli_available() -> bool:
    """Return whether a Codex CLI binary is available on this host."""
    explicit = os.environ.get("CODEX_CLI_PATH", "").strip()
    return bool((explicit and os.path.isfile(explicit)) or shutil.which("codex"))


async def _safe_send(websocket: Optional[WebSocket], payload: dict) -> None:
    if websocket is None:
        return
    try:
        await websocket.send_json(payload)
    except Exception:
        pass


def _snapshot_workspace(workspace_path: str) -> dict[str, tuple[int, int, str]]:
    """Return a lightweight content snapshot for generated source files."""
    snapshot: dict[str, tuple[int, int, str]] = {}
    root_real = os.path.realpath(workspace_path)
    for root, dirs, files in os.walk(workspace_path):
        dirs[:] = [d for d in dirs if d not in _EXCLUDED_DIRS]
        for fname in files:
            if len(snapshot) >= _MAX_SNAPSHOT_FILES:
                return snapshot
            ext = os.path.splitext(fname)[1].lower()
            if ext not in _TEXT_EXTS:
                continue
            full = os.path.realpath(os.path.join(root, fname))
            if not (full == root_real or full.startswith(root_real + os.sep)):
                continue
            try:
                st = os.stat(full)
                rel = os.path.relpath(full, root_real).replace(os.sep, "/")
                digest = ""
                if st.st_size <= 512 * 1024:
                    with open(full, "rb") as fh:
                        digest = hashlib.blake2b(fh.read(), digest_size=16).hexdigest()
                snapshot[rel] = (int(st.st_mtime_ns), int(st.st_size), digest)
            except OSError:
                continue
    return snapshot


def _changed_files_since(
    workspace_path: str,
    before: dict[str, tuple[int, int, str]],
) -> list[str]:
    after = _snapshot_workspace(workspace_path)
    changed: list[str] = []
    for rel, stamp in after.items():
        if before.get(rel) != stamp:
            changed.append(rel)
    for rel in before:
        if rel not in after:
            changed.append(rel)
    return sorted(dict.fromkeys(changed))


def _read_file_safely(
    workspace_path: str,
    rel_path: str,
    *,
    max_bytes: int = 256 * 1024,
) -> tuple[Optional[str], int, bool]:
    try:
        full = os.path.realpath(os.path.join(workspace_path, rel_path))
        root = os.path.realpath(workspace_path)
        if not (full == root or full.startswith(root + os.sep)):
            return None, 0, False
        if os.path.basename(rel_path).startswith(".env"):
            return None, os.path.getsize(full) if os.path.isfile(full) else 0, False
        if not os.path.isfile(full):
            return None, 0, False
        size = os.path.getsize(full)
        if size > max_bytes:
            return None, size, True
        with open(full, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read(), size, False
    except Exception:
        return None, 0, False


def _event_text(evt: dict[str, Any]) -> str:
    """Best-effort text extraction from Codex JSONL events."""
    for key in ("message", "text", "content", "delta", "output"):
        value = evt.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    item = evt.get("item")
    if isinstance(item, dict):
        return _event_text(item)
    return ""


def _is_final_event(evt: dict[str, Any]) -> bool:
    kind = str(evt.get("type") or evt.get("event") or "").lower()
    return kind in {"result", "final", "done", "agent_message"} or "final" in kind


async def run_codex_session(
    *,
    prompt: str,
    workspace_path: str,
    websocket: Optional[WebSocket] = None,
    api_key: str = "",
    user_id: Optional[str] = None,
    model: str = "",
    timeout_seconds: float = 600.0,
    phase_label: str = "step5_codex",
    sandbox_mode: str = "workspace-write",
) -> CodexCLIResult:
    """Run ``codex exec`` against ``workspace_path`` and stream coarse events.

    Codex CLI JSON event shapes may evolve, so file-write reporting is based on
    a before/after workspace snapshot instead of private event internals.
    """
    del user_id  # Reserved for future OpenAI usage metering integration.
    result = CodexCLIResult()

    if not prompt.strip():
        result.error = "empty_prompt"
        return result
    if not workspace_path or not os.path.isdir(workspace_path):
        result.error = "workspace_missing"
        return result

    bin_path = _resolve_codex_binary()
    if bin_path == "codex" and not shutil.which("codex"):
        result.error = "codex_cli_not_found"
        return result

    env = os.environ.copy()
    if api_key:
        env["OPENAI_API_KEY"] = api_key
    env.setdefault("CI", "true")

    argv = [
        bin_path,
        "exec",
        "--json",
        "--cd",
        workspace_path,
        "--sandbox",
        sandbox_mode,
        "--ask-for-approval",
        "never",
        "--skip-git-repo-check",
        "--ephemeral",
        "--color",
        "never",
    ]
    if model:
        argv += ["--model", model]
    argv.append("-")

    before = _snapshot_workspace(workspace_path)
    start = asyncio.get_running_loop().time()
    logger.info(
        "codex_cli: spawning model=%s prompt_chars=%d cwd=%s",
        model or "(default)",
        len(prompt),
        workspace_path,
    )

    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=workspace_path,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=_STREAM_LIMIT_BYTES,
        )
    except Exception as exc:
        logger.warning("codex_cli: failed to spawn: %s", exc)
        result.error = f"spawn_failed: {exc}"
        return result

    try:
        if proc.stdin:
            proc.stdin.write(prompt.encode("utf-8"))
            await proc.stdin.drain()
            proc.stdin.close()
    except Exception as exc:
        logger.debug("codex_cli: stdin write failed: %s", exc)

    async def _drain_stderr() -> str:
        try:
            data = await proc.stderr.read() if proc.stderr else b""
            return data.decode("utf-8", errors="replace")
        except Exception:
            return ""

    stderr_task = asyncio.create_task(_drain_stderr())

    try:
        async with asyncio.timeout(timeout_seconds):
            assert proc.stdout is not None
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                raw = line.decode("utf-8", errors="replace").strip()
                if not raw:
                    continue
                try:
                    evt = json.loads(raw)
                except json.JSONDecodeError:
                    logger.debug("codex_cli non-JSON line: %s", raw[:300])
                    continue

                evt_type = str(evt.get("type") or evt.get("event") or "").lower()
                text = _event_text(evt)
                if text and _is_final_event(evt):
                    result.result_text = text
                    await _safe_send(websocket, {
                        "type": "chat_message",
                        "role": "agent",
                        "content": text[:_MAX_EVENT_TEXT],
                    })
                elif text and any(token in evt_type for token in ("error", "warning")):
                    await _safe_send(websocket, {
                        "type": "warning",
                        "message": text[:_MAX_EVENT_TEXT],
                    })

                await _safe_send(websocket, {
                    "type": "codex_message",
                    "phase": phase_label,
                    "event": evt_type or "event",
                    "content": text[:500] if text else "",
                })

    except asyncio.TimeoutError:
        result.error = f"timeout_{int(timeout_seconds)}s"
        logger.warning("codex_cli timed out after %ss", timeout_seconds)
        _terminate_process(proc)
    except asyncio.CancelledError:
        result.error = "cancelled"
        _terminate_process(proc)
        raise
    except Exception as exc:
        result.error = f"stream_error: {exc}"
        logger.warning("codex_cli stream error: %s", exc, exc_info=True)
        _terminate_process(proc)

    try:
        await asyncio.wait_for(proc.wait(), timeout=10.0)
    except asyncio.TimeoutError:
        _terminate_process(proc, force=True)
        try:
            await proc.wait()
        except Exception:
            pass

    stderr_text = ""
    try:
        stderr_text = await asyncio.wait_for(stderr_task, timeout=2.0)
    except Exception:
        pass

    result.returncode = proc.returncode
    result.duration_ms = int((asyncio.get_running_loop().time() - start) * 1000)
    if proc.returncode == 0 and not result.error:
        result.success = True
    elif not result.error:
        result.error = f"exit_{proc.returncode}: {stderr_text[:300]}"
    if stderr_text and proc.returncode != 0:
        logger.warning("codex_cli stderr: %s", stderr_text[:700])

    result.files_written = _changed_files_since(workspace_path, before)
    for rel in result.files_written:
        payload = {
            "type": "file_write_event",
            "filename": rel,
            "action": "edit",
            "phase": phase_label,
        }
        content, size, truncated = _read_file_safely(workspace_path, rel)
        if content is not None:
            payload["content"] = content
        if size:
            payload["size"] = size
        if truncated:
            payload["content_truncated"] = True
        await _safe_send(websocket, payload)

    logger.info(
        "codex_cli done: success=%s returncode=%s duration=%dms files=%d%s",
        result.success,
        result.returncode,
        result.duration_ms,
        len(result.files_written),
        f" error={result.error}" if result.error else "",
    )
    return result


def _terminate_process(proc: asyncio.subprocess.Process, *, force: bool = False) -> None:
    if proc.returncode is not None:
        return
    try:
        if force:
            proc.kill()
        else:
            proc.send_signal(signal.SIGTERM)
    except (ProcessLookupError, Exception):
        pass
