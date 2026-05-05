"""Deterministic Next.js route-shell builder.

For each page in project_schema["pages"], emits the tiny `page.js`
that imports the page component and exports it as the default + sets
Next.js metadata. Pattern matches what Phase 1 currently produces by
hand for every page.

Skips:
  - The home page (path == "/" or "" — Phase 1 section 5 handles it
    because the home is the landing page with sections, not a thin
    shell that imports a single component).
  - Dynamic routes with path params (e.g. "/products/[id]") — those
    have non-trivial generation requirements (generateStaticParams,
    typed params) and are rare on consumer/marketing sites.
  - Hash anchors (e.g. "/about#mill") — those aren't separate routes,
    they're scroll targets inside another page.

Why deterministic: each shell is 8-12 lines that the LLM has been
producing word-for-word from a template. With 5-10 pages that's
50-120 lines of pure template Phase 1 currently generates. Removing
them shrinks Phase 1's output budget by ~10-15%.

Pure module — no I/O, no LLM, no async. The wire-in writes the files.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


_VALID_ROUTE_SEGMENT = re.compile(r"^[a-zA-Z0-9_-]+$")
_VALID_COMPONENT_NAME = re.compile(r"^[A-Z][A-Za-z0-9_]*$")


def _coerce(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _route_to_dir(path: str) -> str | None:
    """Convert a Next.js route path to its src/app directory location.

    Returns None for paths that are not safe to materialize as a static
    route directory (home, dynamic params, hashes, query strings,
    invalid characters).
    """
    p = (path or "").strip()
    if not p or p == "/":
        return None
    # Drop fragments / queries
    p = p.split("#", 1)[0].split("?", 1)[0]
    if not p or p == "/":
        return None
    if "[" in p or "]" in p:
        return None  # Dynamic param routes — skip
    rel = p.strip("/")
    if not rel:
        return None
    # Validate every segment looks like a normal directory name
    for seg in rel.split("/"):
        if not _VALID_ROUTE_SEGMENT.match(seg):
            return None
    return rel


def _safe_component(name: str, fallback: str) -> str:
    n = _coerce(name)
    if n and _VALID_COMPONENT_NAME.match(n):
        return n
    # Build a fallback PascalCase component name from the route segment
    parts = re.split(r"[^A-Za-z0-9]+", _coerce(fallback) or "Page")
    pascal = "".join(p[:1].upper() + p[1:] for p in parts if p)
    if not pascal or not _VALID_COMPONENT_NAME.match(pascal):
        pascal = "Page"
    if not pascal.endswith("Page"):
        pascal = pascal + "Page"
    return pascal


def build_route_shell(*, component: str, title: str, brand_name: str) -> str:
    """Render one src/app/<route>/page.js source string."""
    metadata_title = f"{title} | {brand_name}" if (title and brand_name) else (title or brand_name or "Page")
    return (
        f'import {component} from "@/components/pages/{component}";\n'
        f'\n'
        f'export const metadata = {{\n'
        f'  title: {json.dumps(metadata_title, ensure_ascii=False)},\n'
        f'}};\n'
        f'\n'
        f'export default function Page() {{\n'
        f'  return <{component} />;\n'
        f'}}\n'
    )


def plan_route_shells(project_schema: dict) -> list[dict]:
    """Return a list of {rel_path, contents, route, component} for every
    page in the schema that's safe to emit as a deterministic shell.

    Caller writes each (rel_path, contents) pair to disk inside the
    workspace. Pure function — no I/O.
    """
    pages = project_schema.get("pages") or []
    brand_name = _coerce((project_schema.get("brand") or {}).get("name")) or "Project"
    seen_paths: set[str] = set()
    plan: list[dict] = []
    for page in pages:
        if not isinstance(page, dict):
            continue
        path = _coerce(page.get("path"))
        rel_dir = _route_to_dir(path)
        if not rel_dir:
            continue
        if rel_dir in seen_paths:
            continue
        seen_paths.add(rel_dir)
        title = _coerce(page.get("title"))
        component = _safe_component(_coerce(page.get("component")), rel_dir)
        contents = build_route_shell(
            component=component, title=title, brand_name=brand_name,
        )
        plan.append({
            "rel_path": f"src/app/{rel_dir}/page.js",
            "contents": contents,
            "route": "/" + rel_dir,
            "component": component,
        })
    return plan
