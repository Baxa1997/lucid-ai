"""BuildValidator — Production build validator with Claude-powered self-healing.

This module implements the build validation gate that sits between code generation
and GitHub commit/push. It runs `npm run build` (or pnpm/yarn), and if the build
fails, sends the errors to Claude for auto-fixing (up to 3 attempts).

Architecture:
  Layer 1 (MCP): TEMPLATE_MANIFEST.md — tells Claude what components exist
  Layer 2 (Skills): Quality standards — tells Claude HOW to fix correctly
  Layer 3 (Plugin): CLAUDE.md — auto-read by Claude before any prompt

Usage:
    from app.services.build_validator import BuildValidator

    validator = BuildValidator(api_key, classification, websocket)
    result = await validator.validate_and_fix(workspace_path)

    if result["success"]:
        # Build passed — commit and push
    elif result["needs_fix"]:
        # Build failed after 3 attempts — commit with flag
"""
import os
import json
import asyncio
import subprocess
import logging
from typing import Optional

from fastapi import WebSocket

logger = logging.getLogger("lucid.build_validator")

# Import helpers from task_pipeline
try:
    from app.services.task_pipeline import (
        detect_package_manager,
        _pm_install_cmd,
        _pm_env,
    )
except ImportError:
    # Fallback for direct import
    def detect_package_manager(workspace_path: str, default: str = "npm") -> str:
        import shutil
        _pm_map = [
            ("pnpm-lock.yaml", "pnpm"),
            ("yarn.lock", "yarn"),
            ("bun.lockb", "bun"),
            ("package-lock.json", "npm"),
        ]
        for lock_file, pm_name in _pm_map:
            if os.path.isfile(os.path.join(workspace_path, lock_file)):
                if shutil.which(pm_name):
                    return pm_name
                else:
                    # Binary not installed — remove lock file and use npm
                    try:
                        os.remove(os.path.join(workspace_path, lock_file))
                    except Exception:
                        pass
                    return "npm"
        return default

    def _pm_install_cmd(pm: str, packages=None):
        if packages:
            return [pm, "add"] + packages if pm != "npm" else ["npm", "install", "--save"] + packages
        if pm == "pnpm":
            return ["pnpm", "install", "--no-frozen-lockfile"]
        elif pm == "yarn":
            return ["yarn", "install", "--non-interactive"]
        return ["npm", "install", "--no-audit", "--no-fund"]

    def _pm_env(pm: str):
        return {**os.environ, "npm_config_loglevel": "error", "CI": "true"}


# Import Claude SDK
try:
    from claude_code_sdk import ClaudeCodeOptions, query
except ImportError:
    ClaudeCodeOptions = None
    query = None


class BuildValidator:
    """Production build validator with Claude-powered self-healing.

    Usage:
        validator = BuildValidator(api_key, classification, websocket)
        result = await validator.validate_and_fix(workspace_path)

        if result["success"]:
            # Build passed — safe to commit and push
        elif result["needs_fix"]:
            # Build failed but files are committed with flag
            # Show errors in UI, offer "Auto-fix" button
    """

    def __init__(
        self,
        api_key: str,
        classification: dict,
        websocket: WebSocket,
        max_retries: int = 3,
    ):
        self.api_key = api_key
        self.classification = classification
        self.websocket = websocket
        self.max_retries = max_retries
        self.attempt = 0
        self.fixed_files: list = []
        self.errors: str = ""
        self.template_manifest: str = ""

    async def _send(self, msg_type: str, status: str, message: str, **extra):
        """Send a websocket message (best-effort)."""
        try:
            payload = {"type": msg_type, "status": status, "message": message}
            payload.update(extra)
            await self.websocket.send_json(payload)
        except Exception:
            pass

    def _build_file_tree(self, workspace_path: str) -> str:
        """Build a flat file tree of the workspace."""
        entries = []
        for root, dirs, flist in os.walk(workspace_path):
            dirs[:] = [d for d in dirs if d not in {
                ".git", "node_modules", "__pycache__", ".lucid",
                ".claude", ".next", "dist", "build",
            }]
            for fname in flist:
                entries.append(os.path.relpath(os.path.join(root, fname), workspace_path))
        return "\n".join(sorted(entries)) if entries else "(empty)"

    def _load_manifest(self, workspace_path: str):
        """Load TEMPLATE_MANIFEST.md for import resolution context."""
        manifest_path = os.path.join(workspace_path, "TEMPLATE_MANIFEST.md")
        if os.path.isfile(manifest_path):
            try:
                with open(manifest_path, "r", errors="replace") as f:
                    self.template_manifest = f.read()[:4000]
            except Exception:
                pass

    async def run_build(self, workspace_path: str, build_cmd: list, build_env: dict) -> dict:
        """Run the build command and return result.

        Returns:
            {"success": True} or {"success": False, "errors": str, "error_count": int}
        """
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                build_cmd,
                cwd=workspace_path,
                capture_output=True,
                text=True,
                timeout=180,
                env=build_env,
            )

            full_output = (result.stdout or "") + "\n" + (result.stderr or "")
            full_output = full_output.strip()

            if result.returncode == 0:
                return {"success": True}

            # Extract meaningful error lines
            error_lines = full_output.splitlines()
            meaningful = [
                line for line in error_lines
                if any(kw in line.lower() for kw in [
                    "error", "module not found", "cannot find", "syntaxerror",
                    "unexpected token", "is not defined", "failed to compile",
                    "can't resolve", "export", "import",
                ])
            ]
            error_count = len(meaningful) or 1
            truncated = "\n".join(error_lines[-80:])[:4000]

            return {
                "success": False,
                "errors": truncated,
                "error_count": error_count,
                "full_output": full_output[-6000:],
            }

        except subprocess.TimeoutExpired:
            logger.warning("BuildValidator: build timed out after %ds", timeout)
            return {
                "success": False,
                "errors": f"Build timed out after {timeout}s — may indicate an infinite loop or very large project.",
                "error_count": 1,
                "timed_out": True,
            }
        except FileNotFoundError as e:
            logger.warning("BuildValidator: command not found: %s", e)
            return {
                "success": False,
                "errors": f"Build command not found: {e}. Ensure npm/pnpm/yarn is installed.",
                "error_count": 1,
            }
        except Exception as e:
            logger.warning("BuildValidator: unexpected error: %s", e)
            return {
                "success": False,
                "errors": f"Build validation error: {e}",
                "error_count": 1,
            }

    async def fix_errors(self, workspace_path: str, errors: str, build_cmd: list) -> list:
        """Send build errors to Claude for fixing.

        Returns list of files that were modified.
        """
        if not ClaudeCodeOptions or not query:
            logger.warning("BuildValidator: Claude SDK not available, skipping fix")
            return []

        ws_tree = self._build_file_tree(workspace_path)

        manifest_block = ""
        if self.template_manifest:
            manifest_block = (
                "\n## TEMPLATE MANIFEST (available components — check before importing):\n"
                f"{self.template_manifest}\n"
            )

        fix_prompt = f"""The production build (`{' '.join(build_cmd)}`) failed with these errors:

```
{errors}
```

## WORKSPACE FILES (these are ALL the files that exist):
{ws_tree}
{manifest_block}
## FIX INSTRUCTIONS:
Fix ONLY the files causing these errors. Do not modify any other files.

Rules:
- If 'Module not found' -> check TEMPLATE_MANIFEST above for correct import path, or create the missing component
- If 'is not defined' -> add missing import or fix variable name
- If 'SyntaxError' or type errors -> fix the JavaScript/TypeScript syntax
- If missing 'use client' -> add it at the top of the file (Next.js only)
- Do not change logic, design, or styling — only fix imports and syntax
- Use RELATIVE imports only (../components/X, ./sections/Y)
- For Next.js: do NOT use @/ alias

After fixing, list every file you changed and why.
STOP when all errors are fixed.
"""
        try:
            import pwd as _bv_pwd
            _lu = _bv_pwd.getpwnam("lucidai")
            _home = _lu.pw_dir
            _user = "lucidai"
        except KeyError:
            _home = "/root"
            _user = "root"

        fix_env = {
            "ANTHROPIC_API_KEY": str(self.api_key).strip(),
            "HOME": _home,
            "USER": _user,
            "PATH": f"{_home}/.npm-global/bin:/usr/local/bin:/usr/bin:/bin",
            "IS_SANDBOX": "1",
        }

        fix_options = ClaudeCodeOptions(
            cwd=str(workspace_path),
            env=fix_env,
            model=str(self.classification.get("model_id", "claude-sonnet-4-6")),
            max_turns=8,
            permission_mode="bypassPermissions",
            allowed_tools=[
                "Read", "Write", "Edit", "MultiEdit",
                "Bash", "Glob", "Grep", "LS",
            ],
            disallowed_tools=[
                "GitCommit", "GitPush", "GitPull", "GitClone",
            ],
            append_system_prompt=(
                "You are a build error fixer. Your ONLY job is to make the build pass.\n"
                "Read the error output carefully. Fix each error precisely.\n"
                "Common fixes:\n"
                "- Missing component -> create a minimal component file with correct exports\n"
                "- Wrong import path -> fix to match actual file location in workspace\n"
                "- Missing 'use client' -> add at top of file (Next.js components with hooks)\n"
                "- Syntax error -> fix the syntax\n"
                "- Missing export -> add named + default export\n"
                "Do NOT redesign or refactor. Only fix build errors.\n"
                "Use Write tool immediately. Stop when done."
            ),
        )

        modified_files = []

        try:
            async with asyncio.timeout(300):
                async for message in query(
                    prompt=fix_prompt,
                    options=fix_options,
                ):
                    msg_str = str(message)
                    try:
                        await self.websocket.send_json({
                            "type": "claude_message",
                            "content": msg_str[:500],
                        })
                    except Exception:
                        pass
        except Exception as fix_err:
            logger.warning("BuildValidator: fix attempt failed: %s", fix_err)

        return modified_files

    async def validate_and_fix(self, workspace_path: str) -> dict:
        """Main entry point: validate build and auto-fix errors.

        Returns:
            {
                "success": bool,      # True if build passes
                "needs_fix": bool,    # True if build failed but should commit anyway
                "attempts": int,      # Number of build attempts made
                "errors": str,        # Last error output (empty if success)
                "fixed_files": list,  # Files Claude modified during fixing
                "error_count": int,   # Number of meaningful errors
            }
        """
        skip_result = {
            "success": True, "needs_fix": False, "attempts": 0,
            "errors": "", "fixed_files": [], "error_count": 0,
        }

        # Check if package.json exists and has a build script
        pkg_json_path = os.path.join(workspace_path, "package.json")
        if not os.path.isfile(pkg_json_path):
            logger.info("BuildValidator: no package.json — skipping")
            return skip_result

        try:
            with open(pkg_json_path, "r") as f:
                pkg_data = json.loads(f.read())
        except Exception:
            return skip_result

        scripts = pkg_data.get("scripts", {})
        if "build" not in scripts:
            logger.info("BuildValidator: no 'build' script — skipping")
            return skip_result

        # Setup
        pm = detect_package_manager(workspace_path, "npm")
        build_cmd = [pm, "run", "build"]
        build_env = _pm_env(pm)

        # Load template manifest for import resolution
        self._load_manifest(workspace_path)

        # Ensure node_modules exist
        if not os.path.isdir(os.path.join(workspace_path, "node_modules")):
            await self._send("build", "checking", f"📦 Installing dependencies ({pm})...")
            try:
                install_cmd = _pm_install_cmd(pm)
                await asyncio.to_thread(
                    subprocess.run,
                    install_cmd,
                    cwd=workspace_path,
                    capture_output=True,
                    text=True,
                    timeout=120,
                    env=build_env,
                )
            except Exception as ie:
                logger.warning("BuildValidator: install failed: %s", ie)

        await self._send("build", "checking",
                         f"🔍 Running production build ({' '.join(build_cmd)})...")

        # Build + Fix loop
        last_errors = ""
        last_error_count = 0

        while self.attempt <= self.max_retries:
            result = await self.run_build(workspace_path, build_cmd, build_env)

            if result["success"]:
                fixed_msg = ""
                if self.attempt > 0:
                    s = "s" if self.attempt > 1 else ""
                    fixed_msg = f" (fixed in {self.attempt} attempt{s})"
                await self._send("build", "passed",
                                 f"✅ Production build passed{fixed_msg} — ready for deployment")
                logger.info("BuildValidator: build passed on attempt %d", self.attempt + 1)
                return {
                    "success": True,
                    "needs_fix": False,
                    "attempts": self.attempt,
                    "errors": "",
                    "fixed_files": self.fixed_files,
                    "error_count": 0,
                }

            # Build failed
            last_errors = result.get("errors", "Unknown error")
            last_error_count = result.get("error_count", 1)

            # Timeout — no parseable error output for Claude to fix
            if result.get("timed_out"):
                await self._send("build", "warning",
                    "⚠️ Build timed out — skipping auto-fix (no error output to parse). "
                    "Project will be deployed as-is.")
                return {
                    "success": False, "needs_fix": True,
                    "attempts": self.attempt + 1,
                    "errors": last_errors,
                    "fixed_files": self.fixed_files,
                    "error_count": last_error_count,
                }

            logger.warning(
                "BuildValidator: build failed (attempt %d/%d) — %d errors",
                self.attempt + 1, self.max_retries + 1, last_error_count,
            )

            if self.attempt < self.max_retries:
                await self._send("build", "fixing",
                    f"⚠️ Build failed with {last_error_count} error(s) — "
                    f"Claude is auto-fixing (attempt {self.attempt + 1}/{self.max_retries})...",
                    errors=last_errors[:2000],
                )
                modified = await self.fix_errors(workspace_path, last_errors, build_cmd)
                self.fixed_files.extend(modified)

            self.attempt += 1

        # All retries exhausted — graceful degradation
        await self._send("build", "failed",
            f"⚠️ {last_error_count} build error(s) remain after "
            f"{self.max_retries} auto-fix attempts. Project will be deployed with warnings.",
            errors=last_errors[:2000],
            needs_fix=True,
        )

        return {
            "success": False,
            "needs_fix": True,
            "attempts": self.attempt,
            "errors": last_errors,
            "fixed_files": self.fixed_files,
            "error_count": last_error_count,
        }
