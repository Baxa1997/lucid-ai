"""llm_retry.py — central retry wrapper for LLM API calls.

Why this exists
───────────────
Every LLM call site (Claude direct HTTP, Gemini SDK, OpenAI direct HTTP)
used to swallow transient API errors (rate limits, 5xx, network blips,
timeouts) and return None — which then crashed the whole pipeline because
downstream code assumed the call succeeded. A single 503 from Anthropic
would lose the user 12 minutes of generation.

This module provides one helper, ``call_with_retry``, that classifies
errors as transient or permanent and retries the transient ones with
exponential backoff + jitter. Permanent errors (400, 401, 403) bubble up
immediately so we don't waste time on bad input.

Usage
─────
    from app.services.llm_retry import call_with_retry, LLMTransientError

    async def make_call() -> dict:
        async with httpx.AsyncClient() as c:
            r = await c.post(url, json=payload)
            if r.status_code in (429, 500, 502, 503, 504):
                raise LLMTransientError(f"{r.status_code}: {r.text[:200]}")
            r.raise_for_status()
            return r.json()

    result = await call_with_retry(make_call, label="copy_director")
"""
from __future__ import annotations

import asyncio
import logging
import random
from typing import Awaitable, Callable, TypeVar

import httpx

logger = logging.getLogger(__name__)

T = TypeVar("T")


# ── Custom exception types ──────────────────────────────────────────────

class LLMError(Exception):
    """Base for LLM call failures."""


class LLMTransientError(LLMError):
    """Retry-able: rate limit, 5xx, network error, timeout."""


class LLMPermanentError(LLMError):
    """Don't retry: 400 bad request, 401/403 auth, content policy block."""


# ── HTTP status classification ──────────────────────────────────────────

# Status codes that are worth retrying. 429 = rate limit (Retry-After).
# 408 = request timeout. 500/502/503/504 = upstream provider hiccup.
_TRANSIENT_STATUSES = {408, 425, 429, 500, 502, 503, 504, 522, 524}

# Status codes that mean "your request is bad" — retrying won't help.
_PERMANENT_STATUSES = {400, 401, 403, 404, 422}


def classify_http_error(status_code: int, body_snippet: str = "") -> LLMError:
    """Convert an HTTP status into the right exception type."""
    msg = f"HTTP {status_code}"
    if body_snippet:
        msg += f": {body_snippet[:200]}"
    if status_code in _TRANSIENT_STATUSES:
        return LLMTransientError(msg)
    if status_code in _PERMANENT_STATUSES:
        return LLMPermanentError(msg)
    # Anything else (3xx, unknown) — treat as transient by default; safer
    # to retry once than to silently lose user work.
    return LLMTransientError(msg)


def classify_exception(exc: Exception) -> LLMError:
    """Convert a raised exception into transient/permanent."""
    if isinstance(exc, LLMError):
        return exc

    # httpx raises specific subclasses for network-level failures
    if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError)):
        return LLMTransientError(f"{type(exc).__name__}: {exc}")

    # Generic asyncio/connection errors → transient
    if isinstance(exc, (asyncio.TimeoutError, ConnectionError, OSError)):
        return LLMTransientError(f"{type(exc).__name__}: {exc}")

    # Default: assume transient. The caller can pre-classify by raising
    # LLMPermanentError directly inside their function.
    return LLMTransientError(f"{type(exc).__name__}: {exc}")


# ── Retry orchestrator ──────────────────────────────────────────────────

async def call_with_retry(
    fn: Callable[[], Awaitable[T]],
    *,
    label: str,
    max_attempts: int = 3,
    base_delay: float = 1.5,
    max_delay: float = 30.0,
    on_retry: Callable[[int, Exception], None] | None = None,
    websocket=None,
) -> T:
    """Call ``fn`` with exponential backoff + jitter on transient failures.

    Args:
        fn: Zero-arg async callable that returns the success value or
            raises an exception. The function should classify its own
            permanent failures by raising ``LLMPermanentError``.
        label: Short string used in logs (e.g. "copy_director", "gemini_explore").
        max_attempts: Total attempts including the first try. Default 3.
        base_delay: Seconds before retry #1. Doubles each attempt.
        max_delay: Cap on per-attempt sleep.
        on_retry: Optional callback ``(attempt, error)`` invoked before
            sleeping — useful for surfacing "retrying…" to the UI.
        websocket: When provided, emits a ``warning`` event on each retry so
            the frontend can surface "We hit a hiccup, retrying…" to the user.

    Returns:
        The function's success value.

    Raises:
        LLMPermanentError or LLMTransientError after attempts are exhausted.
    """
    last_exc: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            return await fn()
        except Exception as exc:
            classified = classify_exception(exc)
            last_exc = classified

            if isinstance(classified, LLMPermanentError):
                logger.warning(
                    "llm_retry [%s] permanent failure (attempt %d): %s",
                    label, attempt, classified,
                )
                raise classified

            if attempt >= max_attempts:
                logger.warning(
                    "llm_retry [%s] giving up after %d attempts: %s",
                    label, attempt, classified,
                )
                raise classified

            # Exponential backoff with full jitter (AWS-recommended pattern)
            delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
            delay = random.uniform(0, delay)

            logger.info(
                "llm_retry [%s] attempt %d/%d failed (%s) — retrying in %.1fs",
                label, attempt, max_attempts, classified, delay,
            )

            if on_retry is not None:
                try:
                    on_retry(attempt, classified)
                except Exception as cb_err:
                    logger.debug("llm_retry on_retry callback raised: %s", cb_err)

            if websocket is not None:
                try:
                    delay_str = "<1s" if delay < 1 else f"{delay:.0f}s"
                    await websocket.send_json({
                        "type": "warning",
                        "message": (
                            f"⏳ Hit a temporary issue, retrying in {delay_str} "
                            f"(attempt {attempt + 1}/{max_attempts})…"
                        ),
                    })
                except Exception as ws_err:
                    logger.debug("llm_retry ws notify failed (non-fatal): %s", ws_err)

            await asyncio.sleep(delay)

    # Unreachable, but keeps type-checkers happy
    if last_exc is not None:
        raise last_exc
    raise LLMTransientError(f"llm_retry [{label}] exhausted with no exception captured")


# ── Pipeline-level failure helper ───────────────────────────────────────

async def emit_pipeline_failure(
    websocket,
    *,
    phase: str,
    code: str,
    message: str,
    retriable: bool = True,
) -> None:
    """Emit a structured failure event so the frontend can render a
    specific recovery UI instead of a generic "Generation failed".

    Args:
        phase: Which pipeline step failed (e.g. "classify", "research", "execute").
        code: Stable machine code — one of: ``rate_limit``, ``auth_error``,
              ``content_blocked``, ``network``, ``timeout``, ``unknown``.
        message: User-facing sentence (no jargon, no stack traces).
        retriable: Whether the user should be encouraged to retry.
    """
    if websocket is None:
        return
    try:
        await websocket.send_json({
            "type": "pipeline_failure",
            "phase": phase,
            "code": code,
            "message": message,
            "retriable": retriable,
        })
    except Exception as exc:
        logger.debug("emit_pipeline_failure: ws send failed: %s", exc)


def failure_code_from_exception(exc: Exception) -> str:
    """Map an LLMError (or generic exception) to a stable code string."""
    msg = str(exc).lower()
    if isinstance(exc, LLMPermanentError):
        if "401" in msg or "403" in msg or "auth" in msg:
            return "auth_error"
        if "content" in msg or "blocked" in msg or "policy" in msg:
            return "content_blocked"
        return "bad_request"
    if "429" in msg or "rate" in msg:
        return "rate_limit"
    if "timeout" in msg or "timed out" in msg:
        return "timeout"
    if "network" in msg or "connection" in msg:
        return "network"
    return "unknown"


# ── Phase 2 Step 1: Unified error envelope ──────────────────────────────
#
# Phase 2 Step 1 of the status/error refactor introduces ONE error envelope
# that subsumes the previously-split `error` / `warning` / `pipeline_failure`
# / `preview_error` / in-band `task_phase status=error` types. The new shape
# carries enough information for the frontend to render the correct recovery
# UI without needing to dispatch on `type`.
#
# Discriminator: presence of the `severity` field. The frontend `error`
# handler checks for `severity` first; if present, it routes to the new
# unified renderer. Legacy emissions (no `severity`) fall through to the
# existing dispatch, so this change is purely additive — nothing breaks.
#
# Migration plan (the dual-shape transition):
#   1. Add `emit_error()` below. New code emits via this helper. (THIS STEP)
#   2. Add frontend handler for unified shape. (next file)
#   3. Migrate ONE callsite at a time from emit_pipeline_failure → emit_error
#      so we can verify each switch in isolation.
#   4. Once all callsites moved, deprecate the legacy emit_pipeline_failure
#      and remove the legacy frontend handler.

_DEFAULT_RECOVERY_HINTS = {
    "rate_limit":       "Our AI provider is rate-limiting us right now. Wait a minute and try again.",
    "auth_error":       "Your account session may have expired. Sign out and back in, then retry.",
    "content_blocked":  "The AI provider blocked this request. Try rephrasing your prompt.",
    "network":          "Network hiccup talking to the AI provider. Please retry.",
    "timeout":          "The AI took too long to respond. Please retry.",
    "bad_request":      "Something about that request was malformed. Please retry, and if it keeps failing reach out.",
    "unknown":          "Please try again. If it keeps happening, reach out and share what you were doing.",
}

_VALID_SEVERITIES = {"warn", "recoverable", "fatal"}


# ── Phase 2 Step 2: Declared phase list ─────────────────────────────────
#
# Phase 2 Step 2 introduces `pipeline.declare` — a single event emitted at
# the start of every pipeline run that tells the frontend the full list of
# phases this specific run will go through. The frontend's progress chart
# renders against that declared list rather than assuming a hardcoded 1-8
# sequence, which:
#
#   • lets a pipeline run a SHORTER sequence (e.g. an edit that skips
#     Publish + Deploy) without showing empty slots in the chart
#   • lets a pipeline run a LONGER sequence later without a frontend release
#   • gives each phase a STABLE KEY (`validate`, `setup`, `research`, ...)
#     so labels can change per-mode without breaking the chart
#
# Migration plan (additive, zero-breakage):
#   1. Backend: emit pipeline.declare at the top of every pipeline run.
#      task_phase events continue to carry the numeric `phase` field as
#      today. (THIS STEP)
#   2. Frontend: handle pipeline.declare — store the declared list in state.
#      When rendering the chart, prefer the declared list; fall back to
#      assuming 1-8 if no declare was seen (handles in-flight reconnects).
#   3. Migrate task_phase emissions to also carry `phase_key` matching the
#      declared list. Once all emissions carry it, drop the numeric `phase`.
#
# The canonical phase keys + default labels — each pipeline picks a SUBSET
# from this list in the order they'll fire. Adding new keys is fine; the
# frontend just renders whatever the declare lists.

PHASE_VALIDATE = ("validate", "Validating inputs")
PHASE_SETUP    = ("setup",    "Setting up workspace")
PHASE_RESEARCH = ("research", "Researching")
PHASE_PLAN     = ("plan",     "Planning")
PHASE_CODE     = ("code",     "Writing code")
PHASE_VERIFY   = ("verify",   "Verifying build")
PHASE_PUBLISH  = ("publish",  "Publishing")
PHASE_DEPLOY   = ("deploy",   "Deploying")
PHASE_PUSH     = ("push",     "Pushing changes")
PHASE_CLASSIFY = ("classify", "Classifying task")
PHASE_EXPLORE  = ("explore",  "Exploring codebase")


# Canonical phase sequences per pipeline. Edit-mode skips research/plan
# because the orchestrator runs classify+explore instead. Both new + edit
# end with verify; publish/deploy fire only when there's something to ship.
PIPELINE_PHASES_NEW = [
    PHASE_VALIDATE, PHASE_SETUP, PHASE_RESEARCH, PHASE_PLAN,
    PHASE_CODE, PHASE_VERIFY, PHASE_PUBLISH, PHASE_DEPLOY,
]
PIPELINE_PHASES_EDIT = [
    PHASE_VALIDATE, PHASE_SETUP, PHASE_CLASSIFY, PHASE_EXPLORE,
    PHASE_CODE, PHASE_VERIFY, PHASE_PUSH,
]


# ── Phase 2 Step 4: Typed structured events ─────────────────────────────
#
# Replaces the free-form ``progress`` text messages with typed events that
# carry structured payloads. The frontend can render typed events with
# proper UI (chips, badges, structured logs) instead of stuffing data into
# a chat bubble.
#
# Migration recipe (each ``progress`` callsite follows this pattern):
#
#   BEFORE (free-form):
#       await websocket.send_json({
#           "type": "progress",
#           "message": f"✅ Bound {bound}/{total} images",
#       })
#
#   AFTER (typed):
#       from app.services.llm_retry import emit_image_binder_summary
#       await emit_image_binder_summary(websocket, bound=bound, requested=total)
#       # legacy progress message kept for 1 release for backward compat:
#       await websocket.send_json({
#           "type": "progress",
#           "message": f"✅ Bound {bound}/{total} images",
#       })
#
# The frontend has a typed handler that takes precedence. Once verified,
# the legacy free-form emission can be deleted from the same callsite.
#
# To add a new typed event:
#   1. Define an ``emit_<category>_<action>`` helper here.
#   2. The payload type is ``<category>.<action>`` (e.g. ``image_binder.summary``).
#   3. Caller wraps the existing free-form emission with the new helper.
#   4. Frontend dispatcher gets a handler for the new type.
#   5. Test the helper here, test the handler there.


async def emit_image_binder_summary(
    websocket,
    *,
    requested: int,
    bound: int,
    unbound: int = 0,
    geo_rejected: int = 0,
    subject_rejected: int = 0,
    retry_used: int = 0,
) -> None:
    """Emit a structured summary of an Unsplash image-binder run.

    Replaces the free-form "✅ Bound N/M images" progress message with a
    typed event the frontend can render as a chip in the chart instead of
    a chat bubble. Fail-soft.

    Args mirror the telemetry counters captured in
    ``landing_image_binder.bind_landing_images`` so the same numbers reach
    both the JSONL telemetry and the frontend without double-bookkeeping.
    """
    if websocket is None:
        return
    try:
        await websocket.send_json({
            "type": "image_binder.summary",
            "requested": requested,
            "bound": bound,
            "unbound": unbound,
            "geo_rejected": geo_rejected,
            "subject_rejected": subject_rejected,
            "retry_used": retry_used,
        })
    except Exception as exc:
        logger.debug(
            "emit_image_binder_summary: ws send failed (bound=%d/%d): %s",
            bound, requested, exc,
        )


async def emit_quality_summary(
    websocket,
    *,
    passed: int,
    total: int,
    blockers: int = 0,
    warnings: int = 0,
    purpose: str = "",
) -> None:
    """Emit a structured summary of the landing quality gate run.

    The full per-check report still ships as ``type: "quality_report"`` (the
    frontend renders it as a chart). This typed summary replaces the two
    free-form duplicates (``"✓ Quality gate: …"`` / ``"✅ Quality gate: …"``)
    so the chart can show a single status chip without us also dumping the
    same number into chat. Blocker case stays on ``type: "warning"`` because
    routing differs. Fail-soft.
    """
    if websocket is None:
        return
    try:
        await websocket.send_json({
            "type": "quality.summary",
            "passed": passed,
            "total": total,
            "blockers": blockers,
            "warnings": warnings,
            "purpose": purpose,
        })
    except Exception as exc:
        logger.debug(
            "emit_quality_summary: ws send failed (passed=%d/%d): %s",
            passed, total, exc,
        )


async def emit_build_start(
    websocket,
    *,
    package_manager: str,
    command: str,
    phase: str = "build",
) -> None:
    """Emit a structured event for the start of a build_validator run.

    ``phase`` distinguishes the two long stages we report on:
      * ``"install"`` — dependency install (npm/pnpm/yarn install)
      * ``"build"``   — production build (npm run build)

    Replaces the free-form "📦 Installing dependencies (pnpm)..." /
    "🔍 Running production build (...)..." progress messages so the
    frontend can render a chip with a spinner instead of dumping the
    same text into chat twice. Fail-soft.
    """
    if websocket is None:
        return
    try:
        await websocket.send_json({
            "type": "build.start",
            "phase": phase,
            "package_manager": package_manager,
            "command": command,
        })
    except Exception as exc:
        logger.debug(
            "emit_build_start: ws send failed (phase=%s, pm=%s): %s",
            phase, package_manager, exc,
        )


async def emit_build_result(
    websocket,
    *,
    success: bool,
    attempts: int = 0,
    error_count: int = 0,
    fixed_count: int = 0,
    install_failed: bool = False,
    timed_out: bool = False,
    needs_fix: bool = False,
) -> None:
    """Emit a structured terminal event for a build_validator run.

    The free-form "✅ Production build passed..." / "⚠️ Build failed
    with N error(s)..." progress messages stay during dual-emit, but
    the chart consumes this typed event to swap the chip status
    (pass/warn/fail). Fail-soft.
    """
    if websocket is None:
        return
    try:
        await websocket.send_json({
            "type": "build.result",
            "success": bool(success),
            "attempts": int(attempts),
            "error_count": int(error_count),
            "fixed_count": int(fixed_count),
            "install_failed": bool(install_failed),
            "timed_out": bool(timed_out),
            "needs_fix": bool(needs_fix),
        })
    except Exception as exc:
        logger.debug(
            "emit_build_result: ws send failed (success=%s, attempts=%d): %s",
            success, attempts, exc,
        )


async def emit_repo_create_started(
    websocket,
    *,
    provider: str = "github",
    repo_name: str = "",
    owner: str = "",
    platform_owned: bool = False,
) -> None:
    """Emit a structured event for the start of a remote-repo creation.

    Replaces the free-form "📦 Creating GitHub repository..." /
    "📦 Creating new repo: {org}/{name}…" progress messages from the
    pipeline orchestrator. ``provider`` is "github" or "gitlab".
    ``platform_owned`` distinguishes the platform-org repo path from the
    user-owned path so the chart can label them differently. Fail-soft.
    """
    if websocket is None:
        return
    try:
        await websocket.send_json({
            "type": "repo.create_started",
            "provider": provider,
            "repo_name": repo_name,
            "owner": owner,
            "platform_owned": bool(platform_owned),
        })
    except Exception as exc:
        logger.debug(
            "emit_repo_create_started: ws send failed (provider=%s, repo=%s): %s",
            provider, repo_name, exc,
        )


async def emit_repo_create_done(
    websocket,
    *,
    success: bool,
    provider: str = "github",
    repo_url: str = "",
    repo_name: str = "",
    platform_owned: bool = False,
    error: str = "",
) -> None:
    """Emit a structured terminal event for remote-repo creation.

    The follow-up ``type: "repo_created"`` event (already structured,
    consumed by the FE for navigation) is unchanged — this typed event
    replaces the free-form "✅ Repository created: {url}" /
    "✅ Repo created: {url}" progress lines so the chart chip can swap
    status without the duplicate chat bubble. Fail-soft.
    """
    if websocket is None:
        return
    try:
        await websocket.send_json({
            "type": "repo.create_done",
            "success": bool(success),
            "provider": provider,
            "repo_url": repo_url,
            "repo_name": repo_name,
            "platform_owned": bool(platform_owned),
            "error": error,
        })
    except Exception as exc:
        logger.debug(
            "emit_repo_create_done: ws send failed (success=%s, repo=%s): %s",
            success, repo_url, exc,
        )


async def emit_fixers_run_started(websocket) -> None:
    """Emit a structured event when landing_fixers starts its sweep.

    Replaces the "🔧 Running landing fixers..." progress text. The
    per-fixer firing telemetry (``fixer.fire``) stays in JSONL — this
    typed event is just for the chart chip. Fail-soft.
    """
    if websocket is None:
        return
    try:
        await websocket.send_json({"type": "fixers.run_started"})
    except Exception as exc:
        logger.debug("emit_fixers_run_started: ws send failed: %s", exc)


async def emit_brief_distill_started(websocket) -> None:
    """Emit a structured event when the landing brief enters the distill stage.

    Replaces the "📋 Distilling research into landing brief..." progress
    text so the chart can swap a sub-step chip without dumping the same
    string into chat. Fail-soft.
    """
    if websocket is None:
        return
    try:
        await websocket.send_json({"type": "brief.distill_started"})
    except Exception as exc:
        logger.debug("emit_brief_distill_started: ws send failed: %s", exc)


async def emit_research_started(
    websocket,
    *,
    kind: str,
    label: str = "",
) -> None:
    """Emit a structured event when a landing research pass kicks off.

    ``kind`` is the discriminator the chart uses to label the chip:
        ``"requirements"`` | ``"structure"`` | ``"design"`` |
        ``"products"`` | ``"domain"`` | ``"conversion"``.

    Replaces the various "🔬 Researching ..." progress strings. Fail-soft.
    """
    if websocket is None:
        return
    try:
        await websocket.send_json({
            "type": "research.started",
            "kind": kind,
            "label": label,
        })
    except Exception as exc:
        logger.debug(
            "emit_research_started: ws send failed (kind=%s): %s", kind, exc,
        )


async def emit_cli_session_started(
    websocket,
    *,
    model: str = "",
) -> None:
    """Emit a structured event when a Claude Code CLI session boots.

    Replaces the "🤖 Claude Code session started (model)" progress text.
    Fail-soft.
    """
    if websocket is None:
        return
    try:
        await websocket.send_json({
            "type": "cli.session_started",
            "model": model,
        })
    except Exception as exc:
        logger.debug(
            "emit_cli_session_started: ws send failed (model=%s): %s",
            model, exc,
        )


async def emit_code_write_started(
    websocket,
    *,
    task_type: str = "",
    model: str = "",
) -> None:
    """Emit a structured event when pipeline/step5_execute starts writing code.

    Replaces the "🤖 Writing code..." progress text. The chart can use
    ``task_type`` (e.g. "ui_simple", "feature_simple") to swap the chip
    label. Fail-soft.
    """
    if websocket is None:
        return
    try:
        await websocket.send_json({
            "type": "code.write_started",
            "task_type": task_type,
            "model": model,
        })
    except Exception as exc:
        logger.debug(
            "emit_code_write_started: ws send failed (task=%s): %s",
            task_type, exc,
        )


async def declare_pipeline_phases(
    websocket,
    *,
    pipeline_id: str,
    phases: list[tuple[str, str]] | None = None,
) -> None:
    """Tell the frontend the full phase list for this pipeline run.

    Emit ONCE per run, BEFORE the first ``task_phase`` event. Frontend
    stores the declared list and uses it to render the progress chart.

    Args:
        pipeline_id: ``"new"`` | ``"edit"`` | ``"regenerate"`` — matches
            the ``mode`` field on ``task_phase`` events from Step 3.
        phases: Optional explicit list of ``(key, label)`` tuples. When
            omitted, picks a canonical sequence based on pipeline_id.

    Fail-soft: never raises into the caller. Drop the event when
    ``websocket`` is None or the send fails.
    """
    if websocket is None:
        return

    if phases is None:
        phases = PIPELINE_PHASES_EDIT if pipeline_id in {"edit", "discuss"} else PIPELINE_PHASES_NEW

    try:
        await websocket.send_json({
            "type": "pipeline.declare",
            "pipeline_id": pipeline_id,
            "phases": [
                {"key": key, "label": label, "index": idx + 1}
                for idx, (key, label) in enumerate(phases)
            ],
        })
    except Exception as exc:
        logger.debug(
            "declare_pipeline_phases: ws send failed (pipeline_id=%s): %s",
            pipeline_id, exc,
        )


async def emit_error(
    websocket,
    *,
    phase: str,
    code: str,
    severity: str,
    message: str,
    retriable: bool | None = None,
    recovery_hint: str | None = None,
) -> None:
    """Emit a unified error envelope (the new shape after Step 1 of the refactor).

    Args:
        phase: Which pipeline step raised this (e.g. "research", "code",
            "preview", "publish"). Free-form string so new phases don't
            need an enum bump.
        code: Stable machine code. Reuse existing codes where possible
            (``rate_limit``, ``auth_error``, ``content_blocked``,
            ``network``, ``timeout``, ``bad_request``, ``unknown``).
        severity: One of ``warn`` | ``recoverable`` | ``fatal``.
            • ``warn`` — informational, the task continues.
            • ``recoverable`` — task aborted but the user can retry.
            • ``fatal`` — session is no longer usable (re-auth, reload).
        message: User-facing sentence. No stack traces, no jargon.
        retriable: Whether the user should be encouraged to retry. Default
            derives from severity (warn=None, recoverable=True, fatal=False).
        recovery_hint: Suggestion for what to do next. Defaults to a
            sensible per-code hint when omitted.

    Fail-soft: never raises into the caller. When ``websocket`` is None or
    the send fails, the event is dropped silently and logged at debug.
    """
    if severity not in _VALID_SEVERITIES:
        # Caller passed something we don't recognize. Keep the event flowing
        # but force severity to recoverable — that's the safe middle ground
        # (frontend will offer a retry UI rather than blocking the session).
        logger.warning(
            "emit_error: invalid severity %r (code=%s phase=%s) — defaulting to recoverable",
            severity, code, phase,
        )
        severity = "recoverable"

    if retriable is None:
        retriable = {"warn": None, "recoverable": True, "fatal": False}[severity]

    if not recovery_hint:
        recovery_hint = _DEFAULT_RECOVERY_HINTS.get(code, _DEFAULT_RECOVERY_HINTS["unknown"])

    if websocket is None:
        return
    try:
        await websocket.send_json({
            "type": "error",
            "phase": phase,
            "code": code,
            "severity": severity,
            "message": message,
            "retriable": retriable,
            "recovery_hint": recovery_hint,
        })
    except Exception as exc:
        logger.debug(
            "emit_error: ws send failed (phase=%s code=%s severity=%s): %s",
            phase, code, severity, exc,
        )
