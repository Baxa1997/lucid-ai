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
                component = (
                    f"const {alias} = ({{ className, ...props }}) => (\n"
                    f"  {svg_markup.replace('<svg ', f'<svg className={{className}} ')}\n"
                    f");"
                )
                # Fix: add props spread
                component = component.replace("</svg>", "</svg>")
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
    from app.services.project_generator import _ws_send
    
    results = {
        "use_client_fixed": [],
        "config_stripped": [],
        "icons_fixed": [],
        "stubs_created": [],
        "route_conflicts_fixed": [],
        "import_mismatches_fixed": [],
        "total_fixes": 0,
    }

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
    
    results["total_fixes"] = (
        len(results["config_stripped"])
        + len(results["use_client_fixed"])
        + len(results["icons_fixed"])
        + len(results["stubs_created"])
        + len(results["route_conflicts_fixed"])
        + len(results["import_mismatches_fixed"])
    )

    if results["total_fixes"] > 0:
        logger.info(
            "Post-generation fixers: %d total fixes "
            "(config_stripped=%d, use_client=%d, icons=%d, stubs=%d, route_conflicts=%d)",
            results["total_fixes"],
            len(results["config_stripped"]),
            len(results["use_client_fixed"]),
            len(results["icons_fixed"]),
            len(results["stubs_created"]),
            len(results["route_conflicts_fixed"]),
        )
    
    return results
