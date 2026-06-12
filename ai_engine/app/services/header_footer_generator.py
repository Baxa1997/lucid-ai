"""Dynamic Header & Footer Claude generators for the unified website pipeline.

The previous architecture used `marketing_header_builder.py` and
`marketing_footer_builder.py` which produced ~8 template variants by
combining a few axes. That's template work, not design. Every Italian
café got the same 2-3 header shapes.

This module generates the MarketingHeader.jsx and MarketingFooter.jsx
as parallel Claude calls, each receiving the full visual_dna + brand
context. Same input contract as page_generator — both run in the same
parallel pool as page generation.

Each call is ~60s timeout (smaller than a page), 4K tokens.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

_HF_MAX_TOKENS = 16000  # was 4000 — Sonnet 4.6 + tool_use schema overhead can
                        # exhaust 4K before emitting useful content, returning
                        # stop=max_tokens with empty tool_use_input. Symptom:
                        # MarketingHeader/Footer FAILED → site has no nav, looks
                        # like a single-page landing. 16K leaves comfortable room.
_HF_TIMEOUT_S = 180.0   # was 60s — header/footer streams under heavy parallel
                        # load can stall briefly; 60s killed otherwise-fine calls.
_HF_MAX_ATTEMPTS = 2


# Industries whose audience expects a modern lifestyle/editorial header — the
# default archetype here is transparent-pill (floating, blur on scroll). Any
# domain word that contains one of these substrings counts.
_LIFESTYLE_KEYWORDS = (
    "travel", "tour", "hotel", "resort", "spa", "restaurant", "cafe", "café",
    "coffee", "bakery", "fashion", "beauty", "wellness", "fitness", "yoga", "salon",
    "boutique", "gallery", "studio", "creative", "music", "wedding", "event",
    "hospitality", "lifestyle", "food", "beverage", "real estate", "luxury",
    "winery", "vineyard", "brewery", "distillery", "patisserie",
)


_ARCHETYPE_GUIDE = {
    "transparent-pill": (
        "TRANSPARENT-PILL — at top of page: bg-transparent (NO bg-background, NO border). "
        "Wordmark + nav float directly on the hero image. On scroll past 16px: "
        "wrap the nav+CTA in a `rounded-full bg-background/80 backdrop-blur-xl border border-border/40 "
        "shadow-sm` pill container (max-w-fit, mx-auto). Lifestyle / hospitality / travel / editorial vibe. "
        "NEVER a solid colored bar at the top — that kills the hero photo's impact."
    ),
    "centered-logo": (
        "CENTERED-LOGO — symmetrical 3-column header: nav links left | wordmark center | CTA + nav right. "
        "At top: transparent or bg-background/0. On scroll: bg-background/95 backdrop-blur-md + border-b. "
        "Wordmark uses display serif type at a larger size than peripheral nav. Editorial / luxury / "
        "boutique hospitality vibe."
    ),
    "mega-menu": (
        "MEGA-MENU — solid sticky bar from scroll 0 (bg-background/95 + border-b). Top-level nav items "
        "open a WIDE dropdown panel (4-column grid: categories, featured items, image, CTA). "
        "Dense, content-rich. E-commerce / large catalog / multi-product vibe."
    ),
    "side-rail": (
        "SIDE-RAIL — fixed VERTICAL nav fixed to the left edge (w-16 lg:w-20, full viewport height). "
        "Icon-only with hover labels (Tooltip or hover:pl-32). Main content of the page expects "
        "pl-16 lg:pl-20 to clear the rail. Portfolio / editorial / minimalist gallery vibe. "
        "NOTE: this header pattern only works if the rest of the site reserves left padding — "
        "if unsure, fall back to transparent-pill."
    ),
    "sticky-bar": (
        "STICKY-BAR — classic horizontal bar, sticky from scroll 0. At top: bg-background/95 + border-b. "
        "On scroll past 64px: bg-background + shadow-sm (slightly heavier). Conservative, navigable. "
        "B2B / SaaS / professional services / finance / healthcare vibe."
    ),
}


def _detect_header_archetype(anatomy: str, domain: str) -> str:
    """Pull the chosen archetype out of the research anatomy paragraph,
    falling back to a domain-aware default. Returns one of the keys in
    `_ARCHETYPE_GUIDE`.
    """
    a = (anatomy or "").lower()
    for arch in ("transparent-pill", "centered-logo", "mega-menu", "side-rail", "sticky-bar"):
        # Match both hyphenated and space-separated forms
        if arch in a or arch.replace("-", " ") in a:
            return arch
    d = (domain or "").lower()
    if any(k in d for k in _LIFESTYLE_KEYWORDS):
        return "transparent-pill"
    return "sticky-bar"


def _format_palette_block(palette: dict) -> str:
    """Render the 8-slot palette as a compact HSL reference so Claude knows
    which colors are warm/cool and uses Tailwind class tokens against them
    instead of defaulting to neutral foreground/background."""
    if not isinstance(palette, dict) or not palette:
        return "  (no palette — fall back to bg-primary / text-foreground / border-border tokens)"
    slot_class = {
        "primary":    "bg-primary text-primary-foreground",
        "secondary":  "bg-secondary text-secondary-foreground",
        "accent":     "bg-accent text-accent-foreground",
        "background": "bg-background",
        "foreground": "text-foreground",
        "muted":      "bg-muted text-muted-foreground",
        "border":     "border-border",
        "card":       "bg-card text-card-foreground",
    }
    lines: list[str] = []
    for slot, classes in slot_class.items():
        hsl = (palette.get(slot) or "").strip()
        if hsl:
            lines.append(f"  {slot:<10} hsl({hsl})  →  {classes}")
    return "\n".join(lines) if lines else "  (palette empty)"


def _hf_system_prompt(
    *,
    component: str,           # "MarketingHeader" | "MarketingFooter"
    brand_name: str,
    tagline: str,
    domain: str,
    visual_dna: dict,
) -> str:
    vd = visual_dna or {}
    intensity = (vd.get("cultural_intensity") or "bold").strip().lower()
    motifs = vd.get("decorative_motifs") or []
    icons = vd.get("iconography_anchors") or []
    palette_emph = (vd.get("cultural_palette_emphasis") or "").strip()
    type_voice = (vd.get("typography_voice") or "").strip()
    palette = vd.get("palette") or {}

    anatomies = vd.get("section_anatomies") or {}
    key = "header" if component == "MarketingHeader" else "footer"
    anatomy = (anatomies.get(key) or "").strip()

    # Detect chosen archetype from anatomy paragraph (extractor encodes it
    # there as "transparent-pill", "centered-logo", etc.). Falls back to a
    # domain-aware default — lifestyle/hospitality → transparent-pill,
    # otherwise sticky-bar.
    archetype = ""
    archetype_block = ""
    if component == "MarketingHeader":
        archetype = _detect_header_archetype(anatomy, domain)
        guide = _ARCHETYPE_GUIDE.get(archetype, "")
        archetype_block = (
            f"\n\nHEADER ARCHETYPE: {archetype}\n  {guide}\n"
            "  This archetype is NON-NEGOTIABLE — it was picked from research "
            "for THIS brand's category. Do not silently substitute a generic sticky bar."
        )

    intensity_note = (
        "Cultural cues sit in accent positions only. Modern restraint dominates."
        if intensity == "subtle"
        else "Cultural cues take a strong role — wordmark uses signature typography, "
             "motif or iconography anchors the brand, color emphasis is unmistakable. "
             "Solid neutral bars (plain white-on-black or black-on-white) are a FAILURE for this intensity — "
             "use the palette colors below as real surface or accent, not just as a 1px border."
    )

    motifs_block = ""
    if motifs:
        motifs_block = "\n".join(f"  - {m}" for m in motifs[:4])
        motifs_block = f"\n\nDECORATIVE MOTIFS:\n{motifs_block}"
    icons_block = ""
    if icons:
        icons_block = f"\n\nICONOGRAPHY ANCHORS: {', '.join(icons[:6])}"

    anatomy_block = ""
    if anatomy:
        anatomy_block = f"\n\nANATOMY (from research):\n  {anatomy}"

    palette_block = _format_palette_block(palette)

    return f"""You are generating ONE component: {component}.jsx for a brand-coherent website.

BRAND
  Name: {brand_name}
  Tagline: {tagline}
  Domain: {domain}

VISUAL DNA — PRIMARY DESIGN DIRECTIVE
  Cultural intensity: {intensity}
    → {intensity_note}

  Cultural palette emphasis: {palette_emph or '(use foundation palette as-is)'}
  Typography voice: {type_voice or '(neutral)'}{motifs_block}{icons_block}{anatomy_block}{archetype_block}

PALETTE (use these Tailwind classes — do NOT default to plain neutral white/black):
{palette_block}

FOUNDATION (DO NOT regenerate — import from these):
  - src/config/site.js          → {{ siteConfig }}: brand name, tagline, logo
  - src/config/navigation.js    → {{ mainNav, footerNav }}: nav items with icon names
  - src/lib/design-system.js    → {{ ds }}: spacing/typography/button class tokens
  - src/app/globals.css         → palette + Google Fonts (in :root)

OUTPUT CONTRACT
  Return JSON: {{"files": [{{"path": "src/components/layout/{component}.jsx", "content": "..."}}]}}
  Single file. Tailwind classes only. No inline styles.
  Default export named {component}.
  Use 'use client' directive (Next.js app router).

QUALITY BAR
  - MUST visibly reflect VISUAL_DNA in ≥2 ways (color emphasis, motif placement, typography voice, iconography).
  - A generic header/footer that could fit any brand is a FAILURE.
  - For lifestyle/hospitality/travel brands (intensity=bold), a plain white-or-black solid bar at the top is a FAILURE — it makes the page feel like a 2018 SaaS template.
"""


def _header_user_prompt(
    *,
    brand_name: str,
    domain: str,
    visual_dna: dict,
) -> str:
    # Determine archetype again so the user prompt can give shape-specific
    # SCROLL behavior (system prompt declares it; user prompt operationalizes it).
    anatomy = ((visual_dna or {}).get("section_anatomies") or {}).get("header") or ""
    archetype = _detect_header_archetype(anatomy, domain)

    scroll_instructions = {
        "transparent-pill": (
            "1. SCROLL-AWARE PILL: useState + useEffect on window.scrollY.\n"
            "   - At top (scrollY < 16): outer wrapper is `bg-transparent border-transparent` — NO bar.\n"
            "     The wordmark + nav + CTA float directly on the hero image.\n"
            "   - Past 16px: wrap nav+CTA in a centered pill:\n"
            "       `rounded-full bg-background/80 backdrop-blur-xl border border-border/40 shadow-sm px-4 py-2`\n"
            "       with `max-w-fit mx-auto` so it doesn't span the whole viewport.\n"
            "   - Smooth 200ms transition on background/border/shadow.\n"
            "   - DO NOT add a solid bar at the top. The hero photo MUST be unobstructed at scroll 0."
        ),
        "centered-logo": (
            "1. CENTERED-LOGO LAYOUT (symmetric three-column):\n"
            "   - Left: first half of mainNav items\n"
            "   - Center: wordmark, larger than nav, font-heading\n"
            "   - Right: second half of mainNav + CTA button\n"
            "   - At top: bg-transparent. On scroll past 16px: bg-background/95 backdrop-blur-md border-b.\n"
            "   - Mobile collapses to: wordmark center + hamburger right."
        ),
        "mega-menu": (
            "1. MEGA-MENU: solid sticky bar from scroll 0 (`bg-background/95 backdrop-blur border-b`).\n"
            "   - Top-level nav items open a WIDE dropdown panel on hover/focus:\n"
            "     4-column grid (categories | featured | image preview | CTA card).\n"
            "   - Use `group` + `group-hover:opacity-100` Tailwind pattern (no extra state needed).\n"
            "   - Mobile: collapse mega panels to nested accordion under each nav item."
        ),
        "side-rail": (
            "1. SIDE-RAIL: fixed left rail `fixed left-0 top-0 h-screen w-16 lg:w-20 bg-card border-r`.\n"
            "   - Vertical icon-only nav, hover reveals labels (`group-hover:w-48` expand).\n"
            "   - Wordmark at top (vertical or rotated -90deg).\n"
            "   - CTA pinned to bottom.\n"
            "   - Mobile: collapse to a top bar with hamburger; the rail re-emerges at lg breakpoint."
        ),
        "sticky-bar": (
            "1. STICKY BAR: classic horizontal bar, sticky from scroll 0.\n"
            "   - At top: `bg-background/95 backdrop-blur border-b border-border/60`.\n"
            "   - On scroll past 64px: `bg-background shadow-sm` (a touch heavier).\n"
            "   - Use 200ms transition between states."
        ),
    }[archetype]

    return f"""Generate src/components/layout/MarketingHeader.jsx using the {archetype} archetype.

REQUIRED ELEMENTS

{scroll_instructions}

2. WORDMARK: use {{ siteConfig.name }} from '@/config/site'
   - Apply font-heading class
   - Optional lucide-react icon glyph matching the domain (Coffee for café, MapPin for travel,
     Sparkles for spa, Utensils for restaurant, etc.) — pair it with the wordmark, color it `text-primary`.
   - Reflect typography_voice from VISUAL_DNA (script-flavored, geometric, etc.).

3. NAV LINKS — MANDATORY, NOT CONDITIONAL:
   - import {{ mainNav }} from '@/config/navigation' (do NOT redefine the list).
   - Render `mainNav.map(...)` UNCONDITIONALLY — no `mainNav.length > 0 &&` short-circuit.
     If mainNav is empty the map renders nothing and the header looks like a single-page
     stub. The pipeline guarantees mainNav has ≥3 entries (either page routes for a
     multi-page site, or anchor fallbacks like #services / #about / #contact for
     single-page); a header with no `<nav>` block is a defect.
   - Render with hover underline OR background pill — never plain text only.
   - Active route gets `text-primary` (read pathname via usePathname).
   - The visible `<nav>` element (or container div carrying the links) MUST appear in
     BOTH the desktop layout AND the mobile menu panel. Hiding nav at md:- is allowed
     ONLY behind the hamburger — never hide it from every breakpoint.

4. PROJECT-AWARE CTA: button text MUST match the domain:
   - Restaurant      → "Reserve a Table"
   - Coffee / café   → "Order Online"
   - Bakery          → "Pre-Order"
   - Fitness / gym   → "Book a Class"
   - Hotel / resort  → "Book Your Stay"
   - Travel / tour   → "Plan Your Trip" or "Book a Tour"
   - Salon / spa     → "Book Appointment"
   - Real estate     → "Browse Listings"
   - Portfolio       → "Start a Project"
   - SaaS            → "Start Free" / "Get Demo"
   Default for this domain ({domain}): pick the best fit.
   - Style: `bg-primary text-primary-foreground rounded-full px-5 py-2 hover:opacity-90`.

5. MOBILE MENU: useState + Menu/X icons from lucide-react.
   - Opens full-width panel below header with nav links + CTA.
   - Auto-close on pathname change (usePathname() from 'next/navigation').

6. COLOR AGGRESSION: use the palette tokens from the system prompt at least TWICE — wordmark icon in
   `text-primary`, active nav in `text-primary`, CTA `bg-primary text-primary-foreground`, accent
   underline using `bg-accent`. A header that only uses `text-foreground` + `bg-background` is a FAILURE.

7. VISIBLE VISUAL_DNA: incorporate motif or iconography_anchors somewhere — wordmark accent, divider
   ornament, hover state ornament, etc. Tiny SVG inline is fine.

Use 'use client'. Default export MarketingHeader. Return JSON with files array.
"""


def _footer_user_prompt(
    *,
    brand_name: str,
    domain: str,
    visual_dna: dict,
) -> str:
    anatomies = (visual_dna or {}).get("section_anatomies") or {}
    footer_anatomy = (anatomies.get("footer") or "").strip()
    # Surface the variant hint if research picked one
    variant_hint = ""
    if footer_anatomy:
        variant_hint = f"\n\nVARIANT HINT FROM RESEARCH:\n  {footer_anatomy}\n"

    return f"""Generate src/components/layout/MarketingFooter.jsx.{variant_hint}

REQUIRED ELEMENTS
1. WORDMARK: use {{ siteConfig.name }} from '@/config/site'
   - Apply font-heading class
   - Optional brief tagline below from {{ siteConfig.tagline }}

2. FOOTER NAV: import {{ footerNav }} from '@/config/navigation' (do NOT redefine)
   - Render as column groups OR a single minimalist row depending on variant
   - Each column has a heading + list of links

3. SOCIAL ROW: simple icon links (lucide-react: Twitter, Instagram, Facebook, etc.)
   - Read from {{ siteConfig.social }} if present, else show 2-3 generic placeholders

4. BOTTOM BAR: copyright + brand-appropriate tagline or motto
   - © {{ new Date().getFullYear() }} {{ siteConfig.name }}
   - Border-top separator

5. VISIBLE VISUAL_DNA: pick a footer variant that matches the brand's energy:
   - "mega-columns"      → bold/luxury brands with rich navigation
   - "minimalist-row"    → focused/modern brands
   - "cta-band"          → conversion-focused (SaaS, services)
   - "centered-stack"    → editorial/portfolio brands
   Incorporate motif/iconography somewhere — divider, link accent, badge, etc.

Use 'use client' (only if needed for the year hydration — otherwise skip).
Default export MarketingFooter. Return JSON with files array.
"""


async def _generate_one(
    *,
    component: str,
    user_prompt: str,
    sys_prompt: str,
    api_key: str,
    websocket: Any,
) -> dict | None:
    """Run the Claude call with retry. Returns {path, content} or None."""
    from app.services.llm_json_client import call_claude_for_json

    last_failure = "unknown"
    for attempt in range(1, _HF_MAX_ATTEMPTS + 1):
        try:
            result = await asyncio.wait_for(
                call_claude_for_json(
                    system_prompt=sys_prompt,
                    user_prompt=user_prompt,
                    api_key=api_key,
                    websocket=websocket,
                    max_tokens=_HF_MAX_TOKENS,
                ),
                timeout=_HF_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            last_failure = f"timeout after {_HF_TIMEOUT_S}s"
            logger.warning("hf_generator: %s timeout %d/%d",
                           component, attempt, _HF_MAX_ATTEMPTS)
            continue
        except Exception as exc:
            last_failure = f"exception: {exc}"
            logger.warning("hf_generator: %s threw %d/%d — %s",
                           component, attempt, _HF_MAX_ATTEMPTS, exc)
            continue

        if not result or "files" not in result:
            last_failure = "no files key"
            continue
        files = result.get("files") or []
        for f in files:
            if not isinstance(f, dict):
                continue
            path = (f.get("path") or "").strip()
            content = (f.get("content") or "")
            if path and content and (path.endswith(".jsx") or path.endswith(".tsx")):
                logger.info("hf_generator: %s ok on attempt %d (%d chars)",
                            component, attempt, len(content))
                return {"path": path, "content": content}
        last_failure = "no jsx/tsx file in response"

    logger.error("hf_generator: %s FAILED after %d attempts — %s",
                 component, _HF_MAX_ATTEMPTS, last_failure)
    return None


async def generate_header(
    *,
    brand_name: str,
    tagline: str,
    domain: str,
    visual_dna: dict,
    api_key: str,
    websocket: Any = None,
) -> dict | None:
    """Generate MarketingHeader.jsx. Returns {path, content} or None."""
    sys_p = _hf_system_prompt(
        component="MarketingHeader",
        brand_name=brand_name, tagline=tagline,
        domain=domain, visual_dna=visual_dna,
    )
    usr_p = _header_user_prompt(
        brand_name=brand_name, domain=domain, visual_dna=visual_dna,
    )
    return await _generate_one(
        component="MarketingHeader",
        sys_prompt=sys_p, user_prompt=usr_p,
        api_key=api_key, websocket=websocket,
    )


async def generate_footer(
    *,
    brand_name: str,
    tagline: str,
    domain: str,
    visual_dna: dict,
    api_key: str,
    websocket: Any = None,
) -> dict | None:
    """Generate MarketingFooter.jsx. Returns {path, content} or None."""
    sys_p = _hf_system_prompt(
        component="MarketingFooter",
        brand_name=brand_name, tagline=tagline,
        domain=domain, visual_dna=visual_dna,
    )
    usr_p = _footer_user_prompt(
        brand_name=brand_name, domain=domain, visual_dna=visual_dna,
    )
    return await _generate_one(
        component="MarketingFooter",
        sys_prompt=sys_p, user_prompt=usr_p,
        api_key=api_key, websocket=websocket,
    )
