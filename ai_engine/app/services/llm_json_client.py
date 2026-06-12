"""Claude Messages API JSON client.

Extracted from project_generator.py (god-module split). Owns the direct
(API, no SDK) Claude call that powers all code generation:

  call_claude_for_json()  — tool_use-enforced JSON output, streaming,
                            fallback model chain, truncation salvage and
                            one slimmed-prompt retry.

Plus the two helpers only it uses (_salvage_partial_json,
_slim_prompt_for_retry) and the model/token constants.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

from app.services.ws_emit import _ws_send

logger = logging.getLogger("lucid.project_generator")

CLAUDE_API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL  = "claude-sonnet-4-6"   # 64K native output — no beta header needed
# Fallback chain (in order). Sonnet 4.5 sits between 4.6 and Opus because
# during high-load windows 4.6 returns `overloaded_error` while 4.5 still
# has free capacity — much cheaper than failing over to Opus right away.
FALLBACK_MODELS = ["claude-sonnet-4-5", "claude-opus-4-7"]
FALLBACK_MODEL = FALLBACK_MODELS[-1]   # legacy alias — last-resort model
MAX_TOKENS_PER_CALL = 64000                # claude-sonnet-4-6 native max output


# ╔══════════════════════════════════════════════════════════════╗
# ║  HELPER — Salvage complete file objects from a truncated     ║
# ║  Claude JSON stream (max_tokens cutoff or stream stall).     ║
# ╚══════════════════════════════════════════════════════════════╝
def _salvage_partial_json(partial: str) -> Optional[dict]:
    """Try to extract complete file objects from a truncated JSON stream.

    When the stream is cut mid-way, we attempt to find all complete
    {"path": ..., "content": ...} objects and return them wrapped in a files list.
    """
    files = []
    # Find all complete path+content pairs using a non-greedy regex
    for m in re.finditer(
        r'\{\s*"path"\s*:\s*"([^"]+)"\s*,\s*"content"\s*:\s*"((?:[^"\\]|\\.)*)"\s*\}',
        partial,
        re.DOTALL,
    ):
        path = m.group(1)
        raw = m.group(2)
        # Unescape JSON string escapes without corrupting non-ASCII (UTF-8) content.
        # unicode_escape codec treats bytes as Latin-1 and mangles multibyte sequences;
        # instead decode only the escape sequences that JSON uses.
        try:
            content = (
                raw
                .replace("\\n", "\n")
                .replace("\\t", "\t")
                .replace("\\r", "\r")
                .replace('\\"', '"')
                .replace("\\'", "'")
                .replace("\\\\", "\\")
            )
        except Exception:
            content = raw
        if path and content and len(content) > 10:
            files.append({"path": path, "content": content})
    if files:
        logger.info("_salvage_partial_json: recovered %d files from truncated stream", len(files))
        return {"files": files}
    return None


def _slim_prompt_for_retry(prompt: str) -> tuple[str, bool]:
    """Shrink a phase prompt for a single truncation retry.

    Targets the three biggest optional padding blocks that the phase-1/2/3
    prompt builders embed, in order:
      - "DESIGN SYSTEM FROM RESEARCH:" → keep first ~6K chars of research
      - "TEMPLATE MANIFEST:"           → keep first ~4K chars
      - "CURRENT FILE TREE ...:"       → keep first ~1K chars
    All other content (instruction, schema spec, design_instruction) is
    load-bearing and left intact.

    Returns (slimmed_prompt, actually_reduced). `actually_reduced=False`
    when the prompt doesn't contain any of the target markers (e.g. the
    schema-build caller) — the caller should then bail as before rather
    than retry with an identical prompt.
    """
    if not prompt:
        return prompt, False

    slimmed = prompt
    original_len = len(slimmed)

    # 1. Cap research block (between DESIGN SYSTEM FROM RESEARCH and TEMPLATE MANIFEST)
    slimmed = re.sub(
        r"(DESIGN SYSTEM FROM RESEARCH:\s*\n)(.*?)(\n\s*TEMPLATE MANIFEST:)",
        lambda m: m.group(1) + m.group(2)[:6000] + m.group(3),
        slimmed,
        count=1,
        flags=re.DOTALL,
    )

    # 2. Cap template manifest block (between TEMPLATE MANIFEST and CURRENT FILE TREE)
    slimmed = re.sub(
        r"(TEMPLATE MANIFEST:\s*\n)(.*?)(\n\s*CURRENT FILE TREE)",
        lambda m: m.group(1) + m.group(2)[:4000] + m.group(3),
        slimmed,
        count=1,
        flags=re.DOTALL,
    )

    # 3. Cap file tree block (between CURRENT FILE TREE header and the next blank line / stack rules)
    slimmed = re.sub(
        r"(CURRENT FILE TREE[^\n]*:\s*\n)(.*?)(\n{2,})",
        lambda m: m.group(1) + m.group(2)[:1000] + m.group(3),
        slimmed,
        count=1,
        flags=re.DOTALL,
    )

    return slimmed, len(slimmed) < original_len


# ╔══════════════════════════════════════════════════════════════╗
# ║  STEP 1 — call_claude_for_json()                             ║
# ║  Direct Claude Messages API call → returns parsed JSON dict  ║
# ╚══════════════════════════════════════════════════════════════╝

async def call_claude_for_json(
    system_prompt: str,
    user_prompt: str,
    api_key: str,
    websocket=None,
    max_tokens: int = MAX_TOKENS_PER_CALL,
    model: str = DEFAULT_MODEL,
    extended_output: bool = False,
    image_refs: Optional[list] = None,
) -> Optional[dict]:
    """Call Claude Messages API and return parsed JSON dict.

    Uses Claude's native tool_use for guaranteed valid JSON output.
    The API enforces JSON schema automatically — no escaping issues.
    On failure with Opus, automatically retries with Sonnet as fallback.

    extended_output=True adds the output-128k beta header, allowing up to
    64K output tokens for complex admin/CRM projects with many pages/entities.

    image_refs=[bytes, ...] attaches up to 3 reference screenshots as image
    content blocks for visual grounding. When empty / None, the user message
    is the plain text string (legacy behavior, byte-identical).
    """
    import base64
    import httpx

    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }
    # claude-sonnet-4-6 has 64K native output — no beta header needed.
    # The output-128k-2025-02-19 beta was for older models (3.5/3.7 sonnet) only.

    # Define the output tool — Claude MUST call this to respond
    write_files_tool = {
        "name": "write_project_files",
        "description": "Write all generated project files. Call this with the complete list of files to create or modify.",
        "input_schema": {
            "type": "object",
            "properties": {
                "files": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "Relative file path (e.g. src/components/HeroSection.jsx)"
                            },
                            "content": {
                                "type": "string",
                                "description": "Complete file source code"
                            }
                        },
                        "required": ["path", "content"]
                    }
                }
            },
            "required": ["files"]
        }
    }

    # Build the user message content. When image_refs is provided, content
    # is a list of [image, image, ..., text] blocks; otherwise it's a plain
    # string (legacy behavior). Cap at 3 images to stay within token budget.
    _imgs = [img for img in (image_refs or []) if isinstance(img, (bytes, bytearray)) and img][:3]
    if _imgs:
        content_blocks: list[dict] = []
        for img_bytes in _imgs:
            content_blocks.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": base64.b64encode(bytes(img_bytes)).decode("ascii"),
                },
            })
        content_blocks.append({"type": "text", "text": user_prompt})
        user_content: object = content_blocks
    else:
        user_content = user_prompt

    payload = {
        "model": model,
        "max_tokens": max_tokens,
        # `temperature` is deprecated for newer Claude models (4.x reasoning
        # variants reject it with a 400). Anthropic uses a sensible default;
        # we don't need to pin it here.
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_content}],
        "tools": [write_files_tool],
        "tool_choice": {"type": "tool", "name": "write_project_files"},
    }

    _last_stop_reason: list[str] = [""]  # mutable container so inner fn can write it
    _last_stream_error: list[str] = [""]  # captures Anthropic's `error` event text

    async def _make_request(use_model: str) -> Optional[dict]:
        payload["model"] = use_model
        # Use streaming so:
        # 1. First token arrives in <5s instead of waiting for the full response
        # 2. We send heartbeat progress updates so the user isn't staring at a blank screen
        # 3. We avoid httpx read-timeout killing a slow but valid generation
        stream_payload = {**payload, "stream": True}
        try:
            raw_chunks: list[str] = []
            last_heartbeat = 0.0
            import time as _time

            # read=60s — Anthropic streaming sends chunks sub-second during normal
            # generation; a 60s no-data gap indicates a stall. This also ensures
            # pipeline_task.cancel() lands within at most ~60s even if the socket
            # hangs with no bytes arriving (see agent_orchestrator._listen_for_stop).
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=60.0)) as client:
                async with client.stream(
                    "POST", CLAUDE_API_URL, headers=headers, json=stream_payload
                ) as response:
                    if response.status_code != 200:
                        error_text = await response.aread()
                        error_text = error_text.decode()[:500]
                        logger.error(
                            "Claude API error %d with %s: %s",
                            response.status_code, use_model, error_text,
                        )
                        # Parse Anthropic's structured error body for a
                        # human-readable reason — without this the chat
                        # only shows "Claude API error (400)" which is
                        # unactionable. The body is JSON like:
                        #   {"type":"error","error":{"type":"invalid_request_error","message":"..."}}
                        _err_reason = ""
                        try:
                            _err_obj = json.loads(error_text)
                            _err_inner = (_err_obj or {}).get("error") or {}
                            _err_reason = (_err_inner.get("message") or "")[:300]
                        except Exception:
                            _err_reason = error_text[:300]
                        if response.status_code in (401, 403):
                            await _ws_send(websocket, "error", "❌ Anthropic API key is invalid.")
                        elif response.status_code in (400, 402) and "credit" in error_text.lower():
                            await _ws_send(websocket, "error", "❌ Anthropic API credits depleted.")
                        elif response.status_code == 429:
                            await _ws_send(websocket, "error", "⚠️ Anthropic rate limit hit. Retrying...")
                        elif response.status_code == 529:
                            await _ws_send(websocket, "error", "⚠️ Anthropic API overloaded. Retrying...")
                        else:
                            _msg = f"❌ Claude API error ({response.status_code})."
                            if _err_reason:
                                _msg = f"{_msg} {_err_reason}"
                            await _ws_send(websocket, "error", _msg)
                        return None

                    _stream_started_at = _time.monotonic()
                    async for line in response.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        raw_chunks.append(line[6:])
                        # Heartbeat every 15s — sends UI progress AND logs
                        # server-side so we can tell "Anthropic is slow but
                        # streaming" from "Anthropic call has stalled
                        # entirely" in container logs. Without the server
                        # log, a 4-min Phase 1 timeout looks identical to
                        # a hung connection.
                        now = _time.monotonic()
                        if now - last_heartbeat > 15:
                            last_heartbeat = now
                            elapsed = int(now - _stream_started_at)
                            logger.info(
                                "Claude stream heartbeat (%s): %d chunks received in %ds",
                                use_model, len(raw_chunks), elapsed,
                            )
                            try:
                                await websocket.send_json({"type": "progress", "message": "⏳ Writing files..."})
                            except Exception:
                                pass

            # Reconstruct full response from SSE stream
            response_data: dict = {}
            tool_input_parts: list[str] = []
            stop_reason = ""
            _stream_error = ""  # captured if SSE includes an error event
            _in_tok = 0
            _cache_read_tok = 0
            _cache_create_tok = 0
            _out_tok = 0
            for chunk_str in raw_chunks:
                if chunk_str == "[DONE]":
                    break
                try:
                    chunk = json.loads(chunk_str)
                except Exception:
                    continue
                ctype = chunk.get("type", "")
                if ctype == "message_start":
                    _msg_usage = (chunk.get("message") or {}).get("usage") or {}
                    _in_tok = int(_msg_usage.get("input_tokens", 0) or 0)
                    _cache_read_tok = int(_msg_usage.get("cache_read_input_tokens", 0) or 0)
                    _cache_create_tok = int(_msg_usage.get("cache_creation_input_tokens", 0) or 0)
                elif ctype == "content_block_delta":
                    delta = chunk.get("delta", {})
                    if delta.get("type") == "input_json_delta":
                        tool_input_parts.append(delta.get("partial_json", ""))
                elif ctype == "message_delta":
                    stop_reason = chunk.get("delta", {}).get("stop_reason", "")
                    # message_delta carries the final cumulative output_tokens
                    _delta_usage = chunk.get("usage") or {}
                    if _delta_usage:
                        _out_tok = int(_delta_usage.get("output_tokens", _out_tok) or _out_tok)
                elif ctype == "error":
                    # Anthropic streams an `error` event when the request is
                    # accepted (200) but the model run aborts mid-stream
                    # (overloaded, rate limited, content blocked). Without
                    # this branch we'd silently fall through to "empty
                    # response" and look stuck.
                    _err_inner = chunk.get("error") or {}
                    _stream_error = (
                        _err_inner.get("message") or _err_inner.get("type") or "stream error"
                    )[:200]

            # Report token usage to the billing meter (fire-and-forget).
            # user_id resolves from the ambient contextvar set by
            # generate_new_project, so we don't have to plumb it here.
            if _in_tok > 0 or _out_tok > 0:
                try:
                    from app.services.billing_meter import report_token_usage
                    report_token_usage(
                        None,
                        _in_tok + _cache_read_tok + _cache_create_tok,
                        _out_tok,
                        source=f"phase_{use_model.split('-')[0] if use_model else 'claude'}",
                    )
                except Exception:
                    pass

            _last_stop_reason[0] = stop_reason
            is_truncated = stop_reason == "max_tokens"
            if is_truncated:
                logger.warning("Claude (%s) response truncated (max_tokens). Attempting salvage...", use_model)
                await _ws_send(websocket, "progress", "⚠️ Response was long — salvaging complete files...")

            # Parse the assembled tool_use input JSON
            full_input = "".join(tool_input_parts)
            if full_input:
                try:
                    result = json.loads(full_input)
                    if isinstance(result, dict) and result.get("files"):
                        files = result["files"]
                        if not isinstance(files, list):
                            logger.warning(
                                "Claude returned 'files' as %s not list — skipping truncation recovery",
                                type(files).__name__,
                            )
                            return None
                        if is_truncated and len(files) > 1:
                            last_entry = files[-1]
                            last_content = last_entry.get("content", "") if isinstance(last_entry, dict) else ""
                            if (
                                len(last_content) < 50
                                or not last_content.rstrip().endswith((";", "}", ">", ");", "/>", "*/", "\n"))
                            ):
                                dropped = files.pop()
                                logger.warning(
                                    "Truncation recovery: dropped incomplete file '%s'",
                                    dropped.get("path", "?") if isinstance(dropped, dict) else "?",
                                )
                        logger.info("Claude (%s) returned %d files via stream", use_model, len(files))
                        return {"files": files}
                except json.JSONDecodeError:
                    # Truncated JSON — try salvage
                    logger.warning("Truncated JSON from stream — attempting partial salvage")
                    salvaged = _salvage_partial_json(full_input)
                    if salvaged and salvaged.get("files"):
                        logger.info("Salvaged %d files from truncated stream", len(salvaged["files"]))
                        return salvaged

            # Detailed diagnostic — we need to know WHY Claude returned
            # max_tokens with no tool_use input. Cases:
            #  • text-only output: Claude wrote preamble/thinking text
            #    instead of calling the tool (tool_choice failed)
            #  • all chunks went to thinking: extended_thinking ate the
            #    output budget before tool_use could fire
            #  • empty stream: API aborted mid-response
            # Count chunk types so we can tell these apart in logs.
            _chunk_types: dict[str, int] = {}
            _text_chars = 0
            _thinking_chars = 0
            for c in raw_chunks:
                if c == "[DONE]":
                    continue
                try:
                    _cj = json.loads(c)
                except Exception:
                    continue
                t = _cj.get("type", "?")
                _chunk_types[t] = _chunk_types.get(t, 0) + 1
                if t == "content_block_delta":
                    d = _cj.get("delta", {}) or {}
                    if d.get("type") == "text_delta":
                        _text_chars += len(d.get("text") or "")
                    elif d.get("type") == "thinking_delta":
                        _thinking_chars += len(d.get("thinking") or "")
            logger.error(
                "Claude stream had no tool_use input (model=%s, stop=%s, error=%s, "
                "chunks=%d, in_tok=%d, out_tok=%d, text_chars=%d, thinking_chars=%d, "
                "chunk_types=%s, max_tokens_sent=%d)",
                use_model, stop_reason, _stream_error or "—", len(raw_chunks),
                _in_tok, _out_tok, _text_chars, _thinking_chars,
                _chunk_types, max_tokens,
            )
            _last_stream_error[0] = _stream_error or ""
            return None

        except httpx.TimeoutException as te:
            logger.error("Claude API stream timeout (%s): %s", use_model, te)
            await _ws_send(websocket, "error", "❌ Claude API timed out. Check your connection and try again.")
            return None
        except Exception as exc:
            logger.error("Claude API call failed (%s): %s", use_model, exc)
            return None


    # ── Retry helper ────────────────────────────────────────────────
    # Anthropic's streaming API frequently aborts mid-stream with
    # "Overloaded" or returns an empty stream (no stop_reason) under
    # load. Without retry, every transient hiccup kills a section.
    # We retry up to 2 times with 1s backoff. Caller wraps this in a
    # primary→fallback model loop, so the effective budget is 2× per
    # model = 4 total Anthropic attempts before the section is dropped —
    # ~120s instead of the old ~360s (3+3 × 60s read timeout). Faster
    # failure frees the section semaphore slot for the next section.
    # `max_tokens` failures are NOT retried (slimmer-prompt path
    # below handles that); 4xx HTTP errors are NOT retried (returned
    # as None directly from _make_request).
    async def _request_with_retry(use_model: str, max_attempts: int = 2) -> Optional[dict]:
        import asyncio as _asyncio
        backoff = 1.0
        for attempt in range(1, max_attempts + 1):
            r = await _make_request(use_model)
            if r is not None:
                return r
            # Truncation isn't retryable here — let the slimmer-prompt logic
            # downstream handle it.
            if _last_stop_reason[0] == "max_tokens":
                return None
            err = _last_stream_error[0] or "(empty stream)"
            if attempt >= max_attempts:
                # Final failure — surface the reason once to the UI.
                await _ws_send(
                    websocket, "warning",
                    f"⚠️ Anthropic {err} after {max_attempts} attempts on {use_model}.",
                )
                return None
            logger.warning(
                "Claude empty/error on %s (attempt %d/%d, reason=%s) — backing off %.1fs",
                use_model, attempt, max_attempts, err, backoff,
            )
            await _ws_send(
                websocket, "progress",
                f"⏳ {err} — retrying in {backoff:.0f}s ({attempt}/{max_attempts})…",
            )
            await _asyncio.sleep(backoff)
            backoff = backoff * 2 + 1  # 1 → 3 → 7
        return None

    # Try with primary model (with retries)
    result = await _request_with_retry(model)
    if result:
        return result

    # If the primary model hit the token limit, try ONCE more with a slimmed
    # prompt (same model — truncation means input+output exceeded the output
    # ceiling; a weaker/stronger model won't fix that, only less input will).
    # The slimmer drops manifest/research/file-tree padding while keeping the
    # load-bearing instruction + schema + design blocks. If the prompt has
    # none of those markers (e.g. schema-build caller), we bail as before.
    if _last_stop_reason[0] == "max_tokens":
        slimmed_prompt, was_reduced = _slim_prompt_for_retry(user_prompt)
        if was_reduced:
            logger.warning(
                "Primary model truncated — retrying with slimmed prompt (%d → %d chars)",
                len(user_prompt), len(slimmed_prompt),
            )
            await _ws_send(
                websocket,
                "progress",
                "⚠️ Response was too large — retrying with a tighter prompt...",
            )
            payload["messages"] = [{"role": "user", "content": slimmed_prompt}]
            result = await _make_request(model)
            if result:
                return result
            logger.warning("Slimmed retry also truncated — accepting partial output")
        else:
            logger.warning("Primary model truncated but prompt has no slimmable markers — bailing")
        await _ws_send(websocket, "warning", "⚠️ Generation was too large — proceeding with partial output...")
        return None

    # Fallback chain (Sonnet 4.5 → Opus 4.7) if the primary failed for a
    # non-truncation reason. We walk the chain so a single transient
    # capacity blip on the primary doesn't immediately cost the user
    # an Opus call — Sonnet 4.5 is usually free even when 4.6 is overloaded.
    for fb_model in FALLBACK_MODELS:
        if fb_model == model:
            continue  # don't retry the same model we just failed on
        await _ws_send(
            websocket, "progress",
            f"⚠️ {model} failed, retrying with {fb_model}...",
        )
        logger.warning("Falling back from %s to %s", model, fb_model)
        result = await _request_with_retry(fb_model)
        if result:
            return result
        model = fb_model  # so the next loop iteration's "from" message is right

    return None
