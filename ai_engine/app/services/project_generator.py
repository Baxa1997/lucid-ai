"""Project generator v4 — Claude Opus 4.6 multi-call generation engine.

3-phase generation pipeline:
  Call 1: Foundation (theme, config, nav, layouts, main page, router)
  Call 2: Content (all sections OR all CRUD features)
  Call 3: Additional pages + completeness check

Uses Gemini 2.5 Flash for ultra-deep research and Claude Opus 4.6 for
code generation via the direct Messages API (no SDK).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
from typing import Any, Optional

logger = logging.getLogger("lucid.project_generator")

# ── Plan confirmation system ─────────────────────────────────
# When a plan is emitted, we store an asyncio.Future here keyed by
# the websocket's id(). The ws.py handler resolves it when the user
# clicks "Looks Good" or "Change Direction".
#   Future result: {"confirmed": True} or {"confirmed": False, "correction": "..."}
pending_plan_confirmations: dict[int, asyncio.Future] = {}

PLAN_CONFIRM_TIMEOUT_SECONDS = 300  # 5 minutes — auto-proceed after this


def register_plan_confirmation(websocket) -> asyncio.Future:
    """Register a pending plan confirmation for the given websocket."""
    loop = asyncio.get_event_loop()
    fut = loop.create_future()
    pending_plan_confirmations[id(websocket)] = fut
    return fut


def resolve_plan_confirmation(websocket, result: dict):
    """Called by ws.py when user confirms or rejects the plan."""
    ws_id = id(websocket)
    fut = pending_plan_confirmations.pop(ws_id, None)
    if fut and not fut.done():
        fut.set_result(result)


# ╔══════════════════════════════════════════════════════════════╗
# ║  CONSTANTS                                                   ║
# ╚══════════════════════════════════════════════════════════════╝

CLAUDE_API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-sonnet-4-6"        # Fast + same UI/UX quality
FALLBACK_MODEL = "claude-opus-4-6"         # Opus fallback for edge cases
MAX_TOKENS_PER_CALL = 32000                # 32K is enough; halves generation time
MAX_FIX_ATTEMPTS = 2

# Files that must NEVER be overwritten by the generator
# NOTE: Layout files (Sidebar, Header, Footer) are NOT protected —
# Claude can fully rewrite them to create unique project-specific layouts.
PROTECTED_FILES = {
    "TEMPLATE_MANIFEST.md",
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "node_modules",
    ".gitignore",
    "tailwind.config.js",
    "tailwind.config.ts",
    "postcss.config.js",
    "postcss.config.mjs",
    "vite.config.js",
    "vite.config.ts",
    "next.config.mjs",
    "next.config.js",
    "jsconfig.json",
    "tsconfig.json",
    "components.json",
}

PROTECTED_DIRS = {"node_modules", ".git", ".next", "dist", ".vite"}


# ╔══════════════════════════════════════════════════════════════╗
# ║  HELPER — WebSocket messaging                               ║
# ╚══════════════════════════════════════════════════════════════╝

async def _ws_send(websocket, msg_type: str, message: str) -> None:
    """Send a typed message to the websocket (swallow errors)."""
    if not websocket:
        return
    try:
        await websocket.send_json({"type": msg_type, "message": message})
    except Exception:
        pass


def _generate_design_system_name(
    domain: str,
    heading_font: str,
    primary_hsl: str,
    description: str = "",
) -> str:
    """Generate a creative design system name like base44 does.

    Examples: 'Living Archive', 'Midnight Stack', 'Parchment & Obsidian'

    Searches both `domain` (brand.domain / app_type) AND the raw user
    `description` for topic keywords so that even generic app_types like
    "landing_page" resolve to a meaningful name.
    """
    # Domain → evocative name mapping
    _domain_names = {
        "library": "Living Archive",
        "archive": "Paper & Ink",
        "education": "Scholar Studio",
        "school": "Campus Canvas",
        "university": "Campus Canvas",
        "course": "Scholar Studio",
        "restaurant": "Saffron Kitchen",
        "food": "Harvest Table",
        "recipe": "Harvest Table",
        "cafe": "Morning Ritual",
        "coffee": "Morning Ritual",
        "ecommerce": "Commerce Canvas",
        "shop": "Merchant Studio",
        "store": "Merchant Studio",
        "market": "Merchant Studio",
        "fashion": "Editorial Grid",
        "clothing": "Editorial Grid",
        "luxury": "Obsidian Atelier",
        "premium": "Obsidian Atelier",
        "brutalist": "Brutalist Canvas",
        "portfolio": "Folio Black",
        "agency": "Studio Noir",
        "creative": "Void & Light",
        "design": "Void & Light",
        "saas": "Midnight Stack",
        "software": "Midnight Stack",
        "platform": "Midnight Stack",
        "startup": "Launch Pad",
        "analytics": "Data Horizon",
        "data": "Data Horizon",
        "dashboard": "Control Tower",
        "admin": "Control Tower",
        "healthcare": "Vital White",
        "health": "Vital White",
        "medical": "Clinical Blue",
        "clinic": "Clinical Blue",
        "finance": "Sterling Grid",
        "fintech": "Sterling Grid",
        "banking": "Vault Blue",
        "invest": "Vault Blue",
        "real_estate": "Urban Elevation",
        "property": "Urban Elevation",
        "real estate": "Urban Elevation",
        "travel": "Horizon Atlas",
        "trip": "Horizon Atlas",
        "hotel": "Grand Welcome",
        "fitness": "Kinetic Form",
        "gym": "Kinetic Form",
        "sport": "Kinetic Form",
        "music": "Sonic Wave",
        "audio": "Sonic Wave",
        "podcast": "Sonic Wave",
        "movie": "Cinematic Dark",
        "video": "Cinematic Dark",
        "film": "Cinematic Dark",
        "blog": "Prose & Type",
        "article": "Prose & Type",
        "news": "Press Layout",
        "media": "Press Layout",
        "social": "Pulse Network",
        "community": "Pulse Network",
        "chat": "Pulse Network",
        "booking": "Reserve & Go",
        "appointment": "Reserve & Go",
        "schedule": "Reserve & Go",
        "hospitality": "Grand Welcome",
        "tech": "Silicon Studio",
        "developer": "Silicon Studio",
        "api": "Silicon Studio",
        "tool": "Silicon Studio",
        "productivity": "Flow Studio",
        "task": "Flow Studio",
        "project": "Flow Studio",
        "crm": "Relation Grid",
        "hr": "Relation Grid",
        "hiring": "Relation Grid",
        "job": "Relation Grid",
        "event": "Stage Light",
        "concert": "Stage Light",
        "ticket": "Stage Light",
        "game": "Neon Arena",
        "gaming": "Neon Arena",
        "legal": "Charter Blue",
        "law": "Charter Blue",
        "logistics": "Route Zero",
        "delivery": "Route Zero",
        "shipping": "Route Zero",
        "agriculture": "Root & Soil",
        "farm": "Root & Soil",
        "environment": "Green Grid",
        "sustainability": "Green Grid",
        "eco": "Green Grid",
    }

    # Search in domain first, then fall through to description
    search_text = domain.lower()
    for keyword, name in _domain_names.items():
        if keyword in search_text:
            return name

    # Search the raw user description for stronger signal
    if description:
        desc_lower = description.lower()
        for keyword, name in _domain_names.items():
            if keyword in desc_lower:
                return name

    # Fallback: derive from font
    if heading_font:
        font_short = heading_font.split()[0]
        return f"{font_short} Studio"

    # Color-vibe fallback — map HSL hue range to evocative names
    if primary_hsl:
        import re as _re
        hue_match = _re.search(r"(\d+(?:\.\d+)?)\s*(?:deg|°)?", primary_hsl)
        if hue_match:
            hue = float(hue_match.group(1))
            if hue < 30 or hue >= 330:
                return "Crimson Canvas"
            elif hue < 60:
                return "Amber Studio"
            elif hue < 150:
                return "Verdant Grid"
            elif hue < 210:
                return "Cyan Horizon"
            elif hue < 270:
                return "Indigo Form"
            elif hue < 330:
                return "Violet Studio"

    # Last resort: pick randomly so repeated runs produce different names
    import random as _random
    _fallbacks = [
        "Obsidian Canvas", "Minimal Grid", "Aurora Studio",
        "Quantum Form", "Prism Layout", "Signal Studio",
        "Apex Grid", "Lumen Form", "Contour Studio", "Slate Zero",
        "Eclipse Form", "Polar Grid", "Meridian Studio", "Zenith Canvas",
    ]
    return _random.choice(_fallbacks)


async def _call_stitch_mcp(description: str, stitch_api_key: str, timeout: float = 45.0) -> str:
    """Call Stitch AI MCP directly from Python via JSON-RPC over stdio.

    Because the project uses the direct Anthropic API (not Claude Code CLI),
    MCP tools are invisible to Claude. We pre-call Stitch ourselves, extract
    the design reference HTML, and inject it into Claude's prompt so the
    layout/spacing/card patterns actually influence the generated code.

    Returns trimmed design reference HTML (≤8 KB), or "" on any failure.
    """
    if not stitch_api_key:
        logger.debug("Stitch MCP skipped — no STITCH_API_KEY")
        return ""

    env = {**os.environ, "STITCH_API_KEY": stitch_api_key}

    try:
        proc = await asyncio.create_subprocess_exec(
            "npx", "--yes", "@_davideast/stitch-mcp", "proxy",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=env,
        )
    except Exception as exc:
        logger.warning("Stitch MCP — failed to spawn process: %s", exc)
        return ""

    async def _write_msg(obj: dict) -> None:
        raw = (json.dumps(obj) + "\n").encode()
        proc.stdin.write(raw)
        await proc.stdin.drain()

    async def _read_msg(wait: float = 30.0) -> dict:
        line = await asyncio.wait_for(proc.stdout.readline(), timeout=wait)
        return json.loads(line.decode().strip())

    try:
        # ── MCP handshake ──────────────────────────────────────
        await _write_msg({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "lucid-ai", "version": "1.0"},
            },
        })
        await _read_msg()  # consume initialize response

        await _write_msg({"jsonrpc": "2.0", "method": "notifications/initialized"})

        # ── Call build_site ────────────────────────────────────
        await _write_msg({
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {
                "name": "build_site",
                "arguments": {"description": description},
            },
        })
        result = await asyncio.wait_for(_read_msg(wait=timeout), timeout=timeout)

        # ── Extract HTML from content array ───────────────────
        content_blocks = result.get("result", {}).get("content", [])
        parts = [
            b["text"] for b in content_blocks
            if isinstance(b, dict) and b.get("type") == "text" and b.get("text")
        ]
        html = "\n".join(parts)

        if not html:
            logger.warning("Stitch MCP returned empty content")
            return ""

        logger.info("Stitch MCP returned %d chars of design reference", len(html))
        return html[:8000]  # cap at ~8 KB to keep prompt manageable

    except asyncio.TimeoutError:
        logger.warning("Stitch MCP timed out after %.0fs — skipping", timeout)
        return ""
    except Exception as exc:
        logger.warning("Stitch MCP call failed (non-fatal): %s", exc)
        return ""
    finally:
        try:
            proc.stdin.close()
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


async def _send_phase(websocket, phase: int, title: str, description: str, status: str) -> None:
    """Send a structured phase event to the frontend for TaskProgress UI."""
    if not websocket:
        return
    try:
        await websocket.send_json({
            "type": "task_phase",
            "phase": phase,
            "title": title,
            "description": description,
            "status": status,
        })
    except Exception:
        pass


# ╔══════════════════════════════════════════════════════════════╗
# ║  HELPER — Build file tree string from workspace              ║
# ╚══════════════════════════════════════════════════════════════╝

def _build_file_tree(workspace_path: str, max_depth: int = 4) -> str:
    """Walk the workspace and build a human-readable file tree string."""
    lines = []
    base = os.path.basename(workspace_path) or "project"

    for root, dirs, files in os.walk(workspace_path):
        # Skip protected directories
        dirs[:] = sorted(d for d in dirs if d not in PROTECTED_DIRS)

        depth = root.replace(workspace_path, "").count(os.sep)
        if depth >= max_depth:
            dirs.clear()
            continue

        indent = "  " * depth
        rel = os.path.relpath(root, workspace_path)
        if rel == ".":
            lines.append(f"{base}/")
        else:
            lines.append(f"{indent}{os.path.basename(root)}/")

        sub_indent = "  " * (depth + 1)
        for f in sorted(files):
            if f.startswith(".") and f not in {".env", ".env.local", ".env.example"}:
                continue
            lines.append(f"{sub_indent}{f}")

    return "\n".join(lines[:200])  # Cap at 200 lines


# ╔══════════════════════════════════════════════════════════════╗
# ║  HELPER — Read TEMPLATE_MANIFEST.md                          ║
# ╚══════════════════════════════════════════════════════════════╝

def _read_manifest(workspace_path: str) -> str:
    """Read TEMPLATE_MANIFEST.md from the workspace root. Return '' if missing."""
    manifest_path = os.path.join(workspace_path, "TEMPLATE_MANIFEST.md")
    if os.path.isfile(manifest_path):
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            pass
    return ""


# ╔══════════════════════════════════════════════════════════════╗
# ║  HELPER — Read key template files for Claude context         ║
# ╚══════════════════════════════════════════════════════════════╝

# Key files per stack — these are the files Claude MUST see to generate
# correct imports, CSS variables, routing, and layout structure.
_KEY_FILES_BY_STACK = {
    "nextjs": [
        "src/app/globals.css",
        "src/app/layout.js",
        "src/app/(marketing)/layout.js",
        "src/app/(marketing)/page.js",
        "src/config/site.js",
        "src/config/navigation.js",
        "src/components/layout/MarketingHeader.jsx",
        "src/components/layout/MarketingFooter.jsx",
        "src/components/Providers.jsx",
    ],
    "react": [
        "src/styles/global.css",
        "src/index.css",
        "src/App.jsx",
        "src/main.jsx",
        "src/router/routes.jsx",
        "src/config/navigation.js",
        "src/components/layout/MainLayout.jsx",
        "src/components/layout/Sidebar.jsx",
        "src/components/layout/Header.jsx",
        "src/pages/dashboard/DashboardPage.jsx",
    ],
    "vue": [
        "src/assets/styles/global.css",
        "src/App.vue",
        "src/main.ts",
        "src/router/routes.js",
        "src/config/navigation.js",
        "src/components/layout/MainLayout.vue",
        "src/components/layout/AppSidebar.vue",
        "src/components/layout/AppHeader.vue",
    ],
}


def _read_key_template_files(workspace_path: str, stack: str) -> str:
    """Read actual source files from the template for Claude context.
    
    Returns a formatted string with file contents so Claude sees
    REAL code (always accurate) instead of relying on documentation.
    Max ~20KB total to stay within token budget.
    """
    # Determine which files to read
    stack_key = "nextjs"
    if "vue" in stack.lower():
        stack_key = "vue"
    elif "react" in stack.lower() and "next" not in stack.lower():
        stack_key = "react"

    candidates = _KEY_FILES_BY_STACK.get(stack_key, [])
    
    parts = []
    total_chars = 0
    MAX_TOTAL = 20000  # ~20KB total, ~5K tokens
    MAX_PER_FILE = 5000  # Cap individual files

    for rel_path in candidates:
        abs_path = os.path.join(workspace_path, rel_path)
        if not os.path.isfile(abs_path):
            continue
        try:
            with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            
            # Truncate large files
            if len(content) > MAX_PER_FILE:
                content = content[:MAX_PER_FILE] + "\n/* ... truncated ... */"
            
            total_chars += len(content)
            if total_chars > MAX_TOTAL:
                break
            
            parts.append(f"### {rel_path}\n```\n{content}\n```")
        except Exception:
            continue

    if not parts:
        return ""

    return (
        "\n\n## EXISTING TEMPLATE FILES (read these to understand what already exists)\n"
        "These are ACTUAL source files. Use the same patterns, imports, and CSS variables.\n\n"
        + "\n\n".join(parts)
    )


# ╔══════════════════════════════════════════════════════════════╗
# ║  LAYER 2 — Skills (component usage knowledge)               ║
# ║  Teaches Claude HOW to use each library correctly            ║
# ╚══════════════════════════════════════════════════════════════╝

# Skills per project type — maps to files in knowledge/components/
_SKILLS_BY_TYPE = {
    "admin": [
        "skill_datatable.md",
        "skill_recharts.md",
        "skill_crud_module.md",
        "skill_framer_motion.md",
    ],
    "landing": [
        "skill_landing_sections.md",
        "skill_framer_motion.md",
        "skill_recharts.md",  # For pricing charts, stats sections
    ],
}

# The directory where skills are stored
_SKILLS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "knowledge", "components",
)


def _load_skills(app_type: str, stack: str, layout_archetype: str = "") -> str:
    """Load relevant skill files based on project type.

    Skills teach Claude the EXACT API and usage patterns for each library.
    This is Layer 2 of the 3-layer architecture:
      Layer 1: MCP (live docs) — future
      Layer 2: Skills (local expertise) — THIS
      Layer 3: Plugins (bundled features) — future
    """
    # Use layout_archetype if provided (new system), else fall back to app_type string matching
    if layout_archetype:
        _admin_archetypes = {"admin_dashboard", "crm", "tms", "saas_dashboard", "ecommerce"}
        skill_key = "admin" if layout_archetype in _admin_archetypes else "landing"
    else:
        admin_types = {
            "admin_panel", "ecommerce", "saas_app", "analytics",
            "education", "medical", "fitness", "booking",
            "social", "food_restaurant", "travel", "real_estate",
        }
        skill_key = "admin" if app_type in admin_types else "landing"
    skill_files = _SKILLS_BY_TYPE.get(skill_key, [])
    
    # For Vue, swap React-specific skills
    if "vue" in stack.lower():
        skill_files = [f for f in skill_files if f not in {"skill_recharts.md", "skill_crud_module.md"}]
    
    parts = []
    total_chars = 0
    MAX_TOTAL = 15000  # ~15KB, ~4K tokens

    for filename in skill_files:
        filepath = os.path.join(_SKILLS_DIR, filename)
        if not os.path.isfile(filepath):
            continue
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
            
            total_chars += len(content)
            if total_chars > MAX_TOTAL:
                break
            
            parts.append(content)
        except Exception:
            continue

    if not parts:
        return ""

    return (
        "\n\n## COMPONENT USAGE SKILLS (follow these patterns EXACTLY)\n"
        "These show the CORRECT API for each library. Copy these patterns precisely.\n\n"
        + "\n\n---\n\n".join(parts)
    )

# ╔══════════════════════════════════════════════════════════════╗
# ║  HELPER — Parse JSON from Claude response text               ║
# ╚══════════════════════════════════════════════════════════════╝

def _parse_json_response(text: str) -> Optional[dict]:
    """Extract a JSON object from Claude's text response.
    
    Handles:
    - Pure JSON
    - JSON wrapped in ```json ... ``` code fences
    - JSON embedded in narrative text
    - Truncated JSON (attempts best-effort recovery)
    """
    if not text:
        return None

    cleaned = text.strip()

    # Strip markdown code fences
    if cleaned.startswith("```"):
        first_nl = cleaned.find("\n")
        if first_nl != -1:
            cleaned = cleaned[first_nl + 1:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:cleaned.rfind("```")]
    cleaned = cleaned.strip()

    # Attempt 1: direct parse
    try:
        result = json.loads(cleaned)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass

    # Attempt 2: find outermost { ... }
    start = cleaned.find("{")
    if start != -1:
        # Find the matching closing brace
        depth = 0
        last_valid_end = -1
        for i in range(start, len(cleaned)):
            if cleaned[i] == "{":
                depth += 1
            elif cleaned[i] == "}":
                depth -= 1
                if depth == 0:
                    last_valid_end = i
                    break

        if last_valid_end > start:
            try:
                result = json.loads(cleaned[start:last_valid_end + 1])
                if isinstance(result, dict):
                    return result
            except json.JSONDecodeError:
                pass

    # Attempt 3: truncated JSON recovery — try adding closing brackets
    if start != -1:
        substr = cleaned[start:]
        for fix in ["}", "]}", "\"]}}", "\"}]}", "\"]}]}"]:
            try:
                result = json.loads(substr + fix)
                if isinstance(result, dict) and result.get("files"):
                    logger.warning("Recovered truncated JSON with fix: +%s", fix)
                    return result
            except json.JSONDecodeError:
                continue

    logger.error("Failed to parse JSON from response (%d chars)", len(text))
    return None


# ╔══════════════════════════════════════════════════════════════╗
# ║  HELPER — Detect package manager                             ║
# ╚══════════════════════════════════════════════════════════════╝

def _detect_pm(workspace_path: str) -> str:
    """Detect package manager from lock files."""
    import shutil

    lock_map = {
        "pnpm-lock.yaml": "pnpm",
        "yarn.lock": "yarn",
        "bun.lockb": "bun",
        "package-lock.json": "npm",
    }

    for lock_file, pm_name in lock_map.items():
        if os.path.exists(os.path.join(workspace_path, lock_file)):
            if shutil.which(pm_name):
                return pm_name
            else:
                # PM not installed — remove lock file and fall back to npm
                try:
                    os.remove(os.path.join(workspace_path, lock_file))
                    logger.warning("%s found but %s not installed — falling back to npm", lock_file, pm_name)
                except Exception:
                    pass
                return "npm"

    return "npm"


# ╔══════════════════════════════════════════════════════════════╗
# ║  STEP 1 — call_claude_for_json()                             ║
# ║  Direct Claude Messages API call → returns parsed JSON dict  ║
# ╚══════════════════════════════════════════════════════════════╝

async def call_claude_for_json(
    system_prompt: str,
    user_prompt: str,
    api_key: str,
    websocket=None,
    max_tokens: int = MAX_TOKENS_PER_CALL,
    model: str = DEFAULT_MODEL,
) -> Optional[dict]:
    """Call Claude Messages API and return parsed JSON dict.
    
    Uses Claude's native tool_use for guaranteed valid JSON output.
    The API enforces JSON schema automatically — no escaping issues.
    On failure with Opus, automatically retries with Sonnet as fallback.
    """
    import httpx

    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }

    # Define the output tool — Claude MUST call this to respond
    write_files_tool = {
        "name": "write_project_files",
        "description": "Write all generated project files. Call this with the complete list of files to create or modify.",
        "input_schema": {
            "type": "object",
            "properties": {
                "files": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "Relative file path (e.g. src/components/HeroSection.jsx)"
                            },
                            "content": {
                                "type": "string",
                                "description": "Complete file source code"
                            }
                        },
                        "required": ["path", "content"]
                    }
                }
            },
            "required": ["files"]
        }
    }

    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_prompt}],
        "tools": [write_files_tool],
        "tool_choice": {"type": "tool", "name": "write_project_files"},
    }

    async def _make_request(use_model: str) -> Optional[dict]:
        payload["model"] = use_model
        try:
            async with httpx.AsyncClient(timeout=600.0) as client:
                response = await client.post(
                    CLAUDE_API_URL,
                    headers=headers,
                    json=payload,
                )

            if response.status_code != 200:
                error_text = response.text[:500]
                logger.error(
                    "Claude API error %d with %s: %s",
                    response.status_code, use_model, error_text,
                )
                # Instant user-facing error for auth/credit failures
                if response.status_code in (401, 403):
                    await _ws_send(websocket, "error", "❌ Anthropic API key is invalid. Check your API key in Settings.")
                elif response.status_code == 402 or (response.status_code == 400 and "credit balance" in error_text.lower()):
                    await _ws_send(websocket, "error", "❌ Anthropic API credits depleted. Add credits at console.anthropic.com.")
                elif response.status_code == 429:
                    await _ws_send(websocket, "error", "⚠️ Anthropic rate limit hit. Retrying in a moment...")
                elif response.status_code == 529:
                    await _ws_send(websocket, "error", "⚠️ Anthropic API overloaded. Retrying...")
                else:
                    await _ws_send(websocket, "error", f"❌ Claude API error ({response.status_code}). Try again.")
                return None

            data = response.json()
            
            # Check for truncation (stop_reason == "max_tokens")
            stop_reason = data.get("stop_reason", "")
            is_truncated = stop_reason == "max_tokens"
            if is_truncated:
                logger.warning("Claude (%s) response truncated (max_tokens). Attempting salvage...", use_model)
                await _ws_send(websocket, "progress", "⚠️ Response was long — salvaging complete files...")
            
            # Method 1: Extract from tool_use block (guaranteed valid JSON)
            for block in data.get("content", []):
                if block.get("type") == "tool_use" and block.get("name") == "write_project_files":
                    result = block.get("input", {})
                    if isinstance(result, dict) and result.get("files"):
                        files = result["files"]
                        
                        # TRUNCATION RECOVERY: If truncated, the LAST file
                        # likely has incomplete content. Remove it to avoid
                        # writing a broken file to disk.
                        if is_truncated and len(files) > 1:
                            last_file = files[-1]
                            last_content = last_file.get("content", "")
                            # Heuristic: if last file content is very short or
                            # doesn't end with a valid closing pattern, drop it
                            if (
                                len(last_content) < 50
                                or not last_content.rstrip().endswith((";", "}", ">", ");", "/>", "*/", "\n"))
                            ):
                                dropped = files.pop()
                                logger.warning(
                                    "Truncation recovery: dropped incomplete file '%s' (%d chars)",
                                    dropped.get("path", "?"), len(last_content),
                                )
                                await _ws_send(
                                    websocket, "progress",
                                    f"⚠️ Dropped 1 truncated file — {len(files)} complete files salvaged",
                                )
                        
                        logger.info(
                            "Claude (%s) returned %d files via tool_use%s",
                            use_model, len(files),
                            " (truncation-salvaged)" if is_truncated else "",
                        )
                        return {"files": files}
            
            # Method 2: Fallback — extract from text content (for compatibility)
            text = ""
            for block in data.get("content", []):
                if block.get("type") == "text":
                    text += block["text"]
            
            if text:
                result = _parse_json_response(text)
                if result and result.get("files"):
                    logger.info(
                        "Claude (%s) returned %d files via text fallback",
                        use_model, len(result["files"]),
                    )
                    return result
            
            logger.error("Claude response had no files (neither tool_use nor text)")
            return None

        except Exception as exc:
            logger.error("Claude API call failed (%s): %s", use_model, exc)
            return None

    # Try with primary model (Opus)
    result = await _make_request(model)
    if result:
        return result

    # Fallback to Sonnet if Opus failed
    if model != FALLBACK_MODEL:
        await _ws_send(websocket, "progress", f"⚠️ {model} failed, retrying with {FALLBACK_MODEL}...")
        logger.warning("Falling back from %s to %s", model, FALLBACK_MODEL)
        result = await _make_request(FALLBACK_MODEL)
        if result:
            return result

    return None


# ╔══════════════════════════════════════════════════════════════╗
# ║  STEP 2 — write_files_from_json()                           ║
# ║  Write generated files to disk, respecting PROTECTED_FILES   ║
# ╚══════════════════════════════════════════════════════════════╝

def write_files_from_json(
    json_response: dict,
    workspace_path: str,
) -> list[str]:
    """Write files from Claude's JSON response to the workspace.
    
    Expected format: {"files": [{"path": "relative/path", "content": "..."}]}
    
    Returns list of file paths that were successfully written.
    Skips PROTECTED_FILES and paths outside the workspace.
    """
    files = json_response.get("files", [])
    if not files:
        logger.warning("No files in JSON response")
        return []

    written = []

    for entry in files:
        rel_path = entry.get("path", "").strip()
        content = entry.get("content", "")

        if not rel_path or not content:
            continue

        # Security: prevent path traversal
        if ".." in rel_path or rel_path.startswith("/"):
            logger.warning("Skipping suspicious path: %s", rel_path)
            continue

        # Check protection
        basename = os.path.basename(rel_path)
        if basename in PROTECTED_FILES:
            logger.info("Skipping protected file: %s", rel_path)
            continue

        # Check if path starts with a protected directory
        first_dir = rel_path.split("/")[0] if "/" in rel_path else ""
        if first_dir in PROTECTED_DIRS:
            logger.info("Skipping file in protected dir: %s", rel_path)
            continue

        # Write file
        abs_path = os.path.join(workspace_path, rel_path)
        try:
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
            with open(abs_path, "w", encoding="utf-8") as f:
                f.write(content)
            written.append(rel_path)
        except Exception as exc:
            logger.error("Failed to write %s: %s", rel_path, exc)

    logger.info("Wrote %d / %d files to %s", len(written), len(files), workspace_path)
    return written


# Per-domain research hints for consumer websites (module-level so both
# gemini_deep_research and _generate_new_project_inner can access it)
_CONSUMER_HINTS: dict = {
    "food_restaurant": {
        "refs": "Nobu, The French Laundry, Eleven Madison Park, local fine-dining restaurant websites",
        "pages": "/ (hero + featured dishes + chef story + gallery preview + hours + reservations CTA), /menu (full menu grouped by category: starters, mains, desserts, drinks — with photo + name + description + price), /reservations (date/time picker + party size + contact form), /gallery (masonry photo grid: food + ambiance + kitchen), /about (chef biography + kitchen philosophy + awards), /contact (location + Google Map embed + phone + hours)",
        "entities": "MenuItem (name, category, description, price, photo, dietary_tags, is_featured), Reservation (date, time, party_size, guest_name, email, phone, status, notes)",
        "key_ui": "menu category tabs, dish card (photo + name + price + dietary badges), reservation form with date/time picker, masonry gallery, chef story section with portrait",
        "vibe": "warm, culinary, upscale — rich food photography, warm amber/cream/dark palette",
    },
    "travel": {
        "refs": "G Adventures, Intrepid Travel, Airbnb Experiences, National Geographic Expeditions",
        "pages": "/ (hero with search widget + featured destinations + popular tours + how it works + traveler testimonials), /destinations (destination grid with continent filter), /destinations/:slug (destination overview + best time to visit + featured tours + photo gallery), /tours (tour catalog with filter: duration, price, difficulty, destination), /tours/:slug (full itinerary accordion + inclusions/exclusions + pricing tiers + group size + booking form), /about (company story + team + why choose us + sustainability), /blog (travel tips + trip reports), /contact",
        "entities": "Destination (name, country, continent, hero_image, description, best_season, featured), Tour (title, destination, duration_days, price, difficulty, group_size, highlights, itinerary_days, inclusions, exclusions, cover_image, rating, review_count), Review (author, avatar, tour, rating, text, travel_date)",
        "key_ui": "destination card (full-bleed photo + country badge + overlay name), tour card (photo + duration badge + price + rating stars + difficulty pill), itinerary day accordion, map embed, traveler review card with avatar",
        "vibe": "adventurous, aspirational — vivid destination photography, teal/sky-blue palette with white",
    },
    "real_estate": {
        "refs": "Zillow, Redfin, Compass Real Estate, Sotheby's International Realty",
        "pages": "/ (hero with search bar + featured listings carousel + neighborhood highlights + agent testimonials + stats counters), /properties (listing grid with sidebar filters: price range, type, beds, baths, sqft, location), /properties/:id (full photo gallery + key details + description + amenities list + map + floor plan + contact agent form), /agents (agent directory with photo + specialization + listing count), /agents/:id (agent profile + active listings + bio + contact form), /neighborhoods (area guide cards), /about (company story + awards + stats), /contact",
        "entities": "Property (title, type, status, price, beds, baths, sqft, address, city, description, photos, amenities, agent_id, year_built, garage, is_featured), Agent (name, photo, title, phone, email, bio, specialization, listing_count, rating, years_experience), Neighborhood (name, city, description, cover_image, avg_price, walkability, schools)",
        "key_ui": "property card (photo carousel + price badge + address + bed/bath/sqft icons), map view toggle, filter sidebar with range sliders, agent card (photo + name + rating + phone), photo gallery lightbox",
        "vibe": "professional, premium, trustworthy — white/slate with gold or deep navy accent",
    },
    "fitness": {
        "refs": "Equinox, Barry's Bootcamp, SoulCycle, F45 Training, CrossFit gym sites",
        "pages": "/ (hero video/image + class teasers + trainers spotlight + membership tiers + transformation testimonials + join CTA), /classes (class catalog with filter: type, level, trainer, duration), /classes/:slug (class detail + trainer + weekly schedule + book a spot CTA), /trainers (trainer directory: photo + name + specialties + social), /trainers/:id (trainer profile + certifications + their classes + bio), /membership (pricing tiers with feature comparison table), /schedule (weekly timetable grid), /about (gym story + facilities + location), /contact",
        "entities": "Class (name, type, level, duration_min, trainer, description, cover_image, max_spots, equipment), Trainer (name, photo, specialties, bio, certifications, instagram, class_count), MembershipTier (name, price_monthly, price_annual, features, is_popular, cta_label)",
        "key_ui": "class card (banner image + level badge + duration + trainer avatar + spots left), trainer card (square photo + name + specialty tags + Instagram link), weekly timetable grid (days × time slots), membership tier cards (with popular badge on middle tier)",
        "vibe": "energetic, bold, motivational — dark background with electric orange or lime accent, strong sans-serif headlines",
    },
    "medical": {
        "refs": "Mayo Clinic, Cleveland Clinic, Kaiser Permanente, boutique clinic/hospital websites",
        "pages": "/ (hero with appointment CTA + services grid + featured doctors + trust stats + patient testimonials + accreditation logos), /services (medical service catalog grouped by department), /services/:slug (service detail + conditions treated + procedures + specialists), /doctors (doctor directory with photo + specialization + availability indicator), /doctors/:id (doctor profile + credentials + education + publications + patient reviews + online booking widget), /appointments (step-by-step booking: select service → select doctor → pick date/time → patient info), /about (institution story + mission + accreditations + stats), /contact (locations map + emergency hotline + department contacts + hours)",
        "entities": "Doctor (name, photo, specialization, title, hospital, languages, education, bio, rating, review_count, next_available, is_featured), Service (name, department, description, icon, conditions_treated, procedures), Appointment (patient_name, email, phone, service, doctor, date, time, notes, status), Review (patient_name, doctor, rating, text, date)",
        "key_ui": "doctor card (photo + name + specialty + rating + next available slot button), service card (icon + name + department badge + brief description), appointment step wizard, stat counter animation (patients served, doctors, years, awards)",
        "vibe": "clean, clinical, trustworthy — white with calm blue or teal accent, clear legible typography",
    },
    "education": {
        "refs": "Coursera, Udemy, MasterClass, Khan Academy, Springboard, General Assembly",
        "pages": "/ (hero with search + featured courses + how it works steps + instructor spotlights + student outcomes + partner logos + enroll CTA), /courses (catalog with filter: subject, level, duration, price range, rating), /courses/:id (course detail: overview + what you'll learn bullets + syllabus accordion + instructor card + student reviews + pricing + enroll CTA), /instructors (instructor directory), /instructors/:id (instructor profile + bio + courses + rating + student count), /pricing (subscription plans vs individual course comparison), /about (institution story + accreditations + outcomes stats), /contact",
        "entities": "Course (title, slug, subject, level, duration_hours, price, thumbnail, description, what_you_learn, instructor_id, rating, review_count, student_count, is_featured, syllabus_sections), Instructor (name, photo, title, bio, expertise, rating, course_count, student_count), Review (student_name, avatar, course_id, rating, text, date)",
        "key_ui": "course card (thumbnail + level badge + rating stars + student count + price), syllabus accordion (module title + lesson list), instructor card (photo + name + rating + students), pricing tier cards with feature checklist",
        "vibe": "approachable, empowering, bright — white with purple or teal accent, friendly sans-serif",
    },
    "entertainment": {
        "refs": "Netflix, Disney+, HBO Max, IMDb, Letterboxd, streaming platform UIs",
        "pages": "/ (hero featured title with trailer CTA + trending now row + new releases row + genre rows + continue watching), /browse (full catalog with filters: genre, year, rating, type: movie/series), /browse/:id (title detail page: hero backdrop + poster + synopsis + cast grid + trailer embed + similar titles row + add to watchlist), /search (search bar + results grid with instant filtering), /genres/:genre (genre-filtered catalog with featured banner), /watchlist (saved/bookmarked titles)",
        "entities": "Title (name, type, genre, year, rating, duration, synopsis, poster_url, backdrop_url, trailer_url, cast, director, is_featured, is_trending), CastMember (name, photo, character, role), Genre (name, slug, icon, color)",
        "key_ui": "content card (poster + hover overlay with play button + title + year + rating badge), hero banner (full-bleed backdrop + gradient overlay + title logo + synopsis + watch/add buttons), horizontal scroll row with section heading, cast card grid, genre pill filters",
        "vibe": "cinematic, premium, dark — near-black background with vivid accent (red, electric blue, or purple), large imagery, Netflix-style density",
    },
    "booking": {
        "refs": "Calendly, Booksy, Vagaro, Square Appointments, Fresha",
        "pages": "/ (hero + service categories + how it works 3-steps + featured providers + trust badges + testimonials), /services (service catalog grouped by category with price + duration), /services/:id (service detail + who performs it + pricing + duration + book CTA), /booking (multi-step: 1. select service → 2. select provider → 3. pick date/time slot → 4. enter details → 5. confirm), /providers (provider directory with photo + specialties + rating + next available), /providers/:id (provider profile + their services + availability calendar + reviews + book CTA), /about, /contact",
        "entities": "Service (name, category, duration_min, price, description, provider_ids, icon), Provider (name, photo, title, bio, specialties, rating, review_count, next_slot), Appointment (service_id, provider_id, date, time, client_name, client_email, client_phone, notes, status), Review (provider_id, client_name, rating, text, date)",
        "key_ui": "service card (icon + name + category badge + duration + price + book CTA), provider card (avatar + name + specialties + rating + next available slot), booking step wizard with progress bar, time slot grid (available/booked states), calendar date picker",
        "vibe": "clean, efficient, friendly — white with primary brand accent (often teal, purple, or green)",
    },
    "social": {
        "refs": "Twitter/X, Instagram, Reddit, LinkedIn, Threads, Mastodon",
        "pages": "/ (main feed/timeline with post cards + trending sidebar + suggested follows), /profile/:username (user profile: avatar + cover + bio + follower stats + posts tab + media tab + likes tab), /explore (trending topics + suggested users + popular posts grid), /messages (conversation list sidebar + active chat with message bubbles), /notifications (activity feed: likes, comments, follows, mentions), /create (post creation: text + media upload + audience selector), /settings (account + privacy + notifications + appearance)",
        "entities": "Post (author_id, content, media_urls, like_count, comment_count, share_count, created_at, tags), User (username, display_name, avatar, cover_image, bio, follower_count, following_count, post_count, is_verified, joined_date), Comment (post_id, author_id, content, like_count, created_at), Notification (user_id, type, actor, entity, read, created_at)",
        "key_ui": "post card (avatar + username + timestamp + content + media + action bar: like/comment/share/bookmark), user card (avatar + name + bio + follow button), chat bubble (left/right alignment, timestamp, read receipt), notification item (icon + text + time + unread dot)",
        "vibe": "modern, social, dynamic — light or dark theme with strong brand color, card-based dense layout",
    },
    "saas_app": {
        "refs": "Linear, Vercel, Stripe Dashboard, Notion, Figma, Loom, Intercom",
        "pages": "/ (marketing landing: hero with product screenshot + animated demo + feature grid + logos social proof + pricing preview + testimonials + CTA), /features (expanded feature breakdown with screenshots and animations), /pricing (detailed pricing table: Free/Pro/Enterprise with feature comparison), /about (company story + team photos + investors + values), /blog (product updates + tutorials + changelog), /contact (support + sales forms), /app/dashboard (after-login: main dashboard with KPIs + recent activity + quick actions), /app/settings (account + billing + integrations + team members)",
        "entities": "Feature (name, description, icon, screenshot_url, category), PricingTier (name, price_monthly, price_annual, cta, is_popular, features_list), Testimonial (quote, author_name, author_title, author_avatar, company, company_logo), BlogPost (title, slug, excerpt, cover, author, category, published_at, reading_time)",
        "key_ui": "product screenshot in browser-frame mockup, feature card (icon + title + description), pricing tier card (with popular badge), testimonial card (large quote + avatar + logo), dashboard KPI card (icon + metric + trend), app sidebar (logo + nav groups + user avatar pinned bottom)",
        "vibe": "polished, developer-friendly, modern — dark or light with electric blue/purple accent, product screenshots as hero",
    },
    "marketplace": {
        "refs": "Airbnb, Etsy, Fiverr, TaskRabbit, Upwork, Rover, Vinted",
        "pages": "/ (hero search bar + featured listings grid + category icon grid + how it works 3-steps + trust badges + testimonials + seller CTA), /listings (browse grid with sidebar filters: category, price range, location, rating, sort), /listings/:id (listing detail: photo gallery + title + price + description + seller card + reviews + map + booking/contact form + similar listings), /categories/:slug (category-filtered listings with editorial header), /search (search results with instant filter update), /profile/:id (seller public profile + their listings + reviews + response rate + member since), /post-listing (multi-step: category → details → pricing → photos → publish), /messages (inbox with conversation threads), /about (how it works + trust & safety), /contact",
        "entities": "Listing (title, category, description, price, unit, photos, location, seller_id, rating, review_count, is_featured, tags, status), Seller (name, avatar, bio, location, member_since, rating, review_count, listing_count, response_rate, is_verified), Review (listing_id, buyer_name, buyer_avatar, rating, text, date), Category (name, slug, icon, color, listing_count, description)",
        "key_ui": "listing card (full-bleed photo + category badge + title + price/unit + rating stars + seller avatar), search bar with location/keyword autocomplete, category icon grid with hover effects, seller trust badges (verified + rating + review count), photo gallery carousel with thumbnails, review card with star rating, map with listing pins",
        "vibe": "trustworthy, community-driven, approachable — clean white with warm accent (orange, teal, or purple), photography-forward, generous whitespace",
    },
    "events": {
        "refs": "Eventbrite, Luma, Meetup, Ra.co, Partiful, Ticketmaster",
        "pages": "/ (hero with event search: name + location + date + category, featured events grid, upcoming near you carousel, browse by category grid, create event CTA), /events (full catalog with filters: date range, category, price: free/paid, format: in-person/online, location), /events/:id (event detail: full-bleed cover + title + date/time + location map + organizer + description + attendee avatars + ticket tiers + register CTA), /categories/:slug (category events with genre banner), /organizers/:id (organizer public profile + their events + follower count), /create (multi-step: basic info → date/location → tickets → cover → publish), /my-tickets (user's registered events with QR code), /search (instant search results), /about, /contact",
        "entities": "Event (title, description, category, start_date, end_date, timezone, location, address, is_online, online_url, organizer_id, cover_image, is_featured, is_free, tags, status), Organizer (name, logo, bio, website, follower_count, event_count, is_verified), TicketType (event_id, name, price, quantity, quantity_sold, description, sale_ends), Registration (event_id, attendee_name, email, ticket_type_id, quantity, total_paid, status, qr_code)",
        "key_ui": "event card (cover image + date badge overlay + title + location + price pill + attendee avatar stack), date badge (large day + month in corner), ticket tier card (name + price + quantity remaining), attendee stack (5 small avatars + count), category filter pills, calendar date range picker, map embed with venue pin",
        "vibe": "vibrant, social, energetic — bold event photography with gradient overlays, festive accent (purple, electric blue, or vivid orange), dark hero with light content sections",
    },
    "finance": {
        "refs": "Mint, YNAB, Robinhood, Betterment, Personal Capital, Wise, Revolut",
        "pages": "/ (marketing hero: value prop + key metrics + security badges + feature grid + testimonials + get started CTA), /dashboard (balance overview cards + spending donut chart + recent transactions + budget progress bars + savings goals row + quick actions), /transactions (sortable filterable table: date, merchant, category icon, amount, account, search bar), /budgets (category budget cards: icon + name + progress bar + spent/limit + alert on overspend), /accounts (linked account cards: bank logo + type + balance + last synced + link new), /goals (savings goal cards: icon + name + target + current + progress bar + ETA), /reports (monthly spending by category chart + income vs expenses bar + net worth line + date range picker), /settings (profile + security + notifications + linked accounts), /about, /pricing",
        "entities": "Transaction (date, description, amount, type: debit/credit, category, category_icon, account_id, status, merchant_logo, notes), Account (name, type: checking/savings/investment/credit_card, balance, institution, last_synced, is_linked, color), Budget (category, category_icon, limit_amount, spent_amount, period: monthly/weekly, color, alert_threshold), Goal (name, target_amount, current_amount, target_date, icon, color, category, monthly_contribution)",
        "key_ui": "account card (institution logo + type badge + masked number + balance + trend arrow), transaction row (merchant logo + category chip + date + amount colored debit/credit), budget progress bar (category icon + name + bar with fill color + spent vs limit text), donut chart with legend, goal card (icon + name + ring progress + days remaining), net worth counter animation",
        "vibe": "trustworthy, professional, data-rich — clean white or deep slate with green/blue accent, monospaced numbers, financial dashboard density with breathing whitespace",
    },
    "fashion": {
        "refs": "ASOS, Net-a-Porter, Shopbop, Farfetch, SSENSE, Reformation, Zara",
        "pages": "/ (hero full-bleed lookbook image + new arrivals grid + shop by category + trending now row + editorial/campaign feature + brand story strip), /shop (product grid: 3-col desktop with sidebar filters: category, size, color, price range, brand, new arrivals toggle), /shop/:id (product detail: multi-photo gallery + name + brand + price + size selector + color swatches + add to bag + model measurements + material + delivery info + you may also like row), /categories/:slug (category landing with editorial banner), /editorial (fashion stories + lookbooks grid), /wishlist (saved items grid + add to bag), /bag (cart with item list + order summary + checkout CTA), /about (brand story + values + sustainability), /contact",
        "entities": "Product (name, brand, category, subcategory, price, sale_price, currency, sizes, colors, photos, hover_photo, description, material, care, is_new, is_featured, is_sale, rating, review_count, model_size), Brand (name, logo, description, is_luxury, country), Review (product_id, customer_name, avatar, rating, size_purchased, fit: runs_small/true_to_size/runs_large, text, date), LookbookItem (title, cover_image, description, products, published_at)",
        "key_ui": "product card (full-bleed photo + hover second photo + brand name + product name + price + sale badge), size selector (letter buttons with sold-out strikethrough), color swatch dots with tooltip, editorial hero (full-screen image + minimal white text overlay), wishlist heart with animation, gallery with main image + thumbnail strip + zoom modal",
        "vibe": "editorial, luxury, aspirational — bold typography on white, photography-first, black/white base with metallic or vivid accent, oversized headlines, refined hover effects",
    },
    "automotive": {
        "refs": "AutoTrader, Cars.com, CarGurus, TrueCar, Tesla.com, Carvana, Autolist",
        "pages": "/ (hero with quick search form: make/model/year/zip + featured listings grid + browse by body type row + browse by brand logos + trust stats), /cars (vehicle grid with sidebar filters: make, model, year range, price min/max, mileage max, fuel type, transmission, color, condition: new/used/certified), /cars/:id (vehicle detail: photo gallery + key specs row + price + carfax badge + dealer card + contact/test drive form + financing calculator + similar vehicles), /dealers (dealer directory with map + list toggle), /dealers/:id (dealer profile + logo + inventory + rating + reviews + hours + directions), /sell (instant valuation: enter year/make/model/mileage → estimated value range), /compare (side-by-side table for 2-3 vehicles), /financing (loan calculator + monthly payment breakdown), /about, /contact",
        "entities": "Vehicle (year, make, model, trim, price, mileage, fuel_type, transmission, exterior_color, interior_color, photos, vin, body_type, drivetrain, engine, doors, mpg_city, mpg_hwy, features, condition: new/used/certified, carfax_url, dealer_id, is_featured), Dealer (name, logo, address, city, state, phone, email, rating, review_count, inventory_count, is_certified_dealer, hours), Review (dealer_id, author_name, rating, text, date, verified_buyer)",
        "key_ui": "vehicle card (photo + year/make/model bold title + price + mileage + fuel badge + condition badge + save button), spec badge row (icons for fuel/trans/drivetrain/engine), photo gallery (main image + thumbnail strip + 360 indicator), financing calculator (loan amount + down + rate → monthly payment), dealer trust badge (certified logo + rating + review count), comparison table with sticky header",
        "vibe": "bold, confident, trustworthy — strong vehicle photography, white with dark navy or electric blue accent, clean specs-forward technical aesthetic, high-information density",
    },
}


def _extract_research_section(text: str, header: str, max_chars: int = 2000) -> str:
    """Extract the content of a ===HEADER=== block from Gemini research output."""
    if header not in text:
        return ""
    start = text.index(header) + len(header)
    rest  = text[start:]
    end_match = rest.find("===")
    end = start + end_match if end_match != -1 else start + max_chars
    return text[start:end].strip()


def _extract_layout_archetype(research: str, fallback_classification: dict) -> dict:
    """Extract the confirmed layout archetype from Gemini's ===CLASSIFICATION=== section.

    Gemini may correct the initial classification after research. This reads its
    confirmed decision and builds an updated classification dict.
    """
    from knowledge.loader import LAYOUT_ARCHETYPES, _build_rich_classification

    section = ""
    if "===CLASSIFICATION===" in research:
        _cs = research.index("===CLASSIFICATION===") + len("===CLASSIFICATION===")
        _ce_match = research[_cs:].find("===")
        _ce = _cs + _ce_match if _ce_match != -1 else _cs + 800
        section = research[_cs:_ce].strip()

    if not section:
        return fallback_classification

    # Parse layout_archetype line
    layout = fallback_classification["layout_archetype"]
    domain = fallback_classification["domain"]

    for line in section.splitlines():
        line = line.strip()
        if line.startswith("layout_archetype:"):
            val = line.split(":", 1)[1].strip().lower()
            if val in LAYOUT_ARCHETYPES:
                layout = val
        elif line.startswith("domain:"):
            val = line.split(":", 1)[1].strip().lower()
            if val:
                domain = val

    result = _build_rich_classification(layout, domain)
    logger.info("Post-research classification: layout=%s domain=%s", layout, domain)
    return result


# ╔══════════════════════════════════════════════════════════════╗
# ║  STEP 3 — gemini_deep_research()                            ║
# ║  Ultra-deep product research via Gemini with internet search ║
# ╚══════════════════════════════════════════════════════════════╝

async def gemini_deep_research(
    description: str,
    classification: dict,   # rich dict from classify_project_type_ai
    stack: str,
    gemini_key: str,
    websocket,
) -> str:
    """Ultra-deep product research via Gemini with internet search.

    Analyzes 3-5 real products in the user's domain and extracts a
    CODE-READY BLUEPRINT — not a report. Returns the raw text blueprint.
    """
    await _ws_send(websocket, "progress", "🔬 Researching top products in this domain...")

    layout_archetype = classification.get("layout_archetype", "consumer_website")
    domain = classification.get("domain", "general")
    is_single_page = classification.get("is_single_page", False)
    has_admin = classification.get("has_admin_features", False)

    # Human-readable archetype label for the prompt
    _archetype_label = {
        "single_page_landing": "single-page landing page (ONE scrollable page, no sub-routes)",
        "consumer_website": "multi-page consumer website (public-facing, top nav)",
        "admin_dashboard": "admin dashboard (sidebar + CRUD + KPI cards)",
        "crm": "CRM system (sidebar + customer pipeline + contacts + deals)",
        "tms": "Transportation Management System (sidebar + shipments + fleet + routes)",
        "saas_dashboard": "SaaS workspace dashboard (sidebar + project/task management)",
        "ecommerce": "e-commerce platform (online store + order management)",
        "blog": "blog / content publishing platform (articles + authors + categories)",
        "portfolio": "portfolio / showcase site (work samples + bio + contact)",
        "marketplace": "marketplace platform (buyers + sellers + listings)",
    }.get(layout_archetype, layout_archetype.replace("_", " "))

    research_prompt = f"""You are an AUTONOMOUS PRODUCT RESEARCHER and UI/UX ARCHITECT with full internet search access.
Research and blueprint a production-quality web application.

PROJECT: "{description}"
INITIAL TYPE: {_archetype_label} | DOMAIN: {domain}
TECH STACK: {stack}

═══════════════════════════════════════════════════════════════
STEP 1 — CONFIRM CLASSIFICATION
═══════════════════════════════════════════════════════════════
Review the initial type. Correct it ONLY if the description clearly implies something different.

LAYOUT OPTIONS:
• single_page_landing  — ONE scrollable page, anchor links. "landing page for X"
• consumer_website     — Multi-page public site, top header nav. "X website"
• admin_dashboard      — Sidebar + CRUD tables + KPIs. Management/operations tools
• crm                  — Sidebar + pipeline + contacts + deals. Customer management
• tms                  — Sidebar + shipments + fleet + routes. Transport/logistics
• saas_dashboard       — Sidebar + workspace. Project/task/team management
• ecommerce            — Products + cart + orders. Online store
• blog                 — Articles + authors + categories. Content platform
• portfolio            — Work samples + bio + contact. Personal/agency showcase
• marketplace          — Buyers + sellers + listings. Two-sided platform

===CLASSIFICATION===
layout_archetype: {layout_archetype}
domain: {domain}
is_single_page: {"yes" if is_single_page else "no"}
nav_style: {"top_header" if not has_admin else "sidebar_left"}
has_admin_features: {"yes" if has_admin else "no"}
reasoning: [confirm or explain any correction in 1 sentence]

(Correct the layout_archetype line above ONLY if clearly wrong — keep others matching)

═══════════════════════════════════════════════════════════════
STEP 2 — INTERNET RESEARCH
═══════════════════════════════════════════════════════════════
Search for TOP 3-5 real products/sites most similar to "{description}".
If "{description}" names a REAL brand → study THAT site FIRST as primary reference.

===SITES_ANALYZED===
1. [Name] ([URL]) — [what makes their design/UX excellent and relevant]
2. [Name] ([URL]) — [what they do well]
3. [Name] ([URL]) — [what they do well]

═══════════════════════════════════════════════════════════════
STEP 3 — DESIGN SYSTEM (always from research — no generic defaults)
═══════════════════════════════════════════════════════════════

===CSS_VARIABLES===
--primary: [hsl] | --primary-foreground: [hsl]
--secondary: [hsl] | --secondary-foreground: [hsl]
--accent: [hsl] | --accent-foreground: [hsl]
--background: [hsl] | --foreground: [hsl]
--card: [hsl] | --card-foreground: [hsl]
--muted: [hsl] | --muted-foreground: [hsl]
--border: [hsl] | --ring: [hsl]
--destructive: [hsl] | --destructive-foreground: [hsl]
--radius: [value]rem

===FONTS===
heading: [Font Name] ([Google Fonts URL + weights 600;700;800;900])
body: [Font Name] ([Google Fonts URL + weights 400;500;600])
hero_size: [px] / [line-height] / [letter-spacing]
h2_size: [px] / [weight]
body_size: [px] / [line-height]
overall_vibe: [2-3 descriptive words — e.g. "bold minimal dark"]

═══════════════════════════════════════════════════════════════
STEP 4 — STRUCTURE (adapts to the confirmed layout_archetype)
═══════════════════════════════════════════════════════════════

[OUTPUT THIS BLOCK IF layout_archetype = single_page_landing]
===HEADER===
logo: [brand name from description]
nav_items: [4-6 anchor links: e.g. Features, How It Works, Pricing, Testimonials, FAQ, Contact]
cta: "[CTA text]" | classes: [Tailwind button classes]
sticky: yes | blur_bg: yes

===SECTIONS===
Sections in conversion-optimal order — 8-12 sections minimum. Every section fully specified.

[section: hero]
headline: "[compelling value prop — ORIGINAL copy, not placeholder]"
subheadline: "[1-2 supporting sentences with real specificity]"
layout: [centered|split-left|split-right|full-bleed]
background: [exact Tailwind gradient or bg classes from research]
cta_primary: "[text]" | cta_secondary: "[text if applicable]"
hero_image: [screenshot-in-frame|illustration|photo|gradient-blob|none]
animation: [fade-up|slide-in|scale-in]

[section: social_proof]
[Add if real sites have it — logos bar, user count, ratings]

[section: features]
headline: "[section heading]"
layout: [grid-3|grid-4|alternating]
items: (6-8 domain-specific features with Lucide icon names)

[section: how_it_works]
[Add step-by-step if applicable]

[section: testimonials]
layout: [grid|carousel|featured]
items: (3-4 realistic testimonials — real names, job titles, company names)

[section: pricing]
headline: "[headline]"
plans: (2-3 tiers with realistic pricing and feature lists)

[section: faq]
items: (5-8 real questions users have about this domain)

[section: cta_final]
headline: "[urgency/excitement headline]"
cta: "[action button text]"

[ADD any other sections real sites in this domain use]

===FOOTER===
columns: [3-4 columns with domain-appropriate links]
bottom: "[copyright + tagline]"

[OUTPUT THIS BLOCK IF layout_archetype = consumer_website OR marketplace OR portfolio OR blog]
===HEADER===
logo: [brand name]
nav_items: [5-7 page links from research]
cta: "[optional primary CTA text]"
sticky: yes | blur_bg: yes

===PAGES===
Study REAL {domain} sites and list EVERY page their sites have (minimum 5-6 pages).

[page: home]
path: /
hero_headline: "[compelling headline]"
hero_subheadline: "[supporting text]"
sections: [comma-separated list of sections on this page]
purpose: [what this page achieves]

[page: about]
path: /about
sections: [team, story, values, mission, stats]
purpose: [...]

[ADD every page this type of site needs — each with: path, hero_headline, sections, purpose]
[For blog: add /articles, /articles/:slug, /write, /categories, /authors/:username, /search]
[For marketplace: add /listings, /listings/:id, /sell, /categories/:slug, /profile/:id]
[For portfolio: add /work, /work/:slug, /about, /contact]

===FOOTER===
columns: [3-4 columns]
bottom: "[copyright + tagline]"

[OUTPUT THIS BLOCK IF layout_archetype = admin_dashboard OR crm OR tms OR saas_dashboard OR ecommerce]
===HEADER===
logo: [tool/product name from description]
topbar: notifications icon | help icon | user avatar with dropdown

===SIDEBAR===
Study REAL {layout_archetype.replace("_", " ")} products to determine the EXACT sidebar structure for this domain.
Name groups after what this specific tool manages.

[group: Overview]
items:
  - label: Dashboard | path: /dashboard | icon: LayoutDashboard
  [add other overview items this domain needs]

[group: [Primary Entity Group]] \u2190 e.g. "Shipments", "Customers", "Projects", "Products"
items:
  - [ALL items this primary group needs]

[group: [Secondary Group]] \u2190 if needed
items:
  - [items]

[group: Settings]
items:
  - label: Settings | path: /settings | icon: Settings
  - label: Help | path: /help | icon: HelpCircle

===DASHBOARD_KPIS===
4-6 KPIs that matter for THIS specific {domain} {layout_archetype.replace("_", " ")}:
1. label: [KPI name] | value: [realistic example value] | change: [\u00b1X%] | trend: [up|down] | icon: [LucideIcon] | color: [tailwind color class]

===ENTITIES===
Data entities this tool manages. Base on research of REAL {layout_archetype.replace("_", " ")} products.
Minimum 3-4 entities for this domain.

[entity: EntityName]
purpose: [what this entity represents in the {domain} domain]
fields:
  - name: [field] | type: [string|number|boolean|enum|date|email|url|textarea|select|file] | required: [yes|no] | inList: [yes|no] | inForm: [yes|no] | options: [if enum: value1,value2,value3]
  [LIST ALL FIELDS from research — minimum 8-10 fields per entity]
mock_data: [12-15 rows of realistic domain-specific data — real names, real statuses, real values]

[REPEAT for every entity]

===STATUS_BADGES===
[status_value]: bg-[color]-100 text-[color]-800 dark:bg-[color]-900/30 dark:text-[color]-400
(all status values from all entities)

═══════════════════════════════════════════════════════════════
STEP 5 — DOMAIN INTELLIGENCE (always output)
═══════════════════════════════════════════════════════════════

===DOMAIN_MUST_HAVES===
5-8 features ALL top {domain} {layout_archetype.replace("_", " ")}s have — missing = product feels incomplete:
1. feature_name: [name] | type: [section|component|interaction] | implementation: [how to build in React/Next.js] | why_essential: [1 sentence]

===KEY_COMPONENTS===
Domain-specific reusable components from research (make it feel like the REAL thing):

[component: ComponentName]
purpose: [what it does]
props: [data it receives]
layout: [key Tailwind structure]

(4-8 components unique to this domain — NOT generic Button/Card)

===UI_PATTERNS===
card: [full card Tailwind class string]
card_hover: [hover transition classes]
button_primary: [full Tailwind — size, color, radius, hover]
button_ghost: [exact Tailwind]
section_spacing: [padding pattern e.g. "py-20 md:py-28 px-4 sm:px-6 lg:px-8"]
max_width: [max-width + margin e.g. "max-w-6xl mx-auto"]
badge: [pill badge classes]
input: [form input classes]
overall_vibe: [2-3 descriptive words from research]

===COPY_TONE===
hero_headline_style: [punchy|formal|warm|bold — max chars and style description]
body_copy_style: [tone description]
cta_style: [verb style]

===IMAGE_SOURCES===
hero: [treatment from research]
content_images: https://picsum.photos/seed/[descriptive_seed]/800/600
avatars: https://i.pravatar.cc/150?u=[unique_string]
icons: Lucide React

CRITICAL: Every value from REAL internet research. Original copy. Domain-specific. Production-quality.
"""

    # Call Gemini via direct REST API (deprecated SDK removed)
    import httpx

    await _ws_send(websocket, "progress", "🔬 Calling Gemini 2.5 Flash with search...")

    gemini_url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-2.5-flash:generateContent?key={gemini_key}"
    )

    # Build payload — try with thinking mode first, fall back without it if rejected.
    def _build_gemini_payload(with_thinking: bool) -> dict:
        cfg: dict = {
            "maxOutputTokens": 32000,
        }
        if with_thinking:
            # temperature=1.0 required when thinkingBudget > 0
            cfg["temperature"] = 1.0
            cfg["thinkingConfig"] = {"thinkingBudget": 10000}
        else:
            cfg["temperature"] = 0.7
        return {
            "contents": [{"parts": [{"text": research_prompt}]}],
            "generationConfig": cfg,
            "tools": [{"google_search": {}}],
        }

    gemini_payload = _build_gemini_payload(with_thinking=True)

    # Retry logic: up to 3 attempts for 429 rate-limits.
    # On 400 with thinkingConfig, retry once without thinking mode.
    _max_attempts = 3
    _thinking_enabled = True
    response = None
    for _attempt in range(1, _max_attempts + 1):
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await asyncio.wait_for(
                    client.post(gemini_url, json=gemini_payload),
                    timeout=130.0,
                )
        except asyncio.TimeoutError:
            logger.warning("Gemini research request timed out (attempt %d/%d)", _attempt, _max_attempts)
            if _attempt < _max_attempts:
                await asyncio.sleep(5 * _attempt)
                continue
            raise RuntimeError("Gemini research API timed out after all retry attempts")

        if response.status_code == 429:
            _backoff = 10 * _attempt
            logger.warning("Gemini rate limit (429) on attempt %d — retrying in %ds", _attempt, _backoff)
            await _ws_send(websocket, "progress", f"⚠️ Gemini rate limit — retrying in {_backoff}s...")
            if _attempt < _max_attempts:
                await asyncio.sleep(_backoff)
                continue

        # 400 with thinking enabled → model may not support thinkingConfig; retry without it
        if response.status_code == 400 and _thinking_enabled:
            _err_body = response.text[:400]
            if "thinkingConfig" in _err_body or "thinking" in _err_body.lower() or "invalid" in _err_body.lower():
                logger.warning("Gemini rejected thinkingConfig — retrying without thinking mode")
                await _ws_send(websocket, "progress", "⚠️ Thinking mode unavailable — retrying with standard mode...")
                _thinking_enabled = False
                gemini_payload = _build_gemini_payload(with_thinking=False)
                continue  # retry immediately without thinking

        break  # non-retryable response

    if response is None or response.status_code != 200:
        status = response.status_code if response is not None else 0
        error_snippet = response.text[:300] if response is not None else "no response"
        logger.error("Gemini research API error %d: %s", status, error_snippet)
        if status in (400, 403):
            await _ws_send(websocket, "error", "❌ Google API key invalid. Check GOOGLE_API_KEY in .env.")
        elif status == 429:
            await _ws_send(websocket, "error", "⚠️ Gemini rate limit hit after retries. Try again in a minute.")
        raise RuntimeError(f"Gemini research API error: {status}")
    
    data = response.json()
    # Concatenate all text parts (grounding can return multiple)
    # DEFENSIVE: Gemini can return unexpected structures (safety filters,
    # grounding errors, string content instead of dict) — handle gracefully.
    candidates = data.get("candidates", [])
    if not candidates:
        finish_reason = data.get("promptFeedback", {}).get("blockReason", "unknown")
        raise RuntimeError(f"Gemini returned no candidates (blockReason={finish_reason})")
    
    candidate = candidates[0]
    # Check if the candidate was blocked by safety filters
    finish_reason = candidate.get("finishReason", "")
    if finish_reason == "SAFETY":
        logger.warning("Gemini response blocked by safety filter")
        raise RuntimeError("Gemini blocked response due to safety filter")
    
    content = candidate.get("content", {})
    if isinstance(content, str):
        # Gemini returned a string instead of structured response
        logger.warning("Gemini returned string content (len=%d), using as-is", len(content))
        text = content
    elif isinstance(content, dict):
        parts = content.get("parts", [])
        # When thinking mode is enabled, Gemini returns thought parts alongside text parts.
        # Thought parts have a "thought": true flag — skip them; only use output text.
        text = "\n".join(
            p.get("text", "") for p in parts
            if isinstance(p, dict) and p.get("text") and not p.get("thought")
        )
        if text:
            thought_count = sum(1 for p in parts if isinstance(p, dict) and p.get("thought"))
            if thought_count:
                logger.info("Gemini thinking: %d thought parts used internally (not included in blueprint)", thought_count)
    else:
        logger.error("Gemini returned unexpected content type: %s", type(content).__name__)
        text = ""

    if not text:
        raise RuntimeError("Gemini returned empty research text")

    await _ws_send(websocket, "progress", "✅ Research complete — building project blueprint...")

    # Send research summary to chat panel so user can see what was analyzed
    try:
        # Check for all known analyzed-sites section headers
        analyzed_section = ""
        for _header in ("===PRODUCTS_ANALYZED===", "===SITES_ANALYZED===", "===PLATFORMS_ANALYZED==="):
            if _header in text:
                _start = text.index(_header) + len(_header)
                # Find the next === boundary (skip it if it's immediately after)
                _rest = text[_start:]
                _end_match = _rest.find("===")
                _end = _start + _end_match if _end_match != -1 else _start + 500
                analyzed_section = text[_start:_end].strip()
                break

        # Also try to extract APP_CLASSIFICATION block (smart universal branch)
        _classification = ""
        if "===APP_CLASSIFICATION===" in text:
            _cs = text.index("===APP_CLASSIFICATION===") + len("===APP_CLASSIFICATION===")
            _ce_match = text[_cs:].find("===")
            _ce = _cs + _ce_match if _ce_match != -1 else _cs + 600
            _classification = text[_cs:_ce].strip()

        if analyzed_section:
            summary = f"🔍 **Research Complete**\n\n**Analyzed:**\n{analyzed_section[:600]}"
        elif _classification:
            summary = f"🔍 **Research Complete**\n\n**App classified:**\n{_classification[:600]}"
        else:
            summary = f"🔍 **Research Complete** — Analyzed top {layout_archetype.replace('_', ' ')} ({domain}) products and synthesized blueprint"

        await websocket.send_json({
            "type": "chat_message",
            "role": "agent",
            "content": summary,
        })
    except Exception:
        pass

    return text


# ╔══════════════════════════════════════════════════════════════╗
# ║  STEP 4 — verify_and_fix_build()                            ║
# ║  Run build, read errors, call Opus for surgical fixes        ║
# ╚══════════════════════════════════════════════════════════════╝

async def verify_and_fix_build(
    workspace_path: str,
    api_key: str,
    websocket=None,
) -> bool:
    """Run npm/pnpm build and auto-fix errors with Claude Opus.
    
    Returns True if build passes (with or without fixes).
    """
    pm = _detect_pm(workspace_path)

    for attempt in range(1, MAX_FIX_ATTEMPTS + 2):  # +1 for initial + N fixes
        await _ws_send(websocket, "progress", f"🔨 Build attempt {attempt}...")

        try:
            result = await asyncio.to_thread(
                subprocess.run,
                [pm, "run", "build"],
                cwd=workspace_path,
                capture_output=True,
                text=True,
                timeout=120,
                env={**os.environ, "CI": "true", "NODE_ENV": "production"},
            )
        except subprocess.TimeoutExpired:
            await _ws_send(websocket, "progress", "⚠️ Build timed out")
            return False

        if result.returncode == 0:
            await _ws_send(websocket, "progress", "✅ Build passed!")
            return True

        # Build failed — extract errors
        errors = (result.stderr or "") + "\n" + (result.stdout or "")
        # Trim to last 3000 chars (most relevant errors are at the end)
        errors = errors[-3000:] if len(errors) > 3000 else errors

        if attempt > MAX_FIX_ATTEMPTS:
            await _ws_send(websocket, "progress", f"⚠️ Build still failing after {MAX_FIX_ATTEMPTS} fix attempts")
            logger.error("Build failed after %d fix attempts. Errors:\n%s", MAX_FIX_ATTEMPTS, errors[:1000])
            return False

        # Ask Opus to fix the specific errors
        await _ws_send(websocket, "progress", f"🔧 Fixing build errors (attempt {attempt})...")

        file_tree = _build_file_tree(workspace_path)
        manifest = _read_manifest(workspace_path)

        fix_prompt = f"""Fix ALL build errors below. Return ONLY the files that need changes.

BUILD ERRORS:
{errors}

CURRENT FILE TREE:
{file_tree}

TEMPLATE MANIFEST (correct import paths):
{manifest[:10000]}

RULES:
- Return ONLY the files that need fixes — NOT the whole project
- If "Module not found" → fix the import path using TEMPLATE_MANIFEST paths
- If "is not defined" → add the missing import statement
- If "'use client'" missing → add it as first line
- If duplicate export → fix the export
- Keep all existing functionality — only fix what's broken
- Use the EXACT import paths from TEMPLATE_MANIFEST.md

Return JSON: {{"files": [{{"path": "...", "content": "..."}}]}}
"""

        fix_result = await call_claude_for_json(
            system_prompt="You are an expert build error fixer. Fix ONLY the broken files. Return minimal JSON.",
            user_prompt=fix_prompt,
            api_key=api_key,
            websocket=websocket,
            max_tokens=32000,
            model=DEFAULT_MODEL,
        )

        if fix_result:
            written = write_files_from_json(fix_result, workspace_path)
            await _ws_send(websocket, "progress", f"🔧 Fixed {len(written)} files, rebuilding...")
        else:
            await _ws_send(websocket, "progress", "⚠️ Could not generate fix")
            return False

    return False


# ╔══════════════════════════════════════════════════════════════╗
# ║  STEP 7 — TEMPLATE-SPECIFIC RULES                          ║
# ╚══════════════════════════════════════════════════════════════╝

NEXTJS_WEBSITE_RULES = """
## TEMPLATE: Next.js 14 Website (App Router + Tailwind + shadcn/ui)

### What ALREADY EXISTS (DO NOT create these):
- Layout: src/app/layout.js (root), src/app/(marketing)/layout.js
- Navigation: src/components/layout/MarketingHeader.jsx, MarketingFooter.jsx
- Auth pages: src/app/(auth)/login/page.js, register/page.js
- Providers: src/components/Providers.jsx (QueryClient + TooltipProvider)
- UI Components: 25+ shadcn/ui components in src/components/ui/
- Config: src/config/site.js, navigation.js, icons.js
- API client: src/lib/api-client.js
- Hooks: useDebounce, useLocalStorage, usePagination

### What YOU generate:
- src/app/globals.css — UPDATE the :root CSS variables for the project theme
- src/config/site.js — REWRITE with the project name, description, URL
- src/config/navigation.js — REWRITE with the project's nav items
- src/app/(marketing)/page.js — REWRITE with section component imports
- src/components/sections/*.jsx — CREATE all section components (Hero, Features, Pricing, etc.)
- Additional pages (src/app/(marketing)/about/page.js, pricing/page.js, contact/page.js) — CREATE
- src/app/(marketing)/blog/page.js — CREATE if blog is in the spec

### File naming (sections):
- src/components/sections/HeroSection.jsx
- src/components/sections/FeaturesSection.jsx
- src/components/sections/PricingSection.jsx
- src/components/sections/TestimonialsSection.jsx
- src/components/sections/FAQSection.jsx
- src/components/sections/CTASection.jsx
- src/components/sections/HowItWorksSection.jsx
- etc.

### Import rules:
- Icons: import { IconName } from 'lucide-react'
- UI: import { Button } from '@/components/ui/Button'  (uppercase custom)
      import { Accordion, AccordionItem } from '@/components/ui/accordion'  (lowercase shadcn)
- Config: import { siteConfig } from '@/config/site'
- Hooks: import { useDebounce } from '@/hooks'
- Next.js: 'use client' at top of any component using hooks/events
- CRITICAL: NEVER add 'use client' to config/data files (navigation.js, site.js, icons.js,
  constants.js). These export plain static data and are consumed by server components.
  Adding 'use client' to them breaks any server component that imports and iterates them.
- Images: use <img> with placeholder URLs, NOT next/image
- Avatars: https://i.pravatar.cc/150?u=person{N}
- Animations: use framer-motion (import { motion } from 'framer-motion')

### Design rules:
- Use Tailwind CSS classes (bg-primary, text-foreground, bg-muted, etc.)
- NEVER hardcode hex/rgb colors — always use CSS variables via Tailwind
- Every section must be responsive (mobile-first, sm: md: lg: xl: breakpoints)
- Add hover effects, transitions, and micro-animations on interactive elements
- Use gradient backgrounds for hero sections (bg-gradient-to-br, etc.)
- Cards must have shadows, rounded corners, and hover elevation
"""

REACT_ADMIN_RULES = """
## TEMPLATE: React + Vite Admin Panel (Tailwind + shadcn/ui)

### What ALREADY EXISTS (DO NOT create these):
- Layout: src/components/layout/MainLayout.jsx, Sidebar.jsx, Header.jsx, AuthLayout.jsx
- Router: src/router/index.jsx, routes.jsx, PrivateRoute.jsx
- Auth: src/pages/auth/LoginPage.jsx, store/auth.store.js, services/auth.service.js
- UI Components: 25+ shadcn/ui components in src/components/ui/
- DataTable wrapper: src/components/ui/data-table.jsx (uses @tanstack/react-table)
- Example CRUD: src/features/users/ (full service + hooks + pages)
- API client: src/api/client.js
- Stores: auth.store.js, ui.store.js (Zustand)
- Config: src/config/navigation.js, icons.js
- i18n: src/i18n/ (en.json, ru.json)

### What YOU generate:
- src/styles/global.css — UPDATE :root CSS variables for brand colors
- src/config/navigation.js — REWRITE with project-specific nav items + Lucide icons
- src/pages/dashboard/DashboardPage.jsx — REWRITE with domain KPI cards + Recharts charts
- src/features/<entity>/ — CREATE new CRUD features for EACH entity from research:
  - services/<entity>.service.js — API client calls (getAll, getById, create, update, delete)
  - hooks/use<Entity>.js — React Query hooks (useQuery, useMutation)
  - pages/<Entity>ListPage.jsx — DataTable with columns, actions, filters, search
  - pages/<Entity>FormPage.jsx — Create/Edit form with validation (react-hook-form + zod)
- src/router/routes.jsx — UPDATE to add new feature routes

### Feature structure pattern (FOLLOW THIS EXACTLY):
src/features/<entity>/
├── services/<entity>.service.js    # API calls
├── hooks/use<Entity>.js            # React Query wrappers
├── pages/<Entity>ListPage.jsx      # DataTable + actions
└── pages/<Entity>FormPage.jsx      # Form (create + edit)

### Import rules:
- Icons: import { IconName } from 'lucide-react'
- UI: import { Button } from '@/components/ui/Button'
      import { DataTable } from '@/components/ui/data-table'
      import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'
- Hooks: import { useDebounce } from '@/hooks'
- Store: import { useAuthStore } from '@/store/auth.store'
- Router: import { useNavigate, useParams } from 'react-router-dom'
- Charts: import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, AreaChart, Area, PieChart, Pie, Cell } from 'recharts'
- Animations: import { motion, AnimatePresence } from 'framer-motion'
- Forms: import { useForm } from 'react-hook-form'
         import { zodResolver } from '@hookform/resolvers/zod'
         import { z } from 'zod'

### Dashboard pattern:
- Row 1: 4 KPI stat cards (value, label, change %, icon, sparkline)
- Row 2: 2 charts (area chart + bar chart or pie chart)
- Row 3: Recent activity table (5-10 rows)
- ALL data must be realistic mock data (not lorem ipsum)
"""

VUE_ADMIN_RULES = """
## TEMPLATE: Vue 3 + Vite Admin Panel (Tailwind + Shadcn Vue)

### What ALREADY EXISTS (DO NOT create these):
- Layout: src/components/layout/AppSidebar.vue, AppHeader.vue, MainLayout.vue, AuthLayout.vue
- Router: src/router/index.js, routes.js, guards.js
- Auth: src/pages/auth/LoginPage.vue, stores/auth.store.js, services/auth.service.js
- UI Components: 30+ Shadcn Vue components in src/components/ui/
- Example CRUD: src/features/users/ (full service + composables + pages)
- API client: src/lib/api-client.js
- Stores: auth.store.js, ui.store.js (Pinia)

### What YOU generate:
- src/assets/styles/global.css — UPDATE :root CSS variables
- src/config/navigation.js — REWRITE with project nav items
- src/pages/dashboard/DashboardPage.vue — REWRITE with domain KPIs and vue-chartjs charts
- src/features/<entity>/ — CREATE new CRUD features:
  - services/<entity>.service.js
  - composables/use<Entity>.js (TanStack Vue Query)
  - pages/<Entity>ListPage.vue
  - pages/<Entity>FormPage.vue
- src/router/routes.js — UPDATE to add new feature routes

### Vue conventions:
- Use <script setup> (Composition API, NOT Options API)
- Use defineProps/defineEmits, NOT this.$props
- Templates use kebab-case for custom components
- Import Shadcn Vue: import { Dialog, DialogTrigger } from '@/components/ui/dialog'
- Charts: import { Bar, Doughnut, Line } from 'vue-chartjs'
          import { Chart as ChartJS, ... } from 'chart.js'
"""


# ╔══════════════════════════════════════════════════════════════╗
# ║  STEP 8 — SYSTEM PROMPT                                    ║
# ╚══════════════════════════════════════════════════════════════╝

SYSTEM_PROMPT = """You are a world-class Senior Frontend Engineer and UI/UX expert building production SaaS products.
Your output must match the visual quality of Linear, Vercel, Stripe, and Notion — not boilerplates.

You work ON TOP of an existing template. The template already provides:
- UI components (shadcn/ui), layouts, routing, auth, API client, state management
- You must USE these existing components, NOT recreate them
- Check the TEMPLATE MANIFEST and FILE TREE to know what exists

OUTPUT: Call the write_project_files tool with ALL files to create or modify.

====================================
MANDATORY PRE-GENERATION THINKING
====================================
Before writing ANY file, silently complete this analysis:

STEP 1 — Domain Intelligence:
  Read the DESIGN SYSTEM FROM RESEARCH section carefully.
  Understand: industry, users, primary data, key actions, must-have features.

STEP 2 — Layout Decision:
  Based on research recommendations:
  - admin_panel → sidebar-left (dark or colored)
  - landing_page → single page with top nav
  - dashboard → top-nav or sidebar with charts
  - saas_app → sidebar-left with workspace
  - crm → sidebar-left with pipeline views

STEP 3 — Visual Identity:
  Use the EXACT color palette from the research.
  Ask: "Would a real company pay $50/month for this?"
  The palette must feel PURPOSE-BUILT for this specific domain.

STEP 4 — Import Safety (non-negotiable):
  Every import you write must point to a file that EXISTS in the template OR in your output.
  Check the file tree and manifest. Missing imports = build failure.

====================================
NO AUTHENTICATION
====================================
The template ALREADY handles authentication.
DO NOT generate: login pages, auth guards, auth stores, logout buttons.
The app starts directly on the main page.

====================================
CSS THEME
====================================
The theme CSS file MUST define ALL these variables with research-provided HSL values:

:root and .dark — FULL variable set:
  --background, --foreground, --card, --card-foreground
  --popover, --popover-foreground, --primary, --primary-foreground
  --secondary, --secondary-foreground, --muted, --muted-foreground
  --accent, --accent-foreground, --destructive, --destructive-foreground
  --border, --input, --ring, --radius
  --sidebar-background, --sidebar-foreground, --sidebar-primary
  --sidebar-primary-foreground, --sidebar-accent, --sidebar-accent-foreground
  --sidebar-border, --sidebar-ring
  --chart-1 through --chart-5

RADIUS by domain feel:
  Enterprise/data-heavy: 0.25rem
  Modern SaaS: 0.5rem
  Friendly/approachable: 0.75rem

Import Google Fonts via @import url() at top of CSS file.

====================================
COLOR RULES
====================================
60/30/10 distribution:
  60% → bg-background, bg-card (main workspace)
  30% → bg-sidebar, bg-muted (structural)
  10% → bg-primary (actions, accents only)

CONTRAST (never violate):
  Dark bg → light text
  Light bg → dark text
  NEVER same lightness for text and background

NEVER hardcode hex/rgb colors in components.
Always use Tailwind classes: bg-primary, text-foreground, bg-muted, etc.

====================================
UI QUALITY STANDARDS
====================================
STATS CARDS (dashboard KPIs):
  - Large number: text-2xl font-bold minimum
  - Trend indicator: +X% green or -X% red
  - Small icon with bg-primary/10 top-right
  - Subtle border + shadow-sm

DATA TABLES:
  - Search bar above table always
  - Status cells → Badge with semantic colors
  - Actions: dropdown or icon buttons
  - Pagination: "X of Y results" + Prev/Next
  - Wrap in Card with header (title + action button)
  - Row hover: hover:bg-muted/50

FILTER ROW:
  - Search input left (40-50% width)
  - Filter dropdowns next
  - Primary action (+ Create) right-aligned

PAGE HEADER:
  - Title: text-2xl font-bold
  - Subtitle: text-sm text-muted-foreground
  - Actions: top-right aligned

BADGE/STATUS (semantic colors — from CSS variables, not hex):
  Active/Success → green tones
  Pending/Warning → amber tones
  Error/Failed → destructive
  Info/Processing → blue tones
  Neutral → secondary

FORMS:
  - Required: asterisk on label
  - Validation messages below fields
  - Submit: disabled + spinner during mutation
  - Cancel always available

====================================
LOADING / EMPTY / ERROR (MANDATORY)
====================================
EVERY component fetching data must handle all THREE states:

LOADING: Skeleton matching content shape (animate-pulse bg-muted rounded)
EMPTY: Centered icon + "No {entity} found" + action button
ERROR: Alert icon + "Something went wrong" + retry button

====================================
ANIMATIONS (safe patterns)
====================================
Page transition: initial={{opacity:0,y:8}} animate={{opacity:1,y:0}} transition={{duration:0.15}}
List stagger: staggerChildren:0.04, child y:20→0
Card hover: whileHover={{y:-2}} transition={{duration:0.1}}
Scroll reveal: whileInView + viewport={{once:true}}

NEVER: layoutId on table rows | animate during loading

====================================
'use client' RULES (Next.js App Router)
====================================
Add 'use client' ONLY to component files that use hooks or browser events.

NEVER add 'use client' to:
  - src/config/navigation.js — exports static nav arrays consumed by server components
  - src/config/site.js — exports plain site metadata
  - src/config/icons.js, constants.js, tokens.js, theme.js — all pure data
  Adding 'use client' to these files turns them into client module exports.
  Server components (e.g. MarketingFooter, MarketingHeader) that call .map()
  on those exports will throw: "Functions cannot be passed directly to Client Components"

====================================
JSX + TYPESCRIPT SAFETY
====================================
- Never render objects/arrays directly in JSX
- Always: {item.name}, {item.id ?? '—'}, {String(item.status)}
- Relations: {item.client?.name} never {item.client}
- Avoid 'any' — use proper interfaces for API response shapes

====================================
OVERLAYS (non-negotiable)
====================================
All overlays MUST be opaque:
  Floating: z-50 bg-popover text-popover-foreground border shadow-md
  Modal backdrop: bg-black/50 backdrop-blur-sm
  NEVER transparent/semi-transparent popover backgrounds

====================================
POLISHING (non-negotiable)
====================================
- SPACING: gap-6 or gap-8 for main sections. Never gap-2 for main layout.
- CARDS: Every data section in a Card with shadow-sm minimum.
- EMPTY STATES: Icon + message + action button. Never empty white space.
- STATS: Every dashboard has KPI row with trend indicators.
- CHARTS: Use recharts / vue-chartjs with 12+ data points, CSS variable colors.
- TABLES: Always in Card wrapper with title + action button header.
- HOVER: Every interactive element has hover state.
- TRANSITIONS: transition-colors duration-150 on all hover/focus.
- MOCK DATA: Realistic names, numbers, dates, statuses. Never lorem ipsum.

====================================
DESIGN SYSTEM (critical for consistency)
====================================
If the project has src/lib/design-system.js, ALL components MUST:
  import { ds } from '@/lib/design-system'

Then use:
  <Card className={ds.card}>        instead of ad-hoc card classes
  <Badge className={ds.badge[status]}>  instead of inline badge styles
  <motion.div {...ds.pageAnimation}>    for consistent page transitions
  ds.stagger for list animations
  ds.maxWidth, ds.sectionSpacing for layout

This ensures EVERY card, badge, and animation looks identical across the entire project.

====================================
SECURITY (non-negotiable)
====================================
API KEYS & SECRETS:
- NEVER return raw secret keys in API responses or display them in full
- When an API key is already saved, show it masked: "sk_test_••••••••••••" + last 4 chars
- Input fields for secrets: type="password" by default, with a show/hide toggle
- On load: if a key exists, populate the input with a masked placeholder (e.g. "••••••••••••") and only send the real value on save if the user typed a new one
- Backend routes that return settings must redact sensitive fields:
    return { ...settings, api_key: settings.api_key ? `••••${settings.api_key.slice(-4)}` : '' }
- NEVER log, expose in URLs, or include secrets in client-side state beyond what is needed for the current save action

====================================
API-READY SERVICES (non-negotiable for admin/CRUD apps)
====================================
Services must make REAL HTTP fetch() calls:
  const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:3001';

Pattern:
  getAll: (params) => fetch(`${API_URL}/entity?${new URLSearchParams(params)}`).then(r => r.json())
  getById: (id) => fetch(`${API_URL}/entity/${id}`).then(r => r.json())
  create: (data) => fetch(`${API_URL}/entity`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)}).then(r => r.json())
  update: (id, data) => fetch(`${API_URL}/entity/${id}`, {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)}).then(r => r.json())
  delete: (id) => fetch(`${API_URL}/entity/${id}`, {method:'DELETE'})

Services should CATCH errors and return empty arrays/objects (graceful degradation).
NEVER hardcode mock data arrays inside service files.
Mock data lives in db.json at the project root (served by json-server).

====================================
STITCH AI — UI POLISH LAYER
====================================
A Stitch AI design reference may be provided in the user message under
"UI POLISH LAYER — STITCH AI DESIGN REFERENCE". If present:

WHAT TO DO:
  - Study the spatial patterns: grid columns, gap sizes, padding ratios, card borders
  - Study typography: heading sizes, font-weight, letter-spacing hierarchy
  - Study component style: shadow depth, border-radius, button shape, icon sizing
  - ADAPT these patterns into React/Vue JSX with Tailwind classes

RULES (critical):
  - Do NOT copy HTML verbatim — convert every element to JSX
  - Replace all inline styles with Tailwind classes (bg-primary, text-foreground, etc.)
  - Use existing shadcn/ui components where applicable
  - The Stitch reference is UI-ONLY — do not change page structure, routes, or entities
  - If no Stitch reference is in the message, apply your own professional judgment
"""


# ╔══════════════════════════════════════════════════════════════╗
# ║  STEP 10 — generate_new_project() — 3-Phase Orchestrator   ║
# ╚══════════════════════════════════════════════════════════════╝

async def generate_new_project(
    description: str,
    workspace_path: str,
    validated: dict,
    websocket,
    chat_session_id: str = "",
    user_jwt: str = "",
) -> bool:
    """3-phase multi-call orchestrator for new project generation.

    Uses Claude Opus 4.6 (120K+ output tokens) across 3 sequential calls:
      Call 1: Foundation (theme, config, nav, layouts, main page, router)
      Call 2: Content (all sections OR all CRUD features — NO LIMIT)
      Call 3: Extra pages + completeness check

    Between each call, the file tree is rebuilt so imports resolve correctly.
    The template is ALREADY cloned into workspace_path by Phase 2.
    """
    try:
        return await _generate_new_project_inner(
            description, workspace_path, validated, websocket,
            chat_session_id, user_jwt,
        )
    except Exception as exc:
        logger.error("generate_new_project crashed: %s", exc, exc_info=True)
        await _ws_send(websocket, "error", f"❌ Generation failed: {str(exc)[:200]}")
        return False


async def _generate_new_project_inner(
    description: str,
    workspace_path: str,
    validated: dict,
    websocket,
    chat_session_id: str = "",
    user_jwt: str = "",
) -> bool:
    """Inner implementation of generate_new_project (wrapped in try/except above)."""
    api_key = validated["anthropic_api_key"]
    gemini_key = validated["gemini_api_key"]

    # generate_new_project is Claude-only (all 3 phases call Anthropic directly).
    # If the user's stored key is a Gemini/Google key (not starting with "sk-ant-"),
    # fall back to the server's ANTHROPIC_API_KEY so schema building and code generation work.
    if not (api_key and api_key.startswith("sk-ant-")):
        _server_anthropic = os.environ.get("ANTHROPIC_API_KEY", "")
        if _server_anthropic:
            logger.info(
                "User key is not an Anthropic key; falling back to server ANTHROPIC_API_KEY for generation"
            )
            api_key = _server_anthropic

    stack = validated.get("project_stack", "") or validated.get("skeleton_stack", "")
    
    MODEL = DEFAULT_MODEL
    MAX_TOKENS = MAX_TOKENS_PER_CALL

    # ── Step 1: Classify app type ──
    from knowledge.loader import classify_project_type_ai
    _classification = await classify_project_type_ai(description, gemini_key)
    app_type = _classification["app_type"]  # legacy compat string
    _layout_archetype = _classification["layout_archetype"]
    _domain = _classification["domain"]

    await _ws_send(websocket, "progress", f"📋 {_layout_archetype.replace('_', ' ').title()} — {_domain} domain")
    
    # ── Step 2: Read template context ──
    manifest = _read_manifest(workspace_path)
    file_tree = _build_file_tree(workspace_path)
    template_context = _read_key_template_files(workspace_path, stack)
    
    # Detect stack-specific rules
    if "next" in stack or "nextjs" in stack:
        stack_rules = NEXTJS_WEBSITE_RULES
    elif "vue" in stack:
        stack_rules = VUE_ADMIN_RULES
    else:
        stack_rules = REACT_ADMIN_RULES
    
    # ── Step 2b: Load Layer 2 Skills ──
    skills = _load_skills(app_type, stack, layout_archetype=_layout_archetype)
    await _ws_send(websocket, "progress", "📚 Loading component skills...")
    
    # ── Step 3: Gemini ULTRA-DEEP research ──
    await _ws_send(websocket, "progress", "🔬 Researching real products in this domain...")
    research_quality = "full"  # Track research quality for gating
    try:
        research = await gemini_deep_research(
            description, _classification, stack, gemini_key, websocket,
        )
        # Research quality gate: check if we got meaningful research
        if len(research) < 200:
            research_quality = "minimal"
            logger.warning("Research returned minimal content (%d chars)", len(research))
            await _ws_send(websocket, "warning", "⚠️ Research returned limited results — generation will use basic patterns")
    except Exception as exc:
        logger.error("Gemini research failed: %s", exc)
        research_quality = "failed"
        await _ws_send(websocket, "warning", "⚠️ Research failed — proceeding with basic generation...")
        research = f"Project: {description}\nApp type: {app_type}\nStack: {stack}"

    # Extract domain-specific component blueprint from Gemini research.
    # This covers every domain automatically — known types get a richer spec,
    # unknown/unusual types get domain guidance they wouldn't have otherwise.
    _research_key_components = _extract_research_section(research, "===KEY_COMPONENTS===")
    _research_pages = _extract_research_section(research, "===PAGES===", max_chars=4000)
    _research_ui_patterns = _extract_research_section(research, "===UI_PATTERNS===")
    _research_copy_tone = _extract_research_section(research, "===COPY_TONE===")
    _research_domain_must_haves = _extract_research_section(research, "===DOMAIN_MUST_HAVES===")
    _research_design_system_name = _extract_research_section(research, "===DESIGN_SYSTEM_NAME===", max_chars=100).strip().strip('"').strip("'")

    # Re-derive layout archetype from Gemini's confirmed classification in research
    _classification = _extract_layout_archetype(research, _classification)
    _layout_archetype = _classification["layout_archetype"]
    app_type = _classification["app_type"]
    _domain = _classification["domain"]

    # ── Step 3b: Build structured project schema ──
    # This is the SINGLE SOURCE OF TRUTH for all 3 generation phases.
    # It eliminates consistency bugs (entities ↔ nav ↔ routes ↔ forms).
    from app.services.project_schema import (
        build_project_schema,
        schema_to_entity_spec,
        schema_to_navigation_spec,
        schema_to_dashboard_spec,
        schema_to_theme_spec,
        schema_to_design_system_spec,
        schema_to_api_spec,
        schema_to_mock_db_json,
        schema_to_sections_spec,
    )
    
    project_schema = await build_project_schema(
        research=research,
        description=description,
        stack=stack,
        app_type=app_type,
        api_key=api_key,
        websocket=websocket,
    )
    
    # Build schema-derived prompt sections (used in all 3 phases)
    schema_entity_spec = schema_to_entity_spec(project_schema)
    schema_nav_spec = schema_to_navigation_spec(project_schema)
    schema_dashboard_spec = schema_to_dashboard_spec(project_schema)
    schema_theme_spec = schema_to_theme_spec(project_schema)
    schema_design_spec = schema_to_design_system_spec(project_schema)
    schema_api_spec = schema_to_api_spec(project_schema)
    schema_sections_spec = schema_to_sections_spec(project_schema)
    
    # ── Step 3c: Write db.json (API mock data) ──
    # This makes the generated app API-ready from day one.
    # json-server serves this as a real REST API at localhost:3001.
    mock_db_json = schema_to_mock_db_json(project_schema)
    if mock_db_json:
        db_json_path = os.path.join(workspace_path, "db.json")
        try:
            with open(db_json_path, "w", encoding="utf-8") as f:
                f.write(mock_db_json)
            await _ws_send(websocket, "progress", "📦 Generated db.json (mock API data)")
            logger.info("Wrote db.json (%d bytes)", len(mock_db_json))
        except Exception as e:
            logger.warning("Failed to write db.json: %s", e)
    
    total_files = []

    # ════════════════════════════════════════════════════════════
    #  STEP A — Emit rich plan to chat (before any design work)
    #  User sees the full structural plan (sections/pages/entities)
    #  THEN Phase 4 (Stitch) runs, THEN coding starts.
    # ════════════════════════════════════════════════════════════
    _plan_design_system_name = "Clean Slate"  # fallback, overwritten below
    try:
        _entities = project_schema.get("entities", [])
        _pages    = project_schema.get("pages", [])
        _sections = project_schema.get("sections", [])
        _brand    = project_schema.get("brand", {})
        _nav      = project_schema.get("navigation", [])
        _theme    = project_schema.get("theme", {})
        _ds       = project_schema.get("design_system", {})

        # Strip context enrichment (everything after the "---" separator) so the
        # project name fallback uses only the original user description, not the
        # "## Previous conversation context\n\n..." block appended by ws.py.
        _clean_desc = description.split("\n\n---\n\n")[0].strip()
        _project_name = _brand.get("name") or (_clean_desc[:40] if _clean_desc else "your app")
        _brand_domain = _brand.get("domain", _domain)

        _h_font      = _theme.get("heading_font", "")
        _b_font      = _theme.get("body_font", "")
        _primary_hsl = _theme.get("primary", "")
        _bg_hsl      = _theme.get("background", "")
        _fg_hsl      = _theme.get("foreground", "")
        _accent_hsl  = _theme.get("accent", "")

        _domain_lower = _brand_domain.lower()
        # Strip enrichment header so the plain user description drives keyword matching
        _raw_desc = description.split("\n\n---\n\n")[0].strip()

        # Design system name priority chain:
        # 1. Schema-generated name (from Gemini → schema builder)
        # 2. Research-extracted name (from ===DESIGN_SYSTEM_NAME=== block)
        # 3. Hardcoded keyword dictionary (last resort cosmetic fallback)
        _schema_ds_name = _ds.get("name", "").strip()
        if _schema_ds_name:
            _plan_design_system_name = _schema_ds_name
        elif _research_design_system_name:
            _plan_design_system_name = _research_design_system_name
        else:
            _plan_design_system_name = _generate_design_system_name(
                _domain_lower, _h_font, _primary_hsl, _raw_desc
            )

        # ── Section/page items ────────────────────────────────
        # Priority 1: schema sections (landing pages)
        # Priority 2: schema pages (admin panels)
        # Priority 3: parse keywords from the user's description
        _page_items = []

        if _sections:
            for s in _sections[:10]:
                stype   = s.get("type", "custom")
                headline = s.get("headline", "")
                subdesc  = s.get("subheadline", "") or s.get("description", "")
                name     = stype.replace("_", " ").title()
                desc     = headline or subdesc or name
                _page_items.append({"name": name, "desc": desc[:80]})

        elif _pages:
            for p in _pages[:10]:
                name  = p.get("title") or p.get("name", "")
                ptype = p.get("type", "")
                desc  = p.get("description", "") or ptype.replace("_", " ")
                if name:
                    _page_items.append({"name": name, "desc": desc[:80]})

        # Fallback: parse keywords from the description so the plan is never empty
        if not _page_items:
            _desc_lower = description.lower()
            _SECTION_MAP = [
                # Blog/content site keyword fallback
                ("article",     "Article Listing",   "Browse and filter all articles"),
                ("post",        "Posts Feed",        "Latest posts with category filters"),
                ("rich text",   "Article Editor",    "Rich text editor for creating articles"),
                ("comment",     "Comment System",    "Reader comments on articles"),
                ("author",      "Author Profiles",   "Writer bios and their published work"),
                ("categor",     "Categories",        "Browse articles by category"),
                ("tag",         "Tags",              "Filter articles by topic tags"),
                # Generic landing page section keywords
                ("hero",        "Hero Section",      "Main headline, subtext, and primary CTA"),
                ("feature",     "Features Grid",     "Product capabilities showcase"),
                ("pricing",     "Pricing Table",     "Subscription plans and tiers"),
                ("testimonial", "Testimonials",      "Customer quotes and social proof"),
                ("faq",         "FAQ Accordion",     "Frequently asked questions"),
                ("how it works","How It Works",      "Step-by-step product walkthrough"),
                ("stat",        "Stats Section",     "Key metrics and numbers"),
                ("integrat",    "Integrations",      "Third-party tool connections"),
                ("comparison",  "Comparison Table",  "Side-by-side feature comparison"),
                ("cta",         "Call to Action",    "Conversion-focused signup section"),
                ("footer",      "Footer",            "Site links, legal, and social icons"),
                ("contact",     "Contact",           "Contact form and info"),
                ("blog",        "Blog",              "Articles and updates listing"),
                ("team",        "Team",              "Team members and bios"),
                ("about",       "About",             "Company story and mission"),
                ("dashboard",   "Dashboard",         "Overview with KPI cards and charts"),
                ("table",       "Data Table",        "Sortable, filterable data listing"),
                ("form",        "Form",              "Create / edit record form"),
            ]
            for kw, name, desc in _SECTION_MAP:
                if kw in _desc_lower and len(_page_items) < 10:
                    _page_items.append({"name": name, "desc": desc})

        # ── Entity list (admin panels) ─────────────────────────
        _entity_list = [
            {
                "name": e.get("name", ""),
                "fields": ", ".join(
                    f.get("name", "") for f in e.get("fields", [])[:5]
                ),
            }
            for e in _entities[:6] if e.get("name")
        ]

        # ── Project description (replaces Components in plan) ─────
        # For landing pages : 1-2 focused sentences (goal + sections)
        # For admin panels  : 2-3 richer sentences (entities + features + tech)
        # For blog/content  : content-platform focused description
        # Use the confirmed layout archetype for plan description routing
        _is_blog     = _layout_archetype == "blog"
        _is_consumer = _layout_archetype in {"consumer_website", "marketplace", "portfolio"}
        _is_admin    = _layout_archetype in {"admin_dashboard", "crm", "tms", "saas_dashboard", "ecommerce"} or (bool(_entities) and not _is_blog and not _is_consumer)

        if _is_blog and _entities:
            # ── Blog / Content platform description ───────────────
            _vibe = _ds.get("overall_vibe", "") or "clean typographic"
            _page_count = len(_pages)
            _page_names = [p.get("title", p.get("path", "")) for p in _pages[:6] if p.get("title") or p.get("path")]
            _page_str = ", ".join(_page_names[:5])
            _entity_names = [e.get("name", "") for e in _entities[:4] if e.get("name")]
            _tagline = _brand.get("tagline", "")

            _about = f"A {_vibe} **{_project_name}**"
            if _tagline:
                _about += f" — {_tagline}"
            _about += f". A full-featured content platform with {_page_count} pages"
            if _page_str:
                _about += f": {_page_str}"
            _about += "."
            if _entity_names:
                _about += (
                    f" Powered by {len(_entity_names)} data models "
                    f"({', '.join(_entity_names)}) served via a json-server REST API."
                )

        elif _is_admin:
            # ── Admin / CRUD description ───────────────────────
            _entity_names = [e.get("name", "") for e in _entities[:6] if e.get("name")]
            _entity_count = len(_entity_names)
            _entity_str   = ", ".join(_entity_names[:4])
            if _entity_count > 4:
                _entity_str += f", and {_entity_count - 4} more"

            # Sentence 1 — what it manages
            _about_s1 = (
                f"A full-stack {_layout_archetype.replace('_', ' ')} ({_domain}) managing "
                f"{_entity_count} resource{'s' if _entity_count != 1 else ''}"
                + (f": {_entity_str}" if _entity_str else "")
                + "."
            )

            # Sentence 2 — key features derived from schema
            _features = []
            _page_types = [p.get("type", "") for p in _pages]
            if any("dashboard" in t for t in _page_types):
                _features.append("KPI dashboard with live Recharts analytics")
            if _entity_count > 0:
                _features.append("DataTable views with search, filters, and row actions")
            if any("form" in t for t in _page_types):
                _features.append("validated create / edit forms")
            if any("settings" in t for t in _page_types):
                _features.append("settings & profile management")
            _vibe = _ds.get("overall_vibe", "")
            if _vibe:
                _features.append(f"{_vibe} design aesthetic")
            _about_s2 = ("Features: " + ", ".join(_features[:4]) + ".") if _features else ""

            # Sentence 3 — tech stack
            _about_s3 = (
                f"Built with {stack}, shadcn/ui components, "
                f"React Query for data fetching, and a json-server REST API."
            )

            _about = " ".join(p for p in [_about_s1, _about_s2, _about_s3] if p)

        elif _is_consumer:
            # ── Consumer website description ──────────────────
            _vibe = _ds.get("overall_vibe", "") or "modern"
            _page_count = len(_pages)
            _page_names = [p.get("title", p.get("path", "")) for p in _pages[:6] if p.get("title") or p.get("path")]
            _page_str = ", ".join(_page_names[:5])
            _entity_names = [e.get("name", "") for e in _entities[:4] if e.get("name")]
            _tagline = _brand.get("tagline", "")

            _about = f"A {_vibe} **{_project_name}** consumer website"
            if _tagline:
                _about += f" — {_tagline}"
            _about += "."
            if _page_str:
                _about += f" Features {_page_count} public-facing pages: {_page_str}."
            if _entity_names:
                _about += (
                    f" Backed by {len(_entity_names)} domain models "
                    f"({', '.join(_entity_names)}) with a json-server REST API."
                )

        else:
            # ── Landing page description ───────────────────────
            _tagline  = _brand.get("tagline", "")
            _vibe     = _ds.get("overall_vibe", "") or "modern"
            _sec_names = [item["name"] for item in _page_items]
            _sec_count = len(_sec_names)
            _sec_str   = ", ".join(_sec_names[:5])
            if _sec_count > 5:
                _sec_str += f", and {_sec_count - 5} more"

            _about = f"A {_vibe} landing page for **{_project_name}**"
            if _tagline:
                _about += f" — {_tagline}"
            _about += "."
            if _sec_str:
                _about += (
                    f" Includes {_sec_count} section{'s' if _sec_count != 1 else ''}"
                    f": {_sec_str}."
                )
            # Derive the closing style sentence from the actual schema design tokens
            # so each generation reflects its unique palette rather than a generic line.
            _primary_hint = ""
            if _primary_hsl:
                import re as _re_hsl
                _hm = _re_hsl.search(r"(\d+(?:\.\d+)?)\s*(?:deg|°)?", _primary_hsl)
                if _hm:
                    _h = float(_hm.group(1))
                    if _h < 30 or _h >= 330:
                        _primary_hint = "bold crimson"
                    elif _h < 60:
                        _primary_hint = "warm amber"
                    elif _h < 90:
                        _primary_hint = "earthy green"
                    elif _h < 150:
                        _primary_hint = "fresh teal"
                    elif _h < 210:
                        _primary_hint = "cool cyan"
                    elif _h < 270:
                        _primary_hint = "deep indigo"
                    else:
                        _primary_hint = "rich violet"
            _style_closers = [
                f"{_primary_hint + ' ' if _primary_hint else ''}{_vibe} palette with smooth scroll animations and high-impact CTAs.",
                f"Tailwind-powered {_primary_hint or _vibe} design with accessible contrast and fluid layout transitions.",
                f"Purpose-built {_vibe} aesthetic — {_primary_hint or 'curated'} color tokens, clean typography, conversion-optimised flow.",
                f"Fully responsive {_primary_hint or _vibe} design with framer-motion micro-interactions and focused conversion paths.",
            ]
            import random as _rand_about
            _about += " " + _rand_about.choice(_style_closers)

        # ── Design description line ────────────────────────────
        font_str = " + ".join(f for f in [_h_font, _b_font] if f) or "Inter + sans-serif"

        # Collect real color tokens for display
        _color_tokens = []
        for label, val in [
            ("primary", _primary_hsl),
            ("accent", _accent_hsl),
            ("bg", _bg_hsl),
        ]:
            if val:
                _color_tokens.append(f"{label}: {val}")

        _radius     = _theme.get("radius", "")
        _vibe       = _ds.get("overall_vibe", "") or _brand_domain.replace("_", " ").title()
        _card_cls   = _ds.get("card_classes", "")

        design_parts = [f"{font_str} fonts"]
        if _color_tokens:
            design_parts.append(f"{_plan_design_system_name} palette ({', '.join(_color_tokens[:2])})")
        else:
            design_parts.append(f"{_plan_design_system_name} palette")
        if _radius:
            design_parts.append(f"radius {_radius}")
        if _vibe:
            design_parts.append(f"{_vibe} vibe")
        _design_line = " · ".join(design_parts)

        _plan_data = {
            "intro": (
                f"I'll build **{_project_name}** using the "
                f"**\"{_plan_design_system_name}\"** design system. "
                f"Here's my plan:"
            ),
            "description": _about,
            "pages":       _page_items,
            "entities":    _entity_list,
            "design":      _design_line,
            "requiresConfirmation": True,
        }
        await websocket.send_json({
            "type": "chat_message",
            "role": "agent",
            "messageType": "plan",
            "planData": _plan_data,
        })

        # ── Persist plan to DB so chat history survives server restarts ──
        if chat_session_id and user_jwt:
            try:
                from app.services.chat import ChatService
                await ChatService.add_message(
                    session_id=chat_session_id,
                    role="assistant",
                    content=json.dumps({"messageType": "plan", "planData": _plan_data}),
                    event_type="Plan",
                    user_jwt=user_jwt,
                )
            except Exception as _db_plan_err:
                logger.warning("Failed to persist plan to DB (non-fatal): %s", _db_plan_err)

        # ── Wait for user confirmation before proceeding ──────────────
        # The user sees the plan and can either confirm ("Looks Good") or
        # reject with a corrected description ("Change Direction").
        # Auto-proceed after 5 minutes if no response.
        await websocket.send_json({
            "type": "plan_awaiting_confirmation",
            "message": "Review your plan above and click 'Looks Good' to start building.",
        })
        _plan_future = register_plan_confirmation(websocket)
        try:
            _confirmation = await asyncio.wait_for(
                _plan_future, timeout=PLAN_CONFIRM_TIMEOUT_SECONDS
            )
            if not _confirmation.get("confirmed", True):
                # User rejected — they want to change direction
                _correction = _confirmation.get("correction", "")
                if _correction:
                    await _ws_send(websocket, "progress", f"🔄 Re-researching: {_correction[:60]}...")
                    logger.info("Plan rejected — re-researching with correction: %s", _correction[:100])
                    # Clean up the pending confirmation (already popped by resolve)
                    # Return False to signal the caller to retry with the corrected description
                    # Store the correction on the websocket for the orchestrator to pick up
                    websocket._plan_correction = _correction
                    return False
                else:
                    logger.info("Plan rejected but no correction — proceeding anyway")
            else:
                logger.info("Plan confirmed by user — proceeding to code generation")
                await _ws_send(websocket, "progress", "✅ Plan confirmed — starting code generation...")
        except asyncio.TimeoutError:
            logger.info("Plan confirmation timed out after %ds — auto-proceeding", PLAN_CONFIRM_TIMEOUT_SECONDS)
            await _ws_send(websocket, "progress", "⏱️ Auto-proceeding with plan (no response after 5 min)...")
            # Clean up
            pending_plan_confirmations.pop(id(websocket), None)
        except Exception as _conf_err:
            logger.warning("Plan confirmation error (non-fatal, auto-proceeding): %s", _conf_err)
            pending_plan_confirmations.pop(id(websocket), None)

    except Exception as _plan_exc:
        logger.warning("Failed to emit plan message (non-fatal): %s", _plan_exc)

    # ════════════════════════════════════════════════════════════
    #  STEP B — Phase 4: Fetch Stitch design reference
    #  This is real work, not a cosmetic delay.
    #  Order: Gemini research → Schema → Plan shown → Stitch → Code
    # ════════════════════════════════════════════════════════════
    _stitch_api_key = os.environ.get("STITCH_API_KEY", "")
    _stitch_description = (
        f"{_layout_archetype.replace('_', ' ')} {stack} — {description[:60]}"
    )

    _theme_d     = project_schema.get("theme", {})
    _disp_font   = " + ".join(f for f in [_theme_d.get("heading_font", ""), _theme_d.get("body_font", "")] if f) or "Inter"
    _disp_color  = _theme_d.get("primary", "")
    _design_summary = f"{_disp_font} · {_disp_color}" if _disp_color else _disp_font

    await _send_phase(
        websocket, 4,
        "Fetching design reference",
        f"Calling Stitch AI for UI patterns — {_plan_design_system_name}",
        "active",
    )

    stitch_html = await _call_stitch_mcp(_stitch_description, _stitch_api_key)

    if stitch_html:
        _stitch_status = f"Design reference loaded — {_design_summary}"
        await _ws_send(websocket, "progress", "✅ Stitch design reference ready")
    else:
        _stitch_status = f"Using research-based design — {_design_summary}"
        await _ws_send(websocket, "progress", "ℹ️ Stitch unavailable — proceeding with Gemini research design")

    await _send_phase(
        websocket, 4,
        "Fetching design reference",
        _stitch_status,
        "done",
    )
    await asyncio.sleep(1.2)   # brief pause so user sees Phase 4 done

    # ── PHASE GATE: Research complete → Coding starts ──
    await _send_phase(websocket, 3, "Researching project", f"Research complete ({research_quality})", "done")
    await asyncio.sleep(0.5)
    await _send_phase(websocket, 5, "Writing code", "Claude is generating project (3-phase)…", "active")
    
    # ═══════════════════════════════════════════════════════
    #  CALL 1 — FOUNDATION
    #  Theme, config, navigation, layouts, main page, router
    # ═══════════════════════════════════════════════════════
    await _ws_send(websocket, "progress", "🏗️ Phase 1/3 — Building foundation...")
    
    # Design system file instruction — generates src/lib/design-system.js
    design_system_instruction = ""
    if schema_design_spec:
        design_system_instruction = f"""\n7. DESIGN SYSTEM FILE — Generate src/lib/design-system.js (or .ts for Vue):
   Export a `ds` object with deterministic Tailwind class tokens:
   - card: exact classes for ALL cards in the project
   - badge variants: status → Tailwind classes mapping
   - section spacing, max width, heading sizes
   - animation presets (page transition, card hover, stagger)
   ALL components in Phase 2 and 3 MUST import {{ ds }} from '@/lib/design-system' and use these tokens.
   This guarantees visual consistency across the entire project.

{schema_design_spec}\n"""

    # API-ready service instruction
    api_instruction = ""
    if schema_api_spec:
        api_instruction = f"""\n8. ENV FILE — Generate .env with API URL:
   {project_schema.get('api_config', {}).get('base_url_env', 'VITE_API_URL')}={project_schema.get('api_config', {}).get('base_url_default', 'http://localhost:3001')}
\n"""

    # Layout archetype — single source of truth from confirmed classification
    _is_admin    = _layout_archetype in {"admin_dashboard", "crm", "tms", "saas_dashboard", "ecommerce"}
    _is_blog     = _layout_archetype == "blog"
    _is_consumer = _layout_archetype in {"consumer_website", "marketplace", "portfolio"}
    _is_landing  = _layout_archetype == "single_page_landing"

    # Schema-based safety net: if the schema has KPIs or CRUD pages and we classified
    # as landing/consumer, trust the schema (Gemini may have made a structure decision
    # reflected in the schema but not the classification text).
    if not _is_admin:
        _si_kpis = project_schema.get("dashboard", {}).get("kpis", [])
        _si_crud = [p for p in project_schema.get("pages", []) if p.get("type") in ("crud_list", "crud_form")]
        if _si_kpis or len(_si_crud) >= 2:
            logger.info("Schema has admin structure — reclassifying layout to admin_dashboard")
            _is_admin = True
            _is_landing = False
            _is_consumer = False
            _is_blog = False

    if stitch_html:
        # We have a live Stitch reference — inject it directly.
        # Claude reads the HTML and adapts the layout/spacing/card patterns
        # to React/Vue JSX with Tailwind classes.
        stitch_instruction = f"""
====================================
STITCH AI DESIGN REFERENCE (pre-fetched)
====================================
The following is a real Stitch AI design reference for this project type.
Study the layout structure, spacing ratios, card styles, typography hierarchy,
and color usage. Then ADAPT these patterns into React/Vue JSX with Tailwind classes.

RULES:
- Do NOT copy HTML verbatim — convert every element to React/Vue JSX
- Replace inline styles with Tailwind classes (bg-primary, text-foreground, etc.)
- Keep the SPATIAL patterns (grid columns, gap sizes, padding ratios)
- Replace any raw colors with the project's design tokens from research

STITCH REFERENCE HTML:
{stitch_html}
====================================
"""
    else:
        # No live Stitch reference — build a schema-driven design spec from Gemini research.
        # Every variable here comes from the project-specific schema, making each
        # generation unique even without a Stitch API key.
        _primary_desc  = _primary_hsl or "brand color"
        _accent_desc   = _accent_hsl or "accent"
        _vibe_desc     = _vibe or "modern"
        _font_desc     = (
            f"{_h_font} (headings) + {_b_font} (body)"
            if _h_font else "Inter (headings) + system-ui (body)"
        )
        _card_desc     = _card_cls or "rounded-lg border border-border shadow-sm p-6"
        _brand_name    = project_schema.get("brand", {}).get("name", description[:30])
        _radius        = project_schema.get("theme", {}).get("radius", "0.5rem")

        # ── Extract blog sub-type for richer design spec ──────────
        _blog_subtype = {
            "blog":          ("article listing, rich-text article body, author profiles, category/tag pages, comment section", "editorial, typographic — generous line-height, strong heading hierarchy"),
            "documentation": ("sidebar navigation tree, code blocks with syntax highlight, search bar, versioned tabs, breadcrumbs, 'On this page' anchor list", "developer-focused, clean mono — high contrast code blocks, compact prose"),
            "portfolio":     ("project grid/cards with cover image + tags + live/github links, case-study detail page, skills section, testimonials, contact form", "creative, personal — bold hero with your name/role, distinctive layout personality"),
        }.get(app_type, ("articles, pages, content sections", "clean typographic"))
        _blog_key_ui, _blog_personality = _blog_subtype

        if _is_blog:
            stitch_instruction = f"""
====================================
DESIGN SPEC — "{_plan_design_system_name}" ({app_type.replace('_', ' ').title()})
====================================
Design personality : {_vibe_desc or _blog_personality}
Primary            : {_primary_desc}
Accent             : {_accent_desc}
Typography         : {_font_desc}
Border radius      : {_radius}
Card surface       : {_card_desc}

THIS IS A {app_type.replace('_', ' ').upper()} — build domain-specific UI, NOT a generic blog.

DOMAIN-SPECIFIC COMPONENTS (build these for {app_type.replace('_', ' ')}):
{_blog_key_ui}

SPATIAL PATTERNS — content site, NO admin sidebar:
- Header: sticky top-0 bg-background/80 backdrop-blur border-b, logo left, nav center, CTA right
  h-16 max-w-6xl mx-auto px-6
- Article/content cards: {_card_desc} overflow-hidden, cover image aspect-video,
  category badge, title xl font-semibold, excerpt text-sm text-muted-foreground line-clamp-2,
  author avatar+name+date row
- Content body: prose max-w-2xl mx-auto text-lg leading-relaxed,
  headings in {_h_font or 'heading font'}, blockquote border-l-4 border-primary pl-4 italic,
  code bg-muted rounded p-4 font-mono text-sm
- Reading progress bar: h-0.5 bg-primary fixed top-0 left-0 z-50 transition-all
- Tag/category badge: text-xs px-2 py-0.5 rounded-full bg-primary/10 text-primary
- Footer: bg-muted border-t py-12, 3-4 col grid, text-sm text-muted-foreground

DO NOT use CSS variable syntax. Tailwind utility classes only.
"""
        elif _is_consumer:
            _consumer_hint = _CONSUMER_HINTS.get(app_type, {})
            _consumer_vibe  = _consumer_hint.get("vibe", _vibe_desc)
            # Research output (from Gemini) wins over hardcoded hints for ALL fields.
            # _CONSUMER_HINTS is ONLY a last-resort fallback when Gemini returns empty.
            _consumer_key_ui = (
                _research_key_components
                or _consumer_hint.get("key_ui", "domain-specific cards, hero section, feature sections")
            )
            _consumer_pages = (
                _research_pages
                or _consumer_hint.get("pages", "home, about, contact and all key domain pages")
            )
            _consumer_domain_must_haves = (
                _research_domain_must_haves
                or ""
            )
            stitch_instruction = f"""
====================================
DESIGN SPEC — "{_plan_design_system_name}" ({_layout_archetype.replace('_', ' ').title()} / {_domain.replace('_', ' ').title()})
====================================
Design personality : {_consumer_vibe}
Primary            : {_primary_desc}
Accent             : {_accent_desc}
Typography         : {_font_desc}
Border radius      : {_radius}
Card surface       : {_card_desc}

THIS IS A {app_type.replace('_', ' ').upper()} WEBSITE — build domain-specific UI, NOT a generic layout.

DOMAIN-SPECIFIC COMPONENTS (build these exactly for {app_type.replace('_', ' ')}):
{_consumer_key_ui}

PAGE STRUCTURE (each page must use these domain-specific sections):
{_consumer_pages}
{f'''
DOMAIN MUST-HAVES (from research — these features are essential):
{_consumer_domain_must_haves}
''' if _consumer_domain_must_haves else ''}
{f'''COPY TONE (from research):
{_research_copy_tone}
''' if _research_copy_tone else ''}
SPATIAL PATTERNS — public consumer website, NO admin sidebar:
- Header: sticky top-0 bg-background/80 backdrop-blur-md border-b, logo left, nav links center, CTA right
  max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 md:h-20
- Hero: min-h-[80vh] or min-h-screen, full-width background with domain-appropriate visual treatment
  ({_consumer_vibe} — use imagery/gradients that match this personality)
  headline {_h_font or 'heading font'} text-4xl md:text-6xl font-bold, subhead text-xl text-muted-foreground, dual CTA row
- Cards: {_card_desc} overflow-hidden hover:shadow-xl hover:-translate-y-1 transition-all duration-300
  (structure per domain component above — NOT generic placeholder cards)
- Section rhythm: py-20 md:py-28 px-4 sm:px-6 lg:px-8, max-w-7xl mx-auto
  alternate: white → bg-muted → white → bg-primary/5
- CTA buttons: bg-primary text-primary-foreground px-8 py-4 rounded-[{_radius}] font-semibold
  hover:opacity-90; ghost: border-2 border-primary text-primary hover:bg-primary hover:text-primary-foreground
- Footer: bg-foreground text-background (inverted) OR bg-muted, 3-4 column grid, py-16

CRITICAL: Every component must be specific to "{description[:60]}" — NOT a generic {app_type.replace('_', ' ')} template.
Do NOT use generic placeholder content — use realistic data specific to "{description[:40]}".
DO NOT use CSS variable syntax. Tailwind utility classes only.
"""
        elif _is_landing:
            # Build ordered section list from schema for domain-specific structure
            _landing_sections = _sections or []
            _section_list = "\n".join(
                f"- {s.get('type', s.get('headline', 'Section'))}: {s.get('subheadline', s.get('headline', ''))}"
                for s in _landing_sections[:12]
            ) or "- Hero (value prop + CTA)\n- Features\n- Social proof\n- Pricing\n- FAQ\n- Final CTA"

            stitch_instruction = f"""
====================================
DESIGN SPEC — "{_plan_design_system_name}" (Landing Page)
====================================
Design personality : {_vibe_desc}
Primary            : {_primary_desc}
Accent             : {_accent_desc}
Typography         : {_font_desc}
Border radius      : {_radius}
Card surface       : {_card_desc}

THIS IS A CONVERSION-OPTIMISED LANDING PAGE for: {description[:80]}

SECTION ORDER (build these exact sections from research — no generic placeholders):
{_section_list}

SPATIAL PATTERNS — apply exactly with Tailwind classes:
- Hero: full-viewport min-h-screen, headline {_h_font or 'heading font'} text-4xl md:text-6xl font-bold,
  subtext text-muted-foreground max-w-xl, dual CTA row (filled primary + ghost outlined),
  background uses primary as gradient anchor, bold visual treatment matching {_vibe_desc} personality
- Feature/benefit cards: {_card_desc}, icon in 40×40 rounded-xl bg-primary/10 p-2.5,
  title text-xl font-semibold, body text-sm text-muted-foreground,
  hover:shadow-md hover:scale-[1.02] transition-all duration-200
- Social proof: testimonial cards with large quote, author avatar + name + title + company logo
- Section rhythm: alternating white → bg-muted → white → bg-primary/5, py-16 md:py-24, max-w-6xl mx-auto px-6
- Typography: hero 4xl–6xl font-bold, h2 2xl–3xl font-semibold, body text-base leading-relaxed
- CTA buttons: bg-primary text-primary-foreground px-8 py-3 rounded-[{_radius}] font-semibold
  hover:opacity-90; ghost: border border-border bg-transparent hover:bg-muted

CRITICAL: Write original, compelling copy for THIS specific product — not lorem ipsum.
DO NOT use CSS variable syntax. Use only Tailwind utility classes.
"""
        else:
            _admin_components_block = (
                f"\nDOMAIN-SPECIFIC COMPONENTS (from research — build these exactly):\n{_research_key_components}\n"
                if _research_key_components else ""
            )
            stitch_instruction = f"""
====================================
DESIGN SPEC — "{_plan_design_system_name}"
====================================
Design personality : {_vibe_desc}
Primary            : {_primary_desc}
Accent             : {_accent_desc}
Typography         : {_font_desc}
Border radius      : {_radius}
Card surface       : {_card_desc}
{_admin_components_block}
SPATIAL PATTERNS — apply exactly with Tailwind classes:
- Sidebar: w-64 bg-card border-r border-border, "{_brand_name}" logo h-16 border-b,
  nav items px-3 py-2 rounded-md hover:bg-muted, icon w-4 h-4 mr-3, active bg-primary/10 text-primary,
  user avatar section pinned to bottom border-t
- Header: h-14 border-b bg-background flex items-center px-4, search input flex-1 max-w-sm
  rounded-md border bg-muted/40 px-3 text-sm, notification bell + user avatar right side
- KPI cards: {_card_desc}, metric value text-2xl font-bold, label text-sm text-muted-foreground,
  trend badge inline-flex items-center text-xs rounded-full px-2 py-0.5 using accent color
- DataTable: w-full sticky header bg-background text-xs uppercase tracking-wide text-muted-foreground,
  rows hover:bg-muted/50, action column with MoreHorizontal dropdown, empty-state centered icon+text
- Forms: label text-sm font-medium mb-1.5, input w-full rounded-[{_radius}] border px-3 py-2 text-sm,
  primary submit bg-primary text-primary-foreground, muted cancel bg-transparent text-muted-foreground

DO NOT use CSS variable syntax. Use only Tailwind utility classes.
"""

    phase1_prompt = f"""PHASE 1 OF 3 — FOUNDATION FILES ONLY

Generate ONLY these foundation files (Phases 2 and 3 will handle sections/features/extra pages):

1. CSS THEME FILE — Complete :root block with ALL researched colors + dark mode + custom tokens:
   - All standard tokens: --primary, --secondary, --accent, --background, --foreground, etc.
   - Custom tokens: --chart-1 through --chart-5, --success, --warning, --info
   - Border radius, ring offset, sidebar colors
   - Import BOTH Google Fonts (heading + body) via @import url()

{schema_theme_spec}

2. SITE CONFIG — Brand name, tagline, meta description, URL, social links
   Brand: {project_schema.get('brand', {}).get('name', description[:30])}
   Tagline: {project_schema.get('brand', {}).get('tagline', '')}

3. NAVIGATION CONFIG — Full domain-specific navigation with Lucide icon names, grouping, badges

{schema_nav_spec}

4. LAYOUT COMPONENTS — FULLY REWRITE Header/Footer (and Sidebar for admin) for this project:
   - Blog/content sites: sticky top nav with logo, nav links, search icon, CTA button; rich footer with columns
   - Admin panels: sidebar (w-64, brand logo, nav groups, user profile area) + top header with search+notifications
   - DO NOT copy template defaults — create a UNIQUE layout matching the research

5. MAIN PAGE:
   - Landing page: page.js that imports section components (sections come in Phase 2)
   - Blog/content: article listing at "/" — grid of article cards + category filter + search bar
   - Consumer website (restaurant/travel/fitness/etc.): homepage with domain-specific sections from research (hero, featured items, etc.)
   - Admin panel: DashboardPage with KPI cards, charts (recharts/vue-chartjs), recent activity table

{schema_dashboard_spec}

6. ROUTER — Add routes for ALL planned pages/features (pages themselves come in Phase 2-3)
   All routes from schema:
{chr(10).join(f'   {p.get("path", "")} → {p.get("component", "")} ({p.get("type", "")})' for p in project_schema.get('pages', []))}
{design_system_instruction}{api_instruction}
PROJECT: {description}
APP TYPE: {app_type}
STACK: {stack}

DESIGN SYSTEM FROM RESEARCH:
{research}

TEMPLATE MANIFEST:
{manifest[:15000]}

FILE TREE:
{file_tree[:2000]}

{stack_rules}
{template_context}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
UI POLISH LAYER — STITCH AI DESIGN REFERENCE
⚠️  FOR VISUAL/UI IMPROVEMENTS ONLY.
    Do NOT change pages, routes, entities, or nav items — those are fixed above.
    Only use this to improve: card styles, spacing ratios, color usage,
    typography hierarchy, button shapes, shadow/border patterns.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{stitch_instruction}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Call the write_project_files tool with ALL files.
"""
    
    result1 = await call_claude_for_json(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=phase1_prompt,
        api_key=api_key,
        websocket=websocket,
        max_tokens=MAX_TOKENS,
        model=MODEL,
    )
    if result1:
        written = write_files_from_json(result1, workspace_path)
        total_files += written
        await _ws_send(websocket, "progress", f"✅ Foundation: {len(written)} files")
    else:
        await _ws_send(websocket, "error", "❌ Phase 1 failed")
        return False
    
    # Rebuild file tree for Phase 2
    file_tree_2 = _build_file_tree(workspace_path)
    
    # ═══════════════════════════════════════════════════════
    #  CALL 2 — CONTENT
    #  All sections (landing) OR all CRUD features (admin)
    # ═══════════════════════════════════════════════════════
    await _ws_send(websocket, "progress", "🎨 Phase 2/3 — Building content...")
    # Phase 2 routing — driven entirely by confirmed layout archetype.
    # No hardcoded type sets. _is_admin/_is_blog/_is_consumer/_is_landing are set in Phase 1.
    if _is_blog:
        _api_cfg = project_schema.get('api_config', {})
        _api_env = _api_cfg.get('base_url_env', 'VITE_API_URL')
        _api_default = _api_cfg.get('base_url_default', 'http://localhost:3001')
        phase2_instruction = f"""Generate ALL content pages and components for this blog/content platform.

PAGES TO BUILD (create EVERY page listed in the schema):
{schema_sections_spec if schema_sections_spec else '- Article listing, Article detail, Editor, Categories, Author profile, Search'}

For EACH page, create a complete, fully-featured component:
  - ArticleListPage (/articles) — grid of article cards with category filter tabs, search bar, pagination
  - ArticleDetailPage (/articles/:slug) — full article body (prose-styled), author bio card, comment list, comment form, related articles sidebar, reading progress bar
  - ArticleEditorPage (/write or /editor) — rich text editor (use TipTap or a textarea with preview), title field, cover image URL field, category/tag selector, publish button, draft save
  - CategoriesPage (/categories) — grid of category cards with icon, color, article count
  - AuthorProfilePage (/authors/:username) — avatar, bio, social links, grid of their articles
  - SearchPage (/search) — search input, results list with query term highlighting

ARTICLE COMPONENTS (used by listing + detail pages):
  - ArticleCard — cover image (aspect-video, rounded-lg), category badge (colored), title, excerpt (line-clamp-2), author row (avatar + name + date + reading time)
  - AuthorAvatar — rounded-full, fallback initials
  - TagBadge — text-xs px-2 py-0.5 rounded-full bg-primary/10 text-primary
  - CommentCard — avatar, author name, date, body, like button
  - CommentForm — name field, body textarea, submit button

SERVICES (use json-server API — do NOT hardcode mock data in services):
  const API_URL = import.meta.env.{_api_env} || '{_api_default}';
  - articles.service.js — getAll(params), getBySlug(slug), getByCategory(category), getByTag(tag), getByAuthor(username), create(data), update(id, data)
  - authors.service.js — getAll(), getByUsername(username)
  - categories.service.js — getAll()
  - comments.service.js — getByArticle(articleId), create(data)

MOCK DATA (from schema entities — use realistic blog content):
{schema_entity_spec}
{schema_api_spec}

ALSO implement DOMAIN_MUST_HAVES from research:
- Reading progress bar (fixed top, h-0.5, bg-primary, updates on scroll)
- Related posts (same category, show 3 cards at bottom of article detail)
- Table of contents (extract headings from body, sticky sidebar list)
- Newsletter signup section (email input + subscribe button, simple design)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
UI POLISH — CONTENT SITE PATTERNS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- Article body: prose max-w-2xl mx-auto, text-lg leading-relaxed text-foreground
- Headings in article: {_h_font or 'serif/heading font'}, font-bold with anchor links
- Images in article: w-full rounded-xl shadow-md my-8
- Blockquote: border-l-4 border-primary pl-6 italic text-muted-foreground
- Code blocks: bg-muted font-mono text-sm rounded-lg p-4
{stitch_instruction}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"""

    elif _is_consumer:
        # Consumer website / marketplace / portfolio: public-facing pages (NOT admin CRUD tables)
        _schema_pages = project_schema.get("pages", [])
        _pages_list = "\n".join(
            f"  - {p.get('path','')} → {p.get('title','').replace('Page','').strip() or p.get('component','')} ({p.get('purpose', p.get('type',''))})"
            for p in _schema_pages
        )
        _api_cfg    = project_schema.get('api_config', {})
        _api_env    = _api_cfg.get('base_url_env', 'VITE_API_URL')
        _api_default = _api_cfg.get('base_url_default', 'http://localhost:3001')

        phase2_instruction = f"""Generate ALL pages and domain-specific components for this {_layout_archetype.replace('_', ' ')} ({_domain} domain).

THIS IS A PUBLIC-FACING {_layout_archetype.replace('_',' ').upper()} — NOT an admin panel.
Use top-nav layout (no sidebar). Pages are customer-facing, NOT management dashboards.

PAGES TO BUILD (from schema — every single one):
{_pages_list or '(build all domain-specific pages from research)'}

For EACH page, create a complete component with:
- Full domain-specific content (real copy, realistic data, proper images)
- All the key sections listed in ===PAGES=== from the research blueprint
- Domain-specific components unique to "{description[:50]}" (from ===KEY_COMPONENTS=== in research)
- Responsive layout (mobile-first: sm: md: lg:)
- Framer-motion animations (fade-up on scroll for sections, hover effects on cards)

DOMAIN-SPECIFIC COMPONENTS to create (reusable across pages):
(Build the components listed in ===KEY_COMPONENTS=== from the research)
- Each as a separate file in src/components/[domain]/
- With realistic mock prop data (hard-coded for display, not fetched)
- Fully styled with Tailwind using design tokens from research

DATA SERVICES (for entities that need dynamic data):
const API_URL = import.meta.env.{_api_env} || '{_api_default}';
- Create one service per entity (getAll, getById, search, filter)
- Use graceful fallback to mock data if API unavailable

{schema_entity_spec}

ALSO: Implement DOMAIN_MUST_HAVES from research:
(gallery masonry, booking calendar, reservation widget, menu filtering, interactive map, etc.)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
UI POLISH — CONSUMER WEBSITE PATTERNS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- Hero: full-width with domain-appropriate image treatment (overlay gradient, large headline)
- Section spacing: py-20 md:py-28, max-w-6xl mx-auto px-4 sm:px-6 lg:px-8
- Cards: rounded-xl overflow-hidden shadow-sm hover:shadow-xl hover:-translate-y-1 transition-all duration-300
- Primary CTA: bg-primary text-primary-foreground px-8 py-4 rounded-lg font-semibold hover:opacity-90
- Images: use picsum.photos for realistic photos (https://picsum.photos/seed/[domain][N]/800/600)
{stitch_instruction}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"""

    elif _is_admin:
        _api_cfg = project_schema.get('api_config', {})
        _api_env = _api_cfg.get('base_url_env', 'VITE_API_URL')
        _api_default = _api_cfg.get('base_url_default', 'http://localhost:3001')
        phase2_instruction = f"""Generate ALL CRUD feature modules from the schema below.
For EACH entity, create the COMPLETE feature folder:
  - services/[entity].service.js — REAL fetch() calls to the API (NOT hardcoded mock data)
  - hooks/use[Entity].js — React Query / Vue Query wrappers
  - pages/[Entity]ListPage — DataTable with schema-defined columns, actions, filters, search
  - pages/[Entity]FormPage — Form with schema-defined fields + validation (react-hook-form + zod)

IMPORTANT — API-READY SERVICES:
  Services must use REAL HTTP fetch() calls:
    const API_URL = import.meta.env.{_api_env} || '{_api_default}';
    getAll: (params) => fetch(`${{API_URL}}/entity?${{new URLSearchParams(params)}}`).then(r => r.json())
  DO NOT hardcode mock data arrays inside services.
  The mock data lives in db.json (already generated) and is served by json-server.
  Services should catch errors and return empty arrays on failure (graceful degradation).

IMPORTANT — DESIGN SYSTEM:
  Import {{ ds }} from '@/lib/design-system' in ALL components.
  Use ds.card for card wrappers, ds.badge[status] for status badges.

{schema_entity_spec}
{schema_api_spec}

ALSO: Create any domain-specific specialized views from DOMAIN_MUST_HAVES in the research:
- Maps, calendars, kanban boards, timelines, etc.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
UI POLISH LAYER — STITCH AI DESIGN REFERENCE
⚠️  FOR VISUAL/UI IMPROVEMENTS ONLY.
    Entities and CRUD structure are fixed above (from schema).
    Only use this to improve: table density, card padding, form layout,
    badge styles, button shapes, empty-state visuals, sidebar widths.
    Do NOT add or remove entities/fields based on this reference.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{stitch_instruction}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"""
    else:
        # Build the UI polish block for landing sections
        if stitch_html:
            _stitch_ui_polish = f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
UI POLISH LAYER — STITCH AI DESIGN REFERENCE
⚠️  FOR VISUAL/UI IMPROVEMENTS ONLY.
    The sections to build are fixed above (from schema).
    Only use this to improve: card spacing, grid gaps, typography scale,
    button shapes, hero layout ratios, shadow/border patterns.
    Do NOT add or remove sections based on this reference.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STITCH REFERENCE HTML (adapt to React JSX + Tailwind — do NOT copy verbatim):
{stitch_html[:4000]}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
        else:
            _stitch_ui_polish = """
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
UI POLISH LAYER — SECTION SPATIAL PATTERNS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- Hero: min-h-screen, split or centered layout, gradient bg, headline text-5xl md:text-7xl, dual CTA
- Features: py-24 section padding, 3-col grid gap-8, icon w-12 h-12, card hover:-translate-y-1 shadow-lg
- Pricing: 3 cards, middle card bg-primary text-primary-foreground scale-105 shadow-2xl
- Testimonials: quote cards with avatar + name + role, grid or horizontal scroll
- CTA: full-width gradient, centered headline, prominent button with icon
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
        phase2_instruction = f"""Generate ALL section components for the landing page.
Create EVERY section listed in the schema — NO LIMIT.

SECTIONS TO BUILD (from research schema — do NOT add or remove any):
{schema_sections_spec}

Each section must be:
- A complete, self-contained component
- Fully responsive (mobile-first: sm: md: lg: xl:)
- Animated with framer-motion (fade-up on scroll, hover effects)
- Using REAL domain-specific copy (not lorem ipsum)
- Import {{ ds }} from '@/lib/design-system' and use ds.sectionSpacing, ds.maxWidth, ds.card
- With realistic mock data (testimonials with i.pravatar.cc avatars, pricing with real USD)

ALSO: Create any domain-specific must-have sections from the research:
- Product demos, ROI calculators, comparison tables, integration showcases, etc.
{_stitch_ui_polish}"""
    
    phase2_prompt = f"""PHASE 2 OF 3 — CONTENT FILES

The foundation is already built (see file tree below). DO NOT regenerate foundation files.
{phase2_instruction}

PROJECT: {description}
APP TYPE: {app_type}
STACK: {stack}

DESIGN SYSTEM FROM RESEARCH:
{research}

TEMPLATE MANIFEST:
{manifest[:15000]}

CURRENT FILE TREE (foundation already written):
{file_tree_2[:2000]}

{stack_rules}
{skills}

Call the write_project_files tool with ALL files.
"""
    
    # Phase 2 is the heaviest — all CRUD modules or all sections in one call.
    # Give it 2x the normal token budget to avoid truncation on big admin panels.
    PHASE2_MAX_TOKENS = 64000
    
    result2 = await call_claude_for_json(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=phase2_prompt,
        api_key=api_key,
        websocket=websocket,
        max_tokens=PHASE2_MAX_TOKENS,
        model=MODEL,
    )
    if result2:
        written = write_files_from_json(result2, workspace_path)
        total_files += written
        await _ws_send(websocket, "progress", f"✅ Content: {len(written)} files")
    else:
        await _ws_send(websocket, "progress", "⚠️ Phase 2 returned no files — continuing with Phase 3...")
        logger.warning("Phase 2 (content) returned no files")
    
    # Rebuild file tree for Phase 3
    file_tree_3 = _build_file_tree(workspace_path)
    
    # ═══════════════════════════════════════════════════════
    #  CALL 3 — ADDITIONAL PAGES + POLISH
    # ═══════════════════════════════════════════════════════
    await _ws_send(websocket, "progress", "✨ Phase 3/3 — Building additional pages...")
    
    # Build the list of schema-required pages that may still be missing
    schema_pages_list = "\n".join(
        f"   {p.get('path', '')} → {p.get('component', '')} ({p.get('type', '')})"
        for p in project_schema.get('pages', [])
    )

    # Build type-aware Phase 3 instruction — prefer schema/research over hardcoded hints.
    # First, check what pages are in the schema that may not have been generated yet.
    _schema_page_names = [
        p.get("title", p.get("component", "")).replace("Page", "").strip()
        for p in project_schema.get("pages", [])
    ]
    _schema_page_hint = ", ".join(_schema_page_names[:10]) if _schema_page_names else ""

    # Phase 3 hint — fully driven by schema + research (no hardcoded per-type hints).
    # Priority: schema pages list → research domain must-haves → generic fallback
    if _schema_page_hint:
        _p3_type_hint = f"All remaining schema pages not yet generated: {_schema_page_hint}"
        if _research_domain_must_haves:
            _p3_type_hint += f"\n   Plus domain must-haves from research:\n   {_research_domain_must_haves[:500]}"
    elif _research_domain_must_haves:
        _p3_type_hint = f"Domain must-haves from research:\n   {_research_domain_must_haves[:500]}"
    else:
        _p3_type_hint = (
            f"Any pages/sections referenced in navigation or schema not yet generated. "
            f"For a {_layout_archetype.replace('_', ' ')} ({_domain} domain), "
            f"ensure all expected {'sections' if _is_landing else 'pages'} are fully built."
        )

    # ── Single-page landing: Phase 3 is COMPLETENESS-ONLY — no extra pages ──────
    if _is_landing:
        phase3_prompt = f"""PHASE 3 OF 3 — COMPLETENESS CHECK (landing page)

This is a SINGLE-PAGE landing page. Do NOT create separate About, Pricing, or Contact pages.
Everything must be a section within the single homepage (src/app/page.js or src/pages/index.js).

RULE: Only skip a section/component if it already exists AND has substantial content (> 40 lines of real JSX).
Any stub or placeholder MUST be fully rewritten.

YOUR ONLY TASK:
1. COMPLETENESS CHECK — verify every section component exists and is fully built:
   - If a section component does NOT exist → CREATE it with FULL content
   - If a section component is a stub (< 40 lines or returns empty div) → REWRITE it with full content
   - Any imports referencing missing files → CREATE those files

2. MISSING COMPONENTS — build any section components referenced in page.js but not yet created:
   - Domain-specific interactive elements (pricing calculator, FAQ accordion, testimonial carousel, etc.)
   - 404 Not Found page (app/not-found.js) with domain-appropriate design
   - Loading/skeleton components if referenced

3. DESIGN SYSTEM CONSISTENCY — all new files must match Phase 1 + Phase 2 visual style:
   - If src/lib/design-system.js exists: import {{ ds }} from '@/lib/design-system' and use ds.card, ds.sectionSpacing, ds.maxWidth
   - Use same Tailwind class patterns as other components in the project

DO NOT generate: /about, /pricing, /contact, or any other route pages.
All content must be sections within the single landing page.

PROJECT: {description}
APP TYPE: {app_type}
STACK: {stack}

DESIGN SYSTEM FROM RESEARCH:
{research}

TEMPLATE MANIFEST:
{manifest[:15000]}

CURRENT FILE TREE (foundation + content already written):
{file_tree_3[:3000]}

{stack_rules}

Call the write_project_files tool with ALL files.
"""
    else:
        phase3_prompt = f"""PHASE 3 OF 3 — ADDITIONAL PAGES + COMPLETENESS CHECK

The foundation and content are built (see file tree below).

APP TYPE: {app_type} — generate type-appropriate additional pages.

RULE: Only skip a page if it already exists AND has substantial content (> 40 lines of real JSX).
Stubs, placeholder components that return <div/>, or pages with < 40 lines MUST be fully rewritten.

Generate:
1. ALL ADDITIONAL PAGES not yet created for this app type:
   {_p3_type_hint}
   Plus any other pages referenced in navigation or schema that don't exist yet.

2. COMPLETENESS CHECK — verify EVERY schema page:
   Schema-required pages:
{schema_pages_list}
   - If a page file does NOT exist → CREATE it with FULL content
   - If a page file exists but is a stub (< 40 lines or returns empty div) → REWRITE it with full content
   - Any navigation items without corresponding pages → CREATE the page with full content
   - Any imports referencing missing files → CREATE those files

   EVERY page must have:
   - A proper hero/header section with real copy for "{description[:50]}"
   - Domain-specific sections (not generic placeholders)
   - Realistic mock data or hardcoded display data
   - Framer-motion animations (fade-up, hover)
   - Responsive layout (mobile-first)

3. DOMAIN-SPECIFIC MISSING COMPONENTS:
   - Any specialized component referenced in research DOMAIN_MUST_HAVES not yet built
   - Interactive maps (use Leaflet or iframe embed), booking calendars, galleries, etc.
   - 404 Not Found page with domain-appropriate design
   - Loading skeleton components for each main data type

4. DESIGN SYSTEM CONSISTENCY — all new files must match Phase 1 + Phase 2 visual style:
   - If src/lib/design-system.js exists: import {{ ds }} from '@/lib/design-system' and use ds.card, ds.sectionSpacing, ds.maxWidth
   - Use same Tailwind class patterns as other pages in the project

PROJECT: {description}
APP TYPE: {app_type}
STACK: {stack}

DESIGN SYSTEM FROM RESEARCH:
{research}

TEMPLATE MANIFEST:
{manifest[:15000]}

CURRENT FILE TREE (foundation + content already written):
{file_tree_3[:3000]}

{stack_rules}

Call the write_project_files tool with ALL files.
"""
    
    result3 = await call_claude_for_json(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=phase3_prompt,
        api_key=api_key,
        websocket=websocket,
        max_tokens=MAX_TOKENS,
        model=MODEL,
    )
    if result3:
        written = write_files_from_json(result3, workspace_path)
        total_files += written
        await _ws_send(websocket, "progress", f"✅ Pages: {len(written)} files")
    else:
        await _ws_send(websocket, "progress", "⚠️ Phase 3 returned no files — proceeding with build...")
        logger.warning("Phase 3 (extra pages) returned no files")
    
    if not total_files:
        await _ws_send(websocket, "error", "❌ No files were generated. Check API key and credits.")
        return False
    
    await _ws_send(websocket, "progress", f"💾 Total: {len(total_files)} files generated")

    # ── Signal Phase 5 (Coding) done, Phase 6 (Build) active ──
    await _send_phase(websocket, 5, "Writing code", f"Code complete — {len(total_files)} files", "done")
    await _send_phase(websocket, 6, "Verifying build", "Running build checks…", "active")
    
    # ── Post-generation fixers (before build) ──
    # Automatically fix the 3 most common build error causes:
    #   1. Missing 'use client' directives
    #   2. Banned lucide-react icon imports
    #   3. Unresolved imports (create stub files)
    try:
        from app.services.post_generation_fixer import run_all_fixers
        fix_results = await run_all_fixers(workspace_path, websocket)
        if fix_results["total_fixes"] > 0:
            await _ws_send(
                websocket, "progress",
                f"🔧 Auto-fixed {fix_results['total_fixes']} potential build issues",
            )
    except Exception as pgf_err:
        logger.warning("Post-generation fixers failed (non-fatal): %s", pgf_err)
    
    # ── Build verification ──
    build_ok = await verify_and_fix_build(
        workspace_path=workspace_path,
        api_key=api_key,
        websocket=websocket,
    )
    
    # ── Quality scoring (non-blocking) ──
    try:
        from app.services.quality_scorer import score_project
        quality_result = await score_project(workspace_path, websocket)
        # Send quality score to frontend
        try:
            await websocket.send_json({
                "type": "quality_score",
                "score": quality_result["score"],
                "grade": quality_result["grade"],
                "checks": {k: {"label": v["label"], "score": v["score"], "value": v["value"]} for k, v in quality_result.get("checks", {}).items()},
                "warnings": quality_result.get("warnings", []),
            })
        except Exception:
            pass
    except Exception as qs_err:
        logger.warning("Quality scoring failed (non-fatal): %s", qs_err)

    # ── Send file list to frontend ──
    try:
        all_files = []
        for root, dirs, fnames in os.walk(workspace_path):
            dirs[:] = [d for d in dirs if d not in {"node_modules", ".git", ".next"}]
            for f in fnames:
                all_files.append(os.path.relpath(os.path.join(root, f), workspace_path))
        await websocket.send_json({
            "type": "file_change",
            "files": sorted(all_files),
        })
    except Exception:
        pass

    # ── Mark generation_complete in DB so re-entering skips rebuild ──
    # This flag is checked in ws.py when a new session opens for an
    # existing project — if True, pipeline is suppressed and the user
    # sees chat history + workspace ready to receive follow-up requests.
    if chat_session_id and user_jwt:
        try:
            from app.supabase_client import db_client
            async with db_client(user_jwt) as _client:
                await (
                    _client.table("chat_sessions")
                    .update({"generation_complete": True})
                    .eq("id", chat_session_id)
                    .execute()
                )
            logger.info("Marked generation_complete=True for session %s", chat_session_id)
        except Exception as _gc_err:
            logger.warning("Failed to set generation_complete (non-fatal): %s", _gc_err)

    return True


VALID_APP_TYPES = [
    "admin_panel", "dashboard", "e_commerce", "saas_app",
    "landing_page", "blog", "portfolio", "crm", "erp",
    "logistics", "healthcare", "finance", "education", "other",
]

