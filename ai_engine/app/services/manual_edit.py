"""Manual no-LLM edits for generated content files.

The preview editor can apply a patch directly in the iframe for instant
feedback. This module persists the same patch when the selected element maps
to one of our structured JSON content files.
"""

from __future__ import annotations

import glob
import json
import os
from typing import Any

from app.services.project_writer import safe_workspace_file, write_text_file


def apply_manual_content_edit(
    *,
    workspace_path: str,
    editable_target: dict | None,
    patch: dict | None,
) -> dict[str, Any]:
    """Persist a manual edit into a generated content JSON file.

    Returns a dict with:
      ok: bool
      rel_path: written file when ok
      content: post-write content when ok
      message: user-facing status/failure
    """

    target = editable_target if isinstance(editable_target, dict) else {}
    data_patch = patch if isinstance(patch, dict) else {}
    dot_path = str(data_patch.get("path") or target.get("path") or "").strip()
    if not workspace_path or not os.path.isdir(workspace_path):
        return _fail("Workspace is not available for manual edit.")
    if not dot_path or bool(target.get("fuzzy")):
        return _fail("Manual save needs a structured editable target.")

    candidates = _candidate_content_files(workspace_path, target)
    matches: list[tuple[str, str, Any]] = []
    for rel_path in candidates:
        loaded = _load_json_file(workspace_path, rel_path)
        if loaded is None:
            continue
        abs_path, parsed = loaded
        if _path_exists(parsed, _split_path(dot_path)):
            matches.append((rel_path, abs_path, parsed))

    if not matches:
        return _fail(f"Could not find content path '{dot_path}' in generated JSON files.")

    route_rel = _route_content_file(target)
    if route_rel:
        route_matches = [m for m in matches if m[0] == route_rel]
        if route_matches:
            matches = route_matches

    unique = {m[0] for m in matches}
    if len(unique) > 1 and not route_rel:
        return _fail(f"Content path '{dot_path}' exists on multiple pages; open the target page and select it again.")

    rel_path, _abs_path, parsed = matches[0]
    changed = _apply_patch(parsed, dot_path, data_patch)
    if not changed:
        return _fail("Manual edit did not contain a supported value to save.")

    content = json.dumps(parsed, ensure_ascii=False, indent=2) + "\n"
    written = write_text_file(
        workspace_path,
        rel_path,
        content,
        protect=True,
        atomic=True,
        inject_img_fallback=False,
        sanity_check=False,
    )
    if not written:
        return _fail("Manual edit could not write the content file.")

    return {
        "ok": True,
        "rel_path": written,
        "content": content,
        "message": f"Manual edit saved to {written}.",
    }


def _fail(message: str) -> dict[str, Any]:
    return {"ok": False, "message": message}


def _candidate_content_files(workspace_path: str, target: dict) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []

    def add(rel: str | None) -> None:
        if not rel or rel in seen:
            return
        safe = safe_workspace_file(workspace_path, rel, protect=True)
        if not safe:
            return
        norm, abs_path = safe
        if os.path.isfile(abs_path):
            seen.add(norm)
            out.append(norm)

    add(_route_content_file(target))
    add("src/content/landing.json")

    for pattern in (
        os.path.join(workspace_path, "src", "content", "pages", "**", "*.json"),
        os.path.join(workspace_path, "src", "content", "*.json"),
    ):
        for abs_path in sorted(glob.glob(pattern, recursive=True)):
            try:
                rel = os.path.relpath(abs_path, workspace_path).replace("\\", "/")
            except ValueError:
                continue
            add(rel)

    return out


def _route_content_file(target: dict) -> str | None:
    route = str(target.get("route") or target.get("pathname") or "").strip()
    if not route:
        return None
    route = route.split("?", 1)[0].split("#", 1)[0].strip("/")
    slug = route or "home"
    return f"src/content/pages/{slug}.json"


def _load_json_file(workspace_path: str, rel_path: str) -> tuple[str, Any] | None:
    safe = safe_workspace_file(workspace_path, rel_path, protect=True)
    if not safe:
        return None
    _rel, abs_path = safe
    try:
        with open(abs_path, "r", encoding="utf-8") as fh:
            return abs_path, json.load(fh)
    except Exception:
        return None


def _split_path(dot_path: str) -> list[str]:
    return [p for p in str(dot_path or "").split(".") if p]


def _path_exists(root: Any, parts: list[str]) -> bool:
    parent = _get_parent(root, parts)
    if not parent:
        return False
    container, key = parent
    if isinstance(container, dict):
        return key in container
    if isinstance(container, list) and key.isdigit():
        idx = int(key)
        return 0 <= idx < len(container)
    return False


def _get_parent(root: Any, parts: list[str]) -> tuple[Any, str] | None:
    if not parts:
        return None
    cur = root
    for part in parts[:-1]:
        if isinstance(cur, dict):
            if part not in cur:
                return None
            cur = cur[part]
            continue
        if isinstance(cur, list) and part.isdigit():
            idx = int(part)
            if idx < 0 or idx >= len(cur):
                return None
            cur = cur[idx]
            continue
        return None
    return cur, parts[-1]


def _get_value(root: Any, parts: list[str]) -> Any:
    parent = _get_parent(root, parts)
    if not parent:
        return None
    container, key = parent
    if isinstance(container, dict):
        return container.get(key)
    if isinstance(container, list) and key.isdigit():
        idx = int(key)
        if 0 <= idx < len(container):
            return container[idx]
    return None


def _set_value(root: Any, dot_path: str, value: Any) -> bool:
    parts = _split_path(dot_path)
    parent = _get_parent(root, parts)
    if not parent:
        return False
    container, key = parent
    if isinstance(container, dict) and key in container:
        if container.get(key) == value:
            return False
        container[key] = value
        return True
    if isinstance(container, list) and key.isdigit():
        idx = int(key)
        if 0 <= idx < len(container):
            if container[idx] == value:
                return False
            container[idx] = value
            return True
    return False


def _apply_patch(root: Any, dot_path: str, patch: dict) -> bool:
    kind = str(patch.get("kind") or "").lower()
    parts = _split_path(dot_path)
    current = _get_value(root, parts)

    if kind == "image":
        return _apply_image_patch(root, dot_path, parts, current, patch)
    if kind == "link":
        return _apply_link_patch(root, dot_path, parts, current, patch)
    if "text" in patch:
        return _set_value(root, dot_path, str(patch.get("text") or ""))
    if "value" in patch:
        return _set_value(root, dot_path, patch.get("value"))
    return False


def _apply_image_patch(root: Any, dot_path: str, parts: list[str], current: Any, patch: dict) -> bool:
    changed = False
    src = str(patch.get("src") or "").strip()
    alt = str(patch.get("alt") or "")

    if isinstance(current, dict):
        if src:
            src_key_seen = False
            for key in ("src", "image_url", "image", "url"):
                if key in current:
                    src_key_seen = True
                    if current.get(key) != src:
                        current[key] = src
                        changed = True
                    break
            if not src_key_seen:
                current["src"] = src
                changed = True
        if "alt" in patch:
            if current.get("alt") != alt:
                current["alt"] = alt
                changed = True
        return changed

    leaf = parts[-1].lower() if parts else ""
    if "alt" in patch and leaf in {"alt", "image_alt"}:
        changed = _set_value(root, dot_path, alt) or changed

    if src and leaf in {"src", "image", "image_url", "url"}:
        changed = _set_value(root, dot_path, src) or changed

    parent = _get_parent(root, parts)
    if src and parent:
        container, _key = parent
        if isinstance(container, dict):
            for sibling in ("src", "image_url", "image", "url"):
                if sibling in container:
                    if container.get(sibling) != src:
                        container[sibling] = src
                        changed = True
                    break

    return changed


def _apply_link_patch(root: Any, dot_path: str, parts: list[str], current: Any, patch: dict) -> bool:
    changed = False
    text = str(patch.get("text") or "")
    href = str(patch.get("href") or "").strip()

    if isinstance(current, dict):
        if "text" in patch:
            text_key_seen = False
            for key in ("label", "text", "title"):
                if key in current:
                    text_key_seen = True
                    if current.get(key) != text:
                        current[key] = text
                        changed = True
                    break
            if not text_key_seen:
                current["label"] = text
                changed = True
        if href:
            if current.get("href") != href:
                current["href"] = href
                changed = True
        return changed

    leaf = parts[-1].lower() if parts else ""
    if "text" in patch and leaf in {"label", "text", "title"}:
        changed = _set_value(root, dot_path, text) or changed
    if href and leaf in {"href", "url"}:
        changed = _set_value(root, dot_path, href) or changed

    parent = _get_parent(root, parts)
    if href and parent:
        container, _key = parent
        if isinstance(container, dict):
            if container.get("href") != href:
                container["href"] = href
                changed = True

    return changed
