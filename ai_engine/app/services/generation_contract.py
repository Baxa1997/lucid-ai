"""Shared contracts for project generation pipelines.

The public pipeline functions still return ``bool`` for compatibility with the
existing orchestrator, but each pipeline can build this richer result internally
and expose consistent metadata as the system grows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class GenerationResult:
    """Uniform result shape for landing, website, and admin generation."""

    pipeline: str
    ok: bool = False
    build_ok: bool | None = None
    files_written: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def add_files(self, files: list[str] | tuple[str, ...]) -> None:
        self.files_written.extend(str(f) for f in files if f)

    def warn(self, message: str) -> None:
        if message:
            self.warnings.append(message)

    def as_dict(self) -> dict[str, Any]:
        return {
            "pipeline": self.pipeline,
            "ok": self.ok,
            "build_ok": self.build_ok,
            "files_written": list(self.files_written),
            "warnings": list(self.warnings),
            "metadata": dict(self.metadata),
        }
