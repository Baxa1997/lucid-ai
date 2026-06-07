"""Gemini-owned intent router for messages inside an existing workspace.

The dashboard/new-project path already has a project-intake gate. Follow-up
chat needs a different guard: decide whether a message is an actionable edit,
a discussion/question, or too vague to run through the code pipeline.

Gemini Flash chooses the next workflow for every prompt. The deterministic
classifier remains available only as fail-soft outage recovery.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.services.prompt_guards import is_gibberish

logger = logging.getLogger(__name__)


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

    # Random-word-bag guard. A real edit/discussion message either:
    #   (a) names an edit verb or UI element from our vocab
    #   (b) is phrased as a question
    #   (c) is a complete sentence (≥8 tokens — long enough to describe
    #       context even without a matched vocab word)
    # Anything else — short noun lists, lyrics, random pasted text, real
    # words in a non-English language with no recognised signal — is
    # treated as incoherent. Previously the heuristic only rejected ≤2
    # token prompts, so "tree apple banana keyboard sunshine" would slip
    # straight through to the pipeline and start building something.
    if (
        not has_edit_signal
        and not has_ui_signal
        and not _is_question(raw)
        and len(toks) < 8
    ):
        return {
            "action": "clarify",
            "message": (
                "I couldn't make out what you'd like me to change. "
                "Could you describe it more concretely? For example: "
                "“make the hero darker” or “add a pricing section”."
            ),
        }

    # "blue hero", "bigger logo", "pricing section" are terse but actionable
    # because they name a UI target. Let the edit pipeline interpret them.
    return {"action": "proceed"}


async def classify_followup_with_gemini(
    text: str,
    *,
    mode: str = "edit",
    has_images: bool = False,
    editable_target: Any = None,
) -> dict[str, str]:
    """Let Gemini Flash choose the next workflow for a workspace prompt.

    The deterministic classifier above remains the fail-soft fallback only.
    """
    if has_images or editable_target:
        return {
            "action": "proceed",
            "workflow_id": "edit",
            "reasoning": "The user supplied direct visual context.",
            "decided_by": "direct_context",
        }

    prompt = f"""You are the prompt-routing agent for an AI coding workspace.
Decide what the agent must do next for the user's latest message.

Current UI mode: {mode}
User message: {text}

Choose exactly one action:
- proceed: the user requests an actionable code or design change
- discuss: inspect/explain the existing project without changing files
- clarify: the request is random, ambiguous, or missing the thing to change
- reply: a greeting or simple conversational message needing no project inspection

Rules:
- In discuss mode, use discuss for substantive project questions.
- A question that asks the agent to change/fix/add something is proceed.
- Do not invent a change from random words.
- Keep reply under 30 words and end clarify replies with a question.

Return ONLY JSON:
{{
  "action": "proceed|discuss|clarify|reply",
  "reply": "only for clarify or reply",
  "reasoning": "short internal routing reason"
}}"""

    try:
        from app.services.gemini_http import gemini_post
        from knowledge.loader import safe_gemini_text

        status, data, raw = await gemini_post(
            model="gemini-3.5-flash",
            payload={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0,
                    "maxOutputTokens": 500,
                    "responseMimeType": "application/json",
                },
            },
            timeout_s=15.0,
            label="followup_prompt_router",
        )
        if status != 200 or data is None:
            raise RuntimeError(raw or f"HTTP {status}")
        parsed = json.loads(safe_gemini_text(data).strip())
        action = str(parsed.get("action") or "").strip().lower()
        if action not in {"proceed", "discuss", "clarify", "reply"}:
            raise ValueError(f"invalid action: {action}")
        result = {
            "action": action,
            "workflow_id": "edit" if action == "proceed" else action,
            "reasoning": str(parsed.get("reasoning") or "").strip(),
            "decided_by": "gemini_flash",
        }
        if action in {"clarify", "reply"}:
            result["message"] = str(parsed.get("reply") or "").strip()
        return result
    except Exception as exc:
        logger.warning("Gemini follow-up router failed: %s — using fallback", exc)
        fallback = classify_followup_message(
            text,
            mode=mode,
            has_images=has_images,
            editable_target=editable_target,
        )
        fallback["workflow_id"] = (
            "edit" if fallback.get("action") == "proceed" else fallback.get("action", "clarify")
        )
        fallback["reasoning"] = "deterministic fallback after Gemini Flash failure"
        fallback["decided_by"] = "deterministic_outage_fallback"
        return fallback
