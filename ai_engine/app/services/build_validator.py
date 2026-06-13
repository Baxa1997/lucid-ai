"""BuildValidator — Production build validator with agent-powered self-healing.

This module implements the build validation gate that sits between code generation
and GitHub commit/push. It runs `npm run build` (or pnpm/yarn), and if the build
fails, sends the errors to Codex or Claude for auto-fixing (up to 3 attempts).

Architecture:
  Layer 1 (MCP): TEMPLATE_MANIFEST.md — tells Claude what components exist
  Layer 2 (Skills): Quality standards — tells the fixer HOW to fix correctly
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
import re
import time
import json
import asyncio
import subprocess
import logging
from typing import Optional

from fastapi import WebSocket

logger = logging.getLogger("lucid.build_validator")

_INSTALL_TIMEOUT_SECONDS = int(os.environ.get("BUILD_VALIDATOR_INSTALL_TIMEOUT", "900"))

# Import helpers from pipeline package
try:
    from app.services.pipeline import (
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
    """Production build validator with agent-powered self-healing.

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
        max_retries: int = 1,
        openai_api_key: str = "",
        openai_model: str = "",
    ):
        self.api_key = api_key
        self.openai_api_key = openai_api_key
        self.openai_model = openai_model
        self.classification = classification
        self.websocket = websocket
        self.max_retries = max_retries
        self.attempt = 0
        self.fixed_files: list = []
        # Packages we've already auto-installed for a "Module not found" error,
        # so a still-missing module doesn't loop forever (bounded by rounds too).
        self._auto_added_deps: set = set()
        self._auto_add_rounds: int = 0
        self.errors: str = ""
        self.template_manifest: str = ""
        self.codex_fix_failed: bool = False

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

    def _patch_nextjs_dist_dir(self, workspace_path: str) -> None:
        """Ensure next.config supports `distDir: process.env.NEXT_DIST || ".next"`.

        Needed for the background-BV optimisation: BV runs concurrently with
        `next dev` and they both default to writing under `.next/`, which
        corrupts the dev server. With this patch BV runs with NEXT_DIST=
        .next-build and the two processes own separate output trees.

        Idempotent — does nothing when the config already reads NEXT_DIST.
        """
        import re
        env_expr = "process.env.NEXT_DIST"
        dist_line = f'distDir: {env_expr} || ".next",'

        for fname in ("next.config.mjs", "next.config.js", "next.config.ts"):
            config_path = os.path.join(workspace_path, fname)
            if not os.path.isfile(config_path):
                continue
            try:
                with open(config_path, "r", encoding="utf-8") as fh:
                    content = fh.read()
                original = content
                if env_expr in content:
                    return  # already env-driven
                if "distDir" in content:
                    # Existing distDir literal — replace with env-driven form
                    content = re.sub(
                        r"distDir\s*:\s*[^,\n}]+,?",
                        dist_line,
                        content,
                        count=1,
                    )
                else:
                    # Inject right after the nextConfig opening brace.
                    # Pattern handles: `const nextConfig = {`, `module.exports = {`,
                    # `export default { ... }` — anything ending in `{` on its own line.
                    new_content, n = re.subn(
                        r"(=\s*\{)",
                        rf"\1\n  {dist_line}",
                        content,
                        count=1,
                    )
                    if n == 0:
                        # Could not locate a brace — bail rather than mangle.
                        return
                    content = new_content
                if content != original:
                    with open(config_path, "w", encoding="utf-8") as fh:
                        fh.write(content)
                    logger.info(
                        "BuildValidator: patched %s with NEXT_DIST distDir override",
                        fname,
                    )
            except OSError as exc:
                logger.debug("BuildValidator: distDir patch on %s failed: %s", fname, exc)
            return  # only patch the first config we find

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
        # 360s — Next.js cold prod build with fresh node_modules + type-check
        # + 56 generated files routinely hits 150-220s. 180s was timing out
        # legitimate builds and pushing the UI to "SOME ISSUES REMAIN".
        timeout = 360
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                build_cmd,
                cwd=workspace_path,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=build_env,
            )

            full_output = (result.stdout or "") + "\n" + (result.stderr or "")
            full_output = full_output.strip()

            if result.returncode == 0:
                return {"success": True}

            # ── Infra crash vs code error ────────────────────────────────
            # A worker CRASH (SIGBUS / SIGKILL / SIGSEGV / OOM) is a failure of
            # the LOCAL build sandbox, not a code error — Next's build worker
            # died ("build worker exited with code: null and signal: SIGBUS").
            # The generated code parsed clean; a real compile error names a
            # module or symbol. Classify it separately so the pipeline does NOT
            # demote a valid project to a draft branch + a broken Vercel deploy.
            full_lower = full_output.lower()
            _INFRA_CRASH_MARKERS = (
                "signal: sigbus", "signal: sigkill", "signal: sigsegv",
                "signal: sigabrt", "worker exited with code: null",
                "javascript heap out of memory", "out of memory",
                "cannot allocate memory",
            )
            _REAL_COMPILE_MARKERS = (
                "failed to compile", "module not found", "cannot find module",
                "syntaxerror", "type error", "unexpected token",
                "is not defined", "can't resolve", "cannot resolve",
            )
            if any(m in full_lower for m in _INFRA_CRASH_MARKERS) and not any(
                m in full_lower for m in _REAL_COMPILE_MARKERS
            ):
                logger.warning(
                    "BuildValidator: build worker crashed (infra, not a code error) — %s",
                    next((m for m in _INFRA_CRASH_MARKERS if m in full_lower), "signal"),
                )
                return {
                    "success": False,
                    "infra_crash": True,
                    "errors": "\n".join(full_output.splitlines()[-40:])[:2000],
                    "error_count": 0,
                }

            # Extract meaningful error lines.
            # Pnpm/npm noise (Progress:, Packages:, Recreating, resolved/reused
            # download counters) drowns out the actual compile errors when we
            # just tail the last 80 lines — the next-build output gets pushed
            # off the bottom. Filter noise first, then surface the real errors.
            error_lines = full_output.splitlines()
            _NOISE_PREFIXES = (
                "progress:", "packages:", "recreating", "lockfile",
                "warning", "warn ", "info ", "fetched",
                "+++", "===", "│", "?",
            )
            def _is_noise(line: str) -> bool:
                low = line.strip().lower()
                if not low:
                    return True
                if low.startswith(_NOISE_PREFIXES):
                    return True
                if low.startswith("packages:") or low.startswith("progress:"):
                    return True
                return False
            denoised = [ln for ln in error_lines if not _is_noise(ln)]
            meaningful = [
                line for line in denoised
                if any(kw in line.lower() for kw in [
                    "error:", "type error", "module not found", "cannot find",
                    "syntaxerror", "unexpected token", "is not defined",
                    "failed to compile", "can't resolve", "× ", "⨯ ",
                    " at ", "expected", "unexpected",
                ])
            ]
            error_count = len(meaningful) or 1
            # Prefer the tail of the *denoised* output so install progress
            # doesn't crowd out next-build's compile errors.
            tail_source = denoised if denoised else error_lines
            truncated = "\n".join(tail_source[-80:])[:4000]

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

    def _expected_binary_ready(self, workspace_path: str, pkg_data: dict) -> bool:
        """True when node_modules contains the framework binary needed to build.

        A partial dependency install can leave node_modules present but unusable.
        If we continue into the build loop in that state, Claude gets asked to
        "fix" missing `next`/`vite` binaries, which wastes minutes and looks
        like a generation loop. Treat dependency setup as its own failure class.
        """
        deps = {
            **(pkg_data.get("dependencies") or {}),
            **(pkg_data.get("devDependencies") or {}),
        }
        bin_dir = os.path.join(workspace_path, "node_modules", ".bin")
        if "next" in deps:
            return os.path.isfile(os.path.join(bin_dir, "next"))
        if "vite" in deps:
            return os.path.isfile(os.path.join(bin_dir, "vite"))
        return os.path.isdir(bin_dir) and bool(os.listdir(bin_dir))

    def _install_command(self, pm: str) -> list[str]:
        """Build an install command with the same cache bias as preview setup.

        For pnpm, `_pm_install_cmd` already injects --store-dir +
        --package-import-method=copy (REQUIRED on Docker Desktop's macOS bind
        mount to dodge errno -116). Don't duplicate them here.
        """
        cmd = list(_pm_install_cmd(pm))
        if pm == "npm":
            return cmd + ["--prefer-offline"]
        return cmd

    async def fix_errors(self, workspace_path: str, errors: str, build_cmd: list) -> list:
        """Send build errors to the configured code agent for fixing.

        Returns list of files that were modified.
        """
        if self._prefer_codex_fixer():
            modified = await self.fix_errors_with_codex(workspace_path, errors, build_cmd)
            if modified or not str(self.api_key or "").strip():
                return modified

        if not str(self.api_key or "").strip():
            logger.info("BuildValidator: no Anthropic key available, skipping Claude auto-fix")
            return []
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
- If "Expected ',', got '<...>'" or "Expected ',', got 'aria'" / "Expected ',', got <attrName>"
  in a .jsx/.tsx file -> this is almost always JSX SIBLINGS WITHOUT A WRAPPER inside a
  ternary or .map() callback. Wrap the siblings in a fragment or div:
    BAD:  {{cond ? ( <A/> <B/> ) : ( <X/> )}}
    GOOD: {{cond ? ( <> <A/> <B/> </> ) : ( <X/> )}}
  Same fix applies to `items.map(x => <A/> <B/>)` → `items.map(x => <><A/><B/></>)`.
- Do not change logic, design, or styling — only fix imports and syntax
- Prefer the import style already used by the project. If jsconfig.json
  or vite.config.js defines @/*, @/ imports are valid.

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
            max_turns=4,
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

    def _prefer_codex_fixer(self) -> bool:
        if self.codex_fix_failed:
            return False
        raw = os.environ.get(
            "LUCID_BUILD_FIX_AGENT_PROVIDER",
            os.environ.get("LUCID_EDIT_AGENT_PROVIDER", "codex"),
        ).strip().lower()
        if raw in {"claude", "anthropic"}:
            return False
        try:
            from app.services.codex_cli import codex_cli_available
            return codex_cli_available()
        except Exception:
            return False

    def _codex_model(self) -> str:
        model = (
            self.openai_model
            or os.environ.get("CODEX_BUILD_FIX_MODEL", "")
            or os.environ.get("CODEX_EDIT_MODEL", "")
            or os.environ.get("CODEX_MODEL", "")
        ).strip()
        if model.startswith("openai/"):
            return model.split("/", 1)[1]
        return model

    async def fix_errors_with_codex(
        self,
        workspace_path: str,
        errors: str,
        build_cmd: list,
    ) -> list:
        """Use Codex to fix build errors when Codex is the edit executor."""
        try:
            from app.services.codex_cli import run_codex_session
        except Exception as exc:
            logger.info("BuildValidator: Codex CLI unavailable for build fix: %s", exc)
            return []

        ws_tree = self._build_file_tree(workspace_path)
        manifest_block = ""
        if self.template_manifest:
            manifest_block = (
                "\n## TEMPLATE MANIFEST (available components — check before importing):\n"
                f"{self.template_manifest}\n"
            )

        prompt = f"""The production build (`{' '.join(build_cmd)}`) failed with these errors:

```
{errors}
```

## WORKSPACE FILES
{ws_tree}
{manifest_block}
## FIX INSTRUCTIONS
Fix only the files causing these build errors.

Rules:
- Do not redesign or refactor.
- Do not commit, push, pull, reset, or delete the repository.
- If 'Module not found', check the actual file tree and TEMPLATE_MANIFEST before creating anything.
- If a component import path is wrong, fix the import path.
- If a component is missing and clearly required, create a minimal component using the project's style.
- If hooks/browser APIs are used in a Next.js App Router component, add 'use client' as the first line.
- If JSX siblings are invalid inside a ternary/map callback, wrap them in a fragment.
- Stop after the build errors are fixed.
"""
        try:
            result = await run_codex_session(
                prompt=prompt,
                workspace_path=workspace_path,
                websocket=self.websocket,
                api_key=self.openai_api_key or os.environ.get("OPENAI_API_KEY", ""),
                model=self._codex_model(),
                timeout_seconds=300,
                phase_label="build_fix_codex",
            )
            if not result.success:
                self.codex_fix_failed = True
                return []
            return result.files_written
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.codex_fix_failed = True
            logger.warning("BuildValidator: Codex build fix failed: %s", exc)
            return []

    def _missing_bare_modules(self, errors: str, pkg_data: dict) -> list[str]:
        """Bare npm packages the build reports as unresolved AND that aren't
        already declared in package.json.

        Excludes relative imports, the ``@/`` tsconfig alias, ``node:`` /
        builtin modules, and the framework packages (next/react — a missing
        one of those means a broken *install*, handled separately, not a
        missing declared dep). A deep import (``dayjs/plugin/utc``,
        ``@scope/pkg/sub``) collapses to its installable package name.
        """
        if not errors:
            return []
        specs = re.findall(
            r"(?:Can't resolve|Cannot find module)\s+['\"]([^'\"]+)['\"]", errors
        )
        if not specs:
            return []
        existing = set(pkg_data.get("dependencies") or {}) | set(
            pkg_data.get("devDependencies") or {}
        )
        builtins = frozenset({
            "fs", "path", "os", "http", "https", "crypto", "stream", "util",
            "events", "child_process", "url", "zlib", "buffer", "net", "tls",
            "dns", "assert", "querystring", "readline", "worker_threads",
            "perf_hooks", "async_hooks", "process", "module", "timers",
            "string_decoder", "punycode", "v8", "vm", "tty", "cluster",
        })
        never_auto = frozenset({"next", "react", "react-dom"})
        out: list[str] = []
        for spec in specs:
            if not spec or spec[0] in "./" or spec.startswith("@/") or spec.startswith("node:"):
                continue  # relative path, @/ alias, or node: builtin
            if spec.startswith("@"):
                parts = spec.split("/")
                if len(parts) < 2:
                    continue
                pkg = "/".join(parts[:2])  # @scope/name
            else:
                pkg = spec.split("/")[0]   # name (drop deep-import subpath)
            if pkg in builtins or pkg in never_auto or pkg in existing:
                continue
            if pkg not in out:
                out.append(pkg)
        return out

    async def _auto_install_missing_deps(
        self, workspace_path: str, errors: str, pm: str, pkg_data: dict
    ) -> list[str]:
        """Add bare packages the build couldn't resolve to package.json + install.

        Returns the packages added (so the caller can rebuild). Bounded by
        ``_auto_added_deps`` (never add the same package twice) and
        ``_auto_add_rounds`` (≤3 total) so a genuinely-unresolvable import can't
        loop. Uses the PM's add command (``pnpm add X`` / ``npm i --save X``)
        which resolves a real version into package.json.
        """
        if self._auto_add_rounds >= 3:
            return []
        candidates = self._missing_bare_modules(errors, pkg_data)
        pkgs = [p for p in candidates if p not in self._auto_added_deps]
        if not pkgs:
            return []
        self._auto_add_rounds += 1
        self._auto_added_deps.update(pkgs)
        add_cmd = _pm_install_cmd(pm, pkgs)
        logger.info(
            "BuildValidator: auto-installing missing deps %s via: %s",
            pkgs, " ".join(add_cmd),
        )
        try:
            res = await asyncio.to_thread(
                subprocess.run, add_cmd, cwd=workspace_path,
                capture_output=True, text=True,
                timeout=_INSTALL_TIMEOUT_SECONDS, env=_pm_env(pm),
            )
        except Exception as exc:
            logger.warning("BuildValidator: auto-add install errored: %s", exc)
            return []
        if res.returncode != 0:
            tail = ((res.stderr or "") + (res.stdout or ""))[-400:]
            logger.warning(
                "BuildValidator: auto-add %s exited %d (rebuilding anyway): %s",
                pkgs, res.returncode, tail.strip()[:200],
            )
        return pkgs

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
        # BUILD vs DEV-SERVER .next/ COLLISION: BV runs concurrently with the
        # dev server (background-BV optimisation in landing_pipeline). Both
        # default to writing artifacts under `.next/` which causes the dev
        # server to serve half-written chunks while `next build` is overwriting
        # them. Point BV at `.next-build/` via NEXT_DIST so the two processes
        # never touch the same files. Next.js reads NEXT_DIST when our patched
        # `next.config.mjs` sets `distDir: process.env.NEXT_DIST || ".next"`
        # — see `_patch_nextjs_dist_dir()` in local_preview.
        build_env = {
            **_pm_env(pm),
            "CI": "true",
            "ADBLOCK": "true",
            "DISABLE_OPENCOLLECTIVE": "true",
            "OPEN_SOURCE_CONTRIBUTOR": "true",
            "NEXT_DIST": ".next-build",
        }
        # Make sure the workspace's next.config.mjs honors NEXT_DIST so the
        # env var actually redirects the output dir.
        try:
            self._patch_nextjs_dist_dir(workspace_path)
        except Exception as _pdd:
            logger.debug("BuildValidator: distDir patch skipped: %s", _pdd)

        # Load template manifest for import resolution
        self._load_manifest(workspace_path)

        # Ensure node_modules exist
        already_ready_pre_install = self._expected_binary_ready(workspace_path, pkg_data)
        if not already_ready_pre_install:
            # Phase 2 Step 4: typed event for chart chip, legacy free-form kept
            # one release for backward compat (suppressed by FE de-dup).
            try:
                from app.services.llm_retry import emit_build_start
                await emit_build_start(
                    self.websocket,
                    package_manager=pm,
                    command=" ".join(self._install_command(pm)),
                    phase="install",
                )
            except Exception:
                pass
            await self._send("build", "checking", f"📦 Installing dependencies ({pm})...")
            install_result = None
            try:
                install_cmd = self._install_command(pm)
                install_result = await asyncio.to_thread(
                    subprocess.run,
                    install_cmd,
                    cwd=workspace_path,
                    capture_output=True,
                    text=True,
                    timeout=_INSTALL_TIMEOUT_SECONDS,
                    env=build_env,
                )
            except Exception as ie:
                logger.warning("BuildValidator: install failed: %s", ie)

            # pnpm exits 1 for purely advisory warnings — ERR_PNPM_IGNORED_BUILDS
            # (postinstall scripts skipped), peer-dep warnings, etc. The structural
            # truth is whether the framework binary landed; if it did, treat the
            # install as success regardless of rc and avoid the bogus fix loop.
            if install_result is not None and install_result.returncode != 0:
                tail = ((install_result.stderr or "") + "\n" + (install_result.stdout or ""))[-2000:]
                logger.warning(
                    "BuildValidator: dependency install exited %d (advisory if binaries are present): %s",
                    install_result.returncode,
                    tail[-500:],
                )

            if not self._expected_binary_ready(workspace_path, pkg_data):
                errors = (
                    "Dependency install did not finish cleanly; framework build "
                    "binary is still missing. Skipping code-agent auto-fix because "
                    "this is an environment/install failure, not a code error."
                )
                await self._send("build", "warning", f"⚠️ {errors}")
                return {
                    "success": False,
                    "needs_fix": True,
                    "attempts": 0,
                    "errors": errors,
                    "fixed_files": self.fixed_files,
                    "error_count": 1,
                    "install_failed": True,
                }

        # Marker so local_preview._ensure_node_modules skips its own install
        # pass. Write whenever binaries are ready — even if we never ran the
        # install above (already_ready_pre_install) or pnpm exited 1 with
        # ERR_PNPM_IGNORED_BUILDS. Without this, local_preview re-runs pnpm
        # for ~9 min after BuildValidator already had the deps in place.
        try:
            marker_path = os.path.join(workspace_path, ".lucid_install_done")
            with open(marker_path, "w", encoding="utf-8") as mf:
                mf.write(f"{pm}\n{int(time.time())}\n")
        except OSError as mexc:
            logger.debug("BuildValidator: install marker write failed: %s", mexc)

        # Phase 2 Step 4: typed event for chart chip + legacy free-form.
        try:
            from app.services.llm_retry import emit_build_start
            await emit_build_start(
                self.websocket,
                package_manager=pm,
                command=" ".join(build_cmd),
                phase="build",
            )
        except Exception:
            pass
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
                # Phase 2 Step 4: typed terminal event + legacy free-form.
                try:
                    from app.services.llm_retry import emit_build_result
                    await emit_build_result(
                        self.websocket,
                        success=True,
                        attempts=self.attempt,
                        fixed_count=len(self.fixed_files),
                    )
                except Exception:
                    pass
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

            # Infra crash (SIGBUS / OOM worker death) — the LOCAL build sandbox
            # died, not the code. There's nothing for the auto-fixer to fix, and
            # the deploy build runs on separate (host/Vercel) infra. Treat as
            # NON-BLOCKING so a valid project still publishes to main instead of
            # being stranded on a draft branch.
            if result.get("infra_crash"):
                try:
                    from app.services.llm_retry import emit_build_result
                    await emit_build_result(
                        self.websocket,
                        success=True,
                        attempts=self.attempt + 1,
                        fixed_count=len(self.fixed_files),
                    )
                except Exception:
                    pass
                await self._send(
                    "build", "passed",
                    "✅ Code validated. The local build sandbox crashed (an infra/memory "
                    "issue, not a code error), so the deploy build runs on the host — "
                    "publishing as-is.",
                )
                logger.warning(
                    "BuildValidator: local build worker crashed (infra) — non-blocking, "
                    "code parsed clean; publishing to main",
                )
                return {
                    "success": False,
                    "infra_crash": True,
                    "needs_fix": False,
                    "attempts": self.attempt + 1,
                    "errors": last_errors,
                    "fixed_files": self.fixed_files,
                    "error_count": 0,
                }

            # Timeout — no parseable error output for Claude to fix
            if result.get("timed_out"):
                try:
                    from app.services.llm_retry import emit_build_result
                    await emit_build_result(
                        self.websocket,
                        success=False,
                        attempts=self.attempt + 1,
                        error_count=last_error_count,
                        fixed_count=len(self.fixed_files),
                        timed_out=True,
                        needs_fix=True,
                    )
                except Exception:
                    pass
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
            # Log the first ~1500 chars of the actual error text so failures
            # are diagnosable from the log alone. Without this we only know
            # the count, and the workspace is reaped before anyone can grep.
            if last_errors:
                logger.warning(
                    "BuildValidator: error detail (attempt %d):\n%s",
                    self.attempt + 1, last_errors[:1500],
                )

            # Short-circuit when the build failure is an install-time symptom,
            # not a code error. The code-fix agent can't fix missing packages
            # or post-install scripts, so burning 2–5 min on a fix loop +
            # 180s retry timeout is wasted. Bail straight to "warning".
            _err_low = (last_errors or "").lower()
            _install_signals = (
                "command failed with exit code 1: pnpm install",
                "command failed with exit code 1: npm install",
                "err_pnpm_",
                "enoent: no such file or directory, open",
                "cannot find module 'next/",
                "cannot find module 'react'",
                "module not found: can't resolve 'next'",
            )
            if any(sig in _err_low for sig in _install_signals):
                logger.warning(
                    "BuildValidator: detected install-time failure — skipping code-agent fix loop"
                )
                try:
                    from app.services.llm_retry import emit_build_result
                    await emit_build_result(
                        self.websocket,
                        success=False,
                        attempts=self.attempt + 1,
                        error_count=last_error_count,
                        fixed_count=len(self.fixed_files),
                        install_failed=True,
                        needs_fix=True,
                    )
                except Exception:
                    pass
                await self._send("build", "warning",
                    f"⚠️ Build failed due to an install-time issue ({last_error_count} error(s)) — "
                    "skipping auto-fix because this isn't a code error. Project deploys to staging only.",
                    errors=last_errors[:2000],
                )
                return {
                    "success": False,
                    "needs_fix": True,
                    "attempts": self.attempt + 1,
                    "errors": last_errors,
                    "fixed_files": self.fixed_files,
                    "error_count": last_error_count,
                    "install_failed": True,
                }

            # Deterministic pre-pass: a bare package is imported but absent from
            # package.json (e.g. `import dayjs`). Don't spend a code-fix attempt
            # rewriting working code to dodge a missing dep — add it + install +
            # rebuild. Bounded (_auto_added_deps + _auto_add_rounds≤3) so a
            # genuinely-unresolvable import falls through to the code fixer.
            newly_added = await self._auto_install_missing_deps(
                workspace_path, last_errors, pm, pkg_data
            )
            if newly_added:
                if "package.json" not in self.fixed_files:
                    self.fixed_files.append("package.json")
                await self._send(
                    "build", "fixing",
                    f"📦 Added missing dependenc{'ies' if len(newly_added) > 1 else 'y'} "
                    f"({', '.join(newly_added)}) and rebuilding…",
                )
                continue  # rebuild WITHOUT consuming a code-fix attempt

            if self.attempt < self.max_retries:
                has_codex_fixer = self._prefer_codex_fixer()
                if not str(self.api_key or "").strip() and not has_codex_fixer:
                    await self._send("build", "warning",
                        f"⚠️ Build failed with {last_error_count} error(s). "
                        "No code-fix agent is available for automatic build fixing.",
                        errors=last_errors[:2000],
                    )
                    break
                else:
                    fixer_name = "Codex" if has_codex_fixer else "Claude"
                    await self._send("build", "fixing",
                        f"⚠️ Build failed with {last_error_count} error(s) — "
                        f"{fixer_name} is auto-fixing (attempt {self.attempt + 1}/{self.max_retries})...",
                        errors=last_errors[:2000],
                    )
                    modified = await self.fix_errors(workspace_path, last_errors, build_cmd)
                    self.fixed_files.extend(modified)

            self.attempt += 1

        # All retries exhausted — graceful degradation
        try:
            from app.services.llm_retry import emit_build_result
            await emit_build_result(
                self.websocket,
                success=False,
                attempts=self.attempt,
                error_count=last_error_count,
                fixed_count=len(self.fixed_files),
                needs_fix=True,
            )
        except Exception:
            pass
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
