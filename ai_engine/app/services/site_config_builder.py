"""Deterministic src/config/site.js builder.

Replaces the LLM's site-config rewrite in Phase 1 with a pure-Python
function that emits a guaranteed-correct ``siteConfig`` export from the
project_schema's brand block.

Why deterministic: Phase 1 is one Claude call that produces six file
groups (CSS, site, navigation, layout, main page, router). Removing
site.js from that scope shrinks Phase 1's output token budget by ~15%
(30-50s wall clock on a 5-page consumer project) without losing any
fidelity — site.js is plain metadata derived from fields the schema
already guarantees (brand.name, tagline, description, domain).

Pure module — no I/O, no LLM calls, no async. Caller writes the
returned string to ``src/config/site.js``.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


# ── Slug helpers ───────────────────────────────────────────────────────
_SLUG_NON_WORD = re.compile(r"[^a-z0-9]+")


def _slugify(value: str) -> str:
    """Lowercase ASCII slug. 'Stonemill Bakery' → 'stonemill-bakery'."""
    s = (value or "").strip().lower()
    s = _SLUG_NON_WORD.sub("-", s)
    return s.strip("-")


def _logo_text(name: str) -> str:
    """Short uppercase wordmark. Prefers the full name when ≤12 chars,
    otherwise the first word — falls back to first 8 chars."""
    n = (name or "").strip()
    if not n:
        return "BRAND"
    if len(n) <= 12:
        return n.upper()
    first = n.split()[0]
    if len(first) <= 12:
        return first.upper()
    return n[:8].upper()


def _truncate(text: str, limit: int) -> str:
    t = (text or "").strip()
    if len(t) <= limit:
        return t
    cut = t[:limit].rsplit(" ", 1)[0]
    return cut.rstrip(",.;:") + "…"


def _coerce_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _js_str(value: str) -> str:
    """Render a Python str as a JS double-quoted string literal with proper
    escaping. ensure_ascii=False keeps unicode (… é à) as literals rather
    than \\u escapes — both are valid JS, the literal form is just nicer
    to read in the source file."""
    return json.dumps(value, ensure_ascii=False)


# ── Main builder ───────────────────────────────────────────────────────
def build_site_config(project_schema: dict, fallback_description: str = "") -> str:
    """Render src/config/site.js as a JS source string.

    Inputs:
      - project_schema: validated schema dict; reads brand.{name, tagline,
        description, domain}.
      - fallback_description: user's original prompt; used only when
        brand.description is empty.

    Output shape (matches the template skeleton plus tagline):
        export const siteConfig = {
          name: "...",
          tagline: "...",
          description: "...",
          url: "https://<slug>.com",
          logoText: "...",
          ogImage: "/og-image.png",
        };

    Tagline is included only when present in the schema — components that
    expect the template's exact 5-key shape still see those 5 keys
    untouched. Downstream Phase 2/3 components that read additional
    contact / social fields from siteConfig will continue to render
    normally; those fields just won't be present, and components that
    read them via optional chaining (siteConfig.contact?.email) won't
    crash. We deliberately do NOT invent placeholder phone / address
    values — fake "(555) 123-4567" data leaks into rendered pages and
    looks unprofessional.
    """
    brand = project_schema.get("brand") or {}
    name = _coerce_str(brand.get("name")) or "Project"
    tagline = _coerce_str(brand.get("tagline"))
    description = _coerce_str(brand.get("description")) or _coerce_str(fallback_description)
    description = _truncate(description, 200)

    slug = _slugify(name) or "project"
    url = f"https://{slug}.com"
    logo_text = _logo_text(name)

    # json.dumps gives us correct JS string escaping (quotes, backslashes,
    # unicode) for free. We emit double-quoted strings to match the
    # skeleton's existing convention.
    fields: list[tuple[str, str]] = [
        ("name", _js_str(name)),
        ("description", _js_str(description)),
        ("url", _js_str(url)),
        ("logoText", _js_str(logo_text)),
        ("ogImage", _js_str("/og-image.png")),
    ]
    if tagline:
        # Insert tagline right after name to match human-written ordering.
        fields.insert(1, ("tagline", _js_str(tagline)))

    body = ",\n  ".join(f"{k}: {v}" for k, v in fields)
    return f"export const siteConfig = {{\n  {body},\n}};\n"
