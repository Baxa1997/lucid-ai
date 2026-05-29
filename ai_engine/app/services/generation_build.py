"""Shared build-check wrapper for generation pipelines."""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from app.services.generation_contract import GenerationResult

logger = logging.getLogger("lucid.generation_build")

SendFn = Callable[[str, str], Awaitable[None]]
PhaseFn = Callable[[str, str], Awaitable[None]]


async def run_generation_build_check(
    *,
    pipeline: str,
    workspace_path: str,
    api_key: str,
    classification: dict[str, Any],
    websocket: Any = None,
    max_retries: int = 2,
    send: SendFn | None = None,
    phase: PhaseFn | None = None,
    progress_message: str = "Final build check...",
    active_status: str = "Running production build to catch errors...",
    success_status: str = "Build passed - preview ready",
    failure_message: str = "Some issues remain - preview it and let me know what to fix.",
    failure_kind: str = "warning",
    failure_status_template: str = "{error_count} error(s) remain",
    generation: GenerationResult | None = None,
) -> dict[str, Any]:
    """Run BuildValidator and keep websocket._build_ok consistent.

    ``send`` receives ``(kind, message)`` and ``phase`` receives
    ``(status_text, state)``. The wrapper keeps UI behavior pipeline-specific
    while centralizing the important contract: build failures set
    ``websocket._build_ok = False`` so publish gating can trust it.
    """

    async def _send(kind: str, message: str) -> None:
        if send is None or not message:
            return
        try:
            await send(kind, message)
        except Exception:
            pass

    async def _phase(status: str, state: str) -> None:
        if phase is None:
            return
        try:
            await phase(status, state)
        except Exception:
            pass

    def _set_build_ok(value: bool) -> None:
        try:
            setattr(websocket, "_build_ok", value)
        except Exception:
            pass

    await _send("progress", progress_message)
    await _phase(active_status, "active")

    try:
        from app.services.build_validator import BuildValidator

        validator = BuildValidator(
            api_key=api_key,
            classification=classification or {},
            websocket=websocket,
            max_retries=max(0, int(max_retries or 0)),
        )
        build_result = await validator.validate_and_fix(workspace_path)
        build_ok = bool(build_result.get("success"))
        _set_build_ok(build_ok)
        build_result["build_ok"] = build_ok
        if generation is not None:
            generation.build_ok = build_ok
            generation.metadata["build"] = {
                "success": build_ok,
                "attempts": build_result.get("attempts", 0),
                "error_count": build_result.get("error_count", 0),
                "fixed_files": list(build_result.get("fixed_files") or []),
            }

        if build_ok:
            if len(build_result.get("fixed_files") or []) > 0:
                await _send("progress", "Cleaned up a few small issues.")
            await _phase(success_status, "done")
            return build_result

        err_count = build_result.get("error_count", 0)
        if generation is not None:
            generation.warn(failure_status_template.format(error_count=err_count))
        await _send(failure_kind, failure_message)
        await _phase(
            failure_status_template.format(error_count=err_count),
            "error",
        )
        return build_result

    except Exception as exc:
        _set_build_ok(False)
        if generation is not None:
            generation.build_ok = False
            generation.warn("Build check failed")
            generation.metadata["build"] = {
                "success": False,
                "exception": True,
                "errors": str(exc),
            }
        logger.warning(
            "%s: build_validator failed (non-fatal) - %s",
            pipeline, exc,
        )
        await _phase("Build check failed", "error")
        return {
            "success": False,
            "build_ok": False,
            "exception": True,
            "errors": str(exc),
            "error_count": 1,
            "fixed_files": [],
        }
