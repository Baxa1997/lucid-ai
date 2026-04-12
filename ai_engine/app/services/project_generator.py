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


def _load_skills(app_type: str, stack: str) -> str:
    """Load relevant skill files based on project type.
    
    Skills teach Claude the EXACT API and usage patterns for each library.
    This is Layer 2 of the 3-layer architecture:
      Layer 1: MCP (live docs) — future
      Layer 2: Skills (local expertise) — THIS
      Layer 3: Plugins (bundled features) — future
    """
    # Determine which skill set to use
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


# ╔══════════════════════════════════════════════════════════════╗
# ║  STEP 3 — gemini_deep_research()                            ║
# ║  Ultra-deep product research via Gemini with internet search ║
# ╚══════════════════════════════════════════════════════════════╝

async def gemini_deep_research(
    description: str,
    app_type: str,
    stack: str,
    gemini_key: str,
    websocket,
) -> str:
    """Ultra-deep product research via Gemini with internet search.
    
    Analyzes 3-5 real products in the user's domain and extracts a
    CODE-READY BLUEPRINT — not a report. Returns the raw text blueprint .
    """
    await _ws_send(websocket, "progress", "🔬 Researching top products in this domain...")

    CRUD_TYPES = {
        "admin_panel", "ecommerce", "saas_app", "analytics",
        "education", "medical", "fitness", "booking",
        "social", "food_restaurant", "travel", "real_estate",
    }
    
    if app_type in CRUD_TYPES:
        research_prompt = f"""
You are a SENIOR UI/UX ARCHITECT. Your job: search the internet, study the TOP 3-5 real products 
similar to "{description}", then output a CODE-READY BLUEPRINT for a code generator.

IMPORTANT RULES:
- EVERY value must come from your actual internet research — never invent or use defaults
- Study the real products' actual UI, colors, layout, navigation, data models
- Synthesize the BEST patterns from ALL products you analyze into one unified design
- Output must be structured and specific — a code generator will parse this directly
- No paragraphs, no explanations — only structured data

UI/UX STANDARDS TO APPLY (on top of competitor research):
- Follow 8px spacing grid (padding/margins in multiples of 8: 8, 16, 24, 32, 48)
- Ensure WCAG AA contrast ratio (4.5:1 for text, 3:1 for large text)
- Use a proper type scale (12/14/16/18/24/30/36/48px)
- Apply visual hierarchy: primary actions prominent, secondary subdued, destructive red
- Consistent icon sizing (16px inline, 20px buttons, 24px nav, 40px empty states)
- Minimum 44x44px touch targets for mobile
- Admin panels = compact density, landing pages = spacious
- Progressive disclosure: essentials first, details on demand

OUTPUT THIS EXACT STRUCTURE (fill ALL values from your research):

===PRODUCTS_ANALYZED===
1. [Product name] ([URL]) — [what makes their UI excellent]
2. [Product name] ([URL]) — [what makes their UI excellent]
3. [Product name] ([URL]) — [what makes their UI excellent]

===CSS_VARIABLES===
(Extract dominant color palette from best product. Convert to HSL. Every value from research.)
--primary: [hsl value from research]
--primary-foreground: [hsl value]
--secondary: [hsl value]
--secondary-foreground: [hsl value]
--accent: [hsl value]
--accent-foreground: [hsl value]
--destructive: [hsl value]
--destructive-foreground: [hsl value]
--background: [hsl value]
--foreground: [hsl value]
--card: [hsl value]
--card-foreground: [hsl value]
--muted: [hsl value]
--muted-foreground: [hsl value]
--border: [hsl value]
--ring: [hsl value]
--radius: [value]rem
--chart-1: [hsl value]
--chart-2: [hsl value]
--chart-3: [hsl value]
--chart-4: [hsl value]
--chart-5: [hsl value]
--success: [hsl value]
--warning: [hsl value]
--info: [hsl value]

===DARK_MODE_VARIABLES===
(If the analyzed products use dark mode, provide dark overrides)
--background: [hsl value]
--foreground: [hsl value]
--card: [hsl value]
(... all overrides needed)

===FONTS===
heading: [font name] ([Google Fonts URL with weights])
body: [font name] ([Google Fonts URL with weights])

===SIDEBAR_NAVIGATION===
(Model after real products' actual navigation structure)
[group: "[group name from research]"]
- [Menu item] | [LucideIconName]
- [Menu item] | [LucideIconName] | badge: "[if applicable]"
[group: "[group name]"]
- [Menu item] | [LucideIconName]
(as many groups and items as the real products have)

===DASHBOARD===
(Model after real products' actual dashboards)

[kpi_cards]
1. label: "[metric from research]" | value: "[realistic format]" | change: "[%]" | trend: up/down | icon: [LucideIcon] | color: "[token]"
2-4. (same format)

[chart_1]
type: [area/bar/line — based on real products]
title: "[chart title from research]"
x_axis: [from research]
data_series:
  - name: "[series]" | values: [12 realistic data points]
color: [CSS variable tokens]

[chart_2]
type: [bar/pie/donut — from research]
title: "[chart title]"
data: [categories and values for this domain]

[recent_table]
title: "[table title from research]"
columns: [columns from real products]
rows: (8 rows of realistic mock data for this domain)

===ENTITIES===
(List EVERY data entity the real products manage)

[entity: [EntityName from research]]
list_columns: [column(type) | column(type) | ... — from real product tables]
list_filters: [filters real products offer]
list_actions: [actions in real products]
form_fields:
  - [field] | type: [type] | required: [true/false] | placeholder: "[domain-specific]"
mock_data: (10 rows of realistic data for this entity)

[entity: [EntityName]]
(repeat for every entity — as many as the real products manage)

===STATUS_BADGES===
(Based on statuses the real products use)
[status]: [color] ([exact Tailwind classes])

===DOMAIN_MUST_HAVES===
(Essential features that ALL real competitors have. If missing, users consider app broken.)
For EACH must-have:
1. feature_name: [name]
2. component_type: [map/calendar/kanban/chart/gallery/timeline/...]
3. implementation: [library or custom component + how to render with mock data]
4. data_needed: [mock data needed]
5. why_essential: [1 sentence]

===UI_PATTERNS===
card: [exact Tailwind classes from research]
table: [table style with Tailwind]
button_primary: [exact Tailwind classes]
input: [exact Tailwind]
sidebar_width: [px from research]
header_height: [px]
content_max_width: [px]
animation: [animation approach]
overall_vibe: [2-3 words]

===AVATAR_SOURCES===
user_avatars: https://i.pravatar.cc/40?u=[unique_string]
profile_avatars: https://i.pravatar.cc/150?u=[unique_string]
company_logos: [rendering approach]

REMEMBER: Every value must come from your internet research.
Do NOT invent values or use generic defaults.
"""
    else:
        # LANDING PAGE / WEBSITE research prompt
        research_prompt = f"""
You are a SENIOR UI/UX ARCHITECT. Your job: search the internet, study the TOP 3-5 real websites 
similar to "{description}", then output a CODE-READY BLUEPRINT for a code generator.

IMPORTANT RULES:
- EVERY value must come from your actual internet research — never invent or use defaults
- Study the real websites' actual design, colors, sections, copy, layout patterns
- Synthesize the BEST patterns from ALL sites you analyze into one unified design
- Output must be structured and specific — a code generator will parse this directly
- No paragraphs, no explanations — only structured data

UI/UX STANDARDS TO APPLY (on top of competitor research):
- Follow 8px spacing grid (section padding, card gaps, margins — multiples of 8)
- Ensure WCAG AA contrast ratio (4.5:1 text, 3:1 large text)
- Use proper type scale (hero 48-72px, section headings 28-36px, body 16-18px)
- F-pattern and Z-pattern reading flow
- Hero = maximum visual impact, decreasing weight per section
- CTA buttons visually dominant (size, color contrast, whitespace)
- Rule of thirds for hero layout balance
- Section rhythm: alternate content-heavy and visual-break sections
- Mobile-first responsive: stack columns, increase touch targets

OUTPUT THIS EXACT STRUCTURE (fill ALL values from your research):

===WEBSITES_ANALYZED===
1. [Website name] ([URL]) — [what makes their design excellent]
2. [Website name] ([URL]) — [what makes their design excellent]
3. [Website name] ([URL]) — [what makes their design excellent]

===CSS_VARIABLES===
(Extract dominant color palette from best website. Convert to HSL.)
--primary: [hsl value from research]
--primary-foreground: [hsl value]
--secondary: [hsl value]
--secondary-foreground: [hsl value]
--background: [hsl value]
--foreground: [hsl value]
--muted: [hsl value]
--muted-foreground: [hsl value]
--accent: [hsl value]
--accent-foreground: [hsl value]
--card: [hsl value]
--card-foreground: [hsl value]
--border: [hsl value]
--ring: [hsl value]
--radius: [value]rem

===DARK_MODE===
is_default: [true/false — based on research]
(provide overrides for the opposite mode)

===FONTS===
heading: [font name] ([Google Fonts URL with weights])
body: [font name] ([Google Fonts URL with weights])
hero_size: [size from research] / [tracking] / [weight]
section_heading: [size] / [weight]
body_size: [size] / [line-height]

===HERO_GRADIENT===
classes: [exact Tailwind gradient classes]
overlay: [overlay classes if applicable]
pattern: [background pattern if used]

===HEADER_NAVIGATION===
logo: [Brand name for this project]
items: [nav items from research]
cta: "[CTA text]" → [button style]
sticky: [true/false]
blur_bg: [true/false]

===FOOTER===
columns:
  - title: "[column title]" | links: [relevant links]
  - (3-4 columns)
bottom: "[copyright text]"
social: [social platforms]

===SECTIONS===
(List EVERY section, based on what real sites include. NO LIMIT.)

[section: hero]
headline: "[Compelling headline — original copy inspired by best sites' tone]"
subheadline: "[Value proposition — original copy]"
cta_primary: "[Button text]" | icon: [LucideIcon] | style: [from research]
cta_secondary: "[Button text]" | icon: [LucideIcon] | style: [from research]
layout: [layout pattern from best site]
background: [exact background treatment]
animation: [animation pattern]

[section: social_proof]
headline: "[social proof headline]"
logos: [6 company names relevant to domain]
style: [exact rendering style]

[section: features]
headline: "[section headline — original copy]"
subheadline: "[intro text]"
layout: [layout from best site]
features:
  1. title: "[feature]" | icon: [LucideIcon] | description: "[1 sentence]"
  (as many features as real sites showcase)
card_style: [exact Tailwind classes]

[section: how_it_works]
headline: "[headline]"
layout: [from research]
steps:
  1. title: "[step]" | description: "[how it works]" | icon: [LucideIcon]

[section: pricing]
headline: "[headline]"
tiers:
  1. name: "[tier]" | price: "[price]" | period: "[billing]" | cta: "[text]" | features: [list]
  2. name: "[tier]" | price: "[price]" | badge: "[if popular]" | features: [list] | highlighted: true
  3. name: "[tier]" | price: "[price]" | cta: "[text]" | features: [list]

[section: testimonials]
headline: "[headline]"
layout: [from research]
testimonials:
  1. quote: "[realistic testimonial]" | name: "[name]" | title: "[role]" | company: "[company]" | avatar: https://i.pravatar.cc/150?u=[unique]
  (3-4 testimonials)

[section: faq]
headline: "[headline]"
layout: [from research]
items:
  1. q: "[real question for this domain]" | a: "[helpful answer]"
  (5-8 FAQs)

[section: cta_banner]
headline: "[final CTA headline]"
subheadline: "[supporting text]"
cta: "[button text]" | icon: [LucideIcon]
background: [exact Tailwind classes]

(add MORE sections if best sites have stats, integrations, comparison tables, etc.)

===ADDITIONAL_PAGES===
about: [content based on real sites]
pricing: [expanded with comparison table]
contact: [form fields + info cards]
blog: [listing layout]

===DOMAIN_MUST_HAVES===
(Essential sections/features ALL real competitors have.)
For EACH must-have:
1. feature_name: [name]
2. section_type: [calculator/carousel/showcase/widget/...]
3. implementation: [how to build with React + Tailwind + framer-motion]
4. data_needed: [mock data]
5. why_essential: [1 sentence]

===UI_PATTERNS===
card: [exact Tailwind classes]
button_primary: [exact Tailwind classes]
button_ghost: [exact Tailwind classes]
section_spacing: [spacing pattern]
max_width: [max-width]
scroll_animation: [animation approach]
hover_cards: [hover effect classes]
overall_vibe: [2-3 words]

===AVATAR_SOURCES===
testimonials: https://i.pravatar.cc/150?u=[unique]
team: https://i.pravatar.cc/300?u=team[N]
hero_image: [how best sites handle hero visuals]
feature_icons: Lucide React (specify icon name per feature)

REMEMBER: Every value must come from your internet research.
Do NOT invent values or use generic defaults.
Write original copy inspired by TONE and STYLE of the best sites.
"""

    # Call Gemini via direct REST API (deprecated SDK removed)
    import httpx
    
    await _ws_send(websocket, "progress", "🔬 Calling Gemini 2.5 Flash with search...")
    
    gemini_url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-2.5-flash:generateContent?key={gemini_key}"
    )
    
    gemini_payload = {
        "contents": [{"parts": [{"text": research_prompt}]}],
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": 8000,
        },
        "tools": [{"google_search": {}}],
    }
    
    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(gemini_url, json=gemini_payload)
    
    if response.status_code != 200:
        error_snippet = response.text[:300]
        logger.error("Gemini research API error %d: %s", response.status_code, error_snippet)
        if response.status_code in (400, 403):
            await _ws_send(websocket, "error", "❌ Google API key invalid. Check GOOGLE_API_KEY in .env.")
        elif response.status_code == 429:
            await _ws_send(websocket, "error", "⚠️ Gemini rate limit hit. Waiting before retry...")
        raise RuntimeError(f"Gemini research API error: {response.status_code}")
    
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
        text = "\n".join(
            p.get("text", "") for p in parts
            if isinstance(p, dict) and p.get("text")
        )
    else:
        logger.error("Gemini returned unexpected content type: %s", type(content).__name__)
        text = ""
    
    if not text:
        raise RuntimeError("Gemini returned empty research text")
    
    await _ws_send(websocket, "progress", "✅ Research complete — building project blueprint...")
    
    # Send research summary to chat panel so user can see what was analyzed
    try:
        # Extract analyzed products section for display
        products_section = ""
        if "===PRODUCTS_ANALYZED===" in text:
            start = text.index("===PRODUCTS_ANALYZED===") + len("===PRODUCTS_ANALYZED===")
            end = text.index("===", start) if "===" in text[start:] else start + 500
            products_section = text[start:end].strip()
        
        if products_section:
            summary = f"🔍 **Research Complete**\n\n**Products Analyzed:**\n{products_section[:500]}"
        else:
            summary = f"🔍 **Research Complete** — Analyzed top products in the {app_type.replace('_', ' ')} domain"
        
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
    app_type = await classify_project_type_ai(description, gemini_key)
    
    await _ws_send(websocket, "progress", f"📋 App type: {app_type.replace('_', ' ').title()}")
    
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
    skills = _load_skills(app_type, stack)
    await _ws_send(websocket, "progress", "📚 Loading component skills...")
    
    # ── Step 3: Gemini ULTRA-DEEP research ──
    await _ws_send(websocket, "progress", "🔬 Researching real products in this domain...")
    research_quality = "full"  # Track research quality for gating
    try:
        research = await gemini_deep_research(
            description, app_type, stack, gemini_key, websocket,
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
        _domain = _brand.get("domain", app_type.replace("_", " "))

        _h_font      = _theme.get("heading_font", "")
        _b_font      = _theme.get("body_font", "")
        _primary_hsl = _theme.get("primary", "")
        _bg_hsl      = _theme.get("background", "")
        _fg_hsl      = _theme.get("foreground", "")
        _accent_hsl  = _theme.get("accent", "")

        _domain_lower = _domain.lower()
        # Strip enrichment header so the plain user description drives keyword matching
        _raw_desc = description.split("\n\n---\n\n")[0].strip()

        # Prefer the AI-generated name from the schema — it knows the brand,
        # colors, and vibe so it can produce a truly product-specific name.
        # Only fall back to the keyword/random lookup when the schema omits it.
        _schema_ds_name = _ds.get("name", "").strip()
        if _schema_ds_name:
            _plan_design_system_name = _schema_ds_name
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
        _is_admin = bool(_entities)

        if _is_admin:
            # ── Admin / CRUD description ───────────────────────
            _entity_names = [e.get("name", "") for e in _entities[:6] if e.get("name")]
            _entity_count = len(_entity_names)
            _entity_str   = ", ".join(_entity_names[:4])
            if _entity_count > 4:
                _entity_str += f", and {_entity_count - 4} more"

            # Sentence 1 — what it manages
            _about_s1 = (
                f"A full-stack {app_type.replace('_', ' ')} managing "
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
        _vibe       = _ds.get("overall_vibe", "") or _domain.replace("_", " ").title()
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

        await asyncio.sleep(1.5)   # let user read the plan

    except Exception as _plan_exc:
        logger.warning("Failed to emit plan message (non-fatal): %s", _plan_exc)

    # ════════════════════════════════════════════════════════════
    #  STEP B — Phase 4: Fetch Stitch design reference
    #  This is real work, not a cosmetic delay.
    #  Order: Gemini research → Schema → Plan shown → Stitch → Code
    # ════════════════════════════════════════════════════════════
    _stitch_api_key = os.environ.get("STITCH_API_KEY", "")
    _stitch_description = (
        f"{app_type.replace('_', ' ')} {stack} — {description[:60]}"
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

    # ── Build Stitch AI reference block for this stack ───────
    _is_landing = app_type not in {
        "admin_panel", "ecommerce", "saas_app", "analytics",
        "education", "medical", "fitness", "booking",
        "social", "food_restaurant", "travel", "real_estate",
    }

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
        # No live reference — keep the original guidance for Claude to try itself.
        if "next" in stack.lower() or "nextjs" in stack.lower():
            stitch_instruction = f"""
====================================
STITCH AI — DESIGN GUIDANCE
====================================
{"- Hero: full-width gradient, headline left-aligned, product screenshot/visual right" if _is_landing else "- Dashboard: sidebar with icon nav, KPI cards in a 4-col grid, chart + table below"}
{"- Features: 3-column icon cards with hover elevation, subtle bg-muted backdrop" if _is_landing else "- Cards: compact padding (p-4), border border-border, shadow-sm, rounded-lg"}
Apply these spatial patterns with Tailwind + React JSX.
"""
        elif "vue" in stack.lower():
            stitch_instruction = """
====================================
STITCH AI — DESIGN GUIDANCE
====================================
- Sidebar: collapsed/expanded state, icon nav, dark bg-card, user profile bottom
- Dashboard: KPI cards row, recharts/vue-chartjs area chart, recent items table
Convert to Vue 3 SFC with Tailwind classes.
"""
        else:
            stitch_instruction = f"""
====================================
STITCH AI — DESIGN GUIDANCE
====================================
{"- Hero: large headline, subtext, dual CTA buttons, background gradient" if _is_landing else "- Sidebar: icon + label nav, collapsible groups, avatar bottom"}
{"- Features: icon cards, 3-column grid, hover shadow" if _is_landing else "- DataTable: sticky header, row actions dropdown, filter bar"}
Convert layout/spacing/card patterns to React JSX with Tailwind.
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

4. LAYOUT COMPONENTS — FULLY REWRITE Sidebar/Header/Footer for this project's unique design:
   - Sidebar: custom width, brand colors, logo area, nav groups, user profile area
   - Header: brand-specific, search bar style, notification bell, user avatar
   - Footer: project-specific columns, links, social icons
   (DO NOT copy template defaults — create a UNIQUE layout matching the research)

5. MAIN PAGE:
   - Landing: page.js that imports section components (sections come in Phase 2)
   - Admin: DashboardPage with KPI cards, charts (recharts/vue-chartjs), activity table

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
    # Types that get CRUD feature modules (admin-style)
    # vs landing page sections (marketing-style)
    CRUD_TYPES = {
        "admin_panel", "ecommerce", "saas_app", "analytics",
        "education", "medical", "fitness", "booking",
        "social", "food_restaurant", "travel", "real_estate",
    }
    # Landing types: landing_page, blog, entertainment, documentation, portfolio
    
    if app_type in CRUD_TYPES:
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

    phase3_prompt = f"""PHASE 3 OF 3 — ADDITIONAL PAGES + COMPLETENESS CHECK

The foundation and content are built (see file tree below). DO NOT regenerate existing files.

Generate:
1. ALL ADDITIONAL PAGES not yet created:
   - Landing: About (team, mission, stats), Pricing (expanded), Contact (form + info), Blog (listing)
   - Admin: Settings (profile/notifications/security tabs), Profile, Help/docs
   - Any other pages referenced in the schema or navigation

2. COMPLETENESS CHECK — verify EVERY schema page exists in the file tree:
   Schema-required pages:
{schema_pages_list}
   - For each page above: if the component file does NOT exist in the file tree → CREATE it
   - Any navigation items without corresponding pages → CREATE the page
   - Any imports referencing missing files → CREATE those files
   - If router has routes to pages that don't exist → CREATE those pages

3. CUSTOM COMPONENTS (if needed by any page):
   - Kanban board, calendar view, timeline, progress tracker
   - Any specialized component not in shadcn/ui → CREATE from scratch with Tailwind

4. IMPORT DESIGN SYSTEM — all new components must:
   - import {{ ds }} from '@/lib/design-system'
   - Use ds.card, ds.badge, ds.pageAnimation for consistency

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

