"""
Design System Builder — one dedicated Claude call that produces a bespoke,
validated, locked design system BEFORE code generation.

Why this exists:
  The old flow let Gemini research free-text a verbal "design blueprint"
  which then leaked inconsistently into 3-5 code-generation phases. Results
  drifted: hero used one palette, features another, cards reinvented
  radius/shadow per section. Quality was capped at "whatever Gemini typed".

  This module replaces that drift with one source of truth:
    (a) One Claude call with a senior-design-director system prompt.
    (b) Structured JSON output via tool-forcing (no free text).
    (c) Deterministic validation — contrast, type scale, grid alignment.
    (d) Retry once on validation failure with the specific violations fed back.

  The resulting design system is rendered into the same ===HEADER=== blocks
  the downstream schema builder and code-generation prompts already parse
  (CSS_VARIABLES, FONTS, DESIGN_SYSTEM_NAME, PALETTE, TYPOGRAPHY,
  LAYOUT_BLUEPRINT). No changes needed in downstream consumers — they just
  see stronger, more consistent content.

Usage:
    from app.services.design_system_builder import build_design_system

    design = await build_design_system(
        description=description,
        domain=_domain,
        brand_name=brand_name,
        copy_tone=copy_tone,
        layout_archetype=_layout_archetype,
        api_key=api_key,
        websocket=websocket,
    )
    research = inject_design_blocks(research, design)
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx

logger = logging.getLogger("lucid.design_system_builder")


# ─── Claude call constants ───────────────────────────────────────────────────

_API_URL = "https://api.anthropic.com/v1/messages"
_MODEL = "claude-sonnet-4-6"
_MAX_TOKENS = 4000
_TIMEOUT = 90.0


# ─── Curated font pairings ──────────────────────────────────────────────────
# 12 hand-picked Google Fonts pairings the Director chooses from. Constraining
# to a curated list (vs free-form "pick any Google font") is what kills the
# Inter+Geist / Inter+Inter convergence that makes every AI-generated site
# look the same. Each entry has concrete Google Fonts CSS2 URLs and matched
# weight defaults so the renderer can build globals.css verbatim regardless
# of what free-form strings the Director also emits.

_FONT_PAIRINGS: dict[str, dict] = {
    "fraunces-inter": {
        "heading_font": "Fraunces",
        "heading_font_url": "https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght@0,9..144,300..900;1,9..144,300..900&display=swap",
        "body_font": "Inter",
        "body_font_url": "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap",
        "heading_weight": "700",
        "body_weight": "400",
        "vibe": "warm editorial — italic display serif + neutral sans",
        "fits_archetypes": ["warm_artisan", "magazine_editorial", "editorial_serif_minimal", "quiet_luxury"],
    },
    "bricolage-grotesque-solo": {
        "heading_font": "Bricolage Grotesque",
        "heading_font_url": "https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,200..800&display=swap",
        "body_font": "Bricolage Grotesque",
        "body_font_url": "https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,200..800&display=swap",
        "heading_weight": "700",
        "body_weight": "400",
        "vibe": "modern grotesque — single variable family with weight contrast",
        "fits_archetypes": ["spatial_functional", "neo_swiss", "minimal_luxe", "fluid_typographic"],
    },
    "dm-serif-dm-sans": {
        "heading_font": "DM Serif Display",
        "heading_font_url": "https://fonts.googleapis.com/css2?family=DM+Serif+Display:ital@0;1&display=swap",
        "body_font": "DM Sans",
        "body_font_url": "https://fonts.googleapis.com/css2?family=DM+Sans:opsz,wght@9..40,300..900&display=swap",
        "heading_weight": "400",
        "body_weight": "400",
        "vibe": "classic publication — high-contrast serif display + clean sans",
        "fits_archetypes": ["magazine_editorial", "editorial_serif_minimal", "quiet_luxury"],
    },
    "space-grotesk-jetbrains": {
        "heading_font": "Space Grotesk",
        "heading_font_url": "https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@300..700&display=swap",
        "body_font": "JetBrains Mono",
        "body_font_url": "https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&display=swap",
        "heading_weight": "600",
        "body_weight": "400",
        "vibe": "terminal-precise — geometric sans + mono body for technical feel",
        "fits_archetypes": ["dense_luxury", "brutalist_mono", "tech_noir_gradient", "sharp_corporate"],
    },
    "playfair-source-sans": {
        "heading_font": "Playfair Display",
        "heading_font_url": "https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,400..900;1,400..900&display=swap",
        "body_font": "Source Sans 3",
        "body_font_url": "https://fonts.googleapis.com/css2?family=Source+Sans+3:ital,wght@0,200..900;1,200..900&display=swap",
        "heading_weight": "700",
        "body_weight": "400",
        "vibe": "luxury feminine — high-contrast Didone + restrained sans",
        "fits_archetypes": ["quiet_luxury", "magazine_editorial", "warm_artisan"],
    },
    "instrument-serif-instrument-sans": {
        "heading_font": "Instrument Serif",
        "heading_font_url": "https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&display=swap",
        "body_font": "Instrument Sans",
        "body_font_url": "https://fonts.googleapis.com/css2?family=Instrument+Sans:ital,wght@0,400..700;1,400..700&display=swap",
        "heading_weight": "400",
        "body_weight": "400",
        "vibe": "editorial italic — sibling typefaces, italic display headlines",
        "fits_archetypes": ["editorial_serif_minimal", "warm_artisan", "magazine_editorial", "dark_editorial"],
    },
    "archivo-archivo-narrow": {
        "heading_font": "Archivo Black",
        "heading_font_url": "https://fonts.googleapis.com/css2?family=Archivo+Black&display=swap",
        "body_font": "Archivo Narrow",
        "body_font_url": "https://fonts.googleapis.com/css2?family=Archivo+Narrow:ital,wght@0,400..700;1,400..700&display=swap",
        "heading_weight": "900",
        "body_weight": "400",
        "vibe": "bold + tight — heavy display contrast against narrow body",
        "fits_archetypes": ["high_contrast_brutalist", "brutalist_mono", "sharp_corporate", "dense_luxury"],
    },
    "crimson-pro-public-sans": {
        "heading_font": "Crimson Pro",
        "heading_font_url": "https://fonts.googleapis.com/css2?family=Crimson+Pro:ital,wght@0,200..900;1,200..900&display=swap",
        "body_font": "Public Sans",
        "body_font_url": "https://fonts.googleapis.com/css2?family=Public+Sans:ital,wght@0,100..900;1,100..900&display=swap",
        "heading_weight": "600",
        "body_weight": "400",
        "vibe": "official editorial — book-style serif + civic sans",
        "fits_archetypes": ["editorial_serif_minimal", "magazine_editorial", "scandi_clean", "quiet_luxury"],
    },
    "unbounded-manrope": {
        "heading_font": "Unbounded",
        "heading_font_url": "https://fonts.googleapis.com/css2?family=Unbounded:wght@200..900&display=swap",
        "body_font": "Manrope",
        "body_font_url": "https://fonts.googleapis.com/css2?family=Manrope:wght@200..800&display=swap",
        "heading_weight": "700",
        "body_weight": "400",
        "vibe": "futuristic display + soft humanist body",
        "fits_archetypes": ["tech_noir_gradient", "spatial_functional", "fluid_typographic", "playful_retro"],
    },
    "young-serif-rubik": {
        "heading_font": "Young Serif",
        "heading_font_url": "https://fonts.googleapis.com/css2?family=Young+Serif&display=swap",
        "body_font": "Rubik",
        "body_font_url": "https://fonts.googleapis.com/css2?family=Rubik:ital,wght@0,300..900;1,300..900&display=swap",
        "heading_weight": "400",
        "body_weight": "400",
        "vibe": "vintage editorial — rounded serif + neutral body",
        "fits_archetypes": ["warm_artisan", "botanical_organic", "magazine_editorial"],
    },
    "bodoni-moda-outfit": {
        "heading_font": "Bodoni Moda",
        "heading_font_url": "https://fonts.googleapis.com/css2?family=Bodoni+Moda:ital,opsz,wght@0,6..96,400..900;1,6..96,400..900&display=swap",
        "body_font": "Outfit",
        "body_font_url": "https://fonts.googleapis.com/css2?family=Outfit:wght@200..900&display=swap",
        "heading_weight": "700",
        "body_weight": "400",
        "vibe": "fashion editorial — high-contrast Didone + geometric sans",
        "fits_archetypes": ["quiet_luxury", "magazine_editorial", "minimal_luxe", "dark_editorial"],
    },
    "bricolage-jetbrains": {
        "heading_font": "Bricolage Grotesque",
        "heading_font_url": "https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,200..800&display=swap",
        "body_font": "JetBrains Mono",
        "body_font_url": "https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&display=swap",
        "heading_weight": "700",
        "body_weight": "400",
        "vibe": "tech editorial — variable display grotesque + mono body",
        "fits_archetypes": ["dense_luxury", "tech_noir_gradient", "spatial_functional"],
    },
}


# ─── System prompt — the taste instruction ──────────────────────────────────

_SYSTEM_PROMPT = """You are a senior design director with the taste of teams
behind Vercel, Linear, Stripe, Anthropic, Ramp, Attio, Notion, Mux, Framer.

YOUR JOB: design a polished, professional design system that fits THIS specific
project's domain. The bar is "looks like a real $5-10M company shipped it" — solid,
trustworthy, on-brand for the industry. NOT an Awwwards submission. NOT a portfolio
piece. NOT an experimental art project.

══════════════════════════════════════════════════════════════
STAY CONVENTIONAL — read this first, every time
══════════════════════════════════════════════════════════════

✓ DO pick palettes that fit the industry's actual conventions:
  - Coffee shop  → warm browns + cream + muted accent
  - Car luxury   → deep charcoal/black + warm metallic + restrained accent
  - Car sporting → high-contrast dark + saturated accent (electric blue / red)
  - Healthcare   → calm blue/green + neutral + plenty of whitespace
  - Finance      → trustworthy navy/charcoal + neutral + restrained accent
  - SaaS B2B     → clean neutrals + ONE brand accent
  - Wedding      → soft cream/sage/blush + serif headlines
  - Fitness      → bold dark + saturated single accent
  - E-commerce   → product-led neutrals + ONE accent for CTAs

✓ DO use standard radii (0.375rem / 0.5rem / 0.75rem). Pick ONE and stick with it.
✓ DO use standard type scales — body 16px, hero 48-64px (NOT 80px+).
✓ DO use 2 fonts max with clear hierarchy (display + body).

✗ DO NOT pick experimental/avant-garde archetypes when a conventional one fits.
✗ DO NOT pick unusual palettes (lime+magenta, cyberpunk, neon-on-pastel) on a
  business website. Save those for art-school portfolios.
✗ DO NOT pick text-7xl/text-8xl heroes — solid 5xl/6xl is the right scale.
✗ DO NOT pick ultra-tight leading like 0.85 — looks like a thesis project.
✗ DO NOT use fluid_typographic / editorial-art / "type IS the design" archetypes
  unless the user explicitly asked for an editorial publication.

HARD RULES:

1. TASTE BEARINGS
   - Colors: specific, restrained. Max 1 accent color. Background and
     foreground have WCAG AA contrast (4.5:1 minimum for body text).
     Never generic "blue 600" SaaS palettes on physical-world brands.
   - Typography: pair 2 Google Fonts with real personality contrast.
     Display + neutral sans is safest. All-sans with weight contrast also
     good. Monospace body is a statement — use sparingly. Never both
     heading and body as the same generic sans (Inter + Inter).
   - Spacing: base unit 4 or 8. Section padding at least 96px vertical on
     landing pages, 40-64px on admin/CRM/TMS.
   - Radius: pick ONE language (sharp / crisp / soft / organic) and stick
     with it. Emit exact per-element radii in radius_tokens — every
     component in the app MUST use one of those values, nothing else.

2. BRAND MARK
   - Every project gets a wordmark or monogram. Specify exact font,
     weight, tracking, and size behavior so the generated navbar/sidebar
     ALWAYS shows the brand — never a bare nav with no logo.

3. ANTI-GENERIC
   - Never output `217 91% 60%` (default Tailwind blue).
   - Never output `Inter + Inter` as the font pairing.
   - Never output `Inter + Geist` — overused by AI tools in 2024-2025.
   - Never output `0.5rem` radius with no thought.
   - Never use symmetric 3-column icon grids as features_archetype.
   - Never use gradient-pastel hero as hero_archetype.
   - Never ship a landing page with generic "About / Features / Pricing /
     Sign In / Get Started" nav for a physical-world brand.

3b. ANTI-2024-OVERUSE (these were cutting-edge in 2024, now clichés in 2026):
   - Never use a generic bento grid as the default features layout — Apple
     bento was fresh in 2022-2023, AI tools overused it through 2024-2025.
     Use bento ONLY if it's the most natural fit; otherwise pick zigzag,
     vertical-tabs, timeline, or numbered-editorial.
   - Never use floating gradient orbs as the ONLY hero decoration — two
     blurred circles behind a headline is the #1 AI-generated cliché signal.
     If you use orbs, combine with grain noise, topographic lines, or strong
     typographic elements so the page has a real identity.
   - Never default to glassmorphism (backdrop-blur cards) — peaked 2022,
     now associated with low-quality templates. Use glass only for a specific
     visual reason (e.g. overlay on a full-bleed photo), never as a card style.
   - Never use "frosted glass navbar on a white page" — meaningless blur with
     no photo underneath looks like an oversight, not a design choice.

4. PROJECT-SPECIFIC
   - A coffee roaster's palette should feel like espresso + cream + one
     warm accent. A B2B SaaS analytics tool should feel like charcoal +
     off-white + one signal color. A wedding venue should feel like linen
     + sage + one terracotta accent. Read the domain and vibe before
     picking ANY HSL value.

5. COMMIT TO A PERSONALITY (pick a CONVENTIONAL archetype that FITS the domain)

   PRIMARY ARCHETYPE LIST — pick from THIS list first. These are conventional,
   polished, production-grade. Each maps to specific industries:

     • clean_modern         — neutral palette + ONE accent, sans-serif, generous
                              whitespace, screenshot-led. SaaS / B2B / tech tools /
                              startup landing pages. Reference: Linear, Vercel, Notion.
     • minimalist_corporate — restrained palette (navy/charcoal/neutral), professional
                              sans, conservative spacing, trust signals. Finance,
                              law, consulting, B2B services. Reference: Stripe homepage.
     • warm_inviting        — earthy/warm palette, friendly serif or rounded sans,
                              photo-led, comfortable spacing. Coffee, restaurants,
                              cafes, bakeries. Reference: Blue Bottle, Sweetgreen.
     • luxury_minimal       — dark + premium accents (or cream + black), elegant serif
                              display, restrained accent color, high-quality photography.
                              Cars (luxury), watches, fashion, real estate, high-end
                              hotels. Reference: Aesop, Range Rover, Bottega.
     • editorial_classic    — magazine-style serif headlines + clean sans body,
                              photo-led, calm rhythm. Wedding venues, lifestyle brands,
                              cultural orgs, publications. Reference: Kinfolk, Cereal.
     • bold_modern          — high-contrast palette + saturated accent, strong sans
                              display, dynamic photography. Fitness, sports brands,
                              EV/sport cars, energy drinks. Reference: Nike, Tesla,
                              Strava.
     • soft_modern          — soft neutrals + sage/blush/cream accent, gentle serifs
                              or warm sans, comfortable spacing. Wellness, beauty,
                              wedding, parenting, lifestyle. Reference: Glossier, Goop.
     • product_focused      — pure-neutral chrome (white/black/gray) so the products
                              do all the visual work, generous grid, photo-led.
                              E-commerce, retail, marketplaces. Reference: Shopify
                              merchants, Apple Store.

   ADVANCED ARCHETYPES — only use when the domain genuinely requires it. NEVER pick
   these on a business website "to make it stand out":

     • dark_editorial       — near-black bg + editorial serif. Use ONLY for actual
                              editorial publications (magazines, online-magazines).
     • spatial_functional   — pure utility, zero ornament. Use ONLY for advanced
                              dashboards / data-dense admin tools.
     • magazine_editorial   — multi-column print-style. Use ONLY for actual
                              long-form content publications.
     • quiet_luxury         — tone-on-tone restraint. Use ONLY for actual high-end
                              fashion / interior / hospitality brands.

   BANNED ARCHETYPES (do NOT pick — they produce art-school output):
     ✗ fluid_typographic    — "type IS the design" with oversized clamp() headlines
                              and tight leading. Looks like a school thesis.
     ✗ kinetic_typography   — text-as-motion. Belongs on Awwwards, not in production.
     ✗ maximalist_collage   — chaotic layered visuals. User can't find anything.
     ✗ brutalist_mono       — intentional ugly. Inappropriate for any business.
     ✗ tech_noir_gradient   — neon-on-black. Overused 2024 cliché.
     ✗ playful_retro        — 1970s nostalgia. Too narrow for most brands.
     ✗ japanese_ma          — extreme negative space. Read as "broken layout".
     ✗ high_contrast_brutalist — same as brutalist_mono.
     ✗ soft_pastel_organic  — dated 2020-era pattern.
     ✗ dense_luxury         — Bloomberg terminal energy. Wrong for landing pages.
     ✗ bauhaus_modern       — geometric primary colors. Too costume-y.

   You may blend TWO archetypes from the PRIMARY list (e.g.
   "clean_modern + warm_inviting" for a friendly SaaS) but NEVER blend a banned
   archetype in. The validator rejects any banned archetype.

5b. PALETTE DEFAULTS PER ARCHETYPE (use these hue/tone ranges as the starting
    point — vary saturation and exact lightness, but stay in these conventional
    families). Each archetype gets ONE primary, ONE accent, neutrals.

    • clean_modern         → primary: cool slate / charcoal (220-240° hue, 10-30% sat).
                             accent: ONE saturated brand color (blue, teal, violet,
                             or domain-tied — e.g. green for fintech, indigo for AI).
                             bg: white or near-white. cards: neutral 50-100.
    • minimalist_corporate → primary: deep navy (215-225° hue, 30-50% sat, 20-35% lite)
                             OR charcoal (220° hue, 10% sat, 15% lite).
                             accent: restrained — muted gold, deep teal, burgundy.
                             bg: white. NEVER bright/saturated primary.
    • warm_inviting        → primary: warm browns / terracotta (15-35° hue, 30-50% sat,
                             25-45% lite). accent: cream + ONE deeper warm tone (sage,
                             rust, mustard). bg: cream / warm-off-white.
    • luxury_minimal       → DARK MODE: bg near-black (220-240° hue, 5-15% sat, 6-10% lite).
                             primary: warm metallic feel (35-50° hue, 30-50% sat for muted
                             gold) OR pure foreground white. accent: ONE restrained tone.
                             OR LIGHT: bg cream + foreground charcoal + restrained accent.
    • editorial_classic    → bg: warm off-white (40° hue, 20% sat, 96% lite).
                             primary: ink-dark (220° hue, 10% sat, 12% lite).
                             accent: ONE deeper saturated tone (deep red, navy, ochre).
                             Text-led — accent is sparse.
    • bold_modern          → DARK: bg near-black + ONE high-saturation accent (electric
                             blue 215° hue 90% sat, hot red 0° 80%, lime 80° 70%).
                             OR LIGHT: white bg + heavy black foreground + saturated accent.
    • soft_modern          → bg: cream / blush / warm off-white (20-40° hue, 20-40% sat,
                             92-96% lite). accent: sage (140° 25% 55%), terracotta
                             (15° 40% 60%), or muted blush. Restrained throughout.
    • product_focused      → bg: pure white. neutrals: gray 50-200. accent: ONE color
                             ONLY for CTAs (often a brand color tied to the product itself).
                             Lets product photography do the visual work.

    CONTRAST RULES (always):
    - foreground on background ≥ 4.5:1 (body text)
    - primary_foreground on primary ≥ 4.5:1 (button readability)
    - muted_foreground on muted ≥ 3.5:1 (caption readability)

6. MUST BE INTERNALLY CONSISTENT
   - Signature motif appears 2-3x across the page.
   - Card language, motion language, and spacing rhythm all reflect the
     same personality. Card says "crisp hairline" → motion says "precise
     linear transitions" → spacing says "tight editorial" (not airy).
   - For admin/CRM/TMS: table, form, sidebar, toolbar, and empty-state
     language ALL share the same radius + border treatment + density.

7. WHEN THE PROJECT IS AN ADMIN PANEL / CRM / TMS / SAAS DASHBOARD / ECOMMERCE
   - Design for SCANNING, not scrolling. Density is compact-comfortable.
   - Tables do the heavy lifting — specify row height, border style,
     header weight, zebra/hover treatment.
   - Forms live inside drawers or dedicated routes with label position,
     input chrome, focus ring, and error treatment matching the brand.
   - Sidebar is the primary nav surface. Specify width, icon style,
     active state, section treatment.
   - Status colors are SEPARATE from brand accent — success/warning/info/
     neutral/error all defined, each with WCAG AA contrast when used on
     badges/fills.
   - Section padding is 32-64px (NOT 96px+). Cards pack dense info.

8. IMAGE + TEXT COMPOSITION — NEVER place text directly on a photograph
   without explicit contrast protection. Every section that overlays copy
   on an image MUST pick ONE of these patterns, specified in
   image_overlay_pattern + overlay_scrim + overlay_text_color:
     A. 'dark_scrim'    — full-bleed image with
        bg-gradient-to-t from-black/70 via-black/30 to-transparent
        + text-white/90 body, text-white display.
     B. 'light_scrim'   — same gradient using background/90 tokens
        + text-foreground. For light-palette brands.
     C. 'split_solid'   — 50/50 grid, image on one side, text on the
        other in bg-background or bg-card. No overlay.
     D. 'card_lift'     — solid bg-card OR bg-background/95 card
        floating over the image with shadow-xl. Card itself is opaque.
     E. 'side_caption'  — text sits ADJACENT to the image (not on top),
        both in their own solid containers.
   - FORMS (reservation, contact, signup, booking): always 'card_lift' or
     'split_solid' with bg-card or bg-background. NEVER glassmorphism
     (backdrop-blur) over busy photography — inputs become unreadable.
   - Quote/testimonial blocks over imagery: require dark_scrim or
     card_lift. Bare italic serif on a light photograph is banned.
   - Image sizing: every image is either full-bleed (w-full), within a
     centered container (max-w-5xl mx-auto), or half of a split grid.
     NEVER a half-width image next to raw whitespace — that reads as
     a broken layout. Specify image_container_mode.

9. HERO ↔ IMAGE COMPOSITION COUPLING (STRICT — VALIDATED)
   The hero_archetype dictates what image_composition is allowed. Picking
   a structural hero (e.g. 'magazine') and then setting overlay_pattern
   to 'dark_scrim' with image_container_mode='full_bleed' silently
   collapses the hero back into the banned full-bleed-dark recipe.

   ALLOWED combinations:
     • hero_archetype = 'cinematic-parallax' OR 'full-bleed-dark'
         → overlay_pattern = 'dark_scrim'
         → image_container_mode = 'full_bleed'
         (the only two archetypes that may use the dark photo recipe)

     • hero_archetype = 'magazine' OR 'typographic-hero' OR 'diagonal'
         → overlay_pattern = 'split_solid' OR 'side_caption'
         → image_container_mode = 'centered' OR 'split_third'
         (image is decorative/secondary, not a backdrop)

     • hero_archetype = 'split' OR 'product-showcase' OR 'editorial-offset'
         → overlay_pattern = 'split_solid' OR 'card_lift' OR 'side_caption'
         → image_container_mode = 'split_half' OR 'split_third' OR 'centered'
         (image is one half/quadrant, NEVER full bleed)

     • hero_archetype = 'bento' OR 'layered-scroll'
         → overlay_pattern = any EXCEPT 'dark_scrim'
         → image_container_mode = any EXCEPT 'full_bleed'

   FORBIDDEN: 'dark_scrim' + 'full_bleed' for any hero_archetype other
   than 'cinematic-parallax' / 'full-bleed-dark'. The validator rejects
   this combination and the run will retry with a violation message.

Output ONLY via the provided tool. No prose.
"""


# ─── Tool (forced JSON schema output) ───────────────────────────────────────

_DESIGN_TOOL = {
    "name": "emit_design_system",
    "description": (
        "Emit the complete, bespoke design system for the project. "
        "Every field must be specific to THIS project's domain and vibe — "
        "not copied from a template."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "design_system_name": {
                "type": "string",
                "description": "2-4 word evocative name unique to this product (e.g. 'Ember Roast', 'Harvest Table', 'Silk & Steel', 'Polar Grid'). Never generic like 'Modern App'.",
            },
            "archetype": {
                "type": "string",
                "description": "The design-direction archetype you committed to. One of the 17 listed in the system prompt, or a blend like 'warm_artisan + editorial_serif_minimal'.",
            },
            "personality": {
                "type": "string",
                "description": "3-5 adjectives capturing the visual personality (e.g. 'confident, bookish, warm, quietly premium').",
            },
            "palette": {
                "type": "object",
                "description": "HSL tokens as bare strings like '22 85% 45%' — NO hsl() wrapper, NO commas.",
                "properties": {
                    "background":        {"type": "string"},
                    "foreground":        {"type": "string"},
                    "primary":           {"type": "string"},
                    "primary_foreground": {"type": "string"},
                    "secondary":         {"type": "string"},
                    "secondary_foreground": {"type": "string"},
                    "accent":            {"type": "string"},
                    "accent_foreground": {"type": "string"},
                    "muted":             {"type": "string"},
                    "muted_foreground":  {"type": "string"},
                    "card":              {"type": "string"},
                    "card_foreground":   {"type": "string"},
                    "border":            {"type": "string"},
                    "ring":              {"type": "string"},
                    "destructive":       {"type": "string"},
                    "destructive_foreground": {"type": "string"},
                },
                "required": [
                    "background", "foreground", "primary", "primary_foreground",
                    "secondary", "secondary_foreground", "accent", "accent_foreground",
                    "muted", "muted_foreground", "card", "card_foreground",
                    "border", "ring", "destructive", "destructive_foreground",
                ],
            },
            "dark_palette": {
                "type": "object",
                "description": "Dark-mode overrides. Same shape as palette (only override the fields that change in dark mode).",
                "properties": {
                    "background":       {"type": "string"},
                    "foreground":       {"type": "string"},
                    "card":             {"type": "string"},
                    "card_foreground":  {"type": "string"},
                    "muted":            {"type": "string"},
                    "muted_foreground": {"type": "string"},
                    "border":           {"type": "string"},
                },
            },
            "radius": {
                "type": "string",
                "description": "Base radius with unit (e.g. '0.375rem', '0.75rem'). Picks ONE value — all other radii derive from this.",
            },
            "radius_tokens": {
                "type": "object",
                "description": "Per-element radii, ALL derived from the same radius language. Every component in the app MUST pick one of these values — no ad-hoc radii.",
                "properties": {
                    "button":      {"type": "string", "description": "e.g. '0.375rem', '0rem', '9999px'"},
                    "input":       {"type": "string"},
                    "card":        {"type": "string"},
                    "badge":       {"type": "string"},
                    "image":       {"type": "string"},
                    "modal":       {"type": "string"},
                    "tooltip":     {"type": "string"},
                },
                "required": ["button", "input", "card", "badge", "image", "modal"],
            },
            "brand_mark": {
                "type": "object",
                "description": "Logo / brand mark specification so the generated navbar and sidebar ALWAYS show the brand. Never a bare nav.",
                "properties": {
                    "treatment":   {"type": "string", "description": "'wordmark' | 'icon_plus_wordmark' | 'monogram' | 'icon_only'"},
                    "font":        {"type": "string", "description": "Google Fonts family used for the wordmark — usually the heading font, occasionally a distinct display face"},
                    "weight":      {"type": "string", "description": "e.g. '500', '700', '900'"},
                    "style":       {"type": "string", "description": "'normal' | 'italic'"},
                    "case":        {"type": "string", "description": "'uppercase' | 'lowercase' | 'title' | 'as-typed'"},
                    "tracking":    {"type": "string", "description": "e.g. 'tracking-tight', '-0.02em', 'tracking-[0.2em]'"},
                    "icon":        {"type": "string", "description": "One-line description of the mark/icon if treatment has an icon; empty string otherwise"},
                    "color_token": {"type": "string", "description": "Which palette token the wordmark uses: 'foreground' | 'primary' | 'accent' | 'card_foreground'"},
                    "size_desktop":{"type": "string", "description": "e.g. '20px', '24px'"},
                    "placement":   {"type": "string", "description": "Where it sits: 'navbar_left' | 'navbar_center' | 'sidebar_top' | 'split_navbar_header'"},
                },
                "required": ["treatment", "font", "weight", "case", "tracking", "color_token", "size_desktop", "placement"],
            },
            "typography": {
                "type": "object",
                "properties": {
                    "font_pairing_id": {
                        "type": "string",
                        "description": (
                            "REQUIRED — pick ONE of the 12 curated Google Fonts pairings. "
                            "The renderer overrides heading_font / body_font / *_url with the "
                            "canonical values from this pairing, so don't worry about getting the "
                            "URLs perfect. Pick based on archetype mood:\n"
                            "  • fraunces-inter                  — warm editorial italic display + sans body\n"
                            "  • bricolage-grotesque-solo        — modern grotesque single family, weight contrast\n"
                            "  • dm-serif-dm-sans                — classic publication, high-contrast serif\n"
                            "  • space-grotesk-jetbrains         — terminal-precise sans + mono body\n"
                            "  • playfair-source-sans            — luxury feminine Didone + restrained sans\n"
                            "  • instrument-serif-instrument-sans — editorial italic, sibling typefaces\n"
                            "  • archivo-archivo-narrow          — bold + tight, heavy display + narrow body\n"
                            "  • crimson-pro-public-sans         — official editorial book serif + civic sans\n"
                            "  • unbounded-manrope               — futuristic display + soft humanist body\n"
                            "  • young-serif-rubik               — vintage editorial rounded serif + neutral body\n"
                            "  • bodoni-moda-outfit              — fashion editorial Didone + geometric sans\n"
                            "  • bricolage-jetbrains             — tech editorial variable display + mono body\n"
                            "NEVER pick `inter-inter` or `inter-geist` — those are not in the list and will be rejected."
                        ),
                    },
                    "heading_font":     {"type": "string", "description": "(Auto-overridden by font_pairing_id)"},
                    "heading_font_url": {"type": "string", "description": "(Auto-overridden by font_pairing_id)"},
                    "body_font":        {"type": "string"},
                    "body_font_url":    {"type": "string"},
                    "heading_weight":   {"type": "string", "description": "e.g. '600', '700', '800'"},
                    "heading_style":    {"type": "string", "description": "'normal' or 'italic'"},
                    "body_weight":      {"type": "string"},
                    "type_scale": {
                        "type": "array",
                        "items": {"type": "number"},
                        "description": "10 font-size steps in px, musical ratio (1.2-1.4). Ordered smallest to largest.",
                    },
                    "overall_vibe": {"type": "string", "description": "2-3 words, e.g. 'bookish editorial', 'crisp technical', 'bold artisan'"},
                },
                "required": ["font_pairing_id", "heading_font", "heading_font_url", "body_font", "body_font_url", "heading_weight", "body_weight", "type_scale", "overall_vibe"],
            },
            "spacing": {
                "type": "object",
                "properties": {
                    "base":              {"type": "number", "description": "Base unit in px — 4 or 8"},
                    "section_padding_y": {"type": "number", "description": "Section vertical padding in px"},
                    "section_padding_x": {"type": "number"},
                    "rhythm":            {"type": "string", "description": "ONE of: tight-editorial / standard-modern / airy-luxury / asymmetric / dense-information. Use these EXACT dashed names — they map to wrapper Tailwind classes."},
                },
                "required": ["base", "section_padding_y", "section_padding_x", "rhythm"],
            },
            "card_language": {
                "type": "object",
                "properties": {
                    "radius":      {"type": "string"},
                    "border":      {"type": "string", "description": "e.g. '1px solid hsl(var(--border))' or 'none'"},
                    "shadow":      {"type": "string", "description": "e.g. 'none', 'sm', 'md'"},
                    "padding":     {"type": "string", "description": "Tailwind token like 'p-8' or '32px'"},
                    "description": {"type": "string", "description": "One-sentence plain English recipe"},
                },
                "required": ["radius", "border", "shadow", "padding", "description"],
            },
            "motion_language": {
                "type": "object",
                "description": (
                    "Per-project motion DNA. The first 5 fields (enter, hover, "
                    "scroll, duration, easing) describe what code-gen should "
                    "EMIT for typical reveals. The last 4 (easing_signature, "
                    "durations, signature_transition, cursor_treatment) are "
                    "hard-typed enums that downstream prompts and the wrapper "
                    "templates consume verbatim — they kill the 'every section "
                    "animates differently' tell that AI tools (base44, Lovable) "
                    "leave behind."
                ),
                "properties": {
                    "enter":   {"type": "string", "description": "e.g. 'fade-up stagger 80ms', 'slide-in-from-left'"},
                    "hover":   {"type": "string"},
                    "scroll":  {"type": "string"},
                    "duration":{"type": "string", "description": "e.g. '300ms', '450ms'"},
                    "easing":  {"type": "string", "description": "CSS easing fn or curve"},

                    # ── Strict tokens consumed by code-gen + wrapper templates ──
                    "easing_signature": {
                        "type": "string",
                        "description": (
                            "ONE named curve used for EVERY entrance/transition. "
                            "Pick one based on archetype mood. Use these EXACT names — "
                            "code-gen prompts map them to concrete cubic-bezier values:\n"
                            "  • 'quint-out'         — cubic-bezier(0.16, 1, 0.3, 1) — premium, calm, awwwards default\n"
                            "  • 'expo-out'          — cubic-bezier(0.19, 1, 0.22, 1) — confident, snappy\n"
                            "  • 'circ-out'          — cubic-bezier(0, 0.55, 0.45, 1) — soft, organic\n"
                            "  • 'back-out-subtle'   — cubic-bezier(0.34, 1.2, 0.64, 1) — playful (small overshoot)\n"
                            "  • 'linear-precise'    — linear — for brutalist/dense_luxury archetypes\n"
                            "  • 'spring-quiet'      — Motion spring(80, 16) — for fluid_typographic"
                        ),
                    },
                    "durations": {
                        "type": "object",
                        "description": (
                            "Three named durations used everywhere. Code-gen MUST "
                            "use these three ms values for ALL motion — no ad-hoc "
                            "200/350/500ms scattered across components."
                        ),
                        "properties": {
                            "fast": {"type": "string", "description": "Hover, focus, micro-interactions. Typically '150ms' to '220ms'"},
                            "base": {"type": "string", "description": "Standard reveals, card stagger. Typically '450ms' to '650ms'"},
                            "slow": {"type": "string", "description": "Hero entrance, dramatic moments. Typically '900ms' to '1200ms'"},
                        },
                        "required": ["fast", "base", "slow"],
                    },
                    "signature_transition": {
                        "type": "string",
                        "description": (
                            "ONE bespoke transition reused in hero AND ≥1 other "
                            "section — the project's motion fingerprint. Without "
                            "this, every section invents its own animation and the "
                            "page reads as 'AI-assembled'. Pick one:\n"
                            "  • 'mask-reveal-diag'   — diagonal clip-path mask sweeps text in\n"
                            "  • 'weight-shift'       — variable-font weight animates 100→700 on enter\n"
                            "  • 'sticky-pin-scrub'   — element pins while inner content scrubs (GSAP-style)\n"
                            "  • 'horizontal-rail'    — section scrolls horizontally inside a vertical pin\n"
                            "  • 'chromatic-glitch'   — RGB channel offset on hover + entrance\n"
                            "  • 'duotone-fade'       — image desaturates → colour on viewport entry\n"
                            "  • 'kinetic-typography' — characters rise individually with 40ms stagger\n"
                            "  • 'parallax-layered'   — 3-layer depth with different scroll speeds\n"
                            "  • 'magnetic-pull'      — interactive elements pull toward cursor in 80px radius\n"
                            "  • 'minimal-precise'    — single 200ms fade-up only — no signature (for brutalist_mono / sharp_corporate)"
                        ),
                    },
                    "cursor_treatment": {
                        "type": "string",
                        "description": (
                            "Custom cursor behaviour. Big perceived-quality lift. "
                            "ONE of:\n"
                            "  • 'default'        — OS cursor, no override (safest for admin/CRM)\n"
                            "  • 'magnetic'       — primary CTAs pull toward cursor inside 60-80px radius\n"
                            "  • 'custom-blob'    — replace with branded blob/dot that scales over interactive elements\n"
                            "  • 'crosshair'      — minimal crosshair for editorial/brutalist archetypes"
                        ),
                    },
                },
                "required": [
                    "enter", "hover", "scroll", "duration", "easing",
                    "easing_signature", "durations", "signature_transition", "cursor_treatment",
                ],
            },
            "section_rhythm": {
                "type": "object",
                "description": "Per-section background/treatment pattern. Keys are section names that appear on the page.",
                "additionalProperties": {"type": "string"},
            },
            "image_composition": {
                "type": "object",
                "description": "Rules for every section that mixes text and photography. UNIVERSAL — applies to landing, CRM, TMS, admin hero banners, ecommerce product shots. Prevents low-contrast text-over-image, glassmorphic forms over busy imagery, and half-width-image-with-empty-whitespace layouts.",
                "properties": {
                    "overlay_pattern": {
                        "type": "string",
                        "description": "ONE of: 'dark_scrim' | 'light_scrim' | 'split_solid' | 'card_lift' | 'side_caption'. Pick based on palette mood — dark_scrim for cinematic, light_scrim for soft, split_solid for editorial, card_lift for form-heavy pages, side_caption for magazine.",
                    },
                    "overlay_scrim_classes": {
                        "type": "string",
                        "description": "Exact Tailwind gradient classes used for the scrim when overlay_pattern is dark_scrim or light_scrim. E.g. 'bg-gradient-to-t from-black/70 via-black/30 to-transparent'. Empty string for split_solid / card_lift / side_caption.",
                    },
                    "overlay_text_color": {
                        "type": "string",
                        "description": "Tailwind text-color class the overlaid copy MUST use. 'text-white' for dark_scrim; 'text-foreground' for split_solid / card_lift / side_caption / light_scrim on light palettes.",
                    },
                    "image_container_mode": {
                        "type": "string",
                        "description": "ONE of: 'full_bleed' | 'centered' | 'split_half' | 'split_third'. No other layouts. Prevents half-width-image + empty-whitespace gaps.",
                    },
                    "form_treatment": {
                        "type": "string",
                        "description": "How reservation/contact/booking/signup forms sit against imagery. ONE of: 'card_lift_solid' (opaque bg-card card with shadow-xl over hero image) | 'split_solid' (50/50 grid, form side in bg-background) | 'standalone_section' (form in its own section with bg-muted/30, no image underneath). NEVER 'glass' — backdrop-blur over photography kills input legibility.",
                    },
                },
                "required": ["overlay_pattern", "overlay_text_color", "image_container_mode", "form_treatment"],
            },
            # ARCHETYPE NAMES MUST USE DASHES — these strings are matched against
            # wrapper-template lookup tables in project_generator.py. Underscored
            # variants (e.g. 'cinematic_parallax') will silently fall through and
            # the code-gen prompt won't know which JSX skeleton to apply.
            "hero_archetype":           {"type": "string", "description": "ONE of: split / bento / diagonal / magazine / layered-scroll / cinematic-parallax / editorial-offset / full-bleed-dark / product-showcase / typographic-hero. Use these EXACT dashed names — they map to wrapper JSX templates."},
            "features_archetype":       {"type": "string", "description": "ONE of: bento-mixed / zigzag / vertical-tabs / horizontal-scroll / masonry / tilt-stack / showcase / timeline / numbered-editorial. Use these EXACT dashed names — they map to wrapper JSX templates."},
            "signature_motif":          {"type": "string", "description": "One recurring decorative element used 2-3x (e.g. 'hairline divider with offset dot', 'hand-drawn squiggle', 'topographic contour line', 'grain texture overlay')"},
            "decorative_pattern":       {"type": "string", "description": "One low-opacity recurring texture — 'dots', 'noise', 'squiggles', 'orbs', 'topographic', or 'none'"},
            "border_radius_language":   {"type": "string", "description": "sharp / crisp / soft / pill / organic / mixed"},
            "color_application_strategy": {"type": "string", "description": "ONE of: mono-accent / duotone-photos / gradient-mesh / inverted-dark / polychrome / photographic-neutral / brand-flood. Use these EXACT dashed names — they map to wrapper templates."},
            "hover_interaction_style":  {"type": "string", "description": "ONE of: lift-and-shadow / tilt-3d / reveal-content / glow-ring / morph-shape / invert-colors / magnetic-cursor. Use these EXACT dashed names — they map to wrapper templates."},
            "chart_colors":             {"type": "array", "items": {"type": "string"}, "description": "5 HSL values for data viz, harmonious with palette."},
            "banned_patterns":          {"type": "array", "items": {"type": "string"}, "description": "3-5 design moves you explicitly reject for this project"},
            "distinctive_moves":        {"type": "array", "items": {"type": "string"}, "description": "3-5 specific patterns that MUST appear to make this design identifiable"},

            # ─── ADMIN / CRM / TMS / SAAS DASHBOARD / ECOMMERCE FIELDS ──────────
            # These are REQUIRED when layout_archetype is admin_dashboard, crm,
            # tms, saas_dashboard, or ecommerce. They are optional otherwise.
            "density_mode": {
                "type": "string",
                "description": "ADMIN ONLY. 'compact' (power users — 28-32px rows) | 'comfortable' (40-44px rows) | 'spacious' (48-56px rows). Pick based on data-volume expectation.",
            },
            "status_palette": {
                "type": "object",
                "description": "ADMIN ONLY. Semantic status colors SEPARATE from brand accent. Each has WCAG AA contrast when used on a badge (fill + foreground).",
                "properties": {
                    "success":            {"type": "string", "description": "HSL bare — e.g. '142 72% 29%'"},
                    "success_foreground": {"type": "string"},
                    "warning":            {"type": "string"},
                    "warning_foreground": {"type": "string"},
                    "info":               {"type": "string"},
                    "info_foreground":    {"type": "string"},
                    "neutral":            {"type": "string"},
                    "neutral_foreground": {"type": "string"},
                    "error":              {"type": "string"},
                    "error_foreground":   {"type": "string"},
                },
            },
            "table_language": {
                "type": "object",
                "description": "ADMIN ONLY. DataTable visual recipe.",
                "properties": {
                    "row_height":        {"type": "string", "description": "e.g. '36px', '44px', '56px'"},
                    "border_style":      {"type": "string", "description": "'hairline_rows' | 'zebra' | 'bordered_cells' | 'borderless' | 'hairline_cols_only'"},
                    "header_weight":     {"type": "string", "description": "e.g. '500', '600', 'uppercase_xs_500'"},
                    "header_background": {"type": "string", "description": "'transparent' | 'muted' | 'card'"},
                    "hover_treatment":   {"type": "string", "description": "'muted' | 'card' | 'primary_5' | 'none'"},
                    "cell_padding":      {"type": "string", "description": "e.g. '12px 16px'"},
                    "description":       {"type": "string", "description": "One-sentence English recipe"},
                },
            },
            "form_language": {
                "type": "object",
                "description": "ADMIN ONLY. Form input recipe.",
                "properties": {
                    "label_position":  {"type": "string", "description": "'above' | 'floating' | 'inline_left' | 'inline_right'"},
                    "label_style":     {"type": "string", "description": "e.g. 'text-sm font-medium text-foreground mb-1.5'"},
                    "input_style":     {"type": "string", "description": "'outlined' | 'underlined' | 'filled_muted' | 'filled_card_with_border'"},
                    "input_height":    {"type": "string", "description": "e.g. '36px', '40px', '44px'"},
                    "focus_style":     {"type": "string", "description": "e.g. 'ring-2 ring-primary/40 border-primary' or '2px solid primary outline'"},
                    "error_style":     {"type": "string", "description": "e.g. 'text-destructive text-xs mt-1 + border-destructive'"},
                    "spacing_between": {"type": "string", "description": "e.g. '20px', '24px'"},
                    "description":     {"type": "string"},
                },
            },
            "sidebar_language": {
                "type": "object",
                "description": "ADMIN ONLY. Sidebar navigation recipe.",
                "properties": {
                    "width":             {"type": "string", "description": "e.g. '256px', '280px', '72px' (icon-only)"},
                    "variant":           {"type": "string", "description": "'wide_labelled' | 'icon_plus_labels' | 'icon_only' | 'rail_plus_panel'"},
                    "background":        {"type": "string", "description": "Palette token: 'card' | 'background' | 'foreground' (for dark sidebar)"},
                    "active_treatment":  {"type": "string", "description": "e.g. 'primary_10_bg_plus_primary_text' | 'left_border_primary_2px' | 'pill_filled_primary'"},
                    "section_divider":   {"type": "string", "description": "'uppercase_label' | 'hairline_rule' | 'spacing_only'"},
                    "icon_size":         {"type": "string", "description": "e.g. '16px', '18px', '20px'"},
                    "collapsed_behavior":{"type": "string", "description": "'collapse_to_icon_rail' | 'hide_entirely' | 'none'"},
                    "description":       {"type": "string"},
                },
            },
            "toolbar_language": {
                "type": "object",
                "description": "ADMIN ONLY. Page toolbar (search + filters + bulk actions + primary CTA).",
                "properties": {
                    "search_chrome":     {"type": "string", "description": "'rounded_filled_muted' | 'underlined' | 'outlined_with_cmd_k'"},
                    "filter_style":      {"type": "string", "description": "'chip_pills' | 'dropdown_buttons' | 'segmented_control' | 'advanced_drawer'"},
                    "bulk_action_style": {"type": "string", "description": "'sticky_bar_bottom' | 'inline_chip_row' | 'contextual_menu'"},
                    "primary_cta":       {"type": "string", "description": "e.g. 'filled primary with plus icon, top-right'"},
                    "description":       {"type": "string"},
                },
            },
            "empty_state_language": {
                "type": "object",
                "description": "ADMIN ONLY. How empty states look (first-run, search-no-results, filter-no-match).",
                "properties": {
                    "illustration_style": {"type": "string", "description": "'line_icon' | 'duotone_spot' | 'photographic' | 'abstract_motif' | 'none'"},
                    "copy_tone":          {"type": "string", "description": "e.g. 'direct and helpful', 'warm encouraging'"},
                    "cta_placement":      {"type": "string", "description": "'centered_button_below' | 'link_in_body' | 'dual_primary_secondary'"},
                    "description":        {"type": "string"},
                },
            },
            "data_viz_style": {
                "type": "object",
                "description": "ADMIN ONLY. Chart/graph treatment beyond just colors.",
                "properties": {
                    "line_weight":   {"type": "string", "description": "e.g. '2px', '1.5px', '3px'"},
                    "axis_style":    {"type": "string", "description": "'hidden' | 'hairline_muted' | 'bold_grid'"},
                    "grid_style":    {"type": "string", "description": "'none' | 'dotted_horizontal' | 'solid_muted'"},
                    "tooltip_style": {"type": "string", "description": "'floating_card_shadow' | 'inline_pill' | 'dashed_crosshair'"},
                    "point_style":   {"type": "string", "description": "'filled_circle' | 'hollow_ring' | 'none_hover_reveal'"},
                    "description":   {"type": "string"},
                },
            },
        },
        "required": [
            "design_system_name", "archetype", "personality", "palette", "radius",
            "radius_tokens", "brand_mark",
            "typography", "spacing", "card_language", "motion_language",
            "section_rhythm", "hero_archetype", "features_archetype",
            "signature_motif", "decorative_pattern", "border_radius_language",
            "color_application_strategy", "hover_interaction_style",
            "image_composition",
            "chart_colors", "banned_patterns", "distinctive_moves",
        ],
    },
}

# Layout archetypes that need the admin UI blocks
_ADMIN_ARCHETYPES = {"admin_dashboard", "crm", "tms", "saas_dashboard", "ecommerce", "internal_tool", "saas_app"}


def _is_admin_archetype(layout_archetype: str) -> bool:
    return (layout_archetype or "").strip().lower() in _ADMIN_ARCHETYPES


# ─── User-prompt builder ────────────────────────────────────────────────────

def _build_user_prompt(
    description: str,
    domain: str,
    brand_name: str,
    copy_tone: str,
    layout_archetype: str,
    vibe: str,
    cultural_atmosphere: str = "",
    retry_feedback: Optional[str] = None,
) -> str:
    feedback_block = ""
    if retry_feedback:
        feedback_block = f"""
⚠️ PREVIOUS ATTEMPT FAILED VALIDATION:
{retry_feedback}

Fix ONLY the flagged issues. Keep everything else the same personality. Re-emit the tool call.
"""

    is_admin = _is_admin_archetype(layout_archetype)

    if is_admin:
        archetype_block = f"""
THIS PROJECT IS AN ADMIN / CRM / TMS / SAAS DASHBOARD / ECOMMERCE APP.
You MUST also emit these admin-specific fields:
  - density_mode (compact | comfortable | spacious)
  - status_palette (success / warning / info / neutral / error — each with foreground, ALL WCAG AA on badges)
  - table_language (row_height, border_style, header_weight, hover_treatment, cell_padding)
  - form_language (label_position, input_style, focus_style, error_style)
  - sidebar_language (width, variant, background, active_treatment, section_divider)
  - toolbar_language (search_chrome, filter_style, bulk_action_style, primary_cta)
  - empty_state_language (illustration_style, copy_tone, cta_placement)
  - data_viz_style (line_weight, axis_style, grid_style, tooltip_style)

Admin design rules:
  - Density is tight. section_padding_y is 32-64px, NOT 96+. Rows are 36-44px.
  - Status colors NEVER equal the brand accent.
  - Sidebar is the primary nav. Specify which palette token backs it.
  - Table, form, sidebar, toolbar ALL share the same border_radius_language.
  - Think about WHICH real SaaS you'd steal from: Linear, Attio, Retool, Ramp,
    Notion, Stripe Dashboard, Vercel Dashboard, Plaid Console. Not Salesforce
    Classic. Not 2018 Material admin templates.
"""
        think_block = """Think through these before emitting:
  1. What is the emotional register this tool should hit? (precise? approachable? terminal-serious?)
  2. Which archetype fits a work-focused app? (neo_swiss / sharp_corporate / minimal_luxe / brutalist_mono)
  3. Density: what's the data volume? Compact for power users, comfortable for execs.
  4. Sidebar variant: icon+labels or icon-only rail? Match the density.
  5. Table style: zebra vs hairline vs borderless? Which fits the brand's precision?
  6. Status palette: pick 5 colors that are semantically clear AND look like they
     belong to this brand — not generic bootstrap red/yellow/green.
  7. Typography: body font MUST be legible at 13-14px for dense tables.
"""
    else:
        archetype_block = ""
        think_block = """Think through these before emitting:
  1. What is the emotional register this brand should hit? (confident? gentle? precise? playful?)
  2. Which of the 17 archetypes fits, or which blend?
  3. What would a senior designer at a top studio choose for palette — specific HSLs,
     not generic Tailwind defaults?
  4. What font pairing earns this brand its personality?
  5. What is the ONE signature motif that becomes the design's fingerprint?
  6. What dated patterns MUST be banned here?
  7. What distinctive moves MUST appear?
  8. Brand mark: wordmark vs icon+wordmark vs monogram? Pick the font and exact case/tracking.
"""

    # Domain → recommended archetype mapping. We bias toward CONVENTIONAL,
    # industry-appropriate choices instead of forcing the AI to "be different".
    # Variety still happens — within an archetype the model picks different
    # palettes / fonts / motifs each run — but the archetype itself is locked
    # to what real designers would actually pick for this category.
    _DOMAIN_ARCHETYPE_LOCK: dict[str, list[str]] = {
        # Food & beverage
        "restaurant":  ["warm_inviting", "editorial_classic"],
        "cafe":        ["warm_inviting", "editorial_classic"],
        "coffee":      ["warm_inviting", "editorial_classic"],
        "bakery":      ["warm_inviting", "editorial_classic"],
        "bar":         ["warm_inviting", "luxury_minimal"],
        # Mobility
        "automotive":  ["luxury_minimal", "bold_modern"],
        "car":         ["luxury_minimal", "bold_modern"],
        "cars":        ["luxury_minimal", "bold_modern"],
        "dealership":  ["luxury_minimal", "bold_modern"],
        "ev":          ["bold_modern", "luxury_minimal"],
        "motorcycle":  ["bold_modern", "luxury_minimal"],
        "rental":      ["clean_modern", "luxury_minimal"],
        # B2B / SaaS
        "saas":        ["clean_modern", "minimalist_corporate"],
        "startup":     ["clean_modern", "minimalist_corporate"],
        "agency":      ["clean_modern", "editorial_classic"],
        "portfolio":   ["editorial_classic", "clean_modern"],
        "developer":   ["clean_modern", "minimalist_corporate"],
        # Trust-heavy
        "healthcare":  ["minimalist_corporate", "clean_modern"],
        "law":         ["minimalist_corporate", "editorial_classic"],
        "finance":     ["minimalist_corporate", "clean_modern"],
        "insurance":   ["minimalist_corporate", "clean_modern"],
        # Lifestyle
        "fitness":     ["bold_modern", "clean_modern"],
        "yoga":        ["soft_modern", "warm_inviting"],
        "spa":         ["soft_modern", "luxury_minimal"],
        "salon":       ["soft_modern", "luxury_minimal"],
        "wellness":    ["soft_modern", "warm_inviting"],
        "wedding":     ["editorial_classic", "soft_modern"],
        "events":      ["editorial_classic", "luxury_minimal"],
        # Property / hospitality
        "hotel":       ["luxury_minimal", "editorial_classic"],
        "real_estate": ["luxury_minimal", "clean_modern"],
        # Fashion / retail
        "fashion":     ["luxury_minimal", "editorial_classic"],
        "ecommerce":   ["product_focused", "clean_modern"],
        # Education / culture
        "education":   ["clean_modern", "editorial_classic"],
        "art":         ["editorial_classic", "minimalist_corporate"],
        "music":       ["bold_modern", "editorial_classic"],
    }
    _d_lower = (domain or "").lower().replace(" ", "_").replace("-", "_")
    _allowed_archetypes = (
        _DOMAIN_ARCHETYPE_LOCK.get(_d_lower)
        or next(
            (v for k, v in _DOMAIN_ARCHETYPE_LOCK.items() if k in _d_lower or _d_lower in k),
            None,
        )
        or ["clean_modern", "minimalist_corporate", "editorial_classic"]
    )

    # Variety still matters — but as a tie-breaker between equally-fitting
    # CONVENTIONAL choices, not as an excuse to ship art-school output.
    import random as _rand
    _variety_seed = _rand.randint(1000, 9999)

    variety_block = f"""
ARCHETYPE LOCK (variety_seed={_variety_seed}):
  Domain "{domain}" maps to these conventional, polished archetypes:
    {", ".join(_allowed_archetypes)}

  Pick ONE of those (or blend two from the list). DO NOT pick anything
  outside this list — it has been chosen to fit how real designers work
  in this industry. The user wants a site that looks like a real $5-10M
  company shipped it, NOT an experimental art project.

  Variety happens INSIDE the chosen archetype — different palettes, fonts,
  signature motifs, hero layouts within "{_allowed_archetypes[0]}" all count
  as variety. Two runs for the same domain should differ in those details
  but should NOT differ in archetype "personality" — both should still feel
  conventionally professional for {domain}.

BANNED DEFAULT HERO RECIPE — DO NOT SHIP THIS:
  ✗ full-bleed Unsplash photo + dark gradient scrim + tiny uppercase eyebrow
    + giant italic serif H1 in white + "min-h-screen flex items-center"
  This recipe has shipped on the last 4 generations across coffee, restaurant,
  school, and finance — every site looks identical AND it's the lazy default.

  Pick a hero_archetype that fits the chosen archetype:
    • split           (text left + media right, light bg)  — clean_modern, warm_inviting,
                                                              minimalist_corporate, luxury_minimal,
                                                              soft_modern, editorial_classic
    • product-showcase (device/product mockup focus)        — clean_modern, product_focused,
                                                              bold_modern
    • magazine        (12-col grid, oversized H1, small img)— editorial_classic only
    • bento           (asymmetric tiled grid)               — clean_modern (saas dashboards),
                                                              product_focused
    • full-bleed-dark (cinematic photo + scrim)             — luxury_minimal, bold_modern
                                                              ONLY (e.g. luxury car, sport car,
                                                              hotel, fashion editorial)
"""

    cultural_block = ""
    if cultural_atmosphere and cultural_atmosphere.strip():
        cultural_block = f"""
CULTURAL ATMOSPHERE (from research — use as PRIMARY input for palette + typography
+ signature_motif when country_or_region is set; ignore when "none — modern global"):
{cultural_atmosphere.strip()}

Apply the cultural atmosphere as follows:
  • palette → use cultural_palette values, NOT generic premium-dark + gold
  • typography_pairing → honor typographic_signature; only fall back to default
    pairings when country_or_region is "none"
  • signature_motif + decorative_pattern → pick from motif_inventory
  • banned_generics from research are FORBIDDEN this run — do not produce them
  • For non-"none" cultures, the design should read as authentically FROM that
    place to a designer who knows it, not "AI-generated landing page with a
    foreign name"

"""

    return f"""Design the bespoke design system for this project.

PROJECT DESCRIPTION: {description}
BRAND NAME: {brand_name}
DOMAIN: {domain}
LAYOUT TYPE: {layout_archetype}
COPY TONE: {copy_tone or '(not specified — infer from domain)'}
VIBE KEYWORDS: {vibe or '(not specified — infer from description)'}
{cultural_block}{archetype_block}{variety_block}
{think_block}{feedback_block}
Call the emit_design_system tool with the complete, internally consistent system."""


# ─── Validation — contrast, type scale, grid ────────────────────────────────

def _hsl_to_rgb(hsl: str) -> Optional[tuple[float, float, float]]:
    """Parse bare HSL string '22 85% 45%' → (r, g, b) in 0-1. Returns None on parse failure."""
    try:
        parts = hsl.strip().replace("%", "").split()
        if len(parts) < 3:
            return None
        h = float(parts[0]) % 360
        s = float(parts[1]) / 100.0
        l = float(parts[2]) / 100.0
    except (ValueError, IndexError):
        return None

    c = (1 - abs(2 * l - 1)) * s
    x = c * (1 - abs((h / 60) % 2 - 1))
    m = l - c / 2
    if   h < 60:  r, g, b = c, x, 0
    elif h < 120: r, g, b = x, c, 0
    elif h < 180: r, g, b = 0, c, x
    elif h < 240: r, g, b = 0, x, c
    elif h < 300: r, g, b = x, 0, c
    else:         r, g, b = c, 0, x
    return (r + m, g + m, b + m)


def _relative_luminance(rgb: tuple[float, float, float]) -> float:
    def _chan(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * _chan(r) + 0.7152 * _chan(g) + 0.0722 * _chan(b)


def _contrast_ratio(hsl_a: str, hsl_b: str) -> float:
    """WCAG contrast ratio, ≥4.5 for AA body, ≥3.0 for AA large text."""
    rgb_a = _hsl_to_rgb(hsl_a)
    rgb_b = _hsl_to_rgb(hsl_b)
    if not rgb_a or not rgb_b:
        return 0.0
    la = _relative_luminance(rgb_a)
    lb = _relative_luminance(rgb_b)
    lighter, darker = max(la, lb), min(la, lb)
    return (lighter + 0.05) / (darker + 0.05)


def validate_design_system(design: dict, layout_archetype: str = "") -> list[str]:
    """Return a list of human-readable violations. Empty list = passes.

    layout_archetype branches the validator: admin archetypes add checks for
    density, status_palette contrast, required admin blocks, and tighter
    section padding. Landing archetypes enforce generous section padding.
    """
    violations: list[str] = []
    palette = design.get("palette") or {}
    is_admin = _is_admin_archetype(layout_archetype)

    # Contrast checks on critical pairs
    contrast_pairs = [
        ("foreground", "background", 4.5, "body text"),
        ("primary_foreground", "primary", 4.5, "primary button"),
        ("secondary_foreground", "secondary", 4.5, "secondary button"),
        ("accent_foreground", "accent", 4.5, "accent button"),
        ("muted_foreground", "muted", 3.5, "muted caption"),
        ("card_foreground", "card", 4.5, "card text"),
        ("destructive_foreground", "destructive", 4.5, "destructive button"),
    ]
    for fg_key, bg_key, min_ratio, label in contrast_pairs:
        fg = palette.get(fg_key) or ""
        bg = palette.get(bg_key) or ""
        if not fg or not bg:
            violations.append(f"Palette missing {fg_key} or {bg_key}")
            continue
        ratio = _contrast_ratio(fg, bg)
        if ratio < min_ratio:
            violations.append(
                f"Contrast too low for {label}: {fg_key} on {bg_key} = {ratio:.2f}:1 "
                f"(need ≥{min_ratio}:1). Darken foreground or lighten background."
            )

    # ── Archetype guard — reject experimental archetypes ─────────────────
    # The user wants polished, conventional, on-brand-for-the-industry output.
    # Even when the system prompt forbids these, the model occasionally drifts
    # toward them (especially fluid_typographic for typography-driven domains).
    # Hard validator catches it and forces a retry.
    _BANNED_ARCHETYPES = {
        "fluid_typographic", "kinetic_typography", "maximalist_collage",
        "brutalist_mono", "tech_noir_gradient", "playful_retro",
        "japanese_ma", "high_contrast_brutalist", "soft_pastel_organic",
        "dense_luxury", "bauhaus_modern",
    }
    archetype_str = (design.get("archetype") or "").strip().lower()
    if archetype_str:
        # Split blends ("warm_artisan + editorial_serif_minimal") and check each.
        parts = [p.strip() for p in archetype_str.replace(",", "+").split("+") if p.strip()]
        for part in parts:
            if part in _BANNED_ARCHETYPES:
                violations.append(
                    f"archetype '{part}' is banned — it produces art-school output. "
                    f"Pick a conventional archetype from the PRIMARY list: clean_modern, "
                    f"minimalist_corporate, warm_inviting, luxury_minimal, editorial_classic, "
                    f"bold_modern, soft_modern, product_focused."
                )

    # Generic-template bans
    def _is_default_blue(hsl: str) -> bool:
        rgb = _hsl_to_rgb(hsl)
        if not rgb:
            return False
        # Tailwind default blue-600 ≈ hsl(221 83% 53%) → rgb ~ (37, 99, 235) / 255
        r, g, b = rgb
        return (abs(r - 37/255) < 0.04 and abs(g - 99/255) < 0.04 and abs(b - 235/255) < 0.06)

    primary = palette.get("primary", "")
    if primary and _is_default_blue(primary):
        violations.append(
            "Primary is default Tailwind blue (~221 83% 53%). "
            "Pick a bespoke accent tied to the domain personality."
        )

    # Typography validation
    typo = design.get("typography") or {}
    pairing_id = (typo.get("font_pairing_id") or "").strip().lower()

    # font_pairing_id is now the primary control. If present and known, the
    # renderer enforces canonical heading/body fonts so the heading==body /
    # Inter+Geist checks below don't matter (they'll get overridden anyway).
    # If MISSING or UNKNOWN, flag it as a violation so the retry loop forces
    # a pick from the curated 12-pair table.
    if not pairing_id:
        violations.append(
            "typography.font_pairing_id is missing. Pick ONE of the 12 curated "
            "pairings (e.g. 'fraunces-inter', 'bricolage-grotesque-solo', "
            "'instrument-serif-instrument-sans')."
        )
    elif pairing_id not in _FONT_PAIRINGS:
        valid_ids = sorted(_FONT_PAIRINGS.keys())
        violations.append(
            f"typography.font_pairing_id='{pairing_id}' is not in the curated list. "
            f"Pick ONE of: {', '.join(valid_ids)}."
        )

    # Only run the heading==body / Inter+Geist drift checks when there's no
    # valid pairing override (otherwise the renderer's _apply_font_pairing
    # will replace whatever the Director put in heading_font / body_font).
    if not pairing_id or pairing_id not in _FONT_PAIRINGS:
        heading = (typo.get("heading_font") or "").strip().lower()
        body = (typo.get("body_font") or "").strip().lower()
        if heading and body and heading == body:
            h_style = (typo.get("heading_style") or "normal").lower()
            if h_style != "italic":
                violations.append(
                    f"Heading font equals body font ({heading}) and heading style is not italic. "
                    "Pick a display font for headings OR set heading_style to 'italic' for clear contrast."
                )
        # Hard ban Inter+Geist outside the pairing system — it's the most
        # recognizable AI-template tell of 2024-2025.
        if heading == "inter" and body == "geist":
            violations.append(
                "Inter + Geist is the #1 recognizable AI-template font pairing. "
                "Pick a font_pairing_id from the curated list instead."
            )

    scale = typo.get("type_scale") or []
    if len(scale) >= 3:
        ratios = [scale[i+1] / scale[i] for i in range(len(scale) - 1) if scale[i] > 0]
        if ratios:
            avg_ratio = sum(ratios) / len(ratios)
            if avg_ratio < 1.12 or avg_ratio > 1.5:
                violations.append(
                    f"Type scale average ratio {avg_ratio:.2f} outside musical range [1.15, 1.45]. "
                    "Rebuild scale with ~1.25 or ~1.333 ratio."
                )

    # Spacing grid
    spacing = design.get("spacing") or {}
    base = spacing.get("base")
    if base not in (4, 8):
        violations.append(f"Spacing base must be 4 or 8 (got {base}).")
    spy = spacing.get("section_padding_y") or 0
    if base and spy and spy % base != 0:
        violations.append(
            f"section_padding_y ({spy}) not a multiple of base ({base}). Re-snap to grid."
        )
    # Archetype-conditional padding: landing pages need breathing room,
    # admin/CRM/TMS need dense scanning.
    if spy:
        if is_admin:
            if spy < 24 or spy > 80:
                violations.append(
                    f"section_padding_y {spy}px is wrong for admin/CRM/TMS — use 32-64px. "
                    "Admin UIs are for scanning, not scrolling."
                )
        else:
            if spy < 72:
                violations.append(
                    f"section_padding_y {spy}px is too tight for a landing page — use 96px+."
                )

    # Font URLs must be Google Fonts CSS2 endpoints
    for key in ("heading_font_url", "body_font_url"):
        url = typo.get(key) or ""
        if url and "fonts.googleapis.com" not in url:
            violations.append(f"{key} is not a Google Fonts URL: {url[:80]}")

    # Brand mark — every project gets one
    bm = design.get("brand_mark") or {}
    if not bm.get("treatment") or not bm.get("font"):
        violations.append(
            "brand_mark missing treatment or font. Every project needs a wordmark/monogram "
            "so the navbar is never a bare nav with no logo."
        )

    # Radius tokens — ensure they exist and share a language
    rt = design.get("radius_tokens") or {}
    if not rt.get("button") or not rt.get("card"):
        violations.append(
            "radius_tokens missing button or card radius. Every element needs an "
            "explicit radius so the border-radius language is applied consistently."
        )

    # Image composition — universal contrast protection for text-over-image
    ic = design.get("image_composition") or {}
    valid_overlays = {"dark_scrim", "light_scrim", "split_solid", "card_lift", "side_caption"}
    valid_containers = {"full_bleed", "centered", "split_half", "split_third"}
    valid_forms = {"card_lift_solid", "split_solid", "standalone_section"}
    ovp = (ic.get("overlay_pattern") or "").strip().lower()
    icm = (ic.get("image_container_mode") or "").strip().lower()
    ft = (ic.get("form_treatment") or "").strip().lower()
    if not ovp or ovp not in valid_overlays:
        violations.append(
            f"image_composition.overlay_pattern '{ovp}' must be one of "
            f"{sorted(valid_overlays)}."
        )
    if not icm or icm not in valid_containers:
        violations.append(
            f"image_composition.image_container_mode '{icm}' must be one of "
            f"{sorted(valid_containers)}. No half-width-image-with-whitespace layouts."
        )
    if not ft or ft not in valid_forms:
        violations.append(
            f"image_composition.form_treatment '{ft}' must be one of {sorted(valid_forms)}. "
            "Glassmorphism over photography is banned — inputs become unreadable."
        )
    # If overlay_pattern is a scrim variant, scrim classes must be present
    if ovp in ("dark_scrim", "light_scrim") and not ic.get("overlay_scrim_classes"):
        violations.append(
            f"image_composition.overlay_pattern='{ovp}' requires overlay_scrim_classes "
            "(e.g. 'bg-gradient-to-t from-black/70 via-black/30 to-transparent')."
        )

    # Hero ↔ image_composition coupling. Without this, the model picks a
    # structural hero_archetype (magazine, split, etc.) and then defaults
    # to dark_scrim + full_bleed for image_composition, which silently
    # collapses the hero back into the banned full-bleed-dark recipe.
    hero_arch = (design.get("hero_archetype") or "").strip().lower().replace("_", "-")
    _DARK_HERO_ARCHETYPES = {"cinematic-parallax", "full-bleed-dark"}
    if hero_arch and hero_arch not in _DARK_HERO_ARCHETYPES:
        if ovp == "dark_scrim":
            violations.append(
                f"hero_archetype='{hero_arch}' forbids image_composition.overlay_pattern="
                "'dark_scrim'. Use 'split_solid', 'card_lift', 'side_caption', or "
                "'light_scrim' instead — dark_scrim is reserved for cinematic-parallax "
                "and full-bleed-dark."
            )
        if icm == "full_bleed":
            violations.append(
                f"hero_archetype='{hero_arch}' forbids image_composition.image_container_mode="
                "'full_bleed'. Use 'split_half', 'split_third', or 'centered' — full_bleed "
                "is reserved for cinematic-parallax and full-bleed-dark."
            )

    # Admin-only: required admin blocks + status palette contrast
    if is_admin:
        required_admin_blocks = [
            "density_mode", "status_palette", "table_language", "form_language",
            "sidebar_language", "toolbar_language", "empty_state_language", "data_viz_style",
        ]
        for block_key in required_admin_blocks:
            if not design.get(block_key):
                violations.append(f"Admin archetype requires {block_key}. Emit this block.")

        dm = (design.get("density_mode") or "").lower()
        if dm and dm not in ("compact", "comfortable", "spacious"):
            violations.append(
                f"density_mode '{dm}' must be compact | comfortable | spacious."
            )

        status = design.get("status_palette") or {}
        status_pairs = [
            ("success_foreground", "success", "success badge"),
            ("warning_foreground", "warning", "warning badge"),
            ("info_foreground", "info", "info badge"),
            ("error_foreground", "error", "error badge"),
        ]
        for fg_key, bg_key, label in status_pairs:
            fg = status.get(fg_key) or ""
            bg = status.get(bg_key) or ""
            if fg and bg:
                r = _contrast_ratio(fg, bg)
                if r < 4.5:
                    violations.append(
                        f"status_palette {label} contrast {r:.2f}:1 < 4.5:1. "
                        "Darken foreground or lighten background."
                    )

        # Status ≠ brand accent (otherwise success looks like a CTA)
        primary_hsl = palette.get("primary") or ""
        success_hsl = status.get("success") or ""
        if primary_hsl and success_hsl and primary_hsl.strip() == success_hsl.strip():
            violations.append(
                "status.success equals palette.primary — status colors MUST be separate "
                "from the brand accent so users can distinguish CTA from positive state."
            )

    return violations


# ─── Gemini design critic ───────────────────────────────────────────────────
#
# The deterministic validator above catches *structural* defects (contrast,
# type scale, default-blue clichés, admin-status-vs-primary clash). It cannot
# catch *taste* defects:
#
#   • palette technically passes contrast but feels generic for a coffee
#     brand ("could be any SaaS")
#   • font pairing is on the curated list but doesn't match the cultural
#     atmosphere ("Manrope on a Tuscan trattoria")
#   • radius + shadow language reads modern-tech when the domain wants
#     editorial / artisanal / premium-hospitality
#
# Gemini sees these because it's been trained on millions of real sites in
# every domain. We hand it the design dict + research vibe block and ask
# for a verdict + concrete change list, then feed that back into one
# additional Claude retry.
#
# Cost: one Gemini Flash call (~5-10s, cheap). Skipped silently when no
# key is provided.

_GEMINI_CRITIC_MODEL = "gemini-2.5-flash"
_GEMINI_CRITIC_TIMEOUT = 30.0


async def _gemini_design_critic(
    *,
    design: dict,
    description: str,
    domain: str,
    layout_archetype: str,
    vibe: str,
    cultural_atmosphere: str,
    gemini_key: str,
) -> Optional[dict]:
    """Ask Gemini whether this design fits the domain.

    Returns ``{"verdict": "pass"|"revise", "issues": [...], "changes": [...]}``
    or None on any failure (caller treats None as pass — fail-soft).
    """
    if not gemini_key:
        return None

    import json as _json
    import re as _re

    palette = design.get("palette") or {}
    typography = design.get("typography") or {}
    radius = design.get("radius") or ""
    archetype = design.get("archetype") or ""
    name = design.get("design_system_name") or ""

    # Hand Gemini a compact summary, not the whole tool dict — keeps the
    # prompt short and focuses attention on the choices that actually
    # affect taste.
    summary = {
        "design_system_name": name,
        "archetype": archetype,
        "palette": {k: palette.get(k) for k in (
            "primary", "secondary", "accent", "background", "foreground", "card", "muted",
        )},
        "typography": {
            "heading_font": typography.get("heading_font"),
            "body_font": typography.get("body_font"),
            "scale": typography.get("scale"),
        },
        "radius": radius,
    }

    prompt = f"""You are a senior brand designer reviewing a generated design system.

PROJECT
description: {description[:600]}
domain: {domain}
layout_archetype: {layout_archetype}
vibe (from research): {vibe[:400]}
cultural_atmosphere: {cultural_atmosphere[:600]}

PROPOSED DESIGN SYSTEM
{_json.dumps(summary, indent=2)}

Judge it on TASTE (not structure — contrast and scale are already validated).

Ask yourself, ruthlessly:
  1. Does the palette feel SPECIFIC to this domain, or could it be any SaaS?
  2. Does the font pairing match the cultural atmosphere and copy_tone?
  3. Does the radius/archetype read right for this kind of business?
     (editorial serif for a tech bro startup = wrong. Geometric mono for
     a Tuscan trattoria = wrong.)
  4. Is the design_system_name evocative of this brand, or generic?
  5. Would a top site in this domain (think Aesop, Apple, Linear, Stripe,
     Bluebottle, Notion, Shopify, Patagonia) actually choose THESE tokens?

Return ONLY this JSON, no markdown fence:
{{
  "verdict": "pass" or "revise",
  "issues": ["short phrase per issue, max 5 items"],
  "changes": ["concrete instruction Claude can act on, max 5 items"]
}}

If the design is solid taste-wise, return verdict="pass" with empty arrays.
Be picky but not pedantic — only flag issues a senior designer would call out."""

    try:
        async with httpx.AsyncClient(timeout=_GEMINI_CRITIC_TIMEOUT) as client:
            resp = await client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"{_GEMINI_CRITIC_MODEL}:generateContent?key={gemini_key}",
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": 0.4},
                },
            )
        if resp.status_code != 200:
            logger.info(
                "Gemini design critic HTTP %d — skipping critic pass",
                resp.status_code,
            )
            return None

        from knowledge.loader import safe_gemini_text
        raw = safe_gemini_text(resp.json()).strip()
        if "```" in raw:
            raw = _re.sub(r"```(?:json)?", "", raw).strip("`").strip()
        parsed = _json.loads(raw)
        verdict = str(parsed.get("verdict", "pass")).lower().strip()
        if verdict not in ("pass", "revise"):
            verdict = "pass"
        return {
            "verdict": verdict,
            "issues": list(parsed.get("issues") or [])[:5],
            "changes": list(parsed.get("changes") or [])[:5],
        }
    except Exception as exc:
        logger.info("Gemini design critic skipped: %s", exc)
        return None


def _format_critic_feedback(critic: dict) -> str:
    """Format the Gemini critic's verdict as Claude-facing feedback text."""
    issues = critic.get("issues") or []
    changes = critic.get("changes") or []
    parts = ["⚠️ A senior designer (Gemini) reviewed your previous attempt and asked for revisions:"]
    if issues:
        parts.append("\nIssues:")
        parts.extend(f"  - {i}" for i in issues)
    if changes:
        parts.append("\nConcrete changes to apply:")
        parts.extend(f"  - {c}" for c in changes)
    parts.append(
        "\nApply these changes only. Keep the rest of the design's personality. "
        "Re-emit the tool call."
    )
    return "\n".join(parts)


# ─── Claude caller ──────────────────────────────────────────────────────────

async def _call_claude(
    system: str,
    user: str,
    api_key: str,
    user_id: str | None = None,
    websocket=None,
) -> Optional[dict]:
    """Call Claude with forced tool output. Returns the tool input dict, or None on failure.

    Retries automatically on rate limits / 5xx / network errors via the
    central llm_retry helper. Permanent errors (400/401/403) bubble up as
    None after a single attempt.
    """
    from app.services.llm_retry import (
        call_with_retry, classify_http_error, LLMPermanentError,
    )

    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }
    payload = {
        "model": _MODEL,
        "max_tokens": _MAX_TOKENS,
        "system": system,
        "messages": [{"role": "user", "content": user}],
        "tools": [_DESIGN_TOOL],
        "tool_choice": {"type": "tool", "name": "emit_design_system"},
    }

    async def _do_call() -> dict:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(_API_URL, headers=headers, json=payload)
        if resp.status_code != 200:
            raise classify_http_error(resp.status_code, resp.text)
        return resp.json()

    try:
        data = await call_with_retry(_do_call, label="design_system_builder", websocket=websocket)
    except LLMPermanentError as exc:
        logger.warning("Design Director permanent failure: %s", exc)
        return None
    except Exception as exc:
        logger.warning("Design Director failed after retries: %s", exc)
        return None

    # Meter token usage (fire-and-forget)
    try:
        from app.services.billing_meter import report_token_usage
        _u = data.get("usage") or {}
        report_token_usage(
            user_id,
            int(_u.get("input_tokens", 0) or 0),
            int(_u.get("output_tokens", 0) or 0),
            source="design_system_builder",
        )
    except Exception:
        pass

    try:
        for block in data.get("content") or []:
            if block.get("type") == "tool_use" and block.get("name") == "emit_design_system":
                return block.get("input") or {}
    except Exception as exc:
        logger.warning("Design Director response parse failed: %s", exc)
    return None


# ─── Public orchestrator ────────────────────────────────────────────────────

async def build_design_system(
    description: str,
    domain: str,
    brand_name: str,
    copy_tone: str,
    layout_archetype: str,
    api_key: str,
    vibe: str = "",
    cultural_atmosphere: str = "",
    gemini_key: str = "",
    websocket=None,
) -> Optional[dict]:
    """One Claude call that designs a bespoke, validated design system.

    Retries once with violation feedback on validation failure. Returns None
    if both attempts fail — caller falls back to old research-based flow.

    FAIL-SOFT: any exception logs a warning and returns None.
    """
    if not api_key:
        logger.info("Design Director skipped — no Anthropic API key")
        return None

    if websocket is not None:
        try:
            await websocket.send_json({
                "type": "progress",
                "content": "🎨 Designing bespoke design system...",
            })
        except Exception:
            pass

    # Attempt 1
    prompt = _build_user_prompt(
        description=description,
        domain=domain,
        brand_name=brand_name,
        copy_tone=copy_tone,
        layout_archetype=layout_archetype,
        vibe=vibe,
        cultural_atmosphere=cultural_atmosphere,
    )
    design = await _call_claude(_SYSTEM_PROMPT, prompt, api_key, websocket=websocket)
    if not design:
        logger.warning("Design Director attempt 1 returned no design")
        return None

    violations = validate_design_system(design, layout_archetype=layout_archetype)
    if not violations:
        logger.info(
            "Design Director attempt 1 valid — name=%s archetype=%s",
            design.get("design_system_name"), design.get("archetype"),
        )
        if websocket is not None:
            try:
                await websocket.send_json({
                    "type": "progress",
                    "content": f"✅ Design system locked: {design.get('design_system_name', 'Custom')} ({design.get('archetype', '')})",
                })
            except Exception:
                pass
        return design

    logger.info("Design Director attempt 1 had %d violations — retrying", len(violations))
    for _v in violations:
        logger.info("Design Director violation: %s", _v)
    feedback = "\n".join(f"- {v}" for v in violations)

    # Attempt 2
    prompt_retry = _build_user_prompt(
        description=description,
        domain=domain,
        brand_name=brand_name,
        copy_tone=copy_tone,
        layout_archetype=layout_archetype,
        vibe=vibe,
        cultural_atmosphere=cultural_atmosphere,
        retry_feedback=feedback,
    )
    design2 = await _call_claude(_SYSTEM_PROMPT, prompt_retry, api_key, websocket=websocket)
    if not design2:
        logger.warning("Design Director retry returned no design — using attempt 1 anyway")
        return design  # better than nothing

    violations2 = validate_design_system(design2, layout_archetype=layout_archetype)
    if violations2:
        logger.warning(
            "Design Director retry still has %d violations: %s — accepting anyway",
            len(violations2), "; ".join(violations2[:3]),
        )
    else:
        logger.info("Design Director retry valid after feedback")

    final_design = design2

    # ── Gemini taste critic (post-validation, agentic Claude ↔ Gemini loop) ──
    # Deterministic validation passed; now ask Gemini whether the design
    # actually fits the domain. One critic call, one optional Claude retry
    # with the critic's notes folded in. Skipped silently when no Gemini key.
    final_design = await _apply_gemini_critic(
        design=final_design,
        description=description,
        domain=domain,
        brand_name=brand_name,
        copy_tone=copy_tone,
        layout_archetype=layout_archetype,
        vibe=vibe,
        cultural_atmosphere=cultural_atmosphere,
        api_key=api_key,
        gemini_key=gemini_key,
        websocket=websocket,
    )

    if websocket is not None:
        try:
            await websocket.send_json({
                "type": "progress",
                "content": f"✅ Design system locked: {final_design.get('design_system_name', 'Custom')}",
            })
        except Exception:
            pass

    return final_design


async def _apply_gemini_critic(
    *,
    design: dict,
    description: str,
    domain: str,
    brand_name: str,
    copy_tone: str,
    layout_archetype: str,
    vibe: str,
    cultural_atmosphere: str,
    api_key: str,
    gemini_key: str,
    websocket,
) -> dict:
    """Run Gemini taste critic; on 'revise' verdict, ask Claude for one more pass.

    Always returns a design dict — never None. If anything fails, returns
    the original ``design`` unchanged.
    """
    if not gemini_key:
        return design

    if websocket is not None:
        try:
            await websocket.send_json({
                "type": "progress",
                "content": "🔍 Senior-designer review (Gemini)…",
            })
        except Exception:
            pass

    critic = await _gemini_design_critic(
        design=design,
        description=description,
        domain=domain,
        layout_archetype=layout_archetype,
        vibe=vibe,
        cultural_atmosphere=cultural_atmosphere,
        gemini_key=gemini_key,
    )

    if not critic or critic.get("verdict") != "revise":
        if critic:
            logger.info("Gemini design critic verdict=pass")
        return design

    issues = critic.get("issues") or []
    changes = critic.get("changes") or []
    if not issues and not changes:
        return design

    logger.info(
        "Gemini design critic verdict=revise — issues=%d changes=%d",
        len(issues), len(changes),
    )
    for _i in issues:
        logger.info("Gemini critic issue: %s", _i)
    for _c in changes:
        logger.info("Gemini critic change: %s", _c)

    if websocket is not None:
        try:
            await websocket.send_json({
                "type": "progress",
                "content": f"🎨 Refining design — {len(issues)} taste note(s) from senior review",
            })
        except Exception:
            pass

    feedback = _format_critic_feedback(critic)
    prompt_taste_retry = _build_user_prompt(
        description=description,
        domain=domain,
        brand_name=brand_name,
        copy_tone=copy_tone,
        layout_archetype=layout_archetype,
        vibe=vibe,
        cultural_atmosphere=cultural_atmosphere,
        retry_feedback=feedback,
    )
    refined = await _call_claude(_SYSTEM_PROMPT, prompt_taste_retry, api_key, websocket=websocket)
    if not refined:
        logger.info("Design taste-retry returned no design — keeping pre-critic version")
        return design

    # Re-validate to make sure the taste retry didn't introduce structural
    # regressions. If it did, prefer the original (which already passed).
    refined_violations = validate_design_system(refined, layout_archetype=layout_archetype)
    if refined_violations:
        logger.warning(
            "Design taste-retry introduced %d structural violation(s) — keeping pre-critic version",
            len(refined_violations),
        )
        return design

    logger.info(
        "Design taste-retry accepted — name=%s archetype=%s",
        refined.get("design_system_name"), refined.get("archetype"),
    )
    return refined


# ─── Rendering: design dict → research text blocks ──────────────────────────

def _render_css_variables(design: dict) -> str:
    p = design.get("palette") or {}
    radius = design.get("radius") or "0.5rem"
    dark = design.get("dark_palette") or {}

    lines = [
        f"--primary: {p.get('primary', '')} | --primary-foreground: {p.get('primary_foreground', '')}",
        f"--secondary: {p.get('secondary', '')} | --secondary-foreground: {p.get('secondary_foreground', '')}",
        f"--accent: {p.get('accent', '')} | --accent-foreground: {p.get('accent_foreground', '')}",
        f"--background: {p.get('background', '')} | --foreground: {p.get('foreground', '')}",
        f"--card: {p.get('card', '')} | --card-foreground: {p.get('card_foreground', '')}",
        f"--muted: {p.get('muted', '')} | --muted-foreground: {p.get('muted_foreground', '')}",
        f"--border: {p.get('border', '')} | --ring: {p.get('ring', '')}",
        f"--destructive: {p.get('destructive', '')} | --destructive-foreground: {p.get('destructive_foreground', '')}",
        f"--radius: {radius}",
    ]
    if dark:
        lines.append("")
        lines.append("[DARK MODE OVERRIDES]")
        for k, v in dark.items():
            lines.append(f"--{k.replace('_', '-')}: {v}")
    return "\n".join(lines)


def _apply_font_pairing(typography: dict) -> dict:
    """Override Director's free-form font fields with canonical pairing values.

    The Director picks a `font_pairing_id`; the renderer enforces the
    canonical heading_font / body_font / *_url / *_weight from the curated
    table. This guarantees:
      • Inter+Geist drift is impossible (pairing not in table)
      • Bad Google Fonts URLs (typos, missing axes) get fixed automatically
      • Same `font_pairing_id` always produces the same CSS @import lines

    If `font_pairing_id` is missing or unknown, the Director's free-form
    values are kept unchanged (back-compat for cached designs).
    """
    pairing_id = (typography.get("font_pairing_id") or "").strip().lower()
    if not pairing_id or pairing_id not in _FONT_PAIRINGS:
        return typography
    pairing = _FONT_PAIRINGS[pairing_id]
    out = dict(typography)
    out["heading_font"]     = pairing["heading_font"]
    out["heading_font_url"] = pairing["heading_font_url"]
    out["body_font"]        = pairing["body_font"]
    out["body_font_url"]    = pairing["body_font_url"]
    if not (typography.get("heading_weight") or "").strip():
        out["heading_weight"] = pairing["heading_weight"]
    if not (typography.get("body_weight") or "").strip():
        out["body_weight"]    = pairing["body_weight"]
    return out


def _render_fonts(design: dict) -> str:
    t = _apply_font_pairing(design.get("typography") or {})
    scale = t.get("type_scale") or []
    body_size = scale[2] if len(scale) > 2 else 16
    hero_size = scale[-1] if scale else 72
    h2_size = scale[-3] if len(scale) > 3 else 40
    pairing_line = ""
    if t.get("font_pairing_id"):
        pairing_line = f"font_pairing_id: {t.get('font_pairing_id')}\n"
    return (
        f"{pairing_line}"
        f"heading: {t.get('heading_font', '')} ({t.get('heading_font_url', '')})\n"
        f"body: {t.get('body_font', '')} ({t.get('body_font_url', '')})\n"
        f"hero_size: {hero_size}px / 1.05 / -0.02em\n"
        f"h2_size: {h2_size}px / weight {t.get('heading_weight', '700')}\n"
        f"body_size: {body_size}px / 1.6\n"
        f"overall_vibe: {t.get('overall_vibe', '')}"
    )


def _render_palette_notes(design: dict) -> str:
    p = design.get("palette") or {}
    strategy = design.get("color_application_strategy") or ""
    return (
        f"strategy: {strategy}\n"
        f"background: {p.get('background', '')} | foreground: {p.get('foreground', '')}\n"
        f"primary (accent): {p.get('primary', '')} on {p.get('primary_foreground', '')}\n"
        f"accent: {p.get('accent', '')} on {p.get('accent_foreground', '')}\n"
        f"muted text: {p.get('muted_foreground', '')} on {p.get('muted', '')}\n"
        f"chart_colors: {', '.join(design.get('chart_colors') or [])}"
    )


def _render_typography_notes(design: dict) -> str:
    t = _apply_font_pairing(design.get("typography") or {})
    scale = t.get("type_scale") or []
    return (
        f"heading: {t.get('heading_font', '')} weight {t.get('heading_weight', '')}, style {t.get('heading_style', 'normal')}\n"
        f"body: {t.get('body_font', '')} weight {t.get('body_weight', '')}\n"
        f"type_scale (px): {scale}\n"
        f"overall_vibe: {t.get('overall_vibe', '')}"
    )


def _render_brand_mark(design: dict) -> str:
    bm = design.get("brand_mark") or {}
    return (
        f"treatment: {bm.get('treatment', 'wordmark')}\n"
        f"font: {bm.get('font', '')}\n"
        f"weight: {bm.get('weight', '700')}\n"
        f"style: {bm.get('style', 'normal')}\n"
        f"case: {bm.get('case', 'as-typed')}\n"
        f"tracking: {bm.get('tracking', '-0.01em')}\n"
        f"icon: {bm.get('icon', '')}\n"
        f"color_token: {bm.get('color_token', 'foreground')}\n"
        f"size_desktop: {bm.get('size_desktop', '22px')}\n"
        f"placement: {bm.get('placement', 'navbar_left')}\n"
        f"\n"
        f"INSTRUCTION: Every Header/Navbar/Sidebar MUST render this brand mark. "
        f"Never output a navbar with only menu links and no logo."
    )


def _render_radius_tokens(design: dict) -> str:
    rt = design.get("radius_tokens") or {}
    radius = design.get("radius") or "0.5rem"
    lang = design.get("border_radius_language") or ""
    lines = [
        f"base: {radius}",
        f"language: {lang}",
        f"button: {rt.get('button', radius)}",
        f"input: {rt.get('input', radius)}",
        f"card: {rt.get('card', radius)}",
        f"badge: {rt.get('badge', radius)}",
        f"image: {rt.get('image', radius)}",
        f"modal: {rt.get('modal', radius)}",
        f"tooltip: {rt.get('tooltip', radius)}",
        "",
        "INSTRUCTION: Every radius in the generated code MUST match one of these "
        "tokens. No ad-hoc values like rounded-xl if cards use rounded-md.",
    ]
    return "\n".join(lines)


def _render_admin_ui_language(design: dict) -> str:
    density = design.get("density_mode") or "comfortable"
    status = design.get("status_palette") or {}
    table = design.get("table_language") or {}
    form = design.get("form_language") or {}
    sidebar = design.get("sidebar_language") or {}
    toolbar = design.get("toolbar_language") or {}
    empty = design.get("empty_state_language") or {}
    viz = design.get("data_viz_style") or {}

    return (
        f"density_mode: {density}\n"
        f"\n"
        f"status_palette:\n"
        f"  success: {status.get('success', '')} on {status.get('success_foreground', '')}\n"
        f"  warning: {status.get('warning', '')} on {status.get('warning_foreground', '')}\n"
        f"  info:    {status.get('info', '')} on {status.get('info_foreground', '')}\n"
        f"  neutral: {status.get('neutral', '')} on {status.get('neutral_foreground', '')}\n"
        f"  error:   {status.get('error', '')} on {status.get('error_foreground', '')}\n"
        f"\n"
        f"table:\n"
        f"  row_height: {table.get('row_height', '44px')}\n"
        f"  border_style: {table.get('border_style', 'hairline_rows')}\n"
        f"  header_weight: {table.get('header_weight', '500')}\n"
        f"  header_background: {table.get('header_background', 'transparent')}\n"
        f"  hover_treatment: {table.get('hover_treatment', 'muted')}\n"
        f"  cell_padding: {table.get('cell_padding', '12px 16px')}\n"
        f"  → {table.get('description', '')}\n"
        f"\n"
        f"form:\n"
        f"  label_position: {form.get('label_position', 'above')}\n"
        f"  label_style: {form.get('label_style', '')}\n"
        f"  input_style: {form.get('input_style', 'outlined')}\n"
        f"  input_height: {form.get('input_height', '40px')}\n"
        f"  focus_style: {form.get('focus_style', '')}\n"
        f"  error_style: {form.get('error_style', '')}\n"
        f"  spacing_between: {form.get('spacing_between', '20px')}\n"
        f"  → {form.get('description', '')}\n"
        f"\n"
        f"sidebar:\n"
        f"  width: {sidebar.get('width', '256px')}\n"
        f"  variant: {sidebar.get('variant', 'wide_labelled')}\n"
        f"  background: {sidebar.get('background', 'card')}\n"
        f"  active_treatment: {sidebar.get('active_treatment', 'primary_10_bg_plus_primary_text')}\n"
        f"  section_divider: {sidebar.get('section_divider', 'uppercase_label')}\n"
        f"  icon_size: {sidebar.get('icon_size', '18px')}\n"
        f"  collapsed_behavior: {sidebar.get('collapsed_behavior', 'none')}\n"
        f"  → {sidebar.get('description', '')}\n"
        f"\n"
        f"toolbar:\n"
        f"  search_chrome: {toolbar.get('search_chrome', 'rounded_filled_muted')}\n"
        f"  filter_style: {toolbar.get('filter_style', 'dropdown_buttons')}\n"
        f"  bulk_action_style: {toolbar.get('bulk_action_style', 'sticky_bar_bottom')}\n"
        f"  primary_cta: {toolbar.get('primary_cta', 'filled primary with plus icon, top-right')}\n"
        f"  → {toolbar.get('description', '')}\n"
        f"\n"
        f"empty_state:\n"
        f"  illustration_style: {empty.get('illustration_style', 'line_icon')}\n"
        f"  copy_tone: {empty.get('copy_tone', 'direct and helpful')}\n"
        f"  cta_placement: {empty.get('cta_placement', 'centered_button_below')}\n"
        f"  → {empty.get('description', '')}\n"
        f"\n"
        f"data_viz:\n"
        f"  line_weight: {viz.get('line_weight', '2px')}\n"
        f"  axis_style: {viz.get('axis_style', 'hairline_muted')}\n"
        f"  grid_style: {viz.get('grid_style', 'dotted_horizontal')}\n"
        f"  tooltip_style: {viz.get('tooltip_style', 'floating_card_shadow')}\n"
        f"  point_style: {viz.get('point_style', 'filled_circle')}\n"
        f"  → {viz.get('description', '')}\n"
        f"\n"
        f"INSTRUCTION: Every DataTable, form, sidebar, toolbar, empty state, and "
        f"chart in this project MUST follow the recipe above. Values here WIN over "
        f"any generic admin defaults in the phase-1/phase-2 code-gen prompts."
    )


def _norm_archetype(value: str) -> str:
    """Normalize archetype-style enum strings to the dashed form.

    The wrapper-template lookup tables in project_generator.py key on dashed
    names (e.g. 'cinematic-parallax', 'lift-and-shadow'). The Director
    occasionally emits underscored variants from training memory despite the
    schema description; this rewrites them so the wrapper match never
    silently falls through to the generic default.
    """
    if not value:
        return ""
    return str(value).strip().replace("_", "-").lower()


def _render_layout_blueprint(design: dict) -> str:
    card = design.get("card_language") or {}
    motion = design.get("motion_language") or {}
    rhythm = design.get("section_rhythm") or {}
    spacing = design.get("spacing") or {}
    rhythm_lines = "\n".join(f"  {k}: {v}" for k, v in rhythm.items())

    return (
        f"design_dna_summary: {design.get('personality', '')} — archetype {design.get('archetype', '')}\n"
        f"\n"
        f"hero_archetype: {_norm_archetype(design.get('hero_archetype', ''))}\n"
        f"features_archetype: {_norm_archetype(design.get('features_archetype', ''))}\n"
        f"\n"
        f"card_language: radius {card.get('radius', '')} | border {card.get('border', '')} | shadow {card.get('shadow', '')} | padding {card.get('padding', '')}\n"
        f"  → {card.get('description', '')}\n"
        f"\n"
        f"typography_pairing: {(design.get('typography') or {}).get('heading_font', '')} heading + {(design.get('typography') or {}).get('body_font', '')} body\n"
        f"\n"
        f"motion_language: enter {motion.get('enter', '')} | hover {motion.get('hover', '')} | scroll {motion.get('scroll', '')} | {motion.get('duration', '')} {motion.get('easing', '')}\n"
        f"motion_signature: easing={motion.get('easing_signature', 'quint-out')} | "
        f"durations(fast/base/slow)={(motion.get('durations') or {}).get('fast', '180ms')}/"
        f"{(motion.get('durations') or {}).get('base', '550ms')}/"
        f"{(motion.get('durations') or {}).get('slow', '1000ms')} | "
        f"signature_transition={motion.get('signature_transition', 'minimal-precise')} | "
        f"cursor_treatment={motion.get('cursor_treatment', 'default')}\n"
        f"\n"
        f"decorative_pattern: {design.get('decorative_pattern', '')}\n"
        f"signature_motif: {design.get('signature_motif', '')}\n"
        f"border_radius_language: {_norm_archetype(design.get('border_radius_language', ''))}\n"
        f"color_application_strategy: {_norm_archetype(design.get('color_application_strategy', ''))}\n"
        f"hover_interaction_style: {_norm_archetype(design.get('hover_interaction_style', ''))}\n"
        f"spacing_rhythm: {_norm_archetype(spacing.get('rhythm', ''))} (base {spacing.get('base', 8)}px, section_y {spacing.get('section_padding_y', 96)}px)\n"
        f"\n"
        f"section_rhythm:\n{rhythm_lines}\n"
        f"\n"
        f"distinctive_moves:\n" + "\n".join(f"  - {m}" for m in (design.get('distinctive_moves') or [])) + "\n"
        f"\n"
        f"banned_patterns:\n" + "\n".join(f"  - {b}" for b in (design.get('banned_patterns') or [])) + "\n"
        f"\n"
        f"novelty_check: This design is bespoke to the project via archetype {design.get('archetype', '')} — validated palette contrast, musical type scale, consistent radius language."
    )


def _render_image_composition(design: dict) -> str:
    ic = design.get("image_composition") or {}
    overlay = ic.get("overlay_pattern", "").strip() or "dark_scrim"
    scrim = ic.get("overlay_scrim_classes", "").strip()
    text_color = ic.get("overlay_text_color", "").strip() or "text-foreground"
    container = ic.get("image_container_mode", "").strip() or "full_bleed"
    form = ic.get("form_treatment", "").strip() or "card_lift_solid"
    lines = [
        f"overlay_pattern: {overlay}",
        f"overlay_scrim_classes: {scrim}",
        f"overlay_text_color: {text_color}",
        f"image_container_mode: {container}",
        f"form_treatment: {form}",
        "",
        "INSTRUCTION — UNIVERSAL image+text rules for EVERY section that "
        "mixes copy with photography (hero, testimonial, reservation, "
        "contact, booking, feature banners, admin hero banners, ecommerce "
        "product shots):",
        f"  1. Apply overlay_pattern='{overlay}' exactly. If scrim variant, "
        f"     use the scrim classes '{scrim}' as an absolute inset-0 div "
        f"     BETWEEN the image and the text.",
        f"  2. Text over imagery MUST use '{text_color}'. Never the default "
        f"     text-foreground on an unmediated photograph.",
        f"  3. Image container must be '{container}'. "
        f"     If split_half: ONE column holds the photo, the OTHER column holds "
        f"     text/CTAs on bg-background — the text column IS the content, NOT whitespace. "
        f"     Do NOT add a background image to the text column or the section wrapper. "
        f"     If full_bleed: ONE image fills the section; do NOT also add a side panel image.",
        f"  4. Forms (reservation, contact, signup, booking, newsletter) "
        f"     use form_treatment='{form}'. NEVER glassmorphism over busy "
        f"     photography — inputs become unreadable.",
        "  5. Quote/testimonial blocks over imagery: require dark_scrim or "
        "     card_lift. Bare italic serif on a light photograph is BANNED.",
    ]
    return "\n".join(lines)


def inject_design_blocks(research: str, design: dict) -> str:
    """Replace (or inject) the design-related headers in research with the Design Director output.

    The downstream distiller and schema builder parse these exact headers:
      ===DESIGN_SYSTEM_NAME===
      ===PALETTE===
      ===TYPOGRAPHY===
      ===CSS_VARIABLES===
      ===FONTS===
      ===BRAND_MARK===        (NEW — logo/wordmark spec)
      ===RADIUS_TOKENS===     (NEW — per-element radii)
      ===IMAGE_COMPOSITION=== (NEW — overlay/scrim/form-card rules)
      ===LAYOUT_BLUEPRINT===
      ===ADMIN_UI_LANGUAGE=== (admin/CRM/TMS archetypes only)

    This function REMOVES any existing instances of these blocks in research
    and prepends the Design Director's rendered blocks so they take priority.
    """
    if not research or not design:
        return research

    # Remove any existing instances of these headers by splitting on each and
    # dropping the header's block contents.
    headers = [
        "===DESIGN_SYSTEM_NAME===",
        "===PALETTE===",
        "===TYPOGRAPHY===",
        "===CSS_VARIABLES===",
        "===FONTS===",
        "===BRAND_MARK===",
        "===RADIUS_TOKENS===",
        "===IMAGE_COMPOSITION===",
        "===LAYOUT_BLUEPRINT===",
        "===ADMIN_UI_LANGUAGE===",
    ]

    stripped = research
    for header in headers:
        while header in stripped:
            start = stripped.index(header)
            rest = stripped[start + len(header):]
            next_header_pos = rest.find("===")
            if next_header_pos == -1:
                stripped = stripped[:start].rstrip()
            else:
                stripped = stripped[:start] + rest[next_header_pos:]

    blocks = [
        f"===DESIGN_SYSTEM_NAME===\n{design.get('design_system_name', '').strip()}",
        f"===CSS_VARIABLES===\n{_render_css_variables(design)}",
        f"===FONTS===\n{_render_fonts(design)}",
        f"===BRAND_MARK===\n{_render_brand_mark(design)}",
        f"===RADIUS_TOKENS===\n{_render_radius_tokens(design)}",
        f"===IMAGE_COMPOSITION===\n{_render_image_composition(design)}",
        f"===PALETTE===\n{_render_palette_notes(design)}",
        f"===TYPOGRAPHY===\n{_render_typography_notes(design)}",
        f"===LAYOUT_BLUEPRINT===\n{_render_layout_blueprint(design)}",
    ]

    # Only emit the admin block if the Director filled in admin fields.
    # The admin fields are optional in the schema — their presence indicates
    # the project IS admin-family.
    if design.get("density_mode") or design.get("status_palette") or design.get("table_language"):
        blocks.append(f"===ADMIN_UI_LANGUAGE===\n{_render_admin_ui_language(design)}")

    design_text = "\n\n" + "\n\n".join(blocks) + "\n\n"

    # Prepend to research so the Design Director blocks win in any parsing tie.
    return design_text + stripped.lstrip()
