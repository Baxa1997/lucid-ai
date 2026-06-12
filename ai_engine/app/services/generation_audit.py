"""Uniform end-of-pipeline artifact-coverage reporting.

Every generation pipeline plans a set of artifacts (landing sections,
website pages, admin entity CRUD pages) and generates them with parallel
LLM calls that can fail individually — stream stalls, max-token
truncation, rate limits. Historically a lost batch produced a quietly
incomplete project: the pipeline reported success and the user discovered
the gap in the preview.

This module gives all three pipelines one loud, structured way to report
planned-vs-generated coverage:

  • ``generation_audit`` WS event — structured payload (additive; frontends
    without a handler ignore unknown types)
  • a chat-visible warning that NAMES the missing artifacts and tells the
    user how to recover
  • ``generation.metadata["artifact_audit"]`` on the GenerationResult
  • a ``pipeline.artifact_audit`` telemetry event for ops queries

The audit NEVER changes a pipeline's success/failure decision — partial
output is still a usable project. It only guarantees the gap is visible
to the user, the frontend, and telemetry instead of being swallowed.
"""

from __future__ import annotations

from typing import Any

from app.config import logger

# Cap how many artifact names go into the chat message — past this the
# message stops being readable and the structured event has the full list.
_MAX_NAMES_IN_CHAT = 8


def _plural(label: str) -> str:
    if label.endswith("y"):
        return label[:-1] + "ies"
    return label + "s"


async def report_artifact_coverage(
    *,
    pipeline: str,
    websocket: Any,
    planned: list[str],
    missing: list[str],
    artifact_label: str,
    generation: Any = None,
    recovery_hint: str = "",
) -> dict[str, Any]:
    """Report planned-vs-generated artifact coverage for one pipeline run.

    Args:
        pipeline:        "landing" | "website" | "admin" (free-form label).
        websocket:       WS / WebSocketProxy — may be None (silent audit).
        planned:         display names of every artifact the plan promised.
        missing:         subset of ``planned`` that is NOT on disk.
        artifact_label:  singular noun for messages ("section", "page", …).
        generation:      optional GenerationResult — receives metadata + warning.
        recovery_hint:   overrides the default "ask me to add …" chat hint.

    Returns the structured audit dict (also sent as the WS event payload).
    """
    planned_n = len(planned)
    missing_n = len(missing)
    audit: dict[str, Any] = {
        "pipeline": pipeline,
        "artifact": artifact_label,
        "planned": planned_n,
        "generated": planned_n - missing_n,
        "missing": list(missing),
        "ok": missing_n == 0,
    }

    if generation is not None:
        try:
            generation.metadata["artifact_audit"] = audit
            if missing_n:
                generation.warn(
                    f"{missing_n}/{planned_n} planned {_plural(artifact_label)} missing: "
                    + ", ".join(missing)
                )
        except Exception:
            pass

    try:
        from app.services.telemetry import emit as _emit
        _emit(
            "pipeline.artifact_audit",
            pipeline=pipeline,
            artifact=artifact_label,
            planned=planned_n,
            missing=missing_n,
            missing_names=", ".join(missing)[:500],
        )
    except Exception:
        pass

    if missing_n == 0:
        logger.info(
            "%s: artifact audit OK — %d/%d %s on disk",
            pipeline, planned_n, planned_n, _plural(artifact_label),
        )
        return audit

    logger.error(
        "%s: artifact audit INCOMPLETE — missing %d/%d %s: %s",
        pipeline, missing_n, planned_n, _plural(artifact_label), ", ".join(missing),
    )

    if websocket is not None:
        try:
            await websocket.send_json({"type": "generation_audit", **audit})
        except Exception:
            pass

        names = ", ".join(missing[:_MAX_NAMES_IN_CHAT])
        if missing_n > _MAX_NAMES_IN_CHAT:
            names += f" (+{missing_n - _MAX_NAMES_IN_CHAT} more)"
        hint = recovery_hint or (
            f"Ask me to add the missing {_plural(artifact_label)} and I'll generate just those."
        )
        try:
            await websocket.send_json({
                "type": "warning",
                "message": (
                    f"⚠️ {planned_n - missing_n} of {planned_n} planned "
                    f"{_plural(artifact_label)} were generated. Missing: {names}. {hint}"
                ),
            })
        except Exception:
            pass

    return audit
