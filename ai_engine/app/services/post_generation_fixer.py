"""Post-generation fixers — automated code quality fixes applied after AI generation.

Runs between generation and build validation to catch the most common build errors:
  1. Missing 'use client' directives (Next.js App Router)
  2. Banned lucide-react icon imports (social/brand icons)
  3. Unresolved imports (missing files → stub creation)

Usage:
    from app.services.post_generation_fixer import run_all_fixers
    fixed = await run_all_fixers(workspace_path, websocket)
"""

from __future__ import annotations

import os
import re
import subprocess
import logging
from typing import Optional

logger = logging.getLogger("lucid.post_generation_fixer")


# ╔══════════════════════════════════════════════════════════════╗
# ║  FIXER 1 — 'use client' Auto-Injector                      ║
# ╚══════════════════════════════════════════════════════════════╝

# Patterns that REQUIRE 'use client' in Next.js App Router
_CLIENT_PATTERNS = [
    # React hooks
    r"\buseState\b",
    r"\buseEffect\b",
    r"\buseRef\b",
    r"\buseCallback\b",
    r"\buseMemo\b",
    r"\buseContext\b",
    r"\buseReducer\b",
    r"\buseLayoutEffect\b",
    r"\buseImperativeHandle\b",
    # Next.js client hooks
    r"\buseRouter\b",
    r"\busePathname\b",
    r"\buseSearchParams\b",
    r"\buseParams\b",
    # Event handlers (in JSX)
    r"\bonClick\b",
    r"\bonChange\b",
    r"\bonSubmit\b",
    r"\bonKeyDown\b",
    r"\bonKeyUp\b",
    r"\bonBlur\b",
    r"\bonFocus\b",
    r"\bonMouseEnter\b",
    r"\bonMouseLeave\b",
    r"\bonScroll\b",
    r"\bonInput\b",
    r"\bonDrag\b",
    r"\bonDrop\b",
    # Third-party client libraries
    r"\bmotion\b",  # framer-motion
    r"\buseForm\b",  # react-hook-form
    r"\buseQuery\b",  # react-query / tanstack
    r"\buseMutation\b",
    r"\buseInView\b",  # framer-motion / intersection observer
    r"\buseAnimation\b",  # framer-motion
    # Browser APIs
    r"\bwindow\.",
    r"\bdocument\.",
    r"\blocalStorage\b",
    r"\bsessionStorage\b",
    r"\bnavigator\b",
]

# Compile patterns once for performance
_CLIENT_RE = re.compile("|".join(_CLIENT_PATTERNS))

# Directories to skip
_SKIP_DIRS = {"node_modules", ".git", ".next", "dist", "build", ".vite", "__pycache__"}

# Only process these extensions
_JSX_EXTENSIONS = {".jsx", ".tsx", ".js", ".ts"}

# Config/data-only files that must NEVER have 'use client' — they export
# plain static data and are imported by server components (e.g. MarketingFooter).
# Adding 'use client' to these turns them into client module exports and breaks
# any server component that calls .map() or iterates over their exports.
_CONFIG_FILENAMES = {
    "navigation.js", "navigation.ts",
    "site.js", "site.ts",
    "icons.js", "icons.ts",
    "constants.js", "constants.ts",
    "config.js", "config.ts",
    "theme.js", "theme.ts",
    "tokens.js", "tokens.ts",
    "routes.js", "routes.ts",
    "metadata.js", "metadata.ts",
    # design-system.js exports a plain { ds } object (no React, no hooks).
    # It's imported by SERVER pages (about, contact, marketing root), which
    # need to dot into ds.maxWidth / ds.section etc. Marking it 'use client'
    # turns it into a client-module export; Next.js then refuses any server
    # component that tries to read ds.something with:
    #   "Cannot access maxWidth.toString on the server."
    # The auto-injector previously added 'use client' because the comments
    # in design-system.js mention "motion" as a token name, matching the
    # framer-motion regex. Allowlist it here.
    "design-system.js", "design-system.ts",
}

# Subdirectory paths that only contain config/data (relative to src/)
_CONFIG_DIRS = {"config", "constants", "tokens", "theme"}


def _is_config_file(filepath: str) -> bool:
    """Return True if this file is a static config/data file.

    Config files must never receive 'use client' — they export plain
    arrays/objects consumed by both server and client components.
    """
    fname = os.path.basename(filepath)
    if fname in _CONFIG_FILENAMES:
        return True
    # Check if any parent directory is a known config-only dir
    parts = filepath.replace("\\", "/").split("/")
    for part in parts[:-1]:  # exclude the filename itself
        if part in _CONFIG_DIRS:
            return True
    return False


def strip_use_client_from_configs(workspace_path: str) -> list[str]:
    """Remove mistakenly-added 'use client' from static config/data files.

    Claude sometimes generates config files (navigation.js, site.js, etc.)
    with 'use client' at the top. This breaks Next.js server components that
    import and iterate over those exports.

    Returns list of file paths that were fixed.
    """
    fixed_files = []
    src_dir = os.path.join(workspace_path, "src")
    if not os.path.isdir(src_dir):
        src_dir = workspace_path

    for root, dirs, files in os.walk(src_dir):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]

        for fname in files:
            ext = os.path.splitext(fname)[1].lower()
            if ext not in _JSX_EXTENSIONS:
                continue

            filepath = os.path.join(root, fname)
            if not _is_config_file(filepath):
                continue

            try:
                with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
            except Exception:
                continue

            # Check if 'use client' is present in the first 5 lines
            lines = content.split("\n")
            first_lines = "\n".join(lines[:5])
            has_use_client = (
                "'use client'" in first_lines or '"use client"' in first_lines
            )
            if not has_use_client:
                continue

            # Strip 'use client' line(s) from the top
            new_lines = []
            skipping = True
            for line in lines:
                stripped = line.strip()
                if skipping and (stripped in ("'use client';", '"use client";', "'use client'", '"use client"') or stripped == ""):
                    if stripped == "":
                        continue  # also drop blank lines immediately after directive
                    continue
                skipping = False
                new_lines.append(line)

            new_content = "\n".join(new_lines)
            try:
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(new_content)
                rel_path = os.path.relpath(filepath, workspace_path)
                fixed_files.append(rel_path)
                logger.info("Stripped 'use client' from config file: %s", rel_path)
            except Exception as e:
                logger.warning("Failed to strip 'use client' from %s: %s", filepath, e)

    if fixed_files:
        logger.info("Config 'use client' stripper fixed %d files", len(fixed_files))

    return fixed_files


def fix_use_client(workspace_path: str) -> list[str]:
    """Scan all JSX/TSX files and inject 'use client' where missing.

    Returns list of file paths that were fixed.
    """
    fixed_files = []
    src_dir = os.path.join(workspace_path, "src")

    if not os.path.isdir(src_dir):
        # No src/ directory — scan the whole workspace
        src_dir = workspace_path

    for root, dirs, files in os.walk(src_dir):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]

        for fname in files:
            ext = os.path.splitext(fname)[1].lower()
            if ext not in _JSX_EXTENSIONS:
                continue

            filepath = os.path.join(root, fname)

            # Never inject 'use client' into static config/data files
            if _is_config_file(filepath):
                continue

            try:
                with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
            except Exception:
                continue

            # Skip if already has 'use client'
            # Check first 5 lines (may have comments/whitespace before it)
            first_lines = "\n".join(content.split("\n")[:5])
            if "'use client'" in first_lines or '"use client"' in first_lines:
                continue

            # Skip pure type/config files (no JSX)
            if ext in {".ts", ".js"} and "<" not in content and "jsx" not in content.lower():
                # Check if it has any client patterns (hooks in non-JSX files like stores)
                if not _CLIENT_RE.search(content):
                    continue

            # Check if any client pattern is present
            if _CLIENT_RE.search(content):
                # Inject 'use client' at the very top
                new_content = "'use client';\n\n" + content
                try:
                    with open(filepath, "w", encoding="utf-8") as f:
                        f.write(new_content)
                    rel_path = os.path.relpath(filepath, workspace_path)
                    fixed_files.append(rel_path)
                    logger.info("Injected 'use client' into %s", rel_path)
                except Exception as e:
                    logger.warning("Failed to fix %s: %s", filepath, e)

    if fixed_files:
        logger.info("'use client' auto-injector fixed %d files", len(fixed_files))

    return fixed_files


# ╔══════════════════════════════════════════════════════════════╗
# ║  FIXER 2 — Banned Icon Auto-Fixer                          ║
# ╚══════════════════════════════════════════════════════════════╝

# Icons that DO NOT exist in lucide-react but Claude frequently imports
_BANNED_ICONS = {
    "Facebook": '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M24 12.073c0-6.627-5.373-12-12-12s-12 5.373-12 12c0 5.99 4.388 10.954 10.125 11.854v-8.385H7.078v-3.47h3.047V9.43c0-3.007 1.792-4.669 4.533-4.669 1.312 0 2.686.235 2.686.235v2.953H15.83c-1.491 0-1.956.925-1.956 1.874v2.25h3.328l-.532 3.47h-2.796v8.385C19.612 23.027 24 18.062 24 12.073z"/></svg>',
    "Instagram": '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2.163c3.204 0 3.584.012 4.85.07 3.252.148 4.771 1.691 4.919 4.919.058 1.265.069 1.645.069 4.849 0 3.205-.012 3.584-.069 4.849-.149 3.225-1.664 4.771-4.919 4.919-1.266.058-1.644.07-4.85.07-3.204 0-3.584-.012-4.849-.07-3.26-.149-4.771-1.699-4.919-4.92-.058-1.265-.07-1.644-.07-4.849 0-3.204.013-3.583.07-4.849.149-3.227 1.664-4.771 4.919-4.919 1.266-.057 1.645-.069 4.849-.069zM12 0C8.741 0 8.333.014 7.053.072 2.695.272.273 2.69.073 7.052.014 8.333 0 8.741 0 12c0 3.259.014 3.668.072 4.948.2 4.358 2.618 6.78 6.98 6.98C8.333 23.986 8.741 24 12 24c3.259 0 3.668-.014 4.948-.072 4.354-.2 6.782-2.618 6.979-6.98.059-1.28.073-1.689.073-4.948 0-3.259-.014-3.667-.072-4.947-.196-4.354-2.617-6.78-6.979-6.98C15.668.014 15.259 0 12 0zm0 5.838a6.162 6.162 0 100 12.324 6.162 6.162 0 000-12.324zM12 16a4 4 0 110-8 4 4 0 010 8zm6.406-11.845a1.44 1.44 0 100 2.881 1.44 1.44 0 000-2.881z"/></svg>',
    "Twitter": '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z"/></svg>',
    "Linkedin": '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M20.447 20.452h-3.554v-5.569c0-1.328-.027-3.037-1.852-3.037-1.853 0-2.136 1.445-2.136 2.939v5.667H9.351V9h3.414v1.561h.046c.477-.9 1.637-1.85 3.37-1.85 3.601 0 4.267 2.37 4.267 5.455v6.286zM5.337 7.433a2.062 2.062 0 01-2.063-2.065 2.064 2.064 0 112.063 2.065zm1.782 13.019H3.555V9h3.564v11.452zM22.225 0H1.771C.792 0 0 .774 0 1.729v20.542C0 23.227.792 24 1.771 24h20.451C23.2 24 24 23.227 24 22.271V1.729C24 .774 23.2 0 22.222 0h.003z"/></svg>',
    "Youtube": '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M23.498 6.186a3.016 3.016 0 00-2.122-2.136C19.505 3.545 12 3.545 12 3.545s-7.505 0-9.377.505A3.017 3.017 0 00.502 6.186C0 8.07 0 12 0 12s0 3.93.502 5.814a3.016 3.016 0 002.122 2.136c1.871.505 9.376.505 9.376.505s7.505 0 9.377-.505a3.015 3.015 0 002.122-2.136C24 15.93 24 12 24 12s0-3.93-.502-5.814zM9.545 15.568V8.432L15.818 12l-6.273 3.568z"/></svg>',
    "Tiktok": '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12.525.02c1.31-.02 2.61-.01 3.91-.02.08 1.53.63 3.09 1.75 4.17 1.12 1.11 2.7 1.62 4.24 1.79v4.03c-1.44-.05-2.89-.35-4.2-.97-.57-.26-1.1-.59-1.62-.93-.01 2.92.01 5.84-.02 8.75-.08 1.4-.54 2.79-1.35 3.94-1.31 1.92-3.58 3.17-5.91 3.21-1.43.08-2.86-.31-4.08-1.03-2.02-1.19-3.44-3.37-3.65-5.71-.02-.5-.03-1-.01-1.49.18-1.9 1.12-3.72 2.58-4.96 1.66-1.44 3.98-2.13 6.15-1.72.02 1.48-.04 2.96-.04 4.44-.99-.32-2.15-.23-3.02.37-.63.41-1.11 1.04-1.36 1.75-.21.51-.15 1.07-.14 1.61.24 1.64 1.82 3.02 3.5 2.87 1.12-.01 2.19-.66 2.77-1.61.19-.33.4-.67.41-1.06.1-1.79.06-3.57.07-5.36.01-4.03-.01-8.05.02-12.07z"/></svg>',
    "Pinterest": '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12.017 0C5.396 0 .029 5.367.029 11.987c0 5.079 3.158 9.417 7.618 11.162-.105-.949-.199-2.403.041-3.439.219-.937 1.406-5.957 1.406-5.957s-.359-.72-.359-1.781c0-1.668.967-2.914 2.171-2.914 1.023 0 1.518.769 1.518 1.69 0 1.029-.655 2.568-.994 3.995-.283 1.194.599 2.169 1.777 2.169 2.133 0 3.772-2.249 3.772-5.495 0-2.873-2.064-4.882-5.012-4.882-3.414 0-5.418 2.561-5.418 5.207 0 1.031.397 2.138.893 2.738a.36.36 0 01.083.345l-.333 1.36c-.053.22-.174.267-.402.161-1.499-.698-2.436-2.889-2.436-4.649 0-3.785 2.75-7.262 7.929-7.262 4.163 0 7.398 2.967 7.398 6.931 0 4.136-2.607 7.464-6.227 7.464-1.216 0-2.359-.631-2.75-1.378l-.748 2.853c-.271 1.043-1.002 2.35-1.492 3.146C9.57 23.812 10.763 24 12.017 24c6.624 0 11.99-5.367 11.99-11.988C24.007 5.367 18.641 0 12.017 0z"/></svg>',
    "Github": '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 .297c-6.63 0-12 5.373-12 12 0 5.303 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61C4.422 18.07 3.633 17.7 3.633 17.7c-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 2.04.138 3 .405 2.28-1.552 3.285-1.23 3.285-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 22.092 24 17.592 24 12.297c0-6.627-5.373-12-12-12"/></svg>',
}

# Regex to match lucide-react import statements
_LUCIDE_IMPORT_RE = re.compile(
    r"import\s*\{([^}]+)\}\s*from\s*['\"]lucide-react['\"]",
)


_SCROLL_INTO_VIEW_RE = re.compile(
    r"(\w+)\.scrollIntoView\s*\(\s*\{[^}]*\}\s*\)\s*;?"
)

# Top-level ALL_CAPS const that initializes a multi-item array — Claude's
# go-to pattern for fabricating "search results", "filter options", etc.
# when landing.json doesn't provide items. We can't auto-fix safely (the
# component references the constant), so we only log it as telemetry.
_HARDCODED_DATA_ARRAY_RE = re.compile(
    r"^const\s+([A-Z_]{3,})\s*=\s*\[",
    re.MULTILINE,
)
# These names are infrastructure, not fabricated data — skip them in counts.
_HARDCODED_DATA_IGNORE = frozenset({
    "UNSPLASH_IMAGES",  # already handled by fix_hardcoded_unsplash_dicts
})


def audit_hardcoded_data_arrays(workspace_path: str) -> list[str]:
    """Telemetry-only: log section files that hardcode data arrays.

    The codegen prompt forbids this pattern — sections should pull arrays
    from `section.items` in landing.json. When Claude fabricates anyway,
    the user can't edit the data and every site reads templated. Log so
    we can measure how often the rule fails; do NOT auto-fix (removing
    the const breaks the component that references it).
    """
    flagged: list[str] = []
    sections_dir = os.path.join(workspace_path, "src", "components", "sections")
    if not os.path.isdir(sections_dir):
        return flagged

    for root, _, files in os.walk(sections_dir):
        for name in files:
            if not name.endswith((".jsx", ".tsx", ".js", ".ts")):
                continue
            path = os.path.join(root, name)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
            except OSError:
                continue
            matches = [
                m for m in _HARDCODED_DATA_ARRAY_RE.findall(content)
                if m not in _HARDCODED_DATA_IGNORE
            ]
            if matches:
                rel = os.path.relpath(path, workspace_path)
                logger.warning(
                    "hardcoded_data: %s declares %s — should read from landing.json section.items",
                    rel, ", ".join(matches),
                )
                flagged.append(path)
    return flagged


# Negative-offset Tailwind classes that push an element OUTSIDE its parent.
# Matches: -top-4, -right-8, -bottom-2, -left-12, -top-[20px], -inset-x-4, etc.
_NEGATIVE_OFFSET_RE = re.compile(
    r"-(?:top|right|bottom|left|inset|inset-x|inset-y)-(?:\[[^\]]+\]|\d+(?:\.\d+)?)"
)

# Marks a JSX element as a "badge / chip / callout" that contains user-readable
# copy. When we find one of these AND it sits on `absolute` AND uses a negative
# offset, the badge clips the moment the section is `overflow-hidden` (which
# every hero section is). Strip the negative offset so the badge stays in-frame.
_BADGE_HINT_RE = re.compile(
    r"\b(?:badge|chip|pill|guarantee|callout|tag-label|highlight-card|sticker)\b",
    re.IGNORECASE,
)


def fix_badge_clipping_in_hero(workspace_path: str) -> list[str]:
    """Strip negative offsets on absolute-positioned badges inside hero sections.

    The pattern Claude keeps producing:
        <div className="absolute -top-4 right-8 ... ">Score Guarantee</div>
    inside `<section className="overflow-hidden ...">`. The -top-4 pushes the
    badge above the section's top edge, and the parent's `overflow-hidden`
    chops it off. Replace the negative offset with a safe positive one
    (`-top-4` → `top-4`) only inside files that look like hero sections.
    """
    fixed: list[str] = []
    sections_dir = os.path.join(workspace_path, "src", "components", "sections")
    if not os.path.isdir(sections_dir):
        return fixed

    for root, _, files in os.walk(sections_dir):
        for name in files:
            if not name.endswith((".jsx", ".tsx", ".js", ".ts")):
                continue
            # Hero-style sections only — story sections and gallery cards
            # sometimes use negative offsets intentionally for decorative
            # bleed, and we don't want to flatten those.
            lower = name.lower()
            if "hero" not in lower and "banner" not in lower:
                continue
            path = os.path.join(root, name)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
            except OSError:
                continue
            if "absolute" not in content or "-" not in content:
                continue

            new_content = content
            # Walk each line; only rewrite when the line carries both an
            # absolute-positioned class AND looks like a badge/chip.
            changed = False
            out_lines: list[str] = []
            for line in new_content.splitlines(keepends=True):
                if (
                    "absolute" in line
                    and _BADGE_HINT_RE.search(line)
                    and _NEGATIVE_OFFSET_RE.search(line)
                ):
                    fixed_line = _NEGATIVE_OFFSET_RE.sub(
                        lambda m: m.group(0).lstrip("-"), line,
                    )
                    out_lines.append(fixed_line)
                    if fixed_line != line:
                        changed = True
                else:
                    out_lines.append(line)
            if not changed:
                continue
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.writelines(out_lines)
                fixed.append(path)
                logger.info(
                    "fix_badge_clipping_in_hero: clamped negative offsets in %s",
                    os.path.relpath(path, workspace_path),
                )
            except OSError as exc:
                logger.warning(
                    "fix_badge_clipping_in_hero: failed to write %s: %s",
                    path, exc,
                )
    return fixed


# Section root opener: `<section className="..."` (the first one in the file).
_SECTION_ROOT_RE = re.compile(
    r'<section\b([^>]*?)className=(["\'])([^"\']*)\2',
    re.DOTALL,
)

# Heuristic for "decorative absolute element that overflows the section":
# Claude renders these as massive serif numerals or background SVG blobs that
# use `absolute` + an oversized font / size. Examples we have seen:
#   • <span className="absolute -left-4 top-0 text-9xl font-serif text-muted/10">04</span>
#   • <div className="absolute -top-20 -right-32 h-96 w-96 rounded-full bg-primary/20 blur-3xl" />
# When the parent <section> does NOT carry `overflow-hidden`, these bleed into
# the next section and read as a layout glitch.
_DECORATIVE_ABSOLUTE_RE = re.compile(
    r"absolute[^\"']*(?:"
    r"text-(?:7|8|9)xl|"           # huge type used as background numeral
    r"text-\[\d{3,}px\]|"          # arbitrary huge size
    r"blur-(?:2xl|3xl)|"           # blob blur
    r"-(?:top|right|bottom|left|inset)-(?:\d{2,}|\[)"  # negative offset ≥10
    r")"
)


_SCROLL_MT_RE = re.compile(r"\bscroll-mt-\d")
_SECTION_HAS_ID_RE = re.compile(r'<section\b[^>]*\bid=(["\'])([^"\']+)\1', re.DOTALL)


def fix_section_scroll_margin(workspace_path: str) -> list[str]:
    """Add `scroll-mt-24 md:scroll-mt-28` to every section root that has an id.

    Why: the layout's sticky `<header>` (h-16 → h-20) sits over the top of the
    page on scroll. When the page jumps to `#section-id` (anchor link, deep
    link, programmatic scroll), the section's top edge parks at the viewport
    top — which puts the heading directly UNDER the nav. Hidden.

    `scroll-mt-*` shifts the scroll target down by the nav height. This was
    the #1 systemic visual bug on shipped pages: nav overlapping "07 — GALLERY",
    "09 — FAQ", story image, seasonal frames, etc.

    Only patches `<section id="...">` (anchor targets); skips sections without
    an id and skips sections that already carry any `scroll-mt-*` class.
    """
    fixed: list[str] = []
    sections_dir = os.path.join(workspace_path, "src", "components", "sections")
    if not os.path.isdir(sections_dir):
        return fixed

    for root, _, files in os.walk(sections_dir):
        for name in files:
            if not name.endswith((".jsx", ".tsx", ".js", ".ts")):
                continue
            path = os.path.join(root, name)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
            except OSError:
                continue

            # Only patch sections that are anchor targets (have id=).
            if not _SECTION_HAS_ID_RE.search(content):
                continue

            match = _SECTION_ROOT_RE.search(content)
            if not match:
                continue
            classes = match.group(3)
            if _SCROLL_MT_RE.search(classes):
                continue  # already has scroll-mt — respect

            new_classes = f"scroll-mt-24 md:scroll-mt-28 {classes.lstrip()}".strip()
            new_content = (
                content[: match.start()]
                + f'<section{match.group(1)}className={match.group(2)}{new_classes}{match.group(2)}'
                + content[match.end():]
            )
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(new_content)
                fixed.append(path)
                logger.info(
                    "fix_section_scroll_margin: added scroll-mt to %s",
                    os.path.relpath(path, workspace_path),
                )
            except OSError as exc:
                logger.warning(
                    "fix_section_scroll_margin: failed to write %s: %s",
                    path, exc,
                )
    return fixed


def fix_section_overflow_clip(workspace_path: str) -> list[str]:
    """Ensure sections containing decorative absolute elements clip overflow.

    The FAQ "04" bleed pattern: a giant decorative number is rendered with
    `position: absolute` inside the section, but the section's root <section>
    tag is missing `overflow-hidden`. The numeral bleeds into the adjacent
    section. Add `overflow-hidden` to the section root only when:
      • the file contains an absolute decorative element (giant text, blur,
        or large negative offset), AND
      • the section root's className does NOT already contain `overflow-`.
    """
    fixed: list[str] = []
    sections_dir = os.path.join(workspace_path, "src", "components", "sections")
    if not os.path.isdir(sections_dir):
        return fixed

    for root, _, files in os.walk(sections_dir):
        for name in files:
            if not name.endswith((".jsx", ".tsx", ".js", ".ts")):
                continue
            path = os.path.join(root, name)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
            except OSError:
                continue
            if "absolute" not in content:
                continue
            if not _DECORATIVE_ABSOLUTE_RE.search(content):
                continue

            match = _SECTION_ROOT_RE.search(content)
            if not match:
                continue
            classes = match.group(3)
            if "overflow-" in classes:
                continue  # already clipping (visible|hidden|clip|auto) — respect

            # Sections with interactive dropdown panels get the x-only clip:
            # `overflow-hidden` would decapitate a panel opening past the
            # section's bottom edge (see fix_popover_overflow_clip).
            clip = "overflow-x-clip" if _DROPDOWN_PANEL_RE.search(content) else "overflow-hidden"
            new_classes = f"{classes.rstrip()} {clip}".strip()
            new_content = (
                content[: match.start()]
                + f'<section{match.group(1)}className={match.group(2)}{new_classes}{match.group(2)}'
                + content[match.end():]
            )
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(new_content)
                fixed.append(path)
                logger.info(
                    "fix_section_overflow_clip: added %s to %s",
                    clip, os.path.relpath(path, workspace_path),
                )
            except OSError as exc:
                logger.warning(
                    "fix_section_overflow_clip: failed to write %s: %s",
                    path, exc,
                )
    return fixed


# Interactive dropdown/popover panel signature: an absolutely-positioned panel
# anchored to its trigger with `top-full` / `bottom-full` (custom selects,
# guest steppers, calendar popovers). z-index never beats ANCESTOR overflow
# clipping, so these are the elements section-root clipping must not catch.
_DROPDOWN_PANEL_RE = re.compile(r"\b(?:top|bottom)-full\b")


def fix_popover_overflow_clip(workspace_path: str) -> list[str]:
    """Downgrade section-root `overflow-hidden` → `overflow-x-clip` when the
    section contains an interactive dropdown panel.

    Roots carry `overflow-hidden` to contain decorative bleed, but it also
    clips dropdown panels that open past the section's bottom edge — a
    booking bar in the lower half of a min-h-screen hero gets its guests/date
    panel cut in half at the section boundary (Saint Cecilia hero,
    2026-06-12). `overflow-x-clip` still prevents decor from causing
    horizontal page scroll; vertical decor bleed is painted over by the next
    section's opaque background, while panels escape vertically intact.
    """
    fixed: list[str] = []
    sections_dir = os.path.join(workspace_path, "src", "components", "sections")
    if not os.path.isdir(sections_dir):
        return fixed

    for root, _, files in os.walk(sections_dir):
        for name in files:
            if not name.endswith((".jsx", ".tsx", ".js", ".ts")):
                continue
            path = os.path.join(root, name)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
            except OSError:
                continue
            if not _DROPDOWN_PANEL_RE.search(content):
                continue

            match = _SECTION_ROOT_RE.search(content)
            if not match:
                continue
            classes = match.group(3)
            if "overflow-hidden" not in classes:
                continue

            new_classes = classes.replace("overflow-hidden", "overflow-x-clip")
            new_content = (
                content[: match.start()]
                + f'<section{match.group(1)}className={match.group(2)}{new_classes}{match.group(2)}'
                + content[match.end():]
            )
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(new_content)
                fixed.append(path)
                logger.info(
                    "fix_popover_overflow_clip: overflow-hidden → overflow-x-clip in %s",
                    os.path.relpath(path, workspace_path),
                )
            except OSError as exc:
                logger.warning(
                    "fix_popover_overflow_clip: failed to write %s: %s",
                    path, exc,
                )
    return fixed


# <h1>/<h2> block (no nesting of same tag inside) + any <br> variant.
_HEADING_BLOCK_RE = re.compile(r"<h([12])\b[^>]*>.*?</h\1>", re.DOTALL)
_BR_TAG_RE = re.compile(r"\s*<br\b[^>]*/?>\s*")


def fix_br_in_headings(workspace_path: str) -> list[str]:
    """Strip `<br>` from inside <h1>/<h2> headings (JSX-safe `{" "}` instead).

    The codegen rules ban manual line breaks in headlines: at text-6xl+ a
    forced `<br>` PLUS natural wrapping scatters the headline across 3-4
    sparse lines with orphan words and dangling accent glyphs (the
    "A storied retreat / — / in the heart of" hero, 2026-06-12). CSS
    line-wrap alone produces the intended 2-3 line editorial block.
    """
    fixed: list[str] = []
    sections_dir = os.path.join(workspace_path, "src", "components", "sections")
    if not os.path.isdir(sections_dir):
        return fixed

    for root, _, files in os.walk(sections_dir):
        for name in files:
            if not name.endswith((".jsx", ".tsx", ".js", ".ts")):
                continue
            path = os.path.join(root, name)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
            except OSError:
                continue
            if "<br" not in content:
                continue

            def _strip_brs(m: "re.Match[str]") -> str:
                return _BR_TAG_RE.sub('{" "}', m.group(0))

            new_content = _HEADING_BLOCK_RE.sub(_strip_brs, content)
            if new_content == content:
                continue
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(new_content)
                fixed.append(path)
                logger.info(
                    "fix_br_in_headings: stripped <br> from headings in %s",
                    os.path.relpath(path, workspace_path),
                )
            except OSError as exc:
                logger.warning(
                    "fix_br_in_headings: failed to write %s: %s",
                    path, exc,
                )
    return fixed


# Detect duplicate-text watermark pattern: same JSX interpolation appears
# inside a huge-text absolute element AND a normal label element within the
# same card. Renders as a "ghost" name behind the readable name (saw this on
# the Lumina IELTS expert card where "Elena Rodriguez" was rendered twice).
_JSX_EXPR_RE = re.compile(r"\{(\w+\.\w+(?:\.\w+)?)\}")
_WATERMARK_LINE_RE = re.compile(
    r"absolute[^\"']*text-(?:5|6|7|8|9)xl"
)


def audit_duplicate_text_watermark(workspace_path: str) -> list[str]:
    """Log sections where the same JSX expression is rendered as both a huge
    absolute watermark AND a normal label — creates a confusing ghost-text
    overlay (the Elena Rodriguez double-name bug). Audit-only; auto-removal
    would risk leaving unbalanced JSX behind.
    """
    flagged: list[str] = []
    sections_dir = os.path.join(workspace_path, "src", "components", "sections")
    if not os.path.isdir(sections_dir):
        return flagged

    for root, _, files in os.walk(sections_dir):
        for name in files:
            if not name.endswith((".jsx", ".tsx", ".js", ".ts")):
                continue
            path = os.path.join(root, name)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    lines = f.readlines()
            except OSError:
                continue

            # Collect (line_no, set_of_exprs, is_watermark) per JSX-bearing line
            watermark_exprs: dict[str, int] = {}   # expr → line_no of watermark
            normal_exprs: dict[str, list[int]] = {}  # expr → [line_no, ...]

            for idx, line in enumerate(lines):
                exprs = _JSX_EXPR_RE.findall(line)
                if not exprs:
                    continue
                # Heuristic: a watermark element typically has both `absolute`
                # and a large text class on the SAME or PREVIOUS line (the
                # className often wraps).
                window = "".join(lines[max(0, idx - 2):idx + 1])
                is_watermark = bool(_WATERMARK_LINE_RE.search(window))
                for e in exprs:
                    if is_watermark:
                        watermark_exprs.setdefault(e, idx + 1)
                    else:
                        normal_exprs.setdefault(e, []).append(idx + 1)

            dupes = [
                (e, watermark_exprs[e], normal_exprs[e])
                for e in watermark_exprs
                if e in normal_exprs
                # close enough to be the same card (within ~25 lines)
                and any(abs(n - watermark_exprs[e]) <= 25 for n in normal_exprs[e])
            ]
            if dupes:
                rel = os.path.relpath(path, workspace_path)
                summary = ", ".join(
                    f"{e} at L{w_line}=watermark + L{n_lines[0]}=label"
                    for e, w_line, n_lines in dupes
                )
                logger.warning(
                    "duplicate_text_watermark: %s renders same content twice — %s",
                    rel, summary,
                )
                flagged.append(path)
    return flagged


def fix_carousel_scroll_hijack(workspace_path: str) -> list[str]:
    """Stop carousel autoplay from yanking the whole page back into view.

    Claude likes to write `card.scrollIntoView({inline: 'center', block: 'nearest'})`
    inside `setInterval` autoplay loops. `block: 'nearest'` makes the BROWSER
    scroll the page vertically every time the carousel rotates, locking the
    user to that section. Replace any `scrollIntoView` call in a file that
    also has `setInterval` with a parent-relative scrollLeft so only the
    carousel itself moves.
    """
    fixed: list[str] = []
    sections_dir = os.path.join(workspace_path, "src", "components", "sections")
    if not os.path.isdir(sections_dir):
        return fixed

    for root, _, files in os.walk(sections_dir):
        for name in files:
            if not name.endswith((".jsx", ".tsx", ".js", ".ts")):
                continue
            path = os.path.join(root, name)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
            except OSError:
                continue
            if "scrollIntoView" not in content or "setInterval" not in content:
                continue
            new_content = _SCROLL_INTO_VIEW_RE.sub(
                lambda m: (
                    f"if ({m.group(1)} && {m.group(1)}.parentElement) {{ "
                    f"{m.group(1)}.parentElement.scrollTo({{ "
                    f"left: {m.group(1)}.offsetLeft - {m.group(1)}.parentElement.offsetLeft, "
                    f"behavior: 'smooth' }}); }}"
                ),
                content,
            )
            if new_content != content:
                try:
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(new_content)
                    fixed.append(path)
                    logger.info(
                        "fix_carousel_scroll_hijack: replaced scrollIntoView in %s",
                        os.path.relpath(path, workspace_path),
                    )
                except OSError as exc:
                    logger.warning(
                        "fix_carousel_scroll_hijack: failed to write %s: %s",
                        path, exc,
                    )
    return fixed


_GRID_POS_TOKEN_RE = re.compile(
    r"\b(col-(?:span|start|end)-(?:\d+|\[[^\]]+\]|full|auto)(?:\s+(?:sm|md|lg|xl|2xl):col-(?:span|start|end)-(?:\d+|\[[^\]]+\]|full|auto))*"
    r"|\b(?:sm|md|lg|xl|2xl):col-(?:span|start|end)-(?:\d+|\[[^\]]+\]|full|auto)"
    r"|\brow-(?:span|start|end)-(?:\d+|\[[^\]]+\]|full|auto)"
    r"|\b(?:sm|md|lg|xl|2xl):row-(?:span|start|end)-(?:\d+|\[[^\]]+\]|full|auto))"
)
_REVEAL_WRAPS_GRID_CHILD_RE = re.compile(
    r"(<Reveal\b)((?:\s+[\w-]+(?:=(?:\"[^\"]*\"|\{[^}]*\}))?)*)\s*>\s*"
    r"(<div\s+className=\"([^\"]*)\")",
    re.DOTALL,
)

# Template-literal case:
#   <Reveal variant="fade-up" delay={i*80}>
#     <div className={`${colSpan} ${variant} rounded-2xl ...`}>
# The col-span lives in a variable expression. We can't statically read it,
# but the strong convention in our codegen is that the FIRST `${name}` in the
# template literal IS the grid-positioning variable when its name matches
# /col|span|grid|pos|placement/. If it does, move the expression onto the
# Reveal as `className={name}` and strip the leading `${name} ` from the
# template literal.
_REVEAL_WRAPS_TPL_LITERAL_RE = re.compile(
    r"(<Reveal\b)((?:\s+[\w-]+(?:=(?:\"[^\"]*\"|\{[^}]*\}))?)*)\s*>\s*"
    r"(<div\s+className=\{`\$\{([A-Za-z_][A-Za-z0-9_]*)\}\s*)",
    re.DOTALL,
)
_GRID_VAR_NAME_RE = re.compile(r"(?:col|span|grid|placement|pos|layout)", re.I)


def fix_grid_positioning_on_wrapper(workspace_path: str) -> list[str]:
    """Move col-span-*/row-span-* from inner div onto the <Reveal> wrapper.

    CSS Grid only honors grid-positioning classes on the direct child of the
    grid container. Claude often emits:

        <div className="grid grid-cols-12 ...">
          <Reveal variant="fade-up">
            <div className="col-span-12 lg:col-span-7 ...">  ← IGNORED

    The result: every card collapses to 1 column and tiles stack over each
    other. This fixer detects the pattern and pulls grid-positioning tokens
    out of the inner div's className into the <Reveal>'s className prop. The
    visual classes (bg-*, p-*, rounded-*) stay on the inner div.

    Safe because:
      • Only moves CLASSES, never restructures markup.
      • Only matches when the inner div is the FIRST child of <Reveal> AND
        carries at least one col/row positioning token.
      • Idempotent — re-running on already-fixed code is a no-op.

    Returns the list of files modified.
    """
    fixed: list[str] = []
    sections_dir = os.path.join(workspace_path, "src", "components", "sections")
    if not os.path.isdir(sections_dir):
        return fixed

    for root, _, files in os.walk(sections_dir):
        for name in files:
            if not name.endswith((".jsx", ".tsx")):
                continue
            path = os.path.join(root, name)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
            except OSError:
                continue
            if "<Reveal" not in content or "col-span" not in content:
                continue

            mutated = False

            def _rewrite(m: "re.Match[str]") -> str:
                nonlocal mutated
                reveal_tag = m.group(1)
                reveal_attrs = m.group(2) or ""
                inner_div_open = m.group(3)
                inner_classes = m.group(4) or ""

                pos_tokens = _GRID_POS_TOKEN_RE.findall(inner_classes)
                pos_flat: list[str] = []
                for t in pos_tokens:
                    if isinstance(t, tuple):
                        for sub in t:
                            if sub:
                                pos_flat.append(sub)
                    elif t:
                        pos_flat.append(t)
                pos_flat = [s.strip() for s in pos_flat if s and s.strip()]
                if not pos_flat:
                    return m.group(0)

                cleaned_inner = _GRID_POS_TOKEN_RE.sub(" ", inner_classes)
                cleaned_inner = re.sub(r"\s+", " ", cleaned_inner).strip()

                pos_str = " ".join(pos_flat).strip()
                pos_str = re.sub(r"\s+", " ", pos_str)

                existing_className_match = re.search(
                    r'className="([^"]*)"', reveal_attrs
                )
                if existing_className_match:
                    existing = existing_className_match.group(1)
                    merged = (existing + " " + pos_str).strip()
                    new_attrs = (
                        reveal_attrs[: existing_className_match.start()]
                        + f'className="{merged}"'
                        + reveal_attrs[existing_className_match.end():]
                    )
                else:
                    new_attrs = reveal_attrs + f' className="{pos_str}"'

                mutated = True
                return f'{reveal_tag}{new_attrs}><div className="{cleaned_inner}"'

            new_content = _REVEAL_WRAPS_GRID_CHILD_RE.sub(_rewrite, content)

            # Second pass: template-literal classNames with a leading
            # `${variable}` expression that names a grid-positioning var.
            def _rewrite_tpl(m: "re.Match[str]") -> str:
                nonlocal mutated
                reveal_tag = m.group(1)
                reveal_attrs = m.group(2) or ""
                inner_div_open = m.group(3)
                var_name = m.group(4) or ""
                if not _GRID_VAR_NAME_RE.search(var_name):
                    return m.group(0)
                # If Reveal already has a className prop, we leave it alone —
                # ambiguous which value wins.
                if re.search(r'\sclassName=', reveal_attrs):
                    return m.group(0)
                new_attrs = reveal_attrs + f' className={{{var_name}}}'
                # Strip the leading `${var_name} ` from the inner template
                # literal so we don't end up with col-span declared twice.
                new_inner = re.sub(
                    r"^<div\s+className=\{`\$\{" + re.escape(var_name) + r"\}\s*",
                    "<div className={`",
                    inner_div_open,
                )
                mutated = True
                return f'{reveal_tag}{new_attrs}>{new_inner}'

            new_content = _REVEAL_WRAPS_TPL_LITERAL_RE.sub(_rewrite_tpl, new_content)

            if mutated and new_content != content:
                try:
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(new_content)
                    fixed.append(path)
                    logger.info(
                        "fix_grid_positioning_on_wrapper: moved col/row-span onto <Reveal> in %s",
                        os.path.relpath(path, workspace_path),
                    )
                except OSError as exc:
                    logger.warning(
                        "fix_grid_positioning_on_wrapper: failed to write %s: %s",
                        path, exc,
                    )

    return fixed


def fix_footer_empty_columns(workspace_path: str) -> list[str]:
    """Stop the footer from rendering empty placeholder columns ('—').

    Claude's instinct is to render a fixed 4-column footer (Languages, About,
    Support, Legal) even when the brief only supplies links for ONE of those
    groups. The result is three near-empty columns with an italic em-dash
    placeholder, which reads as a broken site.

    This fixer rewrites MarketingFooter.jsx in two ways:
      1. The `<li ...>—</li>` placeholder (or any single-token placeholder
         like '...', 'coming soon') inside the empty-column branch becomes
         `null` — empty columns now render nothing instead of a ghost row.
      2. The column-padding loop that force-adds empty entries to reach a
         fixed count (e.g. `while (result.length < 4)` /
         `if (!result.find(...)) result.push({heading, links: []})`) gets a
         guard that drops empty-links entries from the final column list.

    Both rewrites are surgical and idempotent. We don't restructure the JSX
    tree — just neuter the dead-end branches. Returns the list of files
    modified.
    """
    fixed: list[str] = []
    footer_path = os.path.join(
        workspace_path, "src", "components", "layout", "MarketingFooter.jsx"
    )
    if not os.path.isfile(footer_path):
        return fixed

    try:
        with open(footer_path, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return fixed

    original = content

    # 1) Strip the `col.links.length > 0 ? (...) : (<li>—</li>)` ternary so
    #    the empty-column branch is `null`. The map stays; only the empty
    #    placeholder vanishes.
    _ternary_empty_re = re.compile(
        r"(col\.links\.length\s*>\s*0\s*\?\s*\(\s*col\.links\.map\([\s\S]*?\)\s*\))"
        r"\s*:\s*\(\s*(?://[^\n]*\n\s*)*"  # tolerate JS line-comments before the placeholder
        r"<li[^>]*>[^<]*(?:—|---|\.\.\.|coming soon|tbd|—)[^<]*</li>\s*\)",
        re.IGNORECASE,
    )
    content = _ternary_empty_re.sub(r"\1 : null", content)

    # 2) Filter out empty groups in `buildColumns` before render. Insert a
    #    guard right at the return statement. Idempotent — skips if already
    #    present.
    if "buildColumns" in content and "filter(c => c.links && c.links.length" not in content:
        content = re.sub(
            r"(return\s+result\.slice\([^)]+\)\s*;\s*\n?\s*\})",
            lambda m: m.group(1).replace(
                "return result.slice(",
                "return result.filter(c => c.links && c.links.length > 0).slice(",
            ),
            content,
        )

    # 3) Also guard the final columns variable at the render site so an
    #    empty `links: []` entry that slipped through earlier filters never
    #    becomes a rendered column.
    content = re.sub(
        r"const\s+columns\s*=\s*buildColumns\(([^)]+)\)\s*;",
        r"const columns = buildColumns(\1).filter(c => c.links && c.links.length > 0);",
        content,
        count=1,
    )

    if content != original:
        try:
            with open(footer_path, "w", encoding="utf-8") as f:
                f.write(content)
            fixed.append(footer_path)
            logger.info(
                "fix_footer_empty_columns: removed empty-column placeholders in %s",
                os.path.relpath(footer_path, workspace_path),
            )
        except OSError as exc:
            logger.warning(
                "fix_footer_empty_columns: failed to write %s: %s",
                footer_path, exc,
            )

    return fixed


def fix_banned_icons(workspace_path: str) -> list[str]:
    """Replace banned lucide-react icon imports with inline SVG components.

    Returns list of file paths that were fixed.
    """
    fixed_files = []
    src_dir = os.path.join(workspace_path, "src")
    
    if not os.path.isdir(src_dir):
        src_dir = workspace_path
    
    for root, dirs, files in os.walk(src_dir):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        
        for fname in files:
            ext = os.path.splitext(fname)[1].lower()
            if ext not in _JSX_EXTENSIONS:
                continue
            
            filepath = os.path.join(root, fname)
            try:
                with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
            except Exception:
                continue
            
            # Find all lucide-react imports
            matches = _LUCIDE_IMPORT_RE.findall(content)
            if not matches:
                continue
            
            # Check if any banned icons are imported
            banned_found = {}
            for match in matches:
                icons = [i.strip() for i in match.split(",")]
                for icon in icons:
                    # Handle aliased imports: Facebook as FacebookIcon
                    icon_name = icon.split(" as ")[0].strip()
                    if icon_name in _BANNED_ICONS:
                        alias = icon.split(" as ")[-1].strip() if " as " in icon else icon_name
                        banned_found[icon_name] = alias
            
            if not banned_found:
                continue
            
            new_content = content
            
            # Remove banned icons from the import statement
            def _remove_from_import(match_obj):
                icons_str = match_obj.group(1)
                icons = [i.strip() for i in icons_str.split(",")]
                remaining = []
                for icon in icons:
                    icon_name = icon.split(" as ")[0].strip()
                    # Skip empty tokens from trailing commas / double commas
                    # in the original source — otherwise the rebuilt import
                    # ends up as `import { Menu, X,  } from 'lucide-react'`.
                    if not icon_name:
                        continue
                    if icon_name not in _BANNED_ICONS:
                        remaining.append(icon)

                if not remaining:
                    return ""  # Remove entire import line

                return f"import {{ {', '.join(remaining)} }} from 'lucide-react'"
            
            new_content = _LUCIDE_IMPORT_RE.sub(_remove_from_import, new_content)
            
            # Build inline SVG components for each banned icon
            svg_components = []
            for icon_name, alias in banned_found.items():
                svg_markup = _BANNED_ICONS[icon_name]
                # Splice className + width/height fallback + ...props into the
                # <svg> tag. width/height="20" prevents 0×0 rendering when the
                # caller passes a className without explicit `h-* w-*` sizing
                # (a common cause of "invisible square" icons in dark footer
                # social rows). React's prop merge means className from the
                # caller still wins for sizing when provided.
                spliced_svg = svg_markup.replace(
                    "<svg ",
                    '<svg width="20" height="20" className={className} {...props} ',
                    1,
                )
                component = (
                    f"const {alias} = ({{ className, ...props }}) => (\n"
                    f"  {spliced_svg}\n"
                    f");"
                )
                svg_components.append(component)
            
            # Insert SVG components after the last import statement
            import_end = 0
            for m in re.finditer(r"^import\s.+$", new_content, re.MULTILINE):
                import_end = m.end()
            
            if import_end > 0:
                svg_block = "\n\n// Auto-generated SVG icons (not available in lucide-react)\n"
                svg_block += "\n".join(svg_components)
                svg_block += "\n"
                new_content = new_content[:import_end] + svg_block + new_content[import_end:]
            
            # Clean up empty import lines
            new_content = re.sub(r"^\s*\n\s*\n\s*\n", "\n\n", new_content, flags=re.MULTILINE)
            
            try:
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(new_content)
                rel_path = os.path.relpath(filepath, workspace_path)
                fixed_files.append(rel_path)
                logger.info("Fixed banned icons in %s: %s", rel_path, list(banned_found.keys()))
            except Exception as e:
                logger.warning("Failed to fix banned icons in %s: %s", filepath, e)
    
    if fixed_files:
        logger.info("Banned icon fixer fixed %d files", len(fixed_files))

    return fixed_files


# ╔══════════════════════════════════════════════════════════════╗
# ║  FIXER 2b — Missing Icons.foo Object-Key Auto-Adder         ║
# ╚══════════════════════════════════════════════════════════════╝
# Catches the bug class:
#   ContactSection.jsx imports `import { Icons } from "@/config/icons"` then
#   renders `<Icons.instagram />`, but icons.js never declared an `instagram`
#   key. React resolves `Icons.instagram` to `undefined` and crashes with
#   "Element type is invalid: ... got: undefined".
#
# The fix: scan src/ for Icons.<key> references, diff against the actual
# Icons object, and append the missing keys (with matching lucide-react
# imports) to icons.js. Idempotent — safe to run multiple times.

# Common lucide icon name lookup. Keys are the lowercase tokens AI tools
# tend to write (Icons.instagram, Icons.share2, etc.); values are the
# PascalCase lucide-react component names.
_ICON_NAME_LOOKUP: dict[str, str] = {
    # Social / brand
    "instagram": "Instagram", "twitter": "Twitter", "facebook": "Facebook",
    "linkedin": "Linkedin", "youtube": "Youtube", "github": "Github",
    "twitch": "Twitch", "dribbble": "Dribbble", "figma": "Figma",
    "slack": "Slack", "discord": "MessageSquare", "tiktok": "Music2",
    # Contact
    "mail": "Mail", "email": "Mail", "phone": "Phone", "telephone": "Phone",
    "mappin": "MapPin", "map": "Map", "location": "MapPin",
    "globe": "Globe", "world": "Globe", "website": "Globe",
    "share": "Share", "share2": "Share2",
    "message": "MessageCircle", "messagecircle": "MessageCircle",
    "messagesquare": "MessageSquare", "send": "Send", "chat": "MessageCircle",
    # Common UI
    "check": "Check", "checkcircle": "CheckCircle", "x": "X", "close": "X",
    "menu": "Menu", "search": "Search", "filter": "Filter",
    "plus": "Plus", "minus": "Minus", "edit": "Edit", "trash": "Trash2",
    "arrowright": "ArrowRight", "arrowleft": "ArrowLeft",
    "arrowup": "ArrowUp", "arrowdown": "ArrowDown",
    "chevronright": "ChevronRight", "chevronleft": "ChevronLeft",
    "chevronup": "ChevronUp", "chevrondown": "ChevronDown",
    # Domain
    "home": "Home", "user": "User", "users": "Users", "settings": "Settings",
    "logout": "LogOut", "login": "LogIn", "lock": "Lock", "unlock": "Unlock",
    "calendar": "Calendar", "clock": "Clock", "star": "Star", "heart": "Heart",
    "bookmark": "Bookmark", "tag": "Tag", "image": "Image", "camera": "Camera",
    "play": "Play", "pause": "Pause", "video": "Video", "music": "Music",
    "shoppingcart": "ShoppingCart", "shoppingbag": "ShoppingBag",
    "creditcard": "CreditCard", "wallet": "Wallet",
    "alertcircle": "AlertCircle", "info": "Info", "bell": "Bell",
    "loader": "Loader2", "loader2": "Loader2", "spinner": "Loader2",
    "morevertical": "MoreVertical", "morehorizontal": "MoreHorizontal",
    "external": "ExternalLink", "externallink": "ExternalLink",
    "download": "Download", "upload": "Upload", "copy": "Copy",
    "eye": "Eye", "eyeoff": "EyeOff",
    "coffee": "Coffee", "leaf": "Leaf", "flame": "Flame",
}

# Where the project's Icons object lives. Try each path until one resolves.
_ICONS_FILE_CANDIDATES = (
    "src/config/icons.js",
    "src/config/icons.jsx",
    "src/config/icons.ts",
    "src/config/icons.tsx",
    "src/lib/icons.js",
    "src/lib/icons.jsx",
)

# Match `Icons.<key>` references in JSX/JS. Captures the key.
_ICONS_REF_RE = re.compile(r"\bIcons\.([A-Za-z][A-Za-z0-9]*)\b")

# Find the lucide-react `import { ... } from "lucide-react"` in the icons file
_ICONS_LUCIDE_IMPORT_RE = re.compile(
    r"import\s*\{\s*([^}]+?)\s*\}\s*from\s*[\"']lucide-react[\"']",
    re.DOTALL,
)

# Find the `export const Icons = {` … `}` block in the icons file
_ICONS_OBJECT_RE = re.compile(
    r"export\s+const\s+Icons\s*=\s*\{(.*?)\}\s*;?",
    re.DOTALL,
)


def _resolve_icons_file(workspace_path: str) -> Optional[str]:
    for rel in _ICONS_FILE_CANDIDATES:
        p = os.path.join(workspace_path, rel)
        if os.path.isfile(p):
            return p
    return None


def _existing_icons_keys(icons_src: str) -> set[str]:
    """Return the set of keys currently declared in `export const Icons = { ... }`."""
    m = _ICONS_OBJECT_RE.search(icons_src)
    if not m:
        return set()
    body = m.group(1)
    # Match `key: Component,` or `key,` shorthand
    keys = re.findall(r"([A-Za-z][A-Za-z0-9]*)\s*[:,]", body)
    return set(keys)


def _existing_lucide_imports(icons_src: str) -> set[str]:
    """Return the set of lucide-react component names currently imported."""
    out: set[str] = set()
    for m in _ICONS_LUCIDE_IMPORT_RE.finditer(icons_src):
        for tok in m.group(1).split(","):
            name = tok.strip().split(" as ")[0].strip()
            if name:
                out.add(name)
    return out


def _collect_icons_refs(workspace_path: str) -> set[str]:
    """Walk the project, return the set of all `Icons.<key>` keys referenced."""
    src_dir = os.path.join(workspace_path, "src")
    if not os.path.isdir(src_dir):
        src_dir = workspace_path
    refs: set[str] = set()
    for root, dirs, files in os.walk(src_dir):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for fname in files:
            if os.path.splitext(fname)[1].lower() not in _JSX_EXTENSIONS:
                continue
            try:
                with open(os.path.join(root, fname), "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
            except Exception:
                continue
            # Only count refs in files that import the Icons object
            if "@/config/icons" not in content and "from '../config/icons'" not in content:
                continue
            for m in _ICONS_REF_RE.finditer(content):
                refs.add(m.group(1))
    return refs


def fix_missing_icons_object_keys(workspace_path: str) -> list[str]:
    """Add missing keys to `src/config/icons.js` so `Icons.foo` references resolve.

    Returns a list with the icons.js path if it was patched (else empty).
    Why: AI code-gen frequently writes `<Icons.instagram />` without remembering
    to add `instagram: Instagram` to the shared icons object — at runtime that
    produces "Element type is invalid: ... got: undefined" and crashes the page.
    """
    icons_path = _resolve_icons_file(workspace_path)
    if not icons_path:
        return []

    try:
        with open(icons_path, "r", encoding="utf-8", errors="replace") as f:
            icons_src = f.read()
    except Exception as exc:
        logger.warning("Could not read icons file %s: %s", icons_path, exc)
        return []

    declared = _existing_icons_keys(icons_src)
    referenced = _collect_icons_refs(workspace_path)
    missing = sorted(referenced - declared)
    if not missing:
        return []

    # Map each missing key to a lucide component, skipping unknowns
    additions: list[tuple[str, str]] = []   # (icons_key, lucide_component)
    for key in missing:
        comp = _ICON_NAME_LOOKUP.get(key.lower())
        if comp:
            additions.append((key, comp))
    if not additions:
        logger.info("fix_missing_icons_object_keys: %d unknown keys, no fix applied: %s", len(missing), missing)
        return []

    # 1. Update the lucide-react import line — add any components not yet imported.
    existing_lucide = _existing_lucide_imports(icons_src)
    new_components = sorted({c for _, c in additions if c not in existing_lucide})
    if new_components:
        lucide_match = _ICONS_LUCIDE_IMPORT_RE.search(icons_src)
        if lucide_match:
            existing_block = lucide_match.group(1).strip().rstrip(",")
            merged = existing_block + ",\n  " + ",\n  ".join(new_components)
            new_import = f"import {{\n  {merged}\n}} from \"lucide-react\""
            icons_src = icons_src[:lucide_match.start()] + new_import + icons_src[lucide_match.end():]
        else:
            # No existing lucide import — prepend one
            new_import = f"import {{ {', '.join(new_components)} }} from \"lucide-react\";\n\n"
            icons_src = new_import + icons_src

    # 2. Append missing keys to the Icons object. Insert before the closing brace.
    obj_match = _ICONS_OBJECT_RE.search(icons_src)
    if not obj_match:
        logger.warning("fix_missing_icons_object_keys: no Icons object found in %s", icons_path)
        return []
    obj_body = obj_match.group(1).rstrip().rstrip(",")
    additions_text = ",\n  ".join(f"{key}: {comp}" for key, comp in additions)
    sep = ",\n  " if obj_body.strip() else "  "
    new_body = f"{obj_body}{sep}{additions_text},\n"
    new_obj = f"export const Icons = {{\n  {new_body.lstrip()}}};"
    # Replace the matched span
    icons_src = icons_src[:obj_match.start()] + new_obj + icons_src[obj_match.end():]

    try:
        with open(icons_path, "w", encoding="utf-8") as f:
            f.write(icons_src)
    except Exception as exc:
        logger.warning("Could not write icons file %s: %s", icons_path, exc)
        return []

    added_keys = [k for k, _ in additions]
    logger.info(
        "fix_missing_icons_object_keys: added %d keys to %s — %s",
        len(added_keys), icons_path, added_keys,
    )
    return [os.path.relpath(icons_path, workspace_path)]


# ╔══════════════════════════════════════════════════════════════╗
# ║  FIXER 3 — Import Resolution (Stub Creator)                ║
# ╚══════════════════════════════════════════════════════════════╝

# Patterns to extract import paths
_IMPORT_RE = re.compile(
    r"""(?:import\s+(?:(?:\{[^}]*\}|\w+|\*\s+as\s+\w+)(?:\s*,\s*(?:\{[^}]*\}|\w+))*)\s+from\s+['"]([^'"]+)['"]"""
    r"""|import\s+['"]([^'"]+)['"])""",
    re.MULTILINE,
)

# File extensions to try when resolving imports
_RESOLVE_EXTENSIONS = [".jsx", ".tsx", ".js", ".ts", ".css", ".json"]

# Directories where imports are third-party (don't create stubs)
_THIRD_PARTY_PREFIXES = [
    "react", "next", "framer-motion", "lucide-react", "@tanstack",
    "zustand", "zod", "recharts", "vue", "vite", "@/lib", "@/hooks",
    "clsx", "tailwind", "class-variance", "cmdk", "@radix-ui",
    "date-fns", "react-hook-form", "@hookform", "sonner", "axios",
    "embla-carousel", "input-otp", "vaul", "react-resizable",
    "react-day-picker",
]


def _is_third_party(import_path: str) -> bool:
    """Check if an import path is a third-party package (not a local file)."""
    # Relative imports are local
    if import_path.startswith(".") or import_path.startswith("/"):
        return False
    # @/ alias is local
    if import_path.startswith("@/"):
        return False
    # Check known third-party prefixes
    for prefix in _THIRD_PARTY_PREFIXES:
        if import_path.startswith(prefix):
            return True
    # Anything without a dot/slash at the start is likely third-party
    if not import_path.startswith("."):
        return True
    return False


def _resolve_import_path(import_path: str, importer_file: str, workspace_path: str) -> Optional[str]:
    """Try to resolve an import path to an actual file on disk.
    
    Returns the absolute path if found, None if not found.
    """
    if _is_third_party(import_path):
        return "THIRD_PARTY"  # Skip third-party packages
    
    # Handle @/ alias → src/
    if import_path.startswith("@/"):
        resolved_base = os.path.join(workspace_path, "src", import_path[2:])
    elif import_path.startswith("."):
        # Relative import — resolve from the importer's directory
        importer_dir = os.path.dirname(importer_file)
        resolved_base = os.path.normpath(os.path.join(importer_dir, import_path))
    else:
        return "THIRD_PARTY"
    
    # Try exact path first
    if os.path.isfile(resolved_base):
        return resolved_base
    
    # Try with extensions
    for ext in _RESOLVE_EXTENSIONS:
        candidate = resolved_base + ext
        if os.path.isfile(candidate):
            return candidate
    
    # Try as directory with index file
    for ext in _RESOLVE_EXTENSIONS:
        candidate = os.path.join(resolved_base, f"index{ext}")
        if os.path.isfile(candidate):
            return candidate
    
    return None


def _create_stub(filepath: str, import_names: list[str], workspace_path: str) -> bool:
    """Create a stub component file with minimal exports.
    
    The stub ensures imports don't break the build. The content will be
    overwritten by a later phase or by Claude during build-fix.
    """
    rel_path = os.path.relpath(filepath, workspace_path)
    ext = os.path.splitext(filepath)[1].lower()
    
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    
    # Determine what kind of stub to create
    is_jsx = ext in {".jsx", ".tsx"}
    is_css = ext in {".css", ".scss"}
    
    if is_css:
        stub_content = f"/* Stub: {rel_path} — auto-generated by Lucid AI */\n"
    elif is_jsx:
        # Create a minimal React component
        component_name = os.path.splitext(os.path.basename(filepath))[0]
        # Clean up component name (remove non-alphanumeric)
        component_name = re.sub(r"[^a-zA-Z0-9]", "", component_name)
        if not component_name[0].isupper():
            component_name = component_name[0].upper() + component_name[1:]
        
        exports = []
        for name in import_names:
            clean_name = name.strip()
            if clean_name == "default" or clean_name == component_name:
                continue
            exports.append(f"export function {clean_name}({{ children, ...props }}) {{\n  return <div {{...props}}>{{children}}</div>;\n}}")
        
        stub_content = f"'use client';\n\n// Stub: {rel_path} — auto-generated by Lucid AI\n// This file was created to prevent import errors. It will be properly\n// implemented if the build validator detects issues.\n\n"
        stub_content += f"export default function {component_name}({{ children, className, ...props }}) {{\n"
        stub_content += f"  return <div className={{className}} {{...props}}>{{children}}</div>;\n"
        stub_content += f"}}\n"
        if exports:
            stub_content += "\n" + "\n\n".join(exports) + "\n"
    else:
        # JS/TS file — determine if the file is likely a React component module.
        # Heuristics: path contains components/ or pages/, or filename is PascalCase.
        _basename_no_ext = os.path.splitext(os.path.basename(filepath))[0]
        _norm_path = filepath.replace("\\", "/")
        _is_component_module = (
            "/components/" in _norm_path
            or "/pages/" in _norm_path
            or "/screens/" in _norm_path
            or "/views/" in _norm_path
            or (bool(_basename_no_ext) and _basename_no_ext[0].isupper())
        )

        def _is_component_name(n: str) -> bool:
            """Return True if the name looks like a React component (PascalCase)."""
            n = n.strip()
            return bool(n) and n[0].isupper() and n not in {"default"}

        if _is_component_module:
            # Treat this stub as a JSX component file even though the extension is .js/.ts
            component_name = re.sub(r"[^a-zA-Z0-9]", "", _basename_no_ext) or "StubComponent"
            if component_name[0].islower():
                component_name = component_name[0].upper() + component_name[1:]

            named_exports = []
            for name in import_names:
                clean_name = name.strip()
                if clean_name in ("default", component_name):
                    continue
                if _is_component_name(clean_name):
                    named_exports.append(
                        f"export function {clean_name}({{ children, className, ...props }}) {{\n"
                        f"  return <div className={{className}} {{...props}}>{{children}}</div>;\n"
                        f"}}"
                    )
                else:
                    named_exports.append(f"export const {clean_name} = null;")

            stub_content = (
                f"'use client';\n\n"
                f"// Stub: {rel_path} — auto-generated by Lucid AI\n\n"
                f"export default function {component_name}({{ children, className, ...props }}) {{\n"
                f"  return <div className={{className}} {{...props}}>{{children}}</div>;\n"
                f"}}\n"
            )
            if named_exports:
                stub_content += "\n" + "\n\n".join(named_exports) + "\n"
        else:
            # Non-component JS/TS module — export safe falsy values rather than
            # `undefined`, which crashes Server Component rendering when a component
            # name is mistakenly exported as undefined.
            stub_content = f"// Stub: {rel_path} — auto-generated by Lucid AI\n\n"
            for name in import_names:
                clean_name = name.strip()
                if clean_name == "default":
                    # If it's a plain default export in a non-component file,
                    # export an empty object (safe for config/data imports).
                    stub_content += f"const _default = {{}};\nexport default _default;\n"
                elif _is_component_name(clean_name):
                    # PascalCase name in a non-component path — still likely a component.
                    stub_content += (
                        f"export function {clean_name}({{ children, className, ...props }}) {{\n"
                        f"  return null;\n"
                        f"}}\n"
                    )
                else:
                    # Truly non-component: export null (not undefined — undefined breaks RSC)
                    stub_content += f"export const {clean_name} = null;\n"
    
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(stub_content)
        logger.info("Created stub: %s (exports: %s)", rel_path, import_names)
        return True
    except Exception as e:
        logger.warning("Failed to create stub %s: %s", rel_path, e)
        return False


def fix_unresolved_imports(workspace_path: str) -> list[str]:
    """Scan all source files for imports that point to missing files.
    Creates stub files to prevent build errors.
    
    Returns list of stub file paths that were created.
    """
    created_stubs = []
    src_dir = os.path.join(workspace_path, "src")
    
    if not os.path.isdir(src_dir):
        src_dir = workspace_path
    
    # Collect all source files
    source_files = []
    for root, dirs, files in os.walk(src_dir):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for fname in files:
            ext = os.path.splitext(fname)[1].lower()
            if ext in _JSX_EXTENSIONS:
                source_files.append(os.path.join(root, fname))
    
    # Track what needs stubs: {abs_path: [imported_names]}
    missing_imports: dict[str, list[str]] = {}
    
    for filepath in source_files:
        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception:
            continue
        
        # Find all import statements
        for match in _IMPORT_RE.finditer(content):
            import_path = match.group(1) or match.group(2)
            if not import_path:
                continue
            
            resolved = _resolve_import_path(import_path, filepath, workspace_path)
            
            if resolved == "THIRD_PARTY":
                continue  # Skip third-party packages
            
            if resolved is not None:
                continue  # File exists, no problem
            
            # Import cannot be resolved — figure out where the stub should go
            if import_path.startswith("@/"):
                stub_base = os.path.join(workspace_path, "src", import_path[2:])
            elif import_path.startswith("."):
                importer_dir = os.path.dirname(filepath)
                stub_base = os.path.normpath(os.path.join(importer_dir, import_path))
            else:
                continue  # Can't resolve non-relative, non-alias imports
            
            # Determine stub extension
            # Look at the import statement to guess the file type
            stub_path = None
            for ext in [".jsx", ".tsx", ".js", ".ts"]:
                candidate = stub_base + ext
                # Don't overwrite existing files
                if not os.path.exists(candidate):
                    stub_path = candidate
                    break
            
            if not stub_path:
                stub_path = stub_base + ".jsx"  # Default to .jsx
            
            # Extract imported names for the stub
            import_line = match.group(0)
            names_match = re.search(r"\{([^}]+)\}", import_line)
            if names_match:
                names = [n.strip().split(" as ")[0].strip() for n in names_match.group(1).split(",") if n.strip()]
            else:
                # Default import
                default_match = re.search(r"import\s+(\w+)", import_line)
                names = [default_match.group(1)] if default_match else ["default"]
            
            if stub_path not in missing_imports:
                missing_imports[stub_path] = []
            missing_imports[stub_path].extend(names)
    
    # Create stubs for all missing files
    for stub_path, names in missing_imports.items():
        # Deduplicate names
        unique_names = list(dict.fromkeys(names))
        if _create_stub(stub_path, unique_names, workspace_path):
            created_stubs.append(os.path.relpath(stub_path, workspace_path))
    
    if created_stubs:
        logger.info("Import resolver created %d stub files", len(created_stubs))
    
    return created_stubs


# ╔══════════════════════════════════════════════════════════════╗
# ║  FIXER 4 — Named-Import / Default-Export Mismatch Fixer    ║
# ╚══════════════════════════════════════════════════════════════╝

# Matches:  import { Foo, Bar } from './something'
_NAMED_IMPORT_RE = re.compile(
    r'^(import\s+)\{([^}]+)\}(\s+from\s+[\'"]([^\'"]+)[\'"])',
    re.MULTILINE,
)
# Matches any `export default` in a file (function, class, const, arrow …)
_HAS_DEFAULT_EXPORT_RE = re.compile(r'\bexport\s+default\b')
# Matches named export of a specific identifier:  export function Foo | export const Foo | export { Foo }
_HAS_NAMED_EXPORT_RE = re.compile(
    r'export\s+(?:function|class|const|let|var)\s+({name})\b'
    r'|export\s*\{[^}}]*\b({name})\b[^}}]*\}'
)


def fix_named_import_default_export_mismatch(workspace_path: str) -> list[str]:
    """Fix imports that use named braces for a file that only has a default export.

    A very common AI generation mistake:

        // page.js
        import { MarketingHeader } from "@/components/layout/MarketingHeader";

        // MarketingHeader.jsx — only exports:
        export default function MarketingHeader() { ... }

    Named import from a default-only module resolves to ``undefined``, which
    crashes Next.js App Router with "Unsupported Server Component type: undefined".

    Fix: when EVERY name in ``{ A, B }`` matches the file's default export name
    and the file has no named export of that name, rewrite the import to use
    the default import syntax ``import A from '...'``.

    Returns list of file paths that were modified.
    """
    fixed_files: list[str] = []

    src_dir = os.path.join(workspace_path, "src")
    if not os.path.isdir(src_dir):
        src_dir = workspace_path

    for root, dirs, files in os.walk(src_dir):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]

        for fname in files:
            ext = os.path.splitext(fname)[1].lower()
            if ext not in _JSX_EXTENSIONS:
                continue

            filepath = os.path.join(root, fname)
            try:
                with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
            except Exception:
                continue

            if "{" not in content:
                continue  # No named imports at all — fast path

            new_content = content
            changed = False

            for m in _NAMED_IMPORT_RE.finditer(content):
                import_prefix = m.group(1)   # "import "
                names_str = m.group(2)        # "Foo, Bar as B"
                from_suffix = m.group(3)      # " from './Foo'"
                import_path = m.group(4)      # "./Foo"

                # Resolve the imported file
                resolved = _resolve_import_path(import_path, filepath, workspace_path)
                if not resolved or resolved == "THIRD_PARTY" or not os.path.isfile(resolved):
                    continue

                try:
                    with open(resolved, "r", encoding="utf-8", errors="replace") as rf:
                        target_content = rf.read()
                except Exception:
                    continue

                if not _HAS_DEFAULT_EXPORT_RE.search(target_content):
                    continue  # Target has no default export — not our pattern

                # Parse the names: "Foo, Bar as B" → [("Foo", "Foo"), ("Bar", "B")]
                raw_names = [n.strip() for n in names_str.split(",") if n.strip()]
                parsed = []
                for raw in raw_names:
                    parts = raw.split(" as ")
                    original = parts[0].strip()
                    alias = parts[-1].strip()
                    parsed.append((original, alias))

                # Check each name: if the target file lacks a named export for it
                # but DOES have a default export, convert that name to default import.
                default_imports = []   # (original, alias) pairs that should be default imports
                keep_named = []        # pairs that have real named exports → stay in braces

                for original, alias in parsed:
                    named_pattern = re.compile(
                        r'export\s+(?:function|class|const|let|var)\s+' + re.escape(original) + r'\b'
                        r'|export\s*\{[^}]*\b' + re.escape(original) + r'\b[^}]*\}'
                    )
                    if named_pattern.search(target_content):
                        keep_named.append((original, alias))
                    else:
                        # No named export found — candidate for default import conversion
                        default_imports.append((original, alias))

                if not default_imports:
                    continue  # All names have proper named exports

                # Build replacement import(s)
                replacement_lines = []

                # Default imports (convert each to its own `import X from '...'`)
                for original, alias in default_imports:
                    local_name = alias if alias != original else original
                    replacement_lines.append(
                        f"import {local_name}{from_suffix}"
                    )

                # Remaining named imports (keep in braces)
                if keep_named:
                    named_part = ", ".join(
                        orig if orig == alias else f"{orig} as {alias}"
                        for orig, alias in keep_named
                    )
                    replacement_lines.append(f"import {{{named_part}}}{from_suffix}")

                replacement = "\n".join(replacement_lines)
                old_statement = m.group(0)
                if replacement != old_statement:
                    new_content = new_content.replace(old_statement, replacement, 1)
                    changed = True
                    logger.info(
                        "fix_named_import_mismatch: %s: replaced '%s' → '%s'",
                        os.path.relpath(filepath, workspace_path),
                        old_statement[:80],
                        replacement[:80],
                    )

            if changed:
                try:
                    with open(filepath, "w", encoding="utf-8") as f:
                        f.write(new_content)
                    fixed_files.append(os.path.relpath(filepath, workspace_path))
                except Exception as exc:
                    logger.warning("fix_named_import_mismatch: write failed for %s: %s", filepath, exc)

    if fixed_files:
        logger.info("Named-import/default-export mismatch fixer fixed %d file(s)", len(fixed_files))

    return fixed_files


# ╔══════════════════════════════════════════════════════════════╗
# ║  FIXER 4b — Missing Default Export Fixer                   ║
# ╚══════════════════════════════════════════════════════════════╝

# Matches:  import Foo from './something'  (no braces = default import)
_DEFAULT_IMPORT_RE = re.compile(
    r'^import\s+(\w+)\s+from\s+[\'"]([^\'"]+)[\'"]',
    re.MULTILINE,
)
# Matches a named export of a specific name that could serve as the default
_NAMED_EXPORT_FN_RE = re.compile(
    r'export\s+(?:function|class|const|let|var)\s+(\w+)'
)


def fix_missing_default_export(workspace_path: str) -> list[str]:
    """Add a missing `export default` to component files imported as default.

    The bug:
        // page.js
        import HeroSection from '@/components/sections/HeroSection'

        // HeroSection.jsx — only has:
        export function HeroSection() { ... }   // named, no default

    Result: HeroSection resolves to undefined → crash.

    Fix: append `export default HeroSection;` to any component file that
    is imported as a default import but has no `export default` statement.
    Only adds the default when the file has exactly one top-level named
    export whose name matches the imported identifier.

    Returns list of file paths that were modified.
    """
    fixed_files: list[str] = []

    src_dir = os.path.join(workspace_path, "src")
    if not os.path.isdir(src_dir):
        src_dir = workspace_path

    for root, dirs, files in os.walk(src_dir):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]

        for fname in files:
            ext = os.path.splitext(fname)[1].lower()
            if ext not in _JSX_EXTENSIONS:
                continue

            filepath = os.path.join(root, fname)
            try:
                with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
            except Exception:
                continue

            for m in _DEFAULT_IMPORT_RE.finditer(content):
                imported_name = m.group(1)   # e.g. "HeroSection"
                import_path   = m.group(2)   # e.g. "@/components/sections/HeroSection"

                resolved = _resolve_import_path(import_path, filepath, workspace_path)
                if not resolved or resolved == "THIRD_PARTY" or not os.path.isfile(resolved):
                    continue

                try:
                    with open(resolved, "r", encoding="utf-8", errors="replace") as rf:
                        target = rf.read()
                except Exception:
                    continue

                # Skip if the target already has a default export
                if _HAS_DEFAULT_EXPORT_RE.search(target):
                    continue

                # Find named exports in the target file
                named_exports = _NAMED_EXPORT_FN_RE.findall(target)
                if imported_name not in named_exports:
                    continue  # Can't confidently add default for unknown name

                # Append default export
                new_target = target.rstrip() + f"\n\nexport default {imported_name};\n"
                try:
                    with open(resolved, "w", encoding="utf-8") as wf:
                        wf.write(new_target)
                    rel = os.path.relpath(resolved, workspace_path)
                    fixed_files.append(rel)
                    logger.info(
                        "fix_missing_default_export: added 'export default %s' to %s",
                        imported_name, rel,
                    )
                except Exception as exc:
                    logger.warning("fix_missing_default_export: write failed for %s: %s", resolved, exc)

    if fixed_files:
        logger.info("Missing-default-export fixer fixed %d file(s)", len(fixed_files))

    return fixed_files


# ╔══════════════════════════════════════════════════════════════╗
# ║  FIXER 5 — Dynamic Route Conflict Remover                  ║
# ╚══════════════════════════════════════════════════════════════╝

def fix_dynamic_route_conflicts(workspace_path: str) -> list[str]:
    """Remove duplicate dynamic route segments that share the same parent directory.

    Next.js crashes on startup with:
        Error: You cannot use different slug names for the same dynamic path
        ('id' !== 'slug')
    when a directory has two children like ``[id]/`` and ``[slug]/`` that both
    match the same URL shape.  This is a common AI generation artefact where
    the same route is written twice with different parameter names.

    Strategy:
    - Walk every directory under ``app/`` (or ``src/app/``).
    - For each directory, collect its immediate child directories whose names
      match the ``[param]`` pattern.
    - When two or more such children exist under the same parent, keep the one
      whose ``page.js/page.tsx`` file (if it has one) is the largest / most
      complete, then delete the rest.
    - If neither has a page file, keep the one named ``[id]`` (conventional),
      otherwise keep the lexicographically first one.

    Returns list of directory paths that were deleted (relative to workspace).
    """
    import shutil

    removed: list[str] = []

    _DYN_RE = re.compile(r"^\[.+\]$")  # matches [id], [slug], [courseId], etc.

    # Locate app directory
    candidates = [
        os.path.join(workspace_path, "src", "app"),
        os.path.join(workspace_path, "app"),
        os.path.join(workspace_path, "src", "pages"),
        os.path.join(workspace_path, "pages"),
    ]
    app_dirs = [c for c in candidates if os.path.isdir(c)]
    if not app_dirs:
        return removed

    for app_dir in app_dirs:
        for root, dirs, _files in os.walk(app_dir, topdown=True):
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]

            dynamic_children = [d for d in dirs if _DYN_RE.match(d)]
            if len(dynamic_children) < 2:
                continue

            # Multiple dynamic segments under the same parent — conflict!
            logger.warning(
                "fix_dynamic_route_conflicts: conflict in %s — children: %s",
                root, dynamic_children,
            )

            # Score each candidate: prefer the one with a page file and more content.
            def _score(dname: str) -> int:
                dpath = os.path.join(root, dname)
                score = 0
                for fname in ("page.js", "page.jsx", "page.tsx", "page.ts",
                              "index.js", "index.jsx", "index.tsx"):
                    fpath = os.path.join(dpath, fname)
                    if os.path.isfile(fpath):
                        score += os.path.getsize(fpath)
                # Prefer conventional name [id] as a tiebreaker
                if dname in ("[id]", "[Id]"):
                    score += 1
                return score

            dynamic_children_sorted = sorted(dynamic_children, key=_score, reverse=True)
            keep = dynamic_children_sorted[0]
            to_remove = dynamic_children_sorted[1:]

            for dname in to_remove:
                dpath = os.path.join(root, dname)
                rel = os.path.relpath(dpath, workspace_path)
                try:
                    shutil.rmtree(dpath)
                    removed.append(rel)
                    logger.info("fix_dynamic_route_conflicts: removed %s (kept %s)", rel, keep)
                except Exception as exc:
                    logger.warning("fix_dynamic_route_conflicts: could not remove %s: %s", dpath, exc)

            # Update dirs so os.walk doesn't descend into removed directories
            dirs[:] = [d for d in dirs if d not in to_remove]

    if removed:
        logger.info("Dynamic route conflict fixer removed %d director(y/ies): %s", len(removed), removed)

    return removed


# ╔══════════════════════════════════════════════════════════════╗
# ║  FIXER 5 — JSX Unescaped Entities                          ║
# ╚══════════════════════════════════════════════════════════════╝

# Matches bare apostrophes/quotes inside JSX text nodes (not inside attributes or JS strings).
# Strategy: replace ' and " that appear between > and < (i.e. JSX text content).
_JSX_TEXT_RE = re.compile(r">((?:[^<]|\n)*?)<", re.DOTALL)


def _escape_jsx_text(match: re.Match) -> str:
    text = match.group(1)
    # Skip when ANY JSX/JS-expression markers are present. Previous version
    # only checked for `{` which left a class of bugs:
    #
    #   {isSubmitting ? (
    #     <>Loading…</>
    #   ) : (
    #     'Reserve'   ← regex captures `>...</>...) : (...'Reserve'...)}...<`
    #   )}
    #
    # The captured slice has no `{` (only `}`) but it IS the else-branch of a
    # JS ternary, so escaping `'` to `&apos;` produced invalid JS and broke
    # `next build` with "Expression expected".
    #
    # Skip if any of these are present:
    #   • `{` `}` — JSX expression boundaries
    #   • `(` `)` — JS expression continuation across newlines (ternary arms,
    #              fragment-wrapped JSX, conditional renders)
    #   • `=>`    — inline arrow function
    #   • `\`...\``  — template-literal continuation
    if any(c in text for c in "{}()`") or "=>" in text:
        return match.group(0)
    text = text.replace("'", "&apos;")
    text = text.replace('"', "&quot;")
    return f">{text}<"


def fix_jsx_reveal_imbalance(workspace_path: str) -> list[str]:
    """Balance <Reveal>…</Reveal> open/close tag counts per file.

    Claude occasionally writes one extra `</Reveal>` (or, more rarely, drops one)
    when wrapping complex conditional JSX. The next build fails with
    "Unexpected token" because the imbalance only manifests at JSX parse time.

    Strategy (conservative — only fixes the common case):
      • Per file under src/components/, count `<Reveal …>` opens vs `</Reveal>` closes.
      • If they balance, do nothing.
      • If closes > opens, drop the last (closes − opens) `</Reveal>` lines.
        Walking from the END of the file is safe because Claude's bug pattern
        is "extra closing tag after the last real Reveal block ends".
      • If opens > closes, skip — wrapping with extra closes risks breaking
        unrelated structure. Log and leave for manual review.

    Returns the list of files modified.
    """
    fixed_files = []
    src_dir = os.path.join(workspace_path, "src")
    if not os.path.isdir(src_dir):
        return fixed_files

    open_re = re.compile(r"<Reveal\b")
    close_re = re.compile(r"</Reveal\s*>")

    for root, _, files in os.walk(src_dir):
        for name in files:
            if not (name.endswith(".jsx") or name.endswith(".tsx")):
                continue
            path = os.path.join(root, name)
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    content = fh.read()
            except (OSError, UnicodeDecodeError):
                continue

            opens = len(open_re.findall(content))
            closes = len(close_re.findall(content))
            if opens == closes:
                continue
            if opens > closes:
                logger.warning(
                    "fix_jsx_reveal_imbalance: %s has %d opens > %d closes — leaving for manual review",
                    path, opens, closes,
                )
                continue

            # closes > opens: drop the trailing (closes - opens) close tags.
            excess = closes - opens
            # Walk backwards through the source, removing whole lines that are
            # JUST a `</Reveal>` (with optional whitespace). This is the bug
            # shape Claude produces — an orphan closing tag on its own line.
            lines = content.splitlines(keepends=True)
            removed = 0
            i = len(lines) - 1
            while i >= 0 and removed < excess:
                if close_re.search(lines[i]) and lines[i].strip() == "</Reveal>":
                    lines.pop(i)
                    removed += 1
                i -= 1
            if removed == 0:
                # The orphan close isn't on its own line — fall back to
                # surgical regex removal of the LAST (excess) close tags.
                new_content = content
                for _ in range(excess):
                    # find last occurrence and remove just the tag
                    m = None
                    for m in close_re.finditer(new_content):
                        pass
                    if m is None:
                        break
                    new_content = new_content[:m.start()] + new_content[m.end():]
                if new_content != content:
                    try:
                        with open(path, "w", encoding="utf-8") as fh:
                            fh.write(new_content)
                        fixed_files.append(path)
                        logger.info(
                            "fix_jsx_reveal_imbalance: %s — removed %d excess </Reveal> tag(s) inline",
                            path, excess,
                        )
                    except OSError:
                        pass
                continue

            new_content = "".join(lines)
            try:
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(new_content)
                fixed_files.append(path)
                logger.info(
                    "fix_jsx_reveal_imbalance: %s — removed %d orphan </Reveal> line(s) (opens=%d closes=%d)",
                    path, removed, opens, closes,
                )
            except OSError:
                pass

    return fixed_files


def fix_unescaped_entities(workspace_path: str) -> list[str]:
    """Replace bare ' and \" in JSX text nodes with HTML entities.

    Fixes react/no-unescaped-entities ESLint errors that cause Vercel build failures.
    Only touches text between JSX tags (>...<), not JS strings or attribute values.
    """
    fixed_files = []
    src_dir = os.path.join(workspace_path, "src")
    if not os.path.isdir(src_dir):
        src_dir = workspace_path

    for root, dirs, files in os.walk(src_dir):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for fname in files:
            if os.path.splitext(fname)[1].lower() not in {".jsx", ".tsx"}:
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                    original = f.read()
            except Exception:
                continue

            fixed = _JSX_TEXT_RE.sub(_escape_jsx_text, original)
            if fixed != original:
                try:
                    with open(fpath, "w", encoding="utf-8") as f:
                        f.write(fixed)
                    fixed_files.append(os.path.relpath(fpath, workspace_path))
                except Exception as e:
                    logger.warning("fix_unescaped_entities: could not write %s: %s", fpath, e)

    if fixed_files:
        logger.info("Unescaped entities fixer fixed %d file(s)", len(fixed_files))
    return fixed_files


# ╔══════════════════════════════════════════════════════════════╗
# ║  FIXER 5a — Single-quoted JS strings with internal apostrophes ║
# ╚══════════════════════════════════════════════════════════════╝
# Symptom: `next build` fails with
#   Error: Unexpected token ... `chef's` (or similar)
# Cause: Claude wrote a JS string literal like
#   detail: '14 guests per evening's seating; gratuity included.'
# where the apostrophe in `evening's` terminates the string at column 26
# and the rest of the line becomes garbage to the parser.
#
# Fix: scan each line, find single-quoted string literals whose content
# contains an unescaped apostrophe, and convert the outer quotes to "..."
# while escaping any inner double quotes. We skip lines that are obviously
# JSX (start with `<` or contain `>`) — those are handled by FIXER 5.
# Match a single-quoted literal whose body contains an English contraction
# (letter-apostrophe-letter). The contraction requirement is what stops the
# regex from matching ACROSS string boundaries: a ternary like
#     'Click to zoom out' : 'Click to zoom in'
# has no `letter'letter` between the outer quotes (just `' : '`), so it is
# correctly skipped. A real bug like
#     '14 guests per evening's seating'
# DOES contain `g's ` (letter-apos-letter), so we rewrite it.
_APOSTROPHE_WORD_CHARS = r"A-Za-zÀ-ÖØ-öø-ÿĀ-ſƀ-ɏ"
_APOSTROPHE_WORD_RE = rf"[{_APOSTROPHE_WORD_CHARS}]'[{_APOSTROPHE_WORD_CHARS}]"
_CONTRACTION_QUOTED_RE = re.compile(
    rf"'(?P<body>[^'\n]*?{_APOSTROPHE_WORD_RE}[^'\n]*?)'(?=[\s,;:)\]}}])"
)
_BROKEN_SINGLE_QUOTED_PATTERNS = [
    # Object/property values: name: 'Farg'ona', title: 'Ko'cha'
    re.compile(
        rf"(?P<prefix>\b[\w$]+\s*:\s*)'(?P<body>[^\"\n]*?{_APOSTROPHE_WORD_RE}[^\"\n]*?)'(?P<suffix>\s*[,}}\]])"
    ),
    # Assignments and call/array arguments: const city = 'Farg'ona';
    re.compile(
        rf"(?P<prefix>(?:=|\(|\[|,)\s*)'(?P<body>[^\"\n]*?{_APOSTROPHE_WORD_RE}[^\"\n]*?)'(?P<suffix>\s*[,;)\]\}}])"
    ),
    # JSX single-quoted attributes: <Card title='Farg'ona' />
    re.compile(
        rf"(?P<prefix>\s[\w:-]+\s*=\s*)'(?P<body>[^\"\n]*?{_APOSTROPHE_WORD_RE}[^\"\n]*?)'(?P<suffix>\s|/?>)"
    ),
]


def _line_quotes_balanced(line: str) -> bool:
    """Return True if JS-like string delimiters are balanced on this line.

    Apostrophes inside double-quoted strings are content, not delimiters:
    ``"Farg'ona"`` is valid and must pass.
    """
    quote: str | None = None
    escaped = False
    for ch in line:
        if escaped:
            escaped = False
            continue
        if ch == "\\" and quote:
            escaped = True
            continue
        if quote:
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"', "`"):
            quote = ch
    return quote is None


def _double_quote_broken_single_literals(line: str) -> tuple[str, bool]:
    """Repair single-quoted literals containing a real word apostrophe.

    The older fixer handled only already-balanced literals. Uzbek place names
    such as ``'Farg'ona'`` are syntactically broken before the final quote, so
    the balanced-literal regex never saw them. These targeted patterns cover
    object values, assignments/array items, and JSX attributes without trying
    to parse all JavaScript.
    """
    mutated = False

    def _swap(match: re.Match) -> str:
        nonlocal mutated
        body = match.group("body")
        if '"' in body:
            return match.group(0)
        mutated = True
        return f'{match.group("prefix")}"{body}"{match.group("suffix")}'

    candidate = line
    for pattern in _BROKEN_SINGLE_QUOTED_PATTERNS:
        candidate = pattern.sub(_swap, candidate)
    return candidate, mutated


def fix_jsx_apostrophe_in_js_string(workspace_path: str) -> list[str]:
    """Convert single-quoted JS strings with internal `'` to double-quoted.

    Targets the most common Claude bug: writing
        `detail: '14 guests per evening's seating'`
    instead of
        `detail: "14 guests per evening's seating"`.

    Conservative strategy:
      • Only match when the body contains a real English contraction
        (letter-apostrophe-letter), so we never cross a ternary like
        `'a' : 'b'`.
      • After rewriting, RE-VALIDATE the line: if it now has an odd
        count of `'` or `"` we revert. This guards against any edge
        case the regex still gets wrong.
    """
    fixed_files: list[str] = []
    src_dir = os.path.join(workspace_path, "src")
    if not os.path.isdir(src_dir):
        src_dir = workspace_path

    for root, dirs, files in os.walk(src_dir):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for fname in files:
            ext = os.path.splitext(fname)[1].lower()
            if ext not in {".jsx", ".tsx", ".js", ".ts"}:
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                    original = f.read()
            except Exception:
                continue

            if "'" not in original:
                continue

            new_lines: list[str] = []
            mutated = False
            for line in original.splitlines(keepends=True):
                stripped = line.lstrip()
                # Skip obviously pure-JSX text / comment / import / unrelated.
                if (
                    stripped.startswith("//")
                    or stripped.startswith("import ")
                    or stripped.startswith("from ")
                    or stripped.startswith("*")
                    or "'use client'" in stripped
                    or "'use server'" in stripped
                ):
                    new_lines.append(line)
                    continue

                def _swap(match: re.Match) -> str:
                    body = match.group("body")
                    # Refuse if body would need double-quote escaping.
                    if '"' in body:
                        return match.group(0)
                    return f'"{body}"'

                candidate, repaired_broken = _double_quote_broken_single_literals(line)
                candidate = _CONTRACTION_QUOTED_RE.sub(_swap, candidate)
                if candidate != line and (_line_quotes_balanced(candidate) or repaired_broken):
                    new_lines.append(candidate)
                    mutated = True
                elif repaired_broken and candidate != line:
                    # If the line still looks quote-unbalanced after a targeted
                    # repair, keep the original. Better to let the build error
                    # surface than silently corrupt adjacent JSX/JS.
                    new_lines.append(line)
                else:
                    new_lines.append(line)

            if mutated:
                try:
                    with open(fpath, "w", encoding="utf-8") as f:
                        f.writelines(new_lines)
                    fixed_files.append(os.path.relpath(fpath, workspace_path))
                except Exception as e:
                    logger.warning(
                        "fix_jsx_apostrophe_in_js_string: could not write %s: %s",
                        fpath, e,
                    )

    if fixed_files:
        logger.info(
            "Apostrophe-in-JS-string fixer fixed %d file(s)", len(fixed_files),
        )
    return fixed_files


# ╔══════════════════════════════════════════════════════════════╗
# ║  FIXER 5b — Reverse mis-escaped HTML entities in JS context ║
# ╚══════════════════════════════════════════════════════════════╝
# Recovery layer for projects produced by an older fix_unescaped_entities
# that escaped `'`/`"` inside JS expression context (ternary branches,
# function args, return statements). Symptom: `next build` fails with
# "Expression expected" pointing at `&apos;` or `&quot;` in JS code.
#
# This finds `&apos;`/`&quot;` that appear OUTSIDE a JSX tag's text content
# (i.e. either between `(` and `)` of a JS expression, after `return`, or
# after `=` `,` `:`) and reverses them back to bare quotes.

# Match a PAIR of escaped quotes that look like a string literal — the
# same entity name on both sides (apos…apos or quot…quot), no newlines or
# unrelated entities inside. This is far more reliable than scanning every
# entity in isolation: when the pair is in JS expression context (e.g.
# `cond ? &apos;yes&apos; : &apos;no&apos;`), the opener's preceding
# operator (`?`, `:`) classifies the WHOLE pair, not just one side.
_ESCAPED_STRING_PAIR_RE = re.compile(
    r"&(apos|quot);([^&\n]*?)&\1;",
    re.DOTALL,
)

# JS-expression operators. If the LAST non-whitespace char immediately
# BEFORE the opening entity is one of these, the pair is a JS string
# literal (ternary branch, fn arg, attribute, logical-op operand, etc.).
# We strip prior HTML entities first so e.g. `It&apos;s ` doesn't leave a
# bare `;` that fools the test.
_JS_TAIL_OPERATORS = frozenset("():,?=|&;")
_ENTITY_STRIP_RE = re.compile(r"&[a-zA-Z]+;|&#\d+;|&#x[0-9a-fA-F]+;")


def fix_mis_escaped_entities_in_js(workspace_path: str) -> list[str]:
    """Reverse `&apos;`/`&quot;` that ended up inside JS expression context.

    The previous fix_unescaped_entities ran with a too-loose JSX-text regex
    that captured ternary else-branches like `) : ('text')` and wrote
    `&apos;text&apos;` into the JS source. `next build` then chokes with
    'Expression expected'.

    Walks each .jsx/.tsx file, finds every `&apos;`/`&quot;`, examines the
    preceding 30 chars to decide if the entity is in JS context, and reverts
    only those occurrences. JSX text node entities are left alone.
    """
    fixed_files = []
    src_dir = os.path.join(workspace_path, "src")
    if not os.path.isdir(src_dir):
        src_dir = workspace_path

    for root, dirs, files in os.walk(src_dir):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for fname in files:
            if os.path.splitext(fname)[1].lower() not in {".jsx", ".tsx"}:
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                    original = f.read()
            except Exception:
                continue
            if "&apos;" not in original and "&quot;" not in original:
                continue

            # Iterate over PAIRS of escaped entities. For each pair, look at
            # the last non-whitespace char immediately before the opener:
            #   • JS operator → the pair is a JS string literal → revert both
            #   • alphanumeric → the pair is two separate JSX-text entities
            #     (e.g. `It&apos;s a day, isn&apos;t`) → keep both
            #
            # Why pairs work where single-entity scanning failed:
            #   `cond ? (&apos;Reserve&apos;)`  ← opener is preceded by `(` (JS)
            #   `It&apos;s isn&apos;t`         ← opener `&apos;` after `t` (text)
            # The single-entity scan correctly classified the OPENER but
            # mis-classified the CLOSER (its preceding char is the last
            # letter of the string content, e.g. `e` in `Reserve`).
            changed = False
            new_chunks: list[str] = []
            cursor = 0
            for m in _ESCAPED_STRING_PAIR_RE.finditer(original):
                # Walk back up to 200 chars to find the most recent `>`
                window = original[max(0, m.start() - 200):m.start()]
                last_gt = window.rfind(">")
                gap = window[last_gt + 1:] if last_gt >= 0 else window
                gap_clean = _ENTITY_STRIP_RE.sub("", gap).rstrip()
                in_js_ctx = bool(gap_clean) and gap_clean[-1] in _JS_TAIL_OPERATORS
                if in_js_ctx:
                    quote = "'" if m.group(1) == "apos" else '"'
                    new_chunks.append(original[cursor:m.start()])
                    new_chunks.append(f"{quote}{m.group(2)}{quote}")
                    cursor = m.end()
                    changed = True
            if changed:
                new_chunks.append(original[cursor:])
                fixed = "".join(new_chunks)
                try:
                    with open(fpath, "w", encoding="utf-8") as f:
                        f.write(fixed)
                    fixed_files.append(os.path.relpath(fpath, workspace_path))
                except Exception as e:
                    logger.warning("fix_mis_escaped_entities_in_js: write failed %s: %s", fpath, e)

    if fixed_files:
        logger.info(
            "fix_mis_escaped_entities_in_js: reverted JS-context entities in %d file(s)",
            len(fixed_files),
        )
    return fixed_files


# ╔══════════════════════════════════════════════════════════════╗
# ║  FIXER 5c — Decode \uXXXX in JSX text content              ║
# ╚══════════════════════════════════════════════════════════════╝
# Claude's tool-use JSON sometimes emits non-ASCII characters as the literal
# 6-character escape sequence `ʻ` rather than the actual codepoint.
# JSX *expressions* (`{"ʻ"}`) interpret these; JSX *text* (`>koʻp<`)
# does NOT, so the user sees `koʻp` rendered verbatim. This bites every
# non-ASCII language: Uzbek apostrophe (ʻ U+02BB), degree sign (° U+00B0),
# Russian/Arabic/CJK characters, etc.
#
# Fix: walk JSX text nodes only and decode `\uXXXX` → real character. We
# skip the inside of `{…}` expressions because those are valid JS where
# the engine already decodes the escape correctly.

_UNICODE_ESCAPE_RE = re.compile(r"\\u([0-9A-Fa-f]{4})")


def _decode_unicode_escapes_in_jsx_text(jsx_text: str) -> str:
    """Decode `\\uXXXX` → actual char inside a single JSX text node."""
    if "\\u" not in jsx_text:
        return jsx_text

    out: list[str] = []
    i = 0
    depth = 0  # depth of `{ … }` JS-expression nesting within this text node
    while i < len(jsx_text):
        c = jsx_text[i]
        if c == "{":
            depth += 1
            out.append(c)
            i += 1
            continue
        if c == "}":
            depth = max(0, depth - 1)
            out.append(c)
            i += 1
            continue
        if depth == 0 and c == "\\" and i + 5 < len(jsx_text) and jsx_text[i + 1] == "u":
            hexdigits = jsx_text[i + 2 : i + 6]
            try:
                code = int(hexdigits, 16)
                out.append(chr(code))
                i += 6
                continue
            except ValueError:
                pass
        out.append(c)
        i += 1
    return "".join(out)


def fix_unicode_escapes_in_jsx(workspace_path: str) -> list[str]:
    """Replace literal `\\uXXXX` with the real character in JSX text nodes."""
    fixed_files: list[str] = []
    src_dir = os.path.join(workspace_path, "src")
    if not os.path.isdir(src_dir):
        src_dir = workspace_path

    for root, dirs, files in os.walk(src_dir):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for fname in files:
            if os.path.splitext(fname)[1].lower() not in {".jsx", ".tsx"}:
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                    original = f.read()
            except Exception:
                continue

            if "\\u" not in original:
                continue

            def _sub(m: re.Match) -> str:
                inner = m.group(1)
                return ">" + _decode_unicode_escapes_in_jsx_text(inner) + "<"

            fixed = _JSX_TEXT_RE.sub(_sub, original)
            if fixed == original:
                continue

            try:
                with open(fpath, "w", encoding="utf-8") as f:
                    f.write(fixed)
                fixed_files.append(os.path.relpath(fpath, workspace_path))
            except Exception as e:
                logger.warning("fix_unicode_escapes_in_jsx: could not write %s: %s", fpath, e)

    if fixed_files:
        logger.info("Unicode-escape decoder fixed %d file(s)", len(fixed_files))
    return fixed_files


# ╔══════════════════════════════════════════════════════════════╗
# ║  FIXER 6 — <img> → <Image /> (Next.js)                     ║
# ╚══════════════════════════════════════════════════════════════╝

# Locate the start of an <img …> tag. Finding the *end* of the tag is done
# by `_scan_img_tag` below — a regex can't do it safely because attribute
# values can contain `>` (data URLs, `{a > b ? x : y}`, JSX handlers with
# closing braces, etc.) and any `[\s\S]*?(?:/>|>)` form will stop at the
# wrong character.
_IMG_TAG_OPEN_RE = re.compile(r"<img\b", re.IGNORECASE)

_ATTR_KEYS = {"src", "alt", "classname", "class", "width", "height", "style"}


def _parse_jsx_attrs(attrs_str: str) -> dict[str, str]:
    """Hand-rolled JSX attribute parser.

    Why not regex: a regex like ``["\\{][^"}\\n]*["\\}]`` truncates JSX values
    that contain balanced ``{...}`` (e.g. ``style={{color:'red'}}``) or a
    template literal with ``${expr}`` (e.g. ``src={`a${n}`}``) — the inner
    ``}`` fools the regex into closing the value early, which then drops the
    real closing ``}`` and produces an unterminated literal. Walking the
    string with brace/quote/backtick awareness handles all three.

    Returns a dict mapping lowercased attribute name → raw value text
    (including the wrapping ``"..."`` or ``{...}``). Unknown attrs are
    skipped to keep the dict focused on what _render_image_tag consumes.
    """
    attrs: dict[str, str] = {}
    n = len(attrs_str)
    i = 0
    while i < n:
        # Skip whitespace and commas (latter shouldn't appear, but defensive)
        while i < n and attrs_str[i].isspace():
            i += 1
        if i >= n:
            break

        # Read attribute name (alphanumeric, dash, underscore)
        name_start = i
        while i < n and (attrs_str[i].isalnum() or attrs_str[i] in "-_"):
            i += 1
        if i == name_start:
            i += 1  # stray punctuation; skip and continue
            continue
        name = attrs_str[name_start:i].lower()

        # Skip whitespace before '='
        while i < n and attrs_str[i].isspace():
            i += 1

        # Boolean / valueless attribute
        if i >= n or attrs_str[i] != "=":
            if name in _ATTR_KEYS:
                attrs[name] = "true"
            continue
        i += 1  # consume '='
        while i < n and attrs_str[i].isspace():
            i += 1
        if i >= n:
            break

        value_start = i
        ch = attrs_str[i]
        if ch == '"' or ch == "'":
            quote = ch
            i += 1
            while i < n and attrs_str[i] != quote:
                if attrs_str[i] == "\\" and i + 1 < n:
                    i += 2
                else:
                    i += 1
            if i < n:
                i += 1  # consume closing quote
        elif ch == "{":
            # Walk to matching '}', respecting nested braces, strings,
            # and template literals (including their ${...} expressions).
            depth = 1
            i += 1
            while i < n and depth > 0:
                c = attrs_str[i]
                if c == "{":
                    depth += 1
                    i += 1
                elif c == "}":
                    depth -= 1
                    i += 1
                elif c == '"' or c == "'":
                    q = c
                    i += 1
                    while i < n and attrs_str[i] != q:
                        if attrs_str[i] == "\\" and i + 1 < n:
                            i += 2
                        else:
                            i += 1
                    if i < n:
                        i += 1
                elif c == "`":
                    i += 1
                    while i < n and attrs_str[i] != "`":
                        if attrs_str[i] == "\\" and i + 1 < n:
                            i += 2
                        elif attrs_str[i:i + 2] == "${":
                            sub_depth = 1
                            i += 2
                            while i < n and sub_depth > 0:
                                if attrs_str[i] == "{":
                                    sub_depth += 1
                                elif attrs_str[i] == "}":
                                    sub_depth -= 1
                                i += 1
                        else:
                            i += 1
                    if i < n:
                        i += 1  # closing backtick
                else:
                    i += 1
        else:
            # Bare word value (rare in JSX but covered by original regex)
            while i < n and (attrs_str[i].isalnum() or attrs_str[i] in "_-"):
                i += 1

        if name in _ATTR_KEYS:
            attrs[name] = attrs_str[value_start:i]

    # Normalize 'class' → 'classname' (matches the existing _render_image_tag keys)
    if "class" in attrs and "classname" not in attrs:
        attrs["classname"] = attrs.pop("class")
    elif "class" in attrs:
        # Both present — prefer the React-style key, drop the HTML one
        attrs.pop("class", None)
    return attrs


def _scan_img_tag(content: str, start: int) -> int:
    """Return the exclusive end index of the `<img …>` tag that begins at `start`.

    Walks forward respecting JSX `{}` depth, plus `'`/`"`/backtick string
    literals (with backslash escapes and template `${…}` interpolation).
    A `>` is only treated as the tag terminator when we're outside every
    string and at brace-depth 0. Returns -1 if the tag is unterminated.
    """
    n = len(content)
    i = start
    depth = 0
    in_str: Optional[str] = None
    while i < n:
        c = content[i]
        if in_str:
            if c == "\\" and i + 1 < n:
                i += 2
                continue
            if c == in_str:
                in_str = None
            i += 1
            continue
        if c in ("'", '"', "`"):
            in_str = c
            i += 1
            continue
        if c == "{":
            depth += 1
            i += 1
            continue
        if c == "}":
            depth -= 1
            i += 1
            continue
        if c == ">" and depth <= 0:
            return i + 1
        i += 1
    return -1


def _replace_img_tags(content: str) -> str:
    """Find every `<img …>` (or `<img … />`) tag and rewrite to `<Image … />`.

    Uses `_scan_img_tag` for the tag-end so attribute values containing `>`
    (data URLs, JSX comparisons, onError handler bodies) can't fool us.
    """
    out: list[str] = []
    cursor = 0
    for m in _IMG_TAG_OPEN_RE.finditer(content):
        idx = m.start()
        # Make sure this is `<img` followed by a tag-boundary char, not a
        # substring like `<images>`.
        nxt_pos = m.end()
        nxt = content[nxt_pos] if nxt_pos < len(content) else ""
        if nxt and nxt not in (" ", "\t", "\n", "\r", "/", ">"):
            continue
        end = _scan_img_tag(content, m.end())
        if end < 0:
            continue
        # Strip the trailing `>` (and any preceding `/`) to isolate the attrs
        attrs_end = end - 1
        attrs_start = m.end()
        attrs_str = content[attrs_start:attrs_end]
        if attrs_str.rstrip().endswith("/"):
            attrs_str = attrs_str.rstrip()[:-1]
        out.append(content[cursor:idx])
        out.append(_render_image_tag(attrs_str))
        cursor = end
    out.append(content[cursor:])
    return "".join(out)


def _render_image_tag(attrs_str: str) -> str:
    attrs = _parse_jsx_attrs(attrs_str)

    # Build Next.js <Image> props
    parts = []
    if "src" in attrs:
        parts.append(f"src={attrs['src']}")
    if "alt" in attrs:
        parts.append(f"alt={attrs['alt']}")
    else:
        parts.append('alt=""')
    if "width" in attrs and "height" in attrs:
        parts.append(f"width={attrs['width']}")
        parts.append(f"height={attrs['height']}")
    else:
        # No explicit dimensions — use safe defaults (fill requires parent position:relative)
        parts.append("width={800}")
        parts.append("height={600}")
    if "classname" in attrs:
        parts.append(f"className={attrs['classname']}")
    if "style" in attrs:
        parts.append(f"style={attrs['style']}")

    return f"<Image {' '.join(parts)} />"


_NEXT_IMAGE_IMPORT_RE = re.compile(r"^import\s+Image\s+from\s+['\"]next/image['\"]", re.MULTILINE)


def fix_img_tags(workspace_path: str) -> list[str]:
    """Replace <img> with Next.js <Image /> and add the import if missing.

    Fixes @next/next/no-img-element ESLint warnings that fail Vercel builds.
    Only applies to Next.js projects (checks for next.config.* or app/ directory).
    """
    # Only run for Next.js projects
    is_nextjs = (
        os.path.exists(os.path.join(workspace_path, "next.config.mjs"))
        or os.path.exists(os.path.join(workspace_path, "next.config.js"))
        or os.path.isdir(os.path.join(workspace_path, "src", "app"))
    )
    if not is_nextjs:
        return []

    fixed_files = []
    src_dir = os.path.join(workspace_path, "src")
    if not os.path.isdir(src_dir):
        src_dir = workspace_path

    for root, dirs, files in os.walk(src_dir):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for fname in files:
            if os.path.splitext(fname)[1].lower() not in {".jsx", ".tsx"}:
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
            except Exception:
                continue

            if "<img" not in content.lower():
                continue

            new_content = _replace_img_tags(content)
            if new_content == content:
                continue

            # Add next/image import if not already present
            if not _NEXT_IMAGE_IMPORT_RE.search(new_content):
                # Insert after the last existing import line
                import_insert = 'import Image from "next/image";\n'
                last_import = max(
                    (m.end() for m in re.finditer(r"^import\b.*$", new_content, re.MULTILINE)),
                    default=0,
                )
                new_content = new_content[:last_import] + "\n" + import_insert + new_content[last_import:]

            try:
                with open(fpath, "w", encoding="utf-8") as f:
                    f.write(new_content)
                fixed_files.append(os.path.relpath(fpath, workspace_path))
            except Exception as e:
                logger.warning("fix_img_tags: could not write %s: %s", fpath, e)

    if fixed_files:
        logger.info("img→Image fixer fixed %d file(s)", len(fixed_files))
    return fixed_files


# ╔══════════════════════════════════════════════════════════════╗
# ║  FIXER 7 — next.config.js Image Domains Patcher            ║
# ╚══════════════════════════════════════════════════════════════╝

# External image hosts the AI commonly uses in generated projects.
# picsum.photos is the most frequent source of "unconfigured host" build errors.
_IMAGE_HOSTNAMES = [
    "picsum.photos",
    "images.unsplash.com",
    "source.unsplash.com",
    "via.placeholder.com",
    "placehold.co",
    "loremflickr.com",
    "dummyimage.com",
    "randomuser.me",
    "i.pravatar.cc",
    "api.dicebear.com",
    "avatars.githubusercontent.com",
    "lh3.googleusercontent.com",
    "images.pexels.com",
]

_REMOTE_PATTERNS_BLOCK = """
  images: {
    remotePatterns: [
      { protocol: 'https', hostname: 'picsum.photos' },
      { protocol: 'https', hostname: 'images.unsplash.com' },
      { protocol: 'https', hostname: 'source.unsplash.com' },
      { protocol: 'https', hostname: 'via.placeholder.com' },
      { protocol: 'https', hostname: 'placehold.co' },
      { protocol: 'https', hostname: 'loremflickr.com' },
      { protocol: 'https', hostname: 'dummyimage.com' },
      { protocol: 'https', hostname: 'randomuser.me' },
      { protocol: 'https', hostname: 'i.pravatar.cc' },
      { protocol: 'https', hostname: 'api.dicebear.com' },
      { protocol: 'https', hostname: 'avatars.githubusercontent.com' },
      { protocol: 'https', hostname: 'lh3.googleusercontent.com' },
      { protocol: 'https', hostname: 'images.pexels.com' },
    ],
  },"""


def _find_matching_close_brace(content: str, open_pos: int) -> int:
    """Return the index of the closing } that matches content[open_pos] == '{'.

    Returns -1 if no matching brace is found (malformed input).
    content[open_pos] must be '{'.
    """
    depth = 0
    for i in range(open_pos, len(content)):
        ch = content[i]
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return i
    return -1


def fix_next_config_image_domains(workspace_path: str) -> bool:
    """Patch next.config.js / next.config.mjs to allow common external image hosts.

    The AI uses picsum.photos and similar services for placeholder images.
    Without configuring remotePatterns, Next.js Image throws a build error.
    Returns True if the config was patched, False if unchanged or not found.
    """
    for cfg_name in ("next.config.js", "next.config.mjs"):
        cfg_path = os.path.join(workspace_path, cfg_name)
        if not os.path.exists(cfg_path):
            continue

        try:
            with open(cfg_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception as e:
            logger.warning("fix_next_config_image_domains: could not read %s: %s", cfg_path, e)
            continue

        # Already patched — skip
        if "picsum.photos" in content:
            logger.debug("fix_next_config_image_domains: %s already has picsum.photos", cfg_name)
            return False

        patched = False

        # Pattern 1: `const/let/var nextConfig = { ... }`
        obj_match = re.search(r'(?:const|let|var)\s+nextConfig\s*=\s*\{', content)
        if obj_match:
            open_pos = obj_match.end() - 1  # the '{' at end of match
            close_pos = _find_matching_close_brace(content, open_pos)
            if close_pos != -1 and "remotePatterns" not in content[open_pos:close_pos]:
                content = content[:close_pos] + _REMOTE_PATTERNS_BLOCK + "\n" + content[close_pos:]
                patched = True

        # Pattern 2: `module.exports = { ... }` or `export default { ... }`
        if not patched:
            export_match = re.search(
                r'(?:module\.exports\s*=\s*|export\s+default\s+)\{', content
            )
            if export_match:
                open_pos = export_match.end() - 1  # the '{' at end of match
                close_pos = _find_matching_close_brace(content, open_pos)
                if close_pos != -1 and "remotePatterns" not in content[open_pos:close_pos]:
                    content = content[:close_pos] + _REMOTE_PATTERNS_BLOCK + "\n" + content[close_pos:]
                    patched = True

        # Pattern 3: fallback — append a standalone images block export
        if not patched:
            content += (
                "\n// Auto-patched by Lucid AI — allow external image hosts\n"
                f"/** @type {{import('next').NextConfig}} */\n"
                f"module.exports = {{{_REMOTE_PATTERNS_BLOCK}\n}};\n"
            )
            patched = True

        try:
            with open(cfg_path, "w", encoding="utf-8") as f:
                f.write(content)
            logger.info("fix_next_config_image_domains: patched %s with remotePatterns", cfg_name)
            return True
        except Exception as e:
            logger.warning("fix_next_config_image_domains: could not write %s: %s", cfg_path, e)

    return False


# ╔══════════════════════════════════════════════════════════════╗
# ║  FIXER 7b — next.config ESLint / TS ignore-on-build patcher ║
# ╚══════════════════════════════════════════════════════════════╝

# A single missing `key` prop (or any other lint error) should not block a
# Vercel deploy of an AI-generated landing page. We inject Next.js's official
# escape hatches so the build proceeds even when ESLint / tsc complain.
_BUILD_IGNORE_BLOCK = """
  eslint: { ignoreDuringBuilds: true },
  typescript: { ignoreBuildErrors: true },"""


def fix_next_config_build_ignore(workspace_path: str) -> bool:
    """Inject `eslint.ignoreDuringBuilds` + `typescript.ignoreBuildErrors`.

    Mirrors fix_next_config_image_domains. Returns True if the file was
    modified, False if already configured or no config was found.
    """
    for cfg_name in ("next.config.js", "next.config.mjs"):
        cfg_path = os.path.join(workspace_path, cfg_name)
        if not os.path.exists(cfg_path):
            continue

        try:
            with open(cfg_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception as e:
            logger.warning("fix_next_config_build_ignore: could not read %s: %s", cfg_path, e)
            continue

        if "ignoreDuringBuilds" in content and "ignoreBuildErrors" in content:
            return False

        patched = False

        obj_match = re.search(r'(?:const|let|var)\s+nextConfig\s*=\s*\{', content)
        if obj_match:
            open_pos = obj_match.end() - 1
            close_pos = _find_matching_close_brace(content, open_pos)
            if close_pos != -1:
                inside = content[open_pos:close_pos]
                # Build a block that only contains keys not already present
                additions = []
                if "ignoreDuringBuilds" not in inside:
                    additions.append("  eslint: { ignoreDuringBuilds: true },")
                if "ignoreBuildErrors" not in inside:
                    additions.append("  typescript: { ignoreBuildErrors: true },")
                if additions:
                    block = "\n" + "\n".join(additions)
                    content = content[:close_pos] + block + "\n" + content[close_pos:]
                    patched = True

        if not patched:
            export_match = re.search(
                r'(?:module\.exports\s*=\s*|export\s+default\s+)\{', content
            )
            if export_match:
                open_pos = export_match.end() - 1
                close_pos = _find_matching_close_brace(content, open_pos)
                if close_pos != -1:
                    inside = content[open_pos:close_pos]
                    additions = []
                    if "ignoreDuringBuilds" not in inside:
                        additions.append("  eslint: { ignoreDuringBuilds: true },")
                    if "ignoreBuildErrors" not in inside:
                        additions.append("  typescript: { ignoreBuildErrors: true },")
                    if additions:
                        block = "\n" + "\n".join(additions)
                        content = content[:close_pos] + block + "\n" + content[close_pos:]
                        patched = True

        if not patched:
            content += (
                "\n// Auto-patched by Lucid AI — never fail Vercel build on lint/ts\n"
                f"/** @type {{import('next').NextConfig}} */\n"
                f"module.exports = {{{_BUILD_IGNORE_BLOCK}\n}};\n"
            )
            patched = True

        try:
            with open(cfg_path, "w", encoding="utf-8") as f:
                f.write(content)
            logger.info("fix_next_config_build_ignore: patched %s with eslint/ts ignore", cfg_name)
            return True
        except Exception as e:
            logger.warning("fix_next_config_build_ignore: could not write %s: %s", cfg_path, e)

    return False


# ╔══════════════════════════════════════════════════════════════╗
# ║  FIXER — Strip unsupported -q flag from json-server scripts ║
# ╚══════════════════════════════════════════════════════════════╝
#
# json-server v1.x dropped the legacy `-q`/`--quiet` flag. Templates
# that ship `"json-server --watch db.json --port 3001 -q"` in scripts
# crash on `pnpm dev` with `Unknown option '-q'`, and Next.js renders
# a "1 error" badge in the dev overlay even though the page itself is
# fine (HTTP 200). Cosmetic but visible in every screenshot.


def fix_package_json_dev_script(workspace_path: str) -> bool:
    """Strip unsupported `-q`/`--quiet` flag from json-server commands.

    Walks package.json `scripts` block and removes the flag wherever it
    appears in a json-server invocation. Returns True if the file was
    modified.
    """
    pkg_path = os.path.join(workspace_path, "package.json")
    if not os.path.exists(pkg_path):
        return False

    try:
        with open(pkg_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception as e:
        logger.warning("fix_package_json_dev_script: could not read %s: %s", pkg_path, e)
        return False

    if "json-server" not in content or (" -q" not in content and "--quiet" not in content):
        return False

    # Only strip the flag inside lines that mention json-server.
    new_lines = []
    patched = False
    for line in content.splitlines(keepends=True):
        if "json-server" in line and (" -q" in line or "--quiet" in line):
            stripped = re.sub(r"\s+(-q|--quiet)\b", "", line)
            if stripped != line:
                patched = True
                line = stripped
        new_lines.append(line)

    if not patched:
        return False

    try:
        with open(pkg_path, "w", encoding="utf-8") as f:
            f.write("".join(new_lines))
        logger.info("fix_package_json_dev_script: stripped -q from json-server in package.json")
        return True
    except Exception as e:
        logger.warning("fix_package_json_dev_script: could not write %s: %s", pkg_path, e)
        return False


# ╔══════════════════════════════════════════════════════════════╗
# ║  FIXER — Ensure each section has an id matching its filename ║
# ║  so MarketingHeader's anchor nav (#features, #pricing) jumps  ║
# ║  to the right element on the same page.                       ║
# ╚══════════════════════════════════════════════════════════════╝

# Match a section filename and capture the slug part. Examples that match:
#   HeroSection.jsx        → hero
#   OpenRolesSection.jsx   → open_roles
#   PricingSection.tsx     → pricing
#   features.jsx           → features
_SECTION_FILE_RE = re.compile(r"^(?P<slug>[A-Za-z0-9_]+?)(?:Section)?\.(?:jsx|tsx|js|ts)$")


def _filename_to_section_id(filename: str) -> str:
    """Turn `OpenRolesSection.jsx` → `open-roles`.

    Hyphenated kebab-case is what the schema-derived nav anchors use
    (`#open-roles`), so the section id must match that exact slug.
    """
    m = _SECTION_FILE_RE.match(filename)
    base = m.group("slug") if m else os.path.splitext(filename)[0]
    # CamelCase → snake_case → kebab
    snake = re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", base)
    snake = re.sub(r"([A-Z]+)(?=[A-Z][a-z])", r"\1_", snake)
    snake = snake.lower().replace("-", "_")
    snake = re.sub(r"_+", "_", snake).strip("_")
    return snake.replace("_", "-")


# Matches a Next.js `<Link>` whose href reads from a loop variable like
# `link.href`, `item.href`, `nav.href`. Brand `<Link href="/">` and CTA
# `<Link href={cta_href_attr}>` don't match this — those are intentionally
# left as routed Links.
_NAV_LINK_OPEN_RE = re.compile(r"<Link\b([^>]*?\bhref=\{[A-Za-z_]\w*\.href\}[^>]*?)>")

_NAV_ANCHOR_HELPER = (
    "\nfunction NavAnchor({ href, onClick, children, ...rest }) {\n"
    "  const isHash = typeof href === 'string' && href.startsWith('#');\n"
    "  const handleClick = (e) => {\n"
    "    if (typeof onClick === 'function') onClick(e);\n"
    "    if (e.defaultPrevented) return;\n"
    "    if (!isHash) return;\n"
    "    if (typeof document === 'undefined') return;\n"
    "    const id = href.slice(1);\n"
    "    const el = document.getElementById(id);\n"
    "    if (!el) return;\n"
    "    e.preventDefault();\n"
    "    el.scrollIntoView({ behavior: 'smooth', block: 'start' });\n"
    "    if (typeof history !== 'undefined' && history.replaceState) {\n"
    "      history.replaceState(null, '', href);\n"
    "    }\n"
    "  };\n"
    "  if (isHash) {\n"
    "    return <a href={href} onClick={handleClick} {...rest}>{children}</a>;\n"
    "  }\n"
    "  return <Link href={href} onClick={handleClick} {...rest}>{children}</Link>;\n"
    "}\n"
)


def fix_marketing_header_nav_anchors(workspace_path: str) -> bool:
    """Rewrite `<Link href={x.href}>` in MarketingHeader.jsx to use NavAnchor.

    Why: Next.js `<Link>` does NOT trigger native scroll for hash hrefs on the
    same page, so anchor navs silently do nothing once Phase 1's Claude-emitted
    header replaces the deterministic builder's output. The marketing-header
    builder has its own NavAnchor injector keyed on `key={link.href}`, but
    Phase 1 LLM emits `key={item.label}`, `key={nav.id}`, etc., so that
    injector misses. This post-gen pass catches whatever the LLM produced.

    Returns True when the file was patched.
    """
    header_path = os.path.join(
        workspace_path, "src", "components", "layout", "MarketingHeader.jsx"
    )
    if not os.path.isfile(header_path):
        return False
    try:
        with open(header_path, "r", encoding="utf-8", errors="replace") as f:
            src = f.read()
    except Exception as e:
        logger.warning("fix_marketing_header_nav_anchors: cannot read %s: %s", header_path, e)
        return False

    if "NavAnchor" in src:
        return False  # already patched (deterministic builder or earlier pass)
    if not _NAV_LINK_OPEN_RE.search(src):
        return False  # no nav-iteration links to convert

    new_src = src
    # 1. Rewrite each matching <Link …> → <NavAnchor …> and its </Link> close.
    #    We walk left-to-right since regex sub doesn't know about the close tag.
    out: list[str] = []
    i = 0
    while True:
        m = _NAV_LINK_OPEN_RE.search(new_src, i)
        if not m:
            out.append(new_src[i:])
            break
        out.append(new_src[i:m.start()])
        attrs = m.group(1)
        # Find the matching </Link>. Nav-iteration <Link> contents are short
        # (just a label); the next </Link> wins.
        close_idx = new_src.find("</Link>", m.end())
        if close_idx < 0:
            # Malformed source — bail out without partial rewrite.
            out.append(new_src[m.start():])
            break
        body = new_src[m.end():close_idx]
        out.append(f"<NavAnchor{attrs}>{body}</NavAnchor>")
        i = close_idx + len("</Link>")
    new_src = "".join(out)

    if "NavAnchor" not in new_src:
        return False

    # 2. Inject the helper after the last top-level `import …;` line.
    import_re = re.compile(r"^(?:import [^\n]+;\s*\n)+", re.MULTILINE)
    m_imp = import_re.search(new_src)
    if m_imp:
        insert_at = m_imp.end()
        new_src = new_src[:insert_at] + _NAV_ANCHOR_HELPER + new_src[insert_at:]
    else:
        new_src = _NAV_ANCHOR_HELPER + new_src

    try:
        with open(header_path, "w", encoding="utf-8") as f:
            f.write(new_src)
    except Exception as e:
        logger.warning("fix_marketing_header_nav_anchors: cannot write %s: %s", header_path, e)
        return False

    logger.info("fix_marketing_header_nav_anchors: patched MarketingHeader.jsx (NavAnchor injected)")
    return True


def _collect_section_slugs(workspace_path: str) -> list[str]:
    """Return the kebab-case slugs that fix_section_ids will assign.

    Mirrors `_filename_to_section_id` over `src/components/sections/*`.
    Used by the header-anchor audit so hrefs target ids that actually exist.
    """
    sections_dir = os.path.join(workspace_path, "src", "components", "sections")
    if not os.path.isdir(sections_dir):
        return []
    slugs: list[str] = []
    for filename in sorted(os.listdir(sections_dir)):
        full_path = os.path.join(sections_dir, filename)
        if not os.path.isfile(full_path):
            continue
        if not _SECTION_FILE_RE.match(filename):
            continue
        s = _filename_to_section_id(filename)
        if s and s not in slugs:
            slugs.append(s)
    return slugs


_HASH_HREF_RE = re.compile(r'href\s*[:=]\s*\{?\s*[\'"`]#([A-Za-z0-9_\-]+)[\'"`]')


def fix_header_anchor_alignment(workspace_path: str) -> int:
    """Rewrite hash hrefs in MarketingHeader.jsx to match real section ids.

    Phase 1 frequently emits `href="#features"` while the section file is
    `FeatureGridSection.jsx` (id `feature-grid`), or vice-versa, so the
    anchor click scrolls nowhere. This audit reads the section slugs that
    `fix_section_ids` assigns and rewrites each header href to the closest
    match (exact slug, or a slug whose first token matches, or substring).
    Hrefs with no match are left alone.

    Returns the number of href substitutions made.
    """
    header_path = os.path.join(
        workspace_path, "src", "components", "layout", "MarketingHeader.jsx"
    )
    if not os.path.isfile(header_path):
        return 0

    slugs = _collect_section_slugs(workspace_path)
    if not slugs:
        return 0
    slug_set = set(slugs)

    try:
        with open(header_path, "r", encoding="utf-8", errors="replace") as f:
            src = f.read()
    except Exception as e:
        logger.warning("fix_header_anchor_alignment: cannot read %s: %s", header_path, e)
        return 0

    def _best_match(want: str) -> str:
        if want in slug_set:
            return want
        want_l = want.lower()
        want_token = want_l.split("-", 1)[0]
        # First-token equality (e.g. "feature" → "feature-grid")
        for s in slugs:
            if s.split("-", 1)[0] == want_token:
                return s
        # Substring either way (e.g. "open-positions" ↔ "open-roles" wouldn't
        # match here — that's fine, we'd rather not rewrite than guess wrong).
        for s in slugs:
            if want_l in s or s in want_l:
                return s
        return ""

    changes = 0
    out: list[str] = []
    pos = 0
    for m in _HASH_HREF_RE.finditer(src):
        anchor = m.group(1)
        target = _best_match(anchor)
        out.append(src[pos:m.start(1)])
        if target and target != anchor:
            out.append(target)
            changes += 1
        else:
            out.append(anchor)
        pos = m.end(1)
    out.append(src[pos:])

    if changes == 0:
        return 0

    new_src = "".join(out)
    try:
        with open(header_path, "w", encoding="utf-8") as f:
            f.write(new_src)
    except Exception as e:
        logger.warning("fix_header_anchor_alignment: cannot write %s: %s", header_path, e)
        return 0

    logger.info(
        "fix_header_anchor_alignment: rewrote %d hash href(s) to match section ids",
        changes,
    )
    return changes


def fix_section_ids(workspace_path: str) -> list[str]:
    """Inject `id="<slug>"` on the outermost JSX element of each section file.

    Walks `src/components/sections/`, picks the first opening JSX tag inside
    the default-exported component's returned JSX, and adds an `id` attribute
    matching the filename's slug. Skips files that already declare an id on
    the outermost element so we don't double-write.

    Returns the list of relative paths that were patched.
    """
    sections_dir = os.path.join(workspace_path, "src", "components", "sections")
    if not os.path.isdir(sections_dir):
        return []

    patched: list[str] = []
    for filename in sorted(os.listdir(sections_dir)):
        full_path = os.path.join(sections_dir, filename)
        if not os.path.isfile(full_path):
            continue
        if not _SECTION_FILE_RE.match(filename):
            continue

        slug = _filename_to_section_id(filename)
        if not slug:
            continue

        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception as e:
            logger.warning("fix_section_ids: cannot read %s: %s", full_path, e)
            continue

        # Find the FIRST JSX opening tag after the first `return (` or `return <`
        # — that's the outermost element of the component's render tree. We
        # want to add id only there, not to nested <section> tags inside.
        return_match = re.search(r"return\s*(\(|<)", content)
        if not return_match:
            continue
        scan_from = return_match.end() - 1  # position of '(' or '<'

        # If `return (`, the next non-whitespace char should be the JSX opener.
        # If `return <`, scan_from already points at the '<'.
        if content[scan_from] == "(":
            # Skip whitespace/comments to first '<'
            j = scan_from + 1
            while j < len(content) and content[j] in " \t\r\n":
                j += 1
            if j >= len(content) or content[j] != "<":
                continue
            scan_from = j

        # scan_from is now the '<' of the outermost JSX tag.
        # Walk to find tag name + attributes up to the closing '>' or '/>'.
        # Track brace depth so `{...}` expressions don't fool us.
        tag_start = scan_from
        i = tag_start + 1
        # Capture the tag name
        name_start = i
        while i < len(content) and (content[i].isalnum() or content[i] in "._"):
            i += 1
        tag_name = content[name_start:i]
        if not tag_name:
            continue

        # Find end of opening tag, respecting brace depth.
        depth = 0
        end_at = -1
        while i < len(content):
            c = content[i]
            if c == "{":
                depth += 1
            elif c == "}":
                depth = max(0, depth - 1)
            elif c == ">" and depth == 0:
                end_at = i
                break
            i += 1
        if end_at < 0:
            continue

        opening_tag = content[tag_start:end_at + 1]

        # Skip if id already declared on this opening tag.
        if re.search(r"\bid\s*=", opening_tag):
            continue

        # Insert id right after the tag name.
        insert_pos = tag_start + 1 + len(tag_name)
        new_content = (
            content[:insert_pos]
            + f' id="{slug}"'
            + content[insert_pos:]
        )

        try:
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(new_content)
            patched.append(os.path.relpath(full_path, workspace_path))
        except Exception as e:
            logger.warning("fix_section_ids: cannot write %s: %s", full_path, e)
            continue

    if patched:
        logger.info("fix_section_ids: injected id on %d section file(s)", len(patched))
    return patched


# ╔══════════════════════════════════════════════════════════════╗
# ║  FIXER — Inject Next.js App Router error boundaries         ║
# ╚══════════════════════════════════════════════════════════════╝

_ERROR_JS = '''\
'use client'
import { useEffect } from 'react'

export default function Error({ error, reset }) {
  useEffect(() => { console.error(error) }, [error])
  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center gap-4 p-8 text-center">
      <div className="rounded-full bg-destructive/10 p-4">
        <svg className="h-8 w-8 text-destructive" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
            d="M12 9v2m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" />
        </svg>
      </div>
      <h2 className="text-xl font-semibold">Something went wrong</h2>
      <p className="max-w-sm text-sm text-muted-foreground">
        An unexpected error occurred. Try refreshing — if the problem persists, contact support.
      </p>
      <button
        onClick={() => reset()}
        className="rounded-lg bg-primary px-5 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 transition-colors"
      >
        Try again
      </button>
    </div>
  )
}
'''

_GLOBAL_ERROR_JS = '''\
'use client'
export default function GlobalError({ error, reset }) {
  return (
    <html>
      <body style={{ margin: 0, fontFamily: 'system-ui, sans-serif', background: '#fafafa' }}>
        <div style={{ display: 'flex', minHeight: '100vh', flexDirection: 'column',
                      alignItems: 'center', justifyContent: 'center', gap: '1rem', padding: '2rem', textAlign: 'center' }}>
          <h2 style={{ fontSize: '1.25rem', fontWeight: 600, margin: 0 }}>Something went wrong</h2>
          <p style={{ color: '#666', fontSize: '0.875rem', margin: 0 }}>
            A critical error occurred. Please try again.
          </p>
          <button
            onClick={() => reset()}
            style={{ background: '#171717', color: '#fff', border: 'none', borderRadius: '8px',
                     padding: '0.5rem 1.25rem', fontSize: '0.875rem', cursor: 'pointer' }}
          >
            Try again
          </button>
        </div>
      </body>
    </html>
  )
}
'''

_NOT_FOUND_JS = '''\
import Link from 'next/link'

export default function NotFound() {
  return (
    <div className="flex min-h-[70vh] flex-col items-center justify-center gap-4 p-8 text-center">
      <p className="text-6xl font-black text-muted-foreground/30">404</p>
      <h2 className="text-xl font-semibold">Page not found</h2>
      <p className="max-w-sm text-sm text-muted-foreground">
        The page you&apos;re looking for doesn&apos;t exist or has been moved.
      </p>
      <Link
        href="/"
        className="rounded-lg bg-primary px-5 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 transition-colors"
      >
        Go home
      </Link>
    </div>
  )
}
'''


def inject_error_boundaries(workspace_path: str) -> list[str]:
    """Inject Next.js App Router error boundary files if they don't already exist.

    error.js      — catches segment-level render errors, shows try-again UI
    global-error.js — catches root layout crashes (last resort fallback)
    not-found.js  — handles 404s with a graceful page instead of blank screen

    Without these, ANY runtime error (undefined component, bad import) shows a
    completely blank white page in production with no recovery path.

    Returns list of files written.
    """
    app_dir = os.path.join(workspace_path, "src", "app")
    if not os.path.isdir(app_dir):
        app_dir = os.path.join(workspace_path, "app")
    if not os.path.isdir(app_dir):
        return []

    written = []
    for filename, content in [
        ("error.js", _ERROR_JS),
        ("global-error.js", _GLOBAL_ERROR_JS),
        ("not-found.js", _NOT_FOUND_JS),
    ]:
        dest = os.path.join(app_dir, filename)
        if not os.path.exists(dest):
            try:
                with open(dest, "w", encoding="utf-8") as f:
                    f.write(content)
                written.append(filename)
            except Exception as exc:
                logger.warning("inject_error_boundaries: failed to write %s: %s", filename, exc)
    return written


# ╔══════════════════════════════════════════════════════════════╗
# ║  FIXER — Restore template UI components from git            ║
# ╚══════════════════════════════════════════════════════════════╝

def restore_template_ui_files(workspace_path: str) -> bool:
    """Restore src/components/ui/ to git HEAD (original template state).

    Claude sometimes overwrites shadcn/ui files with broken implementations
    despite the PROTECTED_UI_DIR write-filter.  This is the nuclear option:
    after all generation phases complete, hard-reset ui/ back to the cloned
    template so any corrupted file (parse error, wrong import) is undone.

    Returns True if git checkout succeeded, False otherwise.
    """
    ui_rel = os.path.join("src", "components", "ui")
    ui_abs = os.path.join(workspace_path, ui_rel)
    if not os.path.isdir(ui_abs):
        return False

    try:
        result = subprocess.run(
            ["git", "checkout", "HEAD", "--", ui_rel],
            cwd=workspace_path,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode == 0:
            logger.info("restore_template_ui_files: restored %s from git HEAD", ui_rel)
            return True
        else:
            logger.warning(
                "restore_template_ui_files: git checkout failed (rc=%d): %s",
                result.returncode, result.stderr.strip()[:200],
            )
            return False
    except Exception as exc:
        logger.warning("restore_template_ui_files: error: %s", exc)
        return False


# ╔══════════════════════════════════════════════════════════════╗
# ║  FIXER — Missing Tailwind directives in globals.css         ║
# ╚══════════════════════════════════════════════════════════════╝

_TAILWIND_CSS_CANDIDATES = (
    "src/app/globals.css",
    "src/styles/globals.css",
    "src/styles/global.css",
    "src/index.css",
    "src/main.css",
    "app/globals.css",
)

_TAILWIND_PREAMBLE = (
    '@tailwind base;\n'
    '@tailwind components;\n'
    '@tailwind utilities;\n'
    '@import "tw-animate-css";\n'
)


# Attribute value wrapped in HTML entities: className=&quot;...&quot; or onClick=&apos;...&apos;
# These are output by Claude when it over-generalizes the "escape quotes" rule to
# attribute values, producing JSX the SWC parser cannot read. Safe to unescape
# because real attribute values only ever use plain " or ' as delimiters.
_ATTR_ENTITY_QUOT_RE = re.compile(r'(\b[a-zA-Z_][\w:-]*)=&quot;(.*?)&quot;', re.DOTALL)
_ATTR_ENTITY_APOS_RE = re.compile(r"(\b[a-zA-Z_][\w:-]*)=&apos;(.*?)&apos;", re.DOTALL)

# Tag-aware fallback. Matches a single JSX opening tag `<Elem ... >` or `<Elem ... />`.
# Inside such a tag, every &quot;/&apos; is an attribute delimiter (text-node entities
# live between > and <, never inside). This catches unpaired cases the regexes above
# cannot: `className=&quot;foo"`, `className="foo&quot;`, or a bare `className=&quot;foo`
# with a missing closer — any of which leave an unterminated string the SWC parser
# reports as "Unexpected eof". The character class excludes `<` and `>` so the match
# stops at the first closing `>` instead of spanning nested JSX.
_JSX_OPENING_TAG_RE = re.compile(r'<[A-Za-z][\w.:-]*[^<>]*?/?>', re.DOTALL)


def _unescape_jsx_tag_entities(source: str) -> str:
    """Replace &quot;/&apos; with literal quotes inside every JSX opening tag.

    Text between tags is left untouched. Returns the transformed source.
    """
    def _sub(match: "re.Match[str]") -> str:
        tag = match.group(0)
        if "&quot;" not in tag and "&apos;" not in tag:
            return tag
        return tag.replace("&quot;", '"').replace("&apos;", "'")

    return _JSX_OPENING_TAG_RE.sub(_sub, source)


def fix_html_entities_in_attributes(workspace_path: str) -> list[str]:
    """Unescape &quot; / &apos; that appear as JSX attribute-value delimiters.

    Claude occasionally writes `className=&quot;flex gap-2&quot;` instead of
    `className="flex gap-2"`, which the JSX/SWC parser rejects with
    "Expression expected". Balanced pairs are fixed by a direct regex; unpaired
    / mixed delimiter cases are caught by a second tag-aware pass that
    unescapes every entity inside `<Elem ... >` opening tags. Text-node
    entities (between > and <) are left untouched because they are a
    legitimate JSX escape.

    Returns the list of file paths that were patched.
    """
    fixed_files: list[str] = []
    src_dir = os.path.join(workspace_path, "src")
    if not os.path.isdir(src_dir):
        src_dir = workspace_path

    for root, dirs, files in os.walk(src_dir):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for fname in files:
            if os.path.splitext(fname)[1].lower() not in {".jsx", ".tsx", ".js", ".ts"}:
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                    original = f.read()
            except Exception:
                continue

            if "&quot;" not in original and "&apos;" not in original:
                continue

            # Pass 1 — balanced pairs (fast, precise, preserves the attr name in group 1).
            fixed = _ATTR_ENTITY_QUOT_RE.sub(r'\1="\2"', original)
            fixed = _ATTR_ENTITY_APOS_RE.sub(r"\1='\2'", fixed)

            # Pass 2 — tag-aware sweep for any stragglers (unpaired or mixed
            # delimiters). Only touches content inside JSX opening tags, so
            # `<p>Don&apos;t</p>` text-node entities are preserved.
            fixed = _unescape_jsx_tag_entities(fixed)

            if fixed != original:
                try:
                    with open(fpath, "w", encoding="utf-8") as f:
                        f.write(fixed)
                    fixed_files.append(os.path.relpath(fpath, workspace_path))
                    logger.warning(
                        "fix_html_entities_in_attributes: unescaped attribute delimiters in %s",
                        os.path.relpath(fpath, workspace_path),
                    )
                except Exception as exc:
                    logger.warning("fix_html_entities_in_attributes: write failed for %s: %s", fpath, exc)

    if fixed_files:
        logger.info("HTML-entity-in-attributes fixer fixed %d file(s)", len(fixed_files))
    return fixed_files


def fix_missing_tailwind_directives(workspace_path: str) -> list[str]:
    """Prepend missing @tailwind directives to globals.css / global.css.

    When Claude rewrites the theme CSS it sometimes drops the @tailwind
    base/components/utilities directives at the top of the file, which
    silently disables every Tailwind class across the whole app (site
    renders unstyled). This fixer is deterministic: it inspects each
    known CSS entry-point and prepends the directives only when they
    are missing. Files that already contain all three directives are
    left untouched.

    Returns the list of file paths that were patched.
    """
    patched: list[str] = []

    for rel_path in _TAILWIND_CSS_CANDIDATES:
        abs_path = os.path.join(workspace_path, rel_path)
        if not os.path.isfile(abs_path):
            continue

        try:
            with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception as exc:
            logger.debug("fix_missing_tailwind_directives: read failed for %s: %s", rel_path, exc)
            continue

        # Detect all three directives (order/whitespace tolerant).
        has_base = bool(re.search(r'^\s*@tailwind\s+base\s*;', content, re.MULTILINE))
        has_comp = bool(re.search(r'^\s*@tailwind\s+components\s*;', content, re.MULTILINE))
        has_util = bool(re.search(r'^\s*@tailwind\s+utilities\s*;', content, re.MULTILINE))

        if has_base and has_comp and has_util:
            continue

        # Rebuild: strip any partial directives + tw-animate-css import, then
        # prepend the canonical 4-line preamble.
        cleaned = re.sub(
            r'^\s*@tailwind\s+(base|components|utilities)\s*;\s*\n',
            "",
            content,
            flags=re.MULTILINE,
        )
        cleaned = re.sub(
            r'^\s*@import\s+["\']tw-animate-css["\']\s*;\s*\n',
            "",
            cleaned,
            flags=re.MULTILINE,
        )
        new_content = _TAILWIND_PREAMBLE + cleaned.lstrip("\n")

        try:
            with open(abs_path, "w", encoding="utf-8") as f:
                f.write(new_content)
            patched.append(rel_path)
            logger.warning(
                "fix_missing_tailwind_directives: restored @tailwind directives in %s",
                rel_path,
            )
        except Exception as exc:
            logger.warning("fix_missing_tailwind_directives: write failed for %s: %s", rel_path, exc)

    return patched


# ╔══════════════════════════════════════════════════════════════╗
# ║  FIXER — Visibility / UX guards                            ║
# ║    • dark overlay on hero <Image fill /> backgrounds       ║
# ║    • z-50 on absolute/top-full dropdown panels             ║
# ╚══════════════════════════════════════════════════════════════╝

# A full-bleed hero image: <Image ... fill ... />.
_HERO_IMAGE_FILL_RE = re.compile(r'<Image\b[^>]*\bfill\b[^>]*?/>', re.DOTALL)

# A darkening overlay anywhere in the file. Any of these means a hero photo
# already has its contrast guard, so we don't add a second one.
_DARK_OVERLAY_RE = re.compile(
    r'bg-black/[1-9]\d?\b'
    r'|bg-gradient-to-[a-z]+\s+from-(?:black|slate-(?:8|9)\d\d|zinc-(?:8|9)\d\d|neutral-(?:8|9)\d\d|gray-(?:8|9)\d\d|stone-(?:8|9)\d\d)'
    r'|via-black/[1-9]\d?\b'
    r'|to-black/[1-9]\d?\b'
)

# Tailwind text colors that go invisible on a busy stock photo unless an
# overlay sits between text and image.
_LOW_CONTRAST_TEXT_RE = re.compile(
    r'text-(?:white|foreground)/(?:[3-7]0|35|45|55|65|75|80|85)\b'
    r'|text-muted-foreground\b'
)

_HERO_OVERLAY_SNIPPET = (
    '<div aria-hidden="true" className="pointer-events-none absolute inset-0 '
    'bg-gradient-to-b from-black/40 via-black/40 to-black/70" />'
)


def _next_nonspace_char(s: str, idx: int) -> str:
    while idx < len(s) and s[idx] in " \t\r\n":
        idx += 1
    return s[idx] if idx < len(s) else ""


def fix_low_contrast_text_on_image(workspace_path: str) -> list[str]:
    """Inject a dark gradient overlay over hero <Image fill /> backgrounds
    when the surrounding text relies on low-contrast classes without one.

    Without an overlay, light/muted text (text-white/60, text-muted-foreground)
    over busy photos becomes unreadable. The fixer is conservative: it only
    fires when ALL three are true:
      • file contains an <Image ... fill ... /> (full-bleed pattern)
      • file contains at least one low-contrast text class
      • file contains no existing dark overlay

    JSX-safety: when <Image> is the lone element returned from a ternary
    branch (`cond ? (<Image .../>) : (<Fallback/>)`) or a JSX expression
    (`{<Image .../>}`), inserting the overlay as a raw sibling produces
    SWC "Expected ',', got '<...'" — JSX expressions can return only one
    element. Detect that context by peeking at the next non-whitespace
    char after the Image and wrap both in a fragment when needed.
    """
    src_dir = os.path.join(workspace_path, "src")
    if not os.path.isdir(src_dir):
        src_dir = workspace_path

    fixed: list[str] = []

    for root, dirs, files in os.walk(src_dir):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for fname in files:
            if os.path.splitext(fname)[1].lower() not in {".jsx", ".tsx"}:
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
            except Exception:
                continue

            m = _HERO_IMAGE_FILL_RE.search(content)
            if not m:
                continue
            if not _LOW_CONTRAST_TEXT_RE.search(content):
                continue
            if _DARK_OVERLAY_RE.search(content):
                continue

            # Skip if the matched <Image> carries a `key=` prop — wrapping it
            # in a shorthand fragment would silently drop the React key.
            if re.search(r'\bkey\s*=', m.group(0)):
                continue

            image_start = m.start()
            image_end = m.end()
            image_text = m.group(0)

            # Peek at the next non-whitespace char after </>. `)` or `}`
            # means the Image is the lone child of a JSX expression — must
            # wrap in fragment. Anything else (a tag `<`, a closing tag
            # `</`, alphanumeric text) means siblings are already legal.
            next_char = _next_nonspace_char(content, image_end)
            needs_fragment = next_char in (")", "}")

            if needs_fragment:
                replacement = (
                    "<>"
                    + image_text
                    + "\n      "
                    + _HERO_OVERLAY_SNIPPET
                    + "</>"
                )
                new_content = (
                    content[:image_start]
                    + replacement
                    + content[image_end:]
                )
            else:
                new_content = (
                    content[:image_end]
                    + "\n      "
                    + _HERO_OVERLAY_SNIPPET
                    + content[image_end:]
                )

            try:
                with open(fpath, "w", encoding="utf-8") as f:
                    f.write(new_content)
                fixed.append(os.path.relpath(fpath, workspace_path))
                logger.info(
                    "fix_low_contrast_text_on_image: added hero overlay in %s%s",
                    os.path.relpath(fpath, workspace_path),
                    " (fragment-wrapped)" if needs_fragment else "",
                )
            except Exception as exc:
                logger.warning("fix_low_contrast_text_on_image: write failed: %s", exc)

    return fixed


# Dropdown / autocomplete panels are typically <div className="absolute top-full ...">.
# When codegen forgets the z-index they render *behind* sibling buttons that
# carry their own stacking context (shadow, transform, etc.). The fix:
# add z-50 if both `absolute` and `top-full` are present and no z-* class is.
_CLASSNAME_LITERAL_RE = re.compile(r'className="([^"\n]*)"')


# Indicators that an absolute-positioned element is a dropdown panel
# (open menu, listbox, suggestions, calendar popover) — at least one of
# these must be in the className for the fixer to bump z-index.
_DROPDOWN_HINTS = (
    "top-full",       # classic dropdown anchor below the trigger
    "mt-2",           # short gap below button (very common)
    "mt-1",           # tight gap below button
    "left-0",         # full-width below
    "right-0",        # right-aligned below
    "inset-x-0",      # spans the trigger width
    "min-w-",         # menu width hint
    "shadow-lg",      # popover surface
    "shadow-xl",
    "rounded-md",     # menu surface (paired with absolute)
    "rounded-lg",
    "rounded-xl",
)


def _classname_needs_dropdown_zindex(class_str: str) -> bool:
    classes = class_str.split()
    if "absolute" not in classes:
        return False
    # Skip elements that are clearly not dropdowns (full-bleed overlays,
    # decorative blobs, hero badges). These have positioning hints but
    # belong to a different stacking context.
    if any(c in classes for c in ("inset-0", "-z-10", "pointer-events-none")):
        return False
    # Already has any z-* class — but check if it's high enough. z-10/z-20
    # lose to sibling form CTAs that get their own stacking context from
    # `transform`, `shadow-lg`, etc. Force z-[80] minimum.
    existing_z = [c for c in classes if c.startswith("z-")]
    if existing_z:
        # If z-50 or higher is already there, leave it alone. Otherwise upgrade.
        for z_class in existing_z:
            # z-50 / z-[80] / z-[100] etc. → keep
            tier = z_class[2:]  # strip "z-"
            if tier.startswith("[") and tier.endswith("]"):
                try:
                    if int(tier[1:-1]) >= 50:
                        return False
                except ValueError:
                    return False
            elif tier in ("50", "40"):
                return True  # z-50 still loses to floating hero cards / backdrop-blur stacking contexts → bump to z-[80]
            elif tier in ("auto",):
                return False
            else:
                # z-10, z-20, z-30 — definitely too low → bump
                pass
        # Existing z-* is too low → bump (caller will replace it)
        return True
    # No z class yet — require at least one dropdown hint so we don't
    # accidentally bump decorative absolute elements.
    return any(hint in class_str for hint in _DROPDOWN_HINTS)


def fix_dropdown_zindex(workspace_path: str) -> list[str]:
    """Add `z-50` to absolute-positioned dropdown panels missing a z-utility.

    Search-filter dropdowns, custom selects, and autocomplete panels render
    behind hero CTAs, floating cards, and overlay badges when no z-index is
    set. This fixer is additive — it never touches className blocks that
    already declare a z-* class, and it skips decorative absolute elements
    (inset-0 overlays, -z-10 blobs).

    Detection: `absolute` + at least one dropdown hint (top-full, mt-2,
    left-0, min-w-*, shadow-lg, rounded-md, etc.). This catches custom
    selects that don't anchor with `top-full` — the dominant pattern in
    generated forms.

    Only matches simple double-quoted className literals; cn(...) expressions
    are left untouched (codegen rarely splits dropdown classes that way).
    """
    src_dir = os.path.join(workspace_path, "src")
    if not os.path.isdir(src_dir):
        src_dir = workspace_path

    fixed: list[str] = []

    for root, dirs, files in os.walk(src_dir):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for fname in files:
            if os.path.splitext(fname)[1].lower() not in {".jsx", ".tsx"}:
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
            except Exception:
                continue

            if "absolute" not in content:
                continue

            def _replace(m: re.Match) -> str:
                inner = m.group(1)
                if not _classname_needs_dropdown_zindex(inner):
                    return m.group(0)
                # Bump to z-[80] so it wins against sibling form CTAs and
                # any z-50 hero overlay badges. Strip any existing low z-*
                # so we don't end up with "z-10 z-[80]".
                cleaned = " ".join(
                    c for c in inner.split()
                    if not (c.startswith("z-") and not (
                        c.startswith("z-[") and
                        c.endswith("]") and
                        c[2:-1].isdigit() and
                        int(c[2:-1]) >= 60
                    ))
                )
                return f'className="{cleaned} z-[80]"'

            new_content = _CLASSNAME_LITERAL_RE.sub(_replace, content)

            if new_content == content:
                continue

            try:
                with open(fpath, "w", encoding="utf-8") as f:
                    f.write(new_content)
                fixed.append(os.path.relpath(fpath, workspace_path))
                logger.info(
                    "fix_dropdown_zindex: added z-50 to dropdown in %s",
                    os.path.relpath(fpath, workspace_path),
                )
            except Exception as exc:
                logger.warning("fix_dropdown_zindex: write failed: %s", exc)

    return fixed


# ╔══════════════════════════════════════════════════════════════╗
# ║  FIXER — Forced card heights (min-h-[*])                   ║
# ║    Strips min-h-[300px] / min-h-[400px] / etc. from card   ║
# ║    containers. Forced heights create the #1 visual bug:    ║
# ║    rivers of empty space inside cards that didn't fill.    ║
# ║    Whitelist hero <section> and aspect-ratio containers.   ║
# ╚══════════════════════════════════════════════════════════════╝

# Matches `min-h-[<value>]` and `h-[<value>px]` where <value> is e.g. 300px,
# 400px, 480px, 32rem, etc. — anywhere inside a className string.
_FORCED_HEIGHT_RE = re.compile(
    r"\bmin-h-\[(?:[1-9]\d{2,}px|\d+(?:\.\d+)?(?:rem|em|vh)|[1-9]\d?xl)\](?:\s|$)"
)
_FORCED_FIXED_HEIGHT_RE = re.compile(
    r"\bh-\[(?:[3-9]\d{2,}|\d{4,})px\](?:\s|$)"
)
# Skip elements that legitimately need a fixed height — true hero sections
# (the OUTER <section>) and aspect-ratio image containers.
# `relative isolate` is the canonical hero outer marker — the codegen prompt
# mandates it on hero <section>, and basically no card uses it. Treating it as
# a whitelist hint preserves the hero's `min-h-[640px]` floor even when the
# pixel-pattern regex would otherwise strip it. Same goes for `min-h-screen`
# / `min-h-[100svh]` — the new hero spec uses those for full-viewport behavior.
_HEIGHT_OK_HINTS = (
    "aspect-[",
    "aspect-square",
    "aspect-video",
    "<section ",
    "relative isolate",  # hero outer marker
    "min-h-screen",
    "min-h-[100svh]",
    "min-h-[100dvh]",
    "min-h-[100lvh]",
)


def fix_forced_card_heights(workspace_path: str) -> list[str]:
    """Strip `min-h-[300px+]` and `h-[300px+]` from non-section card containers.

    Forced heights inside a card produce empty rectangles whenever content
    density is lower than the model assumed (item list shorter than 3,
    description string shorter than expected, etc.). The fix: let cards
    grow to fit content. Outer <section> tags and aspect-ratio containers
    keep their heights.

    Only modifies double-quoted className literals so cn(...) chains are
    left intact. Additive removal — never inserts new classes.
    """
    src_dir = os.path.join(workspace_path, "src")
    if not os.path.isdir(src_dir):
        src_dir = workspace_path

    fixed: list[str] = []

    for root, dirs, files in os.walk(src_dir):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for fname in files:
            if os.path.splitext(fname)[1].lower() not in {".jsx", ".tsx"}:
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
            except Exception:
                continue

            if "min-h-[" not in content and "h-[" not in content:
                continue

            def _replace(m: re.Match) -> str:
                inner = m.group(1)
                # Skip section outer + aspect containers
                if any(hint in inner for hint in _HEIGHT_OK_HINTS):
                    return m.group(0)
                cleaned = _FORCED_HEIGHT_RE.sub("", inner)
                cleaned = _FORCED_FIXED_HEIGHT_RE.sub("", cleaned)
                # Collapse double spaces left behind
                cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
                if cleaned == inner:
                    return m.group(0)
                return f'className="{cleaned}"'

            new_content = _CLASSNAME_LITERAL_RE.sub(_replace, content)

            # Also scan immediate parent line for the same <section> guard:
            # a card just inside `<section>` is what we're targeting, not the
            # section itself. The hint check above handles `<section >` tags
            # because the className string of a section won't contain
            # `<section ` literal. For purely card-level uses, the strip
            # proceeds. (Hero section's outer min-h is on the <section> tag
            # itself, with the className typically including `relative isolate`
            # — leave it alone; the regex above only strips ≥100px values
            # from CARD-class strings, and hero outer is whitelisted by the
            # presence of `<section ` in its surrounding code.)

            if new_content == content:
                continue

            try:
                with open(fpath, "w", encoding="utf-8") as f:
                    f.write(new_content)
                fixed.append(os.path.relpath(fpath, workspace_path))
                logger.info(
                    "fix_forced_card_heights: stripped fixed-height utilities in %s",
                    os.path.relpath(fpath, workspace_path),
                )
            except Exception as exc:
                logger.warning("fix_forced_card_heights: write failed: %s", exc)

    return fixed


# ╔══════════════════════════════════════════════════════════════╗
# ║  FIXER — Invalid Lucide imports                            ║
# ║    Drops kebab/snake-case names that produce invalid JS    ║
# ║    identifiers and would abort the build before SWC runs.  ║
# ╚══════════════════════════════════════════════════════════════╝

_INVALID_IDENT_RE = re.compile(r"[^A-Za-z0-9_]")


def _is_valid_js_ident(name: str) -> bool:
    return bool(name) and not _INVALID_IDENT_RE.search(name) and not name[0].isdigit()


def fix_invalid_lucide_imports(workspace_path: str) -> list[str]:
    """Drop any kebab/snake-case names from `import {…} from "lucide-react"`.

    `import { shopping-cart } from "lucide-react"` is a JS syntax error before
    SWC even reaches JSX parsing. The brief layer already PascalCases icons,
    but a post-gen fixer caught here doubles as insurance against any
    Claude-emitted import that bypassed the brief contract.
    """
    src_dir = os.path.join(workspace_path, "src")
    if not os.path.isdir(src_dir):
        src_dir = workspace_path

    fixed: list[str] = []

    for root, dirs, files in os.walk(src_dir):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for fname in files:
            if os.path.splitext(fname)[1].lower() not in _JSX_EXTENSIONS:
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
            except Exception:
                continue

            if "lucide-react" not in content:
                continue

            changed = False

            def _scrub(match: re.Match) -> str:
                nonlocal changed
                names_str = match.group(1)
                names = [n.strip() for n in names_str.split(",") if n.strip()]
                kept: list[str] = []
                for n in names:
                    head = n.split(" as ")[0].strip()
                    alias = n.split(" as ")[-1].strip()
                    if _is_valid_js_ident(head) and _is_valid_js_ident(alias):
                        kept.append(n)
                    else:
                        changed = True
                if not kept:
                    return ""  # remove whole import line
                return f'import {{ {", ".join(kept)} }} from "lucide-react"'

            new_content = _LUCIDE_IMPORT_RE.sub(_scrub, content)

            if not changed:
                continue

            try:
                with open(fpath, "w", encoding="utf-8") as f:
                    f.write(new_content)
                fixed.append(os.path.relpath(fpath, workspace_path))
                logger.info(
                    "fix_invalid_lucide_imports: scrubbed invalid identifiers in %s",
                    os.path.relpath(fpath, workspace_path),
                )
            except Exception as exc:
                logger.warning("fix_invalid_lucide_imports: write failed: %s", exc)

    return fixed


# ╔══════════════════════════════════════════════════════════════╗
# ║  FIXER — Hardcoded UNSPLASH_IMAGES dict                    ║
# ║    Strips local `const UNSPLASH_IMAGES = {…}` hardcodes    ║
# ║    so components fall back to the runtime landing.json     ║
# ║    (and the gradient placeholder when a slot is empty).    ║
# ╚══════════════════════════════════════════════════════════════╝

_HARDCODED_IMG_DICT_RE = re.compile(
    r"^[\t ]*const\s+(?:UNSPLASH_IMAGES|IMAGE_MAP|IMAGES_BY_KEY|HERO_IMG|HERO_IMAGES)\s*=\s*[\{\"]"
    r".*?"
    r"(?:\}\s*;?|\"\s*;?)\s*$",
    re.MULTILINE | re.DOTALL,
)


def fix_hardcoded_unsplash_dicts(workspace_path: str) -> list[str]:
    """Strip locally-hardcoded image URL dicts/strings from section files.

    Past failure: Claude defensively emits `const UNSPLASH_IMAGES = {peking_duck:
    "https://images.unsplash.com/..."}` when section.images appears empty in the
    brief snapshot. Those URLs go stale and 404. Stripping them forces the
    component to fall back to `section.images?.[i]` (which the binder fills at
    runtime) and the gradient placeholder when that's empty.
    """
    src_dir = os.path.join(workspace_path, "src")
    if not os.path.isdir(src_dir):
        src_dir = workspace_path

    fixed: list[str] = []

    for root, dirs, files in os.walk(src_dir):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for fname in files:
            if os.path.splitext(fname)[1].lower() not in _JSX_EXTENSIONS:
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
            except Exception:
                continue

            if "images.unsplash.com" not in content:
                continue
            if not _HARDCODED_IMG_DICT_RE.search(content):
                continue

            new_content = _HARDCODED_IMG_DICT_RE.sub("", content)
            if new_content == content:
                continue

            try:
                with open(fpath, "w", encoding="utf-8") as f:
                    f.write(new_content)
                fixed.append(os.path.relpath(fpath, workspace_path))
                logger.info(
                    "fix_hardcoded_unsplash_dicts: stripped hardcoded URL dict from %s",
                    os.path.relpath(fpath, workspace_path),
                )
            except Exception as exc:
                logger.warning("fix_hardcoded_unsplash_dicts: write failed: %s", exc)

    return fixed


# ╔══════════════════════════════════════════════════════════════╗
# ║  FIXER — Design-token drift                                ║
# ║    Validates card/button/image classNames carry the brief's║
# ║    radius token; logs telemetry; auto-splices when missing ║
# ╚══════════════════════════════════════════════════════════════╝

_BUTTON_TAG_RE = re.compile(r'<button\b([^>]*)>', re.IGNORECASE)
_INPUT_TAG_RE = re.compile(r'<(?:input|textarea|select)\b([^>]*?)/?>')
_CARD_DIV_RE = re.compile(
    r'<div\b([^>]*\bclassName="[^"]*\bbg-card\b[^"]*"[^>]*)>',
)
_HARDCODED_HEX_IN_CLASS_RE = re.compile(
    r'\b(?:bg|text|from|to|via|border|ring|fill|stroke|shadow|outline|caret|accent|decoration|divide|placeholder)-\[#[0-9A-Fa-f]{3,8}\]'
)
_HARDCODED_RGB_IN_CLASS_RE = re.compile(
    r'\b(?:bg|text|from|to|via|border|ring|fill|stroke|shadow|outline|caret|accent|decoration|divide|placeholder)-\[(?:rgba?|hsla?)\([^)]+\)\]'
)
_TAILWIND_PALETTE_NUMBERED_RE = re.compile(
    r'\b(?:bg|text|from|to|via|border|ring|fill|stroke|shadow|outline|caret|accent|decoration|divide|placeholder)-(?:slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)-\d{2,3}\b'
)


def _has_radius_class(class_str: str) -> bool:
    return bool(re.search(r'\brounded(?:-[a-z0-9\[\]/_%.\-]+)?\b', class_str))


def _extract_classname_value(attrs: str) -> tuple[str, int, int] | None:
    m = re.search(r'className\s*=\s*"([^"]*)"', attrs)
    if not m:
        return None
    return m.group(1), m.start(1), m.end(1)


def _splice_class(attrs_block: str, addition: str) -> str:
    """Append `addition` to the className literal of attrs_block (if any).
    If no className present, leave attrs_block alone — caller decides.
    """
    extracted = _extract_classname_value(attrs_block)
    if not extracted:
        return attrs_block
    cur, s, e = extracted
    new_value = (cur + " " + addition).strip() if cur else addition
    return attrs_block[:s] + new_value + attrs_block[e:]


def fix_design_token_drift(
    workspace_path: str,
    design_tokens: dict | None = None,
) -> dict[str, int]:
    """Verify generated section files use the brief's radius tokens.

    Reads `design_tokens.{button_radius_class, card_radius_class,
    image_radius_class}` (passed in or read from landing.json) and:
      • Splices the radius class into <button>, <Card>-shaped <div>, and
        <Image>-wrapper divs that don't already carry a `rounded-*` class.
      • Detects hardcoded color literals (hex/rgb/numbered Tailwind palettes)
        and warns via telemetry — does NOT auto-rewrite (palette resolution
        is non-trivial and a wrong rewrite paints the page worse).

    Returns a counters dict — `{drift_fixed: N, hardcoded_color_warnings: M}`.
    """
    counters: dict[str, int] = {"drift_fixed": 0, "hardcoded_color_warnings": 0}

    src_dir = os.path.join(workspace_path, "src")
    if not os.path.isdir(src_dir):
        return counters

    # Pull tokens from landing.json if not provided.
    if not design_tokens:
        landing_path = os.path.join(workspace_path, "src", "content", "landing.json")
        if os.path.isfile(landing_path):
            try:
                import json as _json
                with open(landing_path, "r", encoding="utf-8") as f:
                    design_tokens = (_json.load(f) or {}).get("design_tokens") or {}
            except Exception:
                design_tokens = {}
        else:
            design_tokens = {}

    button_radius = (design_tokens or {}).get("button_radius_class", "rounded-md")
    card_radius = (design_tokens or {}).get("card_radius_class", "rounded-2xl")
    image_radius = (design_tokens or {}).get("image_radius_class", "rounded-xl")

    sections_dir = os.path.join(src_dir, "components", "sections")
    walk_root = sections_dir if os.path.isdir(sections_dir) else src_dir

    for root, dirs, files in os.walk(walk_root):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for fname in files:
            if os.path.splitext(fname)[1].lower() not in _JSX_EXTENSIONS:
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
            except Exception:
                continue

            original = content

            # ── radius drift ────────────────────────────────────────────
            def _maybe_inject(match: re.Match, radius: str) -> str:
                attrs = match.group(1)
                extracted = _extract_classname_value(attrs)
                if not extracted:
                    return match.group(0)
                cur, _s, _e = extracted
                if _has_radius_class(cur):
                    return match.group(0)
                # splice radius class
                new_attrs = _splice_class(attrs, radius)
                counters["drift_fixed"] += 1
                # Reconstruct the original tag string with new attrs.
                whole = match.group(0)
                return whole.replace(attrs, new_attrs, 1)

            content = _BUTTON_TAG_RE.sub(lambda m: _maybe_inject(m, button_radius), content)
            content = _CARD_DIV_RE.sub(lambda m: _maybe_inject(m, card_radius), content)
            content = _INPUT_TAG_RE.sub(lambda m: _maybe_inject(m, button_radius), content)

            # ── hardcoded color drift (info-level telemetry) ────────────
            # Status colors (red/green/amber for errors/success/warnings) and
            # brand-fixed hex (logos, partner badges) are legitimate. We just
            # count + log at INFO so a maintainer can spot drift without it
            # flooding warning streams. The post-gen pipeline does NOT auto-
            # rewrite — picking the right semantic token from a hex requires
            # palette inference and a wrong rewrite paints the page worse.
            for pat in (_HARDCODED_HEX_IN_CLASS_RE, _HARDCODED_RGB_IN_CLASS_RE, _TAILWIND_PALETTE_NUMBERED_RE):
                for m in pat.finditer(original):
                    counters["hardcoded_color_warnings"] += 1
                    logger.info(
                        "design_token_drift: hardcoded color %r in %s",
                        m.group(0),
                        os.path.relpath(fpath, workspace_path),
                    )

            if content != original:
                try:
                    with open(fpath, "w", encoding="utf-8") as f:
                        f.write(content)
                    logger.info(
                        "design_token_drift: spliced missing radius classes in %s",
                        os.path.relpath(fpath, workspace_path),
                    )
                except Exception as exc:
                    logger.warning("design_token_drift: write failed: %s", exc)

    return counters


# ╔══════════════════════════════════════════════════════════════╗
# ║  MAIN ENTRY POINT — Run All Fixers                         ║
# ╚══════════════════════════════════════════════════════════════╝

async def run_all_fixers(
    workspace_path: str,
    websocket=None,
) -> dict:
    """Run ALL post-generation fixers in sequence.
    
    Returns dict with results from each fixer:
    {
        "use_client_fixed": ["path1.jsx", ...],
        "icons_fixed": ["path2.jsx", ...],
        "stubs_created": ["path3.jsx", ...],
        "total_fixes": int,
    }
    """
    from app.services.ws_emit import _ws_send
    
    results = {
        "use_client_fixed": [],
        "config_stripped": [],
        "icons_fixed": [],
        "icons_object_keys_fixed": [],
        "stubs_created": [],
        "route_conflicts_fixed": [],
        "import_mismatches_fixed": [],
        "entities_fixed": [],
        "img_tags_fixed": [],
        "next_config_patched": False,
        "total_fixes": 0,
    }

    # -2. Inject error.js / global-error.js / not-found.js if missing.
    #     These are Next.js App Router error boundaries — without them ANY
    #     runtime crash shows a blank white screen with no recovery UI.
    try:
        injected = inject_error_boundaries(workspace_path)
        if injected:
            await _ws_send(
                websocket, "progress",
                f"🔧 Injected error boundaries: {', '.join(injected)}",
            )
    except Exception as e:
        logger.warning("Error boundary injector failed (non-fatal): %s", e)

    # -1. Restore src/components/ui/ from git HEAD — undoes any AI overwrites of
    #     shadcn/ui template files (parse errors, wrong imports, etc.)
    try:
        restored = restore_template_ui_files(workspace_path)
        if restored:
            await _ws_send(websocket, "progress", "🔧 Restored template UI components from git")
    except Exception as e:
        logger.warning("UI restore fixer failed (non-fatal): %s", e)

    # 0. Strip 'use client' from static config/data files (MUST run first)
    # navigation.js, site.js, etc. must never have 'use client' — they are
    # imported by server components and adding the directive breaks .map() calls.
    try:
        stripped = strip_use_client_from_configs(workspace_path)
        results["config_stripped"] = stripped
        if stripped:
            await _ws_send(
                websocket, "progress",
                f"🔧 Removed 'use client' from {len(stripped)} config file(s): {', '.join(stripped)}",
            )
    except Exception as e:
        logger.warning("Config 'use client' stripper failed (non-fatal): %s", e)

    # 1. Fix 'use client'
    try:
        await _ws_send(websocket, "progress", "🔧 Checking 'use client' directives...")
        fixed = fix_use_client(workspace_path)
        results["use_client_fixed"] = fixed
        if fixed:
            await _ws_send(websocket, "progress", f"✅ Auto-injected 'use client' in {len(fixed)} files")
    except Exception as e:
        logger.warning("'use client' fixer failed (non-fatal): %s", e)

    # 2. Fix banned icons
    try:
        await _ws_send(websocket, "progress", "🔧 Checking icon imports...")
        fixed = fix_banned_icons(workspace_path)
        results["icons_fixed"] = fixed
        if fixed:
            await _ws_send(websocket, "progress", f"✅ Fixed banned icons in {len(fixed)} files")
    except Exception as e:
        logger.warning("Banned icon fixer failed (non-fatal): %s", e)

    # 2b. Add missing keys to the shared Icons object so `Icons.foo` references
    #     resolve at runtime. Catches the "Element type is invalid: ... got
    #     undefined" crash that fires when AI code-gen writes <Icons.instagram>
    #     without first declaring `instagram: Instagram` in icons.js.
    try:
        fixed = fix_missing_icons_object_keys(workspace_path)
        results["icons_object_keys_fixed"] = fixed
        if fixed:
            await _ws_send(
                websocket, "progress",
                f"✅ Added missing keys to {fixed[0]} (prevents Icons.foo undefined crashes)",
            )
    except Exception as e:
        logger.warning("Icons-object key fixer failed (non-fatal): %s", e)

    # 3. Fix named-import / default-export mismatches (import { X } where X is a default export)
    try:
        fixed = fix_named_import_default_export_mismatch(workspace_path)
        results["import_mismatches_fixed"] = fixed
        if fixed:
            await _ws_send(
                websocket, "progress",
                f"🔧 Fixed {len(fixed)} named-import/default-export mismatch(es): {', '.join(fixed)}",
            )
    except Exception as e:
        logger.warning("Named-import mismatch fixer failed (non-fatal): %s", e)

    # 3b. Fix missing default exports (import X from file that only has export function X)
    try:
        fixed = fix_missing_default_export(workspace_path)
        results["missing_default_export_fixed"] = fixed
        if fixed:
            await _ws_send(
                websocket, "progress",
                f"🔧 Added missing default export in {len(fixed)} file(s): {', '.join(fixed)}",
            )
    except Exception as e:
        logger.warning("Missing-default-export fixer failed (non-fatal): %s", e)

    # 4. Fix dynamic route conflicts ([id] vs [slug] under same parent)
    try:
        removed = fix_dynamic_route_conflicts(workspace_path)
        results["route_conflicts_fixed"] = removed
        if removed:
            await _ws_send(
                websocket, "progress",
                f"🔧 Removed {len(removed)} conflicting dynamic route dir(s): {', '.join(removed)}",
            )
    except Exception as e:
        logger.warning("Dynamic route conflict fixer failed (non-fatal): %s", e)

    # 4. Fix unresolved imports
    try:
        await _ws_send(websocket, "progress", "🔧 Resolving missing imports...")
        stubs = fix_unresolved_imports(workspace_path)
        results["stubs_created"] = stubs
        if stubs:
            await _ws_send(websocket, "progress", f"✅ Created {len(stubs)} stub files for missing imports")
    except Exception as e:
        logger.warning("Import resolver failed (non-fatal): %s", e)

    # 4b. Unescape HTML entities that ended up as JSX attribute delimiters
    #     (Claude sometimes writes className=&quot;...&quot; which breaks the parser).
    #     MUST run before the text-node escaper so we don't double-escape.
    try:
        fixed = fix_html_entities_in_attributes(workspace_path)
        results["attribute_entities_fixed"] = fixed
        if fixed:
            await _ws_send(
                websocket, "progress",
                f"🔧 Unescaped HTML entities in JSX attributes of {len(fixed)} file(s)",
            )
    except Exception as e:
        logger.warning("Attribute-entity fixer failed (non-fatal): %s", e)

    # 4c. Recover any &apos;/&quot; that an earlier run mis-escaped INSIDE
    #     JS expression context (ternary branches, function args). Symptom:
    #     `next build` fails with "Expression expected" pointing at &apos;
    #     in something like `cond ? (<Foo/>) : (&apos;text&apos;)`.
    #     Must run before fix_unescaped_entities so the same-pattern doesn't
    #     get re-broken by the same call.
    try:
        fixed = fix_mis_escaped_entities_in_js(workspace_path)
        results["mis_escaped_entities_reverted"] = fixed
        if fixed:
            await _ws_send(
                websocket, "progress",
                f"🔧 Reverted mis-escaped entities in JS context in {len(fixed)} file(s)",
            )
    except Exception as e:
        logger.warning("Mis-escaped entities recovery failed (non-fatal): %s", e)

    # 5. Fix unescaped JSX entities (' and " in text nodes → &apos; / &quot;)
    try:
        fixed = fix_unescaped_entities(workspace_path)
        results["entities_fixed"] = fixed
        if fixed:
            await _ws_send(websocket, "progress", f"🔧 Escaped JSX entities in {len(fixed)} file(s)")
    except Exception as e:
        logger.warning("Unescaped entities fixer failed (non-fatal): %s", e)

    # 5d. Decode `\\uXXXX` escapes in JSX text content. Claude's tool-use
    #     JSON occasionally writes non-ASCII characters (Uzbek apostrophe ʻ,
    #     degree sign °, Cyrillic, etc.) as the literal escape sequence
    #     instead of the codepoint. JSX text nodes do NOT decode these,
    #     so without this pass non-English prompts render the raw `\\u…`.
    try:
        fixed = fix_unicode_escapes_in_jsx(workspace_path)
        results["unicode_escapes_decoded"] = fixed
        if fixed:
            await _ws_send(websocket, "progress", f"🔧 Decoded \\uXXXX in JSX text in {len(fixed)} file(s)")
    except Exception as e:
        logger.warning("Unicode-escape decoder failed (non-fatal): %s", e)

    # 6. Replace <img> with Next.js <Image /> (only for Next.js projects)
    try:
        fixed = fix_img_tags(workspace_path)
        results["img_tags_fixed"] = fixed
        if fixed:
            await _ws_send(websocket, "progress", f"🔧 Replaced <img> with <Image /> in {len(fixed)} file(s)")
    except Exception as e:
        logger.warning("img→Image fixer failed (non-fatal): %s", e)

    # 7. Patch next.config.js with external image remotePatterns (picsum.photos etc.)
    try:
        patched = fix_next_config_image_domains(workspace_path)
        results["next_config_patched"] = patched
        if patched:
            await _ws_send(websocket, "progress", "🔧 Patched next.config.js with image remotePatterns")
    except Exception as e:
        logger.warning("next.config image domain patcher failed (non-fatal): %s", e)

    # 7b. Patch next.config to never fail Vercel build on ESLint / tsc errors.
    #     A single missing `key` prop or unused-var warning should not block
    #     deploy of an AI-generated landing page.
    try:
        patched = fix_next_config_build_ignore(workspace_path)
        results["next_config_build_ignore_patched"] = patched
        if patched:
            await _ws_send(websocket, "progress", "🔧 Patched next.config.js to skip ESLint/TS errors on build")
    except Exception as e:
        logger.warning("next.config build-ignore patcher failed (non-fatal): %s", e)

    # 7c. Strip `-q`/`--quiet` from json-server invocations. v1.x dropped
    #     the flag, so templates that still ship it crash `pnpm dev` and
    #     surface a "1 error" badge in the Next.js overlay.
    try:
        patched = fix_package_json_dev_script(workspace_path)
        results["package_json_dev_script_patched"] = patched
        if patched:
            await _ws_send(websocket, "progress", "🔧 Stripped unsupported -q flag from json-server in package.json")
    except Exception as e:
        logger.warning("package.json dev-script patcher failed (non-fatal): %s", e)

    # 7d. Inject id attribute on each section's outermost element so the
    #     header's #anchor nav links scroll to the right element. Without
    #     this, Phase 2 LLM occasionally omits the id and clicking nav
    #     does nothing on the rendered page.
    try:
        section_id_patched = fix_section_ids(workspace_path)
        results["section_ids_patched"] = section_id_patched
        if section_id_patched:
            await _ws_send(
                websocket, "progress",
                f"🔧 Injected anchor id on {len(section_id_patched)} section file(s)",
            )
    except Exception as e:
        logger.warning("section-id patcher failed (non-fatal): %s", e)

    # 7e. Wrap nav-iteration <Link> tags in MarketingHeader.jsx with a
    #     NavAnchor helper so hash hrefs trigger native smooth scroll
    #     instead of Next.js's router (which silently no-ops on hashes).
    #     The marketing-header builder injects this only when keyed on
    #     `key={link.href}`; Phase 1 Claude often uses other key shapes
    #     and slips past, so we run a broader pass here.
    try:
        header_patched = fix_marketing_header_nav_anchors(workspace_path)
        results["marketing_header_nav_anchor_patched"] = header_patched
        if header_patched:
            await _ws_send(
                websocket, "progress",
                "🔧 Wrapped MarketingHeader nav links with NavAnchor (hash-scroll fix)",
            )
    except Exception as e:
        logger.warning("marketing-header NavAnchor patcher failed (non-fatal): %s", e)

    # 7f. Audit hash hrefs in MarketingHeader against actual section ids.
    #     Phase 1 frequently emits href="#features" while the section file
    #     is FeatureGridSection.jsx (id "feature-grid"). Run AFTER section_ids
    #     so we know the canonical slug each section will end up with.
    try:
        anchor_rewrites = fix_header_anchor_alignment(workspace_path)
        results["header_anchor_rewrites"] = anchor_rewrites
        if anchor_rewrites:
            await _ws_send(
                websocket, "progress",
                f"🔧 Aligned {anchor_rewrites} header anchor(s) to section ids",
            )
    except Exception as e:
        logger.warning("header-anchor alignment patcher failed (non-fatal): %s", e)

    results["total_fixes"] = (
        len(results["config_stripped"])
        + len(results["use_client_fixed"])
        + len(results["icons_fixed"])
        + len(results.get("icons_object_keys_fixed", []))
        + len(results["stubs_created"])
        + len(results["route_conflicts_fixed"])
        + len(results["import_mismatches_fixed"])
        + len(results["entities_fixed"])
        + len(results["img_tags_fixed"])
        + (1 if results["next_config_patched"] else 0)
    )

    if results["total_fixes"] > 0:
        logger.info(
            "Post-generation fixers: %d total fixes "
            "(config_stripped=%d, use_client=%d, icons=%d, icons_keys=%d, stubs=%d, route_conflicts=%d)",
            results["total_fixes"],
            len(results["config_stripped"]),
            len(results["use_client_fixed"]),
            len(results["icons_fixed"]),
            len(results.get("icons_object_keys_fixed", [])),
            len(results["stubs_created"]),
            len(results["route_conflicts_fixed"]),
        )
    
    return results


# ── Click-to-edit listener backfill (Base44 flow) ────────────────────
#
# Adds ``src/components/lucid/EditModeListener.jsx`` and its import +
# render in ``src/app/layout.js`` for projects generated BEFORE the
# listener was added to ``website_pipeline``'s foundation builder.
# Idempotent — projects that already have it are no-ops.
#
# Without this, the workspace's "Edit" toolbar button toggles visually
# but the iframe has no listener, so postMessage selections never
# reach the parent and the user perceives the feature as broken.

# Match either the `from "@/components/lucid/EditModeListener"` import
# or a bare `EditModeListener` render — used to detect whether we've
# already wired the layout.
_EDIT_LISTENER_IMPORT_PATH = "@/components/lucid/EditModeListener"
_EDIT_LISTENER_TAG = "<EditModeListener"


def ensure_edit_mode_listener(workspace_path: str) -> str:
    """Make sure the click-to-edit listener is present + wired.

    Returns a short status string for logging:
      • ``""``          — already wired (no-op)
      • ``"written"``   — listener file was missing, just wrote it
      • ``"patched"``   — layout missing the import/render, patched
      • ``"both"``      — wrote the file AND patched the layout
      • ``"no_layout"`` — no Next.js App Router layout found (likely
                          a Vite admin or unsupported template; skip)

    Idempotent — safe to call on every preview start. Failures bubble
    up as logged warnings; never raises.
    """
    actions: list[str] = []

    listener_path = os.path.join(
        workspace_path, "src", "components", "lucid", "EditModeListener.jsx",
    )
    layout_path = _find_root_layout(workspace_path)

    if not layout_path:
        # No App Router layout to patch — likely a non-website project
        # (e.g. Vite admin shell). Skip rather than guess at the right
        # mount point.
        return "no_layout"

    # ── 1. Write or rewrite the listener ──────────────────
    # Existing projects may have an older version of the listener on
    # disk (e.g. one that only handles data-editable-path and ignores
    # plain clicks). We compare the on-disk version marker against the
    # current one and rewrite when stale.
    try:
        from app.services.website_pipeline import (
            _build_edit_mode_listener_component,
            _current_edit_mode_listener_version,
        )
    except Exception as exc:
        logger.warning(
            "post_generation_fixer: cannot import listener builder: %s", exc,
        )
        return ""

    needs_write = True
    if os.path.isfile(listener_path):
        try:
            with open(listener_path, "r", encoding="utf-8") as fh:
                head = fh.read(400)
            on_disk_version = _parse_listener_version(head)
            if on_disk_version is not None and on_disk_version >= _current_edit_mode_listener_version():
                needs_write = False
        except Exception as exc:
            logger.debug(
                "post_generation_fixer: listener version probe failed: %s", exc,
            )
            # Fall through to rewrite — safer than skipping with unknown state.

    if needs_write:
        try:
            os.makedirs(os.path.dirname(listener_path), exist_ok=True)
            with open(listener_path, "w", encoding="utf-8") as fh:
                fh.write(_build_edit_mode_listener_component())
            actions.append("written")
            logger.info(
                "post_generation_fixer: wrote %s (version=%d)",
                os.path.relpath(listener_path, workspace_path),
                _current_edit_mode_listener_version(),
            )
        except Exception as exc:
            logger.warning(
                "post_generation_fixer: failed to write EditModeListener.jsx: %s",
                exc,
            )
            return ""

    # ── 2. Patch layout if it isn't already wired ─────────
    try:
        with open(layout_path, "r", encoding="utf-8") as fh:
            layout_src = fh.read()
    except Exception as exc:
        logger.warning(
            "post_generation_fixer: cannot read %s: %s", layout_path, exc,
        )
        return actions[0] if actions else ""

    if _EDIT_LISTENER_IMPORT_PATH in layout_src and _EDIT_LISTENER_TAG in layout_src:
        # Already wired — only return "written" if we did write step 1
        return actions[0] if actions else ""

    new_src = _patch_layout_for_listener(layout_src)
    if new_src == layout_src:
        # We couldn't find a safe insertion point — log and bail so we
        # don't produce a broken layout file. The listener file may
        # still be present for a manual fix.
        logger.warning(
            "post_generation_fixer: could not patch %s — no <Providers> "
            "or </body> insertion point found",
            os.path.relpath(layout_path, workspace_path),
        )
        return actions[0] if actions else ""

    try:
        with open(layout_path, "w", encoding="utf-8") as fh:
            fh.write(new_src)
        actions.append("patched")
        logger.info(
            "post_generation_fixer: patched %s to mount EditModeListener",
            os.path.relpath(layout_path, workspace_path),
        )
    except Exception as exc:
        logger.warning(
            "post_generation_fixer: cannot write patched %s: %s",
            layout_path, exc,
        )
        return actions[0] if actions else ""

    if len(actions) == 2:
        return "both"
    return actions[0] if actions else ""


def _parse_listener_version(head: str) -> int | None:
    """Pull the version integer out of a listener source's preamble.

    The builder injects a line like ``/* LUCID_LISTENER_VERSION=2 */``
    near the top of the file. Returns the parsed int, or None when no
    marker is present (legacy listener, pre-versioning).
    """
    if not isinstance(head, str) or "LUCID_LISTENER_VERSION" not in head:
        return None
    import re
    m = re.search(r"LUCID_LISTENER_VERSION\s*=\s*(\d+)", head)
    if not m:
        return None
    try:
        return int(m.group(1))
    except (TypeError, ValueError):
        return None


def _find_root_layout(workspace_path: str) -> str:
    """Return the absolute path to ``src/app/layout.{js,jsx,ts,tsx}``
    if one exists, else an empty string. Picks the first match — App
    Router only ever has one root layout.
    """
    base = os.path.join(workspace_path, "src", "app")
    if not os.path.isdir(base):
        return ""
    for ext in ("js", "jsx", "tsx", "ts"):
        candidate = os.path.join(base, f"layout.{ext}")
        if os.path.isfile(candidate):
            return candidate
    return ""


def _patch_layout_for_listener(source: str) -> str:
    """Insert the EditModeListener import + render tag into a Next.js
    App Router layout.

    Returns the original source unchanged when we can't find a safe
    insertion point (no ``</body>`` or recognizable shape). The caller
    treats no-change as a soft failure.

    The patch follows two rules to stay safe across hand-written
    layouts:

      • Imports are added after the last existing ``import`` line so
        we don't break TypeScript / Next conventions that expect all
        imports at the top of the file.

      • The render tag is inserted immediately before ``</body>`` —
        the universal terminator for any layout shape. We never try
        to splice into the middle of a JSX tree.
    """
    if _EDIT_LISTENER_IMPORT_PATH in source and _EDIT_LISTENER_TAG in source:
        return source

    new_src = source

    # ── Insert import after the last top-of-file import ─────────
    if _EDIT_LISTENER_IMPORT_PATH not in new_src:
        import re
        import_lines = list(re.finditer(r"^import\s+.*?;\s*$", new_src, re.MULTILINE))
        if not import_lines:
            # No imports at all — paste at the top so the layout still parses.
            insertion = (
                'import EditModeListener from "'
                + _EDIT_LISTENER_IMPORT_PATH
                + '";\n'
            )
            new_src = insertion + new_src
        else:
            last = import_lines[-1]
            insertion = (
                '\nimport EditModeListener from "'
                + _EDIT_LISTENER_IMPORT_PATH
                + '";'
            )
            new_src = new_src[: last.end()] + insertion + new_src[last.end():]

    # ── Insert <EditModeListener /> right before </body> ─────────
    if _EDIT_LISTENER_TAG not in new_src:
        idx = new_src.rfind("</body>")
        if idx < 0:
            # No </body> — abort to avoid producing a broken layout.
            return source
        # Match the indentation of the </body> line so the resulting
        # JSX stays readable. We splice BEFORE the line's leading
        # whitespace (insert at line_start, not at idx) so we don't
        # double up on the existing indent that's already on disk.
        line_start = new_src.rfind("\n", 0, idx) + 1
        indent = new_src[line_start:idx]
        # Pad two extra spaces so the tag sits one level deeper than
        # </body> (i.e. lives among </body>'s children).
        tag_indent = indent + "  "
        new_src = (
            new_src[:line_start]
            + tag_indent
            + "<EditModeListener />\n"
            + new_src[line_start:]
        )

    return new_src
