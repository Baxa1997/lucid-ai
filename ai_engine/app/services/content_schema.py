"""Derive .lucid/content-schema.json from generated content JSON files.

Stage 6.5 — runs after page generation. Walks `src/content/pages/*.json`
and produces a flat schema describing every editable field path. The
frontend editor reads this schema to know what's editable, what type
each field is, and any constraints (max_length, etc.).

We DERIVE the schema rather than asking Claude to author it because:
  • content + schema must stay in sync — derivation guarantees that
  • Claude tends to drift on long structured outputs
  • Each new content key picks up a sensible default schema entry for free

Field-type inference rules:
  • key ends in "href" or "url"       → "url"
  • key ends in "image" or contains "image_alt" / "src" → "image" / "text"
  • value is a list                   → "array" + per-item schema
  • key matches color regex           → "color"
  • value is long string (>140)       → "rich_text"
  • value is string                   → "text" + max_length heuristic
  • value is dict                     → recurse, prefix path with "{key}."
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)


_HEX_COLOR_RX = re.compile(r"^#[0-9A-Fa-f]{3,8}$")
_HSL_COLOR_RX = re.compile(r"^\d+\s+\d+%\s+\d+%$")


def _infer_field_type(key: str, value: Any) -> str:
    """Best-effort type label for a leaf field."""
    k = key.lower()
    if isinstance(value, list):
        return "array"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if not isinstance(value, str):
        return "text"

    if k.endswith("href") or k.endswith("url") or k == "link":
        return "url"
    if k in ("src", "image", "background_image", "hero_image"):
        return "image"
    if k.endswith("alt") or k.endswith("image_alt"):
        return "text"
    if k in ("color", "primary", "accent", "background", "foreground"):
        if _HEX_COLOR_RX.match(value) or _HSL_COLOR_RX.match(value):
            return "color"
    if len(value) > 140:
        return "rich_text"
    return "text"


def _max_length_for(key: str, value: Any) -> int | None:
    """Conservative max_length advice for the editor.

    Doesn't enforce — the editor decides whether to clamp.
    """
    if not isinstance(value, str):
        return None
    k = key.lower()
    if k in ("title", "headline"):
        return 100
    if k in ("subtitle", "tagline"):
        return 200
    if k == "label":
        return 30
    if k.endswith("href") or k.endswith("url"):
        return 500
    if len(value) <= 60:
        return 100
    if len(value) <= 140:
        return 240
    return 1000


def _walk_content(node: Any, prefix: str = "") -> dict[str, dict]:
    """Flatten a nested content dict into {dotted.path: schema_entry}.

    For arrays we record both the array itself (type=array) and a
    representative item schema using index "[]" so the editor can render
    an "add item" UI without examining N entries.
    """
    out: dict[str, dict] = {}
    if isinstance(node, dict):
        for k, v in node.items():
            sub = f"{prefix}.{k}" if prefix else k
            ftype = _infer_field_type(k, v)
            if isinstance(v, dict):
                # Don't record the parent dict itself, just walk into it
                out.update(_walk_content(v, sub))
            elif isinstance(v, list):
                entry: dict[str, Any] = {"type": "array", "length": len(v)}
                out[sub] = entry
                # If items are dicts, record a sample item schema
                if v and isinstance(v[0], dict):
                    out.update(_walk_content(v[0], f"{sub}[]"))
            else:
                entry = {"type": ftype}
                ml = _max_length_for(k, v)
                if ml is not None:
                    entry["max_length"] = ml
                out[sub] = entry
    return out


def derive_schema_for_page(content: dict) -> dict[str, dict]:
    """Return {field_path: {type, ...}} for one page's content dict."""
    return _walk_content(content)


def derive_content_schema(workspace_path: str) -> dict[str, Any]:
    """Walk src/content/pages/*.json and produce the full content schema.

    Returns the schema dict. Caller writes it to .lucid/content-schema.json.
    """
    schema: dict[str, Any] = {"version": "1.0", "pages": {}}
    pages_dir = os.path.join(workspace_path, "src", "content", "pages")
    if not os.path.isdir(pages_dir):
        return schema

    for fn in sorted(os.listdir(pages_dir)):
        if not fn.endswith(".json"):
            continue
        page_slug = fn[:-5]
        abs_path = os.path.join(pages_dir, fn)
        try:
            with open(abs_path, "r", encoding="utf-8") as f:
                content = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("content_schema: skipping invalid %s — %s", fn, exc)
            continue
        if not isinstance(content, dict):
            logger.warning("content_schema: %s is not a JSON object — skipping", fn)
            continue
        fields = derive_schema_for_page(content)
        schema["pages"][page_slug] = {"fields": fields}
        logger.info(
            "content_schema: %s — %d fields", page_slug, len(fields),
        )
    return schema


def write_content_schema(workspace_path: str) -> tuple[str, int]:
    """Derive + write .lucid/content-schema.json. Returns (path, field_count)."""
    schema = derive_content_schema(workspace_path)
    out_dir = os.path.join(workspace_path, ".lucid")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "content-schema.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(schema, f, indent=2, ensure_ascii=False)
    total_fields = sum(
        len(page.get("fields") or {})
        for page in schema["pages"].values()
    )
    return out_path, total_fields
