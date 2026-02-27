"""Docker daemon helpers — sandbox creation, health check, and orphan cleanup.

Each agent session gets its own Docker container (the "sandbox") with:
  - An isolated filesystem — user A cannot see user B's files
  - Resource limits — memory and CPU caps per session
  - A bind-mounted workspace directory at WORKSPACE_MOUNT_PATH (/workspace)
  - Labels for lifecycle tracking and orphan cleanup

This module owns the full container lifecycle:
  - create_sandbox()               — spin up a fresh container for a session
  - destroy_container()            — stop + remove a specific container
  - cleanup_orphaned_containers()  — remove leftover containers on startup
  - destroy_all()                  — remove all tracked containers on shutdown
"""

from __future__ import annotations

import asyncio
import os
from typing import Optional

import docker
from docker.errors import NotFound

from app.config import logger, settings


class DockerSessionManager:
    """Manages Docker daemon interaction for sandbox lifecycle."""

    def __init__(self) -> None:
        self._client: Optional[docker.DockerClient] = None
        # session_id → container_id for all live sandboxes this process created
        self._containers: dict[str, str] = {}

    @property
    def client(self) -> docker.DockerClient:
        if self._client is None:
            self._client = docker.from_env()
        return self._client

    def is_docker_available(self) -> bool:
        """Check if the Docker daemon is reachable."""
        try:
            self.client.ping()
            return True
        except Exception:
            return False

    def create_sandbox(
        self,
        *,
        session_id: str,
        user_id: str,
        workspace_dir: str,
    ) -> str:
        """Create an isolated Docker sandbox container for one agent session.

        The workspace directory is bind-mounted into the container at
        ``WORKSPACE_MOUNT_PATH`` (``/workspace``) so the agent's file
        operations are visible to the ai_engine's file API without exec
        overhead.

        DinD note
        ---------
        When the ai_engine itself runs inside Docker the workspace lives at
        an internal path (``/app/storage/{user}/{session}``).  The Docker
        daemon sits on the host and needs the **host-side** path for the
        bind mount.  ``HOST_WORKSPACE_PATH`` maps the internal root to the
        host root:

            internal: /app/storage/{user}/{session}
            host:     {HOST_WORKSPACE_PATH}/{user}/{session}

        Set ``HOST_WORKSPACE_PATH`` to the left-hand side of the
        ``docker-compose.yml`` volume mount (e.g. ``${PWD}/workspaces``).
        For local development without Docker, leave it empty — the absolute
        path of ``workspace_dir`` is used directly.

        Returns the container ID.
        """
        # Resolve the host-side path the Docker daemon needs for the bind mount.
        if settings.HOST_WORKSPACE_PATH:
            rel = os.path.relpath(workspace_dir, settings.WORKSPACE_BASE_PATH)
            host_path = os.path.join(settings.HOST_WORKSPACE_PATH, rel)
        else:
            host_path = os.path.abspath(workspace_dir)

        os.makedirs(host_path, exist_ok=True)

        run_kwargs: dict = {
            "image": settings.SANDBOX_IMAGE,
            # Keep the container alive so the agent can exec commands into it.
            "command": "sleep infinity",
            "detach": True,
            "name": f"{settings.SANDBOX_CONTAINER_PREFIX}{session_id}",
            "labels": {
                "lucid.managed": "true",
                "lucid.session_id": session_id,
                "lucid.user_id": user_id,
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

        container = self.client.containers.run(**run_kwargs)
        self._containers[session_id] = container.id
        logger.info(
            "Sandbox %s (%s) created for session %s — host workspace: %s",
            container.name, container.short_id, session_id, host_path,
        )
        return container.id

    async def destroy_container(self, container_id: str, session_id: str) -> None:
        """Stop and remove a specific sandbox container."""
        self._containers.pop(session_id, None)
        await asyncio.to_thread(self._remove_container, container_id)
        logger.info("Sandbox container destroyed for session %s", session_id)

    def cleanup_orphaned_containers(self) -> int:
        """Remove any leftover containers from previous runs.

        Returns the number of containers cleaned up.
        """
        try:
            containers = self.client.containers.list(
                all=True,
                filters={"label": "lucid.managed=true"},
            )
            count = 0
            for container in containers:
                try:
                    container.stop(timeout=3)
                    container.remove(force=True)
                    count += 1
                except Exception:
                    pass
            return count
        except Exception as exc:
            logger.error("Orphan cleanup failed: %s", exc)
            return 0

    async def destroy_all(self) -> None:
        """Destroy all tracked containers (called on shutdown)."""
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
docker_manager = DockerSessionManager()
