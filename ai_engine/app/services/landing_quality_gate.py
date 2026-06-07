"""Quality gate — verify the generated landing matches its purpose contract.

Runs after the build validator. For each `primary_purpose` we have a small
set of structural checks (does the apply form exist? do pay rates appear?
are the named roles actually rendered?). Every check is a deterministic
file scan — no LLM, no false positives from voice/style differences.

The gate NEVER blocks completion. It returns a structured report; the
pipeline forwards it to the frontend over WebSocket as `quality_report`
so the user can see what's missing and trigger per-section regeneration
(wired up later in Stage A6).

Public entry point: ``run_quality_gate``.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)


# ── File walking ──────────────────────────────────────────────────────

_SKIP_DIRS = {"node_modules", ".git", ".next", "dist", "build", ".vite", "__pycache__"}
_SCAN_EXTS = {".jsx", ".tsx", ".js", ".ts", ".json"}


def _read_workspace(workspace_path: str) -> dict[str, str]:
    """Read every text file under src/ and return {relpath: content}.

    Limited to src/ — never walks node_modules or build outputs. Files
    that fail to decode are skipped silently.
    """
    src_dir = os.path.join(workspace_path, "src")
    if not os.path.isdir(src_dir):
        src_dir = workspace_path

    blob: dict[str, str] = {}
    for root, dirs, files in os.walk(src_dir):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for fname in files:
            ext = os.path.splitext(fname)[1].lower()
            if ext not in _SCAN_EXTS:
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                    blob[os.path.relpath(fpath, workspace_path)] = f.read()
            except Exception:
                continue
    return blob


def _any_file_matches(files: dict[str, str], pattern: re.Pattern[str]) -> list[str]:
    """Return filenames whose content matches the pattern."""
    return [name for name, content in files.items() if pattern.search(content)]


# ── Per-purpose checkers ──────────────────────────────────────────────
#
# Each checker takes (files, intent) and returns a list of CheckResult
# dicts. A check has:
#   • name        — stable id (snake_case)
#   • label       — short human label for UI ("Application form present")
#   • passed      — bool
#   • severity    — "blocker" | "warning" | "info"
#                   (blockers are things the page can't do its job without)
#   • suggestion  — one sentence on what to fix when passed=False, "" otherwise
#   • where       — list of relpaths the evidence came from (best-effort)


_FORM_TAG_RE = re.compile(r"<form\b", re.IGNORECASE)
_SELECT_OPTIONS_RE = re.compile(
    r"<select\b[^>]*>(.*?)</select>",
    re.IGNORECASE | re.DOTALL,
)
_OPTION_RE = re.compile(r"<option\b[^>]*>", re.IGNORECASE)


def _check_hiring(files: dict[str, str], intent: dict[str, Any]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    named_roles = [r.strip() for r in (intent.get("named_roles") or []) if r.strip()]

    # 1) Application form is mandatory. The form tag and the apply-intent
    # copy can live in different files (hero CTA → form section is the
    # common pattern), so we check workspace-wide rather than per-file.
    apply_pat = re.compile(
        r"(apply\s*now|start\s*application|application\s*form|"
        r"submit\s*application|join\s*our\s*team)",
        re.IGNORECASE,
    )
    form_files = _any_file_matches(files, _FORM_TAG_RE)
    apply_intent_files = _any_file_matches(files, apply_pat)
    has_form_with_apply_intent = bool(form_files) and bool(apply_intent_files)
    checks.append({
        "name": "has_application_form",
        "label": "Application form present",
        "passed": has_form_with_apply_intent,
        "severity": "blocker",
        "suggestion": "" if has_form_with_apply_intent else (
            "No <form> paired with apply-style copy found. The hiring page "
            "must include an in-page application form (name, email, phone, "
            "role) reachable from an Apply CTA."
        ),
        "where": (form_files + apply_intent_files)[:3],
    })

    # 2) Role <select> is populated when 2+ named roles exist.
    role_select_passed = True
    role_select_files: list[str] = []
    if len(named_roles) >= 2:
        role_select_passed = False
        for fname, content in files.items():
            for m in _SELECT_OPTIONS_RE.finditer(content):
                inner = m.group(1)
                opts = _OPTION_RE.findall(inner)
                # Require >=2 options *and* at least one named role token
                # appears somewhere in the select block.
                if len(opts) >= 2 and any(role.lower() in inner.lower() for role in named_roles):
                    role_select_passed = True
                    role_select_files.append(fname)
                    break
            if role_select_passed:
                break
    checks.append({
        "name": "has_role_select",
        "label": "Form lists named roles in <select>",
        "passed": role_select_passed,
        "severity": "warning",
        "suggestion": "" if role_select_passed else (
            f"Application form should expose a <select> with options for: "
            f"{', '.join(named_roles[:6])}. Currently the select is missing "
            f"or empty."
        ),
        "where": role_select_files[:3],
    })

    # 3) Pay rates / numbers present somewhere on the page.
    pay_pat = re.compile(
        r"\$\s*\d[\d,]*"                # $1,500
        r"|\b\d+(?:\.\d+)?\s*(?:CPM|cpm)\b"
        r"|\b\d+(?:\.\d+)?\s*/\s*(?:hr|hour|year|yr|mile)\b",
    )
    pay_files = _any_file_matches(files, pay_pat)
    checks.append({
        "name": "has_pay_numbers",
        "label": "Concrete pay numbers visible",
        "passed": bool(pay_files),
        "severity": "blocker",
        "suggestion": "" if pay_files else (
            "Hiring pages must show real pay numbers above the fold or "
            "in section 2 — never just \"competitive pay\"."
        ),
        "where": pay_files[:3],
    })

    # 4) Each named role surfaces in at least one section.
    if named_roles:
        missing_roles: list[str] = []
        for role in named_roles[:8]:
            role_re = re.compile(re.escape(role), re.IGNORECASE)
            if not _any_file_matches(files, role_re):
                missing_roles.append(role)
        checks.append({
            "name": "named_roles_rendered",
            "label": "All named roles appear on the page",
            "passed": not missing_roles,
            "severity": "warning",
            "suggestion": "" if not missing_roles else (
                f"These named roles weren't found in any section: "
                f"{', '.join(missing_roles)}. Add them to open positions or "
                f"the hero copy."
            ),
            "where": [],
        })

    # 5) Employee-framed testimonials (heuristic — don't be too strict).
    testimonial_pat = re.compile(r"testimonial|quote|review", re.IGNORECASE)
    employee_pat = re.compile(
        r"\bdriver\b|\bteam\s*member\b|\bemployee\b|\bcrew\b|\bI\s+(?:drive|work|joined)",
        re.IGNORECASE,
    )
    testimonial_files = _any_file_matches(files, testimonial_pat)
    employee_files = [
        f for f in testimonial_files if employee_pat.search(files[f])
    ]
    if testimonial_files:
        checks.append({
            "name": "testimonials_are_employees",
            "label": "Testimonials are employees, not clients",
            "passed": bool(employee_files),
            "severity": "info",
            "suggestion": "" if employee_files else (
                "Testimonial section reads like client quotes. On a hiring "
                "page these should be employees talking about the work."
            ),
            "where": employee_files[:3],
        })

    return checks


def _check_lead_generation(files: dict[str, str], intent: dict[str, Any]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    quote_pat = re.compile(
        r"get\s*a?\s*quote|request\s*a?\s*quote|contact\s*sales|"
        r"book\s*a?\s*call|free\s*consultation",
        re.IGNORECASE,
    )
    form_files = _any_file_matches(files, _FORM_TAG_RE)
    quote_form_files = [f for f in form_files if quote_pat.search(files[f])]
    # Either a quote-titled form, or *any* in-page form is acceptable —
    # generic contact forms still convert leads.
    checks.append({
        "name": "has_lead_form",
        "label": "Lead-capture form present",
        "passed": bool(form_files),
        "severity": "blocker",
        "suggestion": "" if form_files else (
            "Lead-gen page must include an in-page quote or contact form."
        ),
        "where": (quote_form_files or form_files)[:3],
    })

    trust_pat = re.compile(
        r"client[s]?\s*(?:logo|trust|partner)|trusted\s*by|"
        r"as\s*seen\s*in|certified|iso\s*\d|\b\d{2,}\+?\s*(?:clients|projects|years)",
        re.IGNORECASE,
    )
    trust_files = _any_file_matches(files, trust_pat)
    checks.append({
        "name": "has_trust_signals",
        "label": "Trust signals visible",
        "passed": bool(trust_files),
        "severity": "warning",
        "suggestion": "" if trust_files else (
            "No trust signals found (client logos, certifications, year "
            "counts). Add at least one within the first 1.5 viewports."
        ),
        "where": trust_files[:3],
    })
    return checks


def _check_ecommerce(files: dict[str, str], intent: dict[str, Any]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    price_pat = re.compile(r"\$\s*\d[\d,]*(?:\.\d{2})?\b")
    price_files = _any_file_matches(files, price_pat)
    cta_pat = re.compile(r"shop\s*now|add\s*to\s*cart|buy\s*now|view\s*details",
                         re.IGNORECASE)
    cta_files = _any_file_matches(files, cta_pat)
    checks.append({
        "name": "has_product_grid",
        "label": "Product cards with price + buy CTA",
        "passed": bool(price_files and cta_files),
        "severity": "blocker",
        "suggestion": "" if (price_files and cta_files) else (
            "E-commerce page needs a product grid with visible prices and "
            "shop CTAs (\"Shop Now\" / \"View Details\")."
        ),
        "where": list(set(price_files + cta_files))[:3],
    })
    return checks


def _check_booking(files: dict[str, str], intent: dict[str, Any]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    book_pat = re.compile(
        r"book\s*(?:a|now|table|session|appointment)|reserve|schedule",
        re.IGNORECASE,
    )
    form_files = _any_file_matches(files, _FORM_TAG_RE)
    book_files = [f for f in form_files if book_pat.search(files[f])]
    checks.append({
        "name": "has_booking_form",
        "label": "Booking form / widget present",
        "passed": bool(book_files),
        "severity": "blocker",
        "suggestion": "" if book_files else (
            "Booking pages need an in-page form/widget with scheduling "
            "intent (book, reserve, schedule)."
        ),
        "where": book_files[:3],
    })
    return checks


_CHECKERS = {
    "hiring":           _check_hiring,
    "lead_generation":  _check_lead_generation,
    "ecommerce":        _check_ecommerce,
    "booking":          _check_booking,
}


# ── Public entry point ────────────────────────────────────────────────

def evaluate(workspace_path: str, intent: dict[str, Any]) -> dict[str, Any]:
    """Run the gate synchronously and return the report. Pure function.

    Split out so tests can call it without a websocket.
    """
    purpose = (intent or {}).get("primary_purpose") or ""
    checker = _CHECKERS.get(purpose)

    files = _read_workspace(workspace_path)
    checks = checker(files, intent) if checker else []

    blockers = [c for c in checks if c["severity"] == "blocker" and not c["passed"]]
    warnings = [c for c in checks if c["severity"] == "warning" and not c["passed"]]
    passed = [c for c in checks if c["passed"]]

    return {
        "purpose": purpose,
        "checks": checks,
        "summary": {
            "total":     len(checks),
            "passed":    len(passed),
            "blockers":  len(blockers),
            "warnings":  len(warnings),
        },
    }


async def run_quality_gate(
    workspace_path: str,
    intent: dict[str, Any] | None,
    websocket: Any = None,
) -> dict[str, Any]:
    """Run the gate, log a one-line summary, emit a `quality_report` event.

    Never raises. On any error returns an empty report so the caller can
    keep going without branching.
    """
    if not intent:
        return {"purpose": "", "checks": [], "summary": {"total": 0, "passed": 0, "blockers": 0, "warnings": 0}}

    try:
        report = evaluate(workspace_path, intent)
    except Exception as exc:
        logger.warning("quality_gate: evaluate failed (non-fatal) — %s", exc)
        return {"purpose": "", "checks": [], "summary": {"total": 0, "passed": 0, "blockers": 0, "warnings": 0}}

    s = report["summary"]
    logger.info(
        "quality_gate: purpose=%s total=%d passed=%d blockers=%d warnings=%d",
        report["purpose"], s["total"], s["passed"], s["blockers"], s["warnings"],
    )
    for c in report["checks"]:
        if not c["passed"]:
            logger.warning("quality_gate FAIL [%s] %s — %s",
                           c["severity"], c["name"], c["suggestion"])

    if websocket is not None:
        try:
            from app.services.llm_retry import emit_quality_summary

            await websocket.send_json({"type": "quality_report", "report": report})

            # Phase 2 Step 4: typed event carries the summary counters.
            await emit_quality_summary(
                websocket,
                passed=s["passed"],
                total=s["total"],
                blockers=s["blockers"],
                warnings=s["warnings"],
                purpose=report.get("purpose", "") or "",
            )

            if s["blockers"]:
                await websocket.send_json({
                    "type": "warning",
                    "message": f"⚠️  Quality gate: {s['blockers']} blocker(s), {s['warnings']} warning(s).",
                })
        except Exception:
            pass

    return report
