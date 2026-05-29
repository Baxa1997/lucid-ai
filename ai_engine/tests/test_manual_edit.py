from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.manual_edit import apply_manual_content_edit  # noqa: E402


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_manual_text_edit_persists_route_scoped_content(tmp_path: Path) -> None:
    content_path = tmp_path / "src/content/pages/home.json"
    _write_json(content_path, {"hero": {"title": "Old title"}})

    result = apply_manual_content_edit(
        workspace_path=str(tmp_path),
        editable_target={"path": "hero.title", "route": "/", "fuzzy": False},
        patch={"kind": "text", "path": "hero.title", "text": "New title"},
    )

    assert result["ok"] is True
    assert result["rel_path"] == "src/content/pages/home.json"
    assert _read_json(content_path)["hero"]["title"] == "New title"


def test_manual_link_edit_updates_label_and_href(tmp_path: Path) -> None:
    content_path = tmp_path / "src/content/pages/home.json"
    _write_json(
        content_path,
        {"hero": {"cta_primary": {"label": "Start", "href": "#start"}}},
    )

    result = apply_manual_content_edit(
        workspace_path=str(tmp_path),
        editable_target={"path": "hero.cta_primary.label", "route": "/", "fuzzy": False},
        patch={
            "kind": "link",
            "path": "hero.cta_primary.label",
            "text": "Book now",
            "href": "/booking",
        },
    )

    cta = _read_json(content_path)["hero"]["cta_primary"]
    assert result["ok"] is True
    assert cta == {"label": "Book now", "href": "/booking"}


def test_manual_edit_rejects_fuzzy_target(tmp_path: Path) -> None:
    content_path = tmp_path / "src/content/pages/home.json"
    _write_json(content_path, {"hero": {"title": "Old title"}})

    result = apply_manual_content_edit(
        workspace_path=str(tmp_path),
        editable_target={"path": "hero.title", "route": "/", "fuzzy": True},
        patch={"kind": "text", "path": "hero.title", "text": "New title"},
    )

    assert result["ok"] is False
    assert _read_json(content_path)["hero"]["title"] == "Old title"
