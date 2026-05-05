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
#    - floating_capsule   → pill-shaped floating nav, max-w-4xl, top-4 mt
#    - split_action_bar   → logo + single Menu trigger, opens fullscreen
#                           overlay with oversized centered links
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
    "floating_capsule",
    "split_action_bar",
)

_ADMIN_FAMILY = {
    "admin_dashboard", "crm", "tms", "saas_dashboard", "ecommerce", "internal_tool", "saas_app",
}

_FULL_BLEED_HERO_ARCHETYPES = {
    "full_bleed_dark", "cinematic", "layered", "layered-scroll",
    "diagonal", "immersive", "cinematic-parallax", "editorial-offset",
    "full-bleed-dark", "layered_scroll", "magazine",
}
# Substring fragments — if hero_archetype contains any of these it's full-bleed.
_FULL_BLEED_KEYWORDS = ("cinematic", "full_bleed", "full-bleed", "layered", "immersive")


def pick_header_variant(
    archetype: str,
    design: dict | None = None,
) -> str:
    """Translate Design Director's per-project spec into one of the four
    structural templates.

    Hard constraints (no randomness):
      • Admin family → ``solid_bordered`` (predictability for data-heavy UIs)
      • brand_mark.placement = navbar_center → ``centered_logo`` (Director
        explicitly asked for it; honour the request)

    Soft constraints (multiple variants are visually defensible — pick at
    random within the candidate set so two coffee-shop generations don't
    produce IDENTICAL headers). Prior versions hard-pinned each soft case
    to ONE variant which made every consumer landing page look the same.
    """
    import random as _random
    a = (archetype or "").lower()
    if a in _ADMIN_FAMILY:
        return "solid_bordered"

    design = design or {}
    placement = ((design.get("brand_mark") or {}).get("placement") or "").lower()
    image_comp = design.get("image_composition") or {}
    hero_arch = (design.get("hero_archetype") or "").lower()
    overlay_pattern = (image_comp.get("overlay_pattern") or "").lower()
    spacing_rhythm = ((design.get("spacing") or {}).get("rhythm") or "").lower()

    # Centered placement is a hard signal — keep deterministic.
    if placement in ("navbar_center", "split_navbar_header"):
        return "centered_logo"

    _is_full_bleed = (
        hero_arch in _FULL_BLEED_HERO_ARCHETYPES
        or any(kw in hero_arch for kw in _FULL_BLEED_KEYWORDS)
    )

    # Full-bleed hero — transparent_overlay, split_action_bar, floating_capsule,
    # dark_band (all work over photography). Rotate so cinematic projects don't
    # always get the same translucent-nav cliché.
    if _is_full_bleed:
        return _random.choice((
            "transparent_overlay",
            "split_action_bar",
            "floating_capsule",
            "dark_band",
        ))

    # Editorial / luxury feel (portfolio, agency, fashion, hospitality)
    # — editorial_border (magazine masthead) and split_action_bar (Aesop) fit best.
    _is_editorial = a in ("portfolio", "agency", "fashion") or "luxury" in spacing_rhythm
    if _is_editorial:
        return _random.choice((
            "editorial_border",
            "split_action_bar",
            "minimal",
            "dark_band",
        ))

    # General consumer / landing — rotate evenly across all 8 variants.
    # Equal-ish weights so two coffee-shop generations don't produce the same nav.
    return _random.choices(
        (
            "solid_bordered",
            "centered_logo",
            "minimal",
            "floating_capsule",
            "split_action_bar",
            "dark_band",
            "editorial_border",
        ),
        weights=(2, 2, 2, 2, 1, 2, 2),
        k=1,
    )[0]


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
              className="group relative text-sm font-medium text-foreground/70 transition-colors hover:text-foreground"
            >
              {{link.label}}
              <span className="absolute -bottom-0.5 left-0 h-px w-0 bg-primary transition-all duration-300 group-hover:w-full" />
            </Link>
          ))}}
        </nav>

        <div className="hidden md:flex md:items-center md:gap-3">
          <Link
            href={cta_href_attr}
            className="inline-flex h-10 items-center rounded-full bg-primary px-6 text-sm font-medium text-primary-foreground shadow-sm transition-all hover:bg-primary/90 hover:shadow-md"
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


# ───────────────────────────────────────────────────────────────
#  Variant 5: floating_capsule
#  A pill-shaped nav floating top-center with margin from page edges.
#  Visually distinct from the full-width "sticky bar across the top"
#  family — reads as a deliberate UI object rather than a chrome strip.
# ───────────────────────────────────────────────────────────────
def _render_floating_capsule(
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
    <header className="sticky top-4 z-50 w-full px-4">
      <div className="mx-auto flex h-14 w-full max-w-4xl items-center justify-between rounded-full border border-border bg-background/85 px-3 pl-6 shadow-lg backdrop-blur supports-[backdrop-filter]:bg-background/65">
        {brand_block}

        <nav className="hidden items-center gap-7 md:flex" aria-label="Primary">
          {{navLinks.map((link) => (
            <Link
              key={{link.href}}
              href={{link.href}}
              className="text-sm font-medium text-foreground/75 transition-colors hover:text-foreground"
            >
              {{link.label}}
            </Link>
          ))}}
        </nav>

        <div className="hidden md:block">
          <Link
            href={cta_href_attr}
            className="inline-flex h-10 items-center rounded-full bg-primary px-5 text-sm font-medium text-primary-foreground shadow-sm transition-colors hover:bg-primary/90"
          >
            {{{cta_text_jsx}}}
          </Link>
        </div>

        <button
          type="button"
          onClick={{() => setMobileOpen(!mobileOpen)}}
          className="inline-flex h-10 w-10 items-center justify-center rounded-full text-foreground md:hidden"
          aria-label={{mobileOpen ? 'Close menu' : 'Open menu'}}
          aria-expanded={{mobileOpen}}
        >
          {{mobileOpen ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}}
        </button>
      </div>

      {{mobileOpen && (
        <nav
          className="mx-auto mt-3 max-w-4xl rounded-2xl border border-border bg-background p-3 shadow-lg md:hidden"
          aria-label="Mobile"
        >
          <div className="flex flex-col gap-1">
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
              className="mt-1 inline-flex h-11 items-center justify-center rounded-full bg-primary px-5 text-sm font-medium text-primary-foreground shadow-sm"
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
#  Variant 6: split_action_bar
#  Logo top-left, single hamburger top-right that opens a fullscreen
#  overlay menu with oversized centered links. No primary nav visible
#  in the bar itself — the entire navigation is behind the trigger.
#  Reads as luxury/editorial (Aesop, COS, Maharishi).
# ───────────────────────────────────────────────────────────────
def _render_split_action_bar(
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
  const [open, setOpen] = useState(false);

  useEffect(() => {{
    if (open) {{
      document.body.style.overflow = 'hidden';
      return () => {{ document.body.style.overflow = ''; }};
    }}
  }}, [open]);

  return (
    <>
      <header className="sticky top-0 z-50 w-full bg-background/85 backdrop-blur supports-[backdrop-filter]:bg-background/65">
        <div className="mx-auto flex h-16 max-w-7xl items-center justify-between px-4 sm:px-6 lg:px-8">
          {brand_block}

          <button
            type="button"
            onClick={{() => setOpen(true)}}
            className="inline-flex items-center gap-3 rounded-full border border-border bg-background px-4 py-2 text-sm font-medium text-foreground transition-colors hover:bg-muted"
            aria-label="Open menu"
            aria-expanded={{open}}
          >
            <span className="hidden sm:inline">Menu</span>
            <Menu className="h-4 w-4" />
          </button>
        </div>
      </header>

      {{open && (
        <div
          className="fixed inset-0 z-[60] flex flex-col bg-background"
          role="dialog"
          aria-modal="true"
          aria-label="Site navigation"
        >
          <div className="mx-auto flex h-16 w-full max-w-7xl items-center justify-between px-4 sm:px-6 lg:px-8">
            {brand_block}
            <button
              type="button"
              onClick={{() => setOpen(false)}}
              className="inline-flex h-10 w-10 items-center justify-center rounded-full text-foreground hover:bg-muted"
              aria-label="Close menu"
            >
              <X className="h-5 w-5" />
            </button>
          </div>

          <nav
            className="flex flex-1 flex-col items-center justify-center gap-6 px-6 pb-24"
            aria-label="Primary"
          >
            {{navLinks.map((link, i) => (
              <Link
                key={{link.href}}
                href={{link.href}}
                onClick={{() => setOpen(false)}}
                className="text-4xl font-semibold tracking-tight text-foreground/85 transition-colors hover:text-foreground sm:text-5xl md:text-6xl"
                style={{{{ animation: `fadeUp 0.5s ease-out ${{i * 60}}ms both` }}}}
              >
                {{link.label}}
              </Link>
            ))}}
            <Link
              href={cta_href_attr}
              onClick={{() => setOpen(false)}}
              className="mt-6 inline-flex h-12 items-center rounded-full bg-primary px-8 text-base font-medium text-primary-foreground shadow-sm hover:bg-primary/90"
            >
              {{{cta_text_jsx}}}
            </Link>
          </nav>

          <style>{{`
            @keyframes fadeUp {{
              from {{ opacity: 0; transform: translateY(12px); }}
              to {{ opacity: 1; transform: translateY(0); }}
            }}
          `}}</style>
        </div>
      )}}
    </>
  );
}}
"""


# ───────────────────────────────────────────────────────────────
#  Variant 7: dark_band
#  Inverted header — bg-foreground, text-background. High-contrast
#  editorial feel (The Row, COS, Bottega Veneta). Works with any
#  brand because it uses CSS variables, not hex. CTA is ghost-white.
# ───────────────────────────────────────────────────────────────
def _render_dark_band(
    brand_block: str,
    nav_array: str,
    cta_text_jsx: str,
    cta_href_attr: str,
    lucide_imports: str,
) -> str:
    # Rewrite brand_block color to be background-safe (white on dark)
    dark_brand_block = brand_block.replace("text-foreground", "text-background").replace(
        "text-primary", "text-background"
    )
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
    <header className="sticky top-0 z-50 w-full bg-foreground text-background">
      <div className="mx-auto flex h-16 max-w-7xl items-center justify-between px-4 sm:px-6 lg:px-8">
        {dark_brand_block}

        <nav className="hidden items-center gap-8 md:flex" aria-label="Primary">
          {{navLinks.map((link) => (
            <Link
              key={{link.href}}
              href={{link.href}}
              className="text-xs font-medium uppercase tracking-[0.15em] text-background/60 transition-colors hover:text-background"
            >
              {{link.label}}
            </Link>
          ))}}
        </nav>

        <div className="hidden md:flex md:items-center md:gap-3">
          <Link
            href={cta_href_attr}
            className="inline-flex h-9 items-center rounded-md border border-background/30 bg-transparent px-5 text-sm font-medium text-background transition-colors hover:bg-background hover:text-foreground"
          >
            {{{cta_text_jsx}}}
          </Link>
        </div>

        <button
          type="button"
          onClick={{() => setMobileOpen(!mobileOpen)}}
          className="inline-flex h-10 w-10 items-center justify-center rounded-md text-background md:hidden"
          aria-label={{mobileOpen ? 'Close menu' : 'Open menu'}}
          aria-expanded={{mobileOpen}}
        >
          {{mobileOpen ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}}
        </button>
      </div>

      {{mobileOpen && (
        <nav className="border-t border-background/15 bg-foreground text-background md:hidden" aria-label="Mobile">
          <div className="mx-auto flex max-w-7xl flex-col gap-1 px-4 py-4 sm:px-6">
            {{navLinks.map((link) => (
              <Link
                key={{link.href}}
                href={{link.href}}
                onClick={{() => setMobileOpen(false)}}
                className="rounded-md px-3 py-2 text-base font-medium text-background/70 hover:bg-background/10 hover:text-background"
              >
                {{link.label}}
              </Link>
            ))}}
            <Link
              href={cta_href_attr}
              onClick={{() => setMobileOpen(false)}}
              className="mt-2 inline-flex h-11 items-center justify-center rounded-md border border-background/30 px-5 text-sm font-medium text-background hover:bg-background hover:text-foreground"
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
#  Variant 8: editorial_border
#  Tall header (h-24) with an oversized brand mark, uppercase tiny
#  tracking nav, and a full-width hairline border separating it from
#  the hero. Reads like a magazine masthead (Kinfolk, Monocle, T Magazine).
#  CTA is a ghost pill — no fill, border only.
# ───────────────────────────────────────────────────────────────
def _render_editorial_border(
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
    <header className="sticky top-0 z-50 w-full border-b border-foreground/15 bg-background">
      <div className="mx-auto flex h-20 max-w-7xl items-center justify-between px-4 sm:px-6 lg:px-8">
        {brand_block}

        <nav className="hidden items-center gap-10 md:flex" aria-label="Primary">
          {{navLinks.map((link) => (
            <Link
              key={{link.href}}
              href={{link.href}}
              className="group relative text-[11px] font-medium uppercase tracking-[0.2em] text-foreground/50 transition-colors hover:text-foreground"
            >
              {{link.label}}
              <span className="absolute -bottom-1 left-0 h-px w-0 bg-foreground transition-all duration-300 group-hover:w-full" />
            </Link>
          ))}}
        </nav>

        <div className="hidden md:flex md:items-center md:gap-3">
          <Link
            href={cta_href_attr}
            className="inline-flex h-9 items-center rounded-full border border-foreground/30 px-5 text-[11px] font-medium uppercase tracking-[0.12em] text-foreground transition-colors hover:border-foreground hover:bg-foreground hover:text-background"
          >
            {{{cta_text_jsx}}}
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
        <nav className="border-t border-foreground/10 bg-background md:hidden" aria-label="Mobile">
          <div className="mx-auto flex max-w-7xl flex-col gap-0 px-4 py-2 sm:px-6">
            {{navLinks.map((link) => (
              <Link
                key={{link.href}}
                href={{link.href}}
                onClick={{() => setMobileOpen(false)}}
                className="border-b border-foreground/8 py-3 text-sm font-medium uppercase tracking-[0.12em] text-foreground/60 hover:text-foreground"
              >
                {{link.label}}
              </Link>
            ))}}
            <Link
              href={cta_href_attr}
              onClick={{() => setMobileOpen(false)}}
              className="mt-3 mb-1 inline-flex h-11 items-center justify-center rounded-full border border-foreground/30 px-5 text-xs font-medium uppercase tracking-[0.12em] text-foreground hover:bg-foreground hover:text-background"
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


_VARIANT_RENDERERS = {
    "solid_bordered":      _render_solid_bordered,
    "transparent_overlay": _render_transparent_overlay,
    "centered_logo":       _render_centered_logo,
    "minimal":             _render_minimal,
    "floating_capsule":    _render_floating_capsule,
    "split_action_bar":    _render_split_action_bar,
    "dark_band":           _render_dark_band,
    "editorial_border":    _render_editorial_border,
}


# ═══════════════════════════════════════════════════════════════
#  SPEC-DRIVEN COMPOSITIONAL RENDERER
#  Reads ===HEADER_DESIGN=== that Gemini emits in research and
#  composes JSX from declared axes (structure, surface, etc.)
#  rather than from a preset variant name.
# ═══════════════════════════════════════════════════════════════

_HEADER_SPEC_VALID: dict[str, frozenset] = {
    "structure":      frozenset({"logo_left_nav_right", "centered_logo", "logo_left_hamburger", "floating_pill", "two_row"}),
    "surface":        frozenset({"light_blur", "dark_solid", "transparent_scroll", "primary_tinted", "glass_dark"}),
    "nav_link_style": frozenset({"plain", "underline_slide", "uppercase_track", "pill_hover", "dot_left"}),
    "cta_style":      frozenset({"filled_pill", "filled_sharp", "ghost_pill", "ghost_sharp", "text_arrow", "inverted_pill"}),
    "height":         frozenset({"slim", "standard", "tall", "masthead"}),
    "top_accent":     frozenset({"none", "primary_bar", "gradient_wash"}),
    "mobile_menu":    frozenset({"slide_drawer", "fullscreen_overlay", "simple_dropdown"}),
}

_HEIGHT_CLS_MAP: dict[str, str] = {
    "slim": "h-14", "standard": "h-16", "tall": "h-20", "masthead": "h-24",
}

_OUTER_CLS_MAP: dict[str, str] = {
    "light_blur":     "sticky top-0 z-50 w-full border-b border-border bg-background/92 backdrop-blur supports-[backdrop-filter]:bg-background/75",
    "dark_solid":     "sticky top-0 z-50 w-full bg-foreground text-background",
    "primary_tinted": "sticky top-0 z-50 w-full border-b border-primary/20 bg-primary/10 backdrop-blur",
    "glass_dark":     "fixed top-0 z-50 w-full bg-black/30 backdrop-blur-xl text-white",
}

_NAV_CLS_MAP: dict[str, str] = {
    "plain":          "text-sm font-medium text-foreground/70 transition-colors hover:text-foreground",
    "underline_slide":"group relative text-sm font-medium text-foreground/70 transition-colors hover:text-foreground",
    "uppercase_track":"text-[11px] font-medium uppercase tracking-[0.18em] text-foreground/55 transition-colors hover:text-foreground",
    "pill_hover":     "rounded-full px-3 py-1.5 text-sm font-medium text-foreground/70 transition-colors hover:bg-muted hover:text-foreground",
    "dot_left":       "group flex items-center gap-2 text-sm font-medium text-foreground/70 transition-colors hover:text-foreground",
}

_NAV_CLS_INV_MAP: dict[str, str] = {
    "plain":          "text-sm font-medium text-background/70 transition-colors hover:text-background",
    "underline_slide":"group relative text-sm font-medium text-background/70 transition-colors hover:text-background",
    "uppercase_track":"text-[11px] font-medium uppercase tracking-[0.18em] text-background/55 transition-colors hover:text-background",
    "pill_hover":     "rounded-full px-3 py-1.5 text-sm font-medium text-background/70 transition-colors hover:bg-background/15 hover:text-background",
    "dot_left":       "group flex items-center gap-2 text-sm font-medium text-background/70 transition-colors hover:text-background",
}

_CTA_CLS_MAP: dict[str, str] = {
    "filled_pill":   "inline-flex h-10 items-center rounded-full bg-primary px-6 text-sm font-medium text-primary-foreground shadow-sm transition-colors hover:bg-primary/90",
    "filled_sharp":  "inline-flex h-10 items-center rounded-md bg-primary px-5 text-sm font-medium text-primary-foreground shadow-sm transition-colors hover:bg-primary/90",
    "ghost_pill":    "inline-flex h-10 items-center rounded-full border border-foreground/30 px-5 text-sm font-medium text-foreground transition-colors hover:border-foreground hover:bg-foreground hover:text-background",
    "ghost_sharp":   "inline-flex h-10 items-center rounded-md border border-border px-5 text-sm font-medium text-foreground transition-colors hover:bg-muted",
    "text_arrow":    "text-[13px] font-semibold text-foreground transition-colors hover:text-primary",
    "inverted_pill": "inline-flex h-10 items-center rounded-full bg-background px-5 text-sm font-medium text-foreground shadow-sm transition-colors hover:bg-background/90",
}


def parse_header_spec_from_research(research: str) -> dict:
    """Parse ===HEADER_DESIGN=== block from Gemini research output.

    Returns validated spec dict; empty dict if block is missing/unparseable.
    Caller falls back to pick_header_variant() when this returns {}.
    """
    if not research:
        return {}
    m = re.search(r"===HEADER_DESIGN===\s*\n(.*?)(?=\n===[A-Z_]+===|\Z)", research, flags=re.DOTALL)
    if not m:
        return {}
    block = m.group(1)
    out: dict[str, str] = {}
    for line in block.splitlines():
        if ":" not in line:
            continue
        k, _, rest = line.partition(":")
        k = k.strip().lower()
        if k not in _HEADER_SPEC_VALID:
            continue
        v = rest.strip().split()[0].lower().rstrip(".,;") if rest.strip() else ""
        v = re.sub(r"\s*\(.*?\)$", "", v).strip()
        if v in _HEADER_SPEC_VALID[k]:
            out[k] = v
    return out


def _spec_accent_strip(accent: str) -> str:
    if accent == "primary_bar":
        return '      <div className="h-[3px] w-full bg-primary" />\n'
    if accent == "gradient_wash":
        return '      <div className="h-1 w-full bg-gradient-to-r from-primary/20 via-transparent to-accent/20" />\n'
    return ""


def _spec_nav_map_jsx(style: str, inv: bool) -> str:
    """Return {navLinks.map(...)} JSX string (no surrounding tags)."""
    cls = (_NAV_CLS_INV_MAP if inv else _NAV_CLS_MAP).get(style, _NAV_CLS_MAP["plain"])
    dot_c = "bg-background" if inv else "bg-primary"
    if style == "underline_slide":
        inner = (
            "{link.label}\n"
            f'              <span className="absolute -bottom-0.5 left-0 h-px w-0 {dot_c} transition-all duration-300 group-hover:w-full" />'
        )
    elif style == "dot_left":
        inner = (
            f'<span className="h-1.5 w-1.5 shrink-0 rounded-full {dot_c} opacity-0 transition-opacity group-hover:opacity-100" />\n'
            "              {link.label}"
        )
    else:
        inner = "{link.label}"
    return (
        "{navLinks.map((link) => (\n"
        f'            <Link key={{link.href}} href={{link.href}} className="{cls}">\n'
        f"              {inner}\n"
        "            </Link>\n"
        "          ))}"
    )


def _spec_cta_jsx(style: str, text_jsx: str, href_attr: str, extra_cls: str = "") -> str:
    cls = _CTA_CLS_MAP.get(style, _CTA_CLS_MAP["filled_pill"])
    if extra_cls:
        cls = f"{cls} {extra_cls}"
    arrow = " →" if style == "text_arrow" else ""
    return f'<Link href={href_attr} className="{cls}">{{{text_jsx}}}{arrow}</Link>'


def _spec_mobile_menu_jsx(menu_style: str, cta_text_jsx: str, cta_href_attr: str, cta_style: str) -> str:
    """Build mobile-menu JSX block (rendered when mobileOpen is true)."""
    mob_cls = _CTA_CLS_MAP.get(cta_style, _CTA_CLS_MAP["filled_pill"]).replace("h-10 ", "h-11 ")
    arrow = " →" if cta_style == "text_arrow" else ""
    cta_mob = (
        f'<Link href={cta_href_attr} onClick={{() => setMobileOpen(false)}} '
        f'className="{mob_cls} mt-2 w-full justify-center">'
        f'{{{cta_text_jsx}}}{arrow}</Link>'
    )
    mob_link = (
        "{navLinks.map((link) => (\n"
        '            <Link\n'
        '              key={link.href}\n'
        '              href={link.href}\n'
        '              onClick={() => setMobileOpen(false)}\n'
        '              className="rounded-md px-3 py-2 text-base font-medium text-foreground/80 hover:bg-muted hover:text-foreground"\n'
        "            >\n"
        "              {link.label}\n"
        "            </Link>\n"
        "          ))}"
    )

    if menu_style == "fullscreen_overlay":
        return (
            "      {mobileOpen && (\n"
            '        <div className="fixed inset-0 z-[60] flex flex-col bg-background" role="dialog" aria-modal="true">\n'
            '          <div className="flex items-center justify-end p-4">\n'
            '            <button type="button" onClick={() => setMobileOpen(false)} className="inline-flex h-10 w-10 items-center justify-center rounded-full text-foreground hover:bg-muted" aria-label="Close menu">\n'
            '              <X className="h-5 w-5" />\n'
            "            </button>\n"
            "          </div>\n"
            '          <nav className="flex flex-1 flex-col items-center justify-center gap-6 px-6 pb-24" aria-label="Mobile">\n'
            f"            {mob_link}\n"
            f"            {cta_mob}\n"
            "          </nav>\n"
            "        </div>\n"
            "      )}"
        )

    if menu_style == "slide_drawer":
        return (
            "      {mobileOpen && (\n"
            '        <div className="fixed inset-y-0 right-0 z-[60] flex w-72 flex-col bg-background shadow-xl" role="dialog" aria-modal="true">\n'
            '          <div className="flex items-center justify-end p-4">\n'
            '            <button type="button" onClick={() => setMobileOpen(false)} className="inline-flex h-9 w-9 items-center justify-center rounded-md text-foreground hover:bg-muted" aria-label="Close">\n'
            '              <X className="h-5 w-5" />\n'
            "            </button>\n"
            "          </div>\n"
            '          <nav className="flex flex-col gap-1 px-4 py-2" aria-label="Mobile">\n'
            f"            {mob_link}\n"
            f"            {cta_mob}\n"
            "          </nav>\n"
            "        </div>\n"
            "      )}"
        )

    # simple_dropdown (default)
    return (
        '      {mobileOpen && (\n'
        '        <nav className="border-t border-border bg-background md:hidden" aria-label="Mobile">\n'
        '          <div className="mx-auto flex max-w-7xl flex-col gap-1 px-4 py-4 sm:px-6">\n'
        f"            {mob_link}\n"
        f"            {cta_mob}\n"
        "          </div>\n"
        "        </nav>\n"
        "      )}"
    )


def render_from_spec(
    spec: dict,
    brand_block: str,
    nav_array: str,
    cta_text_jsx: str,
    cta_href_attr: str,
    lucide_imports: str,
) -> str:
    """Compose MarketingHeader.jsx from Gemini-specified design axes.

    Falls back gracefully: any missing spec key uses a sensible default.
    """
    structure  = spec.get("structure",      "logo_left_nav_right")
    surface    = spec.get("surface",        "light_blur")
    nav_style  = spec.get("nav_link_style", "plain")
    cta_style  = spec.get("cta_style",      "filled_pill")
    height     = spec.get("height",         "standard")
    top_accent = spec.get("top_accent",     "none")
    mobile     = spec.get("mobile_menu",    "simple_dropdown")

    is_inv    = surface in ("dark_solid", "glass_dark")
    is_scroll = surface == "transparent_scroll"

    height_cls = _HEIGHT_CLS_MAP.get(height, "h-16")
    accent     = _spec_accent_strip(top_accent)

    brand = (
        brand_block
        .replace("text-foreground", "text-background")
        .replace("text-primary", "text-background")
        if is_inv else brand_block
    )

    nav_map     = _spec_nav_map_jsx(nav_style, is_inv)
    desktop_cta = _spec_cta_jsx(cta_style, cta_text_jsx, cta_href_attr)
    mobile_nav  = _spec_mobile_menu_jsx(mobile, cta_text_jsx, cta_href_attr, cta_style)

    if is_scroll:
        light_cls = "border-b border-border bg-background/95 backdrop-blur text-foreground"
        dark_cls  = "border-b border-transparent bg-transparent text-white"
        header_cls_attr = f'{{`fixed top-0 z-50 w-full transition-colors duration-300 ${{scrolled ? \'{light_cls}\' : \'{dark_cls}\'}}`}}'
        scroll_state  = "\n  const [scrolled, setScrolled] = useState(false);"
        scroll_effect = """
  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 60);
    onScroll();
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => window.removeEventListener('scroll', onScroll);
  }, []);
"""
        extra_import = ", useEffect"
    else:
        outer_cls = _OUTER_CLS_MAP.get(surface, _OUTER_CLS_MAP["light_blur"])
        header_cls_attr = f'"{outer_cls}"'
        scroll_state  = ""
        scroll_effect = ""
        extra_import  = ""

    # ── structure: logo_left_hamburger ────────────────────────
    if structure == "logo_left_hamburger":
        cta_overlay = _spec_cta_jsx(cta_style, cta_text_jsx, cta_href_attr, "mt-8 h-12 px-8 text-base")
        return f"""'use client';

import Link from 'next/link';
import {{ useState, useEffect }} from 'react';
import {{ {lucide_imports} }} from 'lucide-react';

const navLinks = [
  {nav_array}
];

export default function MarketingHeader() {{
  const [open, setOpen] = useState(false);

  useEffect(() => {{
    if (open) {{
      document.body.style.overflow = 'hidden';
      return () => {{ document.body.style.overflow = ''; }};
    }}
  }}, [open]);

  return (
    <>
      {accent}<header className={header_cls_attr}>
        <div className="mx-auto flex {height_cls} max-w-7xl items-center justify-between px-4 sm:px-6 lg:px-8">
          {brand}
          <button
            type="button"
            onClick={{() => setOpen(true)}}
            className="inline-flex items-center gap-2 rounded-full border border-border bg-background px-4 py-2 text-sm font-medium text-foreground transition-colors hover:bg-muted"
            aria-label="Open menu"
            aria-expanded={{open}}
          >
            <span className="hidden sm:inline">Menu</span>
            <Menu className="h-4 w-4" />
          </button>
        </div>
      </header>

      {{open && (
        <div className="fixed inset-0 z-[60] flex flex-col bg-background" role="dialog" aria-modal="true" aria-label="Site navigation">
          <div className="mx-auto flex h-16 w-full max-w-7xl items-center justify-between px-4 sm:px-6 lg:px-8">
            {brand}
            <button type="button" onClick={{() => setOpen(false)}} className="inline-flex h-10 w-10 items-center justify-center rounded-full text-foreground hover:bg-muted" aria-label="Close menu">
              <X className="h-5 w-5" />
            </button>
          </div>
          <nav className="flex flex-1 flex-col items-center justify-center gap-6 px-6 pb-24" aria-label="Primary">
            {{navLinks.map((link, i) => (
              <Link
                key={{link.href}}
                href={{link.href}}
                onClick={{() => setOpen(false)}}
                className="text-4xl font-semibold tracking-tight text-foreground/85 transition-colors hover:text-foreground sm:text-5xl"
                style={{{{ animation: `fadeUp 0.5s ease-out ${{i * 60}}ms both` }}}}
              >
                {{link.label}}
              </Link>
            ))}}
            {cta_overlay}
          </nav>
          <style>{{`
            @keyframes fadeUp {{ from {{ opacity: 0; transform: translateY(12px); }} to {{ opacity: 1; transform: translateY(0); }} }}
          `}}</style>
        </div>
      )}}
    </>
  );
}}
"""

    # ── structure: floating_pill ──────────────────────────────
    if structure == "floating_pill":
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
    <header className="sticky top-4 z-50 w-full px-4">
      {accent}<div className="mx-auto flex {height_cls} w-full max-w-4xl items-center justify-between rounded-full border border-border bg-background/85 px-3 pl-6 shadow-lg backdrop-blur supports-[backdrop-filter]:bg-background/65">
        {brand}
        <nav className="hidden items-center gap-7 md:flex" aria-label="Primary">
          {nav_map}
        </nav>
        <div className="hidden md:block">{desktop_cta}</div>
        <button
          type="button"
          onClick={{() => setMobileOpen(!mobileOpen)}}
          className="inline-flex h-10 w-10 items-center justify-center rounded-full text-foreground md:hidden"
          aria-label={{mobileOpen ? 'Close menu' : 'Open menu'}}
          aria-expanded={{mobileOpen}}
        >
          {{mobileOpen ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}}
        </button>
      </div>
      {mobile_nav}
    </header>
  );
}}
"""

    # ── structure: centered_logo ──────────────────────────────
    if structure == "centered_logo":
        # Centered layout always uses uppercase small-caps nav (editorial feel)
        cnav_cls = (
            "text-xs font-medium uppercase tracking-[0.18em] text-background/70 transition-colors hover:text-background"
            if is_inv else
            "text-xs font-medium uppercase tracking-[0.18em] text-foreground/70 transition-colors hover:text-foreground"
        )
        return f"""'use client';

import Link from 'next/link';
import {{ useState{extra_import} }} from 'react';
import {{ {lucide_imports} }} from 'lucide-react';

const navLinks = [
  {nav_array}
];

export default function MarketingHeader() {{
  const [mobileOpen, setMobileOpen] = useState(false);{scroll_state}{scroll_effect}
  return (
    <>
      {accent}<header className={header_cls_attr}>
        <div className="mx-auto grid {height_cls} max-w-7xl grid-cols-[1fr_auto_1fr] items-center gap-6 px-4 sm:px-6 lg:px-8">
          <nav className="hidden items-center justify-end gap-8 md:flex" aria-label="Primary left">
            {{navLinks.slice(0, Math.ceil(navLinks.length / 2)).map((link) => (
              <Link key={{link.href}} href={{link.href}} className="{cnav_cls}">{{link.label}}</Link>
            ))}}
          </nav>
          <div className="flex justify-center">{brand}</div>
          <div className="hidden items-center justify-start gap-8 md:flex">
            <nav className="flex items-center gap-8" aria-label="Primary right">
              {{navLinks.slice(Math.ceil(navLinks.length / 2)).map((link) => (
                <Link key={{link.href}} href={{link.href}} className="{cnav_cls}">{{link.label}}</Link>
              ))}}
            </nav>
            {desktop_cta}
          </div>
          <div className="md:hidden col-start-3 justify-self-end">
            <button type="button" onClick={{() => setMobileOpen(!mobileOpen)}} className="inline-flex h-10 w-10 items-center justify-center rounded-md" aria-label={{mobileOpen ? 'Close menu' : 'Open menu'}} aria-expanded={{mobileOpen}}>
              {{mobileOpen ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}}
            </button>
          </div>
        </div>
      </header>
      {mobile_nav}
    </>
  );
}}
"""

    # ── structure: two_row ────────────────────────────────────
    if structure == "two_row":
        row_nav_cls = (_NAV_CLS_INV_MAP if is_inv else _NAV_CLS_MAP).get(nav_style, _NAV_CLS_MAP["plain"])
        return f"""'use client';

import Link from 'next/link';
import {{ useState{extra_import} }} from 'react';
import {{ {lucide_imports} }} from 'lucide-react';

const navLinks = [
  {nav_array}
];

export default function MarketingHeader() {{
  const [mobileOpen, setMobileOpen] = useState(false);{scroll_state}{scroll_effect}
  return (
    <>
      {accent}<header className={header_cls_attr}>
        <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
          <div className="flex h-14 items-center justify-between">
            {brand}
            <div className="hidden md:block">{desktop_cta}</div>
            <button type="button" onClick={{() => setMobileOpen(!mobileOpen)}} className="inline-flex h-9 w-9 items-center justify-center rounded-md text-foreground md:hidden" aria-label={{mobileOpen ? 'Close' : 'Menu'}} aria-expanded={{mobileOpen}}>
              {{mobileOpen ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}}
            </button>
          </div>
          <nav className="hidden items-center gap-8 border-t border-border/40 py-2.5 md:flex" aria-label="Primary">
            {{navLinks.map((link) => (
              <Link key={{link.href}} href={{link.href}} className="{row_nav_cls}">{{link.label}}</Link>
            ))}}
          </nav>
        </div>
      </header>
      {mobile_nav}
    </>
  );
}}
"""

    # ── default: logo_left_nav_right ──────────────────────────
    return f"""'use client';

import Link from 'next/link';
import {{ useState{extra_import} }} from 'react';
import {{ {lucide_imports} }} from 'lucide-react';

const navLinks = [
  {nav_array}
];

export default function MarketingHeader() {{
  const [mobileOpen, setMobileOpen] = useState(false);{scroll_state}{scroll_effect}
  return (
    <>
      {accent}<header className={header_cls_attr}>
        <div className="mx-auto flex {height_cls} max-w-7xl items-center justify-between px-4 sm:px-6 lg:px-8">
          {brand}
          <nav className="hidden items-center gap-8 md:flex" aria-label="Primary">
            {nav_map}
          </nav>
          <div className="hidden md:flex md:items-center md:gap-3">
            {desktop_cta}
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
      </header>
      {mobile_nav}
    </>
  );
}}
"""


# ───────────────────────────────────────────────────────────────
#  Main entry.
# ───────────────────────────────────────────────────────────────
def _inject_nav_anchor_helper(jsx: str) -> str:
    """Post-process emitted header JSX so in-page anchor nav actually scrolls.

    Why: nav iterations across all variants render `<Link key={link.href} href={link.href}>`.
    Next.js `<Link>` routes hash hrefs through the router, which does NOT reliably
    trigger native browser scroll-to-element on the same page. Plain `<a href="#id">`
    does — but if any project also has `/route` items in the nav, plain `<a>` causes
    a full page reload.

    Fix: replace nav-iteration `<Link …>` with a small `NavAnchor` helper that picks
    `<a>` for hash hrefs (in-page scroll) and `<Link>` for everything else (route nav).
    Brand `<Link href="/">` and CTA `<Link href={cta_href_attr}>` are left untouched —
    they don't carry `key={link.href}` so the regex doesn't match them.
    """
    helper_js = (
        "\n"
        "function NavAnchor({ href, onClick, children, ...rest }) {\n"
        "  const isHash = typeof href === 'string' && href.startsWith('#');\n"
        "  const handleClick = (e) => {\n"
        "    if (typeof onClick === 'function') onClick(e);\n"
        "    if (e.defaultPrevented) return;\n"
        "    if (!isHash) return;\n"
        "    if (typeof document === 'undefined') return;\n"
        "    const id = href.slice(1);\n"
        "    const el = document.getElementById(id);\n"
        "    if (!el) return;\n"
        "    e.preventDefault();\n"
        "    el.scrollIntoView({ behavior: 'smooth', block: 'start' });\n"
        "    if (typeof history !== 'undefined' && history.replaceState) {\n"
        "      history.replaceState(null, '', href);\n"
        "    }\n"
        "  };\n"
        "  if (isHash) {\n"
        "    return <a href={href} onClick={handleClick} {...rest}>{children}</a>;\n"
        "  }\n"
        "  return <Link href={href} onClick={handleClick} {...rest}>{children}</Link>;\n"
        "}\n"
    )

    # Walk source and rewrite nav-iteration <Link …> blocks.
    out: list[str] = []
    i = 0
    while i < len(jsx):
        idx = jsx.find('<Link', i)
        if idx < 0:
            out.append(jsx[i:])
            break
        out.append(jsx[i:idx])
        # Find this tag's closing >
        depth = 0
        j = idx
        while j < len(jsx):
            c = jsx[j]
            if c == '{':
                depth += 1
            elif c == '}':
                depth = max(0, depth - 1)
            elif c == '>' and depth == 0:
                break
            j += 1
        if j >= len(jsx):
            out.append(jsx[idx:])
            break
        opening = jsx[idx:j + 1]
        if 'key={link.href}' in opening:
            # Nav-iteration link → rewrite tag and find matching </Link>.
            new_open = '<NavAnchor' + opening[len('<Link'):]
            close_idx = jsx.find('</Link>', j + 1)
            if close_idx < 0:
                out.append(opening)
                i = j + 1
                continue
            body = jsx[j + 1:close_idx]
            out.append(new_open + body + '</NavAnchor>')
            i = close_idx + len('</Link>')
        else:
            out.append(opening)
            i = j + 1
    rewritten = ''.join(out)
    if 'NavAnchor' not in rewritten:
        return rewritten

    # Inject the helper definition after the last `import …;` line.
    import_re = re.compile(r"^(?:import [^\n]+;\s*\n)+", re.MULTILINE)
    m = import_re.search(rewritten)
    if not m:
        return helper_js + rewritten
    insert_at = m.end()
    return rewritten[:insert_at] + helper_js + rewritten[insert_at:]


def build_marketing_header_jsx(
    brand_name: str,
    brand_mark: dict | None,
    navigation: list | None,
    domain: str,
    archetype: str,
    design: dict | None = None,
    header_spec: dict | None = None,
) -> tuple[str, str]:
    """Return ``(jsx_source, variant_name)`` for the project's header.

    When ``header_spec`` is provided (parsed from ===HEADER_DESIGN=== in Gemini
    research), the compositional renderer is used and the returned variant_name
    is ``"spec:<structure>/<surface>"``. Otherwise falls back to
    ``pick_header_variant()`` and the preset renderers.
    """
    brand_mark = brand_mark or {}
    navigation = navigation or []

    nav_items = _flatten_nav(navigation)
    cta_text, cta_href = _derive_cta(domain, archetype)
    brand_block = _render_brand(brand_name, brand_mark)
    nav_array = _nav_array_literal(nav_items)

    treatment = (brand_mark.get("treatment") or "wordmark").lower()
    lucide = _lucide_imports(treatment, "spec" if header_spec else "solid_bordered")

    cta_text_jsx = json.dumps(cta_text)
    cta_href_attr = json.dumps(cta_href)

    if header_spec:
        try:
            jsx = render_from_spec(
                spec=header_spec,
                brand_block=brand_block,
                nav_array=nav_array,
                cta_text_jsx=cta_text_jsx,
                cta_href_attr=cta_href_attr,
                lucide_imports=lucide,
            )
            variant_name = f"spec:{header_spec.get('structure', '?')}/{header_spec.get('surface', '?')}"
            return _inject_nav_anchor_helper(jsx), variant_name
        except Exception as _spec_exc:
            logger.warning("render_from_spec failed (%s), falling back to preset", _spec_exc)

    variant = pick_header_variant(archetype, design)
    renderer = _VARIANT_RENDERERS.get(variant, _render_solid_bordered)
    jsx = renderer(
        brand_block=brand_block,
        nav_array=nav_array,
        cta_text_jsx=cta_text_jsx,
        cta_href_attr=cta_href_attr,
        lucide_imports=lucide,
    )
    return _inject_nav_anchor_helper(jsx), variant
