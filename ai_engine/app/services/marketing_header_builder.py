"""Deterministic MarketingHeader.jsx builder.

Replaces the LLM's unreliable header rewrite with a pure-Python function that
emits a guaranteed-correct Next.js header using:
  - brand_name + brand_mark spec from Design Director
  - navigation items from project_schema
  - domain-appropriate CTA verb/link derived from a small mapping

Why deterministic: Claude occasionally keeps the template's default navbar
(Sign In / Get Started / no logo) even when Phase 1 tells it to rewrite.
Direct-writing eliminates that failure mode entirely.

Pure module — no I/O, no LLM calls, no async. Caller is responsible for
writing the returned string to disk.
"""

from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)


# ───────────────────────────────────────────────────────────────
#  Fallback parser — reconstruct brand_mark dict from the
#  ===BRAND_MARK=== block that design_system_builder writes into
#  the research text. Used when _design is unavailable (e.g. the
#  research came from cache and we skipped Design Director).
#  Block format (see design_system_builder._render_brand_mark):
#    treatment: wordmark
#    font: Fraunces
#    weight: 700
#    ...
# ───────────────────────────────────────────────────────────────
_BRAND_MARK_FIELDS = (
    "treatment", "font", "weight", "style", "case",
    "tracking", "icon", "color_token", "size_desktop", "placement",
)


def parse_brand_mark_from_research(research: str) -> dict:
    """Extract brand_mark fields from the ===BRAND_MARK=== block in research.

    Returns an empty dict if the block is missing or unparseable.
    """
    if not research:
        return {}
    # Match the block content between ===BRAND_MARK=== and the next ===header===
    # (or end of string).
    m = re.search(
        r"===BRAND_MARK===\s*\n(.*?)(?=\n===[A-Z_]+===|\Z)",
        research,
        flags=re.DOTALL,
    )
    if not m:
        return {}
    block = m.group(1)
    out: dict[str, str] = {}
    for line in block.splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip().lower()
        if key in _BRAND_MARK_FIELDS:
            out[key] = val.strip()
    return out


# ───────────────────────────────────────────────────────────────
#  CTA mapping — domain-appropriate primary action verb + link.
#  Ordered: more-specific matches first; first substring match wins.
# ───────────────────────────────────────────────────────────────
_CTA_MAP: tuple[tuple[str, tuple[str, str]], ...] = (
    ("coffee",      ("Order Online", "/menu")),
    ("cafe",        ("Order Online", "/menu")),
    ("café",        ("Order Online", "/menu")),
    ("restaurant",  ("Reserve a Table", "/reservations")),
    ("bakery",      ("Order Online", "/menu")),
    ("bar",         ("Reserve a Table", "/reservations")),
    ("hotel",       ("Book Your Stay", "/book")),
    ("travel",      ("Book Your Stay", "/book")),
    ("salon",       ("Book Appointment", "/book")),
    ("spa",         ("Book Appointment", "/book")),
    ("barber",      ("Book Appointment", "/book")),
    ("fitness",     ("Book a Class", "/classes")),
    ("gym",         ("Start Free Week", "/join")),
    ("yoga",        ("Book a Class", "/classes")),
    ("pilates",     ("Book a Class", "/classes")),
    ("real estate", ("Browse Listings", "/listings")),
    ("realty",      ("Browse Listings", "/listings")),
    ("portfolio",   ("Start a Project", "/contact")),
    ("agency",      ("Start a Project", "/contact")),
    ("freelance",   ("Get in Touch", "/contact")),
    ("wedding",     ("Check Availability", "/book")),
    ("venue",       ("Check Availability", "/book")),
    ("event",       ("Check Availability", "/book")),
    ("nonprofit",   ("Donate", "/donate")),
    ("charity",     ("Donate", "/donate")),
    ("automotive",  ("Browse Inventory", "/inventory")),
    ("dealer",      ("Browse Inventory", "/inventory")),
    ("blog",        ("Subscribe", "/subscribe")),
    ("magazine",    ("Subscribe", "/subscribe")),
    ("ecommerce",   ("Shop Now", "/shop")),
    ("shop",        ("Shop Now", "/shop")),
    ("retail",      ("Shop Now", "/shop")),
)

_DEFAULT_CTA: tuple[str, str] = ("Get Started", "/signup")
_SAAS_CTA:    tuple[str, str] = ("Start Free", "/signup")


def _derive_cta(domain: str, archetype: str) -> tuple[str, str]:
    dl = (domain or "").lower()
    for key, cta in _CTA_MAP:
        if key in dl:
            return cta
    al = (archetype or "").lower()
    if "saas" in al or "b2b" in al or "saas_app" in al:
        return _SAAS_CTA
    return _DEFAULT_CTA


# ───────────────────────────────────────────────────────────────
#  Brand mark → Tailwind class mapping.
#  Every field in brand_mark is a string spec; we map it to a safe
#  utility class so the output is Tailwind-only (no inline styles).
# ───────────────────────────────────────────────────────────────
def _weight_class(weight: str) -> str:
    w = (weight or "").strip().lower()
    return {
        "400": "font-normal",   "normal":     "font-normal",
        "500": "font-medium",   "medium":     "font-medium",
        "600": "font-semibold", "semibold":   "font-semibold",
        "700": "font-bold",     "bold":       "font-bold",
        "800": "font-extrabold","extrabold":  "font-extrabold",
        "900": "font-black",    "black":      "font-black",
    }.get(w, "font-bold")


def _case_class(case: str) -> str:
    c = (case or "").strip().lower()
    return {
        "uppercase": "uppercase",
        "lowercase": "lowercase",
        "title":     "capitalize",
        "as-typed":  "normal-case",
    }.get(c, "normal-case")


def _tracking_class(tracking: str) -> str:
    t = (tracking or "").strip()
    if not t:
        return "tracking-tight"
    if t.startswith("tracking-"):
        return t
    return f"tracking-[{t}]"


def _size_class(size_desktop: str) -> str:
    s = (size_desktop or "").strip().lower()
    if s.endswith("px"):
        try:
            px = int(float(s[:-2]))
            if px <= 16: return "text-base"
            if px <= 18: return "text-lg"
            if px <= 22: return "text-xl"
            if px <= 28: return "text-2xl"
            return "text-3xl"
        except ValueError:
            pass
    return "text-xl"


_COLOR_CLASS_MAP: dict[str, str] = {
    "foreground":      "text-foreground",
    "primary":         "text-primary",
    "accent":          "text-accent-foreground",
    "card_foreground": "text-card-foreground",
}


def _render_brand(brand_name: str, brand_mark: dict) -> str:
    """Return the JSX for the left-side brand block (wraps in <Link href='/'>)."""
    treatment    = (brand_mark.get("treatment") or "wordmark").lower()
    weight_cls   = _weight_class(brand_mark.get("weight", "700"))
    case_cls     = _case_class(brand_mark.get("case", "as-typed"))
    tracking_cls = _tracking_class(brand_mark.get("tracking", "tracking-tight"))
    size_cls     = _size_class(brand_mark.get("size_desktop", "20px"))
    color_cls    = _COLOR_CLASS_MAP.get(
        (brand_mark.get("color_token") or "foreground").lower(),
        "text-foreground",
    )

    # JSON-escape the brand name so it's safe as a JSX string literal.
    name_jsx = json.dumps(brand_name or "Brand")  # e.g. "\"Kiln Roasters\""

    wordmark_span = (
        f'<span className="{size_cls} {weight_cls} {case_cls} {tracking_cls} {color_cls}">'
        f"{{{name_jsx}}}</span>"
    )

    if treatment == "monogram":
        letter = ((brand_name or "B").strip() or "B")[:1].upper()
        letter_jsx = json.dumps(letter)
        return (
            '<Link href="/" className="flex items-center gap-2">'
            '<span aria-hidden="true" '
            'className="inline-flex h-9 w-9 items-center justify-center rounded-md '
            'bg-primary text-primary-foreground font-bold">'
            f"{{{letter_jsx}}}</span>"
            f"{wordmark_span}"
            "</Link>"
        )

    if treatment in ("icon_plus_wordmark", "icon_only"):
        # Sparkles is a neutral, cross-domain lucide icon. Wordmark is kept even
        # for icon_only to preserve accessibility — screen readers need the name.
        return (
            '<Link href="/" className="flex items-center gap-2">'
            '<Sparkles aria-hidden="true" className="h-6 w-6 text-primary" />'
            f"{wordmark_span}"
            "</Link>"
        )

    return (
        '<Link href="/" className="flex items-center gap-2">'
        f"{wordmark_span}"
        "</Link>"
    )


# ───────────────────────────────────────────────────────────────
#  Navigation flattening.
#  project_schema.navigation is:
#    [{"group": "...", "items": [{"label", "path", "icon"}]}]
#  We flatten, skip the root "/", dedupe by href, cap at 6.
# ───────────────────────────────────────────────────────────────
def _flatten_nav(navigation: list) -> list[dict]:
    out: list[dict] = []
    for group in navigation or []:
        for item in group.get("items") or []:
            label = (item.get("label") or "").strip()
            href = (item.get("path") or item.get("href") or "").strip()
            if not label or not href:
                continue
            if href == "/":
                continue
            out.append({"label": label, "href": href})
    seen: set[str] = set()
    deduped: list[dict] = []
    for it in out:
        if it["href"] in seen:
            continue
        seen.add(it["href"])
        deduped.append(it)
    return deduped[:6]


# ───────────────────────────────────────────────────────────────
#  Main entry.
# ───────────────────────────────────────────────────────────────
def build_marketing_header_jsx(
    brand_name: str,
    brand_mark: dict | None,
    navigation: list | None,
    domain: str,
    archetype: str,
) -> str:
    """Return the complete MarketingHeader.jsx source.

    Pure function. Caller handles the file write.
    """
    brand_mark = brand_mark or {}
    navigation = navigation or []

    nav_items = _flatten_nav(navigation)
    cta_text, cta_href = _derive_cta(domain, archetype)
    brand_block = _render_brand(brand_name, brand_mark)

    # Build the JSX array literal. json.dumps handles quotes/backslashes safely.
    nav_list_entries = ",\n  ".join(
        f"{{ label: {json.dumps(it['label'])}, href: {json.dumps(it['href'])} }}"
        for it in nav_items
    )
    if not nav_list_entries:
        # Schema produced no usable items — render an empty array rather than crash.
        # The header still ships with brand mark + CTA, which is still better
        # than a template-default navbar with no logo.
        nav_list_entries = ""

    treatment = (brand_mark.get("treatment") or "wordmark").lower()
    needs_sparkles = treatment in ("icon_plus_wordmark", "icon_only")
    lucide_imports = "Menu, X" + (", Sparkles" if needs_sparkles else "")

    # JSON-escape cta_text/href too — defensive in case future mappings include quotes.
    cta_text_jsx = json.dumps(cta_text)
    cta_href_attr = json.dumps(cta_href)  # already produces "\"/signup\"" form

    return f"""'use client';

import Link from 'next/link';
import {{ useState }} from 'react';
import {{ {lucide_imports} }} from 'lucide-react';

const navLinks = [
  {nav_list_entries}
];

export default function MarketingHeader() {{
  const [mobileOpen, setMobileOpen] = useState(false);

  return (
    <header className="sticky top-0 z-50 w-full border-b border-border bg-background/90 backdrop-blur supports-[backdrop-filter]:bg-background/70">
      <div className="mx-auto flex h-16 max-w-7xl items-center justify-between px-4 sm:px-6 lg:px-8">
        {brand_block}

        <nav className="hidden items-center gap-8 md:flex" aria-label="Primary">
          {{navLinks.map((link) => (
            <Link
              key={{link.href}}
              href={{link.href}}
              className="text-sm font-medium text-foreground/80 transition-colors hover:text-foreground"
            >
              {{link.label}}
            </Link>
          ))}}
        </nav>

        <div className="hidden md:flex md:items-center md:gap-3">
          <Link
            href={cta_href_attr}
            className="inline-flex h-10 items-center rounded-md bg-primary px-5 text-sm font-medium text-primary-foreground shadow-sm transition-colors hover:bg-primary/90"
          >
            {{{cta_text_jsx}}}
          </Link>
        </div>

        <button
          type="button"
          onClick={{() => setMobileOpen(!mobileOpen)}}
          className="inline-flex h-10 w-10 items-center justify-center rounded-md text-foreground md:hidden"
          aria-label={{mobileOpen ? 'Close menu' : 'Open menu'}}
          aria-expanded={{mobileOpen}}
        >
          {{mobileOpen ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}}
        </button>
      </div>

      {{mobileOpen && (
        <nav className="border-t border-border bg-background md:hidden" aria-label="Mobile">
          <div className="mx-auto flex max-w-7xl flex-col gap-1 px-4 py-4 sm:px-6">
            {{navLinks.map((link) => (
              <Link
                key={{link.href}}
                href={{link.href}}
                onClick={{() => setMobileOpen(false)}}
                className="rounded-md px-3 py-2 text-base font-medium text-foreground/80 hover:bg-muted hover:text-foreground"
              >
                {{link.label}}
              </Link>
            ))}}
            <Link
              href={cta_href_attr}
              onClick={{() => setMobileOpen(false)}}
              className="mt-2 inline-flex h-11 items-center justify-center rounded-md bg-primary px-5 text-sm font-medium text-primary-foreground shadow-sm"
            >
              {{{cta_text_jsx}}}
            </Link>
          </div>
        </nav>
      )}}
    </header>
  );
}}
"""
