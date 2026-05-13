"""Deterministic Next.js homepage builder.

Generates `src/app/page.js` (or `src/pages/index.jsx` for Vite) by composing
section components from the project_schema's `sections` list. Replaces what
Phase 1 currently generates with a single Claude call.

Pattern: each section in the schema has a `type` (hero, features, gallery,
story, testimonials, cta, etc.) which maps 1:1 to a component file in
`src/components/sections/`. The homepage is just a thin composition that
imports each component and renders them in order inside the page layout.

The actual section components are written by Phase 2 — the homepage just
trusts the contract that they exist at the conventional path.

Pure module — no I/O, no LLM, no async. Caller writes the file.
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


_VALID_COMPONENT_NAME = re.compile(r"^[A-Z][A-Za-z0-9_]*$")


def _coerce(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _to_pascal(text: str) -> str:
    """Convert any string to PascalCase component name."""
    parts = re.split(r"[^A-Za-z0-9]+", text or "")
    pascal = "".join(p[:1].upper() + p[1:] for p in parts if p)
    return pascal or ""


def _section_to_component(section: dict) -> str | None:
    """Resolve a schema section to a component name.

    Priority:
      1. Explicit `component` field on the section
      2. `name` field, PascalCased
      3. `type` field, PascalCased + "Section" suffix
    """
    if not isinstance(section, dict):
        return None

    explicit = _coerce(section.get("component"))
    if explicit and _VALID_COMPONENT_NAME.match(explicit):
        return explicit

    for key in ("name", "type", "id"):
        raw = _coerce(section.get(key))
        if not raw:
            continue
        pascal = _to_pascal(raw)
        if not pascal:
            continue
        # Append "Section" if the name is generic and would clash with React primitives
        if pascal.lower() in {"hero", "features", "gallery", "story", "testimonials",
                               "cta", "pricing", "faq", "menu", "team", "contact",
                               "stats", "about", "services", "process"}:
            pascal = pascal + "Section"
        if _VALID_COMPONENT_NAME.match(pascal):
            return pascal
    return None


def plan_homepage(
    project_schema: dict,
    *,
    stack: str = "nextjs",
) -> dict | None:
    """Return {rel_path, contents, sections: [{component, import_path}]} or None.

    Returns None when the schema has no usable sections — caller should
    fall back to Claude in that case.
    """
    sections = project_schema.get("sections") or []
    if not isinstance(sections, list) or not sections:
        return None

    # Resolve section → component
    resolved: list[dict] = []
    seen: set[str] = set()
    for s in sections:
        comp = _section_to_component(s)
        if not comp or comp in seen:
            continue
        seen.add(comp)
        resolved.append({"component": comp, "raw": s})

    if not resolved:
        return None

    brand_name = _coerce((project_schema.get("brand") or {}).get("name")) or "Home"
    tagline = _coerce((project_schema.get("brand") or {}).get("tagline"))
    metadata_title = brand_name
    metadata_desc = tagline or f"Welcome to {brand_name}"

    is_next = "next" in (stack or "").lower()

    if is_next:
        return _plan_nextjs(resolved, metadata_title, metadata_desc)
    return _plan_vite(resolved, metadata_title)


def _plan_nextjs(
    resolved: list[dict],
    title: str,
    description: str,
) -> dict:
    """Build a Next.js app-router src/app/page.js."""
    import json as _json
    imports = "\n".join(
        f'import {r["component"]} from "@/components/sections/{r["component"]}";'
        for r in resolved
    )
    body = "\n".join(f'      <{r["component"]} />' for r in resolved)
    contents = (
        f'{imports}\n'
        f'\n'
        f'export const metadata = {{\n'
        f'  title: {_json.dumps(title, ensure_ascii=False)},\n'
        f'  description: {_json.dumps(description, ensure_ascii=False)},\n'
        f'}};\n'
        f'\n'
        f'export default function HomePage() {{\n'
        f'  return (\n'
        f'    <main>\n'
        f'{body}\n'
        f'    </main>\n'
        f'  );\n'
        f'}}\n'
    )
    return {
        "rel_path": "src/app/page.js",
        "contents": contents,
        "sections": [{"component": r["component"]} for r in resolved],
    }


def _plan_vite(resolved: list[dict], title: str) -> dict:
    """Build a Vite-style src/pages/index.jsx (React) homepage."""
    imports = "\n".join(
        f'import {r["component"]} from "@/components/sections/{r["component"]}";'
        for r in resolved
    )
    body = "\n".join(f'      <{r["component"]} />' for r in resolved)
    contents = (
        f'{imports}\n'
        f'\n'
        f'export default function HomePage() {{\n'
        f'  return (\n'
        f'    <main>\n'
        f'{body}\n'
        f'    </main>\n'
        f'  );\n'
        f'}}\n'
    )
    return {
        "rel_path": "src/pages/index.jsx",
        "contents": contents,
        "sections": [{"component": r["component"]} for r in resolved],
    }
