"""project_writer — safe workspace write path for generator output.

Carved out of project_generator.py so the write logic (path traversal
guards, symlink escape prevention, atomic replace, JS structural sanity)
can be tested and audited in isolation from the 5000-line pipeline.

Public surface:
    PROTECTED_FILES, PROTECTED_DIRS, PROTECTED_UI_DIR    — guarded paths
    write_files_from_json(json_response, workspace_path) — main entry point
    structural_sanity_check(content, rel_path)            — pre-write gate

The main caller stays ``project_generator``, which re-exports the symbols
so existing ``from app.services.project_generator import X`` lines keep
working.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

logger = logging.getLogger("lucid.project_writer")


# ── Protected paths ──────────────────────────────────────────────────────
# Files and directories the generator must never overwrite. These are
# template infrastructure (lockfiles, config, pre-built shadcn/ui) that
# exists in the skeleton and whose replacement always breaks the build.

PROTECTED_FILES: set[str] = {
    "TEMPLATE_MANIFEST.md",
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "node_modules",
    ".gitignore",
    # tailwind.config stays protected — colour customization belongs in
    # globals.css via HSL CSS variables, not in the tailwind config.
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

PROTECTED_DIRS: set[str] = {"node_modules", ".git", ".next", "dist", ".vite"}

# Pre-built shadcn/ui components live in the template. Replacing them
# causes broken imports (@base-ui / @radix-ui live only in the template).
PROTECTED_UI_DIR: str = "src/components/ui"


# ── Structural sanity for JS-like files ──────────────────────────────────

_JS_LIKE_EXTS = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")

# "I got bored, you finish it" markers the model sometimes emits. Files
# containing any of these aren't structurally wrong but are functionally
# broken, so we refuse them pre-write.
_TRUNCATION_PATTERNS = (
    "// ... rest of",
    "// ... (rest of",
    "/* ... rest of",
    "// ... (remaining",
    "// TODO: implement the rest",
    "// [...]",
    "/* [...] */",
    "// ... unchanged",
    "// ... (truncated",
)


def structural_sanity_check(content: str, rel_path: str) -> Optional[str]:
    """Fast, conservative pre-write check for JS/JSX/TS/TSX files.

    Catches the three failure modes that make downstream builds cryptic:
      1. git merge-conflict markers accidentally synthesized by the model
      2. "// ... rest of code ..." truncation placeholders
      3. unbalanced ``{}`` / ``()`` / ``[]`` (outside strings and comments)

    Returns an error string if the file should be rejected, ``None`` if OK.
    This is **not** a full parser — the real build still runs downstream. It
    just stops obviously-broken files from landing in the workspace, so the
    subsequent build failure doesn't look like a mystery.
    """
    ext = os.path.splitext(rel_path)[1].lower()
    if ext not in _JS_LIKE_EXTS:
        return None

    # 1) merge-conflict markers
    for marker in ("<<<<<<< ", "=======\n", ">>>>>>> "):
        if marker in content:
            return f"merge-conflict marker {marker.strip()!r}"

    # 2) truncation placeholders
    lowered = content.lower()
    for pat in _TRUNCATION_PATTERNS:
        if pat.lower() in lowered:
            return f"truncation placeholder {pat!r}"

    # 3) brace balance (strings + comments stripped first)
    stripped = _strip_strings_and_comments(content)
    counts = {"{}": 0, "()": 0, "[]": 0}
    pairs = {"{": "{}", "}": "{}", "(": "()", ")": "()", "[": "[]", "]": "[]"}
    for ch in stripped:
        if ch in "{([":
            counts[pairs[ch]] += 1
        elif ch in "})]":
            counts[pairs[ch]] -= 1
            if counts[pairs[ch]] < 0:
                return f"unbalanced {pairs[ch][0]!r} (extra closer)"
    for k, v in counts.items():
        if v != 0:
            return f"unbalanced {k} ({v:+d})"
    return None


def _strip_strings_and_comments(src: str) -> str:
    """Blank out JS strings, template literals, and comments.

    Not a real tokenizer — it handles backslash escapes inside strings and
    the common comment forms. Good enough for brace-balance checking.
    """
    out: list[str] = []
    i, n = 0, len(src)
    while i < n:
        ch = src[i]
        nxt = src[i + 1] if i + 1 < n else ""
        # line comment
        if ch == "/" and nxt == "/":
            while i < n and src[i] != "\n":
                i += 1
            continue
        # block comment
        if ch == "/" and nxt == "*":
            i += 2
            while i < n - 1 and not (src[i] == "*" and src[i + 1] == "/"):
                i += 1
            i += 2
            continue
        # string / template literal
        if ch in ("'", '"', "`"):
            quote = ch
            i += 1
            while i < n and src[i] != quote:
                if src[i] == "\\" and i + 1 < n:
                    i += 2
                    continue
                i += 1
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


# ── Write path ───────────────────────────────────────────────────────────

def write_files_from_json(
    json_response: dict,
    workspace_path: str,
) -> list[str]:
    """Write files from Claude's JSON response to the workspace.

    Expected format: ``{"files": [{"path": "relative/path", "content": "..."}]}``

    Returns list of file paths that were successfully written.
    Skips PROTECTED_FILES and paths outside the workspace.

    Safety properties:
      - **Per-file atomicity** — each file is written to ``{path}.lucid.tmp``
        first, then atomically renamed into place with ``os.replace``. If
        the process is killed mid-phase, files either exist fully or don't
        exist at all — no half-written partial content.
      - **Symlink escape prevention** — the real path of each file's
        parent directory must resolve to inside ``workspace_path``. Target
        paths that already exist as symlinks are refused (prevents writing
        through a symlink into a location outside the workspace).
      - **Path traversal** — rejects ``..`` segments and absolute paths.
      - **Structural sanity** — JS/JSX/TS/TSX files with merge-conflict
        markers, truncation placeholders, or unbalanced braces are
        rejected before the write.
    """
    files = json_response.get("files", [])
    if not files:
        logger.warning("No files in JSON response")
        return []

    if not isinstance(files, list):
        logger.warning(
            "write_files_from_json: 'files' is %s not a list, skipping",
            type(files).__name__,
        )
        return []

    # Resolve workspace root once — all target paths must resolve under here
    try:
        workspace_real = os.path.realpath(workspace_path)
    except OSError as exc:
        logger.error(
            "write_files_from_json: cannot resolve workspace %s: %s",
            workspace_path, exc,
        )
        return []

    written: list[str] = []

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
                    logger.warning(
                        "Unserializable non-string content for %s — skipping",
                        rel_path,
                    )
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

        abs_path = os.path.join(workspace_path, rel_path)
        parent_dir = os.path.dirname(abs_path)

        # Symlink escape check: the file's parent (after symlink resolution)
        # must be under the workspace root. Also refuse to write through an
        # existing symlink at the target path itself.
        try:
            parent_real = os.path.realpath(parent_dir)
        except OSError as exc:
            logger.warning("Cannot resolve parent dir for %s: %s", rel_path, exc)
            continue
        if not (parent_real == workspace_real or parent_real.startswith(workspace_real + os.sep)):
            logger.warning(
                "Skipping path that resolves outside workspace: %s → %s",
                rel_path, parent_real,
            )
            continue
        if os.path.islink(abs_path):
            logger.warning(
                "Skipping target that is an existing symlink (possible escape): %s",
                rel_path,
            )
            continue

        # Pre-write structural sanity check — catches merge-conflict markers,
        # truncation placeholders, and unbalanced braces in JS-like files
        # before they reach the workspace.
        sanity_err = structural_sanity_check(content, rel_path)
        if sanity_err:
            logger.warning(
                "Skipping %s — failed structural sanity (%s). "
                "The build validator would have caught this; rejecting now "
                "keeps the workspace clean for the next phase to regenerate.",
                rel_path, sanity_err,
            )
            continue

        # Atomic write: staged file → os.replace → target
        tmp_path = abs_path + ".lucid.tmp"
        try:
            os.makedirs(parent_dir, exist_ok=True)
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(tmp_path, abs_path)  # atomic on POSIX within same fs
            written.append(rel_path)
        except Exception as exc:
            # Clean up the staged file; the real target is untouched because
            # os.replace either succeeded or never ran.
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass
            logger.error("Failed to write %s: %s", rel_path, exc)

    logger.info("Wrote %d / %d files to %s", len(written), len(files), workspace_path)
    return written
