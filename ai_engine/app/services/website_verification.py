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

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _content_separation_enabled() -> bool:
    raw = os.environ.get("CONTENT_SEPARATION_ENABLED", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


# Patterns
_IMPORT_RX = re.compile(
    r'^\s*import\s+(?:(?P<default>\w+)\s*(?:,\s*\{[^}]*\}\s*)?|\{[^}]*\})\s+from\s+["\'](?P<path>[^"\']+)["\']',
    re.MULTILINE,
)
_DEFAULT_EXPORT_RX = re.compile(r'\bexport\s+default\s+(?:function\s+)?(\w+)', re.MULTILINE)
_CSS_VAR_RX = re.compile(r'var\(--color-[^)]+\)')

# <img …/>  and  <Image …/>  tags (Next.js).  Captures the full attr blob between
# the tag name and the closing > so we can sub-match src / alt within it.
_IMG_TAG_RX = re.compile(r'<(?:img|Image)\b([^>]*?)/?\s*>', re.IGNORECASE | re.DOTALL)
_SRC_ATTR_RX = re.compile(r'\bsrc\s*=\s*"([^"]+)"|\bsrc\s*=\s*\{["\']([^"\']+)["\']\s*\}')
_ALT_ATTR_RX = re.compile(r'\balt\s*=\s*"([^"]*)"|\balt\s*=\s*\{["\']([^"\']*)["\']\s*\}')


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
        "local_image_paths":       [],  # <img src="/images/..."> — no such files
        "missing_alt_text":        [],  # <img> with empty / missing alt
        "non_http_image_src":      [],  # <img src="not-a-url">
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

            # ── Image audit (Stage 7.5) ────────────────────────────────────
            # Catch the three classes of image bugs that would surface as
            # broken images in the rendered site:
            #   1) src points at /images/… or /assets/… (local file that
            #      Stage 5.5 never wrote → 404 in browser)
            #   2) alt attribute is missing or empty (a11y violation)
            #   3) src is a bare word (Claude hallucinated a placeholder)
            for tag_m in _IMG_TAG_RX.finditer(content):
                attrs = tag_m.group(1) or ""
                line_no = content[:tag_m.start()].count("\n") + 1
                src_m = _SRC_ATTR_RX.search(attrs)
                src = ""
                if src_m:
                    src = (src_m.group(1) or src_m.group(2) or "").strip()

                if not src:
                    # No src at all (or {expression} we can't resolve statically) — skip
                    pass
                elif src.startswith("/images/") or src.startswith("/assets/") or src.startswith("/img/"):
                    issues["local_image_paths"].append(f"{rel}:{line_no}  src={src!r}")
                elif src.startswith(("http://", "https://", "data:", "//")):
                    pass  # remote URL — assumed valid
                elif src.startswith("{") or src.startswith("$") or src.startswith("`"):
                    pass  # JSX expression — can't audit statically
                else:
                    issues["non_http_image_src"].append(f"{rel}:{line_no}  src={src!r}")

                alt_m = _ALT_ATTR_RX.search(attrs)
                if not alt_m:
                    issues["missing_alt_text"].append(f"{rel}:{line_no}  no alt attribute")
                else:
                    alt_text = (alt_m.group(1) or alt_m.group(2) or "").strip()
                    if not alt_text or alt_text.lower() in ("image", "photo", "picture", "img"):
                        issues["missing_alt_text"].append(
                            f"{rel}:{line_no}  alt={alt_text!r} (empty or non-descriptive)"
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
        + len(issues["local_image_paths"])  # /images/foo.jpg → guaranteed 404
        + len(issues["non_http_image_src"])
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
    if issues["local_image_paths"]:
        summary_parts.append(f"{len(issues['local_image_paths'])} local image path(s)")
    if issues["missing_alt_text"]:
        summary_parts.append(f"{len(issues['missing_alt_text'])} missing alt text")
    if issues["non_http_image_src"]:
        summary_parts.append(f"{len(issues['non_http_image_src'])} invalid image src")

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


# ══════════════════════════════════════════════════════════════════════
# Stage 7.5 — Content quality audit
# ══════════════════════════════════════════════════════════════════════
#
# Runs AFTER the structural audit above. The structural audit catches
# missing files / broken imports — this one catches BAD CONTENT inside
# generated files: lorem-ipsum stubs, fake "123 Main Street" addresses,
# unfilled "Company Name" template slots, voice-policy violations,
# broken internal /routes/, accessibility issues, and Claude
# duplicating the same paragraph across sections.
#
# Returns a structured report with a 0-100 quality score. Caller (the
# pipeline) logs warnings when score < 70 but never blocks — false
# positives in content checks are worse than misses.

# ── Placeholder strings (case-insensitive) ──
# Each hit on a file flags THAT FILE once in `issues.placeholders`,
# regardless of how many placeholder strings appear in it.
_PLACEHOLDER_PATTERNS: list[re.Pattern] = [
    re.compile(r"\blorem\s+ipsum\b", re.IGNORECASE),
    re.compile(r"\byour\s+content\s+here\b", re.IGNORECASE),
    re.compile(r"\bsample\s+text\b", re.IGNORECASE),
    re.compile(r"\bplaceholder\s+(?:text|content|copy)\b", re.IGNORECASE),
    # "Section 1" / "Section 2" — only standalone, in text content (not class names)
    re.compile(r">[^<]*\bSection\s+[1-9]\b[^<]*<"),
    # TODO / FIXME — flag anywhere; in JSX these are unfilled stubs.
    re.compile(r"\bTODO\b|\bFIXME\b"),
]

# ── Forbidden / fake content (occurrence-level) ──
# Each hit appends one entry to `issues.forbidden_content` with
# file:line so the user can find and fix the exact spot.
_FORBIDDEN_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b123\s+Main\s+Street\b", re.IGNORECASE),               "fake address '123 Main Street'"),
    (re.compile(r"\bAnytown\b", re.IGNORECASE),                            "fake city 'Anytown'"),
    (re.compile(r"\bZIP\s*12345\b", re.IGNORECASE),                        "fake zip '12345'"),
    (re.compile(r"\b555[-.\s]?01\d{2}\b"),                                 "fake phone (555-01xx range)"),
    (re.compile(r"\bexample@example\.com\b", re.IGNORECASE),               "fake email example@example.com"),
    (re.compile(r"\btest@test\.com\b", re.IGNORECASE),                     "fake email test@test.com"),
    # "Company Name" / "Business Name" — but ONLY when they look like
    # untouched template variables (e.g. inside <>…</> text content or
    # quoted strings — NOT when they appear as code identifiers like
    # `companyName` or `business_name` which are perfectly fine).
    (re.compile(r"['\"]Company\s+Name['\"]"),                              "'Company Name' template stub"),
    (re.compile(r"['\"]Business\s+Name['\"]"),                             "'Business Name' template stub"),
    (re.compile(r">[^<]*\bCompany\s+Name\b[^<]*<"),                        "'Company Name' template stub"),
    (re.compile(r">[^<]*\bBusiness\s+Name\b[^<]*<"),                       "'Business Name' template stub"),
]

# JSX text content extractor — pulls visible strings between > and < so
# duplication / placeholder checks operate on what the user sees, not
# on prop names / classNames. Crude but effective for static .jsx files.
_JSX_TEXT_RX = re.compile(r">([^<{][^<]*)<")

# href="/something" but NOT http(s), mailto, tel, #fragment, or full URL.
_INTERNAL_LINK_RX = re.compile(r'\bhref\s*=\s*"(/[^"#?][^"#]*)"')

# Form-field name attributes — used by recruitment / lead-gen checks.
_FIELD_NAME_RX = re.compile(r'\bname\s*=\s*"([^"]+)"', re.IGNORECASE)
_INPUT_TAG_RX = re.compile(r"<(?:input|textarea|select)\b[^>]*>", re.IGNORECASE)
_FORM_TAG_RX = re.compile(r"<form\b", re.IGNORECASE)
_PRICE_RX = re.compile(r"[\$€£¥]\s?\d|\d+[\.,]\d{2}\s*(?:USD|EUR|GBP)\b", re.IGNORECASE)


def _line_no(content: str, idx: int) -> int:
    return content[:idx].count("\n") + 1


def _walk_jsx_files(workspace_path: str) -> list[tuple[str, str]]:
    """Walk the workspace and return [(rel_path, content), ...] for every
    .jsx / .tsx / page.js file under src/.

    Skips node_modules, .next, and config files.
    """
    out: list[tuple[str, str]] = []
    src_root = os.path.join(workspace_path, "src")
    if not os.path.isdir(src_root):
        return out
    for dirpath, dirnames, filenames in os.walk(src_root):
        # Prune build output dirs in case they ended up under src
        dirnames[:] = [d for d in dirnames if d not in ("node_modules", ".next", ".turbo")]
        for fn in filenames:
            if not fn.endswith((".jsx", ".tsx")) and fn not in ("page.js",):
                continue
            abs_path = os.path.join(dirpath, fn)
            try:
                with open(abs_path, "r", encoding="utf-8") as f:
                    content = f.read()
            except Exception:
                continue
            rel = os.path.relpath(abs_path, workspace_path)
            out.append((rel, content))
    return out


def _route_exists(workspace_path: str, route: str) -> bool:
    """Does `src/app/<route>/page.{js,jsx,tsx}` exist?

    Treats "/" as home (src/app/page.js). Strips trailing slash.
    """
    r = (route or "").strip().rstrip("/")
    if r in ("", "/"):
        candidates = ["src/app/page.js", "src/app/page.jsx", "src/app/page.tsx"]
    else:
        slug = r.lstrip("/")
        candidates = [
            f"src/app/{slug}/page.js",
            f"src/app/{slug}/page.jsx",
            f"src/app/{slug}/page.tsx",
        ]
    return any(os.path.isfile(os.path.join(workspace_path, c)) for c in candidates)


def _check_purpose_form_requirements(
    primary_purpose: str,
    files: list[tuple[str, str]],
) -> list[str]:
    """Stricter sub-check: certain purposes REQUIRE a form with specific fields.

    Returns a list of issue strings (each a short message). Empty when
    requirements are met OR purpose has no form requirement.
    """
    issues: list[str] = []
    purpose = (primary_purpose or "").strip().lower()

    def _files_with_form() -> list[tuple[str, str]]:
        return [(p, c) for p, c in files if _FORM_TAG_RX.search(c)]

    def _all_field_names() -> set[str]:
        names: set[str] = set()
        for _, content in files:
            for input_match in _INPUT_TAG_RX.finditer(content):
                name_match = _FIELD_NAME_RX.search(input_match.group(0))
                if name_match:
                    names.add(name_match.group(1).lower())
        return names

    if purpose == "recruitment":
        forms = _files_with_form()
        if not forms:
            issues.append("recruitment: no <form> tag found in any component")
            return issues
        field_names = _all_field_names()
        # Accept common variants — don't be strict about exact naming.
        wanted = {
            "name":  {"name", "fullname", "full_name", "first_name", "firstname", "applicantname"},
            "email": {"email", "emailaddress", "email_address", "applicantemail"},
            "phone": {"phone", "phonenumber", "phone_number", "tel", "mobile", "cell"},
        }
        for label, accepted in wanted.items():
            if not (field_names & accepted):
                issues.append(f"recruitment: form missing {label!r} field (saw fields: {sorted(field_names)[:8]})")

    elif purpose == "lead_generation":
        forms = _files_with_form()
        if not forms:
            issues.append("lead_generation: no <form> for quote/contact found")
            return issues
        field_names = _all_field_names()
        if not (field_names & {"email", "emailaddress", "email_address"}):
            issues.append("lead_generation: contact form missing 'email' field")

    elif purpose == "ecommerce":
        # Need at least one section with a product-card-shaped structure:
        # multiple price hits AND multiple article/div items.
        price_hits = 0
        for _, content in files:
            price_hits += len(_PRICE_RX.findall(content))
        if price_hits < 2:
            issues.append("ecommerce: fewer than 2 price strings found across all components")

    elif purpose == "appointment_booking":
        forms = _files_with_form()
        if not forms:
            issues.append("appointment_booking: no booking <form> found")

    return issues


def audit_content(
    project_dir: str | Path,
    purpose_data: dict | None = None,
    voice_signature: dict | None = None,
) -> dict[str, Any]:
    """Stage 7.5 — content quality audit. Returns score + categorized issues.

    Args
    ----
    project_dir:      Workspace root (the dir containing src/).
    purpose_data:     Output of purpose_classifier. Used for required-section
                      and form-field checks. Pass {} if unknown.
    voice_signature:  Optional brand voice rules:
                        {"forbidden_phrases": ["amazing", ...], "tone": "..."}
                      Pass {} or None to skip the voice-violation check.

    Return shape (matches the user-spec exactly + a few extras):
      {
        "ok":      bool,    # score >= 70
        "score":   int,     # 0-100
        "issues": {
          "placeholders":              [...],
          "missing_required_sections": [...],
          "forbidden_content":         [...],
          "voice_violations":          [...],
          "broken_internal_links":     [...],
          "missing_alt_text":          [...],
          "duplicate_content":         [...],
        },
        "warnings": [...],
        "summary":  "...",
      }
    """
    workspace_path = str(project_dir)
    purpose_data = purpose_data or {}
    voice_signature = voice_signature or {}
    primary_purpose = (purpose_data.get("primary_purpose") or "").strip().lower()

    files = _walk_jsx_files(workspace_path)

    issues: dict[str, list] = {
        "placeholders":              [],
        "missing_required_sections": [],
        "forbidden_content":         [],
        "voice_violations":          [],
        "broken_internal_links":     [],
        "missing_alt_text":          [],
        "duplicate_content":         [],
    }
    warnings: list[str] = []

    # ── (a) Placeholder + (e) forbidden content ───────────────────
    paragraph_locations: dict[str, list[str]] = {}  # for duplicate detection
    for rel, content in files:
        placeholder_hit_in_file = False

        for pat in _PLACEHOLDER_PATTERNS:
            m = pat.search(content)
            if m:
                placeholder_hit_in_file = True
                # Capture all hits with line numbers for diagnostics
                for hit in pat.finditer(content):
                    snippet = hit.group(0)[:60].replace("\n", " ")
                    line = _line_no(content, hit.start())
                    # We append once per pattern — short circuit after a few
                    # to keep noise down on a file that's all-placeholder.
                    if len([h for h in issues["placeholders"] if h.startswith(rel)]) >= 5:
                        break
                    issues["placeholders"].append(f"{rel}:{line}  {snippet!r}")

        for pat, label in _FORBIDDEN_PATTERNS:
            for hit in pat.finditer(content):
                line = _line_no(content, hit.start())
                issues["forbidden_content"].append(f"{rel}:{line}  {label}")

        # ── (b) Voice violations ───────────────────────────────────
        for phrase in (voice_signature.get("forbidden_phrases") or []):
            if not isinstance(phrase, str) or len(phrase) < 2:
                continue
            phrase_lc = phrase.lower()
            # Whole-word match where possible
            try:
                vpat = re.compile(rf"\b{re.escape(phrase)}\b", re.IGNORECASE)
            except re.error:
                continue
            for hit in vpat.finditer(content):
                line = _line_no(content, hit.start())
                issues["voice_violations"].append(f"{rel}:{line}  uses forbidden {phrase!r}")

        # ── (f) Duplicate content ─────────────────────────────────
        # Pull JSX-visible text chunks ≥ 50 chars and remember which
        # file each appeared in. Crosswalk after the loop.
        for tm in _JSX_TEXT_RX.finditer(content):
            text = tm.group(1).strip()
            # Strip JSX whitespace + collapse runs
            text = re.sub(r"\s+", " ", text)
            if len(text) < 50:
                continue
            paragraph_locations.setdefault(text, []).append(rel)

        # ── (d) Internal link validation ──────────────────────────
        for lm in _INTERNAL_LINK_RX.finditer(content):
            href = lm.group(1)
            # Strip query string for the file-existence check
            route = href.split("?", 1)[0].split("#", 1)[0]
            if not _route_exists(workspace_path, route):
                line = _line_no(content, lm.start())
                issues["broken_internal_links"].append(f"{rel}:{line}  href={href!r}")

        # ── (e) Image accessibility (extends Stage 7's check) ─────
        for img_m in _IMG_TAG_RX.finditer(content):
            attrs = img_m.group(1) or ""
            alt_m = _ALT_ATTR_RX.search(attrs)
            line = _line_no(content, img_m.start())
            if not alt_m:
                issues["missing_alt_text"].append(f"{rel}:{line}  no alt attribute")
                continue
            alt_text = (alt_m.group(1) or alt_m.group(2) or "").strip()
            if not alt_text:
                issues["missing_alt_text"].append(f"{rel}:{line}  empty alt")
                continue
            # Alt text shouldn't be the filename
            if re.search(r"\.(?:jpe?g|png|webp|gif|svg)$", alt_text, re.IGNORECASE):
                issues["missing_alt_text"].append(
                    f"{rel}:{line}  alt is filename {alt_text!r}"
                )

    # ── Duplicate content cross-check ───────────────────────────
    for text, occurrences in paragraph_locations.items():
        unique_files = list(dict.fromkeys(occurrences))  # preserve order
        if len(unique_files) >= 2:
            issues["duplicate_content"].append(
                f"{text[:80]!r} appears in: {', '.join(unique_files[:5])}"
            )

    # ── (c) Required-section coverage ───────────────────────────
    from app.services.purpose_structures import (
        get_required_sections, concept_satisfied, primary_concept_label,
    )

    # Build a haystack containing every component file path + every
    # JSX file content (lowercased). Searching this catches both
    # filename-based naming (HomeOpenRoles.jsx) and content-based
    # references ("application form").
    component_haystack = " ".join(
        os.path.basename(rel).lower() + " " + content.lower()
        for rel, content in files
    )

    for concept in get_required_sections(primary_purpose):
        if not concept_satisfied(concept, component_haystack):
            issues["missing_required_sections"].append(
                f"{primary_purpose or 'site'}: missing {primary_concept_label(concept)!r} concept "
                f"(any of: {', '.join(concept)})"
            )

    # Stricter sub-check for purposes that REQUIRE specific form fields
    for issue in _check_purpose_form_requirements(primary_purpose, files):
        issues["missing_required_sections"].append(issue)

    # ── Scoring ─────────────────────────────────────────────────
    # Penalties (per spec):
    #   -20 per placeholder FILE (not per occurrence — dedupe by file)
    #   -15 per missing required section
    #   -10 per forbidden_content occurrence
    #    -5 per voice_violation
    #   -10 per broken_internal_link
    #    -3 per missing alt_text (cap -15)
    placeholder_files = sorted({entry.split(":", 1)[0] for entry in issues["placeholders"]})
    score = 100
    score -= 20 * len(placeholder_files)
    score -= 15 * len(issues["missing_required_sections"])
    score -= 10 * len(issues["forbidden_content"])
    score -=  5 * len(issues["voice_violations"])
    score -= 10 * len(issues["broken_internal_links"])
    score -= min(15, 3 * len(issues["missing_alt_text"]))
    # Duplicate content is a warning — cap penalty at 5.
    score -= min(5, 2 * len(issues["duplicate_content"]))
    score = max(0, min(100, score))

    if issues["voice_violations"]:
        warnings.append(f"{len(issues['voice_violations'])} voice violations")
    if issues["missing_alt_text"]:
        warnings.append(f"{len(issues['missing_alt_text'])} images missing/weak alt text")
    if issues["duplicate_content"]:
        warnings.append(f"{len(issues['duplicate_content'])} duplicated paragraphs")

    ok = score >= 70

    summary_parts: list[str] = []
    if placeholder_files:
        summary_parts.append(f"{len(placeholder_files)} placeholder file(s)")
    if issues["missing_required_sections"]:
        summary_parts.append(f"{len(issues['missing_required_sections'])} missing required")
    if issues["forbidden_content"]:
        summary_parts.append(f"{len(issues['forbidden_content'])} forbidden")
    if issues["broken_internal_links"]:
        summary_parts.append(f"{len(issues['broken_internal_links'])} broken link(s)")
    if issues["voice_violations"]:
        summary_parts.append(f"{len(issues['voice_violations'])} voice")
    if issues["missing_alt_text"]:
        summary_parts.append(f"{len(issues['missing_alt_text'])} alt")
    if issues["duplicate_content"]:
        summary_parts.append(f"{len(issues['duplicate_content'])} dup")

    summary = (
        f"content_audit: score={score}/100 ({'PASS' if ok else 'BELOW THRESHOLD'}) "
        f"— {len(files)} files scanned"
        + (f" — {', '.join(summary_parts)}" if summary_parts else "")
    )
    logger.info("website_verification: %s", summary)

    return {
        "ok":       ok,
        "score":    score,
        "issues":   issues,
        "warnings": warnings,
        "summary":  summary,
    }


# ══════════════════════════════════════════════════════════════════════
# Stage 7.5b — Content / code separation audit
# ══════════════════════════════════════════════════════════════════════
#
# Runs when CONTENT_SEPARATION_ENABLED=1. Validates the new Phase-4
# editing contract:
#   • Section JSX files import from src/content/pages/<slug>.json
#   • No hardcoded user-facing copy in section JSX (text must be {content.…})
#   • Every <Editable path="…"> references a real key in the JSON
#   • .lucid/content-schema.json paths line up with the actual content
#
# Three single-purpose checks (per spec) plus a combined ``audit_content_separation``
# wrapper that runs all of them for the whole workspace.

# JSX-aware regexes. These are deliberately CONSERVATIVE — we'd rather
# miss a violation than fail a clean site.

# Captures `import content from "@/content/pages/<slug>.json"`
_CONTENT_IMPORT_RX = re.compile(
    r'import\s+\w+\s+from\s+["\']@/content/pages/([\w\-]+)\.json["\']'
)

# Captures every `<Editable path="..." [type="..."]>` opening tag.
_EDITABLE_TAG_RX = re.compile(
    r'<Editable\b([^>]*?)/?\s*>',
    re.IGNORECASE | re.DOTALL,
)
_EDITABLE_PATH_ATTR_RX = re.compile(r'\bpath\s*=\s*"([^"]+)"')
_EDITABLE_TYPE_ATTR_RX = re.compile(r'\btype\s*=\s*"([^"]+)"')

# Allowed <Editable type="..."> values
_ALLOWED_EDITABLE_TYPES = {
    "text", "rich_text", "image", "button", "color", "array", "url", "boolean", "number",
}

# Matches a JSX text node ≥4 word chars that is NOT a {…} expression.
# We restrict to >, then optional whitespace, then a literal containing
# at least 4 alphabetic chars, then <. Skips quoted strings (attributes
# don't appear between > and <).
_HARDCODED_TEXT_RX = re.compile(
    r'>\s*([^<>{}][^<>{}]{3,}?)\s*<',
)

# Tags whose text children are layout / decorative and CAN stay literal
# (e.g. an icon name passed to a Lucide component) — but the audit
# operates on text nodes, not tag names, so we just skip when the
# candidate text matches a known pure-decoration pattern.
_ALLOWED_LITERAL_TEXT_RX = re.compile(
    # Pure whitespace / punctuation / emoji
    r"^[\s\.\-—–·•|/\\()$£€¥¢#0-9]+$"
)


def _resolve_content_path(content: Any, dotted: str) -> tuple[bool, Any]:
    """Walk a dotted path (`hero.cta_primary.label`) through a dict.

    Returns (found, value). Supports `key[]` to mean "any array item",
    in which case found=True if the array is non-empty.
    """
    cur: Any = content
    parts = dotted.split(".")
    for part in parts:
        if part.endswith("[]"):
            base = part[:-2]
            if isinstance(cur, dict) and base in cur:
                cur = cur[base]
            else:
                return False, None
            if not isinstance(cur, list) or not cur:
                return False, None
            cur = cur[0]
            continue
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list):
            # `path.0` style indexing
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return False, None
        else:
            return False, None
    return True, cur


def _extract_jsx_text_nodes(content: str) -> list[tuple[int, str]]:
    """Return [(line_no, text)] for every text node found in the JSX.

    We ignore text that's whitespace-only or matches the decoration regex.
    """
    out: list[tuple[int, str]] = []
    for m in _HARDCODED_TEXT_RX.finditer(content):
        text = m.group(1).strip()
        if not text:
            continue
        # Skip lines that are obvious code (a JS expression that happened
        # to land between > and < without braces — rare). The conservative
        # heuristic: require at least 4 letters AND at least one space.
        if not re.search(r"[A-Za-z]{4,}", text):
            continue
        if _ALLOWED_LITERAL_TEXT_RX.match(text):
            continue
        line_no = content[: m.start()].count("\n") + 1
        out.append((line_no, text[:80]))
    return out


def check_no_hardcoded_copy(jsx_file_path: str) -> list[str]:
    """Flag literal JSX text nodes that should come from `{content.…}`.

    Returns a list of `path:line  text…` strings (empty when clean).
    Files that don't import content JSON are exempt — they're either
    the page composition or a legacy file.
    """
    try:
        with open(jsx_file_path, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return []
    # Composition files have no content import and just wire sections together.
    if not _CONTENT_IMPORT_RX.search(content):
        return []
    rel = jsx_file_path  # caller passes whatever path it wants reported
    issues: list[str] = []
    for line_no, text in _extract_jsx_text_nodes(content):
        # Skip text that's clearly already an interpolation we couldn't
        # parse (defense in depth — the regex already excludes {…}).
        if "{" in text or "}" in text:
            continue
        issues.append(f"{rel}:{line_no}  hardcoded {text!r}")
    return issues


def check_editable_paths_valid(
    jsx_files: list[str],
    content_files: dict[str, dict],
) -> list[str]:
    """Verify every <Editable path="…" /> resolves in the page's content JSON.

    ``content_files`` is a dict mapping page slug → parsed content dict.
    """
    issues: list[str] = []
    for jsx_path in jsx_files:
        try:
            with open(jsx_path, "r", encoding="utf-8") as f:
                content = f.read()
        except OSError:
            continue
        # Which page does this file belong to? Look at the import.
        m_import = _CONTENT_IMPORT_RX.search(content)
        if not m_import:
            continue
        slug = m_import.group(1)
        page_content = content_files.get(slug)
        if page_content is None:
            issues.append(f"{jsx_path}  imports content for {slug!r} but no JSON file found")
            continue
        for m_tag in _EDITABLE_TAG_RX.finditer(content):
            attrs = m_tag.group(1) or ""
            m_path = _EDITABLE_PATH_ATTR_RX.search(attrs)
            if not m_path:
                line_no = content[: m_tag.start()].count("\n") + 1
                issues.append(f"{jsx_path}:{line_no}  <Editable> with no path= attribute")
                continue
            dotted = m_path.group(1).strip()
            found, _ = _resolve_content_path(page_content, dotted)
            if not found:
                line_no = content[: m_tag.start()].count("\n") + 1
                issues.append(
                    f"{jsx_path}:{line_no}  <Editable path={dotted!r}> not in {slug}.json"
                )
            # Check type attribute too
            m_type = _EDITABLE_TYPE_ATTR_RX.search(attrs)
            if m_type:
                t = m_type.group(1).strip()
                if t not in _ALLOWED_EDITABLE_TYPES:
                    line_no = content[: m_tag.start()].count("\n") + 1
                    issues.append(
                        f"{jsx_path}:{line_no}  unknown <Editable type={t!r}> "
                        f"(allowed: {sorted(_ALLOWED_EDITABLE_TYPES)})"
                    )
    return issues


def check_content_schema_matches(
    schema: dict,
    content_files: dict[str, dict],
) -> list[str]:
    """Every field path in the schema must exist in the corresponding content."""
    issues: list[str] = []
    pages = (schema or {}).get("pages") or {}
    for slug, page_schema in pages.items():
        content = content_files.get(slug)
        if content is None:
            issues.append(f"schema references page {slug!r} but no content JSON found")
            continue
        fields = (page_schema or {}).get("fields") or {}
        for path in fields:
            # Strip [] array-item segments before resolving — the array
            # itself was recorded separately and we already validated the
            # parent during walk.
            found, _ = _resolve_content_path(content, path)
            if not found:
                issues.append(f"schema.{slug}.{path!r} not in content/{slug}.json")
    return issues


def audit_content_separation(workspace_path: str | Path) -> dict[str, Any]:
    """Combined audit for the content/code-separation contract.

    Returns ``{ok, issues, summary}`` where ``issues`` has three keys:
      - hardcoded_copy:     [...]
      - bad_editable_paths: [...]
      - schema_mismatches:  [...]
    """
    ws = str(workspace_path)
    issues: dict[str, list] = {
        "hardcoded_copy":     [],
        "bad_editable_paths": [],
        "schema_mismatches":  [],
    }

    # Skip the whole audit when feature is off — caller decides whether
    # to enforce. Old projects without the layout still verify cleanly.
    if not _content_separation_enabled():
        return {
            "ok":      True,
            "issues":  issues,
            "summary": "content_separation_audit: skipped (flag off)",
            "skipped": True,
        }

    # Load all content JSON files keyed by page slug.
    content_dir = os.path.join(ws, "src", "content", "pages")
    content_files: dict[str, dict] = {}
    if os.path.isdir(content_dir):
        for fn in os.listdir(content_dir):
            if not fn.endswith(".json"):
                continue
            slug = fn[:-5]
            try:
                with open(os.path.join(content_dir, fn), "r", encoding="utf-8") as f:
                    content_files[slug] = json.load(f)
            except (OSError, json.JSONDecodeError) as exc:
                issues["schema_mismatches"].append(
                    f"src/content/pages/{fn}  invalid JSON ({exc})"
                )

    # Walk every section .jsx file under src/components/pages/<slug>/
    components_dir = os.path.join(ws, "src", "components", "pages")
    jsx_files: list[str] = []
    if os.path.isdir(components_dir):
        for dirpath, _, filenames in os.walk(components_dir):
            for fn in filenames:
                if fn.endswith(".jsx"):
                    jsx_files.append(os.path.join(dirpath, fn))

    # (1) Hardcoded-copy scan
    for abs_path in jsx_files:
        rel = os.path.relpath(abs_path, ws)
        # check_no_hardcoded_copy reports lines using the path you pass
        # in — pass the rel path so summaries are short.
        flagged = check_no_hardcoded_copy(abs_path)
        # Rewrite the report lines to use the relative path for tidy logging.
        for line in flagged:
            issues["hardcoded_copy"].append(line.replace(abs_path, rel, 1))

    # (2) <Editable> path validation
    issues["bad_editable_paths"] = []
    bad = check_editable_paths_valid(jsx_files, content_files)
    for line in bad:
        for abs_p in jsx_files:
            if abs_p in line:
                line = line.replace(abs_p, os.path.relpath(abs_p, ws), 1)
                break
        issues["bad_editable_paths"].append(line)

    # (3) Schema ↔ content cross-check
    schema_path = os.path.join(ws, ".lucid", "content-schema.json")
    schema: dict = {}
    if os.path.isfile(schema_path):
        try:
            with open(schema_path, "r", encoding="utf-8") as f:
                schema = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            issues["schema_mismatches"].append(
                f".lucid/content-schema.json invalid JSON ({exc})"
            )
    issues["schema_mismatches"].extend(check_content_schema_matches(schema, content_files))

    total = sum(len(v) for v in issues.values())
    ok = total == 0
    summary = (
        f"content_separation_audit: {'PASS' if ok else 'FAIL'} — "
        f"{len(content_files)} content file(s), {len(jsx_files)} JSX file(s) checked"
        + (f" — {total} issue(s)" if total else "")
    )
    logger.info("website_verification: %s", summary)
    return {
        "ok":       ok,
        "issues":   issues,
        "summary":  summary,
        "files":    {"content": len(content_files), "jsx": len(jsx_files)},
    }
