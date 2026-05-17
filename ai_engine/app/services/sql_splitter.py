"""Lexer-aware splitter for Postgres migration files.

`split_sql` walks the input one character at a time, tracking four
contexts that can contain a stray `;`:

  • `$$ … $$` dollar-quoted blocks (PL/pgSQL function bodies)
  • `-- line comments` (rest-of-line)
  • `/* block comments */`
  • `'string literals'` (with `''` doubled-quote escape)

A statement boundary is a `;` outside ALL of those contexts. The
naive `sql.split(";")` would shred a function body or a line-comment
sentence that includes `;`. This splitter doesn't.

Used by `scripts/apply_migration_026.py` and
`scripts/apply_migration_027.py`. NOT used by
`tenant_sql_generator.split_sql_statements`, which is tuned for the
auto-generated tenant DDL (no function bodies, no embedded `;` in
comments) and is intentionally simpler.
"""
from __future__ import annotations

import re


_COMMENT_RE = re.compile(r"^\s*(--.*$|/\*[\s\S]*?\*/)\s*", re.MULTILINE)


def _is_only_comments(stmt: str) -> bool:
    """True if `stmt` strips down to nothing after removing comments."""
    return not _COMMENT_RE.sub("", stmt).strip()


def split_sql(sql: str) -> list[str]:
    """Split a Postgres migration into top-level statements.

    Returns the non-comment statements in order. Each returned string
    is stripped; the trailing `;` is included so the result can be
    fed directly into `EXECUTE`.
    """
    statements: list[str] = []
    buf: list[str] = []
    in_dollar = False
    in_line   = False
    in_block  = False
    in_string = False
    i = 0
    n = len(sql)
    while i < n:
        ch  = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""

        # End-of-line ends a line comment.
        if in_line:
            buf.append(ch)
            if ch == "\n":
                in_line = False
            i += 1
            continue

        # Block-comment close.
        if in_block:
            buf.append(ch)
            if ch == "*" and nxt == "/":
                buf.append(nxt)
                i += 2
                in_block = False
                continue
            i += 1
            continue

        # String-literal close. Handle `''` doubled-quote escape.
        if in_string:
            buf.append(ch)
            if ch == "'":
                if nxt == "'":
                    buf.append(nxt)
                    i += 2
                    continue
                in_string = False
            i += 1
            continue

        # Dollar-quoted block — closes when we hit another `$$`.
        if in_dollar:
            if ch == "$" and nxt == "$":
                buf.append("$$")
                in_dollar = False
                i += 2
                continue
            buf.append(ch)
            i += 1
            continue

        # OUT of every special context — check for openings + the
        # statement terminator.
        if ch == "-" and nxt == "-":
            in_line = True
            buf.append("--")
            i += 2
            continue
        if ch == "/" and nxt == "*":
            in_block = True
            buf.append("/*")
            i += 2
            continue
        if ch == "'":
            in_string = True
            buf.append(ch)
            i += 1
            continue
        if ch == "$" and nxt == "$":
            in_dollar = True
            buf.append("$$")
            i += 2
            continue

        buf.append(ch)
        if ch == ";":
            stmt = "".join(buf).strip()
            if stmt and not _is_only_comments(stmt):
                statements.append(stmt)
            buf = []
        i += 1

    # Trailing fragment (rare — properly formatted migrations end with `;`).
    tail = "".join(buf).strip()
    if tail and not _is_only_comments(tail):
        statements.append(tail)

    return statements
