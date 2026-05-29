"""User-facing plan helpers for generation pipelines."""

from __future__ import annotations

from typing import Any


def summarize_grounded_research(
    *,
    domain_res: dict[str, Any] | None = None,
    design_res: dict[str, Any] | None = None,
    admin_research: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a compact, UI-safe research summary for plan cards."""
    sources = 0
    grounded_calls = 0
    succeeded_calls = 0
    degenerate_calls = 0
    urls: list[str] = []

    for bundle in (domain_res, design_res):
        if not isinstance(bundle, dict):
            continue
        summary = bundle.get("_summary") or {}
        sources += int(summary.get("total_sources", 0) or 0)
        grounded_calls += int(summary.get("calls_grounded", 0) or 0)
        succeeded_calls += int(summary.get("calls_succeeded", 0) or 0)
        degenerate_calls += int(summary.get("calls_degenerate", 0) or 0)
        for value in bundle.values():
            if isinstance(value, dict):
                urls.extend(str(u) for u in (value.get("urls") or []) if u)

    if isinstance(admin_research, dict):
        sources += int(admin_research.get("entity_sources") or 0)
        sources += int(admin_research.get("operations_sources") or 0)
        if admin_research.get("entity_sources"):
            grounded_calls += 1
            succeeded_calls += 1
        if admin_research.get("operations_sources"):
            grounded_calls += 1
            succeeded_calls += 1
        urls.extend(str(u) for u in (admin_research.get("all_urls") or []) if u)

    unique_urls = []
    seen: set[str] = set()
    for url in urls:
        if url in seen:
            continue
        seen.add(url)
        unique_urls.append(url)

    if sources >= 10 and degenerate_calls == 0:
        confidence = "High"
    elif sources >= 4:
        confidence = "Medium"
    elif sources > 0:
        confidence = "Light"
    else:
        confidence = "Fallback"

    notes: list[str] = []
    if sources:
        notes.append(f"{sources} live web references")
    if grounded_calls:
        notes.append(f"{grounded_calls} grounded research passes")
    if degenerate_calls:
        notes.append("Weak search output was filtered before planning")
    if not notes:
        notes.append("Using the prompt and safe domain defaults")

    return {
        "confidence": confidence,
        "sources": sources,
        "groundedPasses": grounded_calls,
        "succeededPasses": succeeded_calls,
        "notes": notes[:3],
        "urls": unique_urls[:5],
    }


def summary_chip(label: str, value: Any) -> dict[str, str]:
    return {"label": str(label), "value": str(value)}


def compact_count(label: str, count: int, singular: str, plural: str | None = None) -> str:
    word = singular if count == 1 else (plural or f"{singular}s")
    return f"{count} {word} {label}".strip()
