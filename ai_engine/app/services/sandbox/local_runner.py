"""LocalRunner — SandboxRunner that executes commands on the host.

Default backend.  No Docker or external services required.  Commands run
as subprocess calls in the session's workspace directory.  This is the
current production mode — agents write files and run commands locally.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from typing import Sequence

from .runner import CommandResult, SandboxRunner

logger = logging.getLogger(__name__)


class LocalRunner(SandboxRunner):
    """Execute commands as subprocesses on the local host.

    ``setup()`` and ``teardown()`` are no-ops — no external resources are
    allocated.  ``exec_command()`` wraps ``asyncio.to_thread(subprocess.run)``
    so the event loop is never blocked.
    """

    async def setup(self) -> None:
        """No-op for local execution."""
        os.makedirs(self._workspace_dir, exist_ok=True)
        logger.debug("LocalRunner ready at %s", self._workspace_dir)

    async def teardown(self) -> None:
        """No-op — workspace cleanup is handled by the session lifecycle."""

    async def exec_command(
        self,
        cmd: Sequence[str],
        *,
        cwd: str | None = None,
        timeout: float | None = None,
        env: dict[str, str] | None = None,
    ) -> CommandResult:
        """Run *cmd* as a subprocess in the workspace directory.

        Uses ``asyncio.to_thread`` so the event loop remains unblocked during
        long-running commands (npm install, git clone, etc.).

        Args:
            cmd:     Command + arguments (e.g. ``["npm", "install"]``).
            cwd:     Working directory; defaults to ``self.workspace_dir``.
            timeout: Seconds before the subprocess is killed.  ``None`` = no
                     limit (caller is responsible for capping long operations).
            env:     Extra env vars merged on top of ``os.environ``.

        Returns:
            CommandResult.
        """
        effective_cwd = cwd or self._workspace_dir
        effective_env: dict[str, str] | None = None
        if env:
            effective_env = {**os.environ, **env}

        try:
            result = await asyncio.to_thread(
                subprocess.run,
                list(cmd),
                cwd=effective_cwd,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=effective_env,
            )
            return CommandResult(
                returncode=result.returncode,
                stdout=result.stdout or "",
                stderr=result.stderr or "",
            )
        except subprocess.TimeoutExpired:
            return CommandResult(
                returncode=-1,
                stdout="",
                stderr=f"Command timed out after {timeout}s: {' '.join(cmd)}",
            )
        except FileNotFoundError:
            return CommandResult(
                returncode=127,
                stdout="",
                stderr=f"Command not found: {cmd[0]}",
            )
        except Exception as exc:
            logger.error(
                "LocalRunner exec_command failed [session=%s cmd=%s]: %s",
                self._session_id, cmd, exc,
            )
            return CommandResult(returncode=1, stdout="", stderr=str(exc))
