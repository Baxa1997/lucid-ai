"""Post-generation verification for the website pipeline.

Runs AFTER Stage 6 (parallel creative) completes. Validates the generated
files for common failure modes BEFORE the dev server starts. Catches issues
that would otherwise surface as confusing runtime errors.

Verification levels:
  • STATIC AUDIT (no LLM, no build) — fast checks:
      - Every page.js exists for routes in plan
      - Every imported component file actually exists
      - All section components have a default export
      - No file references colors via CSS variables (must use Tailwind)
  • BUILD CHECK (optional) — run `next build` and parse the output
      Not run by default; caller wires it when wanted.

Returns a structured report with categorized issues. Caller decides whether
to retry specific pages, surface warnings, or proceed.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)


# Patterns
_IMPORT_RX = re.compile(
    r'^\s*import\s+(?:(?P<default>\w+)\s*(?:,\s*\{[^}]*\}\s*)?|\{[^}]*\})\s+from\s+["\'](?P<path>[^"\']+)["\']',
    re.MULTILINE,
)
_DEFAULT_EXPORT_RX = re.compile(r'\bexport\s+default\s+(?:function\s+)?(\w+)', re.MULTILINE)
_CSS_VAR_RX = re.compile(r'var\(--color-[^)]+\)')


def audit_generated_website(
    workspace_path: str,
    plan: dict[str, Any],
    *,
    expect_header: bool = True,
    expect_footer: bool = True,
) -> dict[str, Any]:
    """Static audit of the workspace AFTER generation. Pure I/O, no LLM.

    Returns:
      {
        "ok":              bool,    # passes all critical checks
        "files_audited":   int,
        "issues": {
          "missing_page_files":      [...],   # critical
          "missing_section_files":   [...],   # critical
          "missing_chrome_files":    [...],   # warning
          "broken_imports":          [{"file":..,"import":..,"target":..}, ...],
          "css_var_violations":      [...],   # warning (Tailwind not CSS vars)
          "missing_default_exports": [...],
        },
        "summary": "..."
      }
    """
    pages = plan.get("pages") or []

    issues: dict[str, list] = {
        "missing_page_files":      [],
        "missing_section_files":   [],
        "missing_chrome_files":    [],
        "broken_imports":          [],
        "css_var_violations":      [],
        "missing_default_exports": [],
    }
    files_audited = 0

    def _resolve_import_to_path(
        importer_abs: str,
        import_target: str,
    ) -> str | None:
        """Resolve an import path to an absolute file path on disk.

        Handles:
          @/foo/bar           → workspace_path/src/foo/bar.{js,jsx,ts,tsx}
          ./foo/bar           → relative to importer
        Returns None if it's a node_modules import (no @/ or relative prefix).
        """
        if not import_target:
            return None
        if import_target.startswith("@/"):
            base = os.path.join(workspace_path, "src", import_target[2:])
        elif import_target.startswith("./") or import_target.startswith("../"):
            base = os.path.normpath(
                os.path.join(os.path.dirname(importer_abs), import_target)
            )
        else:
            return None  # bare specifier = node_modules

        # Try common extensions
        for ext in ("", ".js", ".jsx", ".ts", ".tsx", "/index.js", "/index.jsx",
                    "/index.ts", "/index.tsx", ".css"):
            candidate = base + ext
            if os.path.isfile(candidate):
                return candidate
        return None

    def _check_file(rel: str, *, must_have_default_export: bool = False) -> None:
        nonlocal files_audited
        abs_path = os.path.join(workspace_path, rel)
        if not os.path.isfile(abs_path):
            return
        files_audited += 1
        try:
            with open(abs_path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception:
            return

        # CSS var violation check — only on .jsx/.tsx
        if rel.endswith((".jsx", ".tsx")):
            for m in _CSS_VAR_RX.finditer(content):
                issues["css_var_violations"].append(
                    f"{rel}:{content[:m.start()].count(chr(10)) + 1}  uses {m.group(0)} — should be Tailwind class"
                )

        # Default export check
        if must_have_default_export and not _DEFAULT_EXPORT_RX.search(content):
            issues["missing_default_exports"].append(rel)

        # Resolve every @/ or relative import
        for m in _IMPORT_RX.finditer(content):
            target = m.group("path")
            resolved = _resolve_import_to_path(abs_path, target)
            if resolved is None and (target.startswith("@/") or target.startswith(".")):
                issues["broken_imports"].append({
                    "file": rel, "import": target,
                })

    # ── Check page composition files + their section components ─────
    for page in pages:
        route = (page.get("route") or "/").strip()
        slug = "home" if route == "/" else route.strip("/").replace("/", "-")

        page_file = "src/app/page.js" if slug == "home" else f"src/app/{slug}/page.js"
        if not os.path.isfile(os.path.join(workspace_path, page_file)):
            issues["missing_page_files"].append(page_file)
        else:
            _check_file(page_file)

        # Check each per-page section component
        for s in (page.get("sections") or []):
            s_type = (s.get("type") or "section").strip().lower()
            from app.services.page_generator import _section_component_name
            comp = _section_component_name(slug, s_type)
            sec_file = f"src/components/pages/{slug}/{comp}.jsx"
            if not os.path.isfile(os.path.join(workspace_path, sec_file)):
                issues["missing_section_files"].append(sec_file)
            else:
                _check_file(sec_file, must_have_default_export=True)

    # ── Chrome files ───────────────────────────────────────────────
    if expect_header:
        header_file = "src/components/layout/MarketingHeader.jsx"
        if not os.path.isfile(os.path.join(workspace_path, header_file)):
            issues["missing_chrome_files"].append(header_file)
        else:
            _check_file(header_file, must_have_default_export=True)
    if expect_footer:
        footer_file = "src/components/layout/MarketingFooter.jsx"
        if not os.path.isfile(os.path.join(workspace_path, footer_file)):
            issues["missing_chrome_files"].append(footer_file)
        else:
            _check_file(footer_file, must_have_default_export=True)

    # ── Foundation files ───────────────────────────────────────────
    for foundation in (
        "src/config/site.js", "src/config/navigation.js",
        "src/lib/design-system.js",
    ):
        if os.path.isfile(os.path.join(workspace_path, foundation)):
            _check_file(foundation)

    critical_failures = (
        len(issues["missing_page_files"])
        + len(issues["missing_section_files"])
        + len(issues["broken_imports"])
        + len(issues["missing_default_exports"])
    )
    ok = critical_failures == 0

    summary_parts: list[str] = []
    if issues["missing_page_files"]:
        summary_parts.append(f"{len(issues['missing_page_files'])} missing page file(s)")
    if issues["missing_section_files"]:
        summary_parts.append(f"{len(issues['missing_section_files'])} missing section file(s)")
    if issues["broken_imports"]:
        summary_parts.append(f"{len(issues['broken_imports'])} broken import(s)")
    if issues["missing_chrome_files"]:
        summary_parts.append(f"{len(issues['missing_chrome_files'])} missing chrome file(s)")
    if issues["css_var_violations"]:
        summary_parts.append(f"{len(issues['css_var_violations'])} CSS var violation(s)")
    if issues["missing_default_exports"]:
        summary_parts.append(f"{len(issues['missing_default_exports'])} missing default export(s)")

    summary = (
        f"audit: {'PASS' if ok else 'FAIL'} — {files_audited} files checked"
        + (f" — {', '.join(summary_parts)}" if summary_parts else "")
    )
    logger.info("website_verification: %s", summary)

    return {
        "ok": ok,
        "files_audited": files_audited,
        "issues": issues,
        "summary": summary,
    }
