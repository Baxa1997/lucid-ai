"""pipeline/step3b_edit_intent.py — Structured edit-intent extraction.

Runs between Step 3 (classify) and Step 4 (explore) for follow-up edits
on platform-owned repos. Parses the user's prompt into a structured
target so file-relevance picking can be deterministic instead of relying
on Gemini to walk a large tree and guess.

Why this exists
---------------
The pre-existing edit flow has Gemini look at the full file tree and a
freeform task string, then return the files it thinks are relevant. For
detailed prompts ("the testimonials cards on /about should have shadow-lg
instead of shadow-md") that's wasteful — the prompt already names the
page, the section, AND a literal class string. A deterministic grep +
small Flash extraction is cheaper, faster, and more precise.

Output is additive: when extraction succeeds with confidence, downstream
stages narrow their candidate file set; when it fails or is ambiguous,
the legacy Gemini file-filter still runs and nothing changes.

Gate
----
EDIT_INTENT_EXTRACTOR_ENABLED=0 to disable (default ON). Fail-open: any
extractor failure returns an empty EditIntent so the orchestrator falls
through to legacy behaviour.

Cost
----
~$0.001 per edit (gemini-3.5-flash, ~1 KB in, ~300 tokens out, ~1s).
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ── Public type ──────────────────────────────────────────────────────────


@dataclass
class EditIntent:
    """Structured representation of what the user wants edited.

    All list fields default to empty so a "no-op" intent (extractor
    disabled or failed) collapses cleanly through downstream logic.
    """
    target_pages:        list[str] = field(default_factory=list)
    target_sections:     list[str] = field(default_factory=list)
    target_components:   list[str] = field(default_factory=list)
    # Files the extractor (or grep over literal_anchors) is confident
    # about. Downstream stages treat this as the candidate set when
    # `scope == "narrow"` and confidence is high.
    candidate_files:     list[str] = field(default_factory=list)
    # change_type ∈ {"style", "content", "layout", "behavior", "feature", ""}
    change_type:         str = ""
    # Verbatim text strings (class names, copy snippets, JSX attrs) the
    # user named that we can grep for in the workspace.
    literal_anchors:     list[str] = field(default_factory=list)
    # scope ∈ {"narrow", "wide", "ambiguous"}.
    #   narrow     → ≤ ~3 files, one section/page, surgical
    #   wide       → multi-file refactor, big redesign
    #   ambiguous  → not enough to act on without a clarify question
    scope:               str = "ambiguous"
    confidence:          int = 0  # 0..100
    # True when we ran the extractor and got back a parseable response.
    # Distinguishes "extractor ran cleanly with low confidence" from
    # "extractor disabled / failed".
    extracted:           bool = False

    @property
    def is_actionable(self) -> bool:
        """Confident-narrow intent that downstream stages should honour."""
        return (
            self.extracted
            and self.scope == "narrow"
            and self.confidence >= 70
            and len(self.candidate_files) >= 1
        )


# ── Feature flag ─────────────────────────────────────────────────────────


def _enabled() -> bool:
    """Return whether the workspace-grounded edit extractor is enabled.

    It only narrows routing when the result is high-confidence and grounded in
    real workspace vocab or grep matches. Set ``EDIT_INTENT_EXTRACTOR_ENABLED=0``
    to fall back to the legacy full-tree Gemini selector.
    """
    raw = os.environ.get("EDIT_INTENT_EXTRACTOR_ENABLED", "1").strip().lower()
    return raw in ("1", "true", "yes", "on")


# ── Workspace vocab builder ──────────────────────────────────────────────

# Cap each vocab list so the Flash prompt stays small. Beyond ~30 entries
# the extractor adds latency without improving accuracy.
_MAX_VOCAB = 40

# Directories never worth scanning for vocab.
_SKIP_DIRS = {
    "node_modules", ".git", "dist", "build", ".next", ".turbo",
    "__pycache__", ".cache", "coverage", ".vercel",
}

# Page-file globs vary per stack. We match the common patterns we
# generate (Next.js App Router, plus the admin Vite shell).
_PAGE_FILE_RE = re.compile(
    r"src/app(?:/[^/]+)*?/page\.(?:js|jsx|ts|tsx)$"
)
_VITE_PAGE_RE = re.compile(
    r"src/pages/[A-Za-z0-9_/-]+\.(?:js|jsx|ts|tsx)$"
)

_SECTION_FILE_RE = re.compile(
    r"src/components/(?:sections|pages/[^/]+)/[A-Za-z0-9_]+\.(?:jsx|tsx)$"
)
_COMPONENT_FILE_RE = re.compile(
    r"src/components/[A-Za-z0-9_/-]+\.(?:jsx|tsx)$"
)


def _walk_workspace_paths(workspace_path: str) -> list[str]:
    """Collect repo-relative file paths up to a budget.

    Single pass, depth-limited skip-dir filter. Soft-caps at 4000
    entries — past that, the extractor's vocab is already overflowed.
    """
    out: list[str] = []
    for root, dirs, files in os.walk(workspace_path):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for f in files:
            full = os.path.join(root, f)
            rel = os.path.relpath(full, workspace_path).replace(os.sep, "/")
            out.append(rel)
            if len(out) >= 4000:
                return out
    return out


def _route_from_page_path(rel_path: str) -> str:
    """Convert ``src/app/about/page.js`` → ``/about`` (Next.js App Router).

    Returns "/" for the root page; "" when the path isn't a page file
    we recognise.
    """
    if not _PAGE_FILE_RE.match(rel_path):
        return ""
    # Strip "src/app/" prefix and "/page.{ext}" suffix.
    body = rel_path[len("src/app/"):]
    body = re.sub(r"/page\.(?:js|jsx|ts|tsx)$", "", body)
    if not body:
        return "/"
    # Drop Next.js route groups: (marketing) etc.
    parts = [p for p in body.split("/") if p and not (p.startswith("(") and p.endswith(")"))]
    if not parts:
        return "/"
    return "/" + "/".join(parts)


def _build_vocab(workspace_path: str) -> dict[str, list[str]]:
    """Index the workspace into the four lists the extractor cares about.

    Lists are bounded by ``_MAX_VOCAB``. When a list overflows, we keep
    the first N entries — which happen to be the highest-priority surfaces
    on every template we ship (Next.js App Router walks the marketing
    routes first, components dir is alphabetical so common section names
    surface early).
    """
    routes:        list[str] = []
    section_files: list[str] = []
    component_files: list[str] = []
    content_keys:  list[str] = []

    paths = _walk_workspace_paths(workspace_path)

    for rel in paths:
        if len(routes) < _MAX_VOCAB:
            route = _route_from_page_path(rel)
            if route:
                routes.append(route)
            elif _VITE_PAGE_RE.match(rel):
                # Vite SPA admin: /<basename without extension>
                base = os.path.splitext(os.path.basename(rel))[0]
                routes.append(f"/{base.lower()}")
        if _SECTION_FILE_RE.match(rel) and len(section_files) < _MAX_VOCAB:
            section_files.append(rel)
        elif _COMPONENT_FILE_RE.match(rel) and len(component_files) < _MAX_VOCAB:
            component_files.append(rel)

    # Content keys: walk one JSON content file if it exists. Cheap; lets
    # the extractor point at content.json paths when the user is editing
    # a brand-name, headline, etc.
    landing_json = os.path.join(workspace_path, "src", "content", "landing.json")
    if os.path.isfile(landing_json):
        try:
            with open(landing_json, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            content_keys = _flatten_keys(data, max_keys=_MAX_VOCAB)
        except Exception as exc:
            logger.debug("step3b_edit_intent: landing.json parse skipped: %s", exc)

    return {
        "routes":           routes,
        "section_files":    section_files,
        "component_files":  component_files,
        "content_keys":     content_keys,
        # Kept out of the LLM prompt. Used only after parsing so target_files
        # cannot smuggle hallucinated src/... paths into the edit pipeline.
        "all_files":        paths[:4000],
    }


def _flatten_keys(node: Any, max_keys: int, prefix: str = "") -> list[str]:
    """Flatten a content JSON into dot-paths so the extractor can name
    them. Stops at ``max_keys`` so giant content files don't bloat the
    prompt."""
    out: list[str] = []
    if isinstance(node, dict):
        for k, v in node.items():
            path = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, (dict, list)):
                out.extend(_flatten_keys(v, max_keys - len(out), path))
            else:
                out.append(path)
            if len(out) >= max_keys:
                break
    elif isinstance(node, list):
        # Index lists by [0] so the extractor knows it's a collection.
        if node:
            out.extend(_flatten_keys(node[0], max_keys - len(out), prefix + "[0]"))
    return out[:max_keys]


# ── Literal-anchor grep ──────────────────────────────────────────────────


# Files we never grep for literal anchors. Same shape as the explore-step
# skip list — keeps the search bounded and avoids matching unrelated docs.
_GREP_SKIP_DIRS = _SKIP_DIRS | {".lucid"}
_GREP_TEXT_EXTS = {
    ".js", ".jsx", ".ts", ".tsx", ".css", ".scss", ".json", ".md", ".mdx", ".html",
}
_GREP_FILE_BUDGET = 600   # Max files scanned per anchor.
_GREP_MATCH_BUDGET = 10   # Max matches retained per anchor.


def _grep_anchors(
    workspace_path: str,
    anchors: list[str],
) -> list[str]:
    """Return relative paths of files containing any literal anchor.

    Deduplicated, capped at ``_GREP_MATCH_BUDGET`` per anchor. Anchors
    shorter than 4 chars are skipped — too noisy ("the" matches every
    file).
    """
    if not anchors:
        return []

    needles = [a for a in (s.strip() for s in anchors) if len(a) >= 4]
    if not needles:
        return []

    hits: dict[str, int] = {}
    seen = 0
    for root, dirs, files in os.walk(workspace_path):
        dirs[:] = [d for d in dirs if d not in _GREP_SKIP_DIRS]
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext not in _GREP_TEXT_EXTS:
                continue
            seen += 1
            if seen > _GREP_FILE_BUDGET:
                return list(hits.keys())
            full = os.path.join(root, f)
            try:
                with open(full, "r", encoding="utf-8", errors="ignore") as fh:
                    text = fh.read()
            except Exception:
                continue
            for needle in needles:
                if needle in text:
                    rel = os.path.relpath(full, workspace_path).replace(os.sep, "/")
                    hits.setdefault(rel, 0)
                    hits[rel] += 1
                    if len(hits) >= _GREP_MATCH_BUDGET * len(needles):
                        return list(hits.keys())
                    break
    return list(hits.keys())


# ── Public entry point ───────────────────────────────────────────────────


# Extractor system prompt — kept short. The vocab block is the real
# content; we lean on Gemini's instruction-following for JSON output.
_SYSTEM_PROMPT = """\
You are a code-edit intent parser. The user is editing an existing
project that we generated. Your job: identify exactly what they want
changed so a downstream agent can edit the minimum set of files.

You are given:
  • the user's edit request
  • the project's actual file structure (routes, section components,
    component files, content keys) — every name you return MUST come
    from these lists

Rules
  1. Only return route/section/component values that appear in the
     supplied vocab. Inventing a name produces a wrong-file edit.
  2. ``target_files`` should be the smallest set that covers the
     user's ask. Prefer 1-3 files. Empty list is valid.
  3. ``literal_anchors`` are verbatim strings the user named (class
     names like "shadow-md", quoted copy, attribute values). Use these
     when the change is a string swap.
  4. ``scope``:
       narrow     — one page or one section, surgical change
       wide       — touches many sections / global tokens / palette
       ambiguous  — user wasn't specific enough to safely act on
     Adding a new page/screen/route is WIDE unless the user explicitly says
     it is a tiny text-only stub, because navigation/router/layout files also
     need to be checked.
  5. ``change_type``: pick the closest of style | content | layout |
     behavior | feature | "".
  6. ``confidence`` (0-100): how sure are you that following this plan
     will land the right edit? Be conservative — under 70 means the
     legacy file-walker will run.

Return ONLY this JSON shape (no markdown, no prose):

{
  "target_pages":      [<route strings from vocab.routes>],
  "target_sections":   [<section names — basename, lowercase>],
  "target_components": [<component names — basename, no extension>],
  "target_files":      [<repo-relative paths from vocab>],
  "change_type":       "<style|content|layout|behavior|feature>",
  "literal_anchors":   [<verbatim strings from the user's prompt>],
  "scope":             "<narrow|wide|ambiguous>",
  "confidence":        <0-100>
}
"""


_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "target_pages":      {"type": "ARRAY", "items": {"type": "STRING"}},
        "target_sections":   {"type": "ARRAY", "items": {"type": "STRING"}},
        "target_components": {"type": "ARRAY", "items": {"type": "STRING"}},
        "target_files":      {"type": "ARRAY", "items": {"type": "STRING"}},
        "change_type":       {"type": "STRING"},
        "literal_anchors":   {"type": "ARRAY", "items": {"type": "STRING"}},
        "scope":             {"type": "STRING"},
        "confidence":        {"type": "INTEGER"},
    },
    "required": ["scope", "confidence"],
}


_VALID_CHANGE_TYPES = {"style", "content", "layout", "behavior", "feature", ""}
_VALID_SCOPES = {"narrow", "wide", "ambiguous"}


async def extract_edit_intent(
    *,
    task: str,
    workspace_path: str,
    classification: dict[str, Any],
    websocket: Any = None,
    timeout_s: float = 18.0,
) -> EditIntent:
    """Parse the user's edit request into a structured intent.

    Always returns an ``EditIntent`` — empty / non-actionable on any
    failure so the caller can keep the legacy file walk as the safety
    net. Mutates nothing on disk.
    """
    if not _enabled():
        logger.info("step3b_edit_intent: SKIPPED (EDIT_INTENT_EXTRACTOR_ENABLED=0)")
        return EditIntent()

    clean_task = (task or "").strip()
    if not clean_task:
        return EditIntent()

    try:
        vocab = _build_vocab(workspace_path)
    except Exception as exc:
        logger.warning("step3b_edit_intent: vocab build failed (%s)", exc)
        return EditIntent()

    # Drop entirely if there's nothing recognisable to anchor against —
    # e.g. an unsupported template. The legacy walker will still run.
    if not any(vocab.values()):
        logger.info("step3b_edit_intent: empty vocab — skipping extractor")
        return EditIntent()

    user_prompt = (
        f"USER EDIT REQUEST:\n{clean_task}\n\n"
        f"CLASSIFICATION HINT: task_type={classification.get('task_type', '')!r} "
        f"complexity={classification.get('complexity', '')!r}\n\n"
        f"PROJECT VOCAB:\n"
        f"  routes:           {vocab['routes']}\n"
        f"  section_files:    {vocab['section_files']}\n"
        f"  component_files:  {vocab['component_files'][:30]}\n"
        f"  content_keys:     {vocab['content_keys'][:30]}\n"
    )

    try:
        from app.services.landing_gemini import structured_distill
    except Exception as exc:
        logger.warning("step3b_edit_intent: landing_gemini import failed (%s)", exc)
        return EditIntent()

    try:
        raw = await structured_distill(
            _SYSTEM_PROMPT + "\n\n" + user_prompt,
            timeout_s,
            label="edit_intent",
            response_schema=_RESPONSE_SCHEMA,
            max_tokens=512,
            temperature=0.1,
            model="gemini-3.5-flash",
        )
    except Exception as exc:
        logger.warning("step3b_edit_intent: extractor call failed (%s)", exc)
        return EditIntent()

    intent = _parse(raw, vocab)
    if intent is None:
        logger.info("step3b_edit_intent: extractor returned unusable JSON — falling back")
        return EditIntent()

    # Augment candidate_files with grep results for literal_anchors.
    # Anchored files are highly likely to be the right targets — the user
    # quoted a verbatim class / copy string and only N files match it.
    if intent.literal_anchors:
        try:
            anchored = _grep_anchors(workspace_path, intent.literal_anchors)
        except Exception as exc:
            logger.warning("step3b_edit_intent: anchor grep failed (%s)", exc)
            anchored = []
        for rel in anchored:
            if rel not in intent.candidate_files:
                intent.candidate_files.append(rel)
        # When grep narrowed to a tight set and the extractor previously
        # said "wide" only because it wasn't sure, promote to narrow.
        if anchored and 1 <= len(anchored) <= 3 and intent.scope == "wide":
            logger.info(
                "step3b_edit_intent: anchor grep narrowed wide→narrow (matches=%d)",
                len(anchored),
            )
            intent.scope = "narrow"
        # Defensive downgrade — if the extractor produced anchors but NONE
        # of them grep-matched any file, the anchors are almost certainly
        # hallucinated (Gemini guessing a class name or copy snippet that
        # doesn't exist). Don't let an actionable claim through in that
        # state; force the legacy file-walk to run instead.
        if not anchored and intent.is_actionable:
            logger.info(
                "step3b_edit_intent: %d anchor(s) named but none matched any file — "
                "downgrading scope to ambiguous",
                len(intent.literal_anchors),
            )
            intent.scope = "ambiguous"
            intent.confidence = min(intent.confidence, 50)

    # Best-effort progress beacon. Keeps the UI from looking idle
    # between classify and explore.
    if websocket is not None and intent.is_actionable:
        try:
            await websocket.send_json({
                "type": "progress",
                "message": (
                    f"🎯 Edit target locked: {len(intent.candidate_files)} file"
                    f"{'s' if len(intent.candidate_files) != 1 else ''} "
                    f"({intent.confidence}% confident)"
                ),
            })
        except Exception:
            pass

    logger.info(
        "step3b_edit_intent: ok — scope=%s confidence=%d files=%d pages=%s "
        "sections=%s anchors=%d change=%s actionable=%s",
        intent.scope, intent.confidence, len(intent.candidate_files),
        intent.target_pages, intent.target_sections,
        len(intent.literal_anchors), intent.change_type, intent.is_actionable,
    )
    return intent


# ── Parser + validators ──────────────────────────────────────────────────


def _parse(raw: str, vocab: dict[str, list[str]]) -> EditIntent | None:
    """Parse Gemini's JSON response and clamp every field against vocab.

    Returns ``None`` when the response can't be coerced into the right
    shape; the caller treats that as a no-op extraction.
    """
    if not isinstance(raw, str) or not raw.strip():
        return None

    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```\s*$", "", text)

    try:
        data = json.loads(text)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None

    scope = (data.get("scope") or "").strip().lower()
    if scope not in _VALID_SCOPES:
        scope = "ambiguous"
    try:
        confidence = int(data.get("confidence", 0) or 0)
    except (TypeError, ValueError):
        confidence = 0
    confidence = max(0, min(100, confidence))

    change_type = (data.get("change_type") or "").strip().lower()
    if change_type not in _VALID_CHANGE_TYPES:
        change_type = ""

    target_pages = _clamp_list(
        data.get("target_pages"), vocab.get("routes") or [],
        normalize=_norm_route,
    )

    # Section names are matched against basenames of section_files; the
    # extractor returns "Testimonials" or "testimonials" — accept both.
    section_basenames = {
        os.path.splitext(os.path.basename(p))[0].lower()
        for p in (vocab.get("section_files") or [])
    }
    target_sections = _clamp_list(
        data.get("target_sections"), list(section_basenames),
        normalize=lambda s: s.strip().lower(),
    )

    component_basenames = {
        os.path.splitext(os.path.basename(p))[0]
        for p in (vocab.get("component_files") or [])
    }
    target_components = _clamp_list(
        data.get("target_components"), list(component_basenames),
        normalize=lambda s: s.strip(),
    )

    # Candidate files: extractor's target_files filtered against the real
    # vocab. We never trust a path that didn't come from `_walk_workspace_paths`.
    real_files: set[str] = set(vocab.get("all_files") or [])
    real_files.update(vocab.get("section_files") or [])
    real_files.update(vocab.get("component_files") or [])
    candidate_files: list[str] = []
    raw_files = data.get("target_files") or []
    if isinstance(raw_files, list):
        for f in raw_files:
            if not isinstance(f, str):
                continue
            rel = f.strip().lstrip("/")
            # Only allow files observed in the real workspace. This prevents
            # an LLM response from routing Codex toward invented src/... paths.
            if rel in real_files:
                if rel not in candidate_files:
                    candidate_files.append(rel)

    literal_anchors: list[str] = []
    raw_anchors = data.get("literal_anchors") or []
    if isinstance(raw_anchors, list):
        for a in raw_anchors:
            if isinstance(a, str) and a.strip():
                literal_anchors.append(a.strip())

    return EditIntent(
        target_pages=target_pages,
        target_sections=target_sections,
        target_components=target_components,
        candidate_files=candidate_files,
        change_type=change_type,
        literal_anchors=literal_anchors,
        scope=scope,
        confidence=confidence,
        extracted=True,
    )


def _norm_route(s: str) -> str:
    """Normalize a route the extractor returned: leading slash, no
    trailing slash, lowercase."""
    s = (s or "").strip().lower()
    if not s:
        return ""
    if not s.startswith("/"):
        s = "/" + s
    if len(s) > 1 and s.endswith("/"):
        s = s[:-1]
    return s


def _clamp_list(
    raw: Any,
    allowed: list[str],
    *,
    normalize,
) -> list[str]:
    """Return the subset of ``raw`` that exists in ``allowed`` after
    normalization. Drops anything that doesn't match the vocab exactly
    so the extractor can't smuggle invented names downstream."""
    if not isinstance(raw, list):
        return []
    allowed_set = {normalize(x) for x in allowed if isinstance(x, str)}
    out: list[str] = []
    for v in raw:
        if not isinstance(v, str):
            continue
        n = normalize(v)
        if n in allowed_set and n not in out:
            out.append(n)
    return out
