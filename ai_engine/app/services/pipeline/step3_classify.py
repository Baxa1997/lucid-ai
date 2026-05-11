"""
pipeline/step3_classify.py — Pipeline Step 3: classify task with Gemini Flash.

Extracted verbatim from task_pipeline.py (lines 1149–1389).
Zero logic changes.
"""

from __future__ import annotations

import json
import asyncio
import logging

from fastapi import WebSocket

from .constants import GEMINI_MODEL

logger = logging.getLogger(__name__)


async def classify_task(
    task: str,
    websocket: WebSocket,
) -> dict:
    """Use Gemini Flash to classify task complexity.

    NEVER raises — always returns a valid dict.
    """
    try:
        await websocket.send_json({
            "type": "progress",
            "message": "🎯 Classifying task...",
        })

        from app.services.llm_retry import call_with_retry, classify_http_error
        from app.services.gemini_http import gemini_post

        _prompt = f"""You are a senior engineering manager at a top tier tech company.
You assign tasks to the right developer.
Return ONLY valid JSON, nothing else. No markdown. No backticks. Just JSON.

You have three developers:

JUNIOR (haiku):
A junior developer who is fast and cheap.
Handles tasks that require NO logical thinking.
Only changes how things LOOK or APPEAR.
Never touches business logic or data flow.
Can complete task in under 30 minutes.
Junior handles ALL of these:
- Adding/removing/changing visual elements (logo, image, icon, button, text, link, header, footer, nav, section)
- Adding a logo or image to any page
- Adding a link or navigation element
- Changing colors, fonts, sizes, spacing
- Hiding or showing elements
- Reordering elements on a page
- Changing text or labels
- Any purely visual/cosmetic change
- Wrapping an element with a link

MID (sonnet):
A mid level developer who handles most everyday development work.
Understands logic, state, data flow.
Can work across multiple files.
Handles features, bugs, integrations.
Reliable for most tasks.
Mid handles:
- Creating new components with logic
- Fixing bugs that require debugging
- Adding functionality that needs state management
- API integrations
- Form handling with validation
- Multi-file refactoring

SENIOR (opus):
A senior architect called ONLY for the most critical system-level work.
Called VERY rarely — less than 5% of tasks.

Senior is ONLY for:
- Building entire auth system from scratch
- Payment gateway integration (Stripe, etc)
- Multi-tenant architecture setup
- Security vulnerability fixes
- Database schema design from scratch
- Role-based access control system
- Third party OAuth implementation
- Performance optimization of entire system

Senior is NOT for:
- Fixing visual bugs on auth pages
- Adding/removing fields from forms
- Changing default values
- Static content on login/signup pages
- Simple navigation fixes
- Removing elements from any page
- Changing text or labels in auth forms
- Any change that touches 1-2 files only

KEY RULE FOR SENIOR:
Only use Opus if the task would require a senior architect who understands the entire system infrastructure.
If a mid-level developer can do it in under 2 hours → use Sonnet.
If task mentions auth/login/signup but is just a UI or simple change → NEVER use Opus, use Sonnet or Haiku.

CRITICAL — NEW PROJECT CREATION RULES:
If the task is to CREATE A NEW PROJECT from scratch (not editing existing code):
- Simple static page (just HTML, CSS, text, inputs, basic layout) → JUNIOR
  Examples: "simple html page", "landing page with text", "html with input and text"
- Standard web app (React/Vue/Angular components, routing, state) → MID
  Examples: "e-commerce app", "admin panel", "dashboard with charts"
- Complex system (auth, payments, multi-service, database design) → SENIOR
  Examples: "SaaS platform with auth and billing", "multi-tenant system"

The number of FILES does not determine complexity for new projects.
A plain HTML project with index.html + style.css = JUNIOR even though it's 2 files.
What matters is the LOGIC COMPLEXITY, not the file count.

User task: "{task}"

KEY QUESTION — Ask yourself:
1. Is this a NEW PROJECT or an EDIT to existing code?
2. If NEW: Does it need JavaScript logic, state management, or API calls?
   If NO (just HTML/CSS) → Junior.
   If YES but standard features → Mid.
   If YES and requires system architecture → Senior.
3. If EDIT: Can it be done by editing HTML/JSX template code only?
   If YES → Junior.
   If NO → Mid or Senior.

Return ONLY this JSON:
{{
    "developer": "junior" or "mid" or "senior",
    "model": "haiku" or "sonnet" or "opus",
    "intent": "what user actually wants",
    "why": "why this developer",
    "files_estimate": number,
    "needs_logic": true or false,
    "max_turns": number,
    "complexity": "simple" or "medium" or "complex"
}}

MAX TURNS RULES:
junior → always between 8 and 10
mid    → always between 12 and 18
senior → always between 20 and 25
Never go below these minimums.
Better to give too many than too few.
"""

        # Tiny fixed-schema JSON — temperature=0 for determinism, thinking_budget=0
        # to cut ~3-5s of reasoning latency.
        _payload = {
            "contents": [{"parts": [{"text": _prompt}]}],
            "generationConfig": {
                "temperature": 0,
                "thinkingConfig": {"thinkingBudget": 0},
            },
        }

        async def _do_classify():
            status, data, raw = await gemini_post(
                model=GEMINI_MODEL,
                payload=_payload,
                timeout_s=60.0,
                label="step3_classify",
            )
            if status != 200 or data is None:
                raise classify_http_error(status if status > 0 else 500, raw or "")
            return data

        data = await call_with_retry(_do_classify, label="step3_classify", websocket=websocket)

        from knowledge.loader import safe_gemini_text
        text = safe_gemini_text(data).strip()
        # Remove markdown code fences if present
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        result = json.loads(text.strip())

    except Exception as e:
        logger.warning("classify_task failed: %s — using fallback", e)
        result = {
            "developer": "mid",
            "model": "sonnet",
            "intent": "unknown",
            "why": "fallback classification",
            "files_estimate": 2,
            "needs_logic": True,
            "max_turns": 12,
            "complexity": "medium",
        }

    # Ensure all fields exist with defaults
    result.setdefault("developer", "mid")
    result.setdefault("model", "sonnet")
    result.setdefault("intent", "unknown")
    result.setdefault("why", "auto-selected")
    result.setdefault("files_estimate", 2)
    result.setdefault("needs_logic", True)
    result.setdefault("max_turns", 12)
    result.setdefault("complexity", "medium")

    # Map developer to model name if model wasn't set correctly
    dev_to_model = {
        "junior": "haiku",
        "mid": "sonnet",
        "senior": "opus",
    }
    if result.get("model") not in ("haiku", "sonnet", "opus"):
        result["model"] = dev_to_model.get(result.get("developer", "mid"), "sonnet")

    # Map model names to full IDs
    model_map = {
        "haiku": "claude-haiku-4-5-20251001",
        "sonnet": "claude-sonnet-4-6",
        "opus": "claude-opus-4-6",
    }
    result["model_id"] = model_map.get(
        result.get("model", "sonnet"), "claude-sonnet-4-6"
    )

    # Enforce minimum max_turns per developer level
    dev = result.get("developer", "mid")
    turns = int(result.get("max_turns", 12))
    if dev == "junior":
        turns = max(8, min(turns, 10))
    elif dev == "senior":
        turns = max(20, min(turns, 25))
    else:
        turns = max(12, min(turns, 18))
    result["max_turns"] = turns

    # Map task_type based on Gemini's developer decision — trust Gemini fully
    intent = str(result.get("intent", "")).lower()

    if dev == "junior":
        # Junior always does simple UI
        result["task_type"] = "ui_simple"

    elif dev == "senior":
        # Senior handles complex systems
        if any(w in intent for w in ("auth", "security", "token", "session", "password")):
            result["task_type"] = "auth"
        elif any(w in intent for w in ("database", "schema", "migration", "table")):
            result["task_type"] = "database"
        else:
            result["task_type"] = "feature_complex"

    else:  # mid developer
        # Mid handles bugs, features, complex UI
        if any(w in intent for w in ("bug", "fix", "broken", "error", "crash", "not working")):
            if result.get("complexity") == "complex":
                result["task_type"] = "bug_complex"
            else:
                result["task_type"] = "bug_simple"
        elif any(w in intent for w in ("refactor", "clean", "reorganize", "restructure")):
            result["task_type"] = "refactor"
        elif result.get("needs_logic") is False:
            result["task_type"] = "ui_complex"
        else:
            result["task_type"] = "feature_simple"

    # Send detailed classification to frontend
    try:
        await websocket.send_json({
            "type": "classification",
            "developer": result.get("developer", "mid"),
            "model": result.get("model", "sonnet"),
            "complexity": result.get("complexity", "medium"),
            "task_type": result.get("task_type", "feature_simple"),
            "max_turns": result.get("max_turns", 12),
            "intent": result.get("intent", ""),
            "reason": result.get("why", "auto-selected"),
            "files_expected": result.get("files_estimate", 2),
            "message": f"🎯 {result.get('developer', 'mid').upper()} DEV ({result.get('model', 'sonnet').upper()}) — {result.get('why', 'auto-selected')}",
        })
    except Exception:
        pass

    return result
