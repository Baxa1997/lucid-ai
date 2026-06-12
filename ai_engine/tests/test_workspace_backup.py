"""Tests for app/services/workspace_backup.py.

The backup runs when a git push fails and the per-run /tmp workspace is
the only copy of the user's generated project. It must produce a REAL
directory at preview_workspace_path(project_id) (never a symlink), and
it must never raise — a backup failure must not mask the push error.
"""

import os

import pytest

import app.paths as paths
from app.services.workspace_backup import backup_workspace_durably


@pytest.fixture
def preview_root(tmp_path, monkeypatch):
    root = tmp_path / "preview_ws"
    monkeypatch.setattr(paths, "PREVIEW_WS_ROOT", str(root))
    return root


def _make_workspace(tmp_path, name="ws"):
    ws = tmp_path / name
    (ws / "src").mkdir(parents=True)
    (ws / "node_modules" / "react").mkdir(parents=True)
    (ws / ".git").mkdir()
    (ws / "package.json").write_text('{"name": "x"}')
    (ws / "src" / "page.js").write_text("export default 1")
    (ws / "node_modules" / "react" / "index.js").write_text("x")
    (ws / ".git" / "config").write_text("[remote]")
    return ws


async def test_basic_backup_copies_files_excluding_heavy_dirs(tmp_path, preview_root):
    ws = _make_workspace(tmp_path)

    target = await backup_workspace_durably(str(ws), "proj-abc-123")

    assert target == paths.preview_workspace_path("proj-abc-123")
    assert os.path.isfile(os.path.join(target, "package.json"))
    assert os.path.isfile(os.path.join(target, "src", "page.js"))
    assert not os.path.exists(os.path.join(target, "node_modules"))
    assert not os.path.exists(os.path.join(target, ".git"))
    assert not os.path.islink(target)


async def test_replaces_symlink_promotion_with_real_copy(tmp_path, preview_root):
    """Success-path promotion symlinks the preview path into /tmp; on push
    failure that symlink must become a real directory or a restart still
    loses everything."""
    ws = _make_workspace(tmp_path)
    target = paths.preview_workspace_path("proj-sym")
    os.symlink(str(ws), target)

    result = await backup_workspace_durably(str(ws), "proj-sym")

    assert result == target
    assert not os.path.islink(target)
    assert os.path.isfile(os.path.join(target, "src", "page.js"))


async def test_replaces_stale_real_dir(tmp_path, preview_root):
    ws = _make_workspace(tmp_path)
    target = paths.preview_workspace_path("proj-stale")
    os.makedirs(target)
    with open(os.path.join(target, "old.txt"), "w") as f:
        f.write("stale")

    await backup_workspace_durably(str(ws), "proj-stale")

    assert not os.path.exists(os.path.join(target, "old.txt"))
    assert os.path.isfile(os.path.join(target, "package.json"))


async def test_noop_when_workspace_is_already_the_durable_path(tmp_path, preview_root):
    target = paths.preview_workspace_path("proj-self")
    os.makedirs(os.path.join(target, "src"))
    with open(os.path.join(target, "package.json"), "w") as f:
        f.write("{}")

    result = await backup_workspace_durably(target, "proj-self")

    assert result == target
    assert os.path.isfile(os.path.join(target, "package.json"))


async def test_skips_on_missing_inputs(tmp_path, preview_root):
    assert await backup_workspace_durably(None, "p") is None
    assert await backup_workspace_durably(str(tmp_path / "nope"), "p") is None
    assert await backup_workspace_durably(str(tmp_path), None) is None


async def test_never_raises_and_notifies_user(tmp_path, preview_root):
    """WS notification goes out on success; a broken WS must not raise."""
    ws = _make_workspace(tmp_path)

    sent = []

    class FakeWS:
        async def send_json(self, data):
            sent.append(data)

    await backup_workspace_durably(str(ws), "proj-notify", FakeWS())
    assert sent and sent[0]["type"] == "chat_message"
    assert "saved" in sent[0]["content"]

    class BrokenWS:
        async def send_json(self, data):
            raise RuntimeError("ws closed")

    # Must not raise despite the dead websocket.
    result = await backup_workspace_durably(str(ws), "proj-notify-2", BrokenWS())
    assert result is not None
