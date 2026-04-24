"""DockerRunner — SandboxRunner that executes inside an isolated container.

Each session gets its own Docker container:
  - Isolated filesystem (users cannot see each other's files)
  - Resource caps (memory + CPU) from settings
  - Workspace directory bind-mounted at WORKSPACE_MOUNT_PATH (/workspace)
  - Labels for lifecycle tracking and orphan cleanup on restart

The module also exposes ``docker_runner_manager`` — a process-level singleton
that tracks all active containers so they can be cleaned up on startup/shutdown.

DinD (Docker-in-Docker) note
-----------------------------
When the ai_engine runs inside Docker, workspaces live at an internal path
(``/app/storage/{user}/{session}``). The Docker daemon is on the host and
needs the *host-side* path for bind mounts.  ``HOST_WORKSPACE_PATH`` maps the
internal root to the host root:

    internal: /app/storage/{user}/{session}
    host:     {HOST_WORKSPACE_PATH}/{user}/{session}

Set ``HOST_WORKSPACE_PATH`` to the left-hand side of the
``docker-compose.yml`` volume mount (e.g. ``${PWD}/workspaces``).
Leave empty for local development — the absolute path is used directly.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional, Sequence

from app.config import logger, settings
from .runner import CommandResult, SandboxRunner

try:
    import docker
    from docker.errors import NotFound
    _DOCKER_AVAILABLE = True
except ImportError:
    docker = None  # type: ignore
    _DOCKER_AVAILABLE = False


# ── Process-level container registry ─────────────────────────
# Tracks all containers this process created so they can be removed
# on startup (orphan cleanup) and shutdown (destroy_all).

class _DockerRunnerManager:
    """Tracks all active Docker sandbox containers for this process.

    Used for two lifecycle operations:
      - ``cleanup_orphaned_containers()`` — called on startup to remove
        containers left behind by a previous crash.
      - ``destroy_all()`` — called on shutdown to remove all containers.
    """

    def __init__(self) -> None:
        self._client: Optional["docker.DockerClient"] = None
        self._containers: dict[str, str] = {}  # session_id → container_id

    @property
    def client(self) -> "docker.DockerClient":
        if not _DOCKER_AVAILABLE:
            raise RuntimeError("docker package is not installed")
        if self._client is None:
            self._client = docker.from_env()
        return self._client

    def is_docker_available(self) -> bool:
        """Check if the Docker daemon is reachable."""
        if not _DOCKER_AVAILABLE:
            return False
        try:
            self.client.ping()
            return True
        except Exception:
            return False

    def register(self, session_id: str, container_id: str) -> None:
        self._containers[session_id] = container_id

    def unregister(self, session_id: str) -> str | None:
        return self._containers.pop(session_id, None)

    def cleanup_orphaned_containers(self) -> int:
        """Remove leftover containers from previous process runs.

        Two discovery strategies so containers are found even when labels
        are lost (e.g. daemon restart):
          1. Label filter  ``lucid.managed=true``  — authoritative
          2. Name prefix   ``lucid-sandbox-*``      — fallback

        Returns the number of containers removed.
        """
        try:
            by_label = self.client.containers.list(
                all=True,
                filters={"label": "lucid.managed=true"},
            )
            by_name = self.client.containers.list(
                all=True,
                filters={"name": settings.SANDBOX_CONTAINER_PREFIX},
            )
            seen: set[str] = set()
            candidates = []
            for c in list(by_label) + list(by_name):
                if c.id not in seen:
                    seen.add(c.id)
                    candidates.append(c)

            count = 0
            for container in candidates:
                try:
                    container.stop(timeout=3)
                    container.remove(force=True)
                    count += 1
                    logger.info("Cleaned up orphaned container %s", container.name)
                except Exception:
                    pass
            return count
        except Exception as exc:
            logger.error("Orphan cleanup failed: %s", exc)
            return 0

    async def destroy_all(self) -> None:
        """Destroy all containers tracked by this process (called on shutdown)."""
        for session_id in list(self._containers.keys()):
            container_id = self._containers.pop(session_id, None)
            if not container_id:
                continue
            try:
                await asyncio.to_thread(self._remove_container, container_id)
                logger.info("Container destroyed for session %s", session_id)
            except Exception as exc:
                logger.error("Failed to destroy container %s: %s", session_id, exc)

    def _remove_container(self, container_id: str) -> None:
        try:
            container = self.client.containers.get(container_id)
            container.stop(timeout=5)
            container.remove(force=True)
        except NotFound:
            pass
        except Exception as exc:
            logger.error("Container removal error: %s", exc)

    @property
    def active_container_count(self) -> int:
        return len(self._containers)


# Module-level singleton
docker_runner_manager = _DockerRunnerManager()


# ── DockerRunner ──────────────────────────────────────────────

class DockerRunner(SandboxRunner):
    """Execute commands inside an isolated Docker container.

    ``setup()``   spins up the container.
    ``teardown()`` stops and removes it.
    ``exec_command()`` runs a command via ``container.exec_run()``.
    """

    def __init__(
        self,
        session_id: str,
        workspace_dir: str,
        user_id: str = "",
    ) -> None:
        super().__init__(session_id, workspace_dir)
        self._user_id = user_id
        self._container_id: str | None = None

    @property
    def container_id(self) -> str | None:
        return self._container_id

    # ── Lifecycle ─────────────────────────────────────────────

    async def setup(self) -> None:
        """Spin up an isolated Docker container for this session."""
        container_id = await asyncio.to_thread(self._create_container)
        self._container_id = container_id
        docker_runner_manager.register(self._session_id, container_id)

    def _create_container(self) -> str:
        """Blocking: create and start the Docker container."""
        client = docker_runner_manager.client

        # Resolve host-side path for the Docker daemon bind mount
        if settings.HOST_WORKSPACE_PATH:
            rel = os.path.relpath(
                self._workspace_dir, settings.WORKSPACE_BASE_PATH
            )
            host_path = os.path.join(settings.HOST_WORKSPACE_PATH, rel)
        else:
            host_path = os.path.abspath(self._workspace_dir)

        os.makedirs(host_path, exist_ok=True)

        run_kwargs: dict = {
            "image": settings.SANDBOX_IMAGE,
            "command": "sleep infinity",
            "detach": True,
            "name": f"{settings.SANDBOX_CONTAINER_PREFIX}{self._session_id}",
            "labels": {
                "lucid.managed": "true",
                "lucid.session_id": self._session_id,
                "lucid.user_id": self._user_id,
            },
            "mem_limit": settings.SANDBOX_MEMORY_LIMIT,
            "nano_cpus": int(float(settings.SANDBOX_CPU_LIMIT) * 1e9),
            "volumes": {
                host_path: {
                    "bind": settings.WORKSPACE_MOUNT_PATH,
                    "mode": "rw",
                }
            },
            "remove": False,
        }
        if settings.DOCKER_NETWORK:
            run_kwargs["network"] = settings.DOCKER_NETWORK

        container = client.containers.run(**run_kwargs)
        logger.info(
            "DockerRunner: container %s (%s) created for session %s — host: %s",
            container.name, container.short_id, self._session_id, host_path,
        )
        return container.id

    async def teardown(self) -> None:
        """Stop and remove the container."""
        container_id = docker_runner_manager.unregister(self._session_id)
        if not container_id:
            return
        await asyncio.to_thread(docker_runner_manager._remove_container, container_id)
        self._container_id = None
        logger.info("DockerRunner: container destroyed for session %s", self._session_id)

    # ── Execution ─────────────────────────────────────────────

    async def exec_command(
        self,
        cmd: Sequence[str],
        *,
        cwd: str | None = None,
        timeout: float | None = None,
        env: dict[str, str] | None = None,
    ) -> CommandResult:
        """Run *cmd* inside the container via ``exec_run``.

        Detects container-gone conditions (force-kill, OOM, daemon restart,
        manual ``docker rm``) and marks the runner dead so callers see a clear
        error instead of retrying against a ghost container. No extra Docker
        API call on the happy path — we rely on the NotFound raised by
        ``exec_run`` itself.
        """
        if not self._container_id:
            return CommandResult(
                returncode=1,
                stdout="",
                stderr="SANDBOX_DEAD: container is not available — session needs to restart",
            )
        effective_cwd = cwd or settings.WORKSPACE_MOUNT_PATH

        try:
            result = await asyncio.to_thread(
                self._exec_in_container,
                list(cmd),
                effective_cwd,
                env,
                timeout,
            )
            return result
        except NotFound:
            # Container was removed externally (force-kill from stop-timeout,
            # OOM, or daemon restart). Mark the runner dead so subsequent
            # calls hit the fast-fail guard above instead of retrying.
            logger.warning(
                "DockerRunner: container for session %s is gone — marking runner dead",
                self._session_id,
            )
            docker_runner_manager.unregister(self._session_id)
            self._container_id = None
            return CommandResult(
                returncode=1,
                stdout="",
                stderr="SANDBOX_DEAD: container was terminated during execution",
            )
        except Exception as exc:
            logger.error(
                "DockerRunner exec_command failed [session=%s cmd=%s]: %s",
                self._session_id, cmd, exc,
            )
            return CommandResult(returncode=1, stdout="", stderr=str(exc))

    def _exec_in_container(
        self,
        cmd: list[str],
        workdir: str,
        env: dict[str, str] | None,
        timeout: float | None,
    ) -> CommandResult:
        """Blocking: exec a command inside the running container."""
        client = docker_runner_manager.client
        container = client.containers.get(self._container_id)

        exec_result = container.exec_run(
            cmd,
            workdir=workdir,
            environment=env,
            demux=True,
        )
        exit_code = exec_result.exit_code
        raw_out, raw_err = exec_result.output or (b"", b"")
        stdout = (raw_out or b"").decode("utf-8", errors="replace")
        stderr = (raw_err or b"").decode("utf-8", errors="replace")
        return CommandResult(returncode=exit_code, stdout=stdout, stderr=stderr)
