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

_HF_MAX_TOKENS = 4000
_HF_TIMEOUT_S = 60.0
_HF_MAX_ATTEMPTS = 2


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

    anatomies = vd.get("section_anatomies") or {}
    key = "header" if component == "MarketingHeader" else "footer"
    anatomy = (anatomies.get(key) or "").strip()

    intensity_note = (
        "Cultural cues sit in accent positions only. Modern restraint dominates."
        if intensity == "subtle"
        else "Cultural cues take a strong role — wordmark uses signature typography, "
             "motif or iconography anchors the brand, color emphasis is unmistakable."
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

    return f"""You are generating ONE component: {component}.jsx for a brand-coherent website.

BRAND
  Name: {brand_name}
  Tagline: {tagline}
  Domain: {domain}

VISUAL DNA — PRIMARY DESIGN DIRECTIVE
  Cultural intensity: {intensity}
    → {intensity_note}

  Cultural palette emphasis: {palette_emph or '(use foundation palette as-is)'}
  Typography voice: {type_voice or '(neutral)'}{motifs_block}{icons_block}{anatomy_block}

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
"""


def _header_user_prompt(
    *,
    brand_name: str,
    domain: str,
    visual_dna: dict,
) -> str:
    return f"""Generate src/components/layout/MarketingHeader.jsx.

REQUIRED ELEMENTS
1. SCROLL-AWARE: useState + useEffect on window.scrollY.
   - At top (scrollY < 16): transparent or near-transparent (bg-background/0 or bg-background/40)
   - Past 16px: bg-background/95 + backdrop-blur + border-b
   - Smooth transition

2. WORDMARK: use {{ siteConfig.name }} from '@/config/site'
   - Apply font-heading class
   - Optional lucide-react icon glyph matching the domain (Coffee for café, Sparkles for spa, etc.)
   - Reflect typography_voice from VISUAL_DNA

3. NAV LINKS: import {{ mainNav }} from '@/config/navigation' (do NOT redefine)
   - Render with hover underline or background pill — never plain text only

4. PROJECT-AWARE CTA: button text MUST match the domain:
   - Restaurant      → "Reserve a Table"
   - Coffee / café   → "Order Online"
   - Bakery          → "Pre-Order"
   - Fitness / gym   → "Book a Class"
   - Hotel           → "Book Your Stay"
   - Salon / spa     → "Book Appointment"
   - Real estate     → "Browse Listings"
   - Portfolio       → "Start a Project"
   - SaaS            → "Start Free" / "Get Demo"
   Default for this domain ({domain}): pick the best fit.

5. MOBILE MENU: useState + Menu/X icons from lucide-react
   - Opens full-width panel below header with nav links + CTA
   - Auto-close on pathname change (usePathname() from 'next/navigation')

6. VISIBLE VISUAL_DNA: incorporate motif/iconography/cultural color emphasis somewhere — wordmark accent, divider, hover state, etc.

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
    from app.services.project_generator import call_claude_for_json

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
