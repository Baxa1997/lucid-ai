"""SandboxRunner — abstract base class for all execution backends.

Every sandbox implementation must provide:
  - setup()         — initialise the execution environment
  - teardown()      — clean up all resources
  - exec_command()  — run a shell command and return its result
  - workspace_dir   — the local filesystem path the agent operates in

The interface is intentionally small so new backends (Firecracker, Modal,
remote SSH) only need to implement four methods.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Sequence


@dataclass
class CommandResult:
    """Return value of SandboxRunner.exec_command()."""

    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        """True when the command exited with code 0."""
        return self.returncode == 0

    @property
    def output(self) -> str:
        """Combined stdout + stderr (stdout first)."""
        parts = []
        if self.stdout:
            parts.append(self.stdout)
        if self.stderr:
            parts.append(self.stderr)
        return "\n".join(parts)


class SandboxRunner(ABC):
    """Abstract sandbox execution backend.

    Subclasses implement the four abstract methods below.  Callers only
    depend on this interface, making the backend swappable at runtime.
    """

    def __init__(self, session_id: str, workspace_dir: str) -> None:
        self._session_id = session_id
        self._workspace_dir = workspace_dir

    # ── Identity ──────────────────────────────────────────────

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def workspace_dir(self) -> str:
        """Host-side filesystem path the agent reads/writes."""
        return self._workspace_dir

    # ── Lifecycle ─────────────────────────────────────────────

    @abstractmethod
    async def setup(self) -> None:
        """Initialise the execution environment.

        Called once after the runner is created.  For LocalRunner this is a
        no-op; for DockerRunner this spins up the container.
        """

    @abstractmethod
    async def teardown(self) -> None:
        """Clean up all resources owned by this runner.

        For LocalRunner this is a no-op; for DockerRunner this stops and
        removes the container.
        """

    # ── Execution ─────────────────────────────────────────────

    @abstractmethod
    async def exec_command(
        self,
        cmd: Sequence[str],
        *,
        cwd: str | None = None,
        timeout: float | None = None,
        env: dict[str, str] | None = None,
    ) -> CommandResult:
        """Run a command inside the sandbox.

        Args:
            cmd:     Command + arguments as a sequence of strings.
            cwd:     Working directory.  Defaults to ``workspace_dir``.
            timeout: Hard wall-clock cap in seconds.  None means no limit.
            env:     Extra environment variables merged with the current env.

        Returns:
            CommandResult with returncode, stdout, stderr.
        """
