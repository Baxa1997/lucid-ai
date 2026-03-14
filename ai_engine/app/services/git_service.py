import asyncio
import logging
from fastapi import WebSocket

logger = logging.getLogger(__name__)

async def run_git(repo_path: str, *args) -> tuple[int, str, str]:
    """Run a git command using asyncio subprocess with 30s timeout."""
    cmd = ["git"] + list(args)
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=repo_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30.0)
        
        out = stdout.decode("utf-8").strip()
        err = stderr.decode("utf-8").strip()
        logger.info(f"git {' '.join(args)} -> stdout: {out} | stderr: {err}")
        return proc.returncode, out, err
    except asyncio.TimeoutError:
        logger.error(f"git {' '.join(args)} timed out after 30s")
        return -1, "", "Timeout after 30 seconds"
    except Exception as e:
        logger.error(f"git {' '.join(args)} error: {e}")
        return -1, "", str(e)

async def push_to_branch(
    repo_path: str,
    branch_name: str,
    github_token: str,
    commit_message: str,
    websocket: WebSocket
) -> bool:
    try:
        # 1. Configure git credentials using token
        await run_git(repo_path, "config", "user.email", "agent@lucidai.com")
        await run_git(repo_path, "config", "user.name", "Lucid AI")

        # 2. Checkout to selected branch
        code, _, _ = await run_git(repo_path, "checkout", branch_name)
        if code != 0:
            # If branch doesn't exist locally: git checkout -b {branch_name} origin/{branch_name}
            code, _, err = await run_git(repo_path, "checkout", "-b", branch_name, f"origin/{branch_name}")
            if code != 0:
                # Fallback to just creating it locally if no origin branch exists
                await run_git(repo_path, "checkout", "-b", branch_name)

        # 3. Stage all changes
        await run_git(repo_path, "add", ".")

        # 4. Check if there are changes to commit
        _, out, _ = await run_git(repo_path, "status", "--porcelain")
        if not out:
            await websocket.send_json({
                "type": "push_complete",
                "branch": branch_name,
                "message": "no changes"
            })
            return True

        # 5. Commit
        await run_git(repo_path, "commit", "-m", commit_message)

        # Extract repo path to construct authenticated push URL
        _, remote_out, _ = await run_git(repo_path, "remote", "get-url", "origin")
        repo_url = remote_out.replace("https://github.com/", "").replace("git@github.com:", "").strip()
        
        # 6. Push with token authentication
        push_url = f"https://{github_token}@github.com/{repo_url}"
        code, out, err = await run_git(repo_path, "push", push_url, branch_name)
        if code != 0:
            await websocket.send_json({
                "type": "error",
                "message": f"Push failed: {err}"
            })
            return False

        # 7. Send success to websocket
        await websocket.send_json({
            "type": "push_complete",
            "branch": branch_name,
            "message": f"Changes pushed to {branch_name}"
        })
        return True

    except Exception as e:
        # 8. On any error
        await websocket.send_json({
            "type": "error",
            "message": f"Push failed: {str(e)}"
        })
        return False
