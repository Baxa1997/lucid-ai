"""Per-session Docker container manager.

Each agent session gets its own isolated Docker container with:
- A cloned git repo (if provided)
- Git credentials configured for push
- Memory/CPU limits
- Automatic cleanup on session destroy
"""

from __future__ import annotations

import asyncio
import shlex
from typing import Optional

import docker
from docker.errors import DockerException, NotFound

from app.config import logger, settings


class DockerSessionManager:
    """Creates and destroys per-session Docker containers."""

    def __init__(self) -> None:
        self._client: Optional[docker.DockerClient] = None
        # session_id → container_id
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

    async def create_workspace(
        self,
        session_id: str,
        user_id: str,
        repo_url: Optional[str] = None,
        git_token: Optional[str] = None,
        branch: Optional[str] = None,
        git_user_name: Optional[str] = None,
        git_user_email: Optional[str] = None,
    ) -> str:
        """Create an isolated Docker container for an agent session.

        Returns the container ID. The container has:
        - /workspace as the working directory
        - git configured for push (if credentials provided)
        - The repo cloned (if repo_url provided)
        """
        container_name = f"{settings.SANDBOX_CONTAINER_PREFIX}{session_id[:12]}"

        container = await asyncio.to_thread(
            self._create_container, container_name, session_id,
        )
        container_id = container.id
        self._containers[session_id] = container_id

        logger.info(
            "Container %s created for session %s (user=%s)",
            container_name, session_id, user_id,
        )

        # Configure git and clone repo inside the container
        if repo_url or git_user_name:
            await self.setup_git_push(
                container_id=container_id,
                repo_url=repo_url,
                git_token=git_token,
                branch=branch,
                git_user_name=git_user_name,
                git_user_email=git_user_email,
            )

        return container_id

    def _create_container(self, container_name: str, session_id: str):
        """Synchronous container creation (called via to_thread)."""
        # Build container kwargs
        kwargs = {
            "image": settings.SANDBOX_IMAGE,
            "name": container_name,
            "detach": True,
            "tty": True,
            "stdin_open": True,
            "working_dir": settings.WORKSPACE_MOUNT_PATH,
            "labels": {
                "lucid.session_id": session_id,
                "lucid.managed": "true",
            },
            "mem_limit": settings.SANDBOX_MEMORY_LIMIT,
            "nano_cpus": int(settings.SANDBOX_CPU_LIMIT * 1e9),
            # Keep the container running
            "command": "sleep infinity",
        }

        if settings.DOCKER_NETWORK:
            kwargs["network"] = settings.DOCKER_NETWORK

        return self.client.containers.run(**kwargs)

    async def setup_git_push(
        self,
        container_id: str,
        repo_url: Optional[str] = None,
        git_token: Optional[str] = None,
        branch: Optional[str] = None,
        git_user_name: Optional[str] = None,
        git_user_email: Optional[str] = None,
    ) -> None:
        """Configure git credentials and clone repo inside the container."""
        commands = []

        # Install git if not present (some images may not have it)
        commands.append(
            "which git || (apt-get update -qq && apt-get install -y -qq git > /dev/null 2>&1)"
        )

        # Configure git user identity
        if git_user_name:
            commands.append(
                f"git config --global user.name {shlex.quote(git_user_name)}"
            )
        if git_user_email:
            commands.append(
                f"git config --global user.email {shlex.quote(git_user_email)}"
            )

        # Configure credential store for push
        if git_token and repo_url:
            commands.append("git config --global credential.helper store")

            # Determine the host for credentials
            if "github.com" in repo_url:
                cred_line = f"https://x-access-token:{git_token}@github.com"
            elif "gitlab" in repo_url:
                cred_line = f"https://oauth2:{git_token}@gitlab.com"
            else:
                # Generic: extract host from URL
                from urllib.parse import urlparse
                parsed = urlparse(repo_url)
                host = parsed.hostname or "github.com"
                cred_line = f"https://x-access-token:{git_token}@{host}"

            # Write credentials file (single echo, no token in command history)
            commands.append(
                f"echo {shlex.quote(cred_line)} > ~/.git-credentials"
            )
            commands.append("chmod 600 ~/.git-credentials")

        # Clone the repo
        if repo_url:
            clone_url = _build_clone_url(repo_url, git_token)
            safe_url = shlex.quote(clone_url)
            branch_flag = f" -b {shlex.quote(branch)}" if branch else ""
            commands.append(
                f"git clone --depth 1{branch_flag} {safe_url} {settings.WORKSPACE_MOUNT_PATH}/repo "
                f"&& cd {settings.WORKSPACE_MOUNT_PATH}/repo"
            )

        # Execute all commands sequentially in the container
        full_cmd = " && ".join(commands)
        exit_code, output = await asyncio.to_thread(
            self._exec_in_container, container_id, full_cmd,
        )

        if exit_code != 0:
            logger.warning(
                "Git setup in container %s exited with code %d: %s",
                container_id[:12], exit_code, output[:500],
            )
        else:
            logger.info("Git configured in container %s", container_id[:12])

    def _exec_in_container(self, container_id: str, cmd: str) -> tuple[int, str]:
        """Run a command inside a container. Returns (exit_code, output)."""
        try:
            container = self.client.containers.get(container_id)
            result = container.exec_run(
                ["sh", "-c", cmd],
                workdir=settings.WORKSPACE_MOUNT_PATH,
            )
            return result.exit_code, result.output.decode("utf-8", errors="replace")
        except Exception as exc:
            logger.error("exec_run failed in %s: %s", container_id[:12], exc)
            return 1, str(exc)

    async def exec_command(self, session_id: str, cmd: str) -> tuple[int, str]:
        """Execute a command in the session's container."""
        container_id = self._containers.get(session_id)
        if not container_id:
            return 1, f"No container for session {session_id}"
        return await asyncio.to_thread(self._exec_in_container, container_id, cmd)

    async def destroy_workspace(self, session_id: str) -> None:
        """Stop and remove the Docker container for a session."""
        container_id = self._containers.pop(session_id, None)
        if not container_id:
            return

        try:
            await asyncio.to_thread(self._remove_container, container_id)
            logger.info("Container destroyed for session %s", session_id)
        except Exception as exc:
            logger.error(
                "Failed to destroy container for session %s: %s",
                session_id, exc,
            )

    def _remove_container(self, container_id: str) -> None:
        """Synchronous container removal."""
        try:
            container = self.client.containers.get(container_id)
            container.stop(timeout=5)
            container.remove(force=True)
        except NotFound:
            pass  # Already removed
        except Exception as exc:
            logger.error("Container removal error: %s", exc)

    async def destroy_all(self) -> None:
        """Destroy all managed containers (used during shutdown)."""
        session_ids = list(self._containers.keys())
        for sid in session_ids:
            await self.destroy_workspace(sid)

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

    @property
    def active_container_count(self) -> int:
        return len(self._containers)


# Module-level singleton
docker_manager = DockerSessionManager()


def _build_clone_url(repo_url: str, git_token: str | None) -> str:
    """Inject credentials into the clone URL when a token is provided."""
    if not git_token:
        return repo_url
    if "github.com" in repo_url:
        return repo_url.replace("https://", f"https://x-access-token:{git_token}@")
    if "gitlab" in repo_url:
        return repo_url.replace("https://", f"https://oauth2:{git_token}@")
    return repo_url
