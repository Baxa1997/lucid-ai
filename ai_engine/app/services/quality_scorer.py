"""Quality scorer — automated quality checks on generated projects.

Scores generated projects across multiple dimensions to objectively measure
quality.  Runs after generation + build verification, BEFORE git push.

Usage:
    from app.services.quality_scorer import score_project
    result = await score_project(workspace_path, websocket)
    # result = {"score": 87, "checks": {...}, "warnings": [...]}
"""

from __future__ import annotations

import json
import os
import re
import logging
from typing import Any

logger = logging.getLogger("lucid.quality_scorer")

# Directories to skip during scanning
_SKIP_DIRS = {"node_modules", ".git", ".next", "dist", "build", ".vite", "__pycache__"}
_SOURCE_EXTENSIONS = {".jsx", ".tsx", ".js", ".ts", ".vue"}
_STYLE_EXTENSIONS = {".css", ".scss"}

# Path prefixes (relative to workspace) and basenames that the design-system
# adherence check should skip. Mirrors the audit set in
# project_generator._audit_design_system_imports — keep these in sync so the
# scorer denominator matches the audit's universe of "Claude-owned components".
_DS_SKIP_PREFIXES = (
    "src/components/ui/",        # shadcn template (restored from git)
    "src/lib/",                  # utility files (incl. design-system.js itself)
    "src/types/",                # type defs
    "src/components/layout/",    # deterministic builders we control directly
)
_DS_SKIP_BASENAMES = frozenset({
    "layout.js", "layout.jsx", "layout.tsx",
    "error.js", "error.jsx", "error.tsx",
    "global-error.js", "global-error.jsx", "global-error.tsx",
    "not-found.js", "not-found.jsx", "not-found.tsx",
    "loading.js", "loading.jsx", "loading.tsx",
    "template.js", "template.jsx", "template.tsx",
    "Providers.jsx", "Providers.tsx", "providers.jsx", "providers.tsx",
})


def _walk_src(workspace_path: str):
    """Walk source files, yielding (filepath, content) tuples."""
    src_dir = os.path.join(workspace_path, "src")
    if not os.path.isdir(src_dir):
        src_dir = workspace_path

    for root, dirs, files in os.walk(src_dir):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for fname in files:
            ext = os.path.splitext(fname)[1].lower()
            if ext in _SOURCE_EXTENSIONS | _STYLE_EXTENSIONS:
                filepath = os.path.join(root, fname)
                try:
                    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                        yield filepath, f.read()
                except Exception:
                    continue


# ╔══════════════════════════════════════════════════════════════╗
# ║  INDIVIDUAL CHECKS                                           ║
# ╚══════════════════════════════════════════════════════════════╝

def _check_file_count(workspace_path: str) -> dict:
    """Count generated source files."""
    count = 0
    by_type = {}
    for fpath, _ in _walk_src(workspace_path):
        ext = os.path.splitext(fpath)[1].lower()
        by_type[ext] = by_type.get(ext, 0) + 1
        count += 1

    # Score: 15+ files = full marks, 5-15 = partial, <5 = poor
    if count >= 15:
        score = 100
    elif count >= 8:
        score = 60 + (count - 8) * 5
    else:
        score = max(0, count * 12)

    return {
        "name": "file_count",
        "label": "Generated Files",
        "value": count,
        "details": by_type,
        "score": score,
        "max": 100,
    }


def _check_mock_data(workspace_path: str) -> dict:
    """Check if db.json exists and has sufficient mock data."""
    db_json_path = os.path.join(workspace_path, "db.json")
    if not os.path.isfile(db_json_path):
        return {
            "name": "mock_data",
            "label": "Mock Data (db.json)",
            "value": 0,
            "details": {"status": "MISSING — no db.json found"},
            "score": 0,
            "max": 100,
        }

    try:
        with open(db_json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, Exception):
        return {
            "name": "mock_data",
            "label": "Mock Data (db.json)",
            "value": 0,
            "details": {"status": "INVALID — db.json is not valid JSON"},
            "score": 10,
            "max": 100,
        }

    total_rows = 0
    entity_counts = {}
    for key, rows in data.items():
        if isinstance(rows, list):
            entity_counts[key] = len(rows)
            total_rows += len(rows)

    # Score: 50+ rows across entities = full, 20-50 = good, <20 = poor
    if total_rows >= 50:
        score = 100
    elif total_rows >= 20:
        score = 60 + int((total_rows - 20) / 30 * 40)
    elif total_rows > 0:
        score = int(total_rows / 20 * 60)
    else:
        score = 0

    return {
        "name": "mock_data",
        "label": "Mock Data (db.json)",
        "value": total_rows,
        "details": entity_counts,
        "score": score,
        "max": 100,
    }


def _check_design_system(workspace_path: str) -> dict:
    """Check if design-system.js exists and is imported by components."""
    ds_paths = [
        os.path.join(workspace_path, "src", "lib", "design-system.js"),
        os.path.join(workspace_path, "src", "lib", "design-system.ts"),
    ]

    ds_exists = any(os.path.isfile(p) for p in ds_paths)
    if not ds_exists:
        return {
            "name": "design_system",
            "label": "Design System",
            "value": "missing",
            "details": {"status": "No design-system.js found"},
            "score": 0,
            "max": 100,
        }

    # Count how many components import it. Scope: Claude-generated components
    # only — see _DS_SKIP_PREFIXES / _DS_SKIP_BASENAMES. Includes .js/.ts page
    # files (Next.js App Router commonly emits page.js, not page.jsx).
    import_count = 0
    total_components = 0
    for fpath, content in _walk_src(workspace_path):
        ext = os.path.splitext(fpath)[1].lower()
        if ext not in {".jsx", ".tsx", ".vue", ".js", ".ts"}:
            continue
        rel = os.path.relpath(fpath, workspace_path).replace(os.sep, "/")
        if any(rel.startswith(p) for p in _DS_SKIP_PREFIXES):
            continue
        if os.path.basename(fpath) in _DS_SKIP_BASENAMES:
            continue
        # Only count files that actually export a component (heuristic).
        if "export default" not in content and "export {" not in content:
            continue
        if ext in {".js", ".ts"} and ("<" not in content or "return " not in content):
            # .js / .ts file with no JSX or return — not a component
            continue
        total_components += 1
        if "design-system" in content or "designSystem" in content:
            import_count += 1

    if total_components == 0:
        return {
            "name": "design_system",
            "label": "Design System",
            "value": "no components",
            "score": 50,
            "max": 100,
        }

    pct = (import_count / total_components) * 100
    score = min(100, int(pct * 1.2))  # Bonus for high adoption

    return {
        "name": "design_system",
        "label": "Design System",
        "value": f"{import_count}/{total_components} components",
        "details": {"import_count": import_count, "total_components": total_components, "percentage": round(pct, 1)},
        "score": score,
        "max": 100,
    }


def _check_css_variables(workspace_path: str) -> dict:
    """Count CSS variables defined in the theme."""
    var_count = 0
    has_dark = False
    has_font_import = False

    for fpath, content in _walk_src(workspace_path):
        ext = os.path.splitext(fpath)[1].lower()
        if ext not in _STYLE_EXTENSIONS:
            continue

        # Count --variable: value declarations
        vars_found = re.findall(r"--[\w-]+\s*:", content)
        var_count += len(vars_found)

        if ".dark" in content or 'class="dark"' in content or "[data-theme=dark]" in content:
            has_dark = True
        if "@import url(" in content:
            has_font_import = True

    # Score: 25+ vars = full, 15-25 = good, <15 = poor
    if var_count >= 25:
        score = 80
    elif var_count >= 15:
        score = 50 + int((var_count - 15) / 10 * 30)
    else:
        score = max(0, int(var_count / 15 * 50))

    if has_dark:
        score = min(100, score + 10)
    if has_font_import:
        score = min(100, score + 10)

    return {
        "name": "css_variables",
        "label": "CSS Theme Variables",
        "value": var_count,
        "details": {"dark_mode": has_dark, "font_import": has_font_import},
        "score": score,
        "max": 100,
    }


def _check_responsive(workspace_path: str) -> dict:
    """Check responsive design usage (breakpoints)."""
    breakpoint_counts = {"sm:": 0, "md:": 0, "lg:": 0, "xl:": 0}
    total_files_with_responsive = 0

    for fpath, content in _walk_src(workspace_path):
        ext = os.path.splitext(fpath)[1].lower()
        if ext not in {".jsx", ".tsx", ".vue"}:
            continue

        has_responsive = False
        for bp in breakpoint_counts:
            count = content.count(bp)
            if count > 0:
                breakpoint_counts[bp] += count
                has_responsive = True

        if has_responsive:
            total_files_with_responsive += 1

    total_usage = sum(breakpoint_counts.values())

    # Score based on responsive usage breadth
    bps_used = sum(1 for v in breakpoint_counts.values() if v > 0)
    if bps_used >= 3 and total_usage >= 20:
        score = 100
    elif bps_used >= 2 and total_usage >= 10:
        score = 70
    elif bps_used >= 1:
        score = 40
    else:
        score = 0

    return {
        "name": "responsive",
        "label": "Responsive Design",
        "value": f"{bps_used}/4 breakpoints, {total_usage} usages",
        "details": breakpoint_counts,
        "score": score,
        "max": 100,
    }


def _check_animations(workspace_path: str) -> dict:
    """Check for framer-motion or animation usage."""
    motion_imports = 0
    animate_pulse = 0

    for fpath, content in _walk_src(workspace_path):
        if "framer-motion" in content:
            motion_imports += 1
        animate_pulse += content.count("animate-pulse")

    # Score: 5+ motion imports = full, 2-5 = good, 0 = poor
    if motion_imports >= 5:
        score = 100
    elif motion_imports >= 2:
        score = 60 + motion_imports * 8
    elif motion_imports == 1:
        score = 40
    else:
        score = 10 if animate_pulse > 0 else 0

    return {
        "name": "animations",
        "label": "Animations",
        "value": f"{motion_imports} framer-motion, {animate_pulse} loading",
        "details": {"framer_motion_files": motion_imports, "loading_animations": animate_pulse},
        "score": score,
        "max": 100,
    }


def _check_loading_states(workspace_path: str) -> dict:
    """Check for loading/empty/error state handling."""
    loading_patterns = 0
    empty_patterns = 0
    error_patterns = 0

    for fpath, content in _walk_src(workspace_path):
        ext = os.path.splitext(fpath)[1].lower()
        if ext not in {".jsx", ".tsx", ".vue"}:
            continue

        if re.search(r"isLoading|loading|skeleton|animate-pulse|Skeleton", content):
            loading_patterns += 1
        if re.search(r"empty|no .* found|nothing to show|no data|No results", content, re.IGNORECASE):
            empty_patterns += 1
        if re.search(r"isError|error|went wrong|try again|retry", content, re.IGNORECASE):
            error_patterns += 1

    total = loading_patterns + empty_patterns + error_patterns

    # Score: all three present and widespread = full
    has_all_three = loading_patterns > 0 and empty_patterns > 0 and error_patterns > 0
    if has_all_three and total >= 6:
        score = 100
    elif has_all_three:
        score = 80
    elif total >= 3:
        score = 60
    elif total > 0:
        score = 30
    else:
        score = 0

    return {
        "name": "loading_states",
        "label": "Loading/Empty/Error States",
        "value": f"L:{loading_patterns} E:{empty_patterns} Err:{error_patterns}",
        "details": {"loading": loading_patterns, "empty": empty_patterns, "error": error_patterns},
        "score": score,
        "max": 100,
    }


def _check_api_ready(workspace_path: str) -> dict:
    """Check if services make real API calls (not hardcoded data)."""
    services_with_fetch = 0
    services_with_hardcoded = 0
    total_service_files = 0

    for fpath, content in _walk_src(workspace_path):
        if "service" not in fpath.lower():
            continue
        ext = os.path.splitext(fpath)[1].lower()
        if ext not in {".js", ".ts"}:
            continue

        total_service_files += 1

        if "fetch(" in content or "axios" in content or "apiClient" in content:
            services_with_fetch += 1
        if re.search(r"const\s+(MOCK_|mock|FAKE_|fake_|SAMPLE_)", content):
            services_with_hardcoded += 1

    if total_service_files == 0:
        return {
            "name": "api_ready",
            "label": "API-Ready Services",
            "value": "no services",
            "score": 50,  # Might be a landing page
            "max": 100,
        }

    # Score: all fetch + no hardcoded = full marks
    fetch_pct = (services_with_fetch / total_service_files) * 100 if total_service_files else 0
    hardcoded_penalty = services_with_hardcoded * 15

    score = max(0, min(100, int(fetch_pct) - hardcoded_penalty))

    return {
        "name": "api_ready",
        "label": "API-Ready Services",
        "value": f"{services_with_fetch}/{total_service_files} use fetch()",
        "details": {
            "fetch_services": services_with_fetch,
            "hardcoded_services": services_with_hardcoded,
            "total": total_service_files,
        },
        "score": score,
        "max": 100,
    }


def _check_hardcoded_colors(workspace_path: str) -> dict:
    """Check for hardcoded hex/rgb colors in JSX/TSX files (should use Tailwind vars).

    Skips the same framework / template / utility paths the design-system check
    skips, plus `global-error.{js,jsx,tsx}` specifically — Next.js renders this
    file WITHOUT the root layout, so it has no access to Tailwind classes or
    CSS variables and MUST inline styles. Hex colors there are correct, not
    a violation.
    """
    violations = 0
    violation_files = []

    hex_re = re.compile(r"""(?:color|background|bg|border)\s*[:=]\s*['"]#[0-9a-fA-F]{3,8}['"]""")
    rgb_re = re.compile(r"""(?:color|background)\s*[:=]\s*['"]rgb""")

    _color_skip_basenames = _DS_SKIP_BASENAMES  # global-error.* etc. already in here

    for fpath, content in _walk_src(workspace_path):
        ext = os.path.splitext(fpath)[1].lower()
        if ext in _STYLE_EXTENSIONS:
            continue  # CSS files are allowed to have hex colors

        rel = os.path.relpath(fpath, workspace_path).replace(os.sep, "/")
        if any(rel.startswith(p) for p in _DS_SKIP_PREFIXES):
            continue
        if os.path.basename(fpath) in _color_skip_basenames:
            continue

        hex_matches = hex_re.findall(content)
        rgb_matches = rgb_re.findall(content)
        count = len(hex_matches) + len(rgb_matches)

        if count > 0:
            violations += count
            rel_path = os.path.relpath(fpath, workspace_path)
            violation_files.append(f"{rel_path} ({count})")

    # Score: 0 violations = full, 1-5 = warning, 5+ = poor
    if violations == 0:
        score = 100
    elif violations <= 3:
        score = 70
    elif violations <= 10:
        score = 40
    else:
        score = max(0, 100 - violations * 5)

    return {
        "name": "hardcoded_colors",
        "label": "Color Consistency",
        "value": f"{violations} violations",
        "details": {"files": violation_files[:10]},
        "score": score,
        "max": 100,
    }


# ╔══════════════════════════════════════════════════════════════╗
# ║  MAIN SCORER                                                 ║
# ╚══════════════════════════════════════════════════════════════╝

# Weights for overall score calculation
_WEIGHTS = {
    "file_count": 10,
    "mock_data": 15,
    "design_system": 15,
    "css_variables": 10,
    "responsive": 10,
    "animations": 10,
    "loading_states": 10,
    "api_ready": 15,
    "hardcoded_colors": 5,
}

# Archetypes that don't ship a json-server / db.json — skipping the
# mock_data check on these prevents false-flagging landings and content
# sites where the missing db.json is correct, not a regression.
_NO_DB_JSON_ARCHETYPES = frozenset({
    "single_page_landing", "landing", "consumer_website", "blog",
    "marketing_site",
})


def _checks_to_skip_for(archetype: str, entity_count: int) -> set[str]:
    """Decide which quality checks don't apply to this archetype.

    Skipped checks are dropped from both numerator and denominator so the
    final score reflects only what was actually measurable.
    """
    skip: set[str] = set()
    arch = (archetype or "").lower()
    # No entities → mock_data + api_ready are irrelevant signals
    if arch in _NO_DB_JSON_ARCHETYPES or entity_count <= 0:
        skip.add("mock_data")
        if entity_count <= 0:
            skip.add("api_ready")
    return skip


async def score_project(
    workspace_path: str,
    websocket=None,
    *,
    archetype: str = "",
    entity_count: int = 0,
) -> dict[str, Any]:
    """Score a generated project on multiple quality dimensions.

    Returns:
        {
            "score": int (0-100),
            "grade": str ("A+", "A", "B", "C", "D", "F"),
            "checks": {check_name: check_result},
            "warnings": [str],
            "skipped": [str],
        }

    archetype + entity_count are optional context that lets the scorer skip
    checks that don't apply (e.g. mock_data on a single-page landing has no
    db.json by design — counting it as 0/100 is misleading).
    """
    from app.services.project_generator import _ws_send

    await _ws_send(websocket, "progress", "📊 Scoring project quality...")

    skip = _checks_to_skip_for(archetype, entity_count)
    checks = {}
    warnings = []

    # Run all checks
    check_fns = [
        _check_file_count,
        _check_mock_data,
        _check_design_system,
        _check_css_variables,
        _check_responsive,
        _check_animations,
        _check_loading_states,
        _check_api_ready,
        _check_hardcoded_colors,
    ]

    for fn in check_fns:
        try:
            result = fn(workspace_path)
            if result["name"] in skip:
                # Don't include in average — track it for transparency.
                continue
            checks[result["name"]] = result

            # Generate warnings for low scores
            if result["score"] < 40:
                warnings.append(f"⚠️ {result['label']}: {result['value']} (score: {result['score']}/100)")
        except Exception as e:
            logger.warning("Quality check %s failed: %s", fn.__name__, e)

    # Calculate weighted overall score over only the applicable checks
    total_weight = sum(_WEIGHTS.get(name, 10) for name in checks)
    weighted_score = sum(
        checks[name]["score"] * _WEIGHTS.get(name, 10)
        for name in checks
    )
    overall_score = int(weighted_score / total_weight) if total_weight > 0 else 0

    # Grade
    if overall_score >= 95:
        grade = "A+"
    elif overall_score >= 85:
        grade = "A"
    elif overall_score >= 75:
        grade = "B+"
    elif overall_score >= 65:
        grade = "B"
    elif overall_score >= 50:
        grade = "C"
    elif overall_score >= 35:
        grade = "D"
    else:
        grade = "F"

    result = {
        "score": overall_score,
        "grade": grade,
        "checks": checks,
        "warnings": warnings,
        "skipped": sorted(skip),
    }

    await _ws_send(
        websocket, "progress",
        f"📊 Quality Score: {overall_score}/100 ({grade}) — "
        + ", ".join(f"{c['label']}:{c['score']}" for c in checks.values()),
    )

    if warnings:
        for w in warnings[:3]:
            await _ws_send(websocket, "progress", w)

    logger.info(
        "Quality score: %d/100 (%s) — %s",
        overall_score,
        grade,
        ", ".join(f"{k}={v['score']}" for k, v in checks.items()),
    )

    return result
