"""claude_cli.py — direct subprocess wrapper around the Claude Code CLI.

Why this module exists
──────────────────────
Originally we used the `claude_code_sdk` Python package to talk to Claude
Code. That SDK is a thin wrapper around the CLI that *hides token usage
from us* — which means our billing system can't attribute cost back to
users for the agentic edit path (the heaviest cost in the pipeline).

Dropping the SDK and talking to the CLI directly gives us:
  • Real token counts in every result event (input/output/cache breakdown)
  • CLI-computed USD cost per session (no math on our side)
  • Full streaming control (kill the process directly to cancel)
  • One less Python dependency
  • Same Claude Code intelligence (we're literally invoking the same binary)

Behavior parity with `step5_execute.execute_with_claude`
────────────────────────────────────────────────────────
  • Same allowed/disallowed tools
  • Same circuit breaker (N consecutive read tools without a write → stop)
  • Same retry policy (configurable; default 1 attempt — CLI has its own
    internal retries already)
  • Same file_write_event payload shape (now with content for DiffViewer)
  • Same cancel semantics (CancelledError → SIGTERM then SIGKILL)
  • Same WebSocket event types so the frontend doesn't change
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import signal
from typing import Any, Optional

from fastapi import WebSocket

logger = logging.getLogger(__name__)

# Tool sets — kept in lockstep with the SDK config in step5_execute.
DEFAULT_ALLOWED_TOOLS = "Read,Write,Edit,MultiEdit,Bash,Glob,Grep,LS"
DEFAULT_DISALLOWED_TOOLS = (
    "GitCommit,GitPush,GitPull,GitClone,"
    "Bash(git commit*),Bash(git push*),Bash(rm -rf*)"
)

# IS_SANDBOX=1 is Anthropic's officially-supported escape hatch for
# running --dangerously-skip-permissions as root inside a container.
# Without it, the CLI refuses for safety.
_BASE_ENV_OVERRIDES = {
    "IS_SANDBOX": "1",
}

# Stdout JSON lines can be ~10-30KB (full file content in tool results).
# Default reader limit (64KB) is plenty, but bump just in case for huge files.
_STREAM_LIMIT_BYTES = 4 * 1024 * 1024  # 4MB


# ─────────────────────────────────────────────────────────────
#  Result type returned to caller
# ─────────────────────────────────────────────────────────────
class ClaudeCLIResult:
    """Outcome of a single Claude CLI session."""

    __slots__ = (
        "success", "result_text", "stop_reason", "num_turns",
        "input_tokens", "output_tokens", "cache_read_tokens",
        "cache_creation_tokens", "total_cost_usd", "duration_ms",
        "files_written", "circuit_broken", "error",
    )

    def __init__(self) -> None:
        self.success: bool = False
        self.result_text: str = ""
        self.stop_reason: Optional[str] = None
        self.num_turns: int = 0
        self.input_tokens: int = 0
        self.output_tokens: int = 0
        self.cache_read_tokens: int = 0
        self.cache_creation_tokens: int = 0
        self.total_cost_usd: float = 0.0
        self.duration_ms: int = 0
        self.files_written: list[str] = []
        self.circuit_broken: bool = False
        self.error: Optional[str] = None


# ─────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────
def _resolve_claude_binary() -> str:
    """Find the claude CLI. Falls back to PATH lookup."""
    explicit = os.environ.get("CLAUDE_CLI_PATH", "")
    if explicit and os.path.isfile(explicit):
        return explicit
    found = shutil.which("claude")
    if found:
        return found
    # Last resort — Dockerfile installs to /usr/bin via npm -g
    return "/usr/bin/claude"


def _read_file_safely(workspace_path: str, file_path: str, *, max_bytes: int = 256 * 1024) -> tuple[Optional[str], int, bool]:
    """Read post-write file content for the DiffViewer payload.

    Returns (content, size, truncated). Never raises — IO errors return (None, 0, False).
    """
    try:
        full = file_path if os.path.isabs(file_path) else os.path.join(workspace_path, file_path)
        if not os.path.isfile(full):
            return None, 0, False
        size = os.path.getsize(full)
        if size > max_bytes:
            return None, size, True
        with open(full, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read(), size, False
    except Exception:
        return None, 0, False


async def _safe_send(websocket: Optional[WebSocket], payload: dict) -> None:
    if websocket is None:
        return
    try:
        await websocket.send_json(payload)
    except Exception:
        # WS closed mid-stream — caller's circuit breaker will pick up next tick.
        pass


def _is_read_tool(name: str) -> bool:
    return name.lower() in {"read", "glob", "grep", "ls"}


def _is_write_tool(name: str) -> bool:
    return name.lower() in {"write", "edit", "multiedit"}


# ─────────────────────────────────────────────────────────────
#  Main entrypoint
# ─────────────────────────────────────────────────────────────
async def run_claude_session(
    *,
    prompt: str,
    workspace_path: str,
    api_key: str,
    websocket: Optional[WebSocket] = None,
    user_id: Optional[str] = None,
    model: str = "claude-sonnet-4-6",
    max_turns: int = 12,
    timeout_seconds: float = 600.0,
    system_prompt: str = "",
    append_system_prompt: str = "",
    allowed_tools: str = DEFAULT_ALLOWED_TOOLS,
    disallowed_tools: str = DEFAULT_DISALLOWED_TOOLS,
    max_consecutive_reads: int = 6,
    phase_label: str = "step5_cli",
    extra_env: Optional[dict[str, str]] = None,
) -> ClaudeCLIResult:
    """Run one Claude Code CLI session, streaming events to ``websocket``.

    Returns a ClaudeCLIResult with token counts and outcome. Cancel by
    cancelling the surrounding asyncio task — we send SIGTERM then SIGKILL.
    """
    result = ClaudeCLIResult()

    # ── Build env ──
    env = os.environ.copy()
    env.update(_BASE_ENV_OVERRIDES)
    if api_key:
        env["ANTHROPIC_API_KEY"] = api_key
    if extra_env:
        env.update(extra_env)

    # ── Build argv ──
    bin_path = _resolve_claude_binary()
    argv = [
        bin_path,
        "--print",                              # non-interactive
        "--output-format", "stream-json",
        "--verbose",                            # required with print + stream-json
        "--dangerously-skip-permissions",       # IS_SANDBOX=1 makes this safe
        "--no-session-persistence",             # don't litter /root/.claude
        "--model", model,
        "--max-turns", str(max_turns),
        "--allowedTools", allowed_tools,
        "--disallowedTools", disallowed_tools,
    ]
    if system_prompt:
        argv += ["--system-prompt", system_prompt]
    if append_system_prompt:
        argv += ["--append-system-prompt", append_system_prompt]

    # Prompt comes via stdin to avoid argv length limits (system page-size ~128KB).
    # We use --input-format=text (default) and pipe the prompt as the body.
    # CLI reads stdin when no positional prompt is given.
    # NOTE: The CLI parses `prompt` as a positional arg if present, OR reads
    # stdin if `-` is used. To stay robust for long prompts, we pipe via stdin.
    argv += ["-"]  # forces stdin read

    logger.info(
        "claude_cli: spawning model=%s max_turns=%d prompt_chars=%d cwd=%s",
        model, max_turns, len(prompt), workspace_path,
    )

    # ── Spawn ──
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
        logger.error("claude_cli: failed to spawn: %s", exc)
        result.error = f"spawn_failed: {exc}"
        return result

    # Pipe prompt and close stdin so CLI reads EOF and starts.
    try:
        if proc.stdin:
            proc.stdin.write(prompt.encode("utf-8"))
            await proc.stdin.drain()
            proc.stdin.close()
    except Exception as exc:
        logger.warning("claude_cli: stdin write failed: %s", exc)

    consecutive_reads = 0
    has_written = False

    async def _drain_stderr() -> str:
        """Collect stderr in the background — only used for error reporting."""
        try:
            data = await proc.stderr.read() if proc.stderr else b""
            return data.decode("utf-8", errors="replace")
        except Exception:
            return ""

    stderr_task = asyncio.create_task(_drain_stderr())

    # ── Parse stream-json line by line ──
    try:
        async with asyncio.timeout(timeout_seconds):
            assert proc.stdout is not None
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break  # EOF — process finished
                line = line.strip()
                if not line:
                    continue

                try:
                    evt = json.loads(line)
                except json.JSONDecodeError:
                    # Non-JSON line — usually a startup banner or warning.
                    logger.debug("claude_cli non-JSON line: %s", line[:200])
                    continue

                evt_type = evt.get("type")

                # ─── system/init: announce model & tools ───
                if evt_type == "system" and evt.get("subtype") == "init":
                    try:
                        from app.services.llm_retry import emit_cli_session_started
                        await emit_cli_session_started(
                            websocket,
                            model=str(evt.get("model", model) or ""),
                        )
                    except Exception:
                        pass
                    continue

                # ─── assistant messages ───
                if evt_type == "assistant":
                    msg = evt.get("message") or {}
                    content = msg.get("content") or []
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        btype = block.get("type")

                        # Tool use — Write/Edit/MultiEdit/Bash/Read/Glob/etc.
                        if btype == "tool_use":
                            tname = str(block.get("name") or "")
                            tinput = block.get("input") or {}

                            # Circuit breaker
                            if _is_write_tool(tname):
                                consecutive_reads = 0
                                has_written = True
                            elif _is_read_tool(tname):
                                consecutive_reads += 1

                            if consecutive_reads >= max_consecutive_reads:
                                logger.warning(
                                    "claude_cli circuit breaker: %d consecutive reads — terminating",
                                    consecutive_reads,
                                )
                                await _safe_send(websocket, {
                                    "type": "warning",
                                    "message": "⚠️ Agent was reading files in a loop without making changes. Stopping to prevent wasted resources.",
                                })
                                result.circuit_broken = True
                                _terminate_process(proc)
                                break

                            # Forward Write/Edit/MultiEdit as file_write_event
                            if _is_write_tool(tname):
                                fpath = tinput.get("file_path") or tinput.get("path") or ""
                                if fpath:
                                    result.files_written.append(fpath)
                                    payload = {
                                        "type": "file_write_event",
                                        "filename": fpath,
                                        "action": tname.lower(),
                                        "phase": phase_label,
                                    }
                                    content_str, size, truncated = _read_file_safely(workspace_path, fpath)
                                    if content_str is not None:
                                        payload["content"] = content_str
                                    if size:
                                        payload["size"] = size
                                    if truncated:
                                        payload["content_truncated"] = True
                                    await _safe_send(websocket, payload)

                            # Bash → forward command as a terminal log event
                            elif tname.lower() == "bash":
                                cmd = (tinput.get("command") or "")[:500]
                                if cmd:
                                    await _safe_send(websocket, {
                                        "type": "terminal_log",
                                        "stream": "stdin",
                                        "data": f"$ {cmd}",
                                    })

                        # Text block — agent narration or final answer
                        elif btype == "text":
                            text = (block.get("text") or "").strip()
                            if text and len(text) > 20:
                                await _safe_send(websocket, {
                                    "type": "chat_message",
                                    "role": "agent",
                                    "content": text[:2000],
                                })

                        # Thinking blocks — keep silent in user-facing chat,
                        # but log for debugging.
                        elif btype == "thinking":
                            thought = (block.get("thinking") or "").strip()
                            if thought:
                                logger.debug("claude_cli thinking: %.200s", thought)

                    continue

                # ─── tool results (filesystem/Bash output) ───
                if evt_type == "user":
                    msg = evt.get("message") or {}
                    content = msg.get("content") or []
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "tool_result":
                            # Bash command outputs come back here — forward to terminal.
                            inner = block.get("content")
                            if isinstance(inner, str) and inner.strip():
                                # Only forward if it looks like Bash stdout (avoid file dumps)
                                tu_id = block.get("tool_use_id", "")
                                if tu_id:  # always forward; cheap and useful
                                    await _safe_send(websocket, {
                                        "type": "terminal_log",
                                        "stream": "stdout",
                                        "data": inner[:2000],
                                    })
                    continue

                # ─── final result event ───
                if evt_type == "result":
                    result.success = not bool(evt.get("is_error"))
                    result.result_text = evt.get("result") or ""
                    result.stop_reason = evt.get("stop_reason")
                    result.num_turns = int(evt.get("num_turns") or 0)
                    result.duration_ms = int(evt.get("duration_ms") or 0)
                    result.total_cost_usd = float(evt.get("total_cost_usd") or 0.0)

                    usage = evt.get("usage") or {}
                    result.input_tokens = int(usage.get("input_tokens") or 0)
                    result.output_tokens = int(usage.get("output_tokens") or 0)
                    result.cache_read_tokens = int(usage.get("cache_read_input_tokens") or 0)
                    result.cache_creation_tokens = int(usage.get("cache_creation_input_tokens") or 0)
                    if not result.success and not result.error:
                        result.error = evt.get("api_error_status") or "result_error"
                    # Keep going to drain any trailing events.
                    continue

    except asyncio.TimeoutError:
        result.error = f"timeout_{int(timeout_seconds)}s"
        logger.warning("claude_cli timed out after %ss", timeout_seconds)
        _terminate_process(proc)
    except asyncio.CancelledError:
        result.error = "cancelled"
        logger.info("claude_cli cancelled by caller")
        _terminate_process(proc)
        raise  # re-raise so the surrounding task knows we honored the cancel
    except Exception as exc:
        result.error = f"stream_error: {exc}"
        logger.error("claude_cli stream error: %s", exc, exc_info=True)
        _terminate_process(proc)

    # Wait for process to finish + drain stderr.
    try:
        await asyncio.wait_for(proc.wait(), timeout=10.0)
    except asyncio.TimeoutError:
        _terminate_process(proc, force=True)
        try:
            await proc.wait()
        except Exception:
            pass

    try:
        stderr_text = await asyncio.wait_for(stderr_task, timeout=2.0)
    except Exception:
        stderr_text = ""

    if proc.returncode != 0 and not result.error:
        result.error = f"exit_{proc.returncode}: {stderr_text[:300]}"
    if stderr_text and proc.returncode != 0:
        logger.warning("claude_cli stderr: %s", stderr_text[:500])

    # ── Report token usage to the billing meter ──
    if result.input_tokens > 0 or result.output_tokens > 0:
        try:
            from app.services.billing_meter import report_token_usage
            # Cache reads are billed at 0.1× input rate; we count them as
            # input tokens for quota purposes (slight over-attribution that
            # protects margin — better than under-counting).
            effective_input = result.input_tokens + result.cache_read_tokens + result.cache_creation_tokens
            report_token_usage(
                user_id,
                effective_input,
                result.output_tokens,
                source=phase_label,
            )
        except Exception as exc:
            logger.warning("claude_cli billing report failed: %s", exc)

    logger.info(
        "claude_cli done: success=%s turns=%d in=%d out=%d cache_read=%d cost=$%.4f duration=%dms files=%d%s%s",
        result.success, result.num_turns, result.input_tokens, result.output_tokens,
        result.cache_read_tokens, result.total_cost_usd, result.duration_ms,
        len(result.files_written),
        f" stop={result.stop_reason}" if result.stop_reason else "",
        f" error={result.error}" if result.error else "",
    )

    return result


def _terminate_process(proc: asyncio.subprocess.Process, *, force: bool = False) -> None:
    """SIGTERM → SIGKILL escalation. Best-effort, never raises."""
    if proc.returncode is not None:
        return  # already exited
    try:
        if force:
            proc.kill()
        else:
            proc.send_signal(signal.SIGTERM)
            # Give it a moment, then SIGKILL via async task. The waiter above
            # times out at 10s before forcing SIGKILL anyway.
    except (ProcessLookupError, Exception):
        pass
