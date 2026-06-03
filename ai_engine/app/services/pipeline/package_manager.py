"""
pipeline/package_manager.py — Package manager detection and command building.

Extracted verbatim from task_pipeline.py (lines 358–454).
Zero logic changes.
"""

import os
import logging

logger = logging.getLogger(__name__)


def detect_package_manager(workspace_path: str, user_preference: str = "npm") -> str:
    """Auto-detect package manager from lock files in the workspace.

    Priority: lock file detection > user preference > npm fallback.
    Supports: npm, yarn, pnpm, bun.

    SAFETY: If a lock file is found but the binary doesn't exist (e.g., pnpm
    not installed in Docker), falls back to npm and removes the conflicting
    lock file to prevent build errors.
    """
    import shutil

    def _binary_exists(name: str) -> bool:
        return shutil.which(name) is not None

    detected = None
    lock_file = None

    if os.path.exists(os.path.join(workspace_path, "yarn.lock")):
        detected, lock_file = "yarn", "yarn.lock"
    elif os.path.exists(os.path.join(workspace_path, "pnpm-lock.yaml")):
        detected, lock_file = "pnpm", "pnpm-lock.yaml"
    elif os.path.exists(os.path.join(workspace_path, "bun.lockb")):
        detected, lock_file = "bun", "bun.lockb"
    elif os.path.exists(os.path.join(workspace_path, "package-lock.json")):
        detected, lock_file = "npm", "package-lock.json"

    if detected:
        if _binary_exists(detected):
            return detected
        else:
            # Binary not installed — fall back to npm
            logger.warning(
                "detect_package_manager: %s lock file found but '%s' binary not installed — falling back to npm",
                lock_file, detected,
            )
            # Remove the lock file so npm doesn't conflict
            try:
                lf_path = os.path.join(workspace_path, lock_file)
                if os.path.isfile(lf_path):
                    os.remove(lf_path)
                    logger.info("Removed %s to allow npm install", lock_file)
            except Exception:
                pass
            return "npm"

    # No lock file found — use user preference (from settings)
    pref = (user_preference or "pnpm").strip().lower()
    return pref if pref in ("npm", "yarn", "pnpm", "bun") else "pnpm"


def _pm_install_cmd(pm: str, packages: list = None) -> list:
    """Build an install command list for the given package manager."""
    if packages:
        # Installing specific packages
        if pm == "yarn":
            return ["yarn", "add"] + packages
        elif pm == "pnpm":
            return ["pnpm", "add"] + packages
        elif pm == "bun":
            return ["bun", "add"] + packages
        else:
            return ["npm", "install", "--save", "--no-audit", "--no-fund"] + packages
    else:
        # Installing all from package.json
        if pm == "yarn":
            return ["yarn", "install", "--non-interactive"]
        elif pm == "pnpm":
            # --store-dir + --package-import-method=copy duplicate the env vars
            # in _pm_env() but are safer here as explicit flags — pnpm sometimes
            # ignores npm_config_* during nested invocations (e.g. when a build
            # script triggers its own install). See _pm_env() docstring for the
            # Docker Desktop -116 / EREMOTE root cause.
            return [
                "pnpm", "install", "--no-frozen-lockfile",
                "--store-dir", "/tmp/pnpm_store",
                "--package-import-method=copy",
                "--prefer-offline",
            ]
        elif pm == "bun":
            return ["bun", "install"]
        else:
            return ["npm", "install", "--no-audit", "--no-fund"]


def _pm_env(pm: str) -> dict:
    """Build environment variables for running a package manager.

    PNPM-on-Docker-Desktop trap (errno -116 / EREMOTE / ESTALE): the bind-mounted
    macOS filesystem (`fakeowner` gRPC-fuse) does not support cross-mountpoint
    hardlinks reliably AND intermittently rejects `copyfile()` syscalls during
    high-concurrency installs. pnpm's defaults (store on the same FS as the
    workspace + hardlink-first import) trip this every time on `/app/storage`.

    Two env-level fixes that apply to every pnpm invocation in the codebase:
      • npm_config_store_dir=/tmp/pnpm_store   — keeps the store on the
        container overlay FS (fast, native filesystem, supports all syscalls).
      • npm_config_package_import_method=copy  — skips the hardlink/clone path
        entirely; plain copy works on every filesystem.
    Together these cut a typical install from ~8 min (with retries on -116) to
    ~2 min, AND make the result deterministic across host OSes.
    """
    import pwd as _pwd
    try:
        _lu = _pwd.getpwnam("lucidai")
        _home = _lu.pw_dir
        _user = "lucidai"
    except KeyError:
        _home = "/root"
        _user = "root"

    env = {
        **os.environ,
        "HOME": _home,
        "USER": _user,
        "PATH": f"{_home}/.npm-global/bin:/usr/local/bin:/usr/bin:/bin",
        "npm_config_loglevel": "error",
        # Shared caches — packages downloaded once are reused across all workspaces
        "npm_config_cache": "/tmp/npm_cache",
        "PNPM_HOME": "/tmp/pnpm_global",
        # pnpm-specific: pin store + force copy-based import so installs survive
        # Docker Desktop's macOS bind mount (see docstring above).
        "npm_config_store_dir": "/tmp/pnpm_store",
        "npm_config_package_import_method": "copy",
    }
    # Enable corepack for yarn/pnpm if needed
    if pm in ("yarn", "pnpm"):
        env["COREPACK_ENABLE_STRICT"] = "0"
    return env
