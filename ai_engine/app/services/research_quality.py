"""Quality helpers for grounded research bundles."""

from __future__ import annotations

from typing import Any


def research_summary_score(data: dict[str, Any] | None) -> int:
    """Rank a grounded research result for retry decisions."""
    summary = (data or {}).get("_summary") or {}
    return (
        int(summary.get("calls_grounded", 0) or 0) * 20
        + int(summary.get("total_sources", 0) or 0)
        + int(summary.get("calls_succeeded", 0) or 0) * 5
        - int(summary.get("calls_degenerate", 0) or 0) * 30
    )


def research_summary_is_strong(data: dict[str, Any] | None) -> bool:
    """True when a 4-call research bundle is good enough to distill."""
    summary = (data or {}).get("_summary") or {}
    return (
        int(summary.get("calls_succeeded", 0) or 0) >= 3
        and int(summary.get("calls_grounded", 0) or 0) >= 2
        and int(summary.get("total_sources", 0) or 0) >= 5
        and int(summary.get("calls_degenerate", 0) or 0) == 0
    )


def admin_research_is_strong(data: dict[str, Any] | None) -> bool:
    if not isinstance(data, dict):
        return False
    entity_sources = int(data.get("entity_sources") or 0)
    operations_sources = int(data.get("operations_sources") or 0)
    return entity_sources >= 3 and operations_sources >= 3


def admin_research_score(data: dict[str, Any] | None) -> int:
    if not isinstance(data, dict):
        return 0
    return (
        int(data.get("entity_sources") or 0) * 10
        + int(data.get("operations_sources") or 0) * 10
        + min(len(data.get("entity_research") or ""), 6000) // 300
        + min(len(data.get("operations_research") or ""), 6000) // 300
    )
