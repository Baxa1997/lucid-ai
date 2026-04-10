"""
pipeline/ws_utils.py — WebSocket helper functions.

Extracted verbatim from task_pipeline.py (lines 89–155).
Zero logic changes.
"""

import logging
from .constants import _FILE_TREE_EXCLUDE

import os

logger = logging.getLogger(__name__)


def _build_ws_file_tree(root_dir: str) -> list:
    """Recursively build a file tree suitable for the frontend Code tab."""
    def walk(dir_path: str) -> list:
        entries = []
        try:
            items = sorted(os.listdir(dir_path))
        except (PermissionError, FileNotFoundError):
            return entries
        for item in items:
            full = os.path.join(dir_path, item)
            rel = os.path.relpath(full, root_dir)
            if os.path.isdir(full):
                if item in _FILE_TREE_EXCLUDE or item.startswith("."):
                    continue
                entries.append({
                    "name": item,
                    "type": "folder",
                    "path": "/" + rel,
                    "children": walk(full),
                })
            else:
                entries.append({
                    "name": item,
                    "type": "file",
                    "path": "/" + rel,
                })
        return entries
    return walk(root_dir)


async def _send_file_tree(websocket, workspace_path: str):
    """Emit a file_tree event to the frontend so the Code tab updates."""
    try:
        tree = _build_ws_file_tree(workspace_path)
        await websocket.send_json({
            "type": "file_tree",
            "tree": tree,
        })
        logger.info("Sent file_tree event (%d top-level entries)", len(tree))
    except Exception as e:
        logger.warning("Failed to send file_tree: %s", e)


async def _send_chat_message(websocket, content: str, role: str = "agent"):
    """Emit a chat message that appears in the frontend chat panel."""
    try:
        await websocket.send_json({
            "type": "chat_message",
            "role": role,
            "content": content,
        })
    except Exception:
        pass


async def _approve_all_tools(tool_name: str, input_data: dict, context):
    from claude_code_sdk.types import PermissionResultAllow
    return PermissionResultAllow()
