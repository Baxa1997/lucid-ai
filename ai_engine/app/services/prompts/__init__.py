"""Prompt builders for admin CRUD codegen.

Each builder returns a ``{"system": str, "user": str}`` dict — the
shape `call_claude_for_json` expects. Builders are pure functions of
the entity + plan; no Gemini, no I/O.

Step 3.6 Part A defines the builders. Part B will call Claude with
their output once Anthropic credits are available.
"""
from app.services.prompts.admin_list_view_prompt import build_list_view_prompt
from app.services.prompts.admin_create_view_prompt import build_create_view_prompt
from app.services.prompts.admin_edit_view_prompt import build_edit_view_prompt

__all__ = [
    "build_list_view_prompt",
    "build_create_view_prompt",
    "build_edit_view_prompt",
]
