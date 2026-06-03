"""External project indexer for user-connected repositories.

Lucid-generated projects have known content files, manifests, and editable
metadata. User-connected repos do not. This scanner builds a deterministic
project briefing before any code agent edits an external repo, so the edit
path starts with framework, route, build, environment, and branch context
instead of guessing.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
from typing import Any

from app.config import logger

_MAX_FILES = 3500
_MAX_ROUTES = 80
_MAX_COMPONENT_DIRS = 24
_MAX_ENV_KEYS = 80
_SKIP_DIRS = {
    ".git",
    ".next",
    ".turbo",
    ".vercel",
    "node_modules",
    "dist",
    "build",
    "coverage",
    "__pycache__",
}
_PROTECTED_BRANCHES = {
    "main",
    "master",
    "production",
    "prod",
    "release",
    "stable",
}


def _detect_package_manager(workspace_path: str, default: str = "npm") -> str:
    for lock_file, pm in (
        ("pnpm-lock.yaml", "pnpm"),
        ("yarn.lock", "yarn"),
        ("bun.lockb", "bun"),
        ("package-lock.json", "npm"),
    ):
        if os.path.isfile(os.path.join(workspace_path, lock_file)):
            return pm if shutil.which(pm) else "npm"
    return default or "npm"


def is_external_project(validated: dict[str, Any]) -> bool:
    """True when the workspace belongs to a user-connected repo."""
    return not (
        validated.get("scratch_mode")
        or validated.get("new_project_mode")
        or validated.get("platform_repo_url")
        or validated.get("is_platform_owned")
        or validated.get("platform_owned")
    )


async def index_external_project(
    *,
    workspace_path: str,
    validated: dict[str, Any],
    task: str = "",
    websocket: Any = None,
) -> dict[str, Any]:
    """Build and persist a deterministic project index for an external repo."""
    index = build_external_project_index(
        workspace_path=workspace_path,
        validated=validated,
        task=task,
    )
    branch_result = await prepare_external_edit_branch(
        workspace_path=workspace_path,
        validated=validated,
        index=index,
        task=task,
        websocket=websocket,
    )
    index["branch_strategy"] = branch_result
    validated["branch"] = branch_result.get("active_branch") or validated.get("branch") or "main"
    validated["package_manager"] = (index.get("project") or {}).get("package_manager") or validated.get("package_manager", "npm")
    validated["external_project_index"] = index
    validated["external_project_brief"] = render_external_project_brief(index)
    _persist_index(workspace_path, index, validated["external_project_brief"])
    return index


def build_external_project_index(
    *,
    workspace_path: str,
    validated: dict[str, Any],
    task: str = "",
) -> dict[str, Any]:
    paths = _walk_paths(workspace_path)
    pkg = _load_package_json(workspace_path)
    deps = {
        **(pkg.get("dependencies") or {}),
        **(pkg.get("devDependencies") or {}),
    }
    scripts = pkg.get("scripts") or {}
    pm = _detect_package_manager(workspace_path, validated.get("package_manager", "npm"))
    framework = _detect_framework(paths, deps)
    routes = _detect_routes(paths, framework)
    component_dirs = _detect_component_dirs(paths)
    env_info = _detect_env_style(workspace_path, paths)
    styling = _detect_styling(paths, deps)
    backend = _detect_backend(paths, deps)
    current_branch = _git_current_branch(workspace_path) or validated.get("branch") or ""
    selected_branch = validated.get("branch") or current_branch or "main"

    return {
        "schema_version": 1,
        "kind": "external_project_index",
        "project": {
            "framework": framework,
            "language": _detect_language(paths),
            "package_manager": pm,
            "workspace_files_scanned": len(paths),
        },
        "scripts": {
            "build": scripts.get("build", ""),
            "dev": scripts.get("dev", ""),
            "start": scripts.get("start", ""),
            "test": scripts.get("test", ""),
            "lint": scripts.get("lint", ""),
        },
        "commands": {
            "install": _install_command(pm),
            "build": f"{pm} run build" if scripts.get("build") else "",
            "dev": f"{pm} run dev" if scripts.get("dev") else "",
            "lint": f"{pm} run lint" if scripts.get("lint") else "",
            "test": f"{pm} test" if scripts.get("test") else "",
        },
        "routes": routes,
        "component_dirs": component_dirs,
        "styling": styling,
        "backend": backend,
        "env": env_info,
        "git": {
            "provider": validated.get("git_provider", ""),
            "selected_branch": selected_branch,
            "current_branch": current_branch,
            "repo_url_present": bool(validated.get("repo_url")),
        },
        "task_hint": (task or "")[:500],
    }


async def prepare_external_edit_branch(
    *,
    workspace_path: str,
    validated: dict[str, Any],
    index: dict[str, Any],
    task: str,
    websocket: Any = None,
) -> dict[str, Any]:
    """Create a safe edit branch for external repos when needed."""
    selected = str(
        validated.get("branch")
        or (index.get("git") or {}).get("current_branch")
        or "main"
    ).strip() or "main"
    lower = selected.lower()
    result = {
        "mode": "existing_branch",
        "base_branch": selected,
        "active_branch": selected,
        "created_branch": "",
        "reason": "Selected branch is already an edit branch.",
    }

    if lower.startswith(("lucid/", "feature/", "fix/", "bugfix/", "chore/")):
        return result
    if lower not in _PROTECTED_BRANCHES:
        result["reason"] = "Selected branch does not look like a production branch."
        return result

    new_branch = _derive_work_branch(task, selected)
    cmd = ["git", "checkout", "-B", new_branch]
    try:
        proc = await asyncio.to_thread(
            subprocess.run,
            cmd,
            cwd=workspace_path,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception as exc:
        logger.warning("external_project_index: branch creation failed: %s", exc)
        result["mode"] = "selected_branch"
        result["reason"] = f"Could not create safe branch: {str(exc)[:120]}"
        return result

    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "unknown git error").strip()[:200]
        logger.warning("external_project_index: git checkout -B failed: %s", err)
        result["mode"] = "selected_branch"
        result["reason"] = f"Could not create safe branch: {err}"
        return result

    result.update({
        "mode": "safe_work_branch",
        "active_branch": new_branch,
        "created_branch": new_branch,
        "reason": f"Selected branch '{selected}' looks production-like, so edits use a Lucid work branch.",
    })
    try:
        if websocket is not None:
            await websocket.send_json({
                "type": "progress",
                "message": f"🌿 External repo edits will use safe branch: {new_branch}",
            })
    except Exception:
        pass
    return result


def render_external_project_brief(index: dict[str, Any]) -> str:
    """Render a compact briefing for code agents."""
    project = index.get("project") or {}
    scripts = index.get("scripts") or {}
    commands = index.get("commands") or {}
    git = index.get("git") or {}
    branch = index.get("branch_strategy") or {}
    env = index.get("env") or {}
    backend = index.get("backend") or {}
    styling = index.get("styling") or {}
    routes = index.get("routes") or []
    component_dirs = index.get("component_dirs") or []

    lines = [
        "## External Project Index",
        "",
        f"- Framework: {project.get('framework') or 'unknown'}",
        f"- Language: {project.get('language') or 'unknown'}",
        f"- Package manager: {project.get('package_manager') or 'unknown'}",
        f"- Build command: {commands.get('build') or 'no build script detected'}",
        f"- Dev command: {commands.get('dev') or 'no dev script detected'}",
        f"- Test command: {commands.get('test') or 'no test script detected'}",
        f"- Lint command: {commands.get('lint') or 'no lint script detected'}",
        f"- Active edit branch: {branch.get('active_branch') or git.get('selected_branch') or 'unknown'}",
        f"- Branch strategy: {branch.get('reason') or 'Use selected branch.'}",
        "",
        "### Scripts",
        f"- build: {scripts.get('build') or '-'}",
        f"- dev: {scripts.get('dev') or '-'}",
        f"- start: {scripts.get('start') or '-'}",
        f"- test: {scripts.get('test') or '-'}",
        f"- lint: {scripts.get('lint') or '-'}",
        "",
        "### Routes",
    ]
    if routes:
        for route in routes[:30]:
            lines.append(f"- {route.get('route')}: {route.get('file')}")
    else:
        lines.append("- No route files detected. Inspect router configuration before adding pages.")

    lines.extend(["", "### Component Directories"])
    if component_dirs:
        for item in component_dirs[:12]:
            lines.append(f"- {item.get('dir')} ({item.get('files')} files)")
    else:
        lines.append("- No common component directory detected.")

    lines.extend([
        "",
        "### Styling And Data",
        f"- Styling: {', '.join(styling.get('systems') or []) or 'unknown'}",
        f"- Backend/data: {', '.join(backend.get('systems') or []) or 'none detected'}",
        f"- Env files: {', '.join(env.get('files') or []) or 'none detected'}",
        f"- Public env prefixes: {', '.join(env.get('public_prefixes') or []) or 'none detected'}",
        "",
        "Editing rules for this external repo:",
        "- Follow existing framework/router conventions from the files above.",
        "- Do not assume Lucid-generated content JSON or editable wrappers exist.",
        "- Do not read or print .env values; use key names only.",
        "- Keep edits on the active branch named above.",
    ])
    return "\n".join(lines) + "\n"


def _walk_paths(workspace_path: str) -> list[str]:
    out: list[str] = []
    for root, dirs, files in os.walk(workspace_path):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for fname in files:
            rel = os.path.relpath(os.path.join(root, fname), workspace_path).replace(os.sep, "/")
            out.append(rel)
            if len(out) >= _MAX_FILES:
                return sorted(out)
    return sorted(out)


def _load_package_json(workspace_path: str) -> dict[str, Any]:
    path = os.path.join(workspace_path, "package.json")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _detect_framework(paths: list[str], deps: dict[str, Any]) -> str:
    names = set(deps.keys())
    path_set = set(paths)
    if "next" in names or "next.config.js" in path_set or "next.config.mjs" in path_set:
        return "nextjs"
    if "nuxt" in names or "nuxt.config.ts" in path_set or "nuxt.config.js" in path_set:
        return "nuxt"
    if "svelte" in names or "svelte.config.js" in path_set:
        return "sveltekit" if "@sveltejs/kit" in names else "svelte"
    if "vite" in names or any(p.startswith("vite.config.") for p in paths):
        if "vue" in names:
            return "vue-vite"
        if "react" in names:
            return "react-vite"
        return "vite"
    if "react-scripts" in names:
        return "create-react-app"
    if "astro" in names or "astro.config.mjs" in path_set:
        return "astro"
    if "react" in names:
        return "react"
    if "vue" in names:
        return "vue"
    return "unknown"


def _detect_language(paths: list[str]) -> str:
    ts = sum(1 for p in paths if p.endswith((".ts", ".tsx")))
    js = sum(1 for p in paths if p.endswith((".js", ".jsx", ".mjs", ".cjs")))
    if ts and ts >= js:
        return "typescript"
    if js:
        return "javascript"
    return "unknown"


def _detect_routes(paths: list[str], framework: str) -> list[dict[str, str]]:
    routes: list[dict[str, str]] = []
    for rel in paths:
        route = ""
        if re.match(r"^(src/)?app/(.+/)?page\.(js|jsx|ts|tsx|mdx)$", rel):
            body = re.sub(r"^(src/)?app/", "", rel)
            body = re.sub(r"/?page\.(js|jsx|ts|tsx|mdx)$", "", body)
            parts = [p for p in body.split("/") if p and not (p.startswith("(") and p.endswith(")"))]
            route = "/" + "/".join(parts) if parts else "/"
        elif re.match(r"^(src/)?pages/.+\.(js|jsx|ts|tsx|mdx)$", rel):
            body = re.sub(r"^(src/)?pages/", "", rel)
            body = re.sub(r"\.(js|jsx|ts|tsx|mdx)$", "", body)
            if body in {"index", "_app", "_document", "404", "500"}:
                route = "/" if body == "index" else f"/{body}"
            elif body.endswith("/index"):
                route = "/" + body[: -len("/index")]
            else:
                route = "/" + body
        elif rel.startswith("src/routes/") and rel.endswith((".svelte", ".js", ".ts")):
            body = rel[len("src/routes/"):]
            body = re.sub(r"/?\+page\.(svelte|js|ts)$", "", body)
            route = "/" + body.strip("/") if body else "/"
        elif rel.startswith("src/pages/") and framework in {"react-vite", "vite", "vue-vite"}:
            body = rel[len("src/pages/"):]
            body = re.sub(r"\.(js|jsx|ts|tsx|vue)$", "", body)
            route = "/" + ("" if body.lower() == "index" else body)
        if route:
            routes.append({"route": route.replace("//", "/"), "file": rel})
        if len(routes) >= _MAX_ROUTES:
            break
    return routes


def _detect_component_dirs(paths: list[str]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for rel in paths:
        if not rel.endswith((".js", ".jsx", ".ts", ".tsx", ".vue", ".svelte")):
            continue
        parts = rel.split("/")
        for idx, part in enumerate(parts[:-1]):
            if part.lower() in {"components", "ui", "widgets", "sections"}:
                directory = "/".join(parts[: idx + 1])
                counts[directory] = counts.get(directory, 0) + 1
                break
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [{"dir": d, "files": n} for d, n in ranked[:_MAX_COMPONENT_DIRS]]


def _detect_styling(paths: list[str], deps: dict[str, Any]) -> dict[str, Any]:
    systems: list[str] = []
    names = set(deps.keys())
    path_set = set(paths)
    if any(p.startswith("tailwind.config.") for p in paths) or "tailwindcss" in names:
        systems.append("tailwind")
    if "components.json" in path_set:
        systems.append("shadcn-ui")
    if "@mui/material" in names:
        systems.append("mui")
    if "@chakra-ui/react" in names:
        systems.append("chakra-ui")
    if "styled-components" in names:
        systems.append("styled-components")
    if any(p.endswith(".module.css") for p in paths):
        systems.append("css-modules")
    if any(p.endswith((".scss", ".sass")) for p in paths):
        systems.append("sass")
    if any(p.endswith(".css") for p in paths) and "plain-css" not in systems:
        systems.append("plain-css")
    return {"systems": systems}


def _detect_backend(paths: list[str], deps: dict[str, Any]) -> dict[str, Any]:
    names = set(deps.keys())
    systems: list[str] = []
    if "@supabase/supabase-js" in names or any("supabase" in p.lower() for p in paths):
        systems.append("supabase")
    if "firebase" in names:
        systems.append("firebase")
    if "prisma" in names or "@prisma/client" in names or any(p.startswith("prisma/") for p in paths):
        systems.append("prisma")
    if "drizzle-orm" in names:
        systems.append("drizzle")
    if "express" in names:
        systems.append("express")
    if any(re.match(r"^(src/)?app/api/.+/route\.(js|ts)$", p) for p in paths):
        systems.append("next-api-routes")
    return {"systems": sorted(set(systems))}


def _detect_env_style(workspace_path: str, paths: list[str]) -> dict[str, Any]:
    env_files = [
        p for p in paths
        if os.path.basename(p).startswith(".env")
        and "/node_modules/" not in p
    ][:12]
    keys: list[str] = []
    for rel in env_files:
        full = os.path.join(workspace_path, rel)
        try:
            with open(full, "r", encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    match = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
                    if match:
                        key = match.group(1)
                        if key not in keys:
                            keys.append(key)
                    if len(keys) >= _MAX_ENV_KEYS:
                        break
        except Exception:
            continue
    public_prefixes = sorted({
        prefix
        for key in keys
        for prefix in ("NEXT_PUBLIC_", "VITE_", "PUBLIC_")
        if key.startswith(prefix)
    })
    secret_like = [k for k in keys if any(tok in k.lower() for tok in ("secret", "token", "key", "password"))]
    return {
        "files": env_files,
        "keys": keys[:_MAX_ENV_KEYS],
        "secret_like_keys": secret_like[:20],
        "public_prefixes": public_prefixes,
    }


def _install_command(pm: str) -> str:
    if pm == "pnpm":
        return "pnpm install --no-frozen-lockfile"
    if pm == "yarn":
        return "yarn install --non-interactive"
    if pm == "bun":
        return "bun install"
    return "npm install --no-audit --no-fund"


def _git_current_branch(workspace_path: str) -> str:
    try:
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=workspace_path,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return (result.stdout or "").strip() if result.returncode == 0 else ""
    except Exception:
        return ""


def _derive_work_branch(task: str, base_branch: str) -> str:
    raw = re.sub(r"[^a-zA-Z0-9]+", "-", (task or "edit").lower()).strip("-")
    slug = raw[:36].strip("-") or "edit"
    digest = hashlib.sha1(f"{base_branch}:{task}".encode("utf-8")).hexdigest()[:8]
    return f"lucid/{slug}-{digest}"


def _persist_index(workspace_path: str, index: dict[str, Any], brief: str) -> None:
    try:
        git_lucid_dir = os.path.join(workspace_path, ".git", "lucid")
        if os.path.isdir(os.path.join(workspace_path, ".git")):
            lucid_dir = git_lucid_dir
        else:
            _exclude_lucid_dir(workspace_path)
            lucid_dir = os.path.join(workspace_path, ".lucid")
        os.makedirs(lucid_dir, exist_ok=True)
        with open(os.path.join(lucid_dir, "external_project_index.json"), "w", encoding="utf-8") as fh:
            json.dump(index, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        with open(os.path.join(lucid_dir, "external_project_brief.md"), "w", encoding="utf-8") as fh:
            fh.write(brief)
    except Exception as exc:
        logger.warning("external_project_index: persist failed: %s", exc)


def _exclude_lucid_dir(workspace_path: str) -> None:
    """Keep Lucid's private index files out of user-connected repos."""
    info_dir = os.path.join(workspace_path, ".git", "info")
    if not os.path.isdir(info_dir):
        return
    exclude_path = os.path.join(info_dir, "exclude")
    try:
        existing = ""
        if os.path.isfile(exclude_path):
            with open(exclude_path, "r", encoding="utf-8", errors="ignore") as fh:
                existing = fh.read()
        if ".lucid/" not in existing:
            with open(exclude_path, "a", encoding="utf-8") as fh:
                if existing and not existing.endswith("\n"):
                    fh.write("\n")
                fh.write(".lucid/\n")
    except Exception as exc:
        logger.debug("external_project_index: could not update git exclude: %s", exc)
