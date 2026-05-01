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
import random
import re
import subprocess
from typing import Optional

logger = logging.getLogger("lucid.project_generator")

# ── Plan confirmation system ─────────────────────────────────
# When a plan is emitted, we store an asyncio.Future here keyed by
# a STABLE string (chat_session_id when available, else f"ws-{id()}").
# A stable key survives websocket reconnects: the pipeline runs in the
# background while the user's tab refreshes, then a new ws connection
# can resolve the same future.
#   Future result: {"confirmed": True} or {"confirmed": False, "correction": "..."}
pending_plan_confirmations: dict[str, asyncio.Future] = {}

PLAN_CONFIRM_TIMEOUT_SECONDS = 300  # 5 minutes — abort if user doesn't confirm


def _confirmation_key(websocket, chat_session_id: str = "") -> str:
    """Build the stable confirmation-future key. Prefers chat_session_id."""
    return chat_session_id or f"ws-{id(websocket)}"


def register_plan_confirmation(key: str) -> asyncio.Future:
    """Register a pending plan confirmation under the given stable key."""
    loop = asyncio.get_event_loop()
    fut = loop.create_future()
    pending_plan_confirmations[key] = fut
    return fut


def resolve_plan_confirmation(key: str, result: dict):
    """Called by ws.py when user confirms or rejects the plan."""
    fut = pending_plan_confirmations.pop(key, None)
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

# Files that must NEVER be overwritten by the generator.
# Source of truth lives in ``project_writer`` — re-exported here so legacy
# imports (``from project_generator import PROTECTED_FILES``) keep working.
from app.services.project_writer import (
    PROTECTED_FILES,
    PROTECTED_DIRS,
    PROTECTED_UI_DIR,
    write_files_from_json,
    structural_sanity_check as _structural_sanity_check,
)

# Prompt input caps — anything a user (or the wizard header) can supply must
# be trimmed before it reaches a prompt, so a pasted 200KB README can't
# consume the entire context window and starve the instruction blocks.
_MAX_DESCRIPTION_CHARS = 10_000


def _normalize_prompt_input(value: Optional[str], *, max_chars: int) -> str:
    """Safe normalization for user-controlled prompt variables.

    - ``None`` → ``""``
    - strips leading / trailing whitespace
    - caps length at ``max_chars`` with a clear ``[...truncated]`` marker so
      downstream logs / debugging show where the snip happened
    """
    if not value:
        return ""
    s = str(value).strip()
    if len(s) <= max_chars:
        return s
    return s[:max_chars].rstrip() + "\n[...truncated]"


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


async def _emit_file_writes(
    websocket,
    paths: list[str],
    *,
    action: str = "write",
    workspace_dir: str | None = None,
    phase: str | None = None,
    phase_elapsed_ms: int | None = None,
) -> None:
    """Emit one ``file_write_event`` per written path.

    The frontend consumes this message to surface generated files in the
    Code tab and to render inline Git-style diffs. We include:
      • ``content`` — the file's post-write text (skipped for binary or
        files >256KB to avoid bloating the WS stream)
      • ``phase`` / ``phase_elapsed_ms`` — provenance + timing so the UI
        can show "this file took N ms (part of step5)"
      • ``size`` — byte count even when content is omitted
    """
    if not websocket or not paths:
        return
    import os as _os
    _MAX_INLINE_BYTES = 256 * 1024  # 256 KB cap on per-file payload
    for p in paths:
        payload: dict = {
            "type": "file_write_event",
            "filename": p,
            "action": action,
        }
        if phase is not None:
            payload["phase"] = phase
        if phase_elapsed_ms is not None:
            payload["phase_elapsed_ms"] = int(phase_elapsed_ms)

        # Best-effort file content read — never block the pipeline on IO errors.
        if workspace_dir:
            try:
                full_path = p if _os.path.isabs(p) else _os.path.join(workspace_dir, p)
                if _os.path.isfile(full_path):
                    size = _os.path.getsize(full_path)
                    payload["size"] = size
                    if size <= _MAX_INLINE_BYTES:
                        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                            payload["content"] = f.read()
                    else:
                        payload["content_truncated"] = True
            except Exception:
                pass

        try:
            await websocket.send_json(payload)
        except Exception:
            # Disconnection during a big batch shouldn't abort the pipeline
            return


# Domain keyword → evocative design-system name. Used by
# _generate_design_system_name as the LAST cosmetic fallback when neither
# the schema builder nor the Design Director produced a name. Module-level
# so it isn't rebuilt on every call.
_DOMAIN_DESIGN_NAMES: dict[str, str] = {
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

# Generic last-resort design-system names — picked at random when even the
# domain keyword search misses.
_DESIGN_NAME_FALLBACKS: tuple[str, ...] = (
    "Obsidian Canvas", "Minimal Grid", "Aurora Studio",
    "Quantum Form", "Prism Layout", "Signal Studio",
    "Apex Grid", "Lumen Form", "Contour Studio", "Slate Zero",
    "Eclipse Form", "Polar Grid", "Meridian Studio", "Zenith Canvas",
)


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
    # Search in domain first, then fall through to description
    search_text = domain.lower()
    for keyword, name in _DOMAIN_DESIGN_NAMES.items():
        if keyword in search_text:
            return name

    # Search the raw user description for stronger signal
    if description:
        desc_lower = description.lower()
        for keyword, name in _DOMAIN_DESIGN_NAMES.items():
            if keyword in desc_lower:
                return name

    # Fallback: derive from font
    if heading_font:
        font_short = heading_font.split()[0]
        return f"{font_short} Studio"

    # Color-vibe fallback — map HSL hue range to evocative names
    if primary_hsl:
        hue_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:deg|°)?", primary_hsl)
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
    return random.choice(_DESIGN_NAME_FALLBACKS)




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


# A legitimate manifest lists components, import paths, and conventions —
# always at least a couple of KB. Below this threshold something is wrong
# (wrong template cloned, empty placeholder, read error silently swallowed).
_MIN_MANIFEST_CHARS = 500


def _validate_manifest(manifest: str, workspace_path: str) -> Optional[str]:
    """Return an error string if the manifest looks missing/stubbed, else ``None``.

    Generation can still proceed without a manifest, but the output quality
    takes a measurable hit (broken imports, missing shadcn/ui usage). Surface
    the issue early so a botched template clone doesn't masquerade as a
    generator bug three phases later.
    """
    if not manifest:
        return f"TEMPLATE_MANIFEST.md missing at {workspace_path}"
    if len(manifest) < _MIN_MANIFEST_CHARS:
        return (
            f"TEMPLATE_MANIFEST.md is only {len(manifest)} chars — "
            f"expected ≥ {_MIN_MANIFEST_CHARS}. Template may be incomplete."
        )
    return None


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
    files = []
    # Find all complete path+content pairs using a non-greedy regex
    for m in re.finditer(
        r'\{\s*"path"\s*:\s*"([^"]+)"\s*,\s*"content"\s*:\s*"((?:[^"\\]|\\.)*)"\s*\}',
        partial,
        re.DOTALL,
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

            # read=60s — Anthropic streaming sends chunks sub-second during normal
            # generation; a 60s no-data gap indicates a stall. This also ensures
            # pipeline_task.cancel() lands within at most ~60s even if the socket
            # hangs with no bytes arriving (see agent_orchestrator._listen_for_stop).
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=60.0)) as client:
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

                    _stream_started_at = _time.monotonic()
                    async for line in response.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        raw_chunks.append(line[6:])
                        # Heartbeat every 15s — sends UI progress AND logs
                        # server-side so we can tell "Anthropic is slow but
                        # streaming" from "Anthropic call has stalled
                        # entirely" in container logs. Without the server
                        # log, a 4-min Phase 1 timeout looks identical to
                        # a hung connection.
                        now = _time.monotonic()
                        if now - last_heartbeat > 15:
                            last_heartbeat = now
                            elapsed = int(now - _stream_started_at)
                            logger.info(
                                "Claude stream heartbeat (%s): %d chunks received in %ds",
                                use_model, len(raw_chunks), elapsed,
                            )
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

# write_files_from_json() + _structural_sanity_check() live in
# ``project_writer``; they are re-exported at the top of this module so the
# existing imports elsewhere in the codebase keep working.


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
    # COPY_DECK provides every hero/section/feature/microcopy string so
    # Phase 1-3 don't fall back to generic "Learn More / Our Services" filler.
    ("BRAND_MARK", 400),
    ("RADIUS_TOKENS", 300),
    ("IMAGE_COMPOSITION", 700),
    ("COPY_DECK", 3000),
    ("COPY_TONE", 500),
    # Vision-grounded DNA from actual screenshots of reference sites.
    # Placed BEFORE LAYOUT_BLUEPRINT so if the model truncates, the concrete
    # visual observations survive — Claude leans on them heavily for hero
    # composition, card language, and motion cues.
    ("VISUAL_DNA", 1800),
    ("LIVE_UI_RESEARCH", 1200),  # scroll effects, counters, marquee, hover depth, ambient — from actual 2025-2026 site research
    # The system prompt at l.4705 declares VISUAL_DISTINCTIVENESS contents
    # MANDATORY (anti_generic + visual_surprise + section_card_matrix). If the
    # block is missing from the distilled research, the mandate silently
    # becomes a no-op and the page comes out generic. Keep it in the allowlist.
    ("VISUAL_DISTINCTIVENESS", 1500),
    # CULTURAL_ATMOSPHERE.signature_imagery is consumed deterministically for
    # Unsplash keywords, but the surrounding mood / sensory / spatial cues
    # help Claude write copy and pick visual language that fits the domain.
    ("CULTURAL_ATMOSPHERE", 1000),
    # ERA_CALIBRATION carries era-specific tokens (deco, neon, brutalist…)
    # that downstream styling rules reference.
    ("ERA_CALIBRATION", 600),
    ("LAYOUT_BLUEPRINT", 7000),  # Design DNA: 25 creative variables per project (hero/features/rhythm/motif/mood/cards/type/motion/pattern/radius/color/hover/spacing + live_ui_recipe/scroll_reveal/counter/marquee/ambient)
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


def _distill_research(research: str, max_total: int = 16000) -> str:
    """Compress the raw Gemini research dump into a compact bullet plan.

    The raw research is typically 10–20K chars with many ===SECTION=== blocks
    (and often long prose inside each). This helper:
      - Picks out the sections known to be load-bearing for UI generation.
      - Truncates each to a per-section cap, total bounded by max_total.
      - Re-emits the same ===HEADER=== markers so downstream extractors still
        find what they need.

    If research is already short or lacks ===HEADERS=== (fallback path),
    return it head-truncated. This keeps the helper robust over messy input.
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
# ║  STEP 2.5 — _expand_short_prompt()                          ║
# ║  Expand 1-3 word prompts via Gemini Flash before research   ║
# ╚══════════════════════════════════════════════════════════════╝

async def _expand_short_prompt(
    description: str,
    layout_archetype: str,
    domain: str,
    gemini_key: str,
    websocket=None,
) -> str:
    """Expand a very short user prompt into a richer brief for downstream stages.

    Short prompts ("ACCA website", "yoga studio app") give Gemini deep-research
    nothing concrete to anchor on, so its ===PAGES===, ===ENTITIES===, ===HEADER===
    blocks come back empty and the plan + schema render with bare fallbacks.

    This helper calls Gemini Flash (cheap, sub-second) to flesh the prompt out
    with: real organization name when applicable, audience, 5-7 likely pages,
    and 1-2 visual/tone qualities. Result feeds the cache key, deep research,
    schema parsing, and plan rendering — one upstream fix, multiple downstream
    wins. Fail-soft: returns the original description on any error.
    """
    _clean = description.split("\n\n---\n\n")[0].strip()
    # Skip expansion if prompt already has substance
    if len(_clean.split()) > 4 or len(_clean) > 50:
        return description

    try:
        import httpx as _httpx
        # Flash is plenty for prompt expansion; thinkingBudget=0 keeps it sub-second.
        _model = "gemini-2.5-flash"
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{_model}:generateContent?key={gemini_key}"
        )
        prompt = (
            f"The user gave a very short product brief: \"{_clean}\".\n"
            f"Project type: {layout_archetype.replace('_', ' ')} in the {domain} domain.\n\n"
            "Expand this into a 3-4 sentence brief that includes:\n"
            "1. The actual organization or concept (e.g. \"ACCA\" → "
            "\"Association of Chartered Certified Accountants — global professional accounting body\").\n"
            "2. The audience and what they want from the site/app.\n"
            "3. The most likely 5-7 pages or sections (use real, domain-specific names).\n"
            "4. One or two visual/tone qualities (e.g. \"authoritative and trustworthy\", "
            "\"playful and energetic\").\n\n"
            "STRICT FORMAT — the FIRST WORD must be the brand or concept name itself.\n"
            "GOOD opening: \"Maplewood Grove is a small-batch candle studio that…\"\n"
            "GOOD opening: \"ACCA (Association of Chartered Certified Accountants) is a…\"\n"
            "BAD opening:  \"This project is for a landing page for Maplewood Grove…\"\n"
            "BAD opening:  \"A landing page for Maplewood Grove that…\"\n"
            "BAD opening:  \"The website is about Maplewood Grove…\"\n"
            "Never start with: 'This project', 'A landing page', 'A website', 'The website', "
            "'The app', 'A modern', 'Build', 'Create'.\n\n"
            "Output ONLY the expanded brief as a single paragraph. "
            "No preamble, no headers, no quotes, no markdown, no bullet lists."
        )
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0,
                "maxOutputTokens": 600,
                "thinkingConfig": {"thinkingBudget": 0},
            },
        }
        async with _httpx.AsyncClient(timeout=20.0) as client:
            r = await client.post(url, json=payload)
        if r.status_code != 200:
            logger.warning("Prompt expansion API %d: %s", r.status_code, r.text[:200])
            return description
        data = r.json()
        expanded = ""
        for cand in data.get("candidates", []):
            for part in cand.get("content", {}).get("parts", []):
                if "text" in part:
                    expanded += part["text"]
        expanded = expanded.strip().strip('"').strip("'")
        if not expanded or len(expanded) < 80:
            return description

        suffix = ""
        if "\n\n---\n\n" in description:
            suffix = "\n\n---\n\n" + description.split("\n\n---\n\n", 1)[1]

        await _ws_send(
            websocket, "progress",
            f"📝 Expanded brief: {expanded[:90]}{'…' if len(expanded) > 90 else ''}",
        )
        logger.info("Expanded short prompt %r → %d chars", _clean, len(expanded))
        return expanded + suffix
    except Exception as exc:
        logger.warning("Prompt expansion failed (non-fatal): %s", exc)
        return description


# ╔══════════════════════════════════════════════════════════════╗
# ║  STEP 3 — gemini_deep_research()                            ║
# ║  Ultra-deep product research via Gemini with internet search ║
# ╚══════════════════════════════════════════════════════════════╝

# Limit concurrent Gemini research calls to avoid rate-limit 429s.
# Gemini 2.5 Pro paid-tier quotas are tighter than Flash (typically 2-5 RPM
# depending on billing plan). Keep the semaphore at 2 to be safe; existing
# retry/backoff logic below handles occasional 429s gracefully.
_gemini_semaphore = asyncio.Semaphore(2)


# Section names produced by the design prompt. Used to defensively rewrite
# Gemini's markdown headers (### NAME / **NAME** / NAME:) back to the literal
# ===NAME=== form that _extract_research_section expects.
_DESIGN_SECTION_NAMES: tuple[str, ...] = (
    "ERA_CALIBRATION",
    "LIVE_UI_RESEARCH",
    "LAYOUT_BLUEPRINT",
)


def _normalize_research_headers(text: str, section_names: tuple[str, ...]) -> str:
    """Rewrite markdown-style section headers to ===NAME=== form.

    Gemini sometimes ignores the requested literal `===NAME===` format and
    emits `### NAME` or `**NAME**` instead. _extract_research_section needs
    the exact `===NAME===` literal, so this helper canonicalizes the output.
    Only the listed section names are rewritten — unrelated `### Heading`
    text is left alone.
    """
    if not text:
        return text
    for name in section_names:
        canonical = f"==={name}==="
        if canonical in text:
            continue
        # Match: "### NAME", "**NAME**", "**NAME:**", "NAME:" (line start),
        # optionally with leading bold markers and trailing colons.
        # Whole-line replacement to avoid touching prose mentions.
        pattern = re.compile(
            rf"^\s*(?:#{{1,6}}\s*)?(?:\*\*)?{re.escape(name)}(?:\*\*)?\s*:?\s*$",
            re.MULTILINE,
        )
        text = pattern.sub(canonical, text)
    return text


async def _call_gemini_single(
    prompt: str,
    gemini_url: str,
    is_pro: bool,
    websocket,
    label: str,
    max_tokens: int = 10000,
) -> str:
    """Single Gemini REST call with retry+backoff. Returns response text.

    Each call acquires its own _gemini_semaphore slot so two parallel calls
    from the same project both proceed concurrently (semaphore value=2) while
    a third concurrent project waits, keeping us inside Gemini rate limits.
    """
    import httpx
    _thinking_config = (
        {"thinkingBudget": 2048} if is_pro else {"thinkingBudget": 0}
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "maxOutputTokens": max_tokens,
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

    response = None
    _max_attempts = 3
    async with _gemini_semaphore:
        for _attempt in range(1, _max_attempts + 1):
            try:
                async with httpx.AsyncClient(timeout=120.0) as client:
                    response = await asyncio.wait_for(
                        client.post(gemini_url, json=payload),
                        timeout=130.0,
                    )
            except asyncio.TimeoutError:
                logger.warning("Gemini %s timed out (attempt %d/%d)", label, _attempt, _max_attempts)
                if _attempt < _max_attempts:
                    await asyncio.sleep(5 * _attempt)
                    continue
                raise RuntimeError(f"Gemini {label} API timed out after all retry attempts")

            if response.status_code == 429:
                _backoff = 10 * _attempt
                logger.warning("Gemini rate limit (429) on %s attempt %d — retrying in %ds", label, _attempt, _backoff)
                await _ws_send(websocket, "progress", f"⚠️ Gemini rate limit ({label}) — retrying in {_backoff}s...")
                if _attempt < _max_attempts:
                    await asyncio.sleep(_backoff)
                    continue
            break

    if response is None or response.status_code != 200:
        status = response.status_code if response is not None else 0
        _api_msg = ""
        try:
            if response is not None:
                _api_msg = (response.json().get("error") or {}).get("message", "")
        except Exception:
            pass
        if status == 403:
            await _ws_send(websocket, "error", "❌ Google API key rejected (403). Check GOOGLE_API_KEY in .env.")
        elif status == 400:
            await _ws_send(websocket, "error", f"❌ Gemini rejected the request (400): {_api_msg or 'bad request'}")
        elif status == 404:
            await _ws_send(websocket, "error", f"❌ Gemini model not found. Set GEMINI_RESEARCH_MODEL to a valid model.")
        elif status == 429:
            await _ws_send(websocket, "error", "⚠️ Gemini rate limit hit after retries. Try again in a minute.")
        else:
            await _ws_send(websocket, "error", f"❌ Gemini API error {status}: {_api_msg or 'unknown'}")
        raise RuntimeError(f"Gemini {label} API error: {status}")

    try:
        data = response.json()
    except Exception as exc:
        raise RuntimeError(f"Gemini {label} returned non-JSON body: {exc}")

    from knowledge.loader import safe_gemini_text
    text = safe_gemini_text(data)
    if not text:
        raise RuntimeError(f"Gemini {label} returned empty text")
    return text


# ── Cultural anchor pool — used when prompt is ambiguous ───────────────────
# Each pool entry = a regional anchor that ships with its own palette / motifs /
# language phrases via the cultural_atmosphere block. Curated to be visually
# DISTINCT from each other — picking from this pool guarantees that two runs
# of "a restaurant" produce structurally different sites.
_CULTURAL_ANCHOR_POOLS: dict[str, list[str]] = {
    "restaurant": [
        "USA → Brooklyn deli", "France → Lyonnaise bistro",
        "Italy → Roman trattoria", "Italy → Sicilian seafood osteria",
        "Mexico → Mexico City taquería", "Spain → Andalusian taberna",
        "Japan → Tokyo izakaya", "Korea → Seoul gastropub",
        "Vietnam → Hanoi pho house", "Thailand → Bangkok night-market",
        "USA → Pacific NW farm-to-table", "Greece → Athenian psarotaverna",
        "Argentina → Buenos Aires asador", "Lebanon → Beirut mezze house",
        "Morocco → Marrakech tagine room", "USA → New Orleans Creole",
        "Peru → Lima cevichería", "Ethiopia → Addis injera house",
    ],
    "cafe": [
        "USA → Portland third-wave coffee", "Australia → Melbourne specialty",
        "Italy → Roman espresso bar", "Vietnam → Hanoi cà phê sữa đá",
        "Sweden → Stockholm fika kafé", "Austria → Vienna kaffeehaus",
        "Japan → Tokyo kissaten", "Türkiye → Istanbul kahvehane",
        "France → Parisian zinc-bar café", "USA → Brooklyn pour-over shop",
    ],
    "hotel": [
        "Greece → Cycladic minimalism", "Morocco → Marrakech riad",
        "Mexico → Tulum coastal", "Japan → Kyoto ryokan",
        "Italy → Tuscan agriturismo", "Iceland → Reykjavik design hotel",
        "Indonesia → Bali jungle villa", "Switzerland → Alpine chalet",
        "USA → Joshua Tree desert lodge", "Portugal → Lisbon townhouse hotel",
    ],
    "fashion": [
        "France → Parisian minimalism", "Japan → Tokyo avant-garde",
        "Italy → Milan tailoring", "Denmark → Copenhagen utilitarian",
        "USA → New York streetwear", "UK → London punk-tailoring",
        "Sweden → Stockholm Scandi-clean", "Korea → Seoul gender-fluid",
    ],
    "bakery": [
        "France → Parisian boulangerie", "Italy → Roman pasticceria",
        "USA → Brooklyn artisan bakery", "Denmark → Copenhagen smørrebrød",
        "Japan → Tokyo neo-patisserie", "Portugal → Lisbon pastel de nata shop",
        "Germany → Berlin bread house",
    ],
    "salon": [
        "France → Parisian atelier-salon", "USA → LA West Hollywood blowout bar",
        "Japan → Tokyo precision-cut studio", "UK → London Soho colour bar",
        "Korea → Seoul K-beauty parlour",
    ],
    "spa": [
        "Indonesia → Bali jungle wellness", "Japan → onsen ryokan",
        "Iceland → geothermal lagoon spa", "Türkiye → Istanbul hammam",
        "Mexico → Tulum cenote spa", "Switzerland → Alpine wellness retreat",
    ],
    "fitness": [
        "USA → Brooklyn boxing studio", "Japan → Tokyo precision pilates",
        "Sweden → Stockholm minimalist gym", "Australia → Bondi beach fitness",
        "USA → LA hot yoga studio", "Germany → Berlin functional training box",
    ],
    "portfolio": [
        "Denmark → Copenhagen design studio", "Japan → Tokyo design firm",
        "USA → Brooklyn creative agency", "France → Parisian atelier",
        "Switzerland → Zurich Swiss-grid studio", "UK → London design house",
    ],
    "travel": [
        "Greece → Aegean island hopping", "Japan → Kyoto cultural travel",
        "Iceland → ring-road expedition", "Morocco → Atlas mountains trek",
        "Peru → Sacred Valley trail", "Mexico → Yucatán cenote tours",
    ],
}

# Substrings that reveal the user already specified a culture / region — when
# any of these appear in the prompt we leave the anchor decision to Gemini
# (the user has already given a strong cue).
_CULTURE_KEYWORDS_IN_PROMPT = (
    "italian", "italy", "italia", "naples", "rome", "milan", "tuscany", "sicilian",
    "japanese", "japan", "tokyo", "kyoto", "osaka", "izakaya", "ramen",
    "mexican", "mexico", "oaxaca", "yucatan", "taqueria", "mezcal",
    "french", "france", "paris", "parisian", "lyon", "provence", "bistro",
    "spanish", "spain", "madrid", "barcelona", "andalusian", "tapas",
    "korean", "korea", "seoul", "k-beauty",
    "vietnamese", "vietnam", "hanoi", "saigon", "pho",
    "thai", "thailand", "bangkok", "chiang mai",
    "indian", "india", "mumbai", "delhi", "kerala",
    "chinese", "china", "shanghai", "beijing", "dim sum",
    "greek", "greece", "athens", "cycladic", "santorini",
    "moroccan", "morocco", "marrakech", "fez", "riad",
    "turkish", "türkiye", "istanbul", "anatolian",
    "scandinavian", "swedish", "danish", "norwegian", "stockholm", "copenhagen",
    "german", "germany", "berlin", "munich",
    "argentine", "argentina", "buenos aires", "asador",
    "peruvian", "peru", "lima", "ceviche",
    "ethiopian", "ethiopia", "addis", "injera",
    "lebanese", "lebanon", "beirut",
    "australian", "melbourne", "sydney",
    "brazilian", "brazil", "rio",
    "english", "british", "uk", "london",
    "irish", "ireland", "dublin",
    "icelandic", "iceland", "reykjavik",
    "balinese", "bali", "indonesia",
    "portuguese", "portugal", "lisbon",
    "brooklyn", "manhattan", "queens", "portland", "austin", "nashville",
    "los angeles", "san francisco", "chicago",
)


def _pick_cultural_anchor(description: str, domain: str) -> str:
    """Pre-pick a regional anchor for ambiguous prompts; return "" otherwise.

    The user types "a restaurant" → Python rolls the dice and returns
    "Spain → Andalusian taberna". This anchor is then injected as a HARD
    constraint into the Gemini research prompt so country_or_region is locked
    to the picked value, guaranteeing variety across consecutive runs of the
    same ambiguous prompt.

    If the user already wrote a culture-specific prompt ("Italian restaurant"
    / "Tokyo izakaya") we return "" — Gemini handles those cases well on its
    own from explicit keywords.
    """
    desc_lc = (description or "").lower()
    # Already culture-specific → let Gemini handle it.
    if any(kw in desc_lc for kw in _CULTURE_KEYWORDS_IN_PROMPT):
        return ""

    # Map domain → pool key.
    domain_lc = (domain or "").lower()
    pool_key: str | None = None
    if any(s in domain_lc for s in ("restaurant", "food", "dining", "tapas", "tavern", "trattoria", "bistro")):
        pool_key = "restaurant"
    elif any(s in domain_lc for s in ("café", "cafe", "coffee", "espresso", "roastery", "roaster")):
        pool_key = "cafe"
    elif any(s in domain_lc for s in ("hotel", "resort", "inn", "bnb", "lodging", "hostel", "boutique_hotel")):
        pool_key = "hotel"
    elif any(s in domain_lc for s in ("fashion", "apparel", "clothing", "boutique")):
        pool_key = "fashion"
    elif any(s in domain_lc for s in ("bakery", "patisserie", "boulangerie")):
        pool_key = "bakery"
    elif any(s in domain_lc for s in ("salon", "barber", "hair")):
        pool_key = "salon"
    elif any(s in domain_lc for s in ("spa", "wellness", "massage")):
        pool_key = "spa"
    elif any(s in domain_lc for s in ("fitness", "gym", "yoga", "pilates", "crossfit")):
        pool_key = "fitness"
    elif any(s in domain_lc for s in ("portfolio", "studio", "agency", "designer")):
        pool_key = "portfolio"
    elif any(s in domain_lc for s in ("travel", "tour", "trip", "expedition")):
        pool_key = "travel"

    # Domain doesn't match a hospitality / lifestyle pool → Gemini decides
    # (most likely "none — modern global" for B2B SaaS / dev tools).
    if not pool_key:
        return ""

    import random as _rand
    return _rand.choice(_CULTURAL_ANCHOR_POOLS[pool_key])


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

    # ── Cultural anchor pre-selection (Python-side, hard constraint) ────
    # When the user's prompt is ambiguous ("a restaurant", "a coffee shop"),
    # Gemini left to its own devices keeps converging on the same regional
    # anchor (Brooklyn deli / Parisian minimal / etc.) — the model has training
    # bias toward whatever's most documented. Rolling the anchor in Python
    # guarantees real variety across N consecutive runs of the same prompt.
    _cultural_anchor_override = _pick_cultural_anchor(description, domain)

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

    _cultural_anchor_block = ""
    if _cultural_anchor_override:
        _cultural_anchor_block = f"""

╔══════════════════════════════════════════════════════════════════════════╗
║  CULTURAL ANCHOR — HARD CONSTRAINT (do not override, do not negotiate)   ║
╠══════════════════════════════════════════════════════════════════════════╣
║  The user gave an AMBIGUOUS prompt with no specific country / cuisine.   ║
║  To guarantee variety across runs, an anchor was pre-selected by the     ║
║  pipeline (random, evenly-weighted across cultures).                     ║
║                                                                          ║
║  >>>  country_or_region = "{_cultural_anchor_override}"
║                                                                          ║
║  Apply this anchor to the ===CULTURAL_ATMOSPHERE=== block, the imagery,  ║
║  the palette, the typography, and the language phrases. Do NOT pick a    ║
║  different region. Do NOT output "none — modern global". Do NOT default  ║
║  to a generic upscale style — make this site feel authentically FROM     ║
║  that place to a designer who knows it.                                  ║
╚══════════════════════════════════════════════════════════════════════════╝
"""

    research_prompt = f"""You are an AUTONOMOUS PRODUCT RESEARCHER and UI/UX ARCHITECT with full internet search access.
Research and blueprint a production-quality web application.

PROJECT: "{description}"
INITIAL TYPE: {_archetype_label} | DOMAIN: {domain}
TECH STACK: {stack}
{_cultural_anchor_block}

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
training data cutoff is mid-2024 and will produce dated design. The goal of this step
is to anchor the design in ACTUAL 2025-2026 reality.

Run AT LEAST these searches before answering:
  1. "{domain} best website navigation structure pages 2026"
  2. "top {domain} website homepage sections content features 2025 2026"
  3. "best {domain} website user experience must-haves 2025 2026"
  4. (if "{description}" names a specific brand) "{description}" official website pages and structure

Focus on STRUCTURE: what pages exist, what navigation labels are used, what sections appear
on the homepage, what features every top {domain} site must have. Visual design is handled
separately — do NOT analyze motion, animations, or visual design here.

If "{description}" names a REAL brand → study THAT site FIRST as primary reference.

===SITES_ANALYZED===
1. [Name] ([URL]) — pages: [...] | nav_items: [...] | homepage_sections: [...] | notable_features: [...]
2. [Name] ([URL]) — pages: [...] | nav_items: [...] | homepage_sections: [...] | notable_features: [...]
3. [Name] ([URL]) — pages: [...] | nav_items: [...] | homepage_sections: [...] | notable_features: [...]
4. [Name] ([URL]) — pages: [...] | nav_items: [...] | homepage_sections: [...] | notable_features: [...]

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

COVERAGE RULE — non-negotiable:
EVERY user-facing CRUD page in your ===SIDEBAR=== above (Shipments, Routes,
Carriers, Customers, Invoices, Drivers, Vehicles, etc.) MUST have a matching
entity defined here. If the sidebar has /shipments and /carriers and /routes
and /invoices, you MUST emit Shipment, Carrier, Route, AND Invoice entities.
A sidebar item without a backing entity is a broken page.

In addition, include any noun the user explicitly named in the project
description even if it didn't reach the sidebar (referenced foreign keys
like driver_id, vehicle_id, route_id, carrier_id all imply entities).

Minimum 5 entities for an admin/CRM/TMS/ecommerce panel. Most real tools
have 7-12. Only landing-style admin tools may have fewer. Do not stop at
the minimum — list everything the tool actually manages.

[entity: EntityName]
purpose: [what this entity represents in the {domain} domain]
fields:
  - name: [field] | type: [string|number|boolean|enum|date|email|url|textarea|select|file] | required: [yes|no] | inList: [yes|no] | inForm: [yes|no] | options: [if enum: value1,value2,value3]
  [LIST ALL FIELDS from research — minimum 8-10 fields per entity]
mock_data: [12-15 rows of realistic domain-specific data — real names, real statuses, real values]

[REPEAT for every sidebar CRUD item AND every domain noun]

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

===CULTURAL_ATMOSPHERE===
country_or_region:
  [Identify the specific country / region / sub-culture this brand should evoke.
   Examples: "Italy → Campania (Naples)", "Japan → Tokyo izakaya", "Mexico → Oaxaca",
   "France → Provence", "USA → Brooklyn deli", "Korea → Seoul minimalist".

   STRICT RULES — WHEN "none" IS / IS NOT ALLOWED:
   ✓ "none — modern global" is ALLOWED only for genuinely culture-neutral verticals:
       B2B SaaS, dev tools, AI infrastructure, generic agency, internal admin tools,
       crypto/web3, fintech dashboards. These have no inherent national identity.

   ✗ "none" is FORBIDDEN for any of these — pick a region even if the user didn't say:
       restaurants / cafés / bakeries / bars (food + hospitality)
       hotels / B&Bs / travel agencies / tour operators (travel + hospitality)
       fashion / apparel / shoes / accessories (consumer goods)
       beauty / salons / spas (lifestyle services)
       wellness / yoga / fitness studios (lifestyle services)
       ecommerce for consumer products
       portfolio sites / personal brands

   When the user prompt is ambiguous (e.g. "a restaurant", "a coffee shop",
   "a fitness studio") and gives no cuisine / region / style cue, YOU MUST PICK
   a regional anchor by inferring from context or rolling a tasteful default.
   Be deliberate — don't always default to NYC/Brooklyn or Paris/France. Examples
   of valid auto-anchors when the prompt is generic:

     "a restaurant"       → "USA → Brooklyn deli", "France → Lyonnaise bistro",
                            "Mexico → Mexico City taquería", "USA → Pacific NW
                            farm-to-table", "Japan → Tokyo izakaya", "Spain →
                            Andalusian taberna" — pick ONE deliberately.
     "a coffee shop"      → "USA → Portland third-wave", "Australia → Melbourne
                            specialty", "Italy → Roman espresso bar", "Vietnam
                            → Hanoi cà phê" — pick ONE.
     "a fashion brand"    → "France → Parisian minimalism", "Japan → Tokyo
                            avant-garde", "Italy → Milan tailoring", "Denmark →
                            Copenhagen utilitarian" — pick ONE.
     "a hotel"            → "Greece → Cycladic", "Morocco → Marrakech riad",
                            "Mexico → Tulum coastal", "Japan → Kyoto ryokan" —
                            pick ONE.

   Rotate variety_seed-style — for ambiguous prompts, pick a culturally distinct
   anchor each run so two "a restaurant" generations don't both default to the
   same Brooklyn-deli look. The point is to ALWAYS be evocative and specific —
   ambiguity in user prompt is opportunity for the system to pick richly, not
   an excuse to fall through to generic.]

authenticity_cues:
  [5-8 specific cultural/visual elements that signal "this is genuinely FROM that place,"
   not a tourist's idea. Be concrete and unfamiliar — avoid Eiffel Tower / pizza-hat
   clichés. For Italian trattoria, NOT "checkered tablecloths" but "hand-rolled pasta
   on a flour-dusted wooden board, copper pots above an open kitchen, vintage Faema
   espresso machine, hand-painted Vietri ceramic plates, family photos in mismatched
   frames, candlelit Tuscan-stone walls". Each cue: 1 short concrete phrase.]

signature_imagery:
  [6-10 specific image search terms / scene descriptions that would feel authentic to
   the culture, NOT generic stock photography. Each as: "search_term: [terms] | mood: [mood]".
   For Italian: "fresh tagliatelle on floured board | rustic warm", "wood-fired pizza
   oven flames | dramatic close-up", "espresso crema pour | quiet morning", "antique
   copper saucepan on stove | lived-in", "italian nonna hands rolling dough | hands-only".
   Use these EXACT search terms in hero_image_url and supporting_image_urls — the
   default picks ("modern restaurant interior" / "elegant dining table") are forbidden.]

cultural_palette:
  [Palette grounded in cultural authenticity, not generic "premium dark + gold".
   For Italian trattoria: "warm cream + sun-dried tomato red + olive green + aged
   copper accent" — reasoning: "matches a traditional southern-Italian trattoria
   palette, not a corporate steakhouse". Output as: "values: [4-5 named colors] |
   reasoning: [why these and not navy+gold]". If country_or_region is "none",
   output "use design system default" and skip.]

typographic_signature:
  [ONE typographic move that feels CULTURALLY authentic to the place, not the
   industry default. For Italian: "vintage hand-painted Italian café signage style
   heading (Cooper, Beth Ellen, or Reenie Beanie for accents) + warm humanist sans
   for body (Source Sans, Mulish)". For Japanese: "thin elegant serif (Shippori
   Mincho) + clean geometric sans (Noto Sans JP) with extra leading". Avoid the
   generic Playfair/Fraunces+Inter pairing if a culturally-specific pairing fits.]

language_phrases:
  [3-6 untranslated source-language words or short phrases to weave into UI copy as
   section headers, eyebrow tags, accent words, or microcopy. For Italian:
   "Antipasti / Primi / Secondi / Dolci" (menu sections), "La Famiglia" (about),
   "Benvenuti" (welcome eyebrow), "Buon Appetito" (CTA accent).
   For Japanese: "おもてなし (omotenashi)" as a values eyebrow, "本日のおすすめ (today's
   recommendation)" for a feature label. NEVER translate these — they are atmospheric
   anchors. If country_or_region is "none", output "n/a" and skip.]

section_label_overrides:
  [If the cultural mapping suggests renaming standard sections, list as
   "default → cultural" pairs. For Italian restaurant:
   "Menu → La Carta", "Our Story → La Nostra Storia", "Reservations → Prenotazioni",
   "Hours → Orari". Codegen will use these renamed labels in headers and section
   eyebrows. If country_or_region is "none", skip.]

banned_generics:
  [5-8 design choices that would make THIS specific cultural site feel generic /
   AI-generated. For Italian trattoria: "navy + gold palette (looks corporate, not
   trattoria)", "blurry restaurant tablescape stock photo (every AI site)", "Playfair
   italic at 8rem (overused on every restaurant template)", "'Reserve a Table' CTA
   without Italian flavor — use 'Prenota' or 'Riserva il tuo Tavolo'".
   These are FORBIDDEN for this run. If country_or_region is "none", list cliches
   for the industry instead (SaaS: "blue gradient hero", "abstract dashboard mockup
   floating in the void").]

motif_inventory:
  [4-6 small recurring decorative motifs to use as signature_motif and supporting
   decorations across sections. For Italian: "olive branch SVG", "hand-drawn pasta
   shape outline (penne, fusilli silhouette)", "vintage Italian postage stamp border",
   "cracked terracotta texture overlay at 8% opacity", "small espresso cup icon".
   Codegen will scatter these 2-3× across sections per signature_motif spec.]

===COPY_TONE===
hero_headline_style: [punchy|formal|warm|bold — max chars and style description]
body_copy_style: [tone description]
cta_style: [verb style]
cultural_voice_overlay: [if cultural_atmosphere is set, override default copy tone
  with a culturally-specific voice. Italian trattoria: "warm, familial, slightly
  exclamatory — like a host welcoming you in. Use occasional Italian phrases as
  accents, not translation". Japanese izakaya: "quiet, precise, respectful, with
  small 'お' honorific touches in microcopy." If "none", output "n/a".]

===VISUAL_DISTINCTIVENESS===
anti_generic:
  [3 SPECIFIC layout/visual choices that ensure this site looks NOTHING like a
   generic AI-generated template. Be concrete and actionable for a developer.
   BAD: "use unique colors" or "add animations" (too vague)
   GOOD examples:
     "hero: stagger headline words on 3 separate lines each offset-x by +40px,
      creating a diagonal reading path instead of a left-aligned block"
     "features: use a magazine-editorial numbered list (01. 02. 03.) with
      text-8xl font-black numbers bleeding behind the card border"
     "testimonials: single rotating full-bleed pull-quote with 5rem italic
      serif text, no avatar cards — one voice at a time, auto-scrolling"
     "menu section: horizontal scrolling film strip of dish photos with
      parallax offset, not a static 3-col grid"]

visual_surprise:
  [ONE unexpected detail a senior designer would notice and appreciate.
   This MUST appear somewhere on the rendered page — not be skipped.
   Examples:
     "a vintage receipt-paper SVG texture overlaid on the menu section at 6% opacity"
     "hero stat cards (4.9★ / 2,400 subs / 12 origins) that count up with
      IntersectionObserver when they scroll into view"
     "a slowly rotating SVG quote mark (360° / 30s, opacity 8%) centered
      behind the testimonials headline"
     "the CTA button has a subtle shimmer sweep animation on hover
      (background-position: 200% 0 → 0 0 over 600ms)"
     "section dividers are hand-drawn wavy SVG paths instead of straight lines"
   This is NOT optional — it must be implemented in the generated code.]

section_card_matrix:
  [For EACH section in ===SECTIONS===, assign which card style to use and WHY.
   Rule: No two consecutive sections may use the same card style.
   Rule: At most 2 sections total may use the same card style on the whole page.
   Styles: soft | glass | featured | no-card (full-bleed layout, no card wrapper)
   Format: section_name → style | reason (1 phrase)
   Example:
     hero         → no-card   | split layout, no wrapper needed
     features     → featured  | bento hero tile needs premium gradient
     story        → no-card   | full-bleed image section
     menu         → soft      | clean item cards on light bg
     testimonials → glass     | cards float on dark branded background
     cta_final    → no-card   | full-width branded band, no card]

section_spacing_rhythm:
  [Override default py-20 for sections where it would create identical visual weight.
   Only list sections that need a different rhythm. Format: section_name → padding | reason
   Example:
     hero         → min-h-[90vh] flex items-center | needs full viewport presence
     menu         → py-32 | rich imagery needs breathing room
     hours        → py-12 | compact info section, tight is intentional
     cta_final    → py-24 | standard CTA band height]

===HEADER_DESIGN===
Study the top sites in this domain. Pick a header that feels native to THIS brand —
not the generic SaaS sticky-nav template. Every field drives the actual rendered JSX.

structure:
  [ONE of:
    logo_left_nav_right  — brand left, nav items right, CTA far right (classic horizontal)
    centered_logo        — brand centered, nav splits evenly left and right of it
    logo_left_hamburger  — brand left, single "Menu" trigger right, fullscreen overlay nav
    floating_pill        — pill-shaped capsule floating 16px from top, max-w-3xl centered
    two_row              — tall header: brand + tagline top row, nav bottom row (fashion/retail)
  reasoning: [why this structure fits the brand — 1 sentence referencing real site]

surface:
  [ONE of:
    light_blur           — bg-background/92 backdrop-blur border-b border-border (default)
    dark_solid           — bg-foreground text-background (high-contrast editorial)
    transparent_scroll   — transparent over hero, morphs to light_blur after 60px scroll
    primary_tinted       — bg-primary/8 border-b border-primary/20 (subtle brand wash)
    glass_dark           — bg-black/30 backdrop-blur-xl text-white (over dark/photo hero)
  reasoning: [why — 1 sentence]

nav_link_style:
  [ONE of:
    plain                — text-foreground/70 hover:text-foreground transition-colors
    underline_slide      — underline animates left→right on hover (h-px bg-primary absolute)
    uppercase_track      — text-[11px] uppercase tracking-[0.18em] text-foreground/55
    pill_hover           — hover:bg-muted rounded-full px-3 py-1 (subtle pill on hover)
    dot_left             — small dot (h-1.5 w-1.5 bg-primary rounded-full) appears left on hover

cta_style:
  [ONE of:
    filled_pill          — rounded-full bg-primary px-6 shadow-sm (modern, friendly)
    filled_sharp         — rounded-md bg-primary px-5 (standard, clean)
    ghost_pill           — rounded-full border border-foreground/30 px-5 (elegant, minimal)
    ghost_sharp          — rounded-md border border-border px-5 (subtle, serious)
    text_arrow           — plain text + " →" no button wrapper (ultra-minimal)
    inverted_pill        — rounded-full bg-background text-foreground px-5 (for dark surface)

height:
  [ONE of: slim (h-14) | standard (h-16) | tall (h-20) | masthead (h-24)]

top_accent:
  [ONE of:
    none                 — no decorative top element
    primary_bar          — 3px border-t border-primary above the header (signature stripe)
    gradient_wash        — bg-gradient-to-r from-primary/20 via-transparent to-accent/20 as top strip

mobile_menu:
  [ONE of: slide_drawer | fullscreen_overlay | simple_dropdown]
  reasoning: [why this fits the brand UX — 1 phrase]

===IMAGE_SOURCES===
hero: [treatment from research]
content_images: https://picsum.photos/seed/[descriptive_seed]/800/600
avatars: https://i.pravatar.cc/150?u=[unique_string]
icons: Lucide React

CRITICAL: Every value from REAL internet research. Original copy. Domain-specific. Production-quality.
"""

    # ── Design prompt: visual DNA, motion, LAYOUT_BLUEPRINT ──────────────────
    # Builds ERA_CALIBRATION + LIVE_UI_RESEARCH + LAYOUT_BLUEPRINT sections.
    # Runs concurrently with the structure prompt above.
    _is_sidebar_layout = layout_archetype in ("admin_dashboard", "crm", "tms", "saas_dashboard")
    _layout_bp_block = (
        "SKIP the ===LAYOUT_BLUEPRINT=== block — this layout_archetype uses a fixed sidebar design."
        if _is_sidebar_layout else
        """You are the creative director of an award-winning design studio.
DESCRIBE the visual design DNA for this site. Every field needs 2-4 specific, concrete sentences.

MANDATORY:
  • Name at least ONE real award-winning site as inspiration (from your research above — not from memory)
  • Be specific: "diagonal split with tilted polaroid on warm beige" beats "split-screen with image"
  • NOVELTY COMMITMENT: your design must be visibly different from a generic version of this site

SKIP this entire ===LAYOUT_BLUEPRINT=== block ONLY if layout_archetype is
admin_dashboard / crm / tms / saas_dashboard (fixed sidebar layouts).

===LAYOUT_BLUEPRINT===

hero_description:
  [2-4 sentences: spatial arrangement, imagery treatment, decorative elements, eye travel path.]
  inspiration_site: [real URL from your research]
  why_this_fits: [1 sentence]

features_description:
  [2-4 sentences. Must be spatially DIFFERENT from hero_description — no same grid used twice.]
  inspiration_site: [real URL]

secondary_sections_description:
  [1-2 sentences per remaining section. Include at least one "surprising" section layout.]

section_rhythm:
  [Every section in order → background treatment. Max 2 consecutive sections same treatment.]

signature_motif:
  [ONE repeated decorative element appearing 2-3× across the page. Specific: shape, color, opacity, placement.]

design_mood:
  [2-3 sentences: visual personality, ONE-WORD adjective, how it translates to type/cards/color.]
  reasoning: [1 sentence citing a research finding or target-user insight]

hero_image_strategy:
  [ONE of these — picks how the hero uses imagery. Coupled to hero_archetype below:
    • background_full      — image is full-bleed backdrop with a scrim. ONLY valid for
                             hero_archetype = cinematic-parallax / full-bleed-dark.
    • structural_half      — image is a HALF or THIRD of the hero (one column/quadrant).
                             Use for: split / editorial-offset / product-showcase.
    • single_feature_card  — ONE small image inside a card or framed panel, NOT a backdrop.
                             Use for: magazine / bento (image as one tile).
    • decorative_scatter   — 3-5 SMALL images scattered around the headline at different
                             sizes/rotations/positions (a wine bottle, a tapas plate,
                             a small portrait — each rotated/offset distinctly). NO single
                             dominant photo. Use for: bento / diagonal / playful brands.
    • illustration_3d      — request a 3D-style render or SVG illustration instead of a
                             photo (Spline-style geometry, abstract gradient blobs,
                             isometric scenes). Use for: tech, SaaS, abstract brands,
                             agency sites, anywhere a photo would feel literal/cliched.
    • typographic_only     — NO hero image at all. Pure typography hero. ONLY valid for
                             hero_archetype = typographic-hero.
    PICK based on what would look LEAST like a generic AI-generated landing page. If the
    domain is "boring" (consulting, legal, finance, education) prefer illustration_3d or
    decorative_scatter over a stock photo. If the project is product-focused (e-bike,
    audio gear, fashion) prefer structural_half with a real product photo.
  ]
  reasoning: [1 sentence on why this strategy fits the domain + chosen hero_archetype]

hero_image_url:
  [Provide an Unsplash URL ONLY when hero_image_strategy ∈ {background_full, structural_half,
   single_feature_card}. For decorative_scatter list 3-5 URLs separated by " | ".
   For illustration_3d output the literal string "GENERATE_3D" (codegen will substitute
   an SVG/Spline placeholder). For typographic_only output the literal string "NONE".
   When provided as URL: https://images.unsplash.com/photo-PHOTO_ID?auto=format&fit=crop&w=1600&q=80]

supporting_image_urls:
  [3-6 Unsplash URLs: "section_name: https://images.unsplash.com/photo-PHOTO_ID?auto=format&fit=crop&w=1200&q=80"]

accent_detail:
  [ONE signature micro-detail a tired designer would skip. Specific to THIS domain.]
  placement: [exact Tailwind position — e.g. "absolute -bottom-4 -left-4 z-20"]

hero_archetype:
  [ONE of: split / bento / diagonal / magazine / layered-scroll / cinematic-parallax /
   editorial-offset / full-bleed-dark / product-showcase / typographic-hero / INVENT one]
  reasoning: [1 sentence why this fits the domain mood]

features_archetype:
  [ONE of: bento-mixed / zigzag / vertical-tabs / horizontal-scroll / masonry /
   tilt-stack / showcase / timeline / numbered-editorial / INVENT.
   These EXACT names map to wrapper JSX templates — do not invent variants
   like `zigzag-split` or `interactive-showcase`; pick the canonical name.]
  reasoning: [1 sentence]

card_language:
  [1-2 sentences beyond "rounded-xl shadow-md". Include: radius style, border, shadow/glow, micro-element.]

typography_pairing:
  [Heading font + body font (real Google Fonts, different from each other). Weight/tracking/italic rules.]

motion_language:
  [2-3 complementary behaviors. MUST specify: enter animation, hover state, scroll-linked behavior, emotional register.]

decorative_pattern:
  [ONE pattern/texture across multiple sections at low opacity. Include how it's applied in Tailwind/CSS.]

border_radius_language:
  [Philosophy + EXACT Tailwind values for: buttons, cards, images, inputs.]

color_application_strategy:
  [ONE of: mono-accent / duotone-photos / gradient-mesh / inverted-dark / polychrome /
   photographic-neutral / brand-flood. Specify which sections get which treatment.]

hover_interaction_style:
  [1-2 consistent behaviors for: primary cards, CTAs, image cards, nav links.]

spacing_rhythm:
  [ONE of: tight-editorial / standard-modern / airy-luxury / asymmetric / dense-information.
   Container max-width + gap values.]

live_ui_recipe:
  [4-6 sentences: what is the ONE signature motion moment that makes visitors stop?
   Plus supporting micro-interactions throughout the page. Domain-specific — not generic.]

scroll_reveal_style:
  [Exact framer-motion values: y-distance, duration, easing, stagger interval, viewport amount trigger.]

counter_animation:
  [Which stats animate as counters, which section they're in, end value + format, animation duration.
   Pattern: framer-motion useMotionValue + useTransform + animate on inView.]

marquee_strip:
  [logo-strip / quote-ticker / stat-ticker / none. Which section, exact content, CSS keyframe approach.
   Preferred: two identical UL sets side by side, overflow-hidden parent, translate-left 50% animation.]

ambient_motion:
  [gradient-mesh / floating-orbs / grain-noise / subtle-scan / none.
   Include CSS keyframe snippet and JSX element placement.]

novelty_check:
  [One sentence: what makes YOUR design visibly different from any other designer's version of this site?]

design_dna_summary:
  [ONE sentence, 15-25 words: the unique design recipe. Example: "Editorial-luxe Fraunces-italic headlines
   over cinematic full-bleed photography, paper-fold cards, airy 40vh spacing, brand-flood CTA finale."]

CRITICAL: Concrete, code-translatable language. No one-word answers. No generic phrases like "modern clean design"."""
    )

    _design_prompt = f"""You are an AWARD-WINNING VISUAL DESIGN DIRECTOR with full internet search access.
Your sole task: research 2025-2026 visual design trends for the project below, then output ONLY these three
sections: ERA_CALIBRATION, LIVE_UI_RESEARCH, LAYOUT_BLUEPRINT.
Do NOT output CLASSIFICATION, CSS_VARIABLES, FONTS, PAGES, ENTITIES, or any structural blocks.

⚠️ OUTPUT FORMAT — STRICT:
  Section headers MUST be the LITERAL string `===NAME===` on their own line.
  ✓ CORRECT:   ===ERA_CALIBRATION===
  ✗ WRONG:     ### ERA_CALIBRATION
  ✗ WRONG:     **ERA_CALIBRATION**
  ✗ WRONG:     ERA_CALIBRATION:
  Sub-fields are plain `key: value` lines (no markdown bold). A downstream parser
  searches for the literal `===HEADER===` markers — anything else is dropped silently.

PROJECT: "{description}"
DOMAIN: {domain}
LAYOUT TYPE: {_archetype_label}
CLASSIFICATION (locked — do NOT change):
  layout_archetype: {layout_archetype}
  is_single_page: {"yes" if is_single_page else "no"}

════════════════════════════════════════════════════════════
DESIGN RESEARCH — MUST use google_search (do NOT rely on training memory)
════════════════════════════════════════════════════════════

Training data cutoff is mid-2024. The current year is 2026. YOU MUST USE google_search
to find what's actually trending in 2025-2026 — do not rely on training memory alone.

Run AT LEAST these searches:
  1. "awwwards {domain} site of the year 2025"
  2. "awwwards {domain} site of the year 2026"
  3. "best {domain} website visual design typography 2026"
  4. "2026 web design trends {domain} scroll-driven animations variable fonts"
  5. "{domain} website hover interactions micro-animations 2025 2026"
  6. "best {domain} website design inspiration 2025 2026"
  7. (if "{description}" names a specific brand) "{description}" official site visual design

For EACH site discovered, note:
  a) Layout: H1 position, split/bento/full-bleed, asymmetry, grid structure
  b) Typography: font names, weight contrast, italic/caps, oversized type treatment
  c) Motion: scroll animations, hover effects, stagger reveals, parallax depth
  d) "Live" feel: counters, marquees, ambient animations, video, looping elements

════════════════════════════════════════════════════════════
DESIGN ERA CALIBRATION
════════════════════════════════════════════════════════════

FORBIDDEN — NEVER USE THESE (dated / generic signal of AI-generated templates):
  ✗ [2020-2022 era] Flat pastel gradient hero with centered text stack and no motion
  ✗ [2020-2022 era] Symmetric 3-column feature grid with icon-over-title-over-description, no hover effect
  ✗ [2020-2022 era] Generic rounded-xl cards with small shadow and nothing else distinctive
  ✗ [2020-2022 era] "From $X/month" pricing cards all identical shape
  ✗ [2020-2022 era] Stock photos of smiling office workers / diverse-team-around-laptop
  ✗ [2020-2022 era] Hero H1 with two dead-centered CTAs and no imagery breaking the grid
  ✗ [2020-2022 era] Hero with bg-background/95 washing out a photo (use dark gradient overlays)
  ✗ [2020-2022 era] Nav with "About / Features / Pricing / Sign In / Get Started" on a non-SaaS site
  ✗ [2020-2022 era] Static stat counters that don't animate when scrolled into view
  ✗ [2020-2022 era] Logo rows with no scroll or motion
  ✗ [2020-2022 era] Hero that has zero motion — everything must have at least an entry animation
  ✗ [2023-2024 era — now overused by AI tools, avoid these as defaults]:
    - Generic bento grid with uniform-sized tiles and no visual hierarchy or content strategy
    - Two blurred gradient orbs as the ONLY hero background decoration
    - Glassmorphism (backdrop-blur) cards on non-photo backgrounds
    - Inter + Geist font pairing (2024 AI default, recognizable from a mile away)
    - Purple/indigo gradient as the go-to SaaS accent color
    - "Frosted glass card on white background" navbar that blurs nothing
    - Dark mode with generic #18181b background + white text and no real palette work

REQUIRED 2025-2026 moves (blueprint MUST include at least 5 of these):
  ✓ Oversized editorial type — at least one H1 using clamp(4rem,10vw,9rem) with
    leading-[0.88] and tight tracking. Type as the primary design element, not decoration.
  ✓ Asymmetric layout — at least one section breaks the symmetric grid: editorial
    magazine offset, staggered columns, intentional negative space, or content
    bleeding out of the container.
  ✓ Depth via material contrast — NOT heavy drop-shadows; instead: hairline borders
    (border-foreground/10), one dramatically inverted section (bg-foreground text-background),
    or a full-bleed dark photo. Restraint IS the luxury signal.
  ✓ Signature motif applied 2-3x — grain noise (opacity-[0.04]), topographic lines,
    hand-drawn squiggle, dot grid, or a brand-specific shape. Must be specific to
    this domain, not a generic choice.
  ✓ Scroll-linked stagger reveals — each card enters 60-80ms after the previous
    (framer-motion staggerChildren or CSS animation-delay).
  ✓ Sticky header scroll transition — transparent → frosted-glass bg-background/90
    backdrop-blur-md on scroll (still essential in 2026, still differentiates premium sites).
  ✓ Horizontal marquee strip for logos / testimonials / stats — CSS keyframe, no JS library.
  ✓ Type-first section — at least one section where oversized text + spacing IS the full
    design (no icons, no photos, just letterforms + tight grid).
  ✓ Animated stat counters — numbers count up from 0 when scrolled into view.
  ✓ Hero entry sequence — badge → H1 → subtitle → CTA → image, cascading 100-600ms delays,
    NOT all at once. Use framer-motion initial/animate (not whileInView for hero).
  ✓ Ambient motion in hero — at minimum one of: CSS scroll-driven background shift,
    grain texture overlay, slow-drifting orbs COMBINED WITH grain/topographic lines
    (orbs alone is the 2024 cliché — layer at least one other texture).
  ✓ Variable font personality — if the heading font supports variable axes, animate
    font-weight on hover (100→700) or use clamp() to vary weight with viewport width.

===ERA_CALIBRATION===
moves_borrowed: [List the 5+ 2025-2026 moves you will use and WHY — 1 line each]
patterns_avoided: [List the 3-5 dated patterns you rejected and why — include both 2020-2022 and 2023-2024 era clichés]

════════════════════════════════════════════════════════════
LIVE UI RESEARCH
════════════════════════════════════════════════════════════

Based on your research above, answer: what makes top {domain} websites feel "live" and
premium in 2025-2026?

===LIVE_UI_RESEARCH===
scroll_effects: [stagger? parallax? clip-path reveals? opacity wipes? Be specific with values]
counter_animations: [which stats animate, what section, format: currency/%/number+?]
ticker_or_marquee: [logo-strip / testimonial-ticker / stat-ticker / none + description]
hover_depth: [flat-lift / tilt-3D / glow-ring / reveal / morph? Specify per element type]
video_or_loop: [hero video / looping animation / none?]
sticky_behavior: [nav color/blur change? any sticky panels?]
text_animation: [character reveal / word reveal / number odometer / typing effect?]
ambient_motion: [gradient-mesh shift / floating-orbs / grain-noise / subtle-scan / none?]
interaction_richness: [1-5 scale]

════════════════════════════════════════════════════════════
LAYOUT BLUEPRINT
════════════════════════════════════════════════════════════

{_layout_bp_block}

CRITICAL: Every value from your research above. Concrete, code-translatable language.
Output ONLY ERA_CALIBRATION, LIVE_UI_RESEARCH, and LAYOUT_BLUEPRINT blocks.
"""

    # ── Parallel Gemini calls ─────────────────────────────────────────────────
    import httpx

    _research_model = os.environ.get("GEMINI_RESEARCH_MODEL", "gemini-3.1-pro-preview")
    await _ws_send(websocket, "progress", f"🔬 Calling {_research_model} — structure & design in parallel...")

    gemini_url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{_research_model}:generateContent?key={gemini_key}"
    )
    _is_pro = "pro" in _research_model.lower()

    # Run structure research (content/pages/entities) and design research
    # (LAYOUT_BLUEPRINT/LIVE_UI/ERA_CALIBRATION) concurrently.
    # _gemini_semaphore(2) lets both slots proceed in parallel for a single project
    # while a second concurrent project waits, keeping inside Gemini rate limits.
    _results = await asyncio.gather(
        _call_gemini_single(research_prompt, gemini_url, _is_pro, websocket, "structure", max_tokens=14000),
        _call_gemini_single(_design_prompt, gemini_url, _is_pro, websocket, "design", max_tokens=8000),
        return_exceptions=True,
    )

    structure_text = _results[0] if not isinstance(_results[0], Exception) else ""
    design_text = _results[1] if not isinstance(_results[1], Exception) else ""

    if not structure_text:
        # Structure is mandatory — propagate the failure
        _exc = _results[0] if isinstance(_results[0], Exception) else RuntimeError("Gemini structure returned empty")
        raise _exc

    if isinstance(_results[1], Exception):
        logger.warning("Gemini design research failed (non-fatal): %s", _results[1])
        await _ws_send(websocket, "progress", "⚠️ Design research partial — continuing with structure only...")

    # Normalize any markdown headers Gemini emitted (### NAME / **NAME**) back
    # to the literal ===NAME=== form. Live testing showed Gemini occasionally
    # ignores the format directive on the smaller design prompt, which silently
    # drops every design-only section downstream.
    if design_text:
        design_text = _normalize_research_headers(design_text, _DESIGN_SECTION_NAMES)

    # Merge: structure sections come first so _extract_research_section (which
    # returns the FIRST occurrence) finds structural CLASSIFICATION/PAGES/ENTITIES
    # correctly; design-only sections (LAYOUT_BLUEPRINT, LIVE_UI_RESEARCH,
    # ERA_CALIBRATION) appear only in design_text and are appended after.
    text = structure_text + ("\n\n" + design_text if design_text else "")

    await _ws_send(websocket, "progress", "✅ Research complete — building project blueprint...")

    # Send research summary to chat panel
    try:
        analyzed_section = ""
        for _header in ("===PRODUCTS_ANALYZED===", "===SITES_ANALYZED===", "===PLATFORMS_ANALYZED==="):
            if _header in text:
                _start = text.index(_header) + len(_header)
                _rest = text[_start:]
                _end_match = _rest.find("===")
                _end = _start + _end_match if _end_match != -1 else _start + 500
                analyzed_section = text[_start:_end].strip()
                break

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
            await _emit_file_writes(websocket, written, action="fix")
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
- Icons: ALWAYS import directly from 'lucide-react' — do NOT route through a
  shared `Icons` object.
    ✅ import { Instagram, Twitter, MapPin, Phone } from 'lucide-react'
       <Instagram className="h-5 w-5" />
    ❌ import { Icons } from '@/config/icons'
       <Icons.instagram className="h-5 w-5" />
  WHY: the template's `src/config/icons.js` only exports a small admin set
  (home, dashboard, users, settings, …). Writing `<Icons.instagram />` when
  `instagram` was never declared resolves to `undefined` at render and
  crashes the page with "Element type is invalid: ... got: undefined".
  Direct lucide-react imports never hit this bug class. Never modify icons.js.
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
- Icons: ALWAYS import directly from 'lucide-react' — never route through a
  shared `Icons` object (`@/config/icons`). `<Icons.foo />` resolves to
  undefined at runtime when `foo` was never declared in icons.js and crashes
  the page with "Element type is invalid".
    ✅ import { Plus, Edit, Trash2 } from 'lucide-react'
    ❌ import { Icons } from '@/config/icons'  →  <Icons.plus />
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

# Curated Unsplash photo pools per category. Each generation picks ONE id at
# random from the matching category — replaces the previous single-id-per-
# category list that made every restaurant ship `photo-1517248135467` and
# every coffee shop ship `photo-1509042239860`. Multiple ids per category =
# real visual variety across runs even before cultural_atmosphere kicks in.
_UNSPLASH_POOLS: dict[str, list[str]] = {
    "Coffee / espresso": [
        "photo-1509042239860-f550ce710b93", "photo-1497935586351-b67a49e012bf",
        "photo-1495474472287-4d71bcdd2085", "photo-1485808191679-5f86510681a2",
        "photo-1494314671902-399b18174975", "photo-1442550528053-c431ecb55509",
        "photo-1521488756137-bce62c6e3ce6",
    ],
    "Coffee shop interior": [
        "photo-1554118811-1e0d58224f24", "photo-1453614512568-c4024d13c247",
        "photo-1559056199-641a0ac8b55e", "photo-1521017432531-fbd92d768814",
        "photo-1525629722858-bccd4f88491e", "photo-1559925393-8be0ec4767c8",
    ],
    "Latte art": [
        "photo-1517231925375-bf2cb42917a5", "photo-1572442388796-11668a67e53d",
        "photo-1551030173-122aabc4489c", "photo-1461023058943-07fcbe16d735",
        "photo-1534687941688-651ccaafbff8",
    ],
    "Plated food": [
        "photo-1414235077428-338989a2e8c0", "photo-1546069901-ba9599a7e63c",
        "photo-1567620905732-2d1ec7ab7445", "photo-1565958011703-44f9829ba187",
        "photo-1504674900247-0877df9cc836", "photo-1540189549336-e6e99c3679fe",
        "photo-1551183053-bf91a1d81141",
    ],
    "Restaurant": [
        "photo-1517248135467-4c7edcad34c4", "photo-1592861956120-e524fc739696",
        "photo-1559339352-11d035aa65de", "photo-1466978913421-dad2ebd01d17",
        "photo-1424847651672-bf20a4b0982b", "photo-1552566626-52f8b828add9",
    ],
    "Italian / pasta": [
        "photo-1551183053-bf91a1d81141", "photo-1473093295043-cdd812d0e601",
        "photo-1565299624946-b28f40a0ae38", "photo-1574484184081-afea8a62f9ab",
        "photo-1572441713132-51c75654db73", "photo-1551892589-865f69869476",
    ],
    "Pizza / wood-fired oven": [
        "photo-1513104890138-7c749659a591", "photo-1604382354936-07c5d9983bd3",
        "photo-1594007654729-407eedc4be65", "photo-1565299624946-b28f40a0ae38",
        "photo-1571066811602-716837d681de",
    ],
    "Japanese / izakaya / sushi": [
        "photo-1579871494447-9811cf80d66c", "photo-1553621042-f6e147245754",
        "photo-1617196034796-73dfa7b1fd56", "photo-1611143669185-af224c5e3252",
        "photo-1535473895227-bdecb20fb157",
    ],
    "Mexican / tacos / mezcal": [
        "photo-1565299585323-38d6b0865b47", "photo-1551504734-5ee1c4a1479b",
        "photo-1542528180-a1208c5169a5", "photo-1604847658149-a1c50d2c4d3c",
    ],
    "Bakery": [
        "photo-1509440159596-0249088772ff", "photo-1555507036-ab1f4038808a",
        "photo-1568254183919-78a4f43a2877", "photo-1486427944299-d1955d23e34d",
        "photo-1517686469429-8bdb88b9f907",
    ],
    "Gym / weights": [
        "photo-1540497077202-7c8a3999166f", "photo-1517836357463-d25dfeac3438",
        "photo-1571019613454-1cb2f99b2d8b", "photo-1534438327276-14e5300c3a48",
        "photo-1581009146145-b5ef050c2e1e",
    ],
    "Yoga / pilates": [
        "photo-1544367567-0f2fcb009e0b", "photo-1506629082955-511b1aa562c8",
        "photo-1518611012118-696072aa579a", "photo-1599901860904-17e6ed7083a0",
        "photo-1545205597-3d9d02c29597",
    ],
    "Salon / hair": [
        "photo-1560066984-138dadb4c035", "photo-1522337360788-8b13dee7a37e",
        "photo-1599387737420-2af44f5ab51d", "photo-1521590832167-7bcbfaa6381f",
    ],
    "Spa": [
        "photo-1540555700478-4be289fbecef", "photo-1544161515-4ab6ce6db874",
        "photo-1571019613454-1cb2f99b2d8b", "photo-1519823551278-64ac92734fb1",
    ],
    "Hotel / travel": [
        "photo-1488085061387-422e29b40080", "photo-1501117716987-c8e1ecb210bc",
        "photo-1564501049412-61c2a3083791", "photo-1542314831-068cd1dbfeeb",
        "photo-1444201983204-c43cbd584d93",
    ],
    "Real estate": [
        "photo-1560518883-ce09059eeffa", "photo-1493809842364-78817add7ffb",
        "photo-1568605114967-8130f3a36994", "photo-1502672260266-1c1ef2d93688",
    ],
    "Dog / pet": [
        "photo-1450778869180-41d0601e046e", "photo-1548199973-03cce0bbc87b",
        "photo-1583511655857-d19b40a7a54e", "photo-1561037404-61cd46aa615b",
    ],
    "Wedding": [
        "photo-1519741497674-611481863552", "photo-1465495976277-4387d4b0e4a6",
        "photo-1511285560929-80b456fea0bc", "photo-1583939003579-730e3918a45a",
    ],
    "Car / automotive": [
        "photo-1492144534655-ae79c964c9d7", "photo-1492144534655-ae79c964c9d7",
        "photo-1503376780353-7e6692767b70", "photo-1580273916550-e323be2ae537",
    ],
    "Fashion / apparel": [
        "photo-1483985988355-763728e1935b", "photo-1490481651871-ab68de25d43d",
        "photo-1469334031218-e382a71b716b", "photo-1487744480471-9ca1bca6fb7d",
    ],
}


def _build_unsplash_pool_block() -> str:
    """Build the prompt's photo-URL block by picking ONE id per category at random.

    Called fresh per generation so two consecutive runs of the same domain don't
    ship identical hero photos. The shape of the block matches the previous static
    list so downstream prompt rules ("Use AT MOST ONE of these URLs in the hero...")
    keep working unchanged.
    """
    import random as _rand
    lines = []
    for label, ids in _UNSPLASH_POOLS.items():
        picked = _rand.choice(ids)
        # 22-char left-justified label keeps the visual alignment of the old block.
        lines.append(f"  {label:<22}→ https://images.unsplash.com/{picked}")
    return "\n".join(lines)


def _parse_signature_imagery(cultural_atmosphere: str) -> list[str]:
    """Extract search terms from the signature_imagery field of ===CULTURAL_ATMOSPHERE===.

    Handles multiple Gemini output styles:
      - "term | mood"   (most common — pipe-separated)
      - "- term"        (dash bullet)
      - "term"          (bare phrase)
      - n/a / none      (no imagery — returns [])
    """
    if not cultural_atmosphere:
        return []
    keywords: list[str] = []
    in_block = False
    for line in cultural_atmosphere.splitlines():
        raw = line.strip()
        # Detect the field start
        if "signature_imagery:" in raw.lower():
            in_block = True
            # Inline value on the same line (unlikely but safe)
            remainder = raw.split(":", 1)[1].strip().strip('"').strip("'")
            if remainder and remainder.lower() not in ("n/a", "none", "na", ""):
                term = remainder.split("|")[0].strip()
                if len(term) > 3:
                    keywords.append(term)
            continue
        if not in_block:
            continue
        # Stop when we hit the next top-level field (unindented word followed by colon)
        if raw and not line.startswith((" ", "\t", "-", '"', "'")):
            if ":" in raw and "|" not in raw:
                break
        # Skip empty lines and skip-markers
        stripped = raw.lstrip("- •*").strip().strip('"').strip("'").strip(",")
        if not stripped or stripped.lower() in ("n/a", "none", "na", "[]"):
            continue
        # Extract the search term (everything before the first "|")
        term = stripped.split("|")[0].strip().strip('"').strip("'")
        # Skip bracket placeholders like "[search_term: ...]"
        if term.startswith("[") or len(term) < 4:
            continue
        keywords.append(term)

    return keywords[:8]  # cap to avoid rate-limit bursts


def _build_live_unsplash_block(photos: list[dict], keywords: list[str]) -> str:
    """Format live Unsplash API results into the same pool-block shape the prompt expects.

    Each line: "  <label>  → <url_hero>"
    Labels come from the search keywords. Falls back to the static pool if photos is empty.
    """
    if not photos:
        return _build_unsplash_pool_block()

    lines = []
    for i, photo in enumerate(photos):
        label = keywords[i] if i < len(keywords) else f"photo {i + 1}"
        lines.append(f"  {label[:28]:<28}→ {photo['url_hero']}")
    return "\n".join(lines)


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

====================================
LIVE UI — MAKE IT BREATHE (non-negotiable)
====================================
A "live" site feels like it's in motion even before the user scrolls. A static site
feels dead. Every section you write MUST implement the live_ui_recipe, scroll_reveal_style,
counter_animation, marquee_strip, and ambient_motion from the LAYOUT_BLUEPRINT.
If those fields aren't present, use the defaults below.

── 1. ANIMATED STAT COUNTERS ─────────────────────────────────────────────
Any time you display a number (stats, KPIs, years, clients, ratings), animate it
counting up when the element enters the viewport. Use this exact pattern:

  'use client'
  import { useEffect, useRef, useState } from 'react'
  import { useInView } from 'framer-motion'

  function AnimatedCounter({ target, suffix = '', prefix = '', duration = 1800 }) {
    const ref = useRef(null)
    const inView = useInView(ref, { once: true, margin: '-20% 0px' })
    const [display, setDisplay] = useState(0)
    useEffect(() => {
      if (!inView) return
      let start = 0
      const step = target / (duration / 16)
      const timer = setInterval(() => {
        start = Math.min(start + step, target)
        setDisplay(Math.round(start))
        if (start >= target) clearInterval(timer)
      }, 16)
      return () => clearInterval(timer)
    }, [inView, target, duration])
    return <span ref={ref}>{prefix}{display.toLocaleString()}{suffix}</span>
  }

  Usage: <AnimatedCounter target={500} suffix="+" /> → counts 0→500+
         <AnimatedCounter target={98} suffix="%" duration={1200} /> → counts 0→98%
         <AnimatedCounter target={4.9} suffix="★" duration={1000} /> → for decimals, adjust step

── 2. SCROLL REVEAL — STAGGER CHILDREN ───────────────────────────────────
Every content section (features, testimonials, pricing, team, menu items) uses
staggered child entry animations. This is the #1 pattern that makes a site feel
premium vs static. Use this exact framer-motion pattern:

  const containerVariants = {
    hidden: {},
    visible: { transition: { staggerChildren: 0.08, delayChildren: 0.1 } }
  }
  const itemVariants = {
    hidden: { opacity: 0, y: 32, scale: 0.97 },
    visible: { opacity: 1, y: 0, scale: 1, transition: { duration: 0.5, ease: [0.25, 0.46, 0.45, 0.94] } }
  }

  <motion.div
    variants={containerVariants}
    initial="hidden"
    whileInView="visible"
    viewport={{ once: true, amount: 0.15 }}
    className="grid grid-cols-1 md:grid-cols-3 gap-6"
  >
    {items.map((item, i) => (
      <motion.div key={i} variants={itemVariants}>
        {/* card content */}
      </motion.div>
    ))}
  </motion.div>

── 3. HERO ENTRY SEQUENCE ────────────────────────────────────────────────
The hero must NOT appear all at once. Use a cascading entry:
  - Badge/eyebrow: delay 0.1s, fade from opacity:0 y:12 → opacity:1 y:0
  - H1: delay 0.25s, clip-path reveal OR fade-up from y:40
  - Subtitle: delay 0.45s, fade from opacity:0
  - CTA buttons: delay 0.6s, fade-up + slight scale from 0.95
  - Hero image/media: delay 0.3s, fade from opacity:0 scale:1.02 → scale:1

  Implement using motion.div with initial/animate (NOT whileInView — hero is already visible):
  <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.1, duration: 0.6 }}>

── 4. HORIZONTAL MARQUEE STRIP ───────────────────────────────────────────
Logo rows, partner lists, testimonial snippets, or stat tickers must use a
CSS marquee (infinite scroll) — NOT a static centered row of logos.

  'use client'
  // Pure CSS marquee — no external library needed
  export function MarqueeStrip({ items, speed = 40 }) {
    return (
      <div className="overflow-hidden [mask-image:linear-gradient(to_right,transparent,white_10%,white_90%,transparent)]">
        <div
          className="flex gap-8 w-max"
          style={{ animation: `marquee ${items.length * speed}s linear infinite` }}
        >
          {[...items, ...items].map((item, i) => (
            <div key={i} className="flex items-center gap-2 shrink-0">
              {item}
            </div>
          ))}
        </div>
        <style>{`
          @keyframes marquee { from { transform: translateX(0) } to { transform: translateX(-50%) } }
        `}</style>
      </div>
    )
  }

  Use for: logo trust strips, testimonial quotes ticker, stat tickers, product photo strips.
  Place one between hero and first content section, and optionally before the CTA.

── 5. STICKY HEADER WITH SCROLL TRANSITION ───────────────────────────────
Headers must transition from transparent to frosted-glass on scroll. This is
standard on ALL premium sites in 2025-2026 and makes the page feel polished:

  'use client'
  import { useEffect, useState } from 'react'
  export default function Header() {
    const [scrolled, setScrolled] = useState(false)
    useEffect(() => {
      const fn = () => setScrolled(window.scrollY > 20)
      window.addEventListener('scroll', fn, { passive: true })
      return () => window.removeEventListener('scroll', fn)
    }, [])
    return (
      <header className={`fixed top-0 inset-x-0 z-50 transition-all duration-500 ${
        scrolled
          ? 'bg-background/90 backdrop-blur-md border-b border-border/60 shadow-sm'
          : 'bg-transparent border-b border-transparent'
      }`}>
        {/* nav content */}
      </header>
    )
  }

── 6. CARD HOVER — 3D TILT ───────────────────────────────────────────────
Feature cards and product cards use subtle 3D tilt on mouse hover. This is the
single biggest differentiator between a 2020 site (flat hover-shadow) and a
premium 2025-2026 site (spatial, dimensional hover):

  'use client'
  import { useRef } from 'react'
  function TiltCard({ children, className }) {
    const ref = useRef(null)
    const handleMouseMove = (e) => {
      const rect = ref.current.getBoundingClientRect()
      const x = (e.clientX - rect.left) / rect.width  - 0.5
      const y = (e.clientY - rect.top)  / rect.height - 0.5
      ref.current.style.transform = `perspective(600px) rotateY(${x * 8}deg) rotateX(${-y * 8}deg) scale3d(1.02,1.02,1.02)`
    }
    const handleMouseLeave = () => {
      ref.current.style.transform = 'perspective(600px) rotateY(0deg) rotateX(0deg) scale3d(1,1,1)'
    }
    return (
      <div ref={ref} onMouseMove={handleMouseMove} onMouseLeave={handleMouseLeave}
        className={`transition-transform duration-200 ease-out will-change-transform ${className}`}>
        {children}
      </div>
    )
  }

  Use TiltCard wrapper on: feature cards, pricing cards, service cards, product cards.
  Do NOT use on text paragraphs, nav items, or full-page sections.

── 7. AMBIENT BACKGROUND MOTION ──────────────────────────────────────────
Hero backgrounds must have gentle, slow movement — never completely static.
⚠️ 2026 NOTE: Two gradient orbs ALONE are the most recognizable AI-generated
cliché. ALWAYS layer orbs with at least one of Option B, C, or D below.

  Option A — Gradient orbs (MUST be combined with B, C, or D — not standalone):
  <div className="absolute inset-0 overflow-hidden pointer-events-none">
    <div className="absolute top-1/4 -left-20 w-96 h-96 bg-primary/20 rounded-full blur-3xl"
         style={{ animation: 'float1 12s ease-in-out infinite alternate' }} />
    <div className="absolute bottom-1/4 -right-20 w-80 h-80 bg-accent/15 rounded-full blur-3xl"
         style={{ animation: 'float2 16s ease-in-out infinite alternate-reverse' }} />
    <style>{`
      @keyframes float1 { from { transform: translate(0,0) scale(1) } to { transform: translate(40px,30px) scale(1.1) } }
      @keyframes float2 { from { transform: translate(0,0) scale(1) } to { transform: translate(-30px,40px) scale(1.08) } }
    `}</style>
  </div>

  Option B — Grain noise (tactile, editorial — pairs well with orbs AND alone):
  <div className="fixed inset-0 pointer-events-none z-[1] opacity-[0.04]"
       style={{ backgroundImage: "url(\"data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='noise'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23noise)'/%3E%3C/svg%3E\")", backgroundRepeat: 'repeat', backgroundSize: '128px' }} />

  Option C — Dot grid (structural, pairs with any palette):
  <div className="absolute inset-0 pointer-events-none"
       style={{ backgroundImage: 'radial-gradient(circle, hsl(var(--foreground)/0.08) 1px, transparent 1px)', backgroundSize: '28px 28px' }} />

  Option D — Topographic / contour lines (2026 premium signal):
  <div className="absolute inset-0 pointer-events-none opacity-[0.06]"
       style={{ backgroundImage: "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='100' height='100'%3E%3Cpath d='M0 50 Q25 30 50 50 Q75 70 100 50' fill='none' stroke='currentColor' stroke-width='0.5'/%3E%3Cpath d='M0 30 Q25 10 50 30 Q75 50 100 30' fill='none' stroke='currentColor' stroke-width='0.5'/%3E%3Cpath d='M0 70 Q25 50 50 70 Q75 90 100 70' fill='none' stroke='currentColor' stroke-width='0.5'/%3E%3C/svg%3E\")", backgroundSize: '100px 100px' }} />

── 8. TEXT CLIP-PATH REVEAL (for H1 / H2) ────────────────────────────────
Use this for dramatic section headings — text slides up from behind a clip mask:

  <div className="overflow-hidden">
    <motion.h2
      initial={{ y: '100%' }}
      whileInView={{ y: '0%' }}
      viewport={{ once: true }}
      transition={{ duration: 0.65, ease: [0.33, 1, 0.68, 1] }}
      className="text-4xl font-bold"
    >
      Section Headline
    </motion.h2>
  </div>

  Use this on H2 headings of 2-3 key sections (not every section — reserve it for high-impact moments).

── 9. CTA BUTTON — ANIMATED GRADIENT BORDER ──────────────────────────────
Primary CTAs should feel alive with a slow-rotating gradient border or
a subtle shimmer sweep on hover:

  /* Shimmer sweep on hover */
  .cta-shimmer {
    position: relative;
    overflow: hidden;
  }
  .cta-shimmer::after {
    content: '';
    position: absolute;
    inset: 0;
    background: linear-gradient(105deg, transparent 40%, rgba(255,255,255,0.15) 50%, transparent 60%);
    transform: translateX(-100%);
    transition: transform 0.5s ease;
  }
  .cta-shimmer:hover::after { transform: translateX(100%); }

  In Tailwind, use group-hover animation classes or the inline style above.
  Apply to the primary "Apply Now", "Book Now", "Get Started" button on each page.

====================================
DESIGN DNA — READ BEFORE EVERY SECTION
====================================
The LAYOUT_BLUEPRINT in the prompt contains these variables that MUST guide your
implementation:
  • live_ui_recipe     → your SIGNATURE MOMENT + supporting micro-interactions
  • scroll_reveal_style → exact enter animation (clip/fade-up/split-word/etc.)
  • counter_animation   → which numbers to animate and with what timing
  • marquee_strip       → location and content of horizontal auto-scroll strip
  • ambient_motion      → what moves in the hero background without user input
  • motion_language     → hover states, enter animations, scroll behaviors
  • hover_interaction_style → how cards, CTAs, images respond to hover

These are not optional. Every section must implement the design DNA.
A section that ignores these variables and uses default fade-in is a failure.

====================================
COLOR TOKENS — HARD BAN (read twice)
====================================
This project's `src/app/globals.css` is the SINGLE SOURCE OF TRUTH for color.
It already defines the project's bespoke palette via shadcn/ui CSS variables —
the deterministic builder wrote it BEFORE you started, with the exact warm /
editorial / brutalist / etc. colors the Design Director chose for THIS brand.
Every Tailwind class you write MUST resolve to one of those tokens. If you
hardcode a Tailwind palette utility, you erase the Design Director's work
and the project ends up looking like a generic gray template.

❌ FORBIDDEN — never use any of these:
   • bg-white, bg-black
   • bg-gray-*, bg-slate-*, bg-zinc-*, bg-neutral-*, bg-stone-*  (any shade)
   • text-white, text-black
   • text-gray-*, text-slate-*, text-zinc-*, text-neutral-*, text-stone-*
   • border-gray-*, border-slate-*, border-zinc-*, border-neutral-*, border-stone-*
   • bg-gradient-to-* using from-gray-*/to-gray-*/from-slate-*/etc.
     (gradients USING THE TOKENS are fine — see below)
   • Arbitrary hex/HSL color literals: bg-[#fff], text-[#000], bg-[hsl(0,0%,90%)]
   • Inline style={{backgroundColor: "..."}} or style={{color: "..."}}

✅ ALLOWED — use ONLY these (they pull from the globals.css palette):
   Backgrounds:  bg-background  bg-card  bg-popover  bg-muted
                 bg-primary  bg-secondary  bg-accent  bg-destructive
   Foregrounds:  text-foreground  text-card-foreground  text-popover-foreground
                 text-muted-foreground  text-primary-foreground
                 text-secondary-foreground  text-accent-foreground
                 text-destructive-foreground
   Borders/ring: border-border  border-input  ring-ring
   With opacity: bg-primary/10, bg-muted/40, bg-foreground/5  (any token + /N)
   Gradients:    bg-gradient-to-b from-background to-muted
                 bg-gradient-to-br from-primary/20 to-accent/10
                 (always token-based, never gray-N)

PAIRING RULE — every background MUST pair with its matching foreground:
   bg-background → text-foreground
   bg-card       → text-card-foreground
   bg-muted      → text-muted-foreground (for body) / text-foreground (for headings)
   bg-primary    → text-primary-foreground
   bg-secondary  → text-secondary-foreground
   bg-accent     → text-accent-foreground
   bg-destructive→ text-destructive-foreground
NEVER pair a token-bg with text-white / text-black — those won't adapt to
the project's actual palette and you get invisible-text bugs (white text on
a near-white surface, dark text on a near-dark surface).

QUICK FIX for the common temptation:
   "I want a subtle hero gradient" → bg-gradient-to-b from-background via-muted/40 to-background
   "I want a bold card"            → bg-card border border-border (NOT bg-white)
   "I want darker text"            → text-foreground (NOT text-gray-900)
   "I want lighter text"           → text-muted-foreground (NOT text-gray-500)
   "I want a hero overlay"         → bg-foreground/60 (NOT bg-black/60)

VIOLATIONS WILL BE LINTED. Files containing any forbidden class are flagged
post-generation and may be auto-rewritten — costing time. Use tokens up front.

====================================
REACT ITERATOR KEYS — HARD BAN (read twice)
====================================
EVERY .map() / .filter().map() / for-rendered iterator that returns JSX MUST
include a `key` prop on the OUTERMOST returned element. ESLint's `react/jsx-key`
rule is ENFORCED on Vercel and a single missing key will FAIL the production
build (exit code 1, deploy aborted).

✅ CORRECT:
   {features.map((f) => (
     <div key={f.id} className="...">...</div>
   ))}

   {items.map((item, i) => (
     <Card key={item.slug ?? i} {...item} />
   ))}

   {/* When wrapping in a Fragment, use <Fragment key=...> NOT <> */}
   {rows.map((r) => (
     <Fragment key={r.id}>
       <td>{r.name}</td>
       <td>{r.value}</td>
     </Fragment>
   ))}

❌ WRONG (will fail build):
   {features.map((f) => <div className="...">...</div>)}    // no key
   {items.map((item) => <><Card {...item} /></>)}            // <> can't take key

KEY-VALUE RULES:
   1. Prefer a stable unique field: `item.id`, `item.slug`, `item.href`
   2. Fall back to index ONLY when the list is static and never reordered:
      `items.map((item, i) => <Card key={i} ... />)`
   3. NEVER use `Math.random()` or `Date.now()` as a key — re-renders break
   4. The key goes on the element RETURNED by the map callback, not on its children

When in doubt, ALWAYS add a key. It is impossible to over-key — extra keys
never fail a build, missing keys always do.

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
  lucide-react                   ← icons ONLY (see icon import rule below)
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
SELECT INPUTS — HARD BAN on native <select> (non-negotiable)
====================================
NEVER use a native HTML `<select>` element — it is un-styled and breaks visual
consistency with the shadcn/ui Input components around it.

✅ ALWAYS use the shadcn/ui Select primitives:
  import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'

  <Select value={val} onValueChange={setVal}>
    <SelectTrigger>
      <SelectValue placeholder="Choose an option" />
    </SelectTrigger>
    <SelectContent>
      <SelectItem value="a">Option A</SelectItem>
      <SelectItem value="b">Option B</SelectItem>
    </SelectContent>
  </Select>

This rule applies to EVERY form on EVERY page type — admin, landing page, consumer website,
contact form, registration form, booking form, filter bar. No exceptions.

====================================
BACKGROUND-IMAGE SECTIONS — min-h required (non-negotiable)
====================================
Any section whose background is a photo (pattern: `<img className="absolute inset-0 ...">`)
MUST declare a minimum height on the `<section>` element itself.
Without it the section collapses to zero height and the photo disappears.

✅ CORRECT:
  <section className="relative min-h-[700px] flex items-center overflow-hidden">
    <img src="..." alt="" className="absolute inset-0 w-full h-full object-cover" />
    <div className="absolute inset-0 bg-gradient-to-t from-black/70 to-black/20" />
    <div className="relative z-10 ...">...</div>
  </section>

❌ WRONG (section collapses — photo invisible):
  <section className="relative overflow-hidden">
    <img src="..." alt="" className="absolute inset-0 w-full h-full object-cover" />
    ...
  </section>

Minimum height guide:
  Hero: min-h-screen or min-h-[90vh]
  Register / contact / form-over-photo: min-h-[700px]
  Testimonial / quote strip over photo: min-h-[400px]
  CTA banner over photo: min-h-[350px]

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
Every interactive element has a hover state + transition-colors duration-150 on hover/focus.

NEVER: layoutId on table rows | animate during loading

====================================
SCROLL-DRIVEN REVEALS — CSS-FIRST WITH MOTION FALLBACK (preferred)
====================================
For section-level entrance reveals (cards staggering in, headlines rising, images
fading), USE CSS `animation-timeline: view()` first. It's native, runs at 60fps on
low-end devices, doesn't bundle 30KB of framer-motion JS for a single fade-up,
and was tagged Baseline-2024 / Chrome+Edge 115+ / Safari 18+ / Firefox 130+.

Pattern — globals.css (write ONCE, every section reuses):

  @keyframes reveal-up {{
    from {{ opacity: 0; transform: translateY(24px); }}
    to   {{ opacity: 1; transform: translateY(0); }}
  }}
  /* Default (fallback for browsers without scroll-driven animations): static, no animation */
  .reveal-up {{ opacity: 1; }}

  @supports (animation-timeline: view()) {{
    .reveal-up {{
      animation: reveal-up var(--d-base, 550ms) var(--ease-sig, cubic-bezier(0.16, 1, 0.3, 1)) both;
      animation-timeline: view();
      animation-range: entry 10% cover 35%;
    }}
    /* Stagger: children get incrementally offset start positions via animation-delay */
    .reveal-up.stagger-1 {{ animation-delay: 80ms;  }}
    .reveal-up.stagger-2 {{ animation-delay: 160ms; }}
    .reveal-up.stagger-3 {{ animation-delay: 240ms; }}
    .reveal-up.stagger-4 {{ animation-delay: 320ms; }}
  }}

  @media (prefers-reduced-motion: reduce) {{
    .reveal-up {{ animation: none; opacity: 1; transform: none; }}
  }}

Usage in JSX (NO framer-motion needed for these — saves bundle size + js cost):

  <section>
    <h2 className="reveal-up">Section headline</h2>
    <p  className="reveal-up stagger-1">Sub-paragraph</p>
    <div className="grid md:grid-cols-3 gap-6">
      <Card className="reveal-up stagger-1" />
      <Card className="reveal-up stagger-2" />
      <Card className="reveal-up stagger-3" />
    </div>
  </section>

When to STILL use framer-motion (not CSS):
  • Hero entrance sequences with cascading delays (badge → H1 → sub → CTAs)
    — these run on initial load, not on scroll, so animation-timeline doesn't apply.
  • Hover micro-interactions (whileHover scale/rotate) where state-driven animation matters.
  • Layout transitions (`layoutId`) on filtering/reordering lists.
  • Scroll-linked scrubbing (`useScroll` + `useTransform`) — the signature_transition
    types like `sticky-pin-scrub` and `parallax-layered`.

For everything else (the 80% case of "card grid stagger as user scrolls in"), use CSS
scroll-driven. Faster, smaller bundle, smoother on mid-tier devices.

Bundle-size rule: if a section's only animation is reveal/stagger/fade-up, do NOT
import motion at all. CSS classes only.

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
COPY_DECK — BRAND-SPECIFIC COPY (CONSUMER / LANDING ARCHETYPES)
───────────────────────────────────────────────────────────────
If a ===COPY_DECK=== block is present (landing/consumer projects only):
EVERY hero headline, section headline/subheadline, feature title/
description, CTA, microcopy string, footer tagline, and SEO meta
is FIXED by this deck. You MUST use the EXACT strings from the deck
wherever they apply:

  - Hero component  → use hero.eyebrow / hero.headline / hero.subheadline
                      / hero.primary_cta / hero.secondary_cta
  - Each section    → match by section_id (story → story section,
                      menu → menu section, etc.) and use that entry's
                      eyebrow / headline / subheadline / cta_primary
  - Feature cards   → use features[].title + features[].description
                      verbatim. The icon hint is a lucide-react name.
  - Forms           → use microcopy.form_submit_primary as the submit
                      button text, microcopy.form_submitting as the
                      loading state
  - Newsletter      → use microcopy.newsletter_cta for the button,
                      microcopy.newsletter_placeholder for the input
  - Empty states    → use microcopy.empty_state_headline
  - Footer          → use footer.tagline, footer.newsletter_pitch,
                      footer.copyright_suffix
  - <head>          → use seo.meta_title + seo.meta_description

ABSOLUTELY BANNED — never emit any of these strings (the deck gives
you a specific, brand-voiced replacement for each):
  "Learn More"  "Get Started" (unless B2B SaaS)  "Our Services"
  "Our Features"  "Why Choose Us"  "Welcome to [anything]"
  "Premium Quality"  "Best in class"  "Cutting-edge"  "World-class"
  "One-stop shop"  "Click here"  "Read more →"  "Subscribe to our newsletter"
  "Lorem ipsum"  "Your content here"  "Subtitle"  "Placeholder"

If the deck doesn't cover a string you need (e.g. individual FAQ
questions, testimonial quotes), write new copy in the SAME voice
and specificity as the deck — never fall back to generic filler.

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

  <section className="relative min-h-[700px] flex items-center overflow-hidden">
    <img className="absolute inset-0 w-full h-full object-cover" src="..." />
    <div className="absolute inset-0 bg-gradient-to-t from-black/60 to-black/20" />
    <div className="relative z-10 container">
      <div className="bg-card text-card-foreground rounded-{radius} shadow-xl p-8 ...">
        {/* form fields here — input bg-background, never transparent */}
        {/* NEVER use native <select> — use shadcn Select with SelectTrigger/SelectContent/SelectItem */}
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
  hero_image_strategy           → background_full | structural_half | single_feature_card | decorative_scatter | illustration_3d | typographic_only
  hero_image_url                → URL or pipe-separated list or "GENERATE_3D" or "NONE" (depends on strategy)
  supporting_image_urls         → URLs for other sections, mapped by section
  accent_detail                 → one signature micro-detail + placement

  DESIGN DNA variables (NEW — these control the unique visual language):
  hero_archetype                → split / bento / diagonal / magazine / layered-scroll / cinematic-parallax / editorial-offset / full-bleed-dark / product-showcase / typographic-hero / invented
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

  ╔══════════════════════════════════════════════════════════════════╗
  ║ HARD ENFORCEMENT: The LAYOUT_BLUEPRINT contains ONE assigned     ║
  ║ hero_archetype value (e.g. "magazine" or "split"). You MUST use  ║
  ║ THAT skeleton verbatim. The list below is a REFERENCE so you     ║
  ║ know what each value means — NOT a menu to pick from.            ║
  ║                                                                  ║
  ║ FORBIDDEN: producing a hero pattern that doesn't match the       ║
  ║ assigned hero_archetype. Specifically forbidden as a fallback:   ║
  ║   • full-bleed photo background with dark scrim                  ║
  ║   • absolute inset-0 object-cover Image as the wrapper           ║
  ║   • white-text-over-dark-photo hero unless hero_archetype is     ║
  ║     literally "full-bleed-dark" or "cinematic-parallax"          ║
  ║                                                                  ║
  ║ This default has shipped on 4 consecutive generations and made   ║
  ║ every site look identical. Break the pattern.                    ║
  ╚══════════════════════════════════════════════════════════════════╝

    split               → grid md:grid-cols-2 gap-12 items-center min-h-[85vh]
                          (LIGHT background — text col-1, image col-2; no dark scrim)
    bento               → grid md:grid-cols-6 md:grid-rows-3 gap-4 min-h-[85vh]
                          (one tile md:col-span-4 md:row-span-2 holds H1)
    diagonal            → relative overflow-hidden + inner
                          <div className="absolute inset-0 bg-primary"
                                 style={{clipPath:'polygon(0 0, 55% 0, 40% 100%, 0 100%)'}} />
    magazine            → grid md:grid-cols-12 gap-8, H1 at col-span-9 text-[clamp(3rem,10vw,9rem)]
                          font-bold leading-[0.9], image at col-span-3 col-start-10 self-start
                          (LIGHT background — H1 is the visual hero, image is small)
    layered-scroll      → relative min-h-screen with 3 absolute layers, data-parallax speeds
    cinematic-parallax  → relative h-screen with <img absolute inset-0 object-cover scale-110> +
                          dark gradient overlay + centered text-white H1
    editorial-offset    → relative h-[90vh] + H1 absolute top-16 left-12 + image absolute
                          bottom-0 right-0 w-[55%] aspect-[4/5] object-cover
                          (LIGHT background — image is bottom-right card, NOT full-bleed)
    full-bleed-dark     → relative min-h-[90vh] + img absolute inset-0 + bg-gradient-to-t
                          from-black/80 via-black/40 to-transparent overlay + text-white
                          (only use if BLUEPRINT explicitly assigned this — never as fallback)
    product-showcase    → grid md:grid-cols-5 gap-8, device mockup at col-span-3, text col-span-2
                          (LIGHT or branded background — product photo is the hero, NOT a scrim)
    typographic-hero    → py-40 text-center with H1 at text-[clamp(4rem,14vw,12rem)] font-black
                          (NO photo background — pure typography)

▸ hero_image_strategy → how to render imagery in the hero:
    background_full     → only when hero_archetype is cinematic-parallax / full-bleed-dark.
                          <Image src={{hero_image_url}} className="absolute inset-0 object-cover" />
                          + scrim overlay + white text. Anywhere else: FORBIDDEN.

    structural_half     → image is one column of a grid. NEVER absolute inset-0.
                          <Image src={{hero_image_url}} className="rounded-2xl object-cover aspect-[4/5]" />
                          inside its grid cell. Light page bg, text-foreground (NOT text-white).

    single_feature_card → image inside a small card, NOT a backdrop. Wrap in:
                          <div className="rounded-3xl bg-card p-3 shadow-lg">
                            <Image className="rounded-2xl aspect-[4/3] object-cover" />
                          </div>
                          The image is decorative — H1 is the visual hero.

    decorative_scatter  → hero_image_url is pipe-separated (3-5 URLs). Render each as a small
                          rotated card absolutely positioned around the centered headline:
                          <div className="absolute top-12 right-[18%] w-32 aspect-[4/5] rotate-6 rounded-xl shadow-xl overflow-hidden">
                            <Image src={{urls[0]}} className="object-cover" />
                          </div>
                          (different sizes/rotations/positions per image — NEVER stack them in a row)

    illustration_3d     → hero_image_url is "GENERATE_3D". DO NOT use Unsplash. Instead build:
                          (a) an inline SVG illustration with gradient blobs, isometric shapes,
                              or abstract geometric scene matching the brand palette, OR
                          (b) a CSS-only 3D scene: stacked rounded shapes with shadow + gradient
                              backgrounds + blur orbs (no image src at all).
                          Position it where the image would otherwise go for the chosen
                          hero_archetype (e.g. col-2 of split, bottom-right of editorial-offset).

    typographic_only    → hero_image_url is "NONE". Render NO image at all. The hero is pure
                          typography. Only valid when hero_archetype is typographic-hero.

  COUPLING (will be visually obvious if violated):
    cinematic-parallax / full-bleed-dark → background_full ONLY
    typographic-hero                     → typographic_only ONLY
    split / editorial-offset / product-showcase → structural_half | illustration_3d
    magazine / bento                     → single_feature_card | decorative_scatter | illustration_3d
    diagonal / layered-scroll            → any except background_full

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

  HERO H1 LEGIBILITY RULES (override blueprint when these apply):
    • If H1 font is a display SERIF (Fraunces, Playfair, DM Serif, Cormorant, Bodoni):
        - Cap font size at clamp(2.75rem, 7vw, 5.5rem) — NEVER 8rem+ italic display serif
          (at extreme sizes the italic 'p'+'a' overlaps and reads as 'b' — "Spain"
          renders as "Sbain"; "place" as "blace")
        - Use tracking-tight or tracking-[-0.02em] — italic display serifs need
          NEGATIVE letter-spacing at large sizes or letters merge
        - Prefer roman (no italic class) for the FULL headline. If the blueprint
          says "italic for H1", apply italic to ONE WORD ONLY (the accent word in
          a different color), not the whole sentence.
        - Always add leading-[1.05] or tighter — italic display serifs visually
          collide with the descenders of the line above at default leading
    • If hero_archetype is editorial-offset / split / product-showcase (light bg):
        - H1 is text-foreground (NOT text-white) — display serifs lose stroke
          contrast on light/cream backgrounds; bump font-weight to 700+ if cream
    • Universal: NEVER ship a hero H1 wider than max-w-5xl. Long single-line
      display serifs at viewport width is the AI-generated hero giveaway.

    • ACCENT WORD PLACEMENT (the "Sbain on a plate / Where Naples Comes to Your Table"
      problem — italic accent word breaks alone onto its own line at the end):
        - When using <span className="italic text-primary"> or similar to highlight
          ONE word in the headline, that word MUST sit IN-LINE with surrounding text,
          NEVER alone on its own line. If the word naturally falls at line end:
          (a) wrap the word + the preceding word together inside the span so they
              break together, OR
          (b) put a <br /> BEFORE the accent phrase to make it intentional, OR
          (c) wrap the entire accent phrase (3-4 words) in the span so it occupies
              a full line by itself rather than one orphan word.
        - The pattern "...Comes to Your <span>Table</span>" with "Table" wrapping
          alone reads as a layout bug. Either rewrite to put the accent at the
          start ("<span>Tonight</span>, Naples Comes to Your Table") or expand the
          span ("...Comes to <span>Your Table Tonight</span>").
        - Headlines with accent words should have a LIGATURE-SAFE choice: avoid
          one-word italic accents containing 'p', 'a', 'b', 'g' next to similar
          letters at extreme display sizes — the italic shapes merge.

▸ motion_language → add Framer Motion or Motion One (preferred: motion/react), wrap components:
    stagger-fade-up     → <motion.div initial={{opacity:0,y:20}} whileInView={{opacity:1,y:0}}
                             viewport={{once:true}} transition={{duration:0.5,delay:i*0.08}}>
    tilt-on-hover       → <motion.div whileHover={{rotate:2}} transition={{type:'spring',stiffness:200}}>
    minimal-no-scroll   → NO Framer wrapper; only Tailwind hover classes:
                          "transition-all duration-200 hover:-translate-y-1 hover:shadow-xl"
    parallax-scroll     → use useScroll + useTransform from motion/react for background layers
    Install: add "framer-motion" to package.json dependencies if motion_language isn't "minimal".

▸ motion_signature → ONE consistent motion DNA across the whole project. Look for the
  `motion_signature:` line in LAYOUT_BLUEPRINT. It carries 4 strict tokens:

  EASING_SIGNATURE (apply this curve to EVERY transition/motion.div):
    quint-out         → cubic-bezier(0.16, 1, 0.3, 1)         ← awwwards default — premium feel
    expo-out          → cubic-bezier(0.19, 1, 0.22, 1)        ← confident, snappy
    circ-out          → cubic-bezier(0, 0.55, 0.45, 1)        ← soft, organic
    back-out-subtle   → cubic-bezier(0.34, 1.2, 0.64, 1)      ← playful, small overshoot
    linear-precise    → linear                                ← brutalist / dense_luxury
    spring-quiet      → use Motion's spring(80, 16) — no cubic-bezier

    In CSS: `transition-timing-function: <curve>;`
    In Motion: `transition={{ease: [0.16, 1, 0.3, 1], duration: ...}}`
    NEVER mix curves across sections.

  DURATIONS (3 named values — use them everywhere; no ad-hoc values):
    fast  → hover, focus, micro-interactions (e.g. 180ms)
    base  → reveals, card stagger entrances (e.g. 550ms)
    slow  → hero entrance, dramatic moments (e.g. 1000ms)

    Encode in CSS as variables in globals.css:
      :root {{
        --d-fast: 180ms; --d-base: 550ms; --d-slow: 1000ms;
        --ease-sig: cubic-bezier(0.16, 1, 0.3, 1);
      }}
    Then `transition-duration: var(--d-fast); transition-timing-function: var(--ease-sig);`
    Or in Motion: `transition={{duration: 0.55, ease: [0.16, 1, 0.3, 1]}}`

  SIGNATURE_TRANSITION (build ONE bespoke component, reuse in hero AND ≥1 other section):
    mask-reveal-diag    → clip-path:polygon(0 0,100% 0,100% 100%,0 100%) animates from
                          polygon(0 0,0 0,0 100%,0 100%) (a sweep from left). On scroll-in.
    weight-shift        → variable-font: animate font-variation-settings:'wght' 100→700
                          on viewport entry over base duration. Requires variable font.
    sticky-pin-scrub    → sticky outer + inner translateY scrubbing — Framer
                          useScroll+useTransform with target: ref, offset: ['start end','end start']
    horizontal-rail     → vertical pin (sticky top-0 h-screen) wrapping a flex row that
                          translateX scrolls horizontally based on scroll progress.
    chromatic-glitch    → 3 stacked text/img layers (red/green/blue tinted) offset by 1-2px
                          on hover; merge to single layer at rest.
    duotone-fade        → grayscale(100%) at rest → grayscale(0) over base on viewport-enter.
                          Use `filter: grayscale(...)` with a transition.
    kinetic-typography  → split headline into <span> per char; each rises with stagger
                          delay = i * 40ms.
    parallax-layered    → 3 absolute div layers with different translateY based on scroll.
                          Use Framer useScroll + useTransform([0,1],[0,Δ]).
    magnetic-pull       → primary CTA tracks cursor in 80px radius; useEventListener
                          + transform translate(x*0.2, y*0.2).
    minimal-precise     → ONE 200ms fade-up on viewport entry, nothing else. For
                          brutalist_mono / sharp_corporate / spatial_functional.

    REQUIREMENT: build the chosen signature as ONE reusable component
    (e.g. <SignatureMaskReveal>, <ChromaticHover>, <HorizontalRail>) and use it
    in the hero AND at least one other section. A single signature applied
    twice is what makes a site read as 'designed' instead of 'assembled'.

  CURSOR_TREATMENT (perceived-quality lift; respects prefers-reduced-motion):
    default       → no override
    magnetic      → wrap primary CTAs in <MagneticButton> that translates toward cursor
                    when within 80px (transform translate(x*0.25, y*0.25))
    custom-blob   → fixed div following cursor with mix-blend-mode:difference, scales 2x
                    over interactive elements
    crosshair     → 1px horizontal + 1px vertical line tracking cursor; fade out on idle

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
     ⚠️  LEFT column = text on a CLEAN background. NO background image on the <section> or
         the text <div>. The photo lives ONLY inside the right column div. Do NOT use a
         bg-[url(...)] or an absolute <img> on the section wrapper for this layout.
     <section className="bg-background relative min-h-[90vh] grid md:grid-cols-2 gap-12 items-center
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
  ⚠️  Use AT MOST ONE of these URLs in the hero section — not two. For split screen,
      pick ONE URL for the right column. Do NOT also use a second URL as a section
      background. Multiple domain photos in the same hero is the #1 banner regression.

  ⚠️  PRIORITY: When ===CULTURAL_ATMOSPHERE=== signature_imagery contains a more
      specific search term for the project (e.g. "blistered cornicione of margherita",
      "tahona stone wheel in palenque", "binchotan yakitori grill smoke"), PREFER
      finding a real Unsplash URL for that specific scene over the generic pool
      below. The generic pool is a fallback. Cultural specificity beats stock.

<<UNSPLASH_POOL_BLOCK>>

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
6) SECTION DIFFERENTIATION MANDATE (self-check before every section)
------------------------------------------------------------
Before writing any section, run this checklist mentally:
  □ Card style differs from the section directly above it?
  □ Background treatment differs from both neighboring sections?
  □ Column/layout count differs from the section directly above it?
  □ At least ONE non-rectangular or non-uniform element present?

If any answer is NO — redesign the section layout before writing JSX.

ANTI-SAMENESS PATTERNS that guarantee a generic-looking page:
  ✗  3+ identical cards in a row — same padding, same icon size, same text length
  ✗  Two consecutive sections with bg-background (zero rotation)
  ✗  Features as 3-column grid with icon-above + title + body text (THE most common AI output)
  ✗  Testimonials as 3 identical white cards with avatar circle + name + stars
  ✗  CTA section as centered H2 + subtext + one button on bg-primary — zero texture
  ✗  About section as text-left + team photo-grid right (seen on every template)
  ✗  Footer as 4 symmetric columns with identical visual weight

WHAT TO DO INSTEAD (pick one variation per section that breaks the template):
  Features  → numbered-editorial (text-8xl font-black "01") | bento-mixed (hero tile col-span-2)
              | zigzag (image alternates left/right) | tilt-stack (rotated hover cards)
  Testimonials → single rotating pull-quote (auto-scroll, no grid) | masonry columns
                 | horizontal scroll strip | one featured + 2 small
  CTA       → full-bleed image with dark overlay + one bold verb | split (text left, visual right)
              | animated counter stat row above the CTA button
  About     → timeline vertical | editorial 2-column with large pull-quote | full-bleed with motif

VISUAL SURPRISE (from ===VISUAL_DISTINCTIVENESS=== in research) — MANDATORY:
  The research block contains a visual_surprise field. That element MUST be implemented.
  Do NOT skip it. It is the ONE thing that makes the page memorable.

------------------------------------------------------------
7) LOCATIONS / CONTACT SECTION — never pin-icon-only
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


def _phase_rules_prefix(phase: int, unsplash_block: str | None = None) -> str:
    """Return the phase-specific rules block to prepend to the user prompt.

    Goes in the user message (not system) so the system prompt stays stable
    for caching. Still gives the model focused rules for the phase:
    Phase 1 sees CSS/theme/nav, Phase 2 sees content/API/feedback patterns,
    Phase 3 sees the lighter completeness checklist.

    unsplash_block: pre-fetched live Unsplash URLs for this project's keywords.
    When provided, these real photos replace the static pool. Falls back to the
    static pool when None (recovery calls, cached paths, legacy code).
    """
    if phase == 1:
        return PHASE_APPENDIX_FOUNDATION
    if phase == 3:
        return PHASE_APPENDIX_COMPLETENESS
    # Phase 2 (CONTENT) — substitute live Unsplash URLs when available,
    # otherwise fall back to the rotating static pool.
    block = unsplash_block if unsplash_block is not None else _build_unsplash_pool_block()
    return PHASE_APPENDIX_CONTENT.replace("<<UNSPLASH_POOL_BLOCK>>", block)


# Back-compat alias — any legacy reference to SYSTEM_PROMPT gets a reasonable
# default (CORE + full content appendix, matches previous superset behaviour).
# Photo pool is substituted at import time; callers that need fresh photos per
# generation should use _get_appendix_for_phase(2) instead.
SYSTEM_PROMPT = (
    SYSTEM_PROMPT_CORE
    + PHASE_APPENDIX_FOUNDATION
    + PHASE_APPENDIX_CONTENT.replace("<<UNSPLASH_POOL_BLOCK>>", _build_unsplash_pool_block())
    + PHASE_APPENDIX_COMPLETENESS
)


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
    # Normalize user-controlled inputs before they flow into prompts.
    # An oversized description can blow past the context window; None or
    # whitespace-only input leaves the downstream prompts with "" which
    # trivially degrades classification and research quality.
    description = _normalize_prompt_input(description, max_chars=_MAX_DESCRIPTION_CHARS)
    if not description:
        await _ws_send(websocket, "error", "❌ Project description is empty.")
        return False
    try:
        return await _generate_new_project_inner(
            description, workspace_path, validated, websocket,
            chat_session_id, user_jwt,
        )
    except Exception as exc:
        logger.error("generate_new_project crashed: %s", exc, exc_info=True)
        await _ws_send(websocket, "error", f"❌ Generation failed: {str(exc)[:200]}")
        return False


def _audit_admin_entity_coverage(
    workspace_path: str,
    entities: list,
) -> list[dict]:
    """Return entities whose CRUD UI is missing from the generated project.

    Phase 2 generates 4 files per entity in heavy admin batches:
      • src/features/<entity>/services/<entity>.service.{js,jsx,ts,tsx}
      • src/features/<entity>/hooks/use<Entity>.{js,jsx,ts,tsx}
      • src/features/<entity>/pages/<Entity>ListPage.{js,jsx,ts,tsx}
      • src/features/<entity>/pages/<Entity>FormPage.{js,jsx,ts,tsx}

    A batch can fail silently (transient stream stall, max_tokens truncation)
    and the merge step at the call site just takes whatever batches succeeded
    — leaving 3 entities with NO UI in the final project. The Phase 3
    completeness prompt only enumerates `schema.pages`, not `schema.entities`,
    so it doesn't catch this on its own.

    This auditor walks the workspace, looks for the `<Entity>ListPage.*`
    file for each entity, and returns the entities that are missing. Caller
    decides what to do (recovery batch, Phase 3 hint, hard error).

    Returns: list of entity dicts (the original schema objects) that are
    missing their ListPage. Empty list = full coverage.
    """
    if not entities or not workspace_path:
        return []

    src_dir = os.path.join(workspace_path, "src")
    if not os.path.isdir(src_dir):
        return entities  # No src dir → everything is missing

    # Index every JSX/TSX file in the project once so the per-entity check
    # is O(1) instead of os.walk per entity.
    found_list_pages: set[str] = set()
    found_form_pages: set[str] = set()
    for root, dirs, files in os.walk(src_dir):
        dirs[:] = [d for d in dirs if d not in ("node_modules", ".git", ".next", "dist", "build")]
        for fname in files:
            if not fname.endswith((".js", ".jsx", ".ts", ".tsx")):
                continue
            stem = os.path.splitext(fname)[0]
            if stem.endswith("ListPage"):
                found_list_pages.add(stem[: -len("ListPage")].lower())
            elif stem.endswith("FormPage"):
                found_form_pages.add(stem[: -len("FormPage")].lower())

    missing: list[dict] = []
    for ent in entities:
        if not isinstance(ent, dict):
            continue
        name = (ent.get("name") or "").strip()
        if not name:
            continue
        # Match on lowercased entity name. Tolerate plurals: schema name
        # is typically singular (Vehicle), file is VehicleListPage.
        key = name.lower()
        if key not in found_list_pages or key not in found_form_pages:
            missing.append(ent)

    return missing


async def _recover_missing_entities(
    workspace_path: str,
    missing_entities: list,
    project_schema: dict,
    description: str,
    api_key: str,
    websocket,
    research_distilled: str,
    manifest_sliced: str,
    file_tree: str,
    stack_rules: str,
    api_env: str,
    api_default: str,
) -> int:
    """Run ONE focused Claude call to generate CRUD for missing entities.

    Called between Phase 2 and Phase 3 when the entity-coverage auditor
    finds gaps. Trades a ~30-60s cost for guaranteed full coverage on
    bigger admin panels — preferable to silently shipping a project with
    half its sidebar nav linking to non-existent pages.

    Returns the number of files written (0 on failure). Failure is
    non-fatal: caller continues to Phase 3 which has its own gap-fill logic.
    """
    if not missing_entities:
        return 0

    from app.services.project_schema import schema_to_entity_spec

    # Build a minimal schema slice with only the missing entities so the
    # spec helper produces a focused entity list.
    recovery_schema = {**project_schema, "entities": missing_entities}
    recovery_spec = schema_to_entity_spec(recovery_schema)
    missing_names = ", ".join(e.get("name", "?") for e in missing_entities)

    await _ws_send(
        websocket,
        "progress",
        f"🔁 Phase 2.5 — recovering {len(missing_entities)} missing entit"
        f"{'ies' if len(missing_entities) != 1 else 'y'}: {missing_names}",
    )
    logger.info(
        "Phase 2.5 entity recovery: generating CRUD for %d missing entit%s — %s",
        len(missing_entities),
        "ies" if len(missing_entities) != 1 else "y",
        missing_names,
    )

    recovery_prompt = f"""PHASE 2.5 — ENTITY COVERAGE RECOVERY

The Phase 2 batch generation missed CRUD modules for these entities:
  {missing_names}

These entities exist in the schema but their feature folders were not
written (the parallel batch that owned them either failed or truncated).

For EACH missing entity below, create the COMPLETE feature folder with
ALL FOUR files. This is the ONLY chance to recover them — Phase 3 won't
backfill missing entity CRUD.

  - src/features/<entity>/services/<entity>.service.{{js,jsx,ts,tsx}}
  - src/features/<entity>/hooks/use<Entity>.{{js,jsx,ts,tsx}}
  - src/features/<entity>/pages/<Entity>ListPage.{{js,jsx,ts,tsx}}
  - src/features/<entity>/pages/<Entity>FormPage.{{js,jsx,ts,tsx}}

IMPORTANT — API-READY SERVICES:
  const API_URL = import.meta.env.{api_env} || '{api_default}';
  Services must use REAL fetch() calls. DO NOT hardcode mock data arrays.
  Catch errors and return empty arrays on failure.

IMPORTANT — DESIGN SYSTEM:
  Import {{ ds }} from '@/lib/design-system' in ALL components.
  Use ds.card for card wrappers, ds.badge[status] for status badges.

IMPORTANT — DO NOT regenerate any other files. Only the missing entities.
The foundation, sidebar, layouts, and other entities' CRUD already exist.

{recovery_spec}

PROJECT: {description}

DESIGN SYSTEM FROM RESEARCH:
{research_distilled}

TEMPLATE MANIFEST:
{manifest_sliced}

CURRENT FILE TREE:
{file_tree[:2000]}

{stack_rules}

Call the write_project_files tool with the recovery files only.
"""

    try:
        result = await asyncio.wait_for(
            call_claude_for_json(
                system_prompt=_system_prompt_for_phase(2),
                user_prompt=_phase_rules_prefix(2) + "\n" + recovery_prompt,
                api_key=api_key,
                websocket=websocket,
                # 4 files × N entities; size cap stays well under truncation risk.
                max_tokens=min(48000, 12000 * len(missing_entities)),
                model=DEFAULT_MODEL,
                extended_output=True,
            ),
            timeout=240.0,  # 4-min cap — recovery is bonus work, don't block Phase 3
        )
    except asyncio.TimeoutError:
        logger.warning("Phase 2.5 entity recovery timed out — proceeding to Phase 3")
        return 0
    except Exception as exc:
        logger.warning("Phase 2.5 entity recovery failed: %s", exc)
        return 0

    if not result or not isinstance(result, dict) or not result.get("files"):
        logger.warning("Phase 2.5 entity recovery returned no files")
        return 0

    written = write_files_from_json(result, workspace_path)
    if written:
        await _emit_file_writes(websocket, written, action="recover")
        await _ws_send(
            websocket,
            "progress",
            f"✅ Recovered {len(written)} file(s) for missing entities",
        )
        logger.info("Phase 2.5 recovered %d files for entities: %s", len(written), missing_names)
    return len(written)


def _validate_plan_data(plan_data: dict, archetype: str) -> list[str]:
    """Sanity-check the plan-card payload before emitting it to the user.

    Returns a list of issue strings (empty list = plan looks solid). Today
    we only LOG the issues — every gap should already be backfilled by
    archetype defaults / brand sanitisation upstream — but the structured
    log gives us visibility into how often plans are still thin in prod
    and which fields are the weakest. Promote to a hard block once the
    field is quiet.

    Checks per archetype:
      consumer/portfolio/blog/marketplace → ≥3 pages
      single_page_landing                  → ≥3 page_items (sections)
      admin/crm/tms/saas/ecommerce         → ≥1 entity
      ALL                                  → brand looks like a name (not prose)
                                           → design line mentions fonts AND palette
                                           → about/description non-empty
    """
    issues: list[str] = []
    brand = (plan_data.get("intro") or "")
    pages = plan_data.get("pages") or []
    entities = plan_data.get("entities") or []
    design = (plan_data.get("design") or "").lower()
    description = (plan_data.get("description") or "").strip()

    # Brand sanity — if the intro contains telltale prose patterns, the
    # downstream brand sanitiser missed something.
    intro_low = brand.lower()
    if any(p in intro_low for p in (
        "this project", "project is for", "page for ", "website for ",
    )):
        issues.append("brand_intro_looks_like_prose")

    if not description or len(description) < 30:
        issues.append("description_thin")

    _multipage = {"consumer_website", "portfolio", "blog", "marketplace"}
    _admin = {"admin_dashboard", "crm", "tms", "saas_dashboard", "ecommerce"}

    if archetype == "single_page_landing":
        if len(pages) < 3:
            issues.append(f"too_few_sections:{len(pages)}")
    elif archetype in _multipage:
        if len(pages) < 3:
            issues.append(f"too_few_pages:{len(pages)}")
    elif archetype in _admin:
        if not entities:
            issues.append("no_entities")
        # Admin without sidebar nav is unusable
        if not pages and not entities:
            issues.append("admin_empty_structure")

    if "fonts" not in design:
        issues.append("design_missing_fonts")
    if "palette" not in design:
        issues.append("design_missing_palette")

    return issues


def _phase_token_budget(
    schema: dict,
    phase: int,
    archetype: str,
) -> tuple[int, bool]:
    """Return (max_tokens, extended_output) for a generation phase.

    claude-sonnet-4-6 has a 64K native output cap and every phase generates
    multiple full React files, so we run at the ceiling except for the
    narrower single-page-landing archetype where phase 1 (shell) and
    phase 3 (polish) can afford smaller caps for a snappier response.
    The ``schema`` argument is retained for API stability in case future
    phases re-introduce complexity-driven sizing.
    """
    del schema  # reserved for future complexity-driven sizing
    if archetype == "single_page_landing":
        if phase == 1: return (32000, True)   # theme + shell + nav
        if phase == 2: return (64000, True)   # all sections — needs full budget
        return (48000, True)                  # phase 3 polish
    # All other archetypes use the full 64K output window every phase.
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

    # ── Step 2.5: Expand very short prompts ──
    # "ACCA website" or "yoga studio" yields empty research blocks because Gemini
    # has nothing concrete to anchor on. Expanding here propagates richer context
    # to the cache key, deep research, schema parser, and plan rendering.
    # We preserve the original (pre-expansion) prompt because brand-name and
    # slug derivation work better on the clean short input than on the
    # prose-heavy expansion (which can leak phrases like "This project is for…"
    # into downstream fields). When no expansion happens, the two are identical.
    original_description = description.split("\n\n---\n\n")[0].strip()
    description = await _expand_short_prompt(
        description, _layout_archetype, _domain, gemini_key, websocket,
    )

    # ── Step 2: Read template context ──
    manifest = _read_manifest(workspace_path)
    _manifest_issue = _validate_manifest(manifest, workspace_path)
    if _manifest_issue:
        logger.warning("Template manifest validation: %s", _manifest_issue)
        await _ws_send(
            websocket,
            "warning",
            f"⚠️ {_manifest_issue} — generation will proceed but output quality may be degraded.",
        )
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

    # Research cache keyed by md5(description+stack+chat_session_id)[:14].
    # Including chat_session_id is important: WITHOUT it, two users (or the
    # same user starting a fresh chat) generating "Coffee Roaster landing
    # page" within 10 minutes of each other got the IDENTICAL Gemini research
    # AND Design Director output — same archetype, same palette, same
    # warm_artisan everything. That's the opposite of "every generation
    # should feel different." Including the session id means:
    #   • Retry inside the SAME chat → cache HIT (saves Gemini cost)
    #   • New chat for the same idea → cache MISS → fresh research +
    #     fresh Director output → different archetype, palette, header.
    import hashlib as _hashlib
    import time as _time_cache
    from app.paths import RESEARCH_CACHE_DIR as _cache_dir
    _cache_key = _hashlib.md5(
        f"{description.strip().lower()}|{stack}|{_layout_archetype}|{chat_session_id or ''}".encode()
    ).hexdigest()[:14]
    _cache_path = f"{_cache_dir}/{_cache_key}.txt"
    _design_cache_path = f"{_cache_dir}/{_cache_key}.design.json"
    _cache_max_age = 600  # 10 minutes — short enough to retest variations
                          # quickly, long enough to make Phase-1-retry-after-
                          # timeout a free skip on the upstream stages.

    research = None
    # _design is the Design Director's per-project spec. Set on cache-miss
    # (after the Director runs) OR on cache-hit (loaded from sidecar JSON).
    # Downstream builders (design_system_js_builder, marketing_header_builder)
    # consume this to produce per-project visual identity. If it's None, they
    # fall through to safe minimal defaults — which is what made cached runs
    # look visually flat before. The sidecar fixes that.
    _design: dict | None = None
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
                    # Sidecar load — keeps cached runs visually distinct.
                    # Sidecar absence is fine for legacy cache entries written
                    # before this change; builders will use defaults.
                    try:
                        if _os_cache.path.exists(_design_cache_path):
                            import json as _json_dd
                            with open(_design_cache_path, "r", encoding="utf-8") as _dcf:
                                _design_loaded = _json_dd.load(_dcf)
                            if isinstance(_design_loaded, dict) and _design_loaded:
                                _design = _design_loaded
                                logger.info(
                                    "Design Director loaded from cache (%s) — name=%s archetype=%s",
                                    _cache_key,
                                    _design.get("design_system_name"),
                                    _design.get("archetype"),
                                )
                    except Exception as _dd_load_exc:
                        logger.warning(
                            "Design sidecar load failed (non-fatal, will use defaults): %s",
                            _dd_load_exc,
                        )
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
                    # Cultural atmosphere — Gemini's research on what country/region
                    # cues this brand should evoke. Drives palette, typography,
                    # imagery, and language phrases. For "none — modern global"
                    # brands, the design system falls back to its generic logic.
                    _cultural_atmosphere = _extract_research_section(research, "===CULTURAL_ATMOSPHERE===", max_chars=2400)
                    # Brand name: Claude can infer it from description inside the call;
                    # passing description as-is avoids brittle regex extraction here.
                    # Outer cap (220s): two Claude attempts × ~90s httpx timeout +
                    # network latency. If Anthropic stalls, we bail and use the
                    # original research rather than holding up the pipeline.
                    _design = await asyncio.wait_for(
                        build_design_system(
                            description=description,
                            domain=_domain,
                            brand_name="",  # inferred from description
                            copy_tone=_copy_tone_from_research,
                            layout_archetype=_layout_archetype,
                            vibe=_vibe_from_research,
                            cultural_atmosphere=_cultural_atmosphere,
                            api_key=api_key,
                            websocket=websocket,
                        ),
                        timeout=220.0,
                    )
                    if _design:
                        research = inject_design_blocks(research, _design)
                        logger.info(
                            "Design Director injected — name=%s archetype=%s",
                            _design.get("design_system_name"),
                            _design.get("archetype"),
                        )
                except asyncio.TimeoutError:
                    logger.warning("Design Director timed out after 220s — falling back to research-only design")
                except Exception as _dd_exc:
                    logger.warning("Design Director failed (non-fatal): %s", _dd_exc)

                # Save to cache for next time (includes VISUAL_DNA + DESIGN_DIRECTOR if present)
                try:
                    with open(_cache_path, "w", encoding="utf-8") as _cf:
                        _cf.write(research)
                    logger.info("Research cached (%s, %d chars)", _cache_key, len(research))
                except Exception:
                    pass
                # Sidecar: persist the Design Director's structured dict so
                # future cache hits can hand the same spec to the builders.
                # Without this, cached runs fall back to minimal defaults
                # and every cached project looks identical.
                if _design:
                    try:
                        import json as _json_dd_save
                        with open(_design_cache_path, "w", encoding="utf-8") as _dcf:
                            _json_dd_save.dump(_design, _dcf)
                        logger.info("Design Director cached (%s)", _cache_key)
                    except Exception as _dd_save_exc:
                        logger.warning(
                            "Design sidecar write failed (non-fatal): %s",
                            _dd_save_exc,
                        )
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
    _research_cultural_atmosphere = _extract_research_section(research, "===CULTURAL_ATMOSPHERE===", max_chars=2400)

    # ── Live Unsplash image fetch ──────────────────────────────────────────────
    # Parse signature_imagery keywords from the cultural atmosphere block and fetch
    # real matching photos via the Unsplash API. The resulting URL block replaces
    # the hardcoded _UNSPLASH_POOLS in Phase 2 so every project gets photos that
    # actually match its content, not random coffee/gym pool IDs.
    # FAIL-SOFT: falls back to the static pool on any error (missing key, rate
    # limit, network failure) so generation always proceeds.
    _live_unsplash_block: str | None = None
    _unsplash_key = os.environ.get("UNSPLASH_ACCESS_KEY", "")
    if _unsplash_key:
        try:
            from app.services.unsplash import fetch_project_images as _unsplash_fetch

            # 1. Try to pull specific imagery from cultural atmosphere block
            _sig_keywords = _parse_signature_imagery(_research_cultural_atmosphere)

            # 2. Fallback: build meaningful queries from domain + description noun phrases
            if not _sig_keywords:
                _DOMAIN_QUERIES: dict[str, list[str]] = {
                    "restaurant": ["restaurant interior", "plated food", "dining table"],
                    "cafe": ["coffee shop", "latte art", "cozy cafe"],
                    "coffee": ["espresso coffee", "coffee shop interior", "barista"],
                    "bakery": ["bakery pastry", "fresh bread", "patisserie"],
                    "fitness": ["gym workout", "fitness weights", "training session"],
                    "yoga": ["yoga studio", "meditation", "pilates class"],
                    "salon": ["hair salon", "beauty treatment", "hairdresser"],
                    "spa": ["spa wellness", "massage therapy", "relaxation"],
                    "hotel": ["hotel lobby", "luxury room", "travel destination"],
                    "real_estate": ["modern house exterior", "interior design", "architecture"],
                    "fashion": ["fashion clothing", "model editorial", "apparel lookbook"],
                    "ecommerce": ["product photography", "shopping lifestyle", "retail"],
                    "saas": ["technology workspace", "software team", "modern office"],
                    "startup": ["startup team", "modern workspace", "technology"],
                    "agency": ["creative agency", "design studio", "team collaboration"],
                    "portfolio": ["creative work", "design portfolio", "photography"],
                    "education": ["students learning", "classroom", "education technology"],
                    "healthcare": ["healthcare professional", "medical clinic", "wellness"],
                    "law": ["law office", "professional meeting", "legal services"],
                    "finance": ["financial professional", "modern office", "business meeting"],
                }
                _d = _domain.lower().replace(" ", "_").replace("-", "_")
                # Try exact match, then prefix match
                _sig_keywords = _DOMAIN_QUERIES.get(_d) or next(
                    (v for k, v in _DOMAIN_QUERIES.items() if k in _d or _d in k), None
                )
                if not _sig_keywords:
                    # Last resort: use first 5 meaningful words from description
                    _desc_words = [w for w in description.split() if len(w) > 4 and w.isalpha()]
                    _sig_keywords = [" ".join(_desc_words[:3])] if _desc_words else ["business professional"]

            if _sig_keywords:
                _unsplash_result = await _unsplash_fetch(
                    keywords=_sig_keywords,
                    hero_count=2,
                    supporting_count=6,
                )
                _all_photos = _unsplash_result["photos"]
                if _all_photos:
                    _live_unsplash_block = _build_live_unsplash_block(_all_photos, _sig_keywords)
                    logger.info(
                        "Unsplash live fetch: %d photos for keywords %s",
                        len(_all_photos), _sig_keywords[:3],
                    )
                    await _ws_send(websocket, "progress", f"🖼️ Fetched {len(_all_photos)} real Unsplash photos")
        except Exception as _unsplash_exc:
            logger.warning("Unsplash fetch failed (non-fatal, using static pool): %s", _unsplash_exc)

    # Re-derive layout archetype from Gemini's confirmed classification in research
    _classification = _extract_layout_archetype(research, _classification)
    _layout_archetype = _classification["layout_archetype"]
    app_type = _classification["app_type"]
    _domain = _classification["domain"]

    # ── Parse Gemini header design spec ───────────────────────────────────────
    # Gemini emits ===HEADER_DESIGN=== with 7 design axes. We parse it here and
    # pass it to build_marketing_header_jsx so the JSX is composed from Gemini's
    # explicit decisions rather than our preset variant pool.
    _header_spec: dict = {}
    try:
        from app.services.marketing_header_builder import parse_header_spec_from_research as _parse_hspec
        _header_spec = _parse_hspec(research or "")
        if _header_spec:
            logger.info("Header spec from Gemini: %s", _header_spec)
    except Exception as _hspec_exc:
        logger.warning("Header spec parse failed (non-fatal): %s", _hspec_exc)

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
        original_description=original_description,
    )

    # Bridge the Director's motion_language onto schema.theme.motion so the
    # deterministic globals.css writer can emit --d-fast/--d-base/--d-slow/
    # --ease-sig variables. Without this the CSS reveal utilities fall back
    # to safe defaults (quint-out / 180/550/1000ms) — still works, but the
    # Director's per-project taste choice doesn't reach the stylesheet.
    if _design and isinstance(_design, dict):
        _ml = _design.get("motion_language") or {}
        if any(_ml.get(k) for k in ("easing_signature", "durations", "signature_transition", "cursor_treatment")):
            project_schema.setdefault("theme", {})
            project_schema["theme"]["motion"] = {
                "easing_signature": _ml.get("easing_signature", "quint-out"),
                "durations": _ml.get("durations") or {"fast": "180ms", "base": "550ms", "slow": "1000ms"},
                "signature_transition": _ml.get("signature_transition", "minimal-precise"),
                "cursor_treatment": _ml.get("cursor_treatment", "default"),
            }
            logger.info(
                "Bridged Director motion_signature onto schema.theme.motion: easing=%s transition=%s cursor=%s",
                project_schema["theme"]["motion"]["easing_signature"],
                project_schema["theme"]["motion"]["signature_transition"],
                project_schema["theme"]["motion"]["cursor_treatment"],
            )

    # ── Step 3b1: Generate Supabase backend schema (admin/CRM only) ──
    # For data-driven projects (admin panels, CRMs, dashboards), turn the
    # entity definitions into a runnable Postgres migration with RLS.
    # Frontend stays on mock data — user runs the SQL in Supabase to get
    # a real backend. Wiring frontend ↔ Supabase is a future step.
    # FAIL-SOFT: any failure logs and proceeds; migration is nice-to-have.
    _backend_archetypes = {
        "admin_dashboard", "crm", "tms", "saas_dashboard", "ecommerce",
    }
    _wrote_backend_migration = False
    _backend_table_count = 0
    if (
        _layout_archetype in _backend_archetypes
        and (project_schema.get("entities") or [])
    ):
        try:
            from app.services.backend_schema import (
                build_supabase_migration,
                write_supabase_migration,
            )
            await _ws_send(websocket, "progress", "💾 Designing Supabase schema…")
            _migration = await asyncio.wait_for(
                build_supabase_migration(
                    description=description,
                    project_schema=project_schema,
                    spec_text=research,
                    api_key=api_key,
                    websocket=websocket,
                ),
                timeout=150.0,
            )
            if _migration and write_supabase_migration(workspace_path, _migration):
                _wrote_backend_migration = True
                _backend_table_count = len(_migration.get("tables_summary") or [])
                logger.info(
                    "backend_schema: wrote migration with %d tables",
                    _backend_table_count,
                )
                await _ws_send(
                    websocket,
                    "progress",
                    f"✅ Supabase migration ready — {_backend_table_count} tables in supabase/migrations/0001_init.sql",
                )
        except asyncio.TimeoutError:
            logger.warning("backend_schema: timed out after 150s — continuing without migration")
        except Exception as _bs_exc:
            logger.warning("backend_schema failed (non-fatal): %s", _bs_exc)

    # ── Step 3b2: Copy Director (landing-family archetypes only) ──
    # Writes a brand-specific copy deck (hero/sections/features/microcopy/
    # footer/seo) so Phase 1-3 prompts have non-generic copy to use. Kills
    # "Learn More / Our Services / Welcome to" filler at its source.
    # Only runs for non-admin archetypes — admin copy comes from entity names
    # and doesn't have the same generic-filler problem.
    # FAIL-SOFT: returns None on failure, phase prompts still generate copy.
    _copy_deck = None
    if _layout_archetype and "admin" not in _layout_archetype.lower() and "crm" not in _layout_archetype.lower() and "tms" not in _layout_archetype.lower():
        try:
            from app.services.copy_director import build_copy_deck, inject_copy_deck
            _schema_sections = project_schema.get("sections", []) or []
            _section_ids = []
            for s in _schema_sections[:10]:
                sid = (s.get("type") or s.get("id") or "").strip()
                if sid and sid not in _section_ids:
                    _section_ids.append(sid)
            _brand_name_for_copy = (
                project_schema.get("brand", {}).get("name")
                or (description[:60].strip() if description else "")
            )
            # Outer cap (220s): mirrors Design Director — two attempts × 90s
            # httpx timeout. Phase prompts still generate copy on fallback.
            _copy_deck = await asyncio.wait_for(
                build_copy_deck(
                    description=description,
                    domain=_domain,
                    brand_name=_brand_name_for_copy,
                    copy_tone=_extract_research_section(research, "===COPY_TONE===", max_chars=400),
                    layout_archetype=_layout_archetype,
                    section_ids=_section_ids,
                    api_key=api_key,
                    vibe=_extract_research_section(research, "===VIBE===", max_chars=400),
                    cultural_atmosphere=_extract_research_section(research, "===CULTURAL_ATMOSPHERE===", max_chars=2400),
                    websocket=websocket,
                ),
                timeout=220.0,
            )
            if _copy_deck:
                research = inject_copy_deck(research, _copy_deck)
                logger.info(
                    "Copy Director injected — hero='%s', %d sections, %d features",
                    (_copy_deck.get("hero") or {}).get("headline", "")[:60],
                    len(_copy_deck.get("sections") or []),
                    len(_copy_deck.get("features") or []),
                )
                await _ws_send(websocket, "progress", "✍️  Copy deck written")
        except asyncio.TimeoutError:
            logger.warning("Copy Director timed out after 220s — phase prompts will generate copy")
        except Exception as _cd_exc:
            logger.warning("Copy Director failed (non-fatal): %s", _cd_exc)

    # Now distill the (possibly enriched) research so Phase 1-3 see COPY_DECK.
    research_distilled = _distill_research(research)
    logger.info(
        "Research distilled: %d → %d chars",
        len(research), len(research_distilled),
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

    # ── Step 3c.1: Deterministic globals.css write ──
    # The skeleton ships with shadcn-blue placeholder tokens. We rely on
    # Claude in Phase 1 to swap them for the project's palette, but Claude
    # frequently leaves the file untouched or only partially overrides — so
    # every project ended up looking like the default blue/Inter theme.
    # Writing globals.css here from the schema's theme + fonts guarantees
    # the project ships with its own palette and Google Fonts every time.
    # FAIL-SOFT: on any error the LLM still has a chance to write it.
    _det_globals_written = False
    _globals_rel_candidates = (
        "src/app/globals.css",   # Next.js
        "src/index.css",         # Vite (React/Vue)
        "src/style.css",         # Vue alt
        "src/styles/globals.css",
    )
    try:
        from app.services.globals_css_builder import build_globals_css
        _globals_css = build_globals_css(project_schema)
        for _rel in _globals_rel_candidates:
            _abs = os.path.join(workspace_path, _rel)
            if os.path.isfile(_abs):
                with open(_abs, "w", encoding="utf-8") as _gf:
                    _gf.write(_globals_css)
                _det_globals_written = True
                logger.info(
                    "Deterministic globals.css written to %s (%d chars, primary=%s)",
                    _rel, len(_globals_css),
                    (project_schema.get("theme") or {}).get("primary", "default"),
                )
                await _ws_send(
                    websocket, "progress",
                    f"🎨 Wrote project palette to {_rel}",
                )
                break
        if not _det_globals_written:
            logger.info(
                "Deterministic globals.css skipped — no candidate file found in %s",
                _globals_rel_candidates,
            )
    except Exception as _gcs_exc:
        logger.warning(
            "Deterministic globals.css write failed (non-fatal): %s", _gcs_exc,
        )

    # ── Step 3c.2: Deterministic src/lib/design-system.js write ──
    # Picks card / button / motion / spacing / container presets based on a
    # hash of (description + archetype). Two consumer projects don't end up
    # sharing the LLM's defaults (shadow card + pill button + framer fade-up).
    # Same project on retry → identical picks; different projects → different
    # visual identity even when both are in the same archetype.
    # FAIL-SOFT: on error the LLM still has a chance to write the file.
    _det_design_system_written = False
    _design_system_picks: dict[str, str] = {}
    _design_system_rel_candidates = (
        "src/lib/design-system.js",
    )
    try:
        from app.services.design_system_js_builder import build_design_system_js
        # Strip ws.py-appended conversation context so the seed is the user's
        # original prompt only (used for logging / picks summary, not for
        # design choices — those come from `_design`).
        _ds_seed_desc = description.split("\n\n---\n\n")[0].strip()[:200]
        _ds_contents, _design_system_picks = build_design_system_js(
            project_schema, _ds_seed_desc, _layout_archetype, design=_design,
        )
        _lib_dir = os.path.join(workspace_path, "src", "lib")
        os.makedirs(_lib_dir, exist_ok=True)
        # Always write .js (Vue projects can import .js fine; we keep the
        # convention single-file rather than branching by stack).
        _ds_abs = os.path.join(_lib_dir, "design-system.js")
        with open(_ds_abs, "w", encoding="utf-8") as _df:
            _df.write(_ds_contents)
        _det_design_system_written = True
        logger.info(
            "Deterministic design-system.js written (%d chars, picks=%s)",
            len(_ds_contents), _design_system_picks,
        )
        await _ws_send(
            websocket, "progress",
            f"🧱 Wrote design-system.js (card={_design_system_picks.get('card')}, "
            f"button={_design_system_picks.get('button')}, motion={_design_system_picks.get('motion')})",
        )
    except Exception as _ds_exc:
        logger.warning(
            "Deterministic design-system.js write failed (non-fatal): %s", _ds_exc,
        )

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
                    # When Gemini emitted ===HEADER_DESIGN===, use its spec for
                    # a fully compositional render. Otherwise fall back to the
                    # preset variant pool (Design Director placement signals).
                    _header_jsx, _header_variant = build_marketing_header_jsx(
                        brand_name=_schema_brand_name,
                        brand_mark=_brand_mark,
                        navigation=_nav_groups,
                        domain=_domain,
                        archetype=_layout_archetype,
                        design=_design,
                        header_spec=_header_spec or None,
                    )
                    with open(_header_abs, "w", encoding="utf-8") as _hf:
                        _hf.write(_header_jsx)
                    _det_header_written = True
                    logger.info(
                        "Deterministic MarketingHeader written: brand=%s domain=%s treatment=%s variant=%s (%d chars)",
                        _schema_brand_name,
                        _domain,
                        _brand_mark.get("treatment"),
                        _header_variant,
                        len(_header_jsx),
                    )
                    await _ws_send(
                        websocket,
                        "progress",
                        f"🎯 Wrote MarketingHeader ({_header_variant} variant, brand: {_schema_brand_name})",
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
    # Plan-build outcomes (consumed by the confirmation gate below)
    _plan_built_ok = False
    _plan_data: dict | None = None

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
        # Defensive: even after schema validation, reject any brand_name that
        # looks like a sentence (extra safety in case a new code path bypasses
        # _validate_schema). Recover from the original short prompt first,
        # then the cleaned expanded description.
        from app.services.project_schema import (
            _looks_like_prose_brand,
            _extract_brand_from_text,
        )
        _brand_raw = (_brand.get("name") or "").strip()
        if _brand_raw and _looks_like_prose_brand(_brand_raw):
            logger.info("Plan-render: discarding prose brand_name %r", _brand_raw[:60])
            _brand_raw = ""
        if not _brand_raw:
            for _src in (original_description, _clean_desc):
                _cand = _extract_brand_from_text(_src)
                if _cand:
                    _brand_raw = _cand
                    break
        _project_name = _brand_raw or "your app"
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

        # Archetype-aware defaults — supplement (don't replace) the keyword
        # scan when it produced fewer than 3 hits, so a single matched word
        # like "contact" doesn't short-circuit the much richer default set.
        # Threshold of 3 is the floor where a plan starts to feel substantive.
        if len(_page_items) < 3:
            _ARCHETYPE_DEFAULTS = {
                "consumer_website": [
                    {"name": "Home",     "desc": "Hero, value proposition, primary CTA"},
                    {"name": "About",    "desc": "Mission, story, leadership"},
                    {"name": "Services", "desc": "Core offerings and capabilities"},
                    {"name": "Resources","desc": "Articles, guides, and downloadables"},
                    {"name": "Contact",  "desc": "Contact form, locations, channels"},
                ],
                "marketplace": [
                    {"name": "Home",     "desc": "Featured listings and search entry"},
                    {"name": "Browse",   "desc": "Filterable listings grid"},
                    {"name": "Listing",  "desc": "Detail view with seller info"},
                    {"name": "Sell",     "desc": "Seller onboarding and create-listing flow"},
                    {"name": "Account",  "desc": "Buyer/seller dashboard"},
                ],
                "portfolio": [
                    {"name": "Home",     "desc": "Hero with featured work"},
                    {"name": "Work",     "desc": "Project gallery"},
                    {"name": "About",    "desc": "Bio and skills"},
                    {"name": "Contact",  "desc": "Contact form and socials"},
                ],
                "blog": [
                    {"name": "Home",        "desc": "Latest posts and featured article"},
                    {"name": "Articles",    "desc": "Browse all posts with filters"},
                    {"name": "Categories",  "desc": "Browse posts by topic"},
                    {"name": "About",       "desc": "About the publication and authors"},
                    {"name": "Contact",     "desc": "Reach the editorial team"},
                ],
                "single_page_landing": [
                    {"name": "Hero",         "desc": "Headline, subtext, primary CTA"},
                    {"name": "Features",     "desc": "Core product capabilities"},
                    {"name": "How It Works", "desc": "Step-by-step walkthrough"},
                    {"name": "Testimonials", "desc": "Customer quotes and social proof"},
                    {"name": "Pricing",      "desc": "Plans and tiers"},
                    {"name": "FAQ",          "desc": "Common questions"},
                    {"name": "CTA",          "desc": "Final conversion section"},
                ],
            }
            _existing_names = {item["name"].lower() for item in _page_items}
            for _default in _ARCHETYPE_DEFAULTS.get(_layout_archetype, []):
                if _default["name"].lower() not in _existing_names:
                    _page_items.append(_default)
                    if len(_page_items) >= 7:
                        break

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
            _page_names = [p.get("title", p.get("path", "")) for p in _pages[:6] if p.get("title") or p.get("path")]
            # Fall back to _page_items (keyword + archetype defaults) when schema is thin
            if not _page_names:
                _page_names = [item["name"] for item in _page_items[:6]]
            _page_count = len(_page_names) or len(_pages)
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
            _page_names = [p.get("title", p.get("path", "")) for p in _pages[:6] if p.get("title") or p.get("path")]
            # Fall back to _page_items (keyword + archetype defaults) when schema is thin
            if not _page_names:
                _page_names = [item["name"] for item in _page_items[:6]]
            _page_count = len(_page_names) or len(_pages)
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
                _hm = re.search(r"(\d+(?:\.\d+)?)\s*(?:deg|°)?", _primary_hsl)
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
            _about += " " + random.choice(_style_closers)

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

        # Surface the Supabase migration in the plan when we wrote one.
        # User-friendly framing — no jargon about RLS or migrations files.
        if _wrote_backend_migration:
            _plan_data["backend"] = (
                f"Supabase database schema with {_backend_table_count or 'several'} "
                "tables, ready to apply in your Supabase dashboard. The frontend "
                "ships with sample data so you can preview immediately; swap to "
                "real data after running the SQL."
            )
        _plan_built_ok = True

    except Exception as _plan_exc:
        logger.warning(
            "Failed to build plan data (non-fatal): %s", _plan_exc, exc_info=True,
        )
        _plan_built_ok = False
        _plan_data = None

    # ──────────────────────────────────────────────────────────────────
    #  STEP B — Emit the plan to the user (best-effort)
    #  Tracked separately so a send failure doesn't bypass the gate.
    # ──────────────────────────────────────────────────────────────────
    _plan_emitted_ok = False
    if _plan_built_ok and _plan_data is not None:
        # Pre-emit validator — log structured warnings for any thin-plan
        # fields so we can monitor how often defaults need to fire. NOT a
        # hard block today; every gap is supposed to be backfilled upstream
        # by archetype defaults / brand sanitisation. If a field shows up
        # repeatedly in logs, that's a signal to promote it to a hard
        # block + retry the schema parse.
        _plan_issues = _validate_plan_data(_plan_data, _layout_archetype)
        if _plan_issues:
            logger.warning(
                "plan_validate: archetype=%s issues=%s",
                _layout_archetype, _plan_issues,
            )
        else:
            logger.info(
                "plan_validate: archetype=%s OK pages=%d entities=%d",
                _layout_archetype,
                len(_plan_data.get("pages") or []),
                len(_plan_data.get("entities") or []),
            )

        try:
            # Persistence to chat_messages happens automatically via
            # WebSocketProxy._persist_chat_event — no explicit add_message
            # needed here. The proxy detects messageType=plan and stores
            # the JSON envelope so the history loader can re-hydrate the
            # plan card on reload.
            await websocket.send_json({
                "type": "chat_message",
                "role": "agent",
                "messageType": "plan",
                "planData": _plan_data,
            })
            _plan_emitted_ok = True
        except Exception as _emit_err:
            logger.warning("Plan emission failed: %s", _emit_err)

    # ──────────────────────────────────────────────────────────────────
    #  STEP C — Confirmation gate (REQUIRED if the plan reached the user)
    #
    #  Behavior:
    #   - User confirmed                      → proceed
    #   - User rejected WITH correction       → return False (orchestrator retries)
    #   - User rejected WITHOUT correction    → return False (abort cleanly)
    #   - 5-minute timeout                    → abort (user did not confirm)
    #   - Any other exception during the wait → return False (abort, do NOT
    #     silently bill the user for code they never approved)
    #   - Plan was never emitted              → skip gate (no UI to confirm with)
    # ──────────────────────────────────────────────────────────────────
    if _plan_emitted_ok:
        _gate_key = _confirmation_key(websocket, chat_session_id)
        try:
            await websocket.send_json({
                "type": "plan_awaiting_confirmation",
                "message": "Review your plan above and click 'Looks Good' to start building.",
            })
        except Exception as _await_send_err:
            logger.warning("plan_awaiting_confirmation send failed: %s", _await_send_err)

        _plan_future = register_plan_confirmation(_gate_key)
        try:
            _confirmation = await asyncio.wait_for(
                _plan_future, timeout=PLAN_CONFIRM_TIMEOUT_SECONDS
            )
            if not _confirmation.get("confirmed", True):
                _correction = _confirmation.get("correction", "")
                if _correction:
                    await _ws_send(websocket, "progress", f"🔄 Re-researching: {_correction[:60]}...")
                    logger.info("Plan rejected — re-researching with correction: %s", _correction[:100])
                    websocket._plan_correction = _correction
                    return False
                # Reject without correction → abort cleanly
                logger.info("Plan rejected without correction — aborting generation")
                await _ws_send(websocket, "warning", "❌ Plan rejected — generation aborted.")
                return False
            logger.info("Plan confirmed by user — proceeding to code generation")
            await _ws_send(websocket, "progress", "✅ Plan confirmed — starting code generation...")
        except asyncio.TimeoutError:
            logger.info(
                "Plan confirmation timed out after %ds — aborting (user did not confirm)",
                PLAN_CONFIRM_TIMEOUT_SECONDS,
            )
            pending_plan_confirmations.pop(_gate_key, None)
            try:
                await _ws_send(
                    websocket, "warning",
                    "⏱️ Plan expired — generation was not started. "
                    "Send your message again to get a new plan.",
                )
            except Exception:
                pass
            return False
        except Exception as _conf_err:
            # Non-Timeout error during the wait (e.g. ws error, future cancelled
            # by some unexpected path). Abort instead of silently proceeding to
            # code generation the user never approved.
            logger.warning(
                "Plan confirmation aborted due to error: %s", _conf_err, exc_info=True,
            )
            pending_plan_confirmations.pop(_gate_key, None)
            try:
                await _ws_send(
                    websocket, "warning",
                    "❌ Plan confirmation failed — generation aborted. "
                    "Please retry your request.",
                )
            except Exception:
                pass
            return False
    else:
        logger.warning(
            "Plan was not delivered to the user — skipping confirmation gate "
            "and proceeding directly to code generation"
        )

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
    if _det_design_system_written:
        # Already written deterministically with project-specific picks.
        # Just remind components to consume the tokens.
        design_system_instruction = (
            "\n7. DESIGN SYSTEM FILE — src/lib/design-system.js HAS ALREADY BEEN WRITTEN deterministically.\n"
            f"   Picks for THIS project: card={_design_system_picks.get('card')}, "
            f"button={_design_system_picks.get('button')}, motion={_design_system_picks.get('motion')}, "
            f"spacing={_design_system_picks.get('spacing')}, container={_design_system_picks.get('container')}.\n"
            "   DO NOT regenerate src/lib/design-system.js — skip it entirely.\n"
            "   ALL components in Phase 2 and 3 MUST import { ds } from '@/lib/design-system' and use:\n"
            "   - ds.card, ds.cardInteractive — every card\n"
            "   - ds.buttonPrimary, ds.buttonSecondary, ds.buttonGhost — every button\n"
            "   - ds.section — every <section> vertical padding\n"
            "   - ds.container — every section's inner container\n"
            "   - ds.heading, ds.body — heading vs body font classes\n"
            "   - ds.motion — framer-motion props for in-view animations (spread it: <motion.div {...ds.motion}>)\n"
            "   This is HOW each project gets a unique visual identity. Inventing your own card / button / motion classes per file BREAKS that.\n"
        )
    elif schema_design_spec:
        design_system_instruction = f"""\n7. DESIGN SYSTEM FILE — Generate src/lib/design-system.js:
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
        _cultural_block_codegen = (
            f"""
====================================
CULTURAL ATMOSPHERE (from research — apply across all sections):
====================================
{_research_cultural_atmosphere}

When country_or_region is set (NOT "none — modern global"):
  • Use signature_imagery search terms in ALL <Image> tags — never default to
    "modern restaurant interior" / "elegant office" / "lifestyle photo"
  • Use language_phrases as eyebrow tags, accent words, microcopy — keep them
    in the source language, do NOT translate
  • Apply section_label_overrides to nav links and section headers
  • banned_generics from research are FORBIDDEN — do not produce them in JSX
  • motif_inventory items appear 2-3× per page as small SVG decorations

When country_or_region is "none — modern global", skip cultural rules.
"""
            if _research_cultural_atmosphere and _research_cultural_atmosphere.strip()
            else ""
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
{_admin_components_block}{_cultural_block_codegen}
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
{"   - ⚠️ globals.css (the project palette + Google Fonts) HAS ALREADY BEEN WRITTEN deterministically with the project's exact colors and fonts. DO NOT regenerate src/app/globals.css / src/index.css / src/styles/globals.css. Skip it entirely — do NOT include it in write_project_files. Trust the existing CSS variables (--primary, --accent, --background, --foreground, etc.) and the .font-heading / .font-body utility classes." if _det_globals_written else ""}

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
    # Phase 1 is the critical path. Two-attempt strategy:
    #   • Attempt 1: 6-min wall-clock cap. Inner httpx read=60s already
    #     catches truly stalled connections; the wall-clock catches any
    #     other local hang (JSON parse loop, async deadlock, etc.).
    #   • Attempt 2 (only on timeout, not on other failures): immediate
    #     retry with a fresh request. Anthropic per-request slowness is
    #     uncorrelated, so a retry usually succeeds in normal duration.
    #     Total worst-case: ~12 min; typical recovery: 2-4 min.
    # Other failures (auth, rate-limit, content-blocked) are NOT retried —
    # they're not transient and a retry would just burn another credit.
    async def _phase1_attempt() -> Optional[dict]:
        return await call_claude_for_json(
            system_prompt=_system_prompt_for_phase(1),
            user_prompt=_phase_rules_prefix(1) + "\n" + phase1_prompt,
            api_key=api_key,
            websocket=websocket,
            max_tokens=PHASE1_MAX_TOKENS,
            model=MODEL,
            extended_output=PHASE1_EXTENDED,
        )

    result1 = None
    _phase1_timed_out_once = False
    try:
        result1 = await asyncio.wait_for(_phase1_attempt(), timeout=360.0)
    except asyncio.TimeoutError:
        _phase1_timed_out_once = True
        logger.warning(
            "Phase 1 hit 6-min wall-clock on attempt 1 — retrying once "
            "(Anthropic slowness is usually uncorrelated)"
        )
        await _ws_send(
            websocket, "progress",
            "⏳ Phase 1 was slow — retrying with a fresh request...",
        )
        try:
            result1 = await asyncio.wait_for(_phase1_attempt(), timeout=360.0)
        except asyncio.TimeoutError:
            logger.error(
                "Phase 1 hit 6-min wall-clock on attempt 2 — aborting pipeline"
            )
            from app.services.llm_retry import emit_pipeline_failure
            await emit_pipeline_failure(
                websocket,
                phase="execute",
                code="timeout",
                message=(
                    "Code generation took too long even after retry. The AI "
                    "provider seems to be having issues right now. Please try "
                    "again in a few minutes."
                ),
                retriable=True,
            )
            await _ws_send(websocket, "error", "❌ Phase 1 timed out twice — please retry")
            return False
    if _phase1_timed_out_once and result1:
        logger.info("Phase 1 succeeded on attempt 2 after attempt 1 timeout")
    if result1:
        written = write_files_from_json(result1, workspace_path)
        await _emit_file_writes(websocket, written)
        total_files += written
        await _ws_send(websocket, "progress", f"✅ Foundation: {len(written)} files")
    else:
        from app.services.llm_retry import emit_pipeline_failure
        await emit_pipeline_failure(
            websocket,
            phase="execute",
            code="unknown",
            message="Code generation failed unexpectedly. Please retry.",
            retriable=True,
        )
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
                user_prompt=_phase_rules_prefix(2, _live_unsplash_block) + "\n" + batch_prompt,
                api_key=api_key,
                websocket=websocket,
                max_tokens=40000,
                model=MODEL,
                extended_output=True,
            )

        # 10-min hard cap across all parallel batches. Each batch has its own
        # 60s httpx read timeout, so a total-elapsed cap of 600s is generous
        # for typical admin apps (3-5 batches × ~2min each). On timeout, we
        # take what batches completed and fall through to Phase 3 to fill gaps.
        def _batch_failed(res) -> bool:
            return (
                isinstance(res, Exception)
                or not isinstance(res, dict)
                or not res.get("files")
            )

        try:
            batch_results = await asyncio.wait_for(
                asyncio.gather(
                    *[_run_admin_batch(i, b) for i, b in enumerate(batches)],
                    return_exceptions=True,
                ),
                timeout=600.0,
            )
        except asyncio.TimeoutError:
            logger.error("Phase 2 (batched) hit 10-min hard timeout — proceeding to Phase 3")
            batch_results = []

        # ── Per-batch retry pass ──────────────────────────────────────────
        # Transient failures (rate-limit bursts, stream stalls, one-off
        # truncation) are common in admin projects with many parallel
        # batches. Re-run just the failed batches once before giving up.
        # Successful batches are preserved; the retry is bounded at 300s so
        # Phase 3 still runs in time.
        batch_results = list(batch_results)
        retry_indices = [i for i, r in enumerate(batch_results) if _batch_failed(r)]
        if retry_indices and len(retry_indices) < len(batches):
            logger.info("Phase 2 retrying %d failed batch(es): %s", len(retry_indices), retry_indices)
            await _ws_send(
                websocket,
                "progress",
                f"🔁 Retrying {len(retry_indices)} failed batch(es)...",
            )
            try:
                retry_results = await asyncio.wait_for(
                    asyncio.gather(
                        *[_run_admin_batch(i, batches[i]) for i in retry_indices],
                        return_exceptions=True,
                    ),
                    timeout=300.0,
                )
                for pos, idx in enumerate(retry_indices):
                    batch_results[idx] = retry_results[pos]
            except asyncio.TimeoutError:
                logger.warning("Phase 2 retry hit 5-min cap — proceeding with partial results")

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
            await _emit_file_writes(websocket, written)
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
        # 8-min hard cap — single Phase 2 call with ~64K max_tokens should
        # never legitimately take longer. Fall through to Phase 3 on timeout.
        try:
            result2 = await asyncio.wait_for(
                call_claude_for_json(
                    system_prompt=_system_prompt_for_phase(2),
                    user_prompt=_phase_rules_prefix(2, _live_unsplash_block) + "\n" + phase2_prompt,
                    api_key=api_key,
                    websocket=websocket,
                    max_tokens=PHASE2_MAX_TOKENS,
                    model=MODEL,
                    extended_output=PHASE2_EXTENDED,
                ),
                timeout=480.0,
            )
        except asyncio.TimeoutError:
            logger.error("Phase 2 (content) hit 8-min hard timeout — proceeding to Phase 3")
            result2 = None
        if result2:
            written = write_files_from_json(result2, workspace_path)
            await _emit_file_writes(websocket, written)
            total_files += written
            await _ws_send(websocket, "progress", f"✅ Content: {len(written)} files")
        else:
            logger.error("Phase 2 (content) returned no files — likely truncated (budget: %d)", PHASE2_MAX_TOKENS)
            await websocket.send_json({
                "type": "chat_message",
                "role": "system",
                "content": "⚠️ Phase 2 generated no files (response was truncated). Phase 3 will attempt to fill the gap.",
            })

    # ── Phase 2.5: Entity coverage audit (admin/CRM/TMS only) ───────────
    # Heavy admin batches occasionally fail silently (one of N parallel
    # batches truncates or stalls), leaving entire entities with no CRUD UI.
    # Phase 3's completeness check only walks `schema.pages`, so it doesn't
    # catch this. Run a focused recovery batch BEFORE Phase 3 so the
    # generated project actually has every entity the user asked for.
    _missing_entities_for_p3 = []
    if _is_admin and _entities_list:
        try:
            _missing = _audit_admin_entity_coverage(workspace_path, _entities_list)
            if _missing:
                _missing_names = [e.get("name", "?") for e in _missing]
                logger.warning(
                    "Entity coverage audit: %d/%d entities missing CRUD UI: %s",
                    len(_missing), len(_entities_list), _missing_names,
                )
                _recovered = await _recover_missing_entities(
                    workspace_path=workspace_path,
                    missing_entities=_missing,
                    project_schema=project_schema,
                    description=description,
                    api_key=api_key,
                    websocket=websocket,
                    research_distilled=research_distilled,
                    manifest_sliced=manifest_sliced,
                    file_tree=_build_file_tree(workspace_path),
                    stack_rules=stack_rules,
                    api_env=_api_env,
                    api_default=_api_default,
                )
                total_files += _recovered

                # Re-audit after recovery — anything STILL missing rides into
                # the Phase 3 prompt as an explicit "must create" list so
                # Claude can take one more shot at it.
                _missing_entities_for_p3 = _audit_admin_entity_coverage(
                    workspace_path, _entities_list,
                )
                if _missing_entities_for_p3:
                    logger.warning(
                        "Phase 2.5 recovery left %d entities still missing — passing to Phase 3",
                        len(_missing_entities_for_p3),
                    )
            else:
                logger.info(
                    "Entity coverage audit: all %d entities have CRUD UI ✓",
                    len(_entities_list),
                )
        except Exception as _audit_err:
            logger.warning("Entity coverage audit failed (non-fatal): %s", _audit_err)

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

    # If the Phase 2.5 entity-recovery batch couldn't bring everything home,
    # surface the still-missing entities as an explicit "MUST CREATE" block in
    # the Phase 3 prompt. Phase 3 then becomes the third (and last) chance to
    # fill in CRUD for entities the user actually asked for.
    _missing_entity_p3_block = ""
    if _missing_entities_for_p3:
        _names = ", ".join(e.get("name", "?") for e in _missing_entities_for_p3)
        _expected_files: list[str] = []
        for ent in _missing_entities_for_p3:
            _ename = (ent.get("name") or "Entity").strip()
            _slug = _ename.lower()
            _expected_files.append(
                f"   - src/features/{_slug}/services/{_slug}.service.* "
                f"+ hooks/use{_ename}.* + pages/{_ename}ListPage.* + pages/{_ename}FormPage.*"
            )
        _missing_entity_p3_block = (
            "\n\n⚠️ CRITICAL — MISSING ENTITY CRUD (final recovery attempt):\n"
            f"   Phase 2 + Phase 2.5 did not generate CRUD UI for: {_names}\n"
            f"   These entities exist in the schema and are linked from the sidebar nav.\n"
            f"   You MUST create the four files for each missing entity:\n"
            + "\n".join(_expected_files)
            + "\n   Use the schema entity definitions below as the source of truth for fields.\n"
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
''' if schema_extra_pages_spec and _is_admin else ''}{_missing_entity_p3_block}
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
        # 4-min hard cap — Phase 3 is polish, timeout is non-fatal, we just skip.
        try:
            result3 = await asyncio.wait_for(
                call_claude_for_json(
                    system_prompt=_system_prompt_for_phase(3),
                    user_prompt=_phase_rules_prefix(3) + "\n" + phase3_prompt,
                    api_key=api_key,
                    websocket=websocket,
                    max_tokens=PHASE3_MAX_TOKENS,
                    model=PHASE3_MODEL,
                    extended_output=PHASE3_EXTENDED,
                ),
                timeout=240.0,
            )
        except asyncio.TimeoutError:
            logger.warning("Phase 3 (polish) hit 4-min hard timeout — skipping to build")
            result3 = None
        if result3:
            written = write_files_from_json(result3, workspace_path)
            await _emit_file_writes(websocket, written)
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

    # ── Post-generation: nudge user to apply the Supabase migration ──
    # The migration file is in the repo and the README explains how to
    # apply it, but most users won't dig there on their own. This message
    # surfaces it explicitly while the workspace is fresh in their mind.
    # Skipped silently when no migration was written (landing pages etc).
    if _wrote_backend_migration:
        try:
            _table_phrase = (
                f"{_backend_table_count} Supabase tables"
                if _backend_table_count
                else "your Supabase database schema"
            )
            await websocket.send_json({
                "type": "chat_message",
                "role": "agent",
                "content": (
                    f"💾 **Your database is ready to set up**\n\n"
                    f"I generated {_table_phrase} in `supabase/migrations/0001_init.sql`. "
                    f"To turn the mock-data preview into a real backend:\n\n"
                    f"1. Open your Supabase project → **SQL Editor** → New query\n"
                    f"2. Paste the contents of `supabase/migrations/0001_init.sql` and click **Run**\n"
                    f"3. Add your Supabase URL + anon key to `.env.local` and install "
                    f"`@supabase/supabase-js`\n"
                    f"4. Replace the mock fetches in your code with Supabase queries\n\n"
                    f"Full step-by-step in `supabase/README.md`. Row-level security is "
                    f"already configured — every user only sees their own data."
                ),
            })
        except Exception as _nudge_err:
            logger.warning("backend_schema nudge send failed (non-fatal): %s", _nudge_err)

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
