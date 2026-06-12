"""Build verification + surgical LLM fix loop.

Extracted from project_generator.py (god-module split).

  _ensure_node_modules()  — install deps once per workspace (skip-marker aware)
  verify_and_fix_build()  — run the production build, parse errors, ask
                            Claude for surgical file fixes, rebuild
                            (MAX_FIX_ATTEMPTS rounds)

Workspace-introspection helpers (_build_file_tree, _read_manifest,
_detect_pm, _emit_file_writes) still live in project_generator and are
imported lazily inside the functions to avoid an import cycle —
project_generator re-exports verify_and_fix_build for its callers.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import time

from app.services.llm_json_client import DEFAULT_MODEL, call_claude_for_json
from app.services.project_writer import write_files_from_json
from app.services.ws_emit import _ws_send

logger = logging.getLogger("lucid.project_generator")

MAX_FIX_ATTEMPTS = 1


def _lazy_helpers():
    """Import the workspace helpers from project_generator at call time.

    Module-level import would be circular: project_generator re-exports
    verify_and_fix_build from this module.
    """
    from app.services.project_generator import (
        _build_file_tree,
        _detect_pm,
        _emit_file_writes,
        _read_manifest,
    )
    return _build_file_tree, _detect_pm, _emit_file_writes, _read_manifest


# ╔══════════════════════════════════════════════════════════════╗
# ║  STEP 4 — verify_and_fix_build()                            ║
# ║  Run build, read errors, call Opus for surgical fixes        ║
# ╚══════════════════════════════════════════════════════════════╝

async def _ensure_node_modules(
    workspace_path: str,
    pm: str,
    websocket,
) -> bool:
    """Install dependencies if node_modules is missing.

    The template clone brings package.json + lockfile but NOT node_modules,
    so the very first build attempt fails with "Command 'next' / 'vite'
    not found" until we install. Detect "node_modules is missing or empty",
    run install with the workspace's preferred package manager, fall back
    to npm if pnpm/yarn/bun aren't available.

    Template-agnostic: triggers on any project (Next.js, Vite, etc.) by
    checking node_modules itself rather than a specific binary, so the
    react-admin / vue-admin paths get the install too.

    Returns True if node_modules looks usable (or install succeeded),
    False on install failure. Caller still tries to build — some failures
    (e.g. network blips on a transitive dep) don't block the actual build
    binary from existing.
    """
    _build_file_tree, _detect_pm, _emit_file_writes, _read_manifest = _lazy_helpers()
    nm_dir = os.path.join(workspace_path, "node_modules")
    bin_dir = os.path.join(nm_dir, ".bin")
    # Skip install when node_modules has actual contents — `os.listdir`
    # check is cheap and avoids a 8-15s no-op install on subsequent runs.
    try:
        if os.path.isdir(bin_dir) and os.listdir(bin_dir):
            return True
    except OSError:
        pass

    import shutil
    install_pm = pm if shutil.which(pm) else "npm"
    if install_pm == "pnpm":
        install_cmd = ["pnpm", "install", "--frozen-lockfile=false", "--reporter=silent"]
    elif install_pm == "yarn":
        install_cmd = ["yarn", "install", "--silent"]
    elif install_pm == "bun":
        install_cmd = ["bun", "install"]
    else:
        install_cmd = ["npm", "install", "--no-audit", "--no-fund", "--loglevel=error"]

    await _ws_send(websocket, "progress", f"📦 Installing dependencies ({install_pm})…")
    logger.info("Build verify: running '%s' in %s", " ".join(install_cmd), workspace_path)
    t0 = time.perf_counter()
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            install_cmd,
            cwd=workspace_path,
            capture_output=True,
            text=True,
            timeout=240,
            env={**os.environ, "CI": "true", "ADBLOCK": "true",
                 "DISABLE_OPENCOLLECTIVE": "true", "OPEN_SOURCE_CONTRIBUTOR": "true"},
        )
    except subprocess.TimeoutExpired:
        await _ws_send(websocket, "progress", "⚠️ Dependency install timed out (4 min)")
        logger.error("Dependency install timed out after 240s")
        return False
    except FileNotFoundError as exc:
        logger.error("Install command not found: %s", exc)
        return False

    elapsed = time.perf_counter() - t0
    if result.returncode != 0:
        tail = ((result.stderr or "") + (result.stdout or ""))[-800:]
        logger.error("Dependency install failed (exit=%d, %.1fs):\n%s",
                     result.returncode, elapsed, tail)
        await _ws_send(websocket, "progress",
                       f"⚠️ {install_pm} install exited {result.returncode} after {elapsed:.0f}s")
        # Even on non-zero exit, node_modules may be sufficiently populated to
        # build — fall through and let the build attempt prove it.
    else:
        await _ws_send(websocket, "progress", f"✅ Dependencies installed in {elapsed:.0f}s")
        logger.info("Dependencies installed via %s in %.1fs", install_pm, elapsed)

    try:
        return os.path.isdir(bin_dir) and bool(os.listdir(bin_dir))
    except OSError:
        return False


async def verify_and_fix_build(
    workspace_path: str,
    api_key: str,
    websocket=None,
    max_fix_attempts: int = MAX_FIX_ATTEMPTS,
) -> bool:
    """Run npm/pnpm build and auto-fix errors with Claude.

    Pass max_fix_attempts=0 to do a check-only run (no Claude fix calls).
    Returns True if build passes (with or without fixes).
    """
    _build_file_tree, _detect_pm, _emit_file_writes, _read_manifest = _lazy_helpers()
    pm = _detect_pm(workspace_path)

    # For Next.js projects use --no-lint to skip ESLint during build.
    # Our post-generation fixers already handle all ESLint issues (unescaped
    # entities, img→Image, use client), so running ESLint again just wastes 20-30s.
    _is_nextjs_project = (
        os.path.exists(os.path.join(workspace_path, "next.config.mjs"))
        or os.path.exists(os.path.join(workspace_path, "next.config.js"))
    )
    # Install node_modules once per workspace if missing. Template-agnostic
    # so Vite/admin templates (react-admin, vue-admin) get deps installed
    # too — otherwise their `vite build` fails with "command not found".
    await _ensure_node_modules(workspace_path, pm, websocket)

    build_cmd = ([pm, "exec", "next", "build", "--no-lint"] if _is_nextjs_project
                 else [pm, "run", "build"])

    for attempt in range(1, max_fix_attempts + 2):  # +1 for initial + N fixes
        await _ws_send(websocket, "progress", f"🔨 Build attempt {attempt}...")

        try:
            result = await asyncio.to_thread(
                subprocess.run,
                build_cmd,
                cwd=workspace_path,
                capture_output=True,
                text=True,
                timeout=120,
                env={**os.environ, "CI": "true", "NODE_ENV": "production"},
            )
        except subprocess.TimeoutExpired:
            await _ws_send(websocket, "progress", "⚠️ Build timed out")
            return False

        if result.returncode == 0:
            await _ws_send(websocket, "progress", "✅ Build passed!")
            return True

        # Build failed — extract errors
        errors = (result.stderr or "") + "\n" + (result.stdout or "")
        # Trim to last 3000 chars (most relevant errors are at the end)
        errors = errors[-3000:] if len(errors) > 3000 else errors

        if attempt > max_fix_attempts:
            await _ws_send(websocket, "progress", f"⚠️ Build still failing after {max_fix_attempts} fix attempt(s)")
            logger.error("Build failed after %d fix attempt(s). Errors:\n%s", max_fix_attempts, errors[:1000])
            return False

        # Ask Opus to fix the specific errors
        await _ws_send(websocket, "progress", f"🔧 Fixing build errors (attempt {attempt})...")

        file_tree = _build_file_tree(workspace_path)
        manifest = _read_manifest(workspace_path)

        fix_prompt = f"""Fix ALL build errors below. Return ONLY the files that need changes.

BUILD ERRORS:
{errors}

CURRENT FILE TREE:
{file_tree}

TEMPLATE MANIFEST (correct import paths):
{manifest[:10000]}

RULES:
- Return ONLY the files that need fixes — NOT the whole project
- If "Module not found" → fix the import path using TEMPLATE_MANIFEST paths
- If "is not defined" → add the missing import statement
- If "'use client'" missing → add it as first line
- If duplicate export → fix the export
- Keep all existing functionality — only fix what's broken
- Use the EXACT import paths from TEMPLATE_MANIFEST.md

Return JSON: {{"files": [{{"path": "...", "content": "..."}}]}}
"""

        fix_result = await call_claude_for_json(
            system_prompt="You are an expert build error fixer. Fix ONLY the broken files. Return minimal JSON.",
            user_prompt=fix_prompt,
            api_key=api_key,
            websocket=websocket,
            max_tokens=32000,
            model=DEFAULT_MODEL,
        )

        if fix_result:
            written = write_files_from_json(fix_result, workspace_path)
            await _emit_file_writes(websocket, written, action="fix")
            await _ws_send(websocket, "progress", f"🔧 Fixed {len(written)} files, rebuilding...")
        else:
            await _ws_send(websocket, "progress", "⚠️ Could not generate fix")
            return False

    return False
