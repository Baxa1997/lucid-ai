"""Deterministic MarketingHeader.jsx builder.

Replaces the LLM's unreliable header rewrite with a pure-Python function that
emits a guaranteed-correct Next.js header using:
  - brand_name + brand_mark spec from Design Director
  - navigation items from project_schema
  - domain-appropriate CTA verb/link derived from a small mapping
  - one of FOUR visual variants picked deterministically from
    (description + archetype + vibe), so two consumer projects don't end
    up with the same sticky-bordered nav

Why deterministic: Claude occasionally keeps the template's default navbar
(Sign In / Get Started / no logo) even when Phase 1 tells it to rewrite.
Direct-writing eliminates that failure mode entirely. The variants below
restore visual variety without giving up that safety.

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
#  Variant selection — driven entirely by Design Director output,
#  no hash, no preset affinity. The Design Director picks values
#  PER PROJECT (brand_mark.placement, image_composition pattern,
#  hero_archetype) and we translate those into one of the four
#  structural header templates.
#
#  Templates are STRUCTURAL DOM patterns, not design opinions:
#    - solid_bordered     → sticky + border-b + opaque bg (default)
#    - transparent_overlay→ floats over hero, white nav, scroll → solid
#    - centered_logo      → 3-column grid with brand centered
#    - minimal            → no border, h-14 slim, text-link CTA
#
#  Translation rules (all fields are LLM-chosen per project):
#    • brand_mark.placement = "navbar_center" / "split_navbar_header"
#         → centered_logo
#    • image_composition.overlay_pattern = "dark_scrim" / "light_scrim"
#         AND a hero archetype that goes full-bleed
#         → transparent_overlay (header sits over the cinematic hero)
#    • Admin family archetypes always → solid_bordered (predictability)
#    • Portfolio + brand_mark.placement = "navbar_left" + tight density
#         → minimal
#    • Otherwise → solid_bordered
# ───────────────────────────────────────────────────────────────
_VARIANTS: tuple[str, ...] = (
    "solid_bordered",
    "transparent_overlay",
    "centered_logo",
    "minimal",
)

_ADMIN_FAMILY = {
    "admin_dashboard", "crm", "tms", "saas_dashboard", "ecommerce", "internal_tool", "saas_app",
}

_FULL_BLEED_HERO_ARCHETYPES = {
    "full_bleed_dark", "cinematic", "layered", "layered-scroll",
    "diagonal", "immersive",
}


def pick_header_variant(
    archetype: str,
    design: dict | None = None,
) -> str:
    """Translate Design Director's per-project spec into one of the four
    structural templates. Pure function. No hash, no random.

    ``design`` is the Design Director output dict (may be ``None``). When
    absent the function returns ``solid_bordered`` — the safe default that
    matches every shadcn-style consumer / admin site.
    """
    a = (archetype or "").lower()
    if a in _ADMIN_FAMILY:
        return "solid_bordered"

    design = design or {}
    placement = ((design.get("brand_mark") or {}).get("placement") or "").lower()
    image_comp = design.get("image_composition") or {}
    hero_arch = (design.get("hero_archetype") or "").lower()
    overlay_pattern = (image_comp.get("overlay_pattern") or "").lower()
    spacing_rhythm = ((design.get("spacing") or {}).get("rhythm") or "").lower()

    # Centered placement is the strongest signal — the Design Director
    # explicitly asked for the brand at the centre of the navbar.
    if placement in ("navbar_center", "split_navbar_header"):
        return "centered_logo"

    # Cinematic / full-bleed hero with a scrim → header should float over it.
    if overlay_pattern in ("dark_scrim", "light_scrim") and (
        hero_arch in _FULL_BLEED_HERO_ARCHETYPES or "full_bleed" in hero_arch
    ):
        return "transparent_overlay"

    # Tight rhythm + portfolio leans minimal.
    if a == "portfolio" and "tight" in spacing_rhythm:
        return "minimal"

    return "solid_bordered"


# ───────────────────────────────────────────────────────────────
#  Shared building blocks (used by every variant).
# ───────────────────────────────────────────────────────────────
def _nav_array_literal(nav_items: list[dict]) -> str:
    """JSX array literal of {label, href} objects for navLinks."""
    if not nav_items:
        return ""
    return ",\n  ".join(
        f"{{ label: {json.dumps(it['label'])}, href: {json.dumps(it['href'])} }}"
        for it in nav_items
    )


def _lucide_imports(treatment: str, variant: str) -> str:
    needs_sparkles = treatment in ("icon_plus_wordmark", "icon_only")
    base = "Menu, X"
    if needs_sparkles:
        base += ", Sparkles"
    # transparent_overlay variant uses ChevronDown for an optional caret on CTA.
    return base


# ───────────────────────────────────────────────────────────────
#  Variant 1: solid_bordered (the original)
# ───────────────────────────────────────────────────────────────
def _render_solid_bordered(
    brand_block: str,
    nav_array: str,
    cta_text_jsx: str,
    cta_href_attr: str,
    lucide_imports: str,
) -> str:
    return f"""'use client';

import Link from 'next/link';
import {{ useState }} from 'react';
import {{ {lucide_imports} }} from 'lucide-react';

const navLinks = [
  {nav_array}
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


# ───────────────────────────────────────────────────────────────
#  Variant 2: transparent_overlay
#  Floats above the hero. White text on first paint; turns into the
#  solid_bordered look once the user scrolls past the hero.
# ───────────────────────────────────────────────────────────────
def _render_transparent_overlay(
    brand_block: str,
    nav_array: str,
    cta_text_jsx: str,
    cta_href_attr: str,
    lucide_imports: str,
) -> str:
    return f"""'use client';

import Link from 'next/link';
import {{ useState, useEffect }} from 'react';
import {{ {lucide_imports} }} from 'lucide-react';

const navLinks = [
  {nav_array}
];

export default function MarketingHeader() {{
  const [mobileOpen, setMobileOpen] = useState(false);
  const [scrolled, setScrolled] = useState(false);

  useEffect(() => {{
    const onScroll = () => setScrolled(window.scrollY > 16);
    onScroll();
    window.addEventListener('scroll', onScroll, {{ passive: true }});
    return () => window.removeEventListener('scroll', onScroll);
  }}, []);

  const headerCls = scrolled
    ? 'border-b border-border bg-background/95 backdrop-blur text-foreground'
    : 'border-b border-transparent bg-transparent text-white';

  const navLinkCls = scrolled
    ? 'text-sm font-medium text-foreground/80 transition-colors hover:text-foreground'
    : 'text-sm font-medium text-white/85 transition-colors hover:text-white';

  const ctaCls = scrolled
    ? 'inline-flex h-10 items-center rounded-md bg-primary px-5 text-sm font-medium text-primary-foreground shadow-sm transition-colors hover:bg-primary/90'
    : 'inline-flex h-10 items-center rounded-md border border-white/30 bg-white/10 px-5 text-sm font-medium text-white backdrop-blur-sm transition-colors hover:bg-white/20';

  return (
    <header className={{`fixed top-0 z-50 w-full transition-colors duration-300 ${{headerCls}}`}}>
      <div className="mx-auto flex h-20 max-w-7xl items-center justify-between px-4 sm:px-6 lg:px-8">
        {brand_block}

        <nav className="hidden items-center gap-8 md:flex" aria-label="Primary">
          {{navLinks.map((link) => (
            <Link key={{link.href}} href={{link.href}} className={{navLinkCls}}>
              {{link.label}}
            </Link>
          ))}}
        </nav>

        <div className="hidden md:flex md:items-center md:gap-3">
          <Link href={cta_href_attr} className={{ctaCls}}>{{{cta_text_jsx}}}</Link>
        </div>

        <button
          type="button"
          onClick={{() => setMobileOpen(!mobileOpen)}}
          className="inline-flex h-10 w-10 items-center justify-center rounded-md md:hidden"
          aria-label={{mobileOpen ? 'Close menu' : 'Open menu'}}
          aria-expanded={{mobileOpen}}
        >
          {{mobileOpen ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}}
        </button>
      </div>

      {{mobileOpen && (
        <nav className="border-t border-border bg-background text-foreground md:hidden" aria-label="Mobile">
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


# ───────────────────────────────────────────────────────────────
#  Variant 3: centered_logo
#  Brand sits at the centre. Nav splits L/R; CTA tucks to far right.
#  Mobile collapses everything into a sheet under the brand.
# ───────────────────────────────────────────────────────────────
def _render_centered_logo(
    brand_block: str,
    nav_array: str,
    cta_text_jsx: str,
    cta_href_attr: str,
    lucide_imports: str,
) -> str:
    return f"""'use client';

import Link from 'next/link';
import {{ useState }} from 'react';
import {{ {lucide_imports} }} from 'lucide-react';

const navLinks = [
  {nav_array}
];

export default function MarketingHeader() {{
  const [mobileOpen, setMobileOpen] = useState(false);
  const half = Math.ceil(navLinks.length / 2);
  const navLeft = navLinks.slice(0, half);
  const navRight = navLinks.slice(half);

  return (
    <header className="sticky top-0 z-50 w-full border-b border-border bg-background">
      <div className="mx-auto grid h-20 max-w-7xl grid-cols-[1fr_auto_1fr] items-center gap-6 px-4 sm:px-6 lg:px-8">
        <nav className="hidden items-center justify-end gap-8 md:flex" aria-label="Primary">
          {{navLeft.map((link) => (
            <Link
              key={{link.href}}
              href={{link.href}}
              className="text-xs font-medium uppercase tracking-[0.18em] text-foreground/75 transition-colors hover:text-foreground"
            >
              {{link.label}}
            </Link>
          ))}}
        </nav>

        <div className="flex justify-center">{brand_block}</div>

        <div className="hidden items-center justify-start gap-8 md:flex">
          <nav className="flex items-center gap-8" aria-label="Primary right">
            {{navRight.map((link) => (
              <Link
                key={{link.href}}
                href={{link.href}}
                className="text-xs font-medium uppercase tracking-[0.18em] text-foreground/75 transition-colors hover:text-foreground"
              >
                {{link.label}}
              </Link>
            ))}}
          </nav>
          <Link
            href={cta_href_attr}
            className="inline-flex h-10 items-center rounded-full bg-primary px-5 text-sm font-medium text-primary-foreground shadow-sm transition-colors hover:bg-primary/90"
          >
            {{{cta_text_jsx}}}
          </Link>
        </div>

        <div className="md:hidden col-start-3 justify-self-end">
          <button
            type="button"
            onClick={{() => setMobileOpen(!mobileOpen)}}
            className="inline-flex h-10 w-10 items-center justify-center rounded-md text-foreground"
            aria-label={{mobileOpen ? 'Close menu' : 'Open menu'}}
            aria-expanded={{mobileOpen}}
          >
            {{mobileOpen ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}}
          </button>
        </div>
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
              className="mt-2 inline-flex h-11 items-center justify-center rounded-full bg-primary px-5 text-sm font-medium text-primary-foreground shadow-sm"
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


# ───────────────────────────────────────────────────────────────
#  Variant 4: minimal
#  No border. Slim h-14. Nav is text-only with small caps. CTA is
#  a text link with an arrow rather than a filled button.
# ───────────────────────────────────────────────────────────────
def _render_minimal(
    brand_block: str,
    nav_array: str,
    cta_text_jsx: str,
    cta_href_attr: str,
    lucide_imports: str,
) -> str:
    return f"""'use client';

import Link from 'next/link';
import {{ useState }} from 'react';
import {{ {lucide_imports} }} from 'lucide-react';

const navLinks = [
  {nav_array}
];

export default function MarketingHeader() {{
  const [mobileOpen, setMobileOpen] = useState(false);

  return (
    <header className="sticky top-0 z-50 w-full bg-background/95 backdrop-blur">
      <div className="mx-auto flex h-14 max-w-6xl items-center justify-between px-4 sm:px-6 lg:px-8">
        {brand_block}

        <nav className="hidden items-center gap-7 md:flex" aria-label="Primary">
          {{navLinks.map((link) => (
            <Link
              key={{link.href}}
              href={{link.href}}
              className="text-[13px] font-medium text-foreground/65 transition-colors hover:text-foreground"
            >
              {{link.label}}
            </Link>
          ))}}
        </nav>

        <div className="hidden md:block">
          <Link
            href={cta_href_attr}
            className="text-[13px] font-semibold text-foreground transition-colors hover:text-primary"
          >
            {{{cta_text_jsx}}} →
          </Link>
        </div>

        <button
          type="button"
          onClick={{() => setMobileOpen(!mobileOpen)}}
          className="inline-flex h-9 w-9 items-center justify-center rounded-md text-foreground md:hidden"
          aria-label={{mobileOpen ? 'Close menu' : 'Open menu'}}
          aria-expanded={{mobileOpen}}
        >
          {{mobileOpen ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}}
        </button>
      </div>

      {{mobileOpen && (
        <nav className="bg-background md:hidden" aria-label="Mobile">
          <div className="mx-auto flex max-w-6xl flex-col gap-1 px-4 py-3 sm:px-6">
            {{navLinks.map((link) => (
              <Link
                key={{link.href}}
                href={{link.href}}
                onClick={{() => setMobileOpen(false)}}
                className="rounded-md px-3 py-2 text-base font-medium text-foreground/75 hover:bg-muted hover:text-foreground"
              >
                {{link.label}}
              </Link>
            ))}}
            <Link
              href={cta_href_attr}
              onClick={{() => setMobileOpen(false)}}
              className="mt-1 px-3 py-2 text-base font-semibold text-foreground hover:text-primary"
            >
              {{{cta_text_jsx}}} →
            </Link>
          </div>
        </nav>
      )}}
    </header>
  );
}}
"""


_VARIANT_RENDERERS = {
    "solid_bordered":      _render_solid_bordered,
    "transparent_overlay": _render_transparent_overlay,
    "centered_logo":       _render_centered_logo,
    "minimal":             _render_minimal,
}


# ───────────────────────────────────────────────────────────────
#  Main entry.
# ───────────────────────────────────────────────────────────────
def build_marketing_header_jsx(
    brand_name: str,
    brand_mark: dict | None,
    navigation: list | None,
    domain: str,
    archetype: str,
    design: dict | None = None,
) -> tuple[str, str]:
    """Return ``(jsx_source, variant_name)`` for the project's header.

    Pure function. Caller handles the file write. ``variant_name`` is
    returned so the caller can log + surface the choice in the plan / progress.

    ``design`` is the Design Director's full output dict — the variant is
    chosen from its ``brand_mark.placement``, ``image_composition``,
    ``hero_archetype`` and ``spacing.rhythm`` fields. No hash, no random.
    """
    brand_mark = brand_mark or {}
    navigation = navigation or []

    nav_items = _flatten_nav(navigation)
    cta_text, cta_href = _derive_cta(domain, archetype)
    brand_block = _render_brand(brand_name, brand_mark)
    nav_array = _nav_array_literal(nav_items)

    treatment = (brand_mark.get("treatment") or "wordmark").lower()
    variant = pick_header_variant(archetype, design)
    lucide = _lucide_imports(treatment, variant)

    cta_text_jsx = json.dumps(cta_text)
    cta_href_attr = json.dumps(cta_href)

    renderer = _VARIANT_RENDERERS.get(variant, _render_solid_bordered)
    jsx = renderer(
        brand_block=brand_block,
        nav_array=nav_array,
        cta_text_jsx=cta_text_jsx,
        cta_href_attr=cta_href_attr,
        lucide_imports=lucide,
    )
    return jsx, variant
