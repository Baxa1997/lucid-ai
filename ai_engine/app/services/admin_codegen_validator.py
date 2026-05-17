"""Static validator for Claude-generated admin CRUD files.

Light static checks — NOT a real parser. The point is to catch the
contract violations Claude is most likely to produce when the prompt
goes wrong:

  • Missing "use client" (would crash at first useState)
  • Missing AuthGuard (page would be world-readable)
  • Missing db_admin imports (page would have no data source)
  • TypeScript syntax (would fail `next build` on JSX-only codebase)
  • Raw fetch / supabase imports (bypasses the auth contract)
  • Obvious JSX brokenness (unbalanced tags / unclosed strings)

The validator returns a list of issue strings; empty list = clean.
Callers decide whether to log, surface to the user, or re-prompt
Claude.

Why not a real parser
---------------------
Esprima / acorn / Babel are JS-side. We could shell out to Node, but
the cost-vs-benefit is poor: Claude generally emits valid JSX, and
the build step catches anything subtler than these checks. The 6
checks below are the ones I've watched fail in practice.
"""
from __future__ import annotations

import re
from typing import Iterable


# ── Tunable patterns ─────────────────────────────────────────────────

# Crude TS detection: `: Type` in a parameter list, `as Type` cast,
# `<T>` generic. Each matches enough false negatives that we use a
# conservative set rather than a single regex.
_TS_PATTERNS = [
    # `function foo(name: string)` — colon-typed function params.
    re.compile(r"function\s+\w+\s*\([^)]*:\s*[A-Z]\w*", re.MULTILINE),
    # `const x: Type =` — typed locals.
    re.compile(r"\bconst\s+\w+\s*:\s*[A-Z]\w*\s*="),
    # `value as Type` — cast.
    re.compile(r"\bas\s+[A-Z]\w*[\s;]"),
    # `interface Foo {` / `type Foo =`.
    re.compile(r"^\s*(interface|type)\s+[A-Z]\w*\s*[{=]", re.MULTILINE),
]

_FORBIDDEN_IMPORTS = [
    # Anything pulling Supabase directly bypasses the db_admin contract.
    re.compile(r"""import[^;]*['"]@supabase/(?!ssr)"""),
    # Service-role key would be catastrophic in browser bundle.
    re.compile(r"SUPABASE_SERVICE_KEY"),
]

# `await fetch(` is the smoking gun for direct API calls.
_FETCH_USAGE_RE = re.compile(r"\bawait\s+fetch\s*\(")


# ── Validators ───────────────────────────────────────────────────────

def validate_generated_crud_file(
    file_content: str,
    *,
    expected_entity: str,
    page_type: str = "list",   # "list" | "create" | "edit"
) -> list[str]:
    """Run every static check against a generated CRUD file.

    `expected_entity` is the table.name (snake_case). The validator
    looks for it in the file as a smoke check that Claude didn't
    silently swap entities. `page_type` is informational — it
    relaxes the check set (e.g. "use client" is required for all
    three; only list view is REQUIRED to import deleteRow).
    """
    issues: list[str] = []

    if not file_content or not file_content.strip():
        return ["empty file"]

    # 1. "use client" directive (must be the first non-comment line)
    if not _has_use_client(file_content):
        issues.append('missing "use client" directive at top of file')

    # 2. AuthGuard
    if "AuthGuard" not in file_content:
        issues.append("AuthGuard not referenced in file")
    elif "@/components/AuthGuard" not in file_content:
        issues.append("AuthGuard used but not imported from @/components/AuthGuard")

    # 3. db_admin import — every page must hit at least one helper
    if "@/lib/db_admin" not in file_content:
        issues.append("missing import from @/lib/db_admin")
    else:
        # Per-page expected helper
        helper_required = {
            "list":   "listCollection",
            "create": "createRow",
            "edit":   "updateRow",  # listCollection + deleteRow are also OK
        }.get(page_type)
        if helper_required and helper_required not in file_content:
            issues.append(
                f"{page_type} page missing expected helper {helper_required!r}"
            )

    # 4. default export
    if "export default" not in file_content:
        issues.append("no default export")

    # 5. Entity name mentioned somewhere
    if expected_entity not in file_content:
        issues.append(
            f"expected entity {expected_entity!r} not mentioned in file"
        )

    # 6. TypeScript syntax
    ts_issues = _detect_typescript(file_content)
    issues.extend(ts_issues)

    # 7. Forbidden imports
    import_issues = validate_imports(file_content)
    issues.extend(import_issues)

    # 8. fetch() call — direct API access bypasses db_admin
    if _FETCH_USAGE_RE.search(file_content):
        issues.append("uses raw fetch() — must use db_admin helpers")

    # 9. Balanced JSX-ish brackets (very crude)
    bracket_issues = _check_balanced_brackets(file_content)
    issues.extend(bracket_issues)

    # 10. Top-level await is invalid in JSX page components.
    if _has_top_level_await(file_content):
        issues.append("await outside an async function")

    return issues


def validate_imports(file_content: str) -> list[str]:
    """Check for forbidden import sources."""
    issues: list[str] = []
    for pattern in _FORBIDDEN_IMPORTS:
        match = pattern.search(file_content)
        if match:
            issues.append(
                f"forbidden import / reference: {match.group(0)!r}"
            )
    return issues


# ── Internal helpers ─────────────────────────────────────────────────

def _has_use_client(text: str) -> bool:
    """True iff the file's first non-comment/blank line is a use-client
    directive (single OR double quotes, optional trailing semicolon).
    """
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("//") or line.startswith("/*"):
            continue
        return bool(re.match(r'^[\'"]use client[\'"];?$', line))
    return False


def _detect_typescript(text: str) -> list[str]:
    """Return any TS-syntax issues found. We match patterns rather
    than try to parse — false positives are acceptable here because
    the failure mode is "warn during code review", not "block the
    build".
    """
    issues: list[str] = []
    for pat in _TS_PATTERNS:
        m = pat.search(text)
        if m:
            issues.append(
                f"TypeScript-looking syntax: {m.group(0)[:60].strip()!r}"
            )
    return issues


def _check_balanced_brackets(text: str) -> list[str]:
    """Very crude balance check on (), {}, [].

    We ignore characters inside string literals — common case: a JSX
    attribute value like className="border-{primary}". The exhaustive
    fix is a real parser; this version catches the gross failures
    (missing close brace at end of file) without flagging valid code.
    """
    open_to_close = {"(": ")", "{": "}", "[": "]"}
    stack: list[str] = []
    in_string: str | None = None  # quote char, or None
    in_line_comment = False
    in_block_comment = False
    in_template = False
    i = 0
    while i < len(text):
        ch = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""

        if in_line_comment:
            if ch == "\n":
                in_line_comment = False
            i += 1
            continue
        if in_block_comment:
            if ch == "*" and nxt == "/":
                in_block_comment = False
                i += 2
                continue
            i += 1
            continue
        if in_template:
            if ch == "`":
                in_template = False
            i += 1
            continue
        if in_string is not None:
            if ch == "\\" and nxt:
                i += 2
                continue
            if ch == in_string:
                in_string = None
            i += 1
            continue

        if ch == "/" and nxt == "/":
            in_line_comment = True
            i += 2
            continue
        if ch == "/" and nxt == "*":
            in_block_comment = True
            i += 2
            continue
        if ch in ("'", '"'):
            in_string = ch
            i += 1
            continue
        if ch == "`":
            in_template = True
            i += 1
            continue

        if ch in open_to_close:
            stack.append(open_to_close[ch])
        elif ch in (")", "}", "]"):
            if not stack or stack[-1] != ch:
                return [f"unbalanced bracket near offset {i}: got {ch!r}"]
            stack.pop()
        i += 1

    if stack:
        return [f"unclosed brackets at end of file: expected {''.join(reversed(stack))!r}"]
    return []


def _has_top_level_await(text: str) -> bool:
    """Detect `await` outside any function. Strips strings + comments
    first so a literal `"await foo"` in a JSX attribute doesn't fool us.
    """
    cleaned = _strip_comments_and_strings(text)
    # Find every `await` keyword.
    for m in re.finditer(r"\bawait\b", cleaned):
        if not _is_inside_function(cleaned, m.start()):
            return True
    return False


def _strip_comments_and_strings(text: str) -> str:
    """Replace string/template/comment contents with spaces so they
    don't generate false matches. Lengths are preserved so offsets
    stay valid relative to the original text.
    """
    out = []
    i = 0
    while i < len(text):
        ch = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if ch == "/" and nxt == "/":
            while i < len(text) and text[i] != "\n":
                out.append(" ")
                i += 1
            continue
        if ch == "/" and nxt == "*":
            out.append(" "); out.append(" ")
            i += 2
            while i < len(text) and not (text[i] == "*" and text[i + 1: i + 2] == "/"):
                out.append(" "); i += 1
            out.append(" "); out.append(" ")
            i += 2
            continue
        if ch in ("'", '"', "`"):
            quote = ch
            out.append(ch); i += 1
            while i < len(text) and text[i] != quote:
                if text[i] == "\\" and i + 1 < len(text):
                    out.append(" "); out.append(" "); i += 2
                    continue
                out.append(" "); i += 1
            if i < len(text):
                out.append(text[i]); i += 1
            continue
        out.append(ch); i += 1
    return "".join(out)


def _is_inside_function(text: str, pos: int) -> bool:
    """Walk backwards counting unmatched braces and look for a
    function-ish keyword (`async function`, `=> {`). Hugely
    approximate — good enough for the "top-level await" smoke check.
    """
    depth = 0
    for j in range(pos - 1, -1, -1):
        c = text[j]
        if c == "}":
            depth += 1
        elif c == "{":
            if depth == 0:
                # Look backwards from here for an arrow or function token.
                preceding = text[max(0, j - 60):j]
                if re.search(r"async\b|=>\s*$|function\b", preceding):
                    return True
                # Still inside *some* block — keep scanning outward.
                # (Anonymous async IIFEs are handled by the regex above.)
                continue
            depth -= 1
    return False


# ── Bulk validation helper for callers ───────────────────────────────

def summarise_issues(issues: Iterable[str]) -> str:
    """Pretty multi-line error summary for log lines."""
    issues = list(issues)
    if not issues:
        return "no issues"
    return "\n".join(f"  • {i}" for i in issues)
