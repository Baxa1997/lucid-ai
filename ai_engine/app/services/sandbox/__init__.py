"""Sandbox runner package.

Provides a swappable execution environment for agent tasks.

Two implementations:
  LocalRunner  — runs commands directly in a local workspace directory.
                 Default mode; no Docker required.
  DockerRunner — runs commands inside an isolated Docker container with
                 a bind-mounted workspace. Opt-in via settings.

Usage
-----
    from app.services.sandbox import create_runner

    runner = create_runner(session_id=sid, workspace_dir="/tmp/ws")
    await runner.setup()

    result = await runner.exec_command(["npm", "install"])
    print(result.stdout)

    await runner.teardown()

The factory returns a LocalRunner unless Docker is explicitly requested
(future: controlled by settings.SANDBOX_BACKEND = "local" | "docker").
"""

from .runner import SandboxRunner, CommandResult
from .local_runner import LocalRunner
from .docker_runner import DockerRunner, docker_runner_manager


def create_runner(
    session_id: str,
    workspace_dir: str,
    user_id: str = "",
    use_docker: bool = False,
) -> SandboxRunner:
    """Factory: return the appropriate SandboxRunner for a session.

    Args:
        session_id:   Unique session identifier.
        workspace_dir: Host-side path to the workspace directory.
        user_id:      Owner user ID (used for Docker labels).
        use_docker:   If True, return a DockerRunner; otherwise LocalRunner.
    """
    if use_docker:
        return DockerRunner(
            session_id=session_id,
            workspace_dir=workspace_dir,
            user_id=user_id,
        )
    return LocalRunner(session_id=session_id, workspace_dir=workspace_dir)


__all__ = [
    "SandboxRunner",
    "CommandResult",
    "LocalRunner",
    "DockerRunner",
    "docker_runner_manager",
    "create_runner",
]
