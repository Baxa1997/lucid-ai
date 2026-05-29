"""Cheap intent guard for messages inside an existing workspace.

The dashboard/new-project path already has a project-intake gate. Follow-up
chat needs a different guard: decide whether a message is an actionable edit,
a discussion/question, or too vague to run through the code pipeline.

This module is deterministic by design. It catches the obvious bad cases
without adding another network call in the hot chat path; real edits pass
through to the existing Gemini/Claude pipeline.
"""

from __future__ import annotations

import re
from typing import Any

from app.services.prompt_guards import is_gibberish


_EDIT_WORDS = {
    "add", "change", "make", "remove", "delete", "fix", "update", "replace",
    "move", "resize", "create", "build", "implement", "improve", "redesign",
    "style", "restyle", "rewrite", "shorten", "extend", "hide", "show",
    "connect", "integrate", "deploy", "publish",
}

_UI_WORDS = {
    "hero", "header", "footer", "nav", "navbar", "menu", "button", "cta",
    "form", "card", "section", "page", "modal", "sidebar", "dashboard",
    "admin", "table", "chart", "image", "logo", "text", "copy", "headline",
    "title", "color", "font", "spacing", "layout", "mobile", "desktop",
    "dark", "light", "theme", "animation", "route", "link",
}

_QUESTION_STARTS = (
    "what", "why", "how", "when", "where", "who", "which", "can you",
    "could you", "should", "is ", "are ", "do ", "does ", "did ",
    "tell me", "explain", "show me",
)

_INFO_QUESTION_STARTS = (
    "what", "why", "how", "when", "where", "who", "which",
    "tell me", "explain", "show me",
)

_STATUS_STARTS = (
    "status", "where are you", "what stage", "what step", "what phase",
    "what are you doing", "are you done", "is it done", "how long",
    "how much longer", "any progress", "progress update",
)

_GREETING_RE = re.compile(
    r"^(hi|hello|hey|yo|sup|how are you|how's it going|thanks|thank you|ok|okay|test)[.!?\s]*$",
    re.IGNORECASE,
)

_VAGUE_EDIT_RE = re.compile(
    r"^(do something|make it better|improve it|improve this|fix it|change it|update it|"
    r"change something|edit something|add something|make something|"
    r"make better|make this better|edit it|edit this|edit|change|update|fix|"
    r"continue|go ahead)[.!?\s]*$",
    re.IGNORECASE,
)


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z][a-z0-9_'-]*", (text or "").lower()) if len(t) > 1}


def _is_question(text: str) -> bool:
    s = (text or "").strip().lower()
    return s.endswith("?") or s.startswith(_QUESTION_STARTS)


def _is_status_question(text: str) -> bool:
    s = (text or "").strip().lower()
    return s.startswith(_STATUS_STARTS)


def classify_followup_message(
    text: str,
    *,
    mode: str = "edit",
    has_images: bool = False,
    editable_target: Any = None,
) -> dict[str, str]:
    """Return how the workspace should handle a follow-up message.

    Actions:
      proceed  - run the normal edit/discuss pipeline
      discuss  - run pipeline in discuss mode even if the UI was in edit mode
      clarify  - ask the user for a more actionable edit
      reply    - answer directly without running the pipeline
    """
    raw = (text or "").strip()
    if has_images or editable_target:
        return {"action": "proceed"}
    if not raw:
        return {"action": "clarify", "message": "What would you like me to change?"}

    if is_gibberish(raw):
        return {
            "action": "clarify",
            "message": "I couldn't read that as a request. What would you like to change or ask about this project?",
        }

    if _GREETING_RE.fullmatch(raw):
        return {
            "action": "reply",
            "message": "I'm here and ready. What would you like to change or ask about this project?",
        }

    if _is_status_question(raw):
        return {
            "action": "reply",
            "message": "I'm ready for your next instruction. Tell me what to change, or switch to Discuss to ask about the project.",
        }

    if mode == "discuss":
        return {"action": "proceed"}

    if _VAGUE_EDIT_RE.fullmatch(raw):
        return {
            "action": "clarify",
            "message": "What specifically should I change? For example: the hero headline, colors, layout, or a section to add.",
        }

    lowered = raw.lower().strip()
    if lowered.startswith(_INFO_QUESTION_STARTS):
        return {"action": "discuss"}

    toks = _tokens(raw)
    has_edit_signal = bool(toks & _EDIT_WORDS)
    has_ui_signal = bool(toks & _UI_WORDS)

    if _is_question(raw) and not has_edit_signal:
        return {"action": "discuss"}

    if len(toks) <= 2 and not (has_edit_signal or has_ui_signal):
        return {
            "action": "clarify",
            "message": "I need a little more detail. What part of the project should I edit?",
        }

    # "blue hero", "bigger logo", "pricing section" are terse but actionable
    # because they name a UI target. Let the edit pipeline interpret them.
    return {"action": "proceed"}
