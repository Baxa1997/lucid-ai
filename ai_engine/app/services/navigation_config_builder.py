"""Deterministic src/config/navigation.js builder.

Emits a single navigation file with three exports so every downstream
consumer pattern works without a shape negotiation between Phase 1 and
Phase 2:

    export const mainNav = [{label, href, icon?, badge?}, ...]
    export const footerNav = [{group, links: [{label, href}, ...]}, ...]
    export const navigationConfig = [{title, href, icon}, ...]

  - mainNav      — flat top-nav array (Header/MobileNav consumers)
  - footerNav    — grouped column array (Footer consumers)
  - navigationConfig — template-shape alias (existing template
    components that haven't been touched yet still import this name)

Driven by project_schema["navigation"] which is already grouped:
    [{group, items: [{label, icon, path, badge?}]}]

Why deterministic: shrinks Phase 1's output budget by ~one file group
(~30-40s wall clock). The schema's nav structure is fully filled in by
schema_build before Phase 1 runs, so we have everything we need.

Pure module — no I/O, no LLM, no async.
"""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def _js_str(value: str) -> str:
    return json.dumps(value or "", ensure_ascii=False)


def _coerce(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _flatten(navigation: list[dict]) -> list[dict]:
    """Flatten the schema's grouped navigation into a single ordered list,
    deduplicating by href. Order: items in group order, items inside each
    group in their original order."""
    out: list[dict] = []
    seen_hrefs: set[str] = set()
    for grp in navigation or []:
        if not isinstance(grp, dict):
            continue
        for item in grp.get("items") or []:
            if not isinstance(item, dict):
                continue
            label = _coerce(item.get("label"))
            href = _coerce(item.get("path") or item.get("href"))
            if not label or not href:
                continue
            if href in seen_hrefs:
                continue
            seen_hrefs.add(href)
            entry: dict[str, Any] = {"label": label, "href": href}
            icon = _coerce(item.get("icon"))
            if icon:
                entry["icon"] = icon
            badge = _coerce(item.get("badge"))
            if badge:
                entry["badge"] = badge
            out.append(entry)
    return out


def _render_main_nav(items: list[dict]) -> str:
    if not items:
        return "export const mainNav = [];\n"
    lines = ["export const mainNav = ["]
    for it in items:
        parts = [f"label: {_js_str(it['label'])}", f"href: {_js_str(it['href'])}"]
        if "icon" in it:
            parts.append(f"icon: {_js_str(it['icon'])}")
        if "badge" in it:
            parts.append(f"badge: {_js_str(it['badge'])}")
        lines.append("  { " + ", ".join(parts) + " },")
    lines.append("];\n")
    return "\n".join(lines)


def _render_footer_nav(navigation: list[dict]) -> str:
    """Render grouped footer nav. Drops empty groups; falls back to
    'Site' as the group title when a group has no name."""
    rendered_groups: list[str] = []
    for grp in navigation or []:
        if not isinstance(grp, dict):
            continue
        items = grp.get("items") or []
        clean_items: list[dict] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            label = _coerce(it.get("label"))
            href = _coerce(it.get("path") or it.get("href"))
            if label and href:
                clean_items.append({"label": label, "href": href})
        if not clean_items:
            continue
        group_name = _coerce(grp.get("group")) or "Site"
        link_lines = [
            f"      {{ label: {_js_str(it['label'])}, href: {_js_str(it['href'])} }},"
            for it in clean_items
        ]
        rendered_groups.append(
            "  {\n"
            f"    group: {_js_str(group_name)},\n"
            "    links: [\n"
            + "\n".join(link_lines)
            + "\n    ],\n"
            "  },"
        )
    if not rendered_groups:
        return "export const footerNav = [];\n"
    return "export const footerNav = [\n" + "\n".join(rendered_groups) + "\n];\n"


def _render_legacy(items: list[dict]) -> str:
    """Template-shape alias — keys are title/href/icon. Default icon
    'circle' so existing template consumers that look up Icons[icon] don't
    crash on undefined."""
    if not items:
        return "export const navigationConfig = [];\n"
    lines = ["export const navigationConfig = ["]
    for it in items:
        icon = it.get("icon") or "circle"
        lines.append(
            "  {\n"
            f"    title: {_js_str(it['label'])},\n"
            f"    href: {_js_str(it['href'])},\n"
            f"    icon: {_js_str(icon)},\n"
            "  },"
        )
    lines.append("];\n")
    return "\n".join(lines)


def build_navigation_config(project_schema: dict) -> str:
    """Render the full navigation.js source.

    Returns an empty-but-valid module (three empty exports) when the
    schema has no navigation entries — keeps consumers compiling rather
    than throwing 'Cannot read properties of undefined'.
    """
    navigation = project_schema.get("navigation") or []
    flat = _flatten(navigation)
    return (
        _render_main_nav(flat)
        + "\n"
        + _render_footer_nav(navigation)
        + "\n"
        + _render_legacy(flat)
    )
