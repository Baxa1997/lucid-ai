"""Project generator v4 — Claude Opus 4.6 multi-call generation engine.

3-phase generation pipeline:
  Call 1: Foundation (theme, config, nav, layouts, main page, router)
  Call 2: Content (all sections OR all CRUD features)
  Call 3: Additional pages + completeness check

Uses Gemini 2.5 Pro (default, configurable via GEMINI_RESEARCH_MODEL) for
ultra-deep research and Claude Opus 4.6 for
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
DEFAULT_MODEL  = "claude-sonnet-4-6"   # 64K native output — no beta header needed
FALLBACK_MODEL = "claude-opus-4-7"    # fallback for edge cases
MAX_TOKENS_PER_CALL = 64000                # claude-sonnet-4-6 native max output
MAX_FIX_ATTEMPTS = 1

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
    # tailwind.config.js/ts MUST stay protected — no plugins needed.
    # Animation is provided by tw-animate-css (CSS @import, not a Tailwind plugin).
    # Color customization happens in globals.css via CSS variables (:root HSL values),
    # NOT in tailwind.config. That is the correct shadcn/ui pattern.
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

# src/components/ui/ contains pre-built shadcn/ui components from the GitHub template.
# Claude must NEVER overwrite these — they are already installed and tested.
# Generating replacements causes broken imports (@base-ui, @radix-ui not in template).
PROTECTED_UI_DIR = "src/components/ui"


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


# Keyword sets for scoring markdown sections by archetype relevance.
# Sections (## or ###) that contain one of the archetype keywords stay;
# sections that contain one of the other-archetype keywords but none of
# ours get dropped. Ambiguous sections (neither) stay by default — this
# conservatively keeps the universal bits (components/ui, package list).
_MANIFEST_KEYWORDS_ADMIN = (
    "admin", "sidebar", "dashboard", "datatable", "data table",
    "crud", "entity", "entities", "kpi", "chart",
)
_MANIFEST_KEYWORDS_LANDING = (
    "landing", "marketing", "hero", "cta",
    "(marketing)", "single-page", "landing page",
)
_MANIFEST_KEYWORDS_BLOG = (
    "blog", "article", "author", "post", "category",
    "editor", "comment", "documentation", "portfolio",
)


def _slice_manifest_for_archetype(
    manifest: str,
    layout_archetype: str,
    max_chars: int = 8000,
) -> str:
    """Return a manifest slice keeping only sections relevant to the archetype.

    Strategy:
      1. Split on markdown headings (##/###).
      2. Keep sections that match the target archetype's keywords.
      3. Drop sections that match a *competing* archetype's keywords but not ours.
      4. Always keep sections that match neither (component list, package list).
      5. Cap the result to `max_chars`.

    If slicing produces suspiciously little (< 20% of the raw manifest),
    fall back to the head-truncated raw manifest so the model still sees
    package/import information.
    """
    if not manifest:
        return ""

    if layout_archetype in ("single_page_landing", "landing_page"):
        own = _MANIFEST_KEYWORDS_LANDING
        other = _MANIFEST_KEYWORDS_ADMIN + _MANIFEST_KEYWORDS_BLOG
    elif layout_archetype in ("blog", "documentation", "portfolio"):
        own = _MANIFEST_KEYWORDS_BLOG
        other = _MANIFEST_KEYWORDS_ADMIN
    elif layout_archetype in (
        "admin_dashboard", "crm", "saas_app", "internal_tool",
    ):
        own = _MANIFEST_KEYWORDS_ADMIN
        other = _MANIFEST_KEYWORDS_LANDING + _MANIFEST_KEYWORDS_BLOG
    else:
        # Consumer / unknown — keep everything up to cap.
        return manifest[:max_chars]

    # Split on ## and ### headings, keeping the heading with each section.
    parts = re.split(r"(?m)^(?=#{2,3}\s)", manifest)
    kept: list[str] = []
    for chunk in parts:
        if not chunk.strip():
            continue
        low = chunk.lower()
        has_own = any(k in low for k in own)
        has_other = any(k in low for k in other)
        if has_own or not has_other:
            kept.append(chunk)

    sliced = "".join(kept).strip()

    # Safety net: if slicing gutted the manifest, fall back to head-truncated raw.
    if len(sliced) < max(800, len(manifest) // 5):
        return manifest[:max_chars]

    if len(sliced) > max_chars:
        sliced = sliced[:max_chars]
    return sliced


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
    # Generic admin/dashboard — used as fallback for admin archetypes
    "admin": [
        "skill_datatable.md",
        "skill_recharts.md",
        "skill_crud_module.md",
        "skill_framer_motion.md",
        "skill_forms.md",
    ],
    # Landing pages and single-page sites
    "landing": [
        "skill_landing_sections.md",
        "skill_framer_motion.md",
        "skill_recharts.md",
    ],
    # CRM and SaaS project management — kanban, pipeline, activity feeds
    "crm": [
        "skill_datatable.md",
        "skill_recharts.md",
        "skill_crud_module.md",
        "skill_forms.md",
        "skill_framer_motion.md",
    ],
    # E-commerce — product grids, order detail, analytics
    "ecommerce": [
        "skill_datatable.md",
        "skill_recharts.md",
        "skill_crud_module.md",
        "skill_forms.md",
        "skill_framer_motion.md",
    ],
    # Consumer / multi-page public websites (restaurants, clinics, law firms, etc.)
    "consumer": [
        "skill_landing_sections.md",
        "skill_framer_motion.md",
    ],
    # Blog / content sites
    "blog": [
        "skill_landing_sections.md",
        "skill_framer_motion.md",
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
    Routed by layout_archetype (specific) → app_type fallback (legacy).
    """
    # Archetype → skill bucket mapping (most specific first)
    _archetype_map = {
        "crm":              "crm",
        "saas_dashboard":   "crm",       # kanban + pipeline patterns apply
        "ecommerce":        "ecommerce",
        "admin_dashboard":  "admin",
        "tms":              "admin",
        "consumer_website": "consumer",
        "portfolio":        "consumer",
        "marketplace":      "consumer",
        "blog":             "blog",
        "single_page_landing": "landing",
    }
    if layout_archetype:
        skill_key = _archetype_map.get(layout_archetype, "admin")
    else:
        # Legacy fallback: admin types vs landing
        _admin_legacy = {
            "admin_panel", "ecommerce", "saas_app", "analytics",
            "education", "medical", "fitness", "booking",
            "social", "food_restaurant", "travel", "real_estate",
        }
        skill_key = "admin" if app_type in _admin_legacy else "landing"
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
# ║  HELPER — Extract design signal from raw Stitch HTML         ║
# ║  Converts a 15-40 KB HTML blob into a compact ~500-char JSON ║
def _salvage_partial_json(partial: str) -> Optional[dict]:
    """Try to extract complete file objects from a truncated JSON stream.

    When the stream is cut mid-way, we attempt to find all complete
    {"path": ..., "content": ...} objects and return them wrapped in a files list.
    """
    import re as _re
    files = []
    # Find all complete path+content pairs using a non-greedy regex
    for m in _re.finditer(
        r'\{\s*"path"\s*:\s*"([^"]+)"\s*,\s*"content"\s*:\s*"((?:[^"\\]|\\.)*)"\s*\}',
        partial,
        _re.DOTALL,
    ):
        path = m.group(1)
        raw = m.group(2)
        # Unescape JSON string escapes without corrupting non-ASCII (UTF-8) content.
        # unicode_escape codec treats bytes as Latin-1 and mangles multibyte sequences;
        # instead decode only the escape sequences that JSON uses.
        try:
            content = (
                raw
                .replace("\\n", "\n")
                .replace("\\t", "\t")
                .replace("\\r", "\r")
                .replace('\\"', '"')
                .replace("\\'", "'")
                .replace("\\\\", "\\")
            )
        except Exception:
            content = raw
        if path and content and len(content) > 10:
            files.append({"path": path, "content": content})
    if files:
        logger.info("_salvage_partial_json: recovered %d files from truncated stream", len(files))
        return {"files": files}
    return None


def _slim_prompt_for_retry(prompt: str) -> tuple[str, bool]:
    """Shrink a phase prompt for a single truncation retry.

    Targets the three biggest optional padding blocks that the phase-1/2/3
    prompt builders embed, in order:
      - "DESIGN SYSTEM FROM RESEARCH:" → keep first ~6K chars of research
      - "TEMPLATE MANIFEST:"           → keep first ~4K chars
      - "CURRENT FILE TREE ...:"       → keep first ~1K chars
    All other content (instruction, schema spec, stitch_instruction) is
    load-bearing and left intact.

    Returns (slimmed_prompt, actually_reduced). `actually_reduced=False`
    when the prompt doesn't contain any of the target markers (e.g. the
    schema-build caller) — the caller should then bail as before rather
    than retry with an identical prompt.
    """
    if not prompt:
        return prompt, False

    slimmed = prompt
    original_len = len(slimmed)

    # 1. Cap research block (between DESIGN SYSTEM FROM RESEARCH and TEMPLATE MANIFEST)
    slimmed = re.sub(
        r"(DESIGN SYSTEM FROM RESEARCH:\s*\n)(.*?)(\n\s*TEMPLATE MANIFEST:)",
        lambda m: m.group(1) + m.group(2)[:6000] + m.group(3),
        slimmed,
        count=1,
        flags=re.DOTALL,
    )

    # 2. Cap template manifest block (between TEMPLATE MANIFEST and CURRENT FILE TREE)
    slimmed = re.sub(
        r"(TEMPLATE MANIFEST:\s*\n)(.*?)(\n\s*CURRENT FILE TREE)",
        lambda m: m.group(1) + m.group(2)[:4000] + m.group(3),
        slimmed,
        count=1,
        flags=re.DOTALL,
    )

    # 3. Cap file tree block (between CURRENT FILE TREE header and the next blank line / stack rules)
    slimmed = re.sub(
        r"(CURRENT FILE TREE[^\n]*:\s*\n)(.*?)(\n{2,})",
        lambda m: m.group(1) + m.group(2)[:1000] + m.group(3),
        slimmed,
        count=1,
        flags=re.DOTALL,
    )

    return slimmed, len(slimmed) < original_len


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
    extended_output: bool = False,
) -> Optional[dict]:
    """Call Claude Messages API and return parsed JSON dict.

    Uses Claude's native tool_use for guaranteed valid JSON output.
    The API enforces JSON schema automatically — no escaping issues.
    On failure with Opus, automatically retries with Sonnet as fallback.

    extended_output=True adds the output-128k beta header, allowing up to
    64K output tokens for complex admin/CRM projects with many pages/entities.
    """
    import httpx

    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }
    # claude-sonnet-4-6 has 64K native output — no beta header needed.
    # The output-128k-2025-02-19 beta was for older models (3.5/3.7 sonnet) only.

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
        "temperature": 0.3,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_prompt}],
        "tools": [write_files_tool],
        "tool_choice": {"type": "tool", "name": "write_project_files"},
    }

    _last_stop_reason: list[str] = [""]  # mutable container so inner fn can write it

    async def _make_request(use_model: str) -> Optional[dict]:
        payload["model"] = use_model
        # Use streaming so:
        # 1. First token arrives in <5s instead of waiting for the full response
        # 2. We send heartbeat progress updates so the user isn't staring at a blank screen
        # 3. We avoid httpx read-timeout killing a slow but valid generation
        stream_payload = {**payload, "stream": True}
        try:
            raw_chunks: list[str] = []
            last_heartbeat = 0.0
            import time as _time

            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=600.0)) as client:
                async with client.stream(
                    "POST", CLAUDE_API_URL, headers=headers, json=stream_payload
                ) as response:
                    if response.status_code != 200:
                        error_text = await response.aread()
                        error_text = error_text.decode()[:500]
                        logger.error(
                            "Claude API error %d with %s: %s",
                            response.status_code, use_model, error_text,
                        )
                        if response.status_code in (401, 403):
                            await _ws_send(websocket, "error", "❌ Anthropic API key is invalid.")
                        elif response.status_code in (400, 402) and "credit" in error_text.lower():
                            await _ws_send(websocket, "error", "❌ Anthropic API credits depleted.")
                        elif response.status_code == 429:
                            await _ws_send(websocket, "error", "⚠️ Anthropic rate limit hit. Retrying...")
                        elif response.status_code == 529:
                            await _ws_send(websocket, "error", "⚠️ Anthropic API overloaded. Retrying...")
                        else:
                            await _ws_send(websocket, "error", f"❌ Claude API error ({response.status_code}).")
                        return None

                    async for line in response.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        raw_chunks.append(line[6:])
                        # Heartbeat every 15s so the user sees progress
                        now = _time.monotonic()
                        if now - last_heartbeat > 15:
                            last_heartbeat = now
                            try:
                                await websocket.send_json({"type": "progress", "message": "⏳ Writing files..."})
                            except Exception:
                                pass

            # Reconstruct full response from SSE stream
            response_data: dict = {}
            tool_input_parts: list[str] = []
            stop_reason = ""
            for chunk_str in raw_chunks:
                if chunk_str == "[DONE]":
                    break
                try:
                    chunk = json.loads(chunk_str)
                except Exception:
                    continue
                ctype = chunk.get("type", "")
                if ctype == "message_start":
                    pass
                elif ctype == "content_block_delta":
                    delta = chunk.get("delta", {})
                    if delta.get("type") == "input_json_delta":
                        tool_input_parts.append(delta.get("partial_json", ""))
                elif ctype == "message_delta":
                    stop_reason = chunk.get("delta", {}).get("stop_reason", "")

            _last_stop_reason[0] = stop_reason
            is_truncated = stop_reason == "max_tokens"
            if is_truncated:
                logger.warning("Claude (%s) response truncated (max_tokens). Attempting salvage...", use_model)
                await _ws_send(websocket, "progress", "⚠️ Response was long — salvaging complete files...")

            # Parse the assembled tool_use input JSON
            full_input = "".join(tool_input_parts)
            if full_input:
                try:
                    result = json.loads(full_input)
                    if isinstance(result, dict) and result.get("files"):
                        files = result["files"]
                        if not isinstance(files, list):
                            logger.warning(
                                "Claude returned 'files' as %s not list — skipping truncation recovery",
                                type(files).__name__,
                            )
                            return None
                        if is_truncated and len(files) > 1:
                            last_entry = files[-1]
                            last_content = last_entry.get("content", "") if isinstance(last_entry, dict) else ""
                            if (
                                len(last_content) < 50
                                or not last_content.rstrip().endswith((";", "}", ">", ");", "/>", "*/", "\n"))
                            ):
                                dropped = files.pop()
                                logger.warning(
                                    "Truncation recovery: dropped incomplete file '%s'",
                                    dropped.get("path", "?") if isinstance(dropped, dict) else "?",
                                )
                        logger.info("Claude (%s) returned %d files via stream", use_model, len(files))
                        return {"files": files}
                except json.JSONDecodeError:
                    # Truncated JSON — try salvage
                    logger.warning("Truncated JSON from stream — attempting partial salvage")
                    salvaged = _salvage_partial_json(full_input)
                    if salvaged and salvaged.get("files"):
                        logger.info("Salvaged %d files from truncated stream", len(salvaged["files"]))
                        return salvaged

            logger.error("Claude stream had no tool_use input (model=%s, stop=%s)", use_model, stop_reason)
            # Send actual error details to help debug
            await _ws_send(websocket, "warning", f"⚠️ Phase returned empty response (stop={stop_reason}). Retrying...")
            return None

        except httpx.TimeoutException as te:
            logger.error("Claude API stream timeout (%s): %s", use_model, te)
            await _ws_send(websocket, "error", "❌ Claude API timed out. Check your connection and try again.")
            return None
        except Exception as exc:
            logger.error("Claude API call failed (%s): %s", use_model, exc)
            return None


    # Try with primary model
    result = await _make_request(model)
    if result:
        return result

    # If the primary model hit the token limit, try ONCE more with a slimmed
    # prompt (same model — truncation means input+output exceeded the output
    # ceiling; a weaker/stronger model won't fix that, only less input will).
    # The slimmer drops manifest/research/file-tree padding while keeping the
    # load-bearing instruction + schema + stitch blocks. If the prompt has
    # none of those markers (e.g. schema-build caller), we bail as before.
    if _last_stop_reason[0] == "max_tokens":
        slimmed_prompt, was_reduced = _slim_prompt_for_retry(user_prompt)
        if was_reduced:
            logger.warning(
                "Primary model truncated — retrying with slimmed prompt (%d → %d chars)",
                len(user_prompt), len(slimmed_prompt),
            )
            await _ws_send(
                websocket,
                "progress",
                "⚠️ Response was too large — retrying with a tighter prompt...",
            )
            payload["messages"] = [{"role": "user", "content": slimmed_prompt}]
            result = await _make_request(model)
            if result:
                return result
            logger.warning("Slimmed retry also truncated — accepting partial output")
        else:
            logger.warning("Primary model truncated but prompt has no slimmable markers — bailing")
        await _ws_send(websocket, "warning", "⚠️ Generation was too large — proceeding with partial output...")
        return None

    # Fallback to Opus if Sonnet failed for a non-truncation reason
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

    if not isinstance(files, list):
        logger.warning("write_files_from_json: 'files' is %s not a list, skipping", type(files).__name__)
        return []

    written = []

    for entry in files:
        if not isinstance(entry, dict):
            continue
        rel_path = entry.get("path", "")
        if not isinstance(rel_path, str):
            continue
        rel_path = rel_path.strip()

        content = entry.get("content", "")
        # Claude almost always returns content as a string, but occasionally
        # emits a dict/list (e.g. JSON-object content for config files) or a
        # number. Coerce to a string rather than crashing .write(content).
        if not isinstance(content, str):
            if isinstance(content, (dict, list)):
                try:
                    content = json.dumps(content, indent=2, ensure_ascii=False)
                except (TypeError, ValueError):
                    logger.warning("Unserializable non-string content for %s — skipping", rel_path)
                    continue
            elif content is None:
                continue
            else:
                content = str(content)

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

        # Protect pre-built shadcn/ui components — never let Claude overwrite them
        norm = rel_path.replace("\\", "/")
        if norm.startswith("./"):
            norm = norm[2:]
        if norm.startswith(PROTECTED_UI_DIR + "/") or norm == PROTECTED_UI_DIR:
            logger.info("Skipping protected UI component: %s", rel_path)
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


# Per-section caps for research distillation — tuned so the sum stays under ~8K.
# Order matters: the most actionable bits (palette, typography, pages, entities)
# come first so if the model only reads the head of the block, it still gets
# the highest-signal content.
_DISTILL_SECTIONS: tuple[tuple[str, int], ...] = (
    ("DESIGN_SYSTEM_NAME", 100),
    ("CLASSIFICATION", 300),
    ("DOMAIN", 250),
    ("VIBE", 300),
    ("PALETTE", 700),
    ("TYPOGRAPHY", 400),
    # Director blocks — always keep. BRAND_MARK guarantees the logo is built.
    # RADIUS_TOKENS locks border-radius consistency across elements.
    # IMAGE_COMPOSITION prevents low-contrast text-over-image + glass forms.
    ("BRAND_MARK", 400),
    ("RADIUS_TOKENS", 300),
    ("IMAGE_COMPOSITION", 700),
    ("COPY_TONE", 500),
    # Vision-grounded DNA from actual screenshots of reference sites.
    # Placed BEFORE LAYOUT_BLUEPRINT so if the model truncates, the concrete
    # visual observations survive — Claude leans on them heavily for hero
    # composition, card language, and motion cues.
    ("VISUAL_DNA", 1800),
    ("LAYOUT_BLUEPRINT", 3500),  # Design DNA: 20 creative variables per project (hero/features/rhythm/motif/mood/cards/type/motion/pattern/radius/color/hover/spacing)
    # Admin/CRM/TMS visual language — table/form/sidebar/status/density recipe.
    # Only present when layout_archetype is admin-family.
    ("ADMIN_UI_LANGUAGE", 2200),
    ("PAGES", 1400),
    ("SECTIONS", 1400),
    ("ENTITIES", 1200),
    ("KEY_COMPONENTS", 1400),
    ("DOMAIN_MUST_HAVES", 900),
    ("UI_PATTERNS", 500),
)


def _distill_research(research: str, max_total: int = 12000) -> str:
    """Compress the raw Gemini research dump into a compact bullet plan.

    The raw research is typically 10–20K chars with many ===SECTION=== blocks
    (and often long prose inside each). This helper:
      - Picks out the sections known to be load-bearing for UI generation.
      - Truncates each to a per-section cap tuned to keep the total under 8K.
      - Re-emits the same ===HEADER=== markers so downstream extractors still
        find what they need.

    If research is already short or lacks ===HEADERS=== (fallback path),
    return it head-truncated. This keeps the function total over robust input.
    """
    if not research:
        return ""

    # No structured blocks → just head-truncate.
    if "===" not in research:
        return research[:max_total]

    parts: list[str] = []
    total = 0
    for name, cap in _DISTILL_SECTIONS:
        body = _extract_research_section(research, f"==={name}===", max_chars=cap)
        if not body:
            continue
        if len(body) > cap:
            body = body[:cap].rstrip() + "…"
        block = f"==={name}===\n{body.strip()}\n"
        if total + len(block) > max_total:
            # Leave room for at least the header of the cut block so callers
            # can tell it existed.
            remaining = max_total - total - len(f"==={name}===\n…\n")
            if remaining > 200:
                parts.append(f"==={name}===\n{body.strip()[:remaining]}…\n")
            break
        parts.append(block)
        total += len(block)

    distilled = "\n".join(parts).strip()
    return distilled or research[:max_total]


def _extract_layout_archetype(research: str, fallback_classification: dict) -> dict:
    """Extract the confirmed layout archetype from Gemini's ===CLASSIFICATION=== section.

    Gemini may refine the initial classification after research. This reads its
    decision and builds an updated classification dict — subject to two constraints:

    1. classification_locked=True means the initial classification came from an explicit
       user keyword (e.g. "landing page", "CRM") and must not be overridden.
    2. Gemini may only refine WITHIN the same structural family
       (single / consumer / admin). Cross-family changes are rejected.
    """
    from knowledge.loader import LAYOUT_ARCHETYPES, _build_rich_classification, get_structural_family

    # If the initial classification was locked by keyword matching, honour the user's intent.
    if fallback_classification.get("classification_locked"):
        logger.info(
            "Classification locked at %s — ignoring Gemini research override",
            fallback_classification["layout_archetype"],
        )
        return fallback_classification

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

    # Reject cross-family changes — Gemini can refine within a family
    # (e.g. admin_dashboard → crm) but cannot cross structural boundaries.
    initial_family = get_structural_family(fallback_classification["layout_archetype"])
    proposed_family = get_structural_family(layout)
    if initial_family != proposed_family:
        logger.warning(
            "Gemini tried to cross structural family boundary %s→%s (%s→%s) — keeping initial",
            initial_family, proposed_family,
            fallback_classification["layout_archetype"], layout,
        )
        return fallback_classification

    result = _build_rich_classification(layout, domain)
    logger.info("Post-research classification: layout=%s domain=%s", layout, domain)
    return result


# ╔══════════════════════════════════════════════════════════════╗
# ║  STEP 3 — gemini_deep_research()                            ║
# ║  Ultra-deep product research via Gemini with internet search ║
# ╚══════════════════════════════════════════════════════════════╝

# Limit concurrent Gemini research calls to avoid rate-limit 429s.
# Gemini 2.5 Pro paid-tier quotas are tighter than Flash (typically 2-5 RPM
# depending on billing plan). Keep the semaphore at 2 to be safe; existing
# retry/backoff logic below handles occasional 429s gracefully.
_gemini_semaphore = asyncio.Semaphore(2)

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
    is_locked = classification.get("classification_locked", False)

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

{"⚠️ LOCKED: The user explicitly requested this layout type. Output layout_archetype EXACTLY as shown above — do NOT change it." if is_locked else "(Correct the layout_archetype line above ONLY if clearly wrong — keep others matching)"}

═══════════════════════════════════════════════════════════════
STEP 2 — INTERNET RESEARCH (MUST use google_search grounding)
═══════════════════════════════════════════════════════════════

YOU HAVE google_search AVAILABLE. USE IT. Do NOT rely on training memory —
training data is 2023-era and will produce dated design. The goal of this step
is to anchor the design in ACTUAL 2024-2025 reality.

Run AT LEAST these searches before answering:
  1. "awwwards {domain} site of the year 2024"
  2. "awwwards {domain} site of the year 2025"
  3. "best {domain} website design 2025"
  4. "{domain} landing page inspiration godly.website"
  5. "{domain} landing page inspiration siteinspire.com"
  6. "2025 web design trends"
  7. (if "{description}" names a specific brand) "{description}" official site

For EACH site you discover, note: URL, what makes its design distinctive in 2025
(not generic praise), and 2-3 concrete patterns you will borrow (layout,
typography, motion, color, decoration).

If "{description}" names a REAL brand → study THAT site FIRST as primary reference.

===SITES_ANALYZED===
1. [Name] ([URL]) — [specific 2025-distinctive design moves: layout + typography + motion]
2. [Name] ([URL]) — [specific 2025-distinctive design moves]
3. [Name] ([URL]) — [specific 2025-distinctive design moves]
4. [Name] ([URL]) — [specific 2025-distinctive design moves]
5. [Name] ([URL]) — [specific 2025-distinctive design moves]

═══════════════════════════════════════════════════════════════
STEP 2b — DESIGN ERA CALIBRATION (forbidden vs. required patterns)
═══════════════════════════════════════════════════════════════

The user has explicitly rejected "generic template" output. To avoid it, you
MUST reject dated 2020-2022 patterns AND include at least THREE 2024-2025
moves in your blueprint.

FORBIDDEN 2020-2022 patterns (produce dated / template output — never output these):
  ✗ Flat pastel gradient hero with centered text stack
  ✗ Symmetric 3-column feature grid with icon-over-title-over-description
  ✗ Generic rounded-xl cards with small shadow and nothing else distinctive
  ✗ "From $X/month" pricing cards all identical shape
  ✗ Stock photos of smiling office workers / diverse-team-around-laptop
  ✗ Hero H1 with two dead-centered CTAs and no imagery breaking the grid
  ✗ Hero with bg-background/95 washing out a photo (use dark gradient overlays)
  ✗ Nav with "About / Features / Pricing / Sign In / Get Started" on a non-SaaS site
  ✗ Map sections showing only a giant MapPin icon (use real photos or iframe)

REQUIRED 2024-2025 moves (blueprint MUST include at least 3 of these):
  ✓ Bento grid somewhere (hero or features) — inspired by Apple iOS/iPadOS
  ✓ Typographic statement: oversized H1 (text-[clamp(3rem,10vw,9rem)]) or
    variable-weight / italic serif (Fraunces, Editorial New, Migra, PP Editorial)
  ✓ At least one asymmetric section (offset grid, editorial magazine layout)
  ✓ Signature motif (dot-grid, grain, hand-drawn squiggle, floating orbs, topographic)
  ✓ Depth via contrast — at least one dramatically inverted section
    (bg-foreground text-background, or full-bleed dark with cinematic photo)
  ✓ Intentional motion — stagger-fade-up on scroll, spring tilts on hover,
    OR minimal-no-scroll with premium lift + shadow (not random random animations)
  ✓ Organic / asymmetric border-radius somewhere (or commit to sharp brutalist —
    but NOT "rounded-xl for everything")
  ✓ Duotone or filtered photography (not raw stock), cinematic crop ratios
  ✓ Kinetic / scroll-driven reveals (CSS animation-timeline or Framer useScroll)
  ✓ Micro-interactions: magnetic CTAs, revealing secondary content on hover,
    morph-shape on hover

In your LAYOUT_BLUEPRINT output later, the Design DNA variables
(hero_archetype, features_archetype, card_language, motion_language,
decorative_pattern, border_radius_language, color_application_strategy) MUST
reflect the 2024-2025 moves above — not defaults pulled from memory.

===ERA_CALIBRATION===
moves_borrowed: [List the 3+ 2024-2025 moves you will use and WHY — 1 line each]
patterns_avoided: [List the 2-3 dated patterns you could have used but rejected]

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
nav_items: [4-6 DOMAIN-APPROPRIATE anchor labels — NOT the generic SaaS stack]

  CRITICAL: Pick nav labels the real top sites in THIS domain actually use.
  Study bluebottlecoffee.com, noma.dk, equinox.com, airbnb.com, tesla.com etc. for
  their domain — copy their navigation vocabulary, not a SaaS template.

  Domain-specific examples (use the pattern, not the exact list):
    Coffee / café        → Our Coffee, Menu, Locations, Subscription, Our Story, Wholesale
    Restaurant           → Menu, Reservations, Private Events, Locations, About, Press
    Bakery / pâtisserie  → Menu, Order Online, Custom Cakes, Visit, Our Story
    Bar / cocktail       → Drinks, Events, Reservations, Visit, About
    Fitness / gym        → Classes, Trainers, Schedule, Membership, Locations, Community
    Yoga / pilates       → Classes, Teachers, Schedule, Retreats, Pricing
    Salon / barber       → Services, Book Now, Stylists, Locations, Gift Cards
    Spa / wellness       → Treatments, Book Now, Memberships, Facilities, Our Story
    Hotel / travel       → Rooms, Experiences, Dining, Location, Offers, Book
    Airbnb-style rental  → Listings, Experiences, Hosts, Trust & Safety, Help
    Real estate          → Buy, Sell, Rent, Neighborhoods, Agents, Market Insights
    Automotive dealer    → Inventory, New, Pre-owned, Finance, Service, About
    Wedding / event      → Packages, Venue, Gallery, Pricing, Contact
    Pet / vet            → Services, Book Visit, Our Team, Shop, Resources
    Portfolio / agency   → Work, Services, Process, About, Journal, Contact
    Blog / magazine      → Latest, Topics, Newsletter, Authors, About
    Nonprofit            → Mission, Programs, Impact, Get Involved, Donate
    B2B SaaS (only!)     → Features, How It Works, Pricing, Docs, Changelog, Log In

  HARD BAN for non-SaaS domains (coffee, restaurant, fitness, hotel, salon, spa,
  retail, wedding, pet, real estate, automotive, event, portfolio, nonprofit):
    ✗ NEVER output "Features", "Pricing", "How It Works", "Sign In", "Log In",
      "Get Started", "Dashboard", "Integrations", "Changelog", "API".
    These are SaaS-tool vocabulary and look like a template on a physical business.

  ADDITIONAL RULES:
    - Each label 1-3 words, Title Case.
    - First item is usually the primary content (Menu / Rooms / Classes / Work).
    - Last item is usually a CTA-adjacent action (Book Now / Reservations / Visit).

cta: "[CTA text — use the domain-appropriate verb: Reserve a Table / Book a Class
       / Order Now / Plan Your Stay / View Menu / Book Appointment. For SaaS only:
       Start Free / Get Demo / Try It Free]"
     | classes: [Tailwind button classes]
sticky: yes | blur_bg: yes

===SECTIONS===
Invent the section list that THIS domain actually needs — do NOT default to the
generic "hero / features / pricing / faq / cta" stack. Study what real top
{domain} sites put on their landing page and pick 7-12 sections that flow in a
domain-appropriate order. The list below is a menu of POSSIBLE sections; pick
what fits this domain, skip what doesn't, and INVENT sections unique to the
domain if needed.

Possible section types (not all apply — pick what THIS domain needs):
  Universal: hero, social_proof (logos / ratings / user count), cta_final, footer
  Product/SaaS: features, how_it_works, integrations, pricing, faq, changelog
  Coffee/Restaurant/Bakery: menu, story, chef_bio, hours, locations, gallery, reservations, press_mentions
  Fitness/Gym: classes_schedule, trainers, membership_tiers, transformations, facilities_tour
  Salon/Spa: services_menu, practitioners, booking, gift_cards, before_after_gallery
  Hotel/Travel: rooms_showcase, amenities, nearby_attractions, booking_widget, reviews_from_agoda
  Real estate: featured_listings, agents, neighborhoods, recent_sales, mortgage_calculator
  Portfolio: selected_work, case_study_preview, about, clients_list, awards, process
  Blog/Content: latest_posts, categories, featured_author, newsletter_signup, trending_topics
  Event/Wedding: venue_gallery, packages, couple_story, guest_book, rsvp
  INVENT NEW ones if THIS domain calls for it (e.g. "coffee_of_the_month_feature",
  "live_cam_of_the_roastery", "farm_partners_map", "seasonal_ritual_calendar").

For EACH section you pick, specify:

[section: <your_section_name>]
headline: "[ORIGINAL copy specific to the domain — not placeholder]"
subheadline: "[1-2 supporting sentences with real specificity — if applicable]"
layout: [describe in 1 sentence — e.g. "bento 3×2 with the center tile featuring
         a latte close-up and a floating '4.9★' stat card, surrounded by 5
         small feature cards with Lucide icons"]
background: [describe in research Tailwind-friendly terms — e.g. "bg-muted/40
             with a faint dot-matrix SVG pattern at 4% opacity behind H2"]
imagery: [what photos/icons/illustrations appear and where]
content: [actual items/rows/cards — real domain-specific copy, no lorem ipsum]
animation: [how content enters as user scrolls]

Hero MUST match the hero_description from LAYOUT_BLUEPRINT. Other sections MUST
respect the section_rhythm mapping from LAYOUT_BLUEPRINT.

IMPORTANT: section ORDER matters and should be chosen for THIS domain's conversion
psychology — a coffee shop leads with ambiance+menu, not "features + pricing".
A B2B SaaS leads with problem-solution + social proof, not "menu".

===FOOTER===
columns: [3-4 columns with domain-appropriate links]
bottom: "[copyright + tagline]"

[OUTPUT THIS BLOCK IF layout_archetype = consumer_website OR marketplace OR portfolio OR blog]
===HEADER===
logo: [brand name]
nav_items: [5-7 DOMAIN-APPROPRIATE page labels from research — Title Case, 1-3 words each]

  CRITICAL: Use labels the real top sites in THIS domain actually use.
  HARD BAN on non-SaaS domains: NEVER output "Features", "Pricing",
  "How It Works", "Sign In", "Log In", "Get Started", "Dashboard",
  "Integrations", "Changelog" on a physical/consumer/portfolio site.

  Pattern examples (copy the vocabulary, not the exact list):
    Restaurant chain → Menu, Locations, Reservations, Private Events, Gift Cards, About
    Coffee roaster   → Our Coffee, Subscription, Wholesale, Cafés, Our Story, Blog
    Hotel            → Rooms, Experiences, Dining, Spa, Location, Offers, Book
    Real estate      → Buy, Sell, Rent, Agents, Neighborhoods, Insights
    Agency/portfolio → Work, Services, Process, About, Journal, Contact
    Magazine/blog    → Latest, Topics, Newsletter, Authors, About, Shop

cta: "[optional primary CTA text — domain verb: Reserve / Book Now / Visit / Order / Donate]"
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

═══════════════════════════════════════════════════════════════
STEP 6 — LAYOUT BLUEPRINT (landing / consumer-website / portfolio / blog / marketplace / ecommerce ONLY)
═══════════════════════════════════════════════════════════════
This block is your CREATIVE DIRECTION for THIS specific project. You are acting
as the creative director of an award-winning design studio. Your job is to
DESCRIBE — in your own words — the visual design of this site.

MANDATORY BEHAVIOR:
  • Describe each field in 2-4 specific sentences — NOT a one-word pick
  • Name at least ONE real award-winning site you're drawing inspiration from
    (candidates: linear.app, stripe.com, vercel.com, arc.net, framer.com,
     apple.com/vision-pro, ramp.com, retool.com, openai.com, pitch.com,
     notion.so, rauchg.com, brusselsmuseums.be, awwwards.com featured sites,
     dribbble.com popular, siteinspire.com, or a real brand website in the
     exact domain of THIS project you found via search)
  • If the inspiration is generic (e.g. "a coffee shop website"), that's a
    FAILURE — name a SPECIFIC real site (e.g. bluebottlecoffee.com, verve.coffee,
    stumptowncoffee.com) and say what about IT you're borrowing.
  • INVENT new patterns when the domain warrants it — the lists below are
    examples to spark ideas, NOT a menu you must pick from.
  • NOVELTY COMMITMENT: imagine 3 other designers each designed this same
    project. Your output must be visibly different from the "safe default"
    a tired designer would produce.

SKIP this entire ===LAYOUT_BLUEPRINT=== block ONLY if layout_archetype is
admin_dashboard / crm / tms / saas_dashboard (fixed sidebar layouts).

===LAYOUT_BLUEPRINT===

hero_description:
  [2-4 sentences describing the hero layout in concrete visual detail.
   Cover: spatial arrangement (where text sits, where imagery sits, asymmetry),
   imagery treatment (single photo / collage / video / illustration / product mock),
   decorative elements (floating cards, badges, scrolling ticker, geometric shapes),
   and how the eye should travel. Be specific — "a diagonal split with a tilted
   polaroid of a latte overlapping a warm beige backdrop, a handwritten 'since 2014'
   label in the upper-right corner, and three tiny circular customer avatars
   pinned to the lower-left of the image" beats "split-screen with image".]
  inspiration_site: [real URL Gemini can name, e.g. "bluebottlecoffee.com" or "linear.app"]
  why_this_fits: [1 sentence linking the choice to the domain + target user]

features_description:
  [2-4 sentences describing how the key features/benefits/menu/services section
   is laid out. If bento — say which card is hero and what it contains. If
   alternating rows — say which images go left, which right. If timeline —
   say what the steps are. Include any decorative motif used within this section.
   Must be DIFFERENT spatially from hero_description (no same grid used twice).]
  inspiration_site: [real URL]

secondary_sections_description:
  [For EACH remaining section (testimonials, gallery, story, pricing, menu,
   locations, cta — whatever this project needs): 1-2 sentences on the layout.
   Include at least one "section that surprises" — e.g. a full-bleed quote
   with a photo backdrop, a horizontally scrolling marquee of product cards,
   a playable video testimonial. Format as "section_name: description".]

section_rhythm:
  [List every section in order, mapping each to a background treatment.
   At most 2 consecutive sections may share the same treatment.
   Treatments are OPEN-ENDED — describe them in 3-6 words each.
   Example: "hero=soft cream-to-blush gradient mesh with grain texture,
   features=crisp bg-background with grid-pattern behind H2, story=full-bleed
   cafe interior photo with bg-background/80 overlay, testimonials=deep-espresso
   bg-primary with white text, locations=bg-muted with embedded maps,
   cta=bg-gradient-to-br primary→accent with noise overlay."]

signature_motif:
  [Describe ONE repeated decorative element that appears 2-3 times across the
   page and gives it a consistent visual signature. Be specific about shape,
   color, opacity, and placement. Examples: "Faint 1px grid pattern at 4%
   opacity behind hero + CTA", "Three blurred amber orbs bottom-right of hero
   and top-left of testimonials", "Hand-drawn coffee-cup-ring stamps at 15%
   opacity as section dividers". Invent a motif that fits the domain personality.
   Say "none" ONLY for brutalist-minimal moods where typography alone carries
   the design.]

design_mood:
  [2-3 sentence description of the overall visual personality. Include: the
   ONE-WORD adjective that best captures it (editorial / luxe / brutalist /
   retro / techy / playful / organic / classic / cinematic — or invent your own),
   how that personality translates to type treatment, card shape, and color
   use. Must be internally coherent with the palette + fonts picked above.]
  reasoning: [1 sentence citing a specific research finding or target-user insight]

hero_image_url:
  [ONE exact Unsplash photo URL for the hero, with query string
   ?auto=format&fit=crop&w=1600&q=80. Pick a photo that PERFECTLY matches the
   domain and the hero_description (not a generic stock photo). Format:
   https://images.unsplash.com/photo-PHOTO_ID?auto=format&fit=crop&w=1600&q=80]

supporting_image_urls:
  [Provide 3-6 more Unsplash URLs for other sections. Format each as:
   "section_name: https://images.unsplash.com/photo-PHOTO_ID?auto=format&fit=crop&w=1200&q=80"
   e.g. "menu_item_1: ..., menu_item_2: ..., about_story: ..., location_exterior: ..."]

accent_detail:
  [Describe ONE signature micro-detail that makes this page feel hand-crafted.
   Something a tired designer would skip. Examples:
     - "A tiny pulsing green dot + 'Open now — closes 9 PM' pill in the top-right
        of the hero, positioned over the image corner with z-20."
     - "Three overlapping customer avatars with a 4.9★ rating and
        '2,400+ morning regulars' caption, floating bottom-left of hero image."
     - "A rotating word under the main H1 that cycles through three synonyms
        every 3 seconds with a fade transition."
     - "A scribbled hand-drawn arrow (SVG inline) pointing from the H1 to the
        primary CTA button, slight rotation, accent color."
   Invent a detail that specifically fits THIS domain. Generic details = failure.]
  placement: [exact placement — "absolute -bottom-4 -left-4 z-20" style]

# ═══════════════════════════════════════════════════════════════
# DESIGN DNA — 10 variables that control this project's unique visual language.
# These force Claude away from its defaults. Every variable must be specific,
# concrete, and DIFFERENT from what a generic site would ship.
# ═══════════════════════════════════════════════════════════════

hero_archetype:
  [Pick ONE and describe. Do NOT default to "split-screen":
     - split                 → text left, image right (classic, overused — only pick if domain demands)
     - bento                 → grid of 4-6 tiles, one hero tile dominant, text occupies 1-2 tiles
     - diagonal              → diagonal split line between text half and image half (use clip-path or skew)
     - magazine              → giant oversized H1 as editorial headline, tiny image column offset
     - layered-scroll        → three stacked layers with parallax scroll offsets
     - cinematic-parallax    → full-bleed video or image with slow pan, text emerges on scroll
     - editorial-offset      → asymmetric: H1 anchored top-left, image floats bottom-right with margin bleed
     - full-bleed-dark       → dark cinematic image fills viewport, text overlays with cinematic gradient
     - product-showcase      → product/device mockup centerpiece, text as supporting caption
     - typographic-hero      → massive text-only hero with kinetic type, no photo (only for fashion/editorial)
     - INVENT one if domain calls for it (e.g. "stacked-polaroids" for photography portfolio)]
  reasoning: [1 sentence why THIS archetype fits the domain mood]

features_archetype:
  [Pick ONE — must be DIFFERENT from hero spatial pattern:
     - bento-mixed          → 2x3 grid of mixed sizes, hero tile 2x-width with image
     - zigzag-split         → alternating image-left / image-right rows (3-4 rows)
     - vertical-tabs        → sticky tab list left, content panel right, click to switch
     - horizontal-scroll    → horizontal marquee or snap-scroll cards
     - masonry              → pinterest-style staggered columns, variable heights
     - tilt-stack           → stacked cards each rotated slightly, offset, on scroll they straighten
     - interactive-showcase → big primary feature visualization + 4 clickable thumbnails below
     - timeline             → vertical timeline with alternating sides, connecting line
     - comparison-grid      → 3-column comparison (not pricing — feature tiers)
     - numbered-editorial   → giant numbered steps (01, 02, 03) with editorial typography
     - INVENT if domain calls for it]
  reasoning: [1 sentence]

card_language:
  [Describe this project's unique card visual treatment in 1-2 sentences.
   Go BEYOND generic "rounded-xl shadow-md". Examples:
     - "Thick 2px charcoal borders with no shadow, 90° corners — brutalist editorial."
     - "Soft cream cards with a single hairline inner border and wax-seal corner emblem."
     - "Glassmorphic cards with 20px backdrop-blur, white/8 border, soft primary glow ring."
     - "Paper-folded cards — subtle inner fold crease via linear-gradient, slight shadow offset up."
     - "Organic blob-shaped cards with asymmetric border-radius (30% 70% 40% 60%), no shadow."
     - "Card + image sit in a shared torn-paper container with a deckle-edge SVG mask."
   MUST include: radius style, border style, shadow/glow treatment, any decorative micro-element.]

typography_pairing:
  [Specify the heading font + body font (different from each other) AND their mixing rules.
   Must be real Google Fonts. Examples:
     - "Heading: Fraunces (serif, weight 600-900, tight -0.03 tracking, can be set in italic).
        Body: Inter (400-500, 0 tracking). H1 in oversized 80px+, all-caps variant on section labels
        with widely-spaced 0.3em tracking."
     - "Heading: Space Grotesk (500-700). Body: IBM Plex Mono for captions, Inter for paragraphs.
        Use mono for stat labels and handwriting-style decorative accents."
     - "Heading: Playfair Display in italic for all H1s only, Manrope 700 for other headings.
        Body: Manrope 400. Contrast is built from italic-serif vs geometric-sans."
   Include: heading font name, body font name, weight usage, tracking rules, italic/caps treatment.]

motion_language:
  [Describe how content enters and reacts. Pick 2-3 complementary behaviors:
     - "Stagger-fade-up: each child in a section fades up 20px with 80ms stagger on scroll-into-view.
        Cards tilt 2° on hover with smooth spring. Primary CTA has a subtle breathing scale animation."
     - "Scroll-triggered reveals using Intersection Observer: H2s slide in from left, body text fades.
        Hover state lifts card up 4px with ring-2 ring-primary/20 glow."
     - "Aggressive: sections use full scroll-linked parallax. Hero H1 letters animate in one at a time.
        Hover rotates cards 3° and shifts background color."
     - "Minimal: no scroll animations, only hover — cards lift 2px + shadow deepens. Focus states
        use ring-offset rings. Page feels stable and premium."
   MUST specify: enter animation, hover state, any scroll-linked behavior, and emotional register.]

decorative_pattern:
  [Describe ONE pattern or texture that shows up across multiple sections at low opacity.
   This is the background "noise" that makes the page feel designed rather than assembled.
   Examples:
     - "1px dot grid at 4% opacity on bg-background sections, hidden on bg-card sections."
     - "Subtle diagonal line pattern (Tailwind repeating-linear-gradient) behind H2 elements."
     - "SVG hand-drawn squiggle underlines under key brand words, in primary/30 color."
     - "Grainy film-noise PNG overlay at 8% opacity globally via fixed::before pseudo-element."
     - "Topographic-map contour lines SVG behind the footer, off-canvas extending beyond viewport."
     - "Floating ambient orbs — 3 blurred (blur-3xl) colored circles, parallax-scrolled at different speeds."
   Say "none" ONLY for ultra-minimal brutalist brands. Include how it's applied in Tailwind/CSS.]

border_radius_language:
  [Specify the corner rounding philosophy for THIS project. Options:
     - sharp          → 0px all corners (brutalist, editorial, fashion)
     - crisp          → 4-6px (technical, clean, B2B)
     - standard       → 12-16px (mainstream SaaS, friendly)
     - soft           → 20-28px (premium consumer, wellness)
     - pill           → rounded-full on CTAs, rounded-3xl on cards (playful, consumer)
     - organic        → irregular asymmetric blob-radius (wellness, beauty, nature brands)
     - mixed          → CTAs pill-shaped, cards sharp, images soft — intentional contrast
   Include the EXACT Tailwind radius values to use for: buttons, cards, images, inputs.
   Example: "soft — cards rounded-3xl, images rounded-2xl, buttons rounded-full, inputs rounded-xl".]

color_application_strategy:
  [How color is distributed across the page. Pick ONE:
     - mono-accent         → 85% neutral, one primary accent color used sparingly for CTAs + highlights
     - duotone-photos      → all photos filtered to 2-color duotone matching brand palette
     - gradient-mesh       → soft gradient meshes as section backgrounds (from-primary/5 via-background to-accent/5)
     - inverted-dark       → one major section is inverted (dark bg, light text) for dramatic contrast
     - polychrome          → 3-4 accent colors each "owning" a different section
     - photographic-neutral → color comes only from imagery, UI is near-monochrome neutral
     - brand-flood         → brand color as background on hero AND final CTA, everything else neutral
   Include WHICH sections get which treatment. Example:
     "inverted-dark: hero neutral, testimonials full bg-foreground text-background, locations neutral, CTA brand-flood."]

hover_interaction_style:
  [How cards, buttons, and images respond to hover. Pick 1-2 consistent behaviors:
     - lift-and-shadow     → translateY(-4px) + shadow deepens (default, safe)
     - tilt-3d             → rotateX/Y 2-3deg on mouse move (requires small JS, use Framer or CSS transforms)
     - reveal-content      → secondary info (read-more, icon) slides in from bottom on hover
     - glow-ring           → ring-2 ring-primary/40 with offset appears, no movement
     - morph-shape         → border-radius animates between two values on hover
     - invert-colors       → card inverts: bg-card hover → bg-foreground text-background
     - magnetic-cursor     → button slightly follows cursor within its bounds (small translate)
   Specify for: primary cards, CTAs, image cards, nav links. Keep consistent within a category.]

spacing_rhythm:
  [Pick the page's vertical rhythm personality:
     - tight-editorial   → py-12 md:py-16 between sections, dense packed feel
     - standard-modern   → py-20 md:py-28 (most common)
     - airy-luxury       → py-28 md:py-40, generous whitespace, premium feel
     - asymmetric        → varies per section: hero py-32, next py-16, next py-40 — intentional rhythm
     - dense-information → py-10 md:py-14, info-heavy, dashboard-adjacent
   Also specify gutter tightness: "container max-w-5xl gap-6" (tight) vs "max-w-7xl gap-12" (airy).]

# ═══════════════════════════════════════════════════════════════

novelty_check:
  [One sentence: if another designer made a {domain} landing page tomorrow,
   what about YOUR design would be visibly different from theirs? If you can't
   answer this, your blueprint is too safe — redo it.]

design_dna_summary:
  [ONE sentence in 15-25 words describing the DESIGN DNA as a unique recipe.
   Example: "Editorial-luxe Fraunces-italic headlines over cinematic full-bleed photography,
   paper-fold cards with wax-seal emblems, airy 40vh spacing, brand-flood CTA finale."
   This is the one-liner Claude should keep in mind while implementing every section.]

CRITICAL for LAYOUT_BLUEPRINT:
  - Your output gets passed to Claude who MUST implement what you describe.
  - Describe in code-translatable language — "grid-cols-2 gap-12 with an
    aspect-[4/5] image on the right" is better than "two-column with an image".
  - NO one-word answers. NO generic phrases like "modern clean design",
    "beautiful gradient", "engaging hero" — these are meaningless.
  - Every DNA variable must be CONCRETE and DIFFERENT across projects.
    Two coffee-shop projects should get different hero_archetype, different
    card_language, different motion_language — even if palette is similar.
  - Your success = Claude produces something that looks bespoke, not templated.

CRITICAL: Every value from REAL internet research. Original copy. Domain-specific. Production-quality.
"""

    # Call Gemini via direct REST API (deprecated SDK removed)
    import httpx

    # Research model is configurable via GEMINI_RESEARCH_MODEL env var.
    # Default is gemini-2.5-pro for superior design/trend reasoning (research runs
    # once per project so the ~5x cost delta is acceptable for the quality lift).
    # Set GEMINI_RESEARCH_MODEL=gemini-2.5-flash to trade quality for speed/cost.
    _research_model = os.environ.get("GEMINI_RESEARCH_MODEL", "gemini-2.5-pro")
    await _ws_send(websocket, "progress", f"🔬 Calling {_research_model} with search...")

    gemini_url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{_research_model}:generateContent?key={gemini_key}"
    )

    # Thinking budget is model-dependent:
    # - Flash allows thinkingBudget: 0 (plain text output, faster)
    # - Pro REQUIRES thinking mode (API rejects budget=0 with 400)
    # Our parser (knowledge.loader.safe_gemini_text) already filters out thought
    # parts, so enabling thinking on Pro is safe — we just ignore the scratchpad.
    _is_pro = "pro" in _research_model.lower()
    _thinking_config = (
        {"thinkingBudget": 2048}  # modest budget; Pro can't accept 0
        if _is_pro
        else {"thinkingBudget": 0}  # Flash: skip thinking for plain text
    )
    def _build_gemini_payload() -> dict:
        return {
            "contents": [{"parts": [{"text": research_prompt}]}],
            "generationConfig": {
                "maxOutputTokens": 16000,  # raised from 8K — prevents entity spec truncation
                "temperature": 0.3,
                "thinkingConfig": _thinking_config,
            },
            "tools": [{"google_search": {}}],
            "systemInstruction": {
                "parts": [{
                    "text": (
                        "You are a senior product researcher and UX strategist. "
                        "Return precise, factual, structured output only. "
                        "Use real-world product references and industry-standard design patterns. "
                        "Prioritize specificity over generality — name actual colors (HSL values), "
                        "real font pairings, and concrete UI patterns used by top products in the domain. "
                        "Never hallucinate — if unsure about a specific value, use the most common industry default."
                    )
                }]
            },
        }

    gemini_payload = _build_gemini_payload()

    # Retry logic: up to 3 attempts for 429 rate-limits.
    # Semaphore caps concurrent Gemini calls to avoid quota exhaustion.
    _max_attempts = 3
    _thinking_enabled = False
    response = None
    async with _gemini_semaphore:
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

            break  # non-retryable response

    if response is None or response.status_code != 200:
        status = response.status_code if response is not None else 0
        error_snippet = response.text[:300] if response is not None else "no response"
        logger.error("Gemini research API error %d: %s", status, error_snippet)

        # Parse the API's error message so the user sees the real reason
        # (e.g. model access denied vs. malformed request vs. expired key).
        _api_msg = ""
        try:
            if response is not None:
                _api_msg = (response.json().get("error") or {}).get("message", "")
        except Exception:
            pass

        if status == 403:
            await _ws_send(websocket, "error", "❌ Google API key rejected (403). Check GOOGLE_API_KEY in .env.")
        elif status == 400:
            _reason = _api_msg or "bad request"
            await _ws_send(websocket, "error", f"❌ Gemini rejected the request (400): {_reason}")
        elif status == 404:
            await _ws_send(websocket, "error", f"❌ Gemini model not found: {_research_model}. Set GEMINI_RESEARCH_MODEL to a valid model.")
        elif status == 429:
            await _ws_send(websocket, "error", "⚠️ Gemini rate limit hit after retries. Try again in a minute.")
        else:
            await _ws_send(websocket, "error", f"❌ Gemini API error {status}: {_api_msg or 'unknown'}")
        raise RuntimeError(f"Gemini research API error: {status}")
    
    try:
        data = response.json()
    except Exception as exc:
        logger.error("Gemini response was not valid JSON: %s", exc)
        raise RuntimeError(f"Gemini returned non-JSON body: {exc}")

    from knowledge.loader import safe_gemini_text
    text = safe_gemini_text(data)

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
    max_fix_attempts: int = MAX_FIX_ATTEMPTS,
) -> bool:
    """Run npm/pnpm build and auto-fix errors with Claude.

    Pass max_fix_attempts=0 to do a check-only run (no Claude fix calls).
    Returns True if build passes (with or without fixes).
    """
    pm = _detect_pm(workspace_path)

    # For Next.js projects use --no-lint to skip ESLint during build.
    # Our post-generation fixers already handle all ESLint issues (unescaped
    # entities, img→Image, use client), so running ESLint again just wastes 20-30s.
    _is_nextjs_project = (
        os.path.exists(os.path.join(workspace_path, "next.config.mjs"))
        or os.path.exists(os.path.join(workspace_path, "next.config.js"))
    )
    build_cmd = ([pm, "exec", "next", "build", "--no-lint"] if _is_nextjs_project
                 else [pm, "run", "build"])

    for attempt in range(1, max_fix_attempts + 2):  # +1 for initial + N fixes
        await _ws_send(websocket, "progress", f"🔨 Build attempt {attempt}...")

        try:
            result = await asyncio.to_thread(
                subprocess.run,
                build_cmd,
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

        if attempt > max_fix_attempts:
            await _ws_send(websocket, "progress", f"⚠️ Build still failing after {max_fix_attempts} fix attempt(s)")
            logger.error("Build failed after %d fix attempt(s). Errors:\n%s", max_fix_attempts, errors[:1000])
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
  IMPORTANT: Keep `@import "tw-animate-css"` at the top — NEVER replace with tailwindcss-animate
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

SYSTEM_PROMPT_CORE = """You are a world-class Senior Frontend Engineer and UI/UX expert building award-winning websites.
Your output must match the visual quality of top Awwwards sites, Stripe, Linear, Vercel — NEVER boilerplates or templates.

VISUAL AMBITION (non-negotiable):
- Every page must look like a $50K+ agency build, not a free template
- Full-bleed hero sections with rich visual treatment (gradients, overlays, bold type)
- Every section has a clear visual identity: imagery, color, spacing, type hierarchy
- NEVER blank white sections — every section has background variation and visual depth
- Cards must have hover effects, images, and rich content — not placeholder boxes
- Typography must be bold and expressive — hero headlines text-5xl to text-7xl
- Generous whitespace: py-24 to py-32 between major sections

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
ALLOWED PACKAGES (non-negotiable)
====================================
Only import from packages that exist in the template. DO NOT add imports from any other package.

Next.js template packages (use ONLY these):
  next, react, react-dom
  lucide-react                   ← icons ONLY
  framer-motion                  ← animations
  @tanstack/react-query          ← server state / data fetching
  axios                          ← HTTP client
  react-hook-form                ← forms
  @hookform/resolvers            ← form validation
  zod                            ← schema validation
  zustand                        ← client state
  recharts                       ← charts
  dayjs                          ← date formatting
  clsx, tailwind-merge, class-variance-authority, tw-animate-css

React-admin / Vue template also has: react-router-dom, @supabase/supabase-js

UI components live in src/components/ui/ — import from there:
  import { Button } from '@/components/ui/Button'
  import { Accordion, AccordionItem, AccordionTrigger, AccordionContent } from '@/components/ui/accordion'
  import { Card, CardHeader, CardContent } from '@/components/ui/Card'
  (see TEMPLATE_MANIFEST.md for the full list)

NEVER import from: @radix-ui/react-*, @base-ui/*, @headlessui/*, cmdk, sonner,
  react-select, react-table, @dnd-kit/*, react-beautiful-dnd, or any package
  not listed above. If you need a component, BUILD it from scratch using React
  and Tailwind, or use what is already in src/components/ui/.

====================================
EXPORT RULES (non-negotiable)
====================================
Every React component file MUST end with a default export:
  CORRECT: export default function HeroSection() { ... }
  CORRECT: export function HeroSection() { ... }  then  export default HeroSection;
  WRONG:   export function HeroSection() { ... }  with NO default export

Pages always import components as default: import HeroSection from '@/components/sections/HeroSection'
If you use a named export in a component file, you MUST also add "export default ComponentName;" at the end.
Mixing named-only exports with default imports causes "Unsupported Server Component type: undefined".

====================================
JSX TEXT CONTENT RULES (non-negotiable)
====================================
NEVER use bare apostrophes or quotes in JSX *text nodes* — they cause ESLint build failures:
  ✗ <p>Don't forget</p>        → ESLint error: react/no-unescaped-entities
  ✓ <p>Don&apos;t forget</p>   → correct
  ✓ <p>{"Don't forget"}</p>    → also correct

Use &apos; for apostrophes and &quot; for quotes ONLY in JSX text nodes
(content between > and <). NEVER use these entities as attribute-value
delimiters — the JSX/SWC parser cannot read entity-delimited attributes.

  ✗ className=&quot;flex gap-2&quot;    → "Expression expected" parser error
  ✗ onClick=&apos;...&apos;             → same parser error
  ✓ className="flex gap-2"              → correct — attribute values ALWAYS use literal " or '
  ✓ onClick={(e) => e.preventDefault()} → expression, literal braces

Rule of thumb: entities (&apos; &quot;) go INSIDE text between tags. Attribute
values always use literal " or ' as the delimiter — never an HTML entity.

====================================
OVERLAYS (non-negotiable)
====================================
All overlays MUST be opaque:
  Floating: z-50 bg-popover text-popover-foreground border shadow-md
  Modal backdrop: bg-black/50 backdrop-blur-sm
  NEVER transparent/semi-transparent popover backgrounds

====================================
NO INLINE STYLES (non-negotiable)
====================================
NEVER use the `style={{...}}` prop for colors, spacing, layout, typography,
borders, shadows, or background images. Use Tailwind classes instead.

Specifically forbidden (each has a Tailwind replacement):
  ✗ style={{ backgroundImage: 'linear-gradient(...)' }}
     → className="bg-gradient-to-br from-primary to-accent"
  ✗ style={{ backgroundImage: "url('https://images.unsplash.com/photo-...')" }}
     → use an <img> tag underneath with absolute inset-0 object-cover, OR
       className="bg-[url('https://images.unsplash.com/photo-...')] bg-cover bg-center"
  ✗ style={{ backgroundColor: '#1e293b' }}   → className="bg-slate-800" or bg-primary
  ✗ style={{ color: 'white' }}               → className="text-white"
  ✗ style={{ width: '380px' }}               → className="w-[380px]"
  ✗ style={{ padding: '24px' }}              → className="p-6"

Banner/CTA hero pattern (the common trap) — use this structure, NEVER inline style:
  <section className="relative overflow-hidden rounded-3xl bg-gradient-to-br from-primary to-accent">
    <img src="https://images.unsplash.com/..." alt="" className="absolute inset-0 w-full h-full object-cover opacity-30" />
    <div className="relative z-10 px-8 py-20 md:py-28 text-center">...</div>
  </section>

The only acceptable `style={{}}` cases are:
  - Framer Motion hardcoded props (opacity/x/y transforms driven by state)
  - Truly dynamic values computed from props/state at runtime (e.g.
    style={{ width: `${progress}%` }} for a progress bar).
Static color / image / size values ALWAYS go in className.

====================================
DESIGN SPEC BLOCK
====================================
Every generation includes a "UI DESIGN SPEC (from research)" block in the prompt.
This block contains domain-specific colors, fonts, spacing, and component patterns
derived from Gemini deep research on real products in the same industry.

WHAT TO DO:
  - Apply the color palette exactly to the CSS theme variables (:root HSL values)
  - Use the design personality and typography to set the tone for all components
  - Use spatial pattern cues for gap/padding/border-radius decisions
  - Build components that feel PURPOSE-BUILT for the specific domain

RULES (critical):
  - All colors from the spec → CSS variables, then Tailwind tokens (bg-primary, etc.)
  - Never hardcode hex in component files — CSS variables only
  - The design spec is UI-ONLY — do not change page structure, routes, or entities

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
ANIMATIONS (safe patterns)
====================================
Page transition: initial={{opacity:0,y:8}} animate={{opacity:1,y:0}} transition={{duration:0.15}}
List stagger: staggerChildren:0.04, child y:20→0
Card hover: whileHover={{y:-2}} transition={{duration:0.1}}
Scroll reveal: whileInView + viewport={{once:true}}
Every interactive element has a hover state + transition-colors duration-150 on hover/focus.

NEVER: layoutId on table rows | animate during loading

====================================
SPACING DENSITY RULES
====================================
Content pages (admin/dashboard): compact-to-comfortable
  - Table rows: py-3 px-4 (not py-6)
  - Form rows: space-y-4 (not space-y-8)
  - Card padding: p-4 md:p-6
  - Section gap: gap-4 md:gap-6

Landing / marketing / public pages (incl. 404, About, Contact): comfortable-to-spacious
  - Hero: min-h-[80vh] py-24 md:py-32
  - Sections: py-20 md:py-28
  - Feature cards: p-8 gap-8

NEVER mix dense and spacious sections on the same page.
"""


# Phase 1 (foundation) — theme tokens, palette math, typography scaffolding,
# navigation structure. These rules matter most when laying down globals.css,
# tailwind.config, navigation config, layout components.
PHASE_APPENDIX_FOUNDATION = """

====================================
CSS THEME — FILE STRUCTURE (non-negotiable)
====================================
Any rewrite of globals.css / global.css / src/index.css MUST start with these
4 lines BEFORE any @import url() for fonts, before :root, before anything else:

  @tailwind base;
  @tailwind components;
  @tailwind utilities;
  @import "tw-animate-css";

Order after that:
  1. @import url('https://fonts.googleapis.com/...') for heading + body fonts
  2. @layer base { :root { --primary: ...; ... } .dark { ... } }
  3. @layer base { * { @apply border-border; } body { @apply bg-background text-foreground; } }

Omitting the @tailwind directives breaks every Tailwind class in every component
(site renders unstyled). Omitting @import "tw-animate-css" breaks all animations.
NEVER replace @import "tw-animate-css" with tailwindcss-animate — it is not a plugin.

====================================
CSS THEME — VARIABLES
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
NEVER add tailwindcss-animate to tailwind.config.js plugins — animations come from `tw-animate-css` via CSS @import.

====================================
TYPOGRAPHY HIERARCHY (non-negotiable)
====================================
Every page must have a clear 4-level hierarchy:
  L1 — Page title: text-3xl font-bold tracking-tight (one per page)
  L2 — Section title: text-xl font-semibold (cards, panels)
  L3 — Item label: text-sm font-medium text-foreground
  L4 — Supporting: text-sm text-muted-foreground

NEVER use font-bold for body copy. NEVER use the same size for L1 and L2.
Data labels (table headers, form labels): text-xs font-medium uppercase tracking-wider text-muted-foreground

====================================
NAVIGATION QUALITY
====================================
Sidebar active item: bg-primary/10 text-primary font-medium border-l-2 border-primary
Sidebar hover: hover:bg-muted transition-colors duration-150
Breadcrumb on nested pages: text-sm text-muted-foreground with / separator, current page text-foreground
Top-nav active link: text-primary font-medium underline-offset-4 underline
"""


# Phase 2 (content) — pages/sections/CRUD modules. This is where UI quality
# rules, data fetching states, animations, forms, API services, and feedback
# patterns apply.
PHASE_APPENDIX_CONTENT = """

====================================
LANDING PAGE VISUAL LANGUAGE (non-negotiable)
====================================
These rules apply to landing / marketing / website pages (hero, features, about,
menu, testimonials, pricing, locations, CTA). Violating any of them produces a
"generic SaaS template" result that the user will reject.

------------------------------------------------------------
0) READ THE ===LAYOUT_BLUEPRINT=== BLOCK FIRST — IT IS YOUR DIRECTION
------------------------------------------------------------
The DESIGN SYSTEM FROM RESEARCH section contains a ===LAYOUT_BLUEPRINT=== block
written by the creative director (Gemini research). It describes — in plain
language — the exact visual design you must implement.

If a ===VISUAL_DNA=== block is ALSO present, it contains direct visual
observations from screenshots of real reference sites (hero composition,
color ratios, card language, spacing rhythm, motion cues). The VISUAL_DNA
values override the verbal LAYOUT_BLUEPRINT wherever the two conflict — the
visual analysis is grounded in actual pixels, the verbal blueprint is
inference. Treat VISUAL_DNA as the authoritative source for: hero_composition,
color_application, typography_system, card_language, spacing_rhythm,
motion_language. Use the distinctive_moves list as must-have touches.

───────────────────────────────────────────────────────────────
BRAND_MARK, RADIUS_TOKENS, IMAGE_COMPOSITION, ADMIN_UI_LANGUAGE — DIRECTOR BLOCKS
───────────────────────────────────────────────────────────────
If a ===BRAND_MARK=== block is present: every Header/Navbar/Sidebar you
generate MUST render that wordmark/monogram with the exact font, weight,
case, tracking, and size specified. A navbar without a logo is a failure.

If a ===RADIUS_TOKENS=== block is present: every rounded element MUST use
one of those exact radii. Buttons use radius_tokens.button, inputs use
radius_tokens.input, cards use radius_tokens.card, badges use
radius_tokens.badge. Never mix ad-hoc values like rounded-xl on one card
and rounded-md on another — pick one language and apply it.

If a ===IMAGE_COMPOSITION=== block is present (it is — this is UNIVERSAL):
EVERY section that mixes copy with photography (hero, testimonial,
quote, reservation, contact, booking, feature banners, admin hero
banners, ecommerce product shots) MUST obey the exact values in that block:
  • overlay_pattern (dark_scrim | light_scrim | split_solid | card_lift |
    side_caption) decides the composition. Do NOT invent your own.
  • overlay_scrim_classes is the literal Tailwind gradient to place as
    `absolute inset-0` BETWEEN the image and the text when the pattern
    is a scrim variant. Without the scrim, overlaid text has no contrast.
  • overlay_text_color is the text-color class used on any copy sitting
    over the image. Never use default `text-foreground` on a raw photo.
  • image_container_mode (full_bleed | centered | split_half | split_third):
    images are NEVER half-width next to raw whitespace. That reads as a
    broken layout — the primary failure mode we are fixing.
  • form_treatment (card_lift_solid | split_solid | standalone_section):
    reservation / contact / booking / signup / newsletter forms ALWAYS
    sit in an OPAQUE bg-card or bg-background container. NEVER
    glassmorphism (`backdrop-blur`) over photography — inputs become
    unreadable, labels vanish, placeholders disappear. If form_treatment
    is `standalone_section`, the form has its OWN section (bg-muted/30 or
    bg-background) — no image underneath.
  • Quote/testimonial blocks over imagery: require `dark_scrim` or
    `card_lift`. Bare italic serif floating on a light photograph is
    BANNED — reads as unreadable and broken.

Concrete code pattern for a scrim section (applies to every hero-with-image,
testimonial-with-image, cta-with-image section you build):

  <section className="relative ...">
    <div className="absolute inset-0">
      <img src="..." alt="..." className="w-full h-full object-cover" />
      <div className="absolute inset-0 {overlay_scrim_classes}" />
    </div>
    <div className="relative z-10 ...">
      <h2 className="{overlay_text_color} ...">Headline</h2>
    </div>
  </section>

Concrete code pattern for a form-over-image section (reservation / contact):

  <section className="relative ...">
    <img className="absolute inset-0 w-full h-full object-cover" src="..." />
    <div className="absolute inset-0 bg-gradient-to-t from-black/60 to-black/20" />
    <div className="relative z-10 container">
      <div className="bg-card text-card-foreground rounded-{radius} shadow-xl p-8 ...">
        {/* form fields here — input bg-background, never transparent */}
      </div>
    </div>
  </section>


If a ===ADMIN_UI_LANGUAGE=== block is present (admin/CRM/TMS/SaaS
dashboard/ecommerce projects only): every DataTable, form, sidebar,
toolbar, empty state, and chart MUST follow the recipe there. Values in
that block WIN over any generic admin defaults in the phase-1/phase-2
prompts below. In particular:
  - Table row_height, border_style, header_weight come from the block.
  - Form label_position, input_style, focus_style come from the block.
  - Sidebar width, variant, active_treatment come from the block.
  - Toolbar search_chrome, filter_style, bulk_action_style come from the block.
  - Empty-state illustration_style, copy_tone, cta_placement come from the block.
  - Chart line_weight, axis_style, tooltip_style come from the block.
  - Status colors (success/warning/info/neutral/error) come from status_palette
    in the block, NEVER from the brand primary/accent.

Fields you will see in LAYOUT_BLUEPRINT:

  hero_description              → 2-4 sentences describing hero layout/imagery/accents
  features_description          → how the primary content section is laid out
  secondary_sections_description → layout for testimonials/gallery/story/etc.
  section_rhythm                → every section → bg treatment (open-ended phrases)
  signature_motif               → one decorative element used 2-3x across the page
  design_mood                   → 2-3 sentences on the visual personality
  hero_image_url                → exact Unsplash URL for the hero
  supporting_image_urls         → URLs for other sections, mapped by section
  accent_detail                 → one signature micro-detail + placement

  DESIGN DNA variables (NEW — these control the unique visual language):
  hero_archetype                → split / bento / diagonal / magazine / layered / cinematic / editorial-offset / full-bleed-dark / typographic / invented
  features_archetype            → bento-mixed / zigzag / vertical-tabs / horizontal-scroll / masonry / tilt-stack / showcase / timeline / numbered-editorial
  card_language                 → 1-2 sentences on radius + border + shadow + micro-details (paper-fold, wax-seal, glass, organic-blob, brutalist etc.)
  typography_pairing            → heading font + body font + mixing rules (tracking, weight, italic, caps)
  motion_language               → enter animation + hover state + scroll behavior + emotional register
  decorative_pattern            → ONE recurring low-opacity texture/pattern across sections (dots, noise, squiggles, orbs, topographic, or "none")
  border_radius_language        → sharp / crisp / standard / soft / pill / organic / mixed — plus exact values per element
  color_application_strategy    → mono-accent / duotone-photos / gradient-mesh / inverted-dark / polychrome / photographic-neutral / brand-flood
  hover_interaction_style       → lift-and-shadow / tilt-3d / reveal-content / glow-ring / morph-shape / invert-colors / magnetic-cursor
  spacing_rhythm                → tight-editorial / standard-modern / airy-luxury / asymmetric / dense-information + gutter tightness
  design_dna_summary            → the ONE-SENTENCE recipe Claude should keep in mind per section

  novelty_check                 → what makes this design visibly different

YOUR JOB AS THE IMPLEMENTER:
  1. Read every blueprint field CAREFULLY before writing any JSX.
  2. Translate the descriptive language into concrete Tailwind classes.
     e.g. blueprint says "diagonal split with a tilted polaroid of a latte
     overlapping a warm beige backdrop, handwritten 'since 2014' label upper-right"
     → you write:
        <section className="relative grid md:grid-cols-5 gap-8 bg-[#f5ecd8] overflow-hidden">
          <div className="md:col-span-3 py-24 px-8"><h1>...</h1></div>
          <div className="md:col-span-2 relative">
            <img src="{hero_image_url}" className="rotate-3 rounded-lg shadow-2xl aspect-[4/5] object-cover" />
            <span className="absolute top-6 right-6 font-handwritten text-primary text-xl">since 2014</span>
          </div>
        </section>
  3. Implement EVERY element the blueprint describes — floating cards, motifs,
     accent details, section rhythm. If the blueprint mentions an element and
     you skip it, that's a regression.
  4. The hero_image_url and supporting_image_urls are EXACT — use them verbatim
     as <img src="..."/> values.
  5. If the blueprint invents a section name not in your default list (e.g.
     "coffee_of_the_month_feature"), BUILD that section as described.

DO NOT fall back to a generic hero/features/testimonials/cta stack if the
blueprint describes something more specific. The blueprint is what gives this
project its unique personality — overriding it produces the "template" output
the user is trying to avoid.

═══════════════════════════════════════════════════════════════
DESIGN DNA → TAILWIND TRANSLATION GUIDE
═══════════════════════════════════════════════════════════════
Each Design DNA value has a canonical Tailwind realization. Apply it
CONSISTENTLY across every section of the project. Mixing archetypes within
one project is what makes output look "AI-generated". Pick the DNA once,
apply it everywhere.

▸ hero_archetype → wrapper JSX skeleton:
    split               → grid md:grid-cols-2 gap-12 items-center min-h-[85vh]
    bento               → grid md:grid-cols-6 md:grid-rows-3 gap-4 min-h-[85vh]
                          (one tile md:col-span-4 md:row-span-2 holds H1)
    diagonal            → relative overflow-hidden + inner
                          <div className="absolute inset-0 bg-primary"
                                 style={{clipPath:'polygon(0 0, 55% 0, 40% 100%, 0 100%)'}} />
    magazine            → grid md:grid-cols-12 gap-8, H1 at col-span-9 text-[clamp(3rem,10vw,9rem)]
                          font-bold leading-[0.9], image at col-span-3 col-start-10 self-start
    layered-scroll      → relative min-h-screen with 3 absolute layers, data-parallax speeds
    cinematic-parallax  → relative h-screen with <img absolute inset-0 object-cover scale-110> +
                          dark gradient overlay + centered text-white H1
    editorial-offset    → relative h-[90vh] + H1 absolute top-16 left-12 + image absolute
                          bottom-0 right-0 w-[55%] aspect-[4/5] object-cover
    full-bleed-dark     → relative min-h-[90vh] + img absolute inset-0 + bg-gradient-to-t
                          from-black/80 via-black/40 to-transparent overlay + text-white
    product-showcase    → grid md:grid-cols-5 gap-8, device mockup at col-span-3, text col-span-2
    typographic-hero    → py-40 text-center with H1 at text-[clamp(4rem,14vw,12rem)] font-black

▸ features_archetype → section JSX skeleton (choose the one named, do NOT default to 3-col grid):
    bento-mixed         → grid md:grid-cols-4 md:auto-rows-[16rem] gap-4
                          (feature 1: col-span-2 row-span-2; features 2-5: col-span-1)
    zigzag              → space-y-32 with each row: grid md:grid-cols-2 gap-12 items-center
                          (even rows: image left; odd rows: image right via md:order-2)
    vertical-tabs       → grid md:grid-cols-3 gap-8, left col sticky top-24 with tab list,
                          right col-span-2 shows active panel with fade transition
    horizontal-scroll   → flex gap-6 overflow-x-auto snap-x snap-mandatory px-6
                          (each card: min-w-[22rem] snap-start)
    masonry             → columns-1 md:columns-2 lg:columns-3 gap-6 (cards: break-inside-avoid mb-6)
    tilt-stack          → relative h-[38rem] with cards absolute, each rotate-[N]deg at different
                          offsets, hover: scroll-triggered to rotate-0 with Framer/Motion One
    showcase            → grid md:grid-cols-5 gap-8, big viz at col-span-3 row-span-2, 4 small
                          clickable thumbnails at col-span-2 split into 2x2
    timeline            → relative border-l-2 border-border pl-8 space-y-16, each step has
                          absolute -left-[9px] top-1 w-4 h-4 rounded-full bg-primary
    numbered-editorial  → grid md:grid-cols-3 gap-12 with giant text-8xl font-black text-muted
                          numbers (01, 02, 03) above each step title

▸ card_language → card className (use this EXACT pattern for every card on the page):
    brutalist-editorial   → "rounded-none border-2 border-foreground bg-card p-8
                             hover:bg-foreground hover:text-background transition-colors"
    cream-hairline-seal   → "rounded-2xl border border-foreground/15 bg-card p-8 relative
                             before:absolute before:top-3 before:right-3 before:w-6 before:h-6
                             before:rounded-full before:bg-primary/20"
    glass                 → "rounded-2xl border border-white/10 bg-white/5 backdrop-blur-md
                             p-8 shadow-[0_8px_32px_rgb(0_0_0/0.12)]"
    paper-fold            → "rounded-xl bg-card p-8 shadow-md relative overflow-hidden
                             before:absolute before:inset-x-0 before:top-1/2 before:h-px
                             before:bg-gradient-to-r before:from-transparent before:via-foreground/5 before:to-transparent"
    organic-blob          → "p-8 bg-card shadow-lg
                             style={{borderRadius:'30% 70% 40% 60%/40% 50% 60% 50%'}}"
    Otherwise: interpret the blueprint's description literally as Tailwind.

▸ typography_pairing → Google Fonts import in globals.css AND font-family utility classes:
    e.g. "Fraunces heading + Inter body" → @import url('...Fraunces...'); @import url('...Inter...');
    in :root {{ --font-heading:'Fraunces',serif; --font-body:'Inter',sans-serif; }}
    tailwind.config extend: fontFamily: {{ heading:['var(--font-heading)'], body:['var(--font-body)'] }}
    Then use: <h1 className="font-heading"> and <body className="font-body">
    All H1/H2 use font-heading. Body paragraphs use font-body (or default).
    Honor tracking/italic/caps rules from the blueprint (e.g. "italic for H1" → italic class).

▸ motion_language → add Framer Motion or Motion One (preferred: motion/react), wrap components:
    stagger-fade-up     → <motion.div initial={{opacity:0,y:20}} whileInView={{opacity:1,y:0}}
                             viewport={{once:true}} transition={{duration:0.5,delay:i*0.08}}>
    tilt-on-hover       → <motion.div whileHover={{rotate:2}} transition={{type:'spring',stiffness:200}}>
    minimal-no-scroll   → NO Framer wrapper; only Tailwind hover classes:
                          "transition-all duration-200 hover:-translate-y-1 hover:shadow-xl"
    parallax-scroll     → use useScroll + useTransform from motion/react for background layers
    Install: add "framer-motion" to package.json dependencies if motion_language isn't "minimal".

▸ decorative_pattern → place once in a global component (background fixed layer) OR per-section:
    dot-grid            → absolute inset-0 bg-[radial-gradient(circle_at_1px_1px,rgb(var(--foreground-rgb)/0.08)_1px,transparent_0)] bg-[size:24px_24px]
    grain-noise         → fixed inset-0 pointer-events-none opacity-[0.06] bg-[url('data:image/svg+xml;base64,...noise-svg...')]
    squiggle-underline  → inline <svg> under key brand words with stroke=currentColor stroke-width=2
    floating-orbs       → 3 absolute divs, each w-[40rem] h-[40rem] rounded-full bg-primary/20
                          blur-3xl, placed -top-40 -left-40 / top-1/2 right-0 / bottom-0 left-1/3
    topographic         → absolute bottom-0 inset-x-0 h-64 bg-[url('contour.svg')] opacity-10
    none                → skip entirely (ultra-minimal brands)

▸ border_radius_language → set --radius in CSS + use named Tailwind classes consistently:
    sharp        → --radius:0px;        buttons: rounded-none  cards: rounded-none  images: rounded-none
    crisp        → --radius:0.375rem;   buttons: rounded-md    cards: rounded-lg    images: rounded-md
    standard     → --radius:0.75rem;    buttons: rounded-lg    cards: rounded-xl    images: rounded-xl
    soft         → --radius:1.25rem;    buttons: rounded-2xl   cards: rounded-3xl   images: rounded-2xl
    pill         → --radius:1rem;       buttons: rounded-full  cards: rounded-3xl   images: rounded-2xl
    organic      → cards: style={{borderRadius:'30% 70% 40% 60%/40% 50% 60% 50%'}}
    mixed        → interpret blueprint's specific per-element mapping
    APPLY CONSISTENTLY: every button must use the same radius, every card must use the same radius.

▸ color_application_strategy → WHICH section gets which bg:
    mono-accent         → 95% bg-background + 5% bg-primary (CTA buttons only)
    duotone-photos      → CSS filter: grayscale(100%) + mix-blend-multiply + bg-primary/40 over images
    gradient-mesh       → hero + CTA use bg-gradient-to-br from-primary/10 via-background to-accent/10
    inverted-dark       → ONE section (usually testimonials or final CTA) gets
                          bg-foreground text-background — strong contrast break
    polychrome          → hero bg-primary/5, features bg-accent/5, testimonials bg-secondary/10 etc.
    photographic-neutral→ UI stays bg-background; color lives only in <img> content
    brand-flood         → hero AND final CTA both use bg-primary text-primary-foreground,
                          everything in between stays neutral — bookends visual energy

▸ hover_interaction_style → add these utility combos to every card/button:
    lift-and-shadow  → "transition-all duration-200 hover:-translate-y-1 hover:shadow-xl"
    glow-ring        → "transition-shadow duration-300 hover:shadow-[0_0_0_4px_hsl(var(--primary)/0.2)]"
    morph-shape      → "transition-[border-radius] duration-300 rounded-2xl hover:rounded-3xl"
    invert-colors    → "transition-colors duration-200 hover:bg-foreground hover:text-background"
    reveal-content   → group + inner absolute translate-y-full group-hover:translate-y-0 transition
    tilt-3d / magnetic-cursor → use Framer Motion whileHover={{rotateX:-5,rotateY:5}} or custom mouse handler

▸ spacing_rhythm → set the section spacing CLASS used throughout the page:
    tight-editorial    → every <section> uses "py-12 md:py-20"
    standard-modern    → "py-20 md:py-28"     (safe default)
    airy-luxury        → "py-28 md:py-40"
    asymmetric         → vary per section as blueprint specifies
    dense-information  → "py-10 md:py-14"

BEFORE writing ANY section JSX, mentally confirm:
  □ Which hero_archetype am I using? Apply its wrapper skeleton.
  □ Which features_archetype am I using? Apply its section skeleton.
  □ Am I using the card_language consistently on EVERY card?
  □ Is the font-heading / font-body pairing applied?
  □ Is the hover_interaction_style the same everywhere?
  □ Is the border_radius_language the same on every button/card/image?
  □ Is the section spacing_rhythm applied to EVERY section?
If any answer is inconsistent, you are drifting toward "template output".

Every project must feel like ONE designer made it — not a mashup of 5 components
Claude found in its training data.

If the ===LAYOUT_BLUEPRINT=== block is MISSING (admin-dashboard / CRM archetypes
skip it), fall back to the rules below as defaults.

------------------------------------------------------------
1) HERO COPY — must reference the actual domain noun
------------------------------------------------------------
The H1 headline MUST name the product/service noun from the research
(coffee/café/bean/brew | plate/menu/kitchen | workout/training | cut/style/salon |
 stay/trip/room | home/listing/keys | paw/pet | ring/vows | car/ride).

FORBIDDEN hero H1 openers (these are template boilerplate — always reject):
  ✗ "Build something remarkable"
  ✗ "Grow your business"
  ✗ "Transform your workflow"
  ✗ "Welcome to {Brand}"
  ✗ "Your all-in-one platform"
  ✗ "The future of {category}"
  ✗ "Unlock your potential"
  ✗ "Take your {X} to the next level"
  ✗ Any headline that could appear on a totally unrelated business unchanged.

GOOD examples by domain:
  Coffee shop     → "Your morning ritual, perfected" | "Small-batch coffee, big-city mornings"
  Restaurant      → "Seasonal plates, rooted in the coast"
  Fitness studio  → "Strength is built one rep at a time"
  Salon / spa     → "A quieter kind of beautiful"
  Hotel / travel  → "Stay like you belong here"
  Real estate     → "Find the keys to your next chapter"
  B2B SaaS        → "Ship invoices in 60 seconds, not 6 days"

Sub-headline (the paragraph under H1) must also reference at least one domain-specific
word (beans, crema, farm, grind | seasonal, local, chef | reps, coach, PR | etc.).

------------------------------------------------------------
2) HERO IMAGERY — mandatory for consumer domains
------------------------------------------------------------
For food / coffee / restaurant / bakery / fitness / salon / spa / retail / travel /
real-estate / hospitality / automotive / pet / wedding / beauty / event venue:
the hero MUST include real imagery. Text-only heroes are FORBIDDEN for these domains.

Pick ONE of these hero layouts:

  a) SPLIT SCREEN (recommended default):
     <section className="relative min-h-[90vh] grid md:grid-cols-2 gap-12 items-center
                         container mx-auto px-6 py-20">
       <div>{/* badge, H1, sub, CTAs, social-proof row */}</div>
       <div className="relative aspect-[4/5] rounded-3xl overflow-hidden shadow-2xl">
         <img src="https://images.unsplash.com/photo-..." alt="..." className="w-full h-full object-cover" />
         {/* optional floating stat card absolute -bottom-6 -left-6 */}
       </div>
     </section>

  b) FULL-BLEED with DARK overlay (cinematic):
     <section className="relative min-h-[90vh] flex items-center overflow-hidden">
       <img src="..." alt="" className="absolute inset-0 w-full h-full object-cover" />
       {/* DARK overlay — the photo must remain visible + readable, never washed out */}
       <div className="absolute inset-0 bg-gradient-to-t from-black/80 via-black/40 to-black/20" />
       <div className="relative z-10 container mx-auto px-6 max-w-2xl text-white">
         {/* H1 in text-white, sub in text-white/80, CTAs with glass or brand bg */}
       </div>
     </section>

     OVERLAY FORBIDDEN LIST (these wash out the photo and produce "template" output):
       ✗ bg-background/95, bg-background/90, bg-white/80
       ✗ from-background/95 (light overlays obscure the photo instead of darkening it)
       ✗ bg-black/10 on a bright photo (not enough contrast for white text to read)
     USE INSTEAD:
       ✓ bg-black/40 for even darkening on busy photos
       ✓ bg-gradient-to-t from-black/80 via-black/40 to-transparent (cinematic)
       ✓ bg-gradient-to-r from-black/70 via-black/30 to-transparent (text on left)

  c) TEXT + FLOATING IMAGE CARD (tilted, with shadow):
     image wrapped in rotate-2 shadow-2xl, absolute decorative orbs behind.

  d) BENTO HERO (editorial / award-site feel):
     <section className="container mx-auto px-6 py-16 grid md:grid-cols-6 md:grid-rows-3 gap-4 min-h-[85vh]">
       <div className="md:col-span-4 md:row-span-2 flex flex-col justify-end p-10 rounded-3xl bg-muted">
         {/* H1, sub, CTAs */}
       </div>
       <div className="md:col-span-2 md:row-span-3 rounded-3xl overflow-hidden">
         <img src="..." className="w-full h-full object-cover" />
       </div>
       <div className="md:col-span-2 rounded-3xl p-6 bg-primary text-primary-foreground">{/* stat / badge */}</div>
       <div className="md:col-span-2 rounded-3xl overflow-hidden"><img src="..." /></div>
     </section>

ASYMMETRY IS MANDATORY — FORBIDDEN HERO LAYOUTS:
  ✗ Centered H1 + centered sub + centered CTAs all stacked over a full-bleed photo
    with NOTHING on the left/right sides. This is the #1 "template output" giveaway.
  ✗ H1 dead-centered with two CTAs centered below it and no imagery/cards/stats
    breaking the symmetry.
  ✗ Full-bleed photo behind centered text with a light overlay (bg-background/90)
    that washes out the photo to near-white.

WHEN A hero_image_url IS PROVIDED, THE HERO MUST HAVE STRUCTURAL ASYMMETRY:
  - Use a 2-column grid (text | image) OR a bento grid (not centered-stack).
  - At least ONE floating/absolute element breaks the grid
    (rotated polaroid, floating stat card, signature-motif SVG, handwritten label).
  - Text block is left-aligned or right-aligned, NEVER center-aligned text with
    mx-auto on a photo hero.
  - The image occupies a meaningful portion of the viewport width (≥ 40%) —
    not just a small decorative thumbnail.

BEFORE committing any hero, self-check:
  □ Does the text block sit on one SIDE, not dead center?
  □ Is the image a real visible element (not hidden behind a light overlay)?
  □ Is there at least ONE asymmetric accent (floating card, tilted image, motif)?
  If any answer is NO, redesign before writing the JSX.

USE THESE UNSPLASH PHOTO URLs (exact, not /random):
  Coffee / espresso   → https://images.unsplash.com/photo-1509042239860-f550ce710b93
  Coffee shop interior→ https://images.unsplash.com/photo-1554118811-1e0d58224f24
  Latte art           → https://images.unsplash.com/photo-1517231925375-bf2cb42917a5
  Plated food         → https://images.unsplash.com/photo-1414235077428-338989a2e8c0
  Restaurant          → https://images.unsplash.com/photo-1517248135467-4c7edcad34c4
  Bakery              → https://images.unsplash.com/photo-1509440159596-0249088772ff
  Gym / weights       → https://images.unsplash.com/photo-1540497077202-7c8a3999166f
  Yoga / pilates      → https://images.unsplash.com/photo-1544367567-0f2fcb009e0b
  Salon / hair        → https://images.unsplash.com/photo-1560066984-138dadb4c035
  Spa                 → https://images.unsplash.com/photo-1540555700478-4be289fbecef
  Hotel / travel      → https://images.unsplash.com/photo-1488085061387-422e29b40080
  Real estate         → https://images.unsplash.com/photo-1560518883-ce09059eeffa
  Dog / pet           → https://images.unsplash.com/photo-1450778869180-41d0601e046e
  Wedding             → https://images.unsplash.com/photo-1519741497674-611481863552
  Car / automotive    → https://images.unsplash.com/photo-1492144534655-ae79c964c9d7

Append `?auto=format&fit=crop&w=1600&q=80` to every Unsplash URL for performance.

IMAGERY IN OTHER SECTIONS — also required for consumer domains:
  - Menu / products: every item card must have a real photo (aspect-[4/3] object-cover rounded-xl)
  - Locations: each card must show a real interior/exterior photo, NOT just a pin icon
  - About / Story: at least one team or space photo
  - Testimonials: avatar photos (already good — keep)

For B2B SaaS: product screenshot mockup (dashboard UI) replaces the photo. Never use
an empty text-only hero for a B2B app either.

------------------------------------------------------------
3) SECTION BACKGROUND ROTATION — non-negotiable
------------------------------------------------------------
A landing page with 5+ sections MUST rotate backgrounds. At most 2 consecutive
sections may share the same background. At least 3 distinct bg treatments per page.

Valid section background palette (mix at least 3 of these):
  bg-background                                          → default / neutral
  bg-muted/40                                            → subtle off-tone
  bg-card                                                → slightly elevated
  bg-gradient-to-br from-primary/5 via-background to-accent/5   → soft mesh
  bg-primary text-primary-foreground                     → dark-on-brand (for testimonials or final CTA)
  bg-[url('...unsplash...')] bg-cover bg-center relative, with an
    absolute inset-0 bg-background/85 overlay inside                → imagery-backed

At least ONE section per page MUST be visually distinct (dark/branded or image-backed).
Testimonials and the final CTA are the natural candidates for the dark/brand section.

FORBIDDEN: every section on the page has the same bg class. That's a template output.

------------------------------------------------------------
4) AT LEAST ONE ASYMMETRIC SECTION — non-negotiable
------------------------------------------------------------
Do NOT build every section as a 3- or 4-column grid of identically-sized cards.
At least ONE of Features / About / Menu / Services must use an asymmetric layout.

BENTO GRID (preferred, modern):
  <div className="grid grid-cols-1 md:grid-cols-3 md:grid-rows-2 gap-4">
    <Card className="md:col-span-2 md:row-span-2 ...">{/* hero feature — large, with image */}</Card>
    <Card>{/* small */}</Card>
    <Card>{/* small */}</Card>
    <Card className="md:col-span-2">{/* wide */}</Card>
  </div>

SPLIT LAYOUT (image + stacked features):
  <div className="grid md:grid-cols-2 gap-12 items-center">
    <div className="relative aspect-[4/5] rounded-3xl overflow-hidden">
      <img src="..." className="w-full h-full object-cover" />
    </div>
    <div className="space-y-8">
      {/* 3-4 feature rows, each with icon + h3 + description */}
    </div>
  </div>

A page where every section is a perfectly symmetric 3- or 4-col grid is "template output".

------------------------------------------------------------
5) CARD DEPTH SYSTEM — pick a named style, be consistent
------------------------------------------------------------
Every card on the page must use ONE of these three styles. Do NOT invent custom
card class combos per-component — it produces visual inconsistency.

SOFT (default, most cards):
  className="rounded-2xl border border-border/60 bg-card p-8 shadow-sm
             hover:shadow-xl hover:-translate-y-0.5 transition-all duration-200"

GLASS (for overlays on image sections, or cards on dark/branded bg):
  className="rounded-2xl border border-white/10 bg-white/5 backdrop-blur-md p-8
             text-white shadow-[0_8px_32px_rgb(0_0_0/0.12)]"

FEATURED (exactly ONE per section max — the "hero" card of a bento):
  className="rounded-2xl border border-primary/30 bg-gradient-to-br from-primary/10 via-card to-accent/5
             p-10 shadow-2xl shadow-primary/10"

FORBIDDEN:
  ✗ Flat borderless rectangles with no shadow
  ✗ 5 different card shapes on the same page
  ✗ Cards with neither shadow nor border (they vanish into the bg)

------------------------------------------------------------
6) LOCATIONS / CONTACT SECTION — never pin-icon-only
------------------------------------------------------------
If the project has multiple physical locations or a contact section with an address,
each location card MUST show either:
  a) A real exterior/interior photo (Unsplash coffee-shop / restaurant / gym url), OR
  b) An embedded Google Maps iframe:
     <iframe src="https://www.google.com/maps/embed?pb=..." className="w-full h-64
       rounded-xl border-0" loading="lazy" />

NEVER show a giant <MapPin /> icon alone as a placeholder for an actual map.
That is the #1 giveaway that the page is AI-generated template output.

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
FEEDBACK PATTERNS (non-negotiable)
====================================
After EVERY user action (create/update/delete/submit):
  - Success: useToast() — "✓ {Entity} created successfully"
  - Error: useToast({ variant: "destructive" }) — "Failed to save. Please try again."
  - Delete: Always show AlertDialog confirmation first, then proceed

Mutations must show BOTH a disabled state AND a loading spinner on the submit button.
Never silently succeed/fail — always surface feedback.

====================================
DATA VISUALIZATION SELECTION
====================================
Choose chart type by data shape:
  KPI over time → LineChart (smooth, area fill with 10% opacity)
  Category comparison → BarChart (horizontal for many items)
  Part-to-whole → DonutChart (max 6 segments)
  Distribution → AreaChart with gradient fill
  Always use CSS variable colors: fill="hsl(var(--primary))" etc.
  Always include a legend and labeled axes.
  Wrap charts in Card with title + time-period selector.

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
"""


# Phase 3 (completeness + polish) — fills gaps, builds 404, ensures every
# schema page exists. Lighter than Phase 2: we focus on what's missing.
PHASE_APPENDIX_COMPLETENESS = """

====================================
LOADING / EMPTY / ERROR (MANDATORY)
====================================
EVERY new component fetching data must handle all THREE states:

LOADING: Skeleton matching content shape (animate-pulse bg-muted rounded)
EMPTY: Centered icon + "No {entity} found" + action button
ERROR: Alert icon + "Something went wrong" + retry button

====================================
FEEDBACK PATTERNS (non-negotiable)
====================================
After EVERY user action (create/update/delete/submit):
  - Success: useToast() — "✓ {Entity} created successfully"
  - Error: useToast({ variant: "destructive" }) — "Failed to save. Please try again."
  - Delete: Always show AlertDialog confirmation first, then proceed

====================================
DESIGN SYSTEM (critical for consistency)
====================================
If the project has src/lib/design-system.js, ALL new components MUST:
  import { ds } from '@/lib/design-system'
  — use ds.card, ds.badge[status], ds.pageAnimation, ds.sectionSpacing, ds.maxWidth
  — match the card + badge + animation style already used in Phases 1–2.

====================================
POLISHING (non-negotiable)
====================================
- EMPTY STATES: Icon + message + action button. Never empty white space.
- HOVER: Every interactive element has hover state.
- MOCK DATA: Realistic names, numbers, dates, statuses. Never lorem ipsum.
- 404 PAGE: Friendly message, illustration or icon, link back home — NEVER generic stub.
- Any new page imports must resolve. If you reference a file, CREATE it in the same response.
"""


def _system_prompt_for_phase(phase: int) -> str:
    """System prompt — stable across every phase so Anthropic prompt caching
    hits on all 4 calls (a per-phase system prompt invalidated the cache and
    erased ~2–3× savings on input tokens). Phase-specific rules now live in
    the user message via `_phase_rules_prefix(phase)`.
    """
    del phase  # unused; kept for call-site compatibility
    return SYSTEM_PROMPT_CORE


def _phase_rules_prefix(phase: int) -> str:
    """Return the phase-specific rules block to prepend to the user prompt.

    Goes in the user message (not system) so the system prompt stays stable
    for caching. Still gives the model focused rules for the phase:
    Phase 1 sees CSS/theme/nav, Phase 2 sees content/API/feedback patterns,
    Phase 3 sees the lighter completeness checklist.
    """
    if phase == 1:
        return PHASE_APPENDIX_FOUNDATION
    if phase == 3:
        return PHASE_APPENDIX_COMPLETENESS
    return PHASE_APPENDIX_CONTENT


# Back-compat alias — any legacy reference to SYSTEM_PROMPT gets a reasonable
# default (CORE + full content appendix, matches previous superset behaviour).
SYSTEM_PROMPT = SYSTEM_PROMPT_CORE + PHASE_APPENDIX_FOUNDATION + PHASE_APPENDIX_CONTENT + PHASE_APPENDIX_COMPLETENESS


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


def _phase_token_budget(
    schema: dict,
    phase: int,
    archetype: str,
) -> tuple[int, bool]:
    """Calculate max_tokens and whether extended output beta is needed.

    Purely complexity-driven — based on page/entity/feature count from the
    Gemini schema, NOT on archetype labels. A simple 3-page admin gets the
    same small budget as a simple consumer site; a 15-entity CRM gets more.

    Complexity score = n_pages + (n_entities × 2) + n_features
    Entities weighted ×2 because each needs a list page + form component.

    Returns (max_tokens, extended_output).
    extended_output=True adds anthropic-beta: output-128k-2025-02-19,
    enabling up to 128K output for very large projects.
    """
    n_pages    = len(schema.get("pages", []))
    n_entities = len(schema.get("entities", []))
    n_features = len(schema.get("features", []))
    complexity = n_pages + (n_entities * 2) + n_features

    # Claude Sonnet's default output cap is 8 192 tokens.
    # The output-128k-2025-02-19 beta unlocks up to 128 K tokens.
    # Every phase generates multiple full React/Vue files — always well above
    # 8 192 tokens — so the beta header is ALWAYS required.
    # Complexity still drives the actual max_tokens ceiling: smaller projects
    # get a smaller ceiling (faster response), larger ones get more room.

    # claude-sonnet-4-6 native output cap is 64K tokens.
    # All phases use the full 64K for Phase 2 (heaviest) and 64K for Phase 1/3
    # so complex consumer sites / magazines / admin apps never get truncated.

    # Landing pages: one scrollable page but still needs full sections.
    if archetype == "single_page_landing":
        if phase == 1: return (32000, True)   # theme + shell + nav
        if phase == 2: return (64000, True)   # all sections — needs full budget
        return (48000, True)                  # phase 3 polish

    if phase == 1:
        # Foundation: theme, layout, nav, main page, router — 5-10 files
        return (64000, True)

    elif phase == 2:
        # Content: sections / CRUD modules — always the heaviest phase
        # Always use full native 64K — a rich magazine/CRM needs every token
        return (64000, True)

    else:  # phase 3 — extra pages, 404, polish
        return (64000, True)


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

    # ── Step 1: Classify app type (AI-powered) ──
    # Gemini Flash classifies the description accurately so the research prompt
    # outputs the correct structural blocks (===SECTIONS=== vs ===PAGES=== vs
    # ===ENTITIES===). A wrong initial type causes the research to output the
    # wrong schema structure even if ===CLASSIFICATION=== is later corrected.
    from knowledge.loader import classify_project_type_ai
    _classification = await classify_project_type_ai(description, gemini_key)
    app_type = _classification["app_type"]
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
    
    # ── Step 3: Gemini research (all project types) ──
    await _ws_send(websocket, "progress", "🔬 Researching real products in this domain...")
    research_quality = "full"

    # Research cache keyed by md5(description+stack)[:14] — avoids re-researching
    # identical tasks (e.g. retries, repeated demos).  Stored in /tmp, auto-evicted
    # by OS.  Cache entries expire after 1 hour via mtime check.
    import hashlib as _hashlib
    import time as _time_cache
    _cache_dir = "/tmp/lucid_research_cache"
    _cache_key = _hashlib.md5(
        f"{description.strip().lower()}|{stack}|{_layout_archetype}".encode()
    ).hexdigest()[:14]
    _cache_path = f"{_cache_dir}/{_cache_key}.txt"
    _cache_max_age = 3600  # 1 hour

    research = None
    _design: dict | None = None  # Set by Design Director (below) on cache-miss.
    try:
        import os as _os_cache
        _os_cache.makedirs(_cache_dir, exist_ok=True)
        if _os_cache.path.exists(_cache_path):
            _age = _time_cache.time() - _os_cache.path.getmtime(_cache_path)
            if _age < _cache_max_age:
                with open(_cache_path, "r", encoding="utf-8") as _cf:
                    _cached = _cf.read()
                if len(_cached) > 500:
                    research = _cached
                    research_quality = "cached"
                    logger.info("Research cache HIT (%s, %.0fs old)", _cache_key, _age)
                    await _ws_send(websocket, "progress", "⚡ Research loaded from cache")
    except Exception:
        pass  # Cache miss is fine — just proceed with fresh research

    if research is None:
        try:
            research = await gemini_deep_research(
                description, _classification, stack, gemini_key, websocket,
            )
            if len(research) < 200:
                research_quality = "minimal"
                logger.warning("Research returned minimal content (%d chars)", len(research))
                await _ws_send(websocket, "warning", "⚠️ Research returned limited results — generation will use basic patterns")
            else:
                # ── Vision-grounded enrichment ──
                # Fetch screenshots of the reference sites Gemini just named,
                # then ask Gemini Pro to critique them visually. The resulting
                # ===VISUAL_DNA=== block gets appended to the research text so
                # Claude sees concrete visual patterns, not just verbal ones.
                # FAIL-SOFT: empty string on any error, pipeline proceeds unchanged.
                try:
                    from app.services.vision_research import vision_enrich_research
                    _visual_dna = await vision_enrich_research(
                        research_text=research,
                        description=description,
                        domain=_domain,
                        gemini_key=gemini_key,
                        websocket=websocket,
                    )
                    if _visual_dna:
                        research = research + _visual_dna
                        logger.info("Research enriched with VISUAL_DNA (+%d chars)", len(_visual_dna))
                except Exception as _vision_exc:
                    logger.warning("Vision enrichment failed (non-fatal): %s", _vision_exc)

                # ── Design Director (Option: Design System First) ──
                # One dedicated Claude call that designs a bespoke, validated
                # design system BEFORE code generation. Replaces the weak verbal
                # design hints from Gemini research with enforced tokens
                # (contrast-validated palette, musical type scale, grid-aligned
                # spacing, locked card + motion language). Downstream code-gen
                # phases read the same ===CSS_VARIABLES===/===FONTS===/===LAYOUT_BLUEPRINT===
                # blocks — no consumer changes needed.
                # FAIL-SOFT: returns None on failure, pipeline uses original research.
                try:
                    from app.services.design_system_builder import (
                        build_design_system,
                        inject_design_blocks,
                    )
                    _vibe_from_research = _extract_research_section(research, "===VIBE===", max_chars=400)
                    _copy_tone_from_research = _extract_research_section(research, "===COPY_TONE===", max_chars=400)
                    # Brand name: Claude can infer it from description inside the call;
                    # passing description as-is avoids brittle regex extraction here.
                    _design = await build_design_system(
                        description=description,
                        domain=_domain,
                        brand_name="",  # inferred from description
                        copy_tone=_copy_tone_from_research,
                        layout_archetype=_layout_archetype,
                        vibe=_vibe_from_research,
                        api_key=api_key,
                        websocket=websocket,
                    )
                    if _design:
                        research = inject_design_blocks(research, _design)
                        logger.info(
                            "Design Director injected — name=%s archetype=%s",
                            _design.get("design_system_name"),
                            _design.get("archetype"),
                        )
                except Exception as _dd_exc:
                    logger.warning("Design Director failed (non-fatal): %s", _dd_exc)

                # Save to cache for next time (includes VISUAL_DNA + DESIGN_DIRECTOR if present)
                try:
                    with open(_cache_path, "w", encoding="utf-8") as _cf:
                        _cf.write(research)
                    logger.info("Research cached (%s, %d chars)", _cache_key, len(research))
                except Exception:
                    pass
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

    # ── Archetype-sliced manifest ──
    # The raw manifest covers every layout type the template supports (admin +
    # landing + blog bits). Passing the whole thing to every phase wastes
    # tokens and invites the model to mix paradigms (sidebar components on a
    # landing page, section components on an admin). Slice once here and use
    # in all prompts. Falls back to head-truncated raw manifest if slicing
    # would gut it. Cap drops from 15K → 8K.
    manifest_sliced = _slice_manifest_for_archetype(manifest, _layout_archetype, max_chars=8000)
    logger.info(
        "Manifest sliced for %s: %d → %d chars",
        _layout_archetype, len(manifest), len(manifest_sliced),
    )

    # ── Distilled research block ──
    # Gemini returns 10–20K of raw research with multiple ===SECTIONS===.
    # Dumping that raw into every phase prompt eats tokens and dilutes signal.
    # Distill once into a compact bullet plan capped at ~8K, structured by
    # known section headers so the model sees the most important bits first.
    research_distilled = _distill_research(research)
    logger.info(
        "Research distilled: %d → %d chars",
        len(research), len(research_distilled),
    )

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
        schema_to_pages_spec,
        schema_to_extra_pages_spec,
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
    schema_pages_spec = schema_to_pages_spec(project_schema)       # consumer: pages + sections
    schema_extra_pages_spec = schema_to_extra_pages_spec(project_schema)  # admin: non-entity pages
    
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

    # ── Step 3d: Deterministic MarketingHeader.jsx write ──
    # The LLM occasionally preserves the cloned template's default navbar
    # (Sign In / Get Started, no logo) despite explicit Phase 1 instructions
    # to rewrite it. Emitting the file directly from brand_mark + navigation
    # removes that entire failure mode. Only runs when:
    #   - Stack is Next.js (MarketingHeader.jsx path convention)
    #   - The file already exists (template has it; admin-only templates won't)
    #   - We have a brand_mark (from Design Director dict or parsed from research)
    # FAIL-SOFT on any exception — LLM still handles it in Phase 1.
    _det_header_written = False
    _marketing_header_rel = "src/components/layout/MarketingHeader.jsx"
    _stack_lower = (stack or "").lower()
    if ("next" in _stack_lower or "nextjs" in _stack_lower):
        _header_abs = os.path.join(workspace_path, _marketing_header_rel)
        if os.path.isfile(_header_abs):
            try:
                from app.services.marketing_header_builder import (
                    build_marketing_header_jsx,
                    parse_brand_mark_from_research,
                )
                _brand_mark = (_design or {}).get("brand_mark") if _design else {}
                if not _brand_mark:
                    _brand_mark = parse_brand_mark_from_research(research or "")
                _nav_groups = project_schema.get("navigation", []) or []
                _schema_brand_name = (
                    project_schema.get("brand", {}).get("name")
                    or description[:40].strip()
                    or "Brand"
                )
                if _brand_mark and _nav_groups:
                    _header_jsx = build_marketing_header_jsx(
                        brand_name=_schema_brand_name,
                        brand_mark=_brand_mark,
                        navigation=_nav_groups,
                        domain=_domain,
                        archetype=_layout_archetype,
                    )
                    with open(_header_abs, "w", encoding="utf-8") as _hf:
                        _hf.write(_header_jsx)
                    _det_header_written = True
                    logger.info(
                        "Deterministic MarketingHeader written: brand=%s domain=%s treatment=%s (%d chars)",
                        _schema_brand_name,
                        _domain,
                        _brand_mark.get("treatment"),
                        len(_header_jsx),
                    )
                    await _ws_send(
                        websocket,
                        "progress",
                        f"🎯 Wrote MarketingHeader (brand: {_schema_brand_name})",
                    )
                else:
                    logger.info(
                        "Deterministic MarketingHeader skipped — brand_mark=%s nav_groups=%d",
                        bool(_brand_mark), len(_nav_groups),
                    )
            except Exception as _hdr_exc:
                logger.warning(
                    "Deterministic MarketingHeader write failed (non-fatal): %s",
                    _hdr_exc,
                )

    total_files = []

    # Safe defaults for variables extracted inside the plan try block.
    # If the try block throws before setting them, phase assembly at line ~2930
    # still has valid strings instead of NameError.
    _h_font = ""
    _b_font = ""
    _primary_hsl = ""
    _accent_hsl = ""
    _bg_hsl = ""
    _fg_hsl = ""
    _vibe = ""
    _radius = "0.5rem"
    _card_cls = ""
    _brand_name = description[:30]
    _plan_design_system_name = "Clean Slate"

    # ════════════════════════════════════════════════════════════
    #  STEP A — Emit rich plan to chat (before any design work)
    #  User sees the full structural plan (sections/pages/entities)
    #  THEN Phase 4 (Stitch) runs, THEN coding starts.
    # ════════════════════════════════════════════════════════════
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
    # as consumer, trust the schema (Gemini may have built admin structure regardless).
    # Never fires for landing pages — the user explicitly requested a landing page.
    if not _is_admin and not _is_landing:
        _si_kpis = project_schema.get("dashboard", {}).get("kpis", [])
        _si_crud = [p for p in project_schema.get("pages", []) if p.get("type") in ("crud_list", "crud_form")]
        if _si_kpis or len(_si_crud) >= 2:
            logger.info("Schema has admin structure — reclassifying layout to admin_dashboard")
            _is_admin = True
            _is_consumer = False
            _is_blog = False

    # Build a schema-driven design spec from Gemini research.
    # Every variable here comes from the project-specific schema, making each
    # generation unique.
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

   HARD BAN on SaaS template vocabulary for non-SaaS domains (restaurant, café, coffee,
   bakery, fitness, gym, yoga, salon, spa, hotel, hospitality, travel, retail,
   real estate, automotive, wedding, pet, nonprofit, portfolio, agency):
     ✗ NEVER include "About", "Features", "Pricing", "How It Works", "Sign In",
       "Log In", "Get Started", "Dashboard", "Integrations", "Changelog"
       as nav items or CTAs on these domains.
     ✗ These are SaaS-tool vocabulary. They make a physical-business site look
       like a generic template.

   Required behavior by domain:
     - Restaurant / café / bakery  → Menu, Reservations, Locations, Private Events, Gift Cards, Story
     - Fitness / gym / yoga / spa  → Classes, Trainers, Schedule, Membership, Locations, Community
     - Hotel / travel / rental     → Rooms, Experiences, Dining, Location, Offers, Book
     - Salon / barber              → Services, Book Now, Stylists, Locations, Gift Cards
     - Real estate                 → Buy, Sell, Rent, Neighborhoods, Agents, Insights
     - Automotive dealer           → Inventory, New, Pre-owned, Finance, Service, About
     - Portfolio / agency          → Work, Services, Process, About, Journal, Contact
     - Wedding / event venue       → Packages, Venue, Gallery, Pricing, Contact
     - Blog / magazine             → Latest, Topics, Newsletter, Authors, About, Shop
     - Nonprofit                   → Mission, Programs, Impact, Get Involved, Donate

   If the NAVIGATION STRUCTURE block above is empty or missing, DERIVE the nav
   items from the project description + the domain list above. NEVER fall back
   to "About / Features / Contact / Sign In / Get Started" on a physical business.

   Header CTA button — domain-appropriate verb:
     Restaurant      → "Reserve a Table"
     Coffee / café   → "Order Online" or "Find a Café"
     Fitness / gym   → "Book a Class" or "Start Free Week"
     Hotel           → "Book Your Stay"
     Salon / spa     → "Book Appointment"
     Real estate     → "Browse Listings"
     Portfolio       → "Start a Project" or "Let's Talk"
     B2B SaaS (only) → "Start Free" / "Get Demo"

4. LAYOUT COMPONENTS — FULLY REWRITE Header/Footer (and Sidebar for admin) for this project:
   - Blog/content sites: sticky top nav with logo, nav links, search icon, CTA button; rich footer with columns
   - Admin panels: sidebar (w-64, brand logo, nav groups, user profile area) + top header with search+notifications
   - DO NOT copy template defaults — create a UNIQUE layout matching the research
{"   - ⚠️ MarketingHeader.jsx HAS ALREADY BEEN WRITTEN deterministically. DO NOT regenerate src/components/layout/MarketingHeader.jsx. Skip it entirely — do NOT include it in write_project_files. The Footer and Sidebar (if admin) are still yours to build." if _det_header_written else ""}

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
{research_distilled}

TEMPLATE MANIFEST:
{manifest_sliced}

FILE TREE:
{file_tree[:2000]}

{stack_rules}
{template_context}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
UI DESIGN SPEC (from research)
⚠️  FOR VISUAL/UI IMPROVEMENTS ONLY.
    Do NOT change pages, routes, entities, or nav items — those are fixed above.
    Only use this to improve: card styles, spacing ratios, color usage,
    typography hierarchy, button shapes, shadow/border patterns.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{stitch_instruction}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Call the write_project_files tool with ALL files.
"""
    
    PHASE1_MAX_TOKENS, PHASE1_EXTENDED = _phase_token_budget(project_schema, 1, _layout_archetype)
    logger.info("Phase 1 budget: max_tokens=%d extended=%s (archetype=%s, complexity score derived from schema)",
                PHASE1_MAX_TOKENS, PHASE1_EXTENDED, _layout_archetype)
    result1 = await call_claude_for_json(
        system_prompt=_system_prompt_for_phase(1),
        user_prompt=_phase_rules_prefix(1) + "\n" + phase1_prompt,
        api_key=api_key,
        websocket=websocket,
        max_tokens=PHASE1_MAX_TOKENS,
        model=MODEL,
        extended_output=PHASE1_EXTENDED,
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
        _api_cfg    = project_schema.get('api_config', {})
        _api_env    = _api_cfg.get('base_url_env', 'VITE_API_URL')
        _api_default = _api_cfg.get('base_url_default', 'http://localhost:3001')

        phase2_instruction = f"""Generate ALL pages and domain-specific components for this {_layout_archetype.replace('_', ' ')} ({_domain} domain).

THIS IS A PUBLIC-FACING {_layout_archetype.replace('_',' ').upper()} — NOT an admin panel.
Use top-nav layout (no sidebar). Pages are customer-facing, NOT management dashboards.

{schema_pages_spec or f"PAGES TO BUILD (from research — every single one):\\n(build all domain-specific pages: home, about, services, contact and every domain-specific page from research)"}

For EACH page, build every section listed above completely:
- Full domain-specific content (real copy, realistic data, proper images from picsum.photos)
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
UI DESIGN SPEC (from research)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
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
UI DESIGN SPEC (from research)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{stitch_instruction}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"""
    else:
        # Build the UI polish block for landing sections
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

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SINGLE-PAGE LANDING — HARD RULES (non-negotiable)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
This is ONE scrollable page. Everything lives on the root route.

DO create:
  ✓ src/app/page.jsx (Next.js) OR src/App.jsx (React/Vue) — the single page that renders all sections
  ✓ src/components/sections/<SectionName>.jsx — one file per section (Hero, Menu, About, Contact, etc.)

DO NOT create any of the following (these would break the single-page model):
  ✗ src/app/about/page.jsx, src/app/menu/page.jsx, src/app/contact/page.jsx — NO sub-route pages
  ✗ src/pages/About.jsx, src/pages/Menu.jsx — NO React-Router route files
  ✗ Any <Link to="/about">, <Link href="/contact"> — navigation must be ANCHOR links (#about, #menu, #contact)

Navigation rule:
  Header nav items MUST be <a href="#section-id"> anchor links that scroll to the matching section
  on the SAME page. Never use Next.js <Link href="/route"> or React Router <Link to="/route"> for
  nav items in a single-page landing.

If the schema's navigation array has items labeled "Menu"/"About"/"Contact"/etc., they become
SECTIONS on the landing page (e.g. <MenuSection id="menu" />) — NOT separate pages.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{_stitch_ui_polish}"""
    
    phase2_prompt = f"""PHASE 2 OF 3 — CONTENT FILES

The foundation is already built (see file tree below). DO NOT regenerate foundation files.
{phase2_instruction}

PROJECT: {description}
APP TYPE: {app_type}
STACK: {stack}

DESIGN SYSTEM FROM RESEARCH:
{research_distilled}

TEMPLATE MANIFEST:
{manifest_sliced}

CURRENT FILE TREE (foundation already written):
{file_tree_2[:2000]}

{stack_rules}
{skills}

Call the write_project_files tool with ALL files.
"""
    
    # Phase 2 token budget.
    PHASE2_MAX_TOKENS, PHASE2_EXTENDED = _phase_token_budget(project_schema, 2, _layout_archetype)
    logger.info("Phase 2 budget: max_tokens=%d extended=%s", PHASE2_MAX_TOKENS, PHASE2_EXTENDED)

    # ── Admin batching ─────────────────────────────────────────────────────
    # Heavy admin projects (>4 entities) reliably truncate a single 64K call.
    # Split entities into chunks of 3 and run the batches in parallel: each
    # batch gets a focused prompt + smaller budget, and wall-time is bounded
    # by the slowest batch instead of the sum.
    _entities_list = project_schema.get("entities", []) if _is_admin else []
    ADMIN_BATCH_THRESHOLD = 4
    ADMIN_ENTITIES_PER_BATCH = 3
    _use_admin_batching = _is_admin and len(_entities_list) > ADMIN_BATCH_THRESHOLD

    if _use_admin_batching:
        batches = [
            _entities_list[i:i + ADMIN_ENTITIES_PER_BATCH]
            for i in range(0, len(_entities_list), ADMIN_ENTITIES_PER_BATCH)
        ]
        logger.info(
            "Phase 2 batching: %d entities split into %d parallel batches (<=%d each)",
            len(_entities_list), len(batches), ADMIN_ENTITIES_PER_BATCH,
        )
        await _ws_send(
            websocket,
            "progress",
            f"⚙️  Generating {len(_entities_list)} entities in {len(batches)} parallel batches...",
        )

        async def _run_admin_batch(batch_idx: int, entity_batch: list) -> dict | None:
            batch_schema = {**project_schema, "entities": entity_batch}
            batch_entity_spec = schema_to_entity_spec(batch_schema)
            batch_names = ", ".join(e.get("name", "?") for e in entity_batch)

            batch_instruction = f"""Generate CRUD feature modules for THIS BATCH of entities ONLY: {batch_names}

For EACH entity in this batch, create the COMPLETE feature folder:
  - services/[entity].service.js — REAL fetch() calls to the API (NOT hardcoded mock data)
  - hooks/use[Entity].js — React Query / Vue Query wrappers
  - pages/[Entity]ListPage — DataTable with schema-defined columns, actions, filters, search
  - pages/[Entity]FormPage — Form with schema-defined fields + validation (react-hook-form + zod)

IMPORTANT — ONLY generate files for the entities listed above in THIS batch.
Other entities are being generated in parallel — do NOT create files for them.

IMPORTANT — API-READY SERVICES:
  const API_URL = import.meta.env.{_api_env} || '{_api_default}';
  Services must use REAL fetch() calls. DO NOT hardcode mock data arrays.
  The mock data lives in db.json (already generated) and is served by json-server.
  Catch errors and return empty arrays on failure (graceful degradation).

IMPORTANT — DESIGN SYSTEM:
  Import {{ ds }} from '@/lib/design-system' in ALL components.
  Use ds.card for card wrappers, ds.badge[status] for status badges.

{batch_entity_spec}
{schema_api_spec}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
UI DESIGN SPEC (from research)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{stitch_instruction}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"""

            batch_prompt = f"""PHASE 2 OF 3 — CONTENT FILES (batch {batch_idx + 1}/{len(batches)})

The foundation is already built (see file tree below). DO NOT regenerate foundation files.
{batch_instruction}

PROJECT: {description}
APP TYPE: {app_type}
STACK: {stack}

DESIGN SYSTEM FROM RESEARCH:
{research_distilled}

TEMPLATE MANIFEST:
{manifest_sliced}

CURRENT FILE TREE (foundation already written):
{file_tree_2[:2000]}

{stack_rules}
{skills}

Call the write_project_files tool with ALL files for THIS batch only.
"""

            # Per-batch budget: 3 CRUD modules comfortably fit in 40K with headroom.
            # Smaller ceiling also means the model returns sooner when done.
            return await call_claude_for_json(
                system_prompt=_system_prompt_for_phase(2),
                user_prompt=_phase_rules_prefix(2) + "\n" + batch_prompt,
                api_key=api_key,
                websocket=websocket,
                max_tokens=40000,
                model=MODEL,
                extended_output=True,
            )

        batch_results = await asyncio.gather(
            *[_run_admin_batch(i, b) for i, b in enumerate(batches)],
            return_exceptions=True,
        )

        merged_files: list[dict] = []
        seen_paths: set[str] = set()
        failed_batches = 0
        for idx, res in enumerate(batch_results):
            if isinstance(res, Exception):
                logger.error("Phase 2 batch %d raised: %s", idx + 1, res)
                failed_batches += 1
                continue
            if not isinstance(res, dict) or not res.get("files"):
                failed_batches += 1
                continue
            for f in res["files"]:
                if not isinstance(f, dict):
                    continue
                path = f.get("path")
                if not path or path in seen_paths:
                    continue
                merged_files.append(f)
                seen_paths.add(path)

        if merged_files:
            written = write_files_from_json({"files": merged_files}, workspace_path)
            total_files += written
            status_msg = f"✅ Content: {len(written)} files across {len(batches)} parallel batches"
            if failed_batches:
                status_msg += f" ({failed_batches} batch(es) failed)"
            await _ws_send(websocket, "progress", status_msg)
        else:
            logger.error(
                "Phase 2 (batched) returned no files — all %d batches failed",
                len(batches),
            )
            await websocket.send_json({
                "type": "chat_message",
                "role": "system",
                "content": "⚠️ Phase 2 generated no files across all batches. Phase 3 will attempt to fill the gap.",
            })
    else:
        result2 = await call_claude_for_json(
            system_prompt=_system_prompt_for_phase(2),
            user_prompt=_phase_rules_prefix(2) + "\n" + phase2_prompt,
            api_key=api_key,
            websocket=websocket,
            max_tokens=PHASE2_MAX_TOKENS,
            model=MODEL,
            extended_output=PHASE2_EXTENDED,
        )
        if result2:
            written = write_files_from_json(result2, workspace_path)
            total_files += written
            await _ws_send(websocket, "progress", f"✅ Content: {len(written)} files")
        else:
            logger.error("Phase 2 (content) returned no files — likely truncated (budget: %d)", PHASE2_MAX_TOKENS)
            await websocket.send_json({
                "type": "chat_message",
                "role": "system",
                "content": "⚠️ Phase 2 generated no files (response was truncated). Phase 3 will attempt to fill the gap.",
            })
    
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
{research_distilled}

TEMPLATE MANIFEST:
{manifest_sliced}

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

{f'''SIDEBAR PAGES WITHOUT CRUD (build these as full feature pages — NOT stubs):
{schema_extra_pages_spec}
''' if schema_extra_pages_spec and _is_admin else ''}
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
{research_distilled}

TEMPLATE MANIFEST:
{manifest_sliced}

CURRENT FILE TREE (foundation + content already written):
{file_tree_3[:3000]}

{stack_rules}

Call the write_project_files tool with ALL files.
"""
    
    # Landing pages are fully covered in Phase 2 (all sections + completeness).
    # Skipping Phase 3 saves 60–90s on the most common generation type.
    if _is_landing:
        await _ws_send(websocket, "progress", "⚡ Landing page complete — skipping extra-pages phase")
        logger.info("Phase 3 skipped for single_page_landing (saves ~60-90s)")
    else:
        PHASE3_MAX_TOKENS, PHASE3_EXTENDED = _phase_token_budget(project_schema, 3, _layout_archetype)
        logger.info("Phase 3 budget: max_tokens=%d extended=%s", PHASE3_MAX_TOKENS, PHASE3_EXTENDED)
        # Phase 3 always uses Sonnet (MODEL). The previous Haiku downgrade on
        # consumer/blog saved ~30s but produced visibly weaker copy + layout
        # on exactly the pages (404, About, Contact, gallery detail) that
        # users hit most often on a public site.
        PHASE3_MODEL = MODEL
        result3 = await call_claude_for_json(
            system_prompt=_system_prompt_for_phase(3),
            user_prompt=_phase_rules_prefix(3) + "\n" + phase3_prompt,
            api_key=api_key,
            websocket=websocket,
            max_tokens=PHASE3_MAX_TOKENS,
            model=PHASE3_MODEL,
            extended_output=PHASE3_EXTENDED,
        )
        if result3:
            written = write_files_from_json(result3, workspace_path)
            total_files += written
            await _ws_send(websocket, "progress", f"✅ Pages: {len(written)} files")
        else:
            logger.warning("Phase 3 (extra pages) returned no files (budget: %d)", PHASE3_MAX_TOKENS)
            await _ws_send(websocket, "progress", "⚠️ Phase 3 skipped — proceeding with build...")
    
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
    # Landing pages: check-only (max_fix_attempts=0) — post-generation fixers already
    # catch the common issues, so skip the expensive Claude fix loop but still report errors.
    # Other projects: 1 fix attempt (Claude rewrites broken files once, then final build check).
    build_ok = await verify_and_fix_build(
        workspace_path=workspace_path,
        api_key=api_key,
        websocket=websocket,
        max_fix_attempts=MAX_FIX_ATTEMPTS,
    )

    # Signal actual build result so the orchestrator knows not to overwrite it.
    # The orchestrator sends phase 6 done/error based on this attribute.
    try:
        websocket._build_ok = build_ok
    except Exception:
        pass

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

