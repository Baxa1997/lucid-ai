"""Parallel per-section codegen for landing pages.

Replaces Phase 1 + Phase 2 single-call codegen for the landing flow with
N parallel Claude calls, one per section. Each call generates exactly one
section component file that reads its copy / items / images from
`@/content/landing.json` (written upstream by landing_content.py).

Why parallel-per-section:
  • Faster wall-time (8 sections in ~30-60s vs 3-5 min serial)
  • Smaller per-call token budget → fewer truncations / hallucinations
  • Each call has tight context — only the section it's building
  • Cohesion comes from shared design tokens + 2 sibling specs passed as context

This module reuses the existing `call_claude_for_json` helper for retry,
billing, and streaming behavior — no new HTTP / model code.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any

from app.services.project_writer import write_text_file

logger = logging.getLogger(__name__)

# Per-section budget. Sections are small, focused components — even a complex
# pricing table fits in <8K tokens of JSX. Bumped to 24K so the new
# editorial-numbering eyebrow + hero floating data cards + motif watermarks
# don't get truncated on rich sections (hero with overlay cards, full pricing
# table, multi-step process). At ~150 tok/s that's ~160s — comfortably inside
# the 240s outer timeout. Sonnet 4 supports up to 64K output tokens.
_SECTION_MAX_TOKENS = 24000


# ── Section anatomy: minimal fallback skeletons ──────────────────────
# We deliberately keep these THIN and CATEGORY-AGNOSTIC. The real per-section
# anatomy comes from `brief["visual_dna"]["section_anatomies"][section_type]`,
# which Gemini writes from grounded research. These fallbacks fire only when:
#   • visual_dna research failed entirely, OR
#   • Gemini didn't produce an anatomy for THIS specific section type.
# A fallback's job is to ship a structurally-correct section, not a beautiful
# one. Cohesion + culture come from visual_dna; pixel correctness comes from
# PROJECT_DESIGN_TOKENS + the global rules in the system prompt.
_FALLBACK_SKELETONS: dict[str, str] = {
    "hero": (
        "STRUCTURAL FLOOR — hero section minimum:\n"
        "  ⚠️ NON-NEGOTIABLE MINIMUM — every hero MUST ship ALL of these. A hero that\n"
        "    omits any of them looks plain and generic and is a failure:\n"
        "      1. EYEBROW tag above the headline (small uppercase tracked text, ≤32 chars,\n"
        "         `text-xs md:text-sm uppercase tracking-[0.18em] text-primary font-semibold`,\n"
        "         optionally prefixed with an em-dash flourish `— BRAND TAGLINE`). NEVER omit.\n"
        "      2. HEADLINE with ONE ITALIC ACCENT WORD wrapped in\n"
        "         `<span className=\"italic text-primary\">word</span>` — picks a load-bearing\n"
        "         noun or verb (the brand's hook word). NEVER all-plain text.\n"
        "      3. SUBHEAD — exactly 1 sentence, max 140 chars, max-w-xl, text-muted-foreground.\n"
        "      4. AT LEAST 1 PRIMARY CTA + 1 SECONDARY CTA (secondary is outline / ghost).\n"
        "         A lone single CTA in the hero reads as a blank-template prototype.\n"
        "      5. ≥1 FLOATING DATA CARD overlaying the hero photo (patterns A/B/C/D) — see\n"
        "         FLOATING DATA CARDS section below for size + position rules. NEVER skip\n"
        "         on patterns A-D; pattern E is the ONLY exception.\n"
        "      6. AT LEAST 1 DECORATIVE MOTIF from visual_dna.decorative_motifs rendered as\n"
        "         either a corner SVG flourish, an eyebrow ornament, or a thin divider line\n"
        "         under the headline. NEVER a plain hero with no decorative element.\n"
        "      7. TRUST CHIPS ROW below the CTAs (3-5 inline chips with icon + 1-3 word label\n"
        "         — \"CEFR Aligned\", \"James Beard 2024\", \"Open · Closes 11pm\", etc.) sourced\n"
        "         from brand.business_info OR section.items OR domain-realistic facts.\n"
        "         OPTIONAL only when pattern is E (Centered Editorial).\n"
        "    Self-check before output: count items 1-7 in your JSX. If any is missing,\n"
        "    rewrite. A minimal hero is the #1 cause of \"too plain / generic\" feedback.\n"
        "  • Outer <section> is `relative isolate min-h-[640px] md:min-h-[100svh] lg:min-h-screen overflow-hidden`.\n"
        "      Why full-viewport at md+: a hero that stops at 820px on a 1080p monitor reads as a half-finished\n"
        "      banner — the user sees the next section's eyebrow peeking under the fold. `min-h-screen` at lg+\n"
        "      (and `100svh` at md to account for iOS dynamic viewport) makes the hero command the entire first\n"
        "      screen, which is the expected behavior for editorial, hospitality, education, and product brands.\n"
        "      Keep `640px` floor at mobile so very short phone viewports don't collapse the hero into a strip.\n"
        "      Inner content (headline + CTAs + floating cards + hero image) sits inside `flex flex-col justify-center`\n"
        "      so it vertically centers when the section is taller than the content — never top-anchored with a\n"
        "      400px gap below.\n"
        "  • THREE stacked layers when bg media exists: media (z-0) → readability overlay (z-10) → content (z-20).\n"
        "  • Foreground content includes (in order): eyebrow tag → headline (text-5xl md:text-6xl lg:text-7xl, leading-[1.05]) → 1-line subhead (text-lg md:text-xl, max-w-xl) → ≥1 CTA + 0-1 secondary.\n"
        "  • HEADLINE TYPOGRAPHY — render the headline as ONE flowing inline `<h1>` with the full string as its child. NEVER split punctuation onto its own line. NEVER force a `<span className=\"block\">` per clause. Let CSS line-wrap decide breaks based on container width.\n"
        "    HARD RULES (no exceptions):\n"
        "      1. NO `<br/>` inside the headline.\n"
        "      2. NO `<span className=\"block\">` wrapping a sub-clause unless the headline is a SINGLE long clause with ≤8 words AND visual_dna.layout_signature explicitly calls for centered-editorial-stacked. In ALL other cases the headline is a single `<h1>` with all text inline. The accent word may be wrapped in `<span className=\"italic text-primary\">` BUT THAT SPAN MUST NOT BE `block` OR `flex` — leave it as default inline.\n"
        "      3. If the headline string contains MULTIPLE sentences (more than one `.`, `!`, or `?`), render them ALL INLINE inside one `<h1>`. The reader gets a 2-3 line natural wrap, not a fixed N-line stack. \"The Burger. The Bean. The Best of Both.\" is ONE inline `<h1>`, NOT four `<span className=\"block\">` lines.\n"
        "      4. An em-dash (—) or en-dash (–) ALWAYS stays glued to the phrase on its left, never the first character of a new visual line. If you can't avoid it landing alone on a wrap, drop the dash entirely or replace with a period.\n"
        "    The size tokens (`text-5xl md:text-6xl lg:text-7xl leading-[1.05]`) already give the headline its editorial impact. The model does NOT need to also force structure by stacking spans — that always produces the 4-line awkward-stack failure mode.\n"
        "  • Wrap content group in <Reveal variant=\"fade-up\">.\n"
        "  • FLOATING BADGES / CHIPS (Score Guarantee badge, status pill, callout card) MUST stay inside the section's content container — never use negative offsets that push them outside the viewport (`-top-4`, `-right-8`, etc.) and never position them with absolute coordinates that exceed the parent. Use `absolute top-4 right-4` AT MOST, and prefer placing them in the document flow inside the copy column instead of floating. A clipped or floating-off-edge badge is a hard fail — it reads as a layout bug.\n"
        "  • Hero copy column MUST sit on a grid (grid grid-cols-1 lg:grid-cols-2 gap-8 + relative z-20 on the copy block) — NEVER stack copy on top of the hero image with `position: absolute`. Absolute layered copy creates overlapping/unreadable text at every viewport unless the photo is intentionally darkened with the readability overlay.\n"
        "  • DECORATIVE WATERMARK TEXT (large background numerals, oversized initials, ghosted brand letters) is allowed but MUST be DIFFERENT content from the foreground label. NEVER render `{item.name}` or `{member.name}` or any prose interpolation twice — once as the watermark and once as the readable title — that creates a confusing double-vision ghost. Watermarks are for static decoration (an index number like `01`, a single Greek letter, a quote mark `&ldquo;`), not for repeating the title.\n"
        "    WATERMARK CONTAINMENT — non-negotiable:\n"
        "      • The OUTER `<section>` MUST carry `overflow-hidden` so a decorative\n"
        "        watermark CANNOT bleed into the header above or the next section\n"
        "        below. NEVER omit `overflow-hidden` on a hero that includes any\n"
        "        absolute-positioned decorative element.\n"
        "      • Watermark size is CAPPED at `text-[14rem]` (≈ 224px). NEVER\n"
        "        `text-[20rem]` or larger — at that size a 2-character glyph spans\n"
        "        300-400px and inevitably overlaps neighbors.\n"
        "      • Watermark POSITION uses small offsets only: `top-8 right-8` /\n"
        "        `bottom-8 right-8` etc. NEVER `-top-20`, `-right-32`, or any\n"
        "        negative offset that exceeds the parent. A clipped or floating-off-\n"
        "        edge watermark reads as a layout bug.\n"
        "      • Watermark opacity ≤ `text-foreground/8` so it sits below the\n"
        "        content visually AND uses `pointer-events-none z-0` so it never\n"
        "        eats clicks meant for nav/CTA buttons that overlap it.\n"
        "  • COMPOSITION — pick ONE pattern from this catalog (driven by visual_dna.layout_signature + section_flavors.hero):\n"
        "      A. FULL-BLEED PHOTO — hero image covers entire section as <Image fill object-cover>; readability overlay `bg-foreground/40` (light bg) or `bg-foreground/60` (over busy photo); content left-aligned in a max-w-2xl block. Best for travel / hospitality / restaurants / lifestyle. Headline allowed to use ONE italic accent word: `<span className=\"italic text-primary\">Table</span>` (the Bella Luna pattern).\n"
        "      B. ASYMMETRIC SPLIT 60/40 — copy column (lg:col-span-3) on left with eyebrow + h1 + subhead + CTA; image column (lg:col-span-2) on right with a single large photo `aspect-[4/5]` + rounded-3xl + decorative motif overlay. No overlay on copy column. Best for editorial brands, architecture, premium product (the architecture-studio + Veloretti pattern).\n"
        "      C. PRODUCT-STAGE DARK — dark surface (`bg-foreground text-background` or `bg-card`), single hero photo (a product, a dish, an architectural detail) centered with a soft radial glow underneath (`absolute inset-x-0 bottom-0 h-32 bg-primary/30 blur-3xl`); headline + sub copy bottom-left, CTA bottom-right. Best for luxury / fragrance / premium audio / fine dining (the Chanel + WAAW pattern).\n"
        "      D. FLOATING COUNTER — main photo left or right at `aspect-[4/3]`, secondary photo card stacked at small size with a numbered indicator (`<span className=\"text-3xl font-bold\">03</span>` + `<ChevronLeft/>` `<ChevronRight/>` icons). Architecture / portfolio / gallery sites (the architecture-site pattern with `03→`).\n"
        "      E. CENTERED EDITORIAL — fully centered, oversized serif headline that spans 3 lines (text-6xl md:text-7xl lg:text-8xl), 1-line eyebrow above, single CTA below; minimal photo treatment OR a small framed photo card at the bottom. Best for boutique / wedding / ceremony / cultural-institution brands.\n"
        "    Default if visual_dna offers no signal: pick A for hospitality/travel, B for architecture/product, C for luxury/fragrance, E for ceremony/cultural.\n"
        "  • FLOATING DATA CARDS (REQUIRED on patterns A, B, C, D when a hero photo is present): overlay 1-2 SMALL UI chips on the hero image to make it feel like a live product, not stock photography. SIZE CAP: each card MUST fit on a single visual line, `max-w-[240px]` ABSOLUTE — they are CHIPS, not panels. NEVER render a multi-row form, a search widget, or anything wider than ~240px as a 'floating card'. If the brief gives course types / filter values, that goes into the section BODY below the hero copy, not as a hero overlay. Each card sits on the photo with backdrop-blur + subtle border + small shadow, NOT inside the copy column. POSITIONING — the header occupies the top of the section and almost always carries a primary CTA in its top-right corner. To avoid colliding with the header CTA, place floating cards in the LOWER HALF of the hero only: ONE bottom-right (`absolute bottom-6 right-6 md:bottom-10 md:right-10 max-w-[240px] z-20`) and at most ONE bottom-left (`absolute bottom-20 left-6 md:bottom-24 md:left-10 max-w-[240px] z-20`) — never in the top corners, never on the same edge as each other. NEVER one panel covering >25% of the hero area. Card surface: `inline-flex items-center gap-3 px-4 py-3 rounded-2xl bg-background/85 backdrop-blur-md border border-border/40 shadow-lg`. Content MUST be business-specific (read from brand.business_info or section.items when present, otherwise infer from the brief domain):\n"
        "      • Restaurant / café    → live stat (\"4.9 ★ — 2,340 reviews\"), status pill (\"Open · Closes 11pm\"), or menu badge (\"Tonight's special — Pici al Tartufo\").\n"
        "      • Hotel / travel       → progress bar (\"32 / 48 rooms booked tonight\"), location chip with map pin (\"Brooklyn Heights · 0.4 mi from Promenade\").\n"
        "      • SaaS / tech / dev    → metric card (\"+38% conversion · 7-day rollout\"), status row (\"All systems normal\" + green dot).\n"
        "      • Logistics / freight  → counter card (\"412 loads delivered this week\"), live-tracker badge (\"En route — ETA 4h 12m\").\n"
        "      • Healthcare / wellness → rating row (\"5.0 ★ — 312 patient reviews\"), next-available chip (\"Earliest: Thu 9:30am\").\n"
        "      • Education / kids     → enrollment bar (\"82% full — Fall 2026 cohort\"), badge (\"Accredited K-8 · est. 1972\").\n"
        "      • Agency / studio      → metric (\"24 launches · 9 awards · 2025\"), status (\"Now booking Q3\").\n"
        "    These cards are STATIC display elements — no React state needed, no functional widget. Use real lucide icons (Star, MapPin, Clock, TrendingUp, CheckCircle2, Truck, GraduationCap) sized `h-4 w-4` with `text-primary` stroke. NEVER render generic placeholder data (\"100+\", \"Awesome\", \"Trusted\") — derive numbers from brand.business_info, section.items, or domain-realistic numbers. Pattern E (Centered Editorial) is the ONE exception and may skip these cards.\n"
        "  • Apply ≥2 motifs/textures from visual_dna in concrete accent positions (eyebrow ornament, divider, decorative SVG in a corner — small, not loud)."
    ),
    "menu": (
        "STRUCTURAL FLOOR — list-of-items section minimum:\n"
        "  • Heading group at top (eyebrow + h2 + 1-line subhead).\n"
        "  • Items rendered grouped by category (when item.label or item.category exists), or as a single grid otherwise.\n"
        "  • Each item shows: title (font-semibold), description (text-sm text-muted-foreground), price/value (when present, font-semibold text-primary).\n"
        "  • Photo-led grid when section.images[i] exists for items; editorial text-only rows when not.\n"
        "  • Cards in a row share aspect ratios + heights; mt-auto on price/CTA so footers align.\n"
        "  • Decorative elements (category dividers, bullet markers, frame ornaments) come from visual_dna.decorative_motifs.\n"
        "  • FOR E-COMMERCE / PRODUCT BRANDS — when this section is rendering\n"
        "    physical or digital products (not menu items / dishes):\n"
        "      • EVERY item card MUST show a real `$NN.NN` or `$NN` price\n"
        "        (NOT \"Inquire\" / \"Coming Soon\" placeholders).\n"
        "      • EVERY item card MUST have a buy/shop CTA labeled with one of:\n"
        "        \"Shop Now\", \"Add to Cart\", \"Buy Now\", \"View Details\".\n"
        "        Quality-gate fails when product cards lack visible prices +\n"
        "        these CTA tokens. \"Learn More\" doesn't qualify for products."
    ),
    "gallery": (
        "STRUCTURAL FLOOR — image grid section minimum:\n"
        "  • Heading group above (eyebrow + h2 + subhead, max-w-2xl).\n"
        "  • Section root needs `overflow-hidden` to contain decorative blobs.\n"
        "  • PATTERN — pick ONE from this catalog (driven by visual_dna.layout_signature + section_flavors.gallery):\n"
        "      A. BENTO MOSAIC — `grid grid-cols-12 gap-3 md:gap-4` with 1 feature image at `col-span-7 row-span-2 aspect-[4/3]` + 2 stacked at `col-span-5 aspect-[3/2]` + 3-4 smaller `col-span-3 aspect-square` to fill. Editorial / architecture / portfolio brands (the architecture-site bottom-row pattern).\n"
        "      B. SCROLL-SNAP HORIZONTAL — outer `flex gap-4 overflow-x-auto snap-x snap-mandatory pb-4 -mx-4 px-4` with each tile `min-w-[280px] md:min-w-[360px] snap-start aspect-[3/4] rounded-2xl`. Add a `text-xs text-muted-foreground` hint below: `← Scroll to explore`. Best for travel / destination / restaurant / story-led brands.\n"
        "      C. NUMBERED COUNTER CAROUSEL — 1 large featured photo on left at `aspect-[4/3] rounded-3xl`, with a `<div className=\"absolute bottom-6 right-6\">` block showing `<span className=\"text-5xl font-bold text-background\">0{{currentIndex+1}}</span><span className=\"text-background/60\"> / 0{{section.images.length}}</span>` plus prev/next pill buttons. useState + Reveal. Architecture / portfolio (the architecture-site `03 ←→` pattern).\n"
        "      D. MAGAZINE 12-COL ASYMMETRIC — `grid grid-cols-12 gap-4` with rows of (col-span-8 + col-span-4) alternating (col-span-4 + col-span-8). Each row has ONE feature image + 1-2 supporting smaller tiles. Editorial / publication / agency feel.\n"
        "      E. UNIFORM 4-COL THUMBNAILS — `grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3` with all tiles at the same aspect-square. FALLBACK ONLY — picks this when content count exceeds 16 or none of A-D fits. Avoid for hero gallery sections.\n"
        "    Default: A for editorial/architecture/portfolio, B for travel/destination/restaurant, C for portfolio with sequenced shots, D for magazine/agency, E only as last resort.\n"
        "  • Tiles use varied aspect ratios (aspect-[4/5] | aspect-square | aspect-[3/4]) for editorial rhythm — never uniform thumbnails in A-D patterns.\n"
        "  • Each tile: `relative overflow-hidden` with photo as <Image fill object-cover> + hover scale (`transition-transform duration-500 hover:scale-105`)."
    ),
    "testimonials": (
        "STRUCTURAL FLOOR — testimonial cards minimum:\n"
        "  • Heading group above.\n"
        "  • ≥3 quote cards (grid or carousel — pick from visual_dna.section_flavors.testimonials).\n"
        "  • Each card: optional star row (lucide Star, fill-primary), quote body (italic or display serif when quote is hero-level), attribution row (avatar circle with initials OR <Image>, name font-semibold, role/location text-sm muted).\n"
        "  • Cards in a row share heights via h-full + items-stretch.\n"
        "  • Decorative quote-mark glyph or culturally-resonant frame element from visual_dna.decorative_motifs."
    ),
    "features": (
        "STRUCTURAL FLOOR — capability/benefits section minimum:\n"
        "  • Heading group above (eyebrow + h2 + subhead).\n"
        "  • Render items in one of: 3-col icon grid | split-image-bullets | numbered-stepper | editorial-numbered-list | bento — pick from visual_dna.layout_signature + section_flavors.\n"
        "  • Each item: icon chip (h-10 w-10 or h-12 w-12, fixed) OR oversized numeral (when stepper/editorial), title (font-semibold), description (text-sm text-muted-foreground).\n"
        "  • Cards equalize via h-full + items-stretch; gap-4 md:gap-6 lg:gap-8.\n"
        "  • Lucide icons should match visual_dna.iconography_anchors when item.icon is available."
    ),
    "press": (
        "STRUCTURAL FLOOR — press / publications section minimum (NEVER overlapping cards):\n"
        "  • Heading group with eyebrow ('PRESS' or research-grounded label).\n"
        "  • FLAT logo strip — `grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-x-6 md:gap-x-10 gap-y-8 items-center`. Each cell renders the publication name as an uppercase wordmark (NOT an Unsplash image).\n"
        "  • Pull-quote block below — single editorial quote (text-xl md:text-2xl font-serif italic) + attribution. Carousel when 3+ quotes.\n"
        "  • NEVER absolute-positioned, rotated, or overlapping cards. Press is in-flow only.\n"
        "  • Section MUST be at least min-h-[480px] content-wise; fill all 3 blocks before any spacer."
    ),
    "story": (
        "STRUCTURAL FLOOR — narrative / about section minimum:\n"
        "  • Body copy uses `text-base md:text-lg text-muted-foreground leading-relaxed`, max-w-prose for readability.\n"
        "  • PATTERN — pick ONE from this catalog (driven by visual_dna.layout_signature + section_flavors.story):\n"
        "      A. ITALIC-ACCENT EDITORIAL — single-column centered editorial layout with an oversized serif headline that splits across 2-3 lines (text-5xl md:text-6xl lg:text-7xl font-serif leading-[1.05]). ONE WORD inside the headline is wrapped in `<span className=\"italic text-primary\">Word</span>` for the accent hit. Body 2-3 paragraphs in a single max-w-2xl column, with a small framed photo card to the right at md+ breakpoint (`md:absolute md:right-8 md:bottom-8 md:w-1/3 aspect-[4/5] rounded-2xl`). Lifestyle / hospitality / boutique / fine dining (the Bella Luna pattern).\n"
        "      B. TWO-PHOTO PULL-QUOTE — three-row layout: top row is one wide photo `aspect-[16/9] rounded-2xl`, middle row is a centered pull-quote `text-3xl md:text-4xl font-serif italic max-w-3xl mx-auto` with attribution below, bottom row is a 2-up photo grid (`grid grid-cols-2 gap-4 aspect-[16/9]`). Editorial / journalism / agency / cultural-institution.\n"
        "      C. SCROLL-SNAP CHAPTERS — horizontal scroll with `flex overflow-x-auto snap-x snap-mandatory` and 3-4 `min-w-[80vw] snap-start` panels, each panel being a chapter (year/title heading + 2 paragraphs + 1 photo). Best for brand-story / heritage / decade-long-history pages.\n"
        "      D. ASYMMETRIC TIMELINE — vertical narrative with year markers on the left (`text-sm uppercase tracking-widest text-primary`) and content on the right. Each entry: year → headline → 2 lines → optional photo. Connected by a thin `border-l-2 border-border` line, NEVER absolute-positioned overlapping cards. Heritage / brewery / law-firm / academic brands.\n"
        "      E. ASYMMETRIC SPLIT — classic two-column split (asymmetric is fine): copy column with eyebrow + h2 + multi-paragraph body + optional small CTA, image column with hero photo OR a stat panel OR a quote pull-out. Default fallback when none of A-D fits.\n"
        "    Default: A for hospitality/dining/boutique/lifestyle, B for editorial/cultural, C for heritage with decades of history, D for multi-decade timeline narratives, E only when others don't fit.\n"
        "  • Optional decorative element from visual_dna.decorative_motifs as a divider or accent (e.g. a thin SVG flourish between paragraphs).\n"
        "  • CRITICAL — for pattern D, NEVER absolute-position card chips overlapping each other. Cards stay in document flow. Overlapping timeline cards was a real bug in Laqod Tour."
    ),
    "process": (
        "STRUCTURAL FLOOR — process / how-it-works section minimum:\n"
        "  • Heading group above.\n"
        "  • Sequential steps (3-5 typical): horizontal stepper on lg+, vertical on mobile. Each step has numbered indicator + title + description.\n"
        "  • Optional connecting line behind the indicators (h-px bg-border, hidden on mobile).\n"
        "  • Numbered indicator style (circle, square, hand-drawn glyph) comes from visual_dna.decorative_motifs."
    ),
    "stats": (
        "STRUCTURAL FLOOR — big numbers band:\n"
        "  • 2-4 columns separated by `divide-x divide-border`.\n"
        "  • Each item: huge number (text-5xl sm:text-6xl font-bold text-primary, optional count-up on scroll), label below (uppercase tracking-widest text-muted-foreground).\n"
        "  • Optional small description per item (text-sm) when section.items[i].description exists."
    ),
    "faq": (
        "STRUCTURAL FLOOR — FAQ accordion:\n"
        "  • Heading group above (eyebrow + h2).\n"
        "  • Vertical accordion using <details>+<summary> OR useState. Each row: question (font-semibold), answer (text-muted-foreground) revealed on toggle.\n"
        "  • Plus icon rotates 45° on open. max-w-3xl mx-auto for readability."
    ),
    "pricing": (
        "STRUCTURAL FLOOR — pricing tiers:\n"
        "  • Heading group above + optional billing-period toggle (useState).\n"
        "  • 2-3 plan cards: each with plan name, big price (REAL `$NN` or `$NN/mo` numbers, NEVER \"Contact us\" / \"Free\" placeholders for paid plans), billing-period note, feature list (check icons), CTA.\n"
        "  • Highlight the recommended tier via `ring-2 ring-primary` + small 'Recommended' pill.\n"
        "  • Cards equalize heights; CTAs align via mt-auto."
    ),
    "cta": (
        "STRUCTURAL FLOOR — CTA band:\n"
        "  • Full-width band with `bg-primary text-primary-foreground`, generous padding (py-16 lg:py-24), centered or left-aligned.\n"
        "  • Giant headline (text-4xl md:text-5xl lg:text-6xl font-bold) + supporting line + primary CTA pill (`bg-background text-foreground`).\n"
        "  • Optional decorative motif from visual_dna in a corner accent position."
    ),
    "team": (
        "STRUCTURAL FLOOR — team grid:\n"
        "  • Heading group above.\n"
        "  • Grid of avatar cards (3-4 col): rounded portrait (rounded-full or rounded-2xl), name (font-semibold), role (text-sm muted), optional 1-line bio.\n"
        "  • Cards equalize heights, hover-lift."
    ),
    "locations": (
        "STRUCTURAL FLOOR — locations / addresses:\n"
        "  • Heading group above.\n"
        "  • Cards or rows per location: name (font-semibold), address, phone (tel:), hours table, optional map link.\n"
        "  • Optional embedded map or photo per location."
    ),
    "reservation": (
        "STRUCTURAL FLOOR — booking / reservation form:\n"
        "  • MUST use an actual `<form>` element wrapping the inputs (not a `<div>`).\n"
        "  • Submit button label MUST contain one of: \"Book\", \"Reserve\", or \"Schedule\"\n"
        "    (e.g. \"Book a Table\", \"Reserve Now\", \"Schedule Consultation\").\n"
        "    Quality-gate scans for these tokens — a button labeled \"Submit\" or\n"
        "    \"Send\" fails the booking-form check.\n"
        "  • Heading copy MUST also include book/reserve/schedule intent\n"
        "    (e.g. \"Reserve Your Table\", \"Book Your Stay\", \"Schedule a Visit\").\n"
        "  • Two-column layout: form (left or right), info panel with brand business_info (address/phone/hours).\n"
        "  • Form fields: name, email, phone, date (input type=date), party-size or quantity (input type=number), notes textarea, submit.\n"
        "  • Real validation (required + email regex) + success state on submit. Mark file 'use client'."
    ),
    "contact": (
        "STRUCTURAL FLOOR — contact form section:\n"
        "  • MUST use an actual `<form>` element (not a `<div>` masquerading as one).\n"
        "    Quality-gate scans for `<form>` tags — without one, the lead-gen\n"
        "    gate fails for lead-generation-purpose pages.\n"
        "  • Two-column: form (name, email, message textarea, submit) + info panel (address, phone, email, hours).\n"
        "  • For LEAD-GEN brands (agencies, consultancies, B2B services, professional\n"
        "    services), submit button label should reflect lead intent: \"Get a Quote\",\n"
        "    \"Request a Consultation\", \"Book a Call\", \"Contact Sales\". The generic\n"
        "    \"Send Message\" / \"Submit\" works for general contact but reads weaker.\n"
        "  • Real validation + success state. Mark file 'use client'."
    ),
    "newsletter": (
        "STRUCTURAL FLOOR — newsletter signup:\n"
        "  • Centered band: heading + 1-line description + inline email input + submit button.\n"
        "  • Real email validation + success state. Optional GDPR/privacy line below."
    ),
    # NOTE: "footer" intentionally NOT listed here. Footer fallback is
    # selected dynamically from _FOOTER_VARIANTS by `_pick_footer_variant`
    # so the shape varies with brand personality / category rather than
    # collapsing every project into the same minimalist row.
}


# ── Header fallback variants ─────────────────────────────────────────
# Same idea as footer variants: when Gemini omits visual_dna.header, do
# not collapse every project into the same sticky 3-part bar.
_HEADER_VARIANTS: dict[str, str] = {
    "solid-bar": (
        "STRUCTURAL FLOOR — site header (solid sticky bar):\n"
        "  • <header> is `sticky top-0 z-50 w-full border-b border-border bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/80`.\n"
        "  • Inner: `container mx-auto flex h-16 items-center justify-between px-4 sm:px-6 lg:px-8` — brand left, nav center (gap-8 text-sm, max 6 links), CTA right.\n"
        "  • Mobile drawer below the bar when hamburger toggled. ESC closes; click outside closes.\n"
        "  • Best for straightforward professional brands."
    ),
    "utility-split": (
        "STRUCTURAL FLOOR — site header (utility row + main nav):\n"
        "  • TWO rows on desktop. Top utility row: `h-9 border-b border-border bg-muted/40 text-xs` showing 1-2 business_info items (email/phone/location) from landing.brand.business_info and social links when present.\n"
        "  • Main row: `h-16 bg-background/95 backdrop-blur` with brand left, nav center, CTA right.\n"
        "  • Mobile collapses to one sticky row with brand, CTA, hamburger; utility details move inside the drawer.\n"
        "  • Best for schools, academies, clinics, service businesses, and local venues where contact/location matter."
    ),
    "centered-logo": (
        "STRUCTURAL FLOOR — site header (centered-logo editorial):\n"
        "  • Desktop row uses left nav group, centered wordmark, right nav/CTA group. Height `h-20`; background `bg-background/90 backdrop-blur` with a soft border.\n"
        "  • Wordmark uses heading font and one restrained motif accent. Nav is uppercase, small, evenly spaced.\n"
        "  • Mobile collapses to brand left + hamburger right.\n"
        "  • Best for refined, elegant, boutique, and ceremonial brands."
    ),
    "floating-pill": (
        "STRUCTURAL FLOOR — site header (floating pill):\n"
        "  • <header> is `fixed top-4 inset-x-0 z-50 px-4`. Inner shell is `container mx-auto flex h-14 items-center justify-between rounded-full border border-border bg-background/85 px-4 shadow-lg backdrop-blur-md`.\n"
        "  • Brand left, compact nav center, CTA right as solid primary pill. On scroll, increase opacity and shadow.\n"
        "  • Mobile drawer opens as a rounded panel below the floating shell.\n"
        "  • Best for high-energy, SaaS, creator, event, and conversion-heavy landing pages."
    ),
}


# ── Footer fallback variants ─────────────────────────────────────────
# When `visual_dna.section_anatomies.footer` is missing, we pick ONE of
# these patterns based on brand personality + category. This prevents the
# same 4-column megacolumn footer appearing on every generation. Each
# entry is a structural floor — visual_dna composes the actual look on
# top (decorative motifs, surface treatment, typography flavor).
_FOOTER_VARIANTS: dict[str, str] = {
    "minimalist-row": (
        "STRUCTURAL FLOOR — site footer (minimalist single row):\n"
        "  • `border-t border-border bg-background`.\n"
        "  • Inner: `container mx-auto flex flex-col gap-4 px-6 py-8 sm:flex-row sm:items-center sm:justify-between`.\n"
        "  • Left: brand monogram + © year. Center (sm+): inline links from landing.footer.links (text-xs uppercase tracking-widest). Right: 3-4 social icons.\n"
        "  • Optional single accent motif from visual_dna.decorative_motifs as the only flourish.\n"
        "  • NO multi-column grid; NO newsletter form; NO contact column. This footer says LESS on purpose."
    ),
    "mega-columns": (
        "STRUCTURAL FLOOR — site footer (4-column mega-columns):\n"
        "  • `border-t border-border bg-background` OR `bg-foreground text-background` if visual_dna.cultural_palette_emphasis suggests dark surface.\n"
        "  • Inner: `container mx-auto grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-10 px-6 py-14`.\n"
        "  • Column 1: brand mark + tagline + short description from landing.brand. 2-3 social icons below.\n"
        "  • Column 2: 'Explore' (or category-specific label) — links from landing.footer.links (text-sm).\n"
        "  • Column 3: 'Visit' — business_info: address (with MapPin icon), phone (tel:), email (mailto:), hours.\n"
        "  • Column 4: newsletter signup OR a single editorial pull-quote/tagline.\n"
        "  • Bottom strip: `border-t border-border/50 mt-12 pt-6 flex flex-col sm:flex-row items-center justify-between text-xs text-muted-foreground`. © year + brand on left; small links (Privacy / Terms) on right."
    ),
    "cta-band": (
        "STRUCTURAL FLOOR — site footer (CTA band + thin footer bar):\n"
        "  • TWO bands stacked.\n"
        "  • UPPER BAND: full-width `bg-primary text-primary-foreground` panel, py-16 lg:py-20. Centered: oversized invitation headline (text-3xl md:text-5xl font-bold, max-w-3xl), short supporting line, ONE primary CTA pill (use landing.ctas.primary). Optional decorative motif from visual_dna in a corner.\n"
        "  • LOWER BAND: thin minimalist bar `bg-background border-t border-border py-6`. Inner: `container mx-auto flex flex-col gap-3 px-6 sm:flex-row sm:items-center sm:justify-between`. Brand + © year left, social icon row right, small Privacy/Terms links if relevant.\n"
        "  • Suited to brands with assertive/high-energy personality — the footer is a final pitch, not a directory."
    ),
    "centered-stack": (
        "STRUCTURAL FLOOR — site footer (centered stack):\n"
        "  • `border-t border-border bg-background` (or a soft cream/muted surface if visual_dna calls for it).\n"
        "  • Inner: `container mx-auto flex flex-col items-center gap-6 px-6 py-16 text-center`.\n"
        "  • TOP: large brand mark / wordmark (font-serif text-3xl md:text-4xl, can carry a decorative motif from visual_dna as ornament above/below).\n"
        "  • MIDDLE: single line of inline nav links (gap-6 text-sm tracking-widest uppercase), then optional contact line (city · phone · email), then a row of 4-5 social icons.\n"
        "  • BOTTOM: small © year + tagline.\n"
        "  • Suited to refined/minimal/sophisticated personalities — a quiet, ceremonial close."
    ),
}


def _pick_footer_variant(brief: dict | None) -> str:
    """Deterministically pick a footer skeleton key based on brand context.

    The pick is stable per-brand (same brief → same variant) and skewed
    toward the variant that best fits the brand's personality + category.
    Falls back to minimalist-row when context is missing.
    """
    b = brief or {}
    pers = (b.get("personality") or {})
    energy = (pers.get("energy") or "").strip().lower()
    tone = (pers.get("tone") or "").strip().lower()
    vibe = " ".join(pers.get("vibe_keywords") or []).lower()
    category = (b.get("category") or "").strip().lower()
    purpose = ((b.get("_research") or {}).get("primary_purpose") or "").lower()
    brand = b.get("brand") or {}
    info = brand.get("business_info") or {}
    social = brand.get("social") or []
    sections = b.get("sections") or []
    info_count = sum(1 for k in ("address", "phone", "email", "hours", "city") if info.get(k))
    navish_section_count = len([
        s for s in sections
        if (s.get("type") or "").lower() not in {"hero", "footer", "cta", "cta_band", "newsletter"}
    ])

    # Heuristics — order matters; first match wins.
    # cta-band: assertive, high-energy, conversion-driven brands
    if energy == "high" or any(w in vibe for w in ("bold", "assertive", "energetic", "playful")) \
       or purpose in ("lead-gen", "signup", "convert"):
        return "cta-band"

    # centered-stack: refined / sophisticated / ceremonial brands
    if any(w in tone for w in ("refined", "elegant", "sophisticated", "minimal")) \
       or any(w in vibe for w in ("refined", "elegant", "minimal", "sophisticated", "quiet", "understated")):
        return "centered-stack"

    # mega-columns: content-heavy or contact-heavy brands. Lots of info to
    # surface in the footer. This intentionally catches education/language/
    # tutoring sites like LinguistFlow — otherwise they collapse to the same
    # minimalist row despite having address, phone, email, social, and many nav
    # targets.
    if (
        category in (
            "restaurant", "hotel", "resort", "hospitality", "spa", "retail",
            "ecommerce", "marketplace", "agency", "studio", "education",
            "school", "academy", "language", "tutoring", "course", "coaching",
            "clinic", "healthcare", "fitness", "wellness", "real estate",
            "nonprofit", "community",
        )
        or info_count >= 2
        or bool(social)
        or navish_section_count >= 5
    ):
        return "mega-columns"

    # minimalist-row: low-energy or info-light brands; safe default
    return "minimalist-row"


def _pick_header_variant(brief: dict | None) -> str:
    """Deterministically pick a fallback header skeleton by brand context."""
    b = brief or {}
    pers = (b.get("personality") or {})
    energy = (pers.get("energy") or "").strip().lower()
    tone = (pers.get("tone") or "").strip().lower()
    vibe = " ".join(pers.get("vibe_keywords") or []).lower()
    category = (b.get("category") or "").strip().lower()
    brand = b.get("brand") or {}
    info = brand.get("business_info") or {}
    info_count = sum(1 for k in ("address", "phone", "email", "hours", "city") if info.get(k))

    if energy == "high" or any(w in vibe for w in ("bold", "energetic", "playful", "launch")):
        return "floating-pill"
    if any(w in tone for w in ("refined", "elegant", "sophisticated", "minimal")) \
       or any(w in vibe for w in ("refined", "elegant", "minimal", "sophisticated", "quiet")):
        return "centered-logo"
    if category in (
        "education", "school", "academy", "language", "tutoring", "course",
        "coaching", "clinic", "healthcare", "real estate", "nonprofit",
    ) or info_count >= 2:
        return "utility-split"
    return "solid-bar"

# Aliases — section types that map to the same fallback. Aliases live here
# rather than in _FALLBACK_SKELETONS so the canonical list reads cleanly.
_TYPE_ALIASES: dict[str, str] = {
    "value_prop":       "features",
    "benefits":         "features",
    "how_it_works":     "process",
    "steps":            "process",
    "experience":       "gallery",
    "experiences":      "gallery",
    "events":           "gallery",
    "publications":     "press",
    "logos":            "press",
    "awards":           "press",
    "philosophy":       "story",
    "about":            "story",
    "marketing_header": "header",
    "navbar":           "header",
    "marketing_footer": "footer",
    "site_footer":      "footer",
    "booking_form":     "reservation",
    "contact_form":     "contact",
    "menu_highlights":  "menu",
    "featured_dishes":  "menu",
    "destinations":     "gallery",
    "rooms":            "gallery",
    "accommodations":   "gallery",
    "products":         "menu",
    "portfolio":        "gallery",
    "instagram_feed":   "gallery",
}


_GENERIC_FALLBACK = (
    "STRUCTURAL FLOOR — generic content section minimum:\n"
    "  • Heading group at top (eyebrow + h2 + optional 1-line subhead).\n"
    "  • Primary content block: list of items, grid, or single narrative paragraph — pick what fits visual_dna.layout_signature.\n"
    "  • At least one supporting block (CTA row, micro-stat triple, attribution row, info card) so the section feels complete.\n"
    "  • Decorative integration from visual_dna.decorative_motifs in accent positions only."
)


def _canonical_section_type(section_type: str) -> str:
    """Resolve a section type to its canonical key in _FALLBACK_SKELETONS."""
    t = (section_type or "").strip().lower()
    return _TYPE_ALIASES.get(t, t)


def _resolve_anatomy(
    section_type: str,
    visual_dna: dict | None,
    *,
    brief: dict | None = None,
) -> tuple[str, str]:
    """Return (anatomy_text, source) for a section.

    Priority:
      1. visual_dna.section_anatomies[section_type]   — research-grounded
      2. visual_dna.section_anatomies[canonical_type] — research via alias
      3a. footer: dynamic pick from _FOOTER_VARIANTS  — varies by brand
      3b. _FALLBACK_SKELETONS[canonical_type]         — minimal floor
      4. _GENERIC_FALLBACK                            — last resort

    Source is one of: "research", "research-aliased", "fallback",
    "fallback:<variant>" (footer only), or "generic".

    `brief` is optional and only consulted for variants that need brand
    context (currently the footer picker).
    """
    raw_type = (section_type or "").strip().lower()
    canonical = _canonical_section_type(raw_type)
    anatomies = ((visual_dna or {}).get("section_anatomies") or {})

    custom = anatomies.get(raw_type)
    if isinstance(custom, str) and len(custom.strip()) >= 40:
        return custom.strip(), "research"

    custom_alias = anatomies.get(canonical)
    if isinstance(custom_alias, str) and len(custom_alias.strip()) >= 40:
        return custom_alias.strip(), "research-aliased"

    if canonical == "header":
        variant = _pick_header_variant(brief)
        return _HEADER_VARIANTS[variant], f"fallback:{variant}"

    if canonical == "footer":
        variant = _pick_footer_variant(brief)
        return _FOOTER_VARIANTS[variant], f"fallback:{variant}"

    fallback = _FALLBACK_SKELETONS.get(canonical)
    if fallback:
        return fallback, "fallback"

    return _GENERIC_FALLBACK, "generic"


def _section_filename(section: dict[str, Any]) -> str:
    """`HeroSection.jsx` from {id: 'hero', type: 'hero'}.

    Uses the section's unique `id` (deduped upstream by _normalize_brief) so
    two sections with the same `type` (e.g. two "features" blocks) don't
    collide on the same filename. Falls back to type then "section".
    """
    raw = (section.get("id") or section.get("type") or "section").strip()
    parts = re.split(r"[\s_\-]+", raw)
    pascal = "".join(p.capitalize() for p in parts if p)
    if not pascal:
        pascal = "Section"
    if not pascal.endswith("Section"):
        pascal = f"{pascal}Section"
    return f"{pascal}.jsx"


def _component_name(filename: str) -> str:
    """Strip extension from filename to get React component name."""
    return filename.rsplit(".", 1)[0]


def _section_content_looks_valid(content: str, section_id: str, component_name: str) -> tuple[bool, str]:
    """Fast contract check before writing Claude output to disk.

    The build validator catches syntax errors later; this catches the more
    damaging class of "valid JSX that ignores the pipeline contract" before it
    ships: hardcoded copy, wrong section id, or no runtime landing.json import.
    """
    if not content.strip():
        return False, "empty content"
    if "@/content/landing.json" not in content:
        return False, "does not import runtime landing.json"
    if "landing.sections" not in content or ".find" not in content:
        return False, "does not select section from landing.sections"
    if section_id and section_id not in content:
        return False, "does not reference requested section id"
    if f"export default function {component_name}" not in content:
        return False, "missing matching default export"
    if 'href="#"' in content or "href='#'" in content:
        return False, "contains placeholder href"
    if "style={{ fontFamily" in content or "fontFamily:" in content:
        return False, "uses inline fontFamily"
    return True, "ok"


def _layout_content_looks_valid(content: str, kind: str, component_name: str) -> tuple[bool, str]:
    """Validate header/footer output still reads all runtime data from JSON."""
    if not content.strip():
        return False, "empty content"
    if "@/content/landing.json" not in content:
        return False, "does not import runtime landing.json"
    if f"export default function {component_name}" not in content:
        return False, "missing matching default export"
    if 'href="#"' in content or "href='#'" in content:
        return False, "contains placeholder href"
    if "style={{ fontFamily" in content or "fontFamily:" in content:
        return False, "uses inline fontFamily"
    if kind == "header" and "landing.nav" not in content:
        return False, "header does not read landing.nav"
    if kind == "footer" and "landing.footer" not in content:
        return False, "footer does not read landing.footer"
    if "landing.brand" not in content:
        return False, "does not read landing.brand"
    return True, "ok"


def _fallback_section_component(section: dict[str, Any], component_name: str, file_path: str) -> dict[str, Any]:
    """Deterministic, data-driven section fallback.

    Used only when Claude fails or violates the runtime JSON contract. The
    fallback keeps every nav anchor live, renders the brief's real content, and
    includes functional forms for form-like sections so the generated app stays
    usable instead of silently dropping a page segment.
    """
    section_id = (section.get("id") or section.get("type") or "section").strip()
    section_type = (section.get("type") or "").strip().lower()
    needs_form = section_type in {"contact", "contact_form", "reservation", "booking_form", "newsletter"}
    form_jsx = """
      <form onSubmit={handleSubmit} className="mt-8 grid gap-4 rounded-2xl border border-border bg-background p-5 shadow-sm">
        <div className="grid gap-4 sm:grid-cols-2">
          <label className="grid gap-2 text-sm font-medium text-foreground">
            Name
            <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required className="rounded-md border border-border bg-background px-3 py-2 outline-none ring-primary/20 focus:ring-4" />
          </label>
          <label className="grid gap-2 text-sm font-medium text-foreground">
            Email
            <input type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} required className="rounded-md border border-border bg-background px-3 py-2 outline-none ring-primary/20 focus:ring-4" />
          </label>
        </div>
        <label className="grid gap-2 text-sm font-medium text-foreground">
          Message
          <textarea value={form.message} onChange={(e) => setForm({ ...form, message: e.target.value })} rows={4} className="rounded-md border border-border bg-background px-3 py-2 outline-none ring-primary/20 focus:ring-4" />
        </label>
        <button type="submit" className="inline-flex items-center justify-center rounded-full bg-primary px-6 py-3 text-sm font-semibold text-primary-foreground transition hover:opacity-90">
          {submitted ? "Sent" : (section.cta?.label || landing.ctas?.primary?.label || "Send")}
        </button>
      </form>""" if needs_form else ""

    content = f'''{"'use client';" if needs_form else ""}
{"import { useState } from \"react\";" if needs_form else ""}
import Image from "next/image";
import Link from "next/link";
import landing from "@/content/landing.json";

export default function {component_name}() {{
  const section = landing.sections.find((s) => s.id === "{section_id}");
  {"const [form, setForm] = useState({ name: \"\", email: \"\", message: \"\" });" if needs_form else ""}
  {"const [submitted, setSubmitted] = useState(false);" if needs_form else ""}
  {"const handleSubmit = (event) => { event.preventDefault(); setSubmitted(true); };" if needs_form else ""}

  if (!section) return null;

  const items = section.items || [];
  const image = section.images?.find(Boolean);
  const cta = section.cta || landing.ctas?.primary;

  return (
    <section id="{section_id}" className="bg-background py-20 md:py-28 lg:py-32">
      <div className="container mx-auto grid max-w-7xl gap-10 px-4 sm:px-6 lg:grid-cols-[0.95fr_1.05fr] lg:items-center lg:px-8">
        <div className="max-w-2xl">
          {{section.nav_label && (
            <p className="mb-3 text-xs font-semibold uppercase tracking-[0.2em] text-primary">{{section.nav_label}}</p>
          )}}
          <h2 className="font-[family-name:var(--font-heading)] text-3xl font-bold tracking-tight text-foreground sm:text-4xl lg:text-5xl">
            {{section.headline}}
          </h2>
          {{section.subheadline && (
            <p className="mt-4 text-lg leading-relaxed text-muted-foreground md:text-xl">{{section.subheadline}}</p>
          )}}
          {{section.body && (
            <p className="mt-5 text-base leading-relaxed text-muted-foreground md:text-lg">{{section.body}}</p>
          )}}
          {{cta?.href && (
            <Link href={{cta.href}} className="mt-8 inline-flex items-center justify-center rounded-full bg-primary px-6 py-3 text-sm font-semibold text-primary-foreground transition hover:opacity-90">
              {{cta.label || "Explore"}}
            </Link>
          )}}
          {form_jsx}
        </div>
        <div className="grid gap-4">
          {{image ? (
            <div className="relative aspect-[4/3] overflow-hidden rounded-3xl border border-border bg-muted shadow-lg">
              <Image src={{image}} alt={{section.image_alts?.[0] || section.headline || landing.brand.name}} fill sizes="(min-width: 1024px) 48vw, 100vw" className="object-cover" />
              <div className="absolute inset-0 bg-gradient-to-t from-foreground/35 to-transparent" />
            </div>
          ) : (
            <div className="relative aspect-[4/3] overflow-hidden rounded-3xl border border-border bg-gradient-to-br from-primary/15 via-accent/10 to-muted p-8">
              <div className="absolute -right-10 -top-10 h-40 w-40 rounded-full bg-primary/10" />
              <p className="relative max-w-sm font-[family-name:var(--font-heading)] text-4xl font-bold text-foreground">{{landing.brand.name}}</p>
            </div>
          )}}
          {{items.length > 0 && (
            <div className="grid gap-4 sm:grid-cols-2">
              {{items.slice(0, 4).map((item, index) => (
                <article key={{item.title || item.label || index}} className="h-full rounded-2xl border border-border bg-card p-5 shadow-sm">
                  <p className="text-sm font-semibold uppercase tracking-[0.16em] text-primary">{{item.label || item.value || `0${{index + 1}}`}}</p>
                  <h3 className="mt-3 text-lg font-semibold text-foreground">{{item.title || item.name}}</h3>
                  {{item.description && <p className="mt-2 text-sm leading-relaxed text-muted-foreground">{{item.description}}</p>}}
                </article>
              ))}}
            </div>
          )}}
        </div>
      </div>
    </section>
  );
}}
'''
    return {"path": file_path, "content": content, "fallback": True}


# Per-slot palette table — shows each slot's HSL, the Tailwind class that
# maps to it in globals.css, and the slot's intended role. Replaces the
# previous opaque "--primary: 30 35% 45%;" dump, which Claude tended to
# ignore in favor of bg-background / text-foreground everywhere.
_PALETTE_SLOT_META = {
    "primary":    ("bg-primary text-primary-foreground",   "brand color — use as a full surface on ≥1 band/CTA per page"),
    "secondary":  ("bg-secondary text-secondary-foreground","supporting tone — chip / badge backgrounds, secondary surfaces"),
    "accent":     ("bg-accent text-accent-foreground",      "pop color — small flourishes, highlights, badge underlays"),
    "background": ("bg-background text-foreground",         "page surface — neutral default; do NOT use everywhere"),
    "foreground": ("text-foreground",                       "primary text on light surfaces; flip to bg-foreground for dark bands"),
    "muted":      ("bg-muted text-muted-foreground",        "subtle alternating section background; secondary text"),
    "border":     ("border-border",                         "card / divider hairlines"),
    "card":       ("bg-card text-card-foreground",          "card and panel surfaces — must contrast with section bg"),
}


def _format_palette_table(palette: dict) -> str:
    rows: list[str] = []
    for slot in ("primary", "secondary", "accent", "background", "foreground", "muted", "border", "card"):
        hsl = (palette.get(slot) or "").strip()
        if not hsl:
            continue
        classes, role = _PALETTE_SLOT_META[slot]
        rows.append(f"  --{slot:<10} hsl({hsl:<14}) → {classes:<48}  ({role})")
    return "\n".join(rows) if rows else "  (no palette — Tailwind defaults only)"


def _system_prompt(
    brand_name: str,
    motif: str,
    palette: dict,
    typography: dict,
    design_system: dict,
    *,
    personality: dict | None = None,
    references: list[dict] | None = None,
    design_tokens: dict | None = None,
    visual_dna: dict | None = None,
) -> str:
    """Per-call system prompt — small, focused, no rules unrelated to a single section."""
    palette_lines = _format_palette_table(palette)
    ds = design_system or {}
    dt = design_tokens or {}
    pers = personality or {}
    vd = visual_dna or {}
    pers_block = ""
    if pers:
        vibe = ", ".join(pers.get("vibe_keywords") or [])
        pers_block = (
            f"\nPERSONALITY (tune copy tone, color usage, and motion intensity to match):\n"
            f"  Tone: {pers.get('tone', 'confident')}\n"
            f"  Vibe keywords: {vibe or 'modern, clear'}\n"
            f"  Energy: {pers.get('energy', 'medium')}\n"
        )

    # ── VISUAL DNA — the lead design directive ─────────────────────────
    # Rich, concrete cultural cues distilled from grounded design research.
    # When present, these are the PRIMARY visual instruction — the abstract
    # motion/accent_shape/surface enums below are still authoritative for
    # tokens (radius, padding, transition) but the LOOK and FEEL of the
    # section is driven by visual_dna. When absent (research failed), the
    # codegen falls back to the legacy enums-only path and produces the
    # generic "modern luxe" output.
    visual_dna_block = ""
    if vd:
        intensity = (vd.get("cultural_intensity") or "bold").strip().lower()
        motifs = vd.get("decorative_motifs") or []
        textures = vd.get("signature_textures") or []
        icons = vd.get("iconography_anchors") or []
        photo = (vd.get("photography_style") or "").strip()
        layout_sig = (vd.get("layout_signature") or "").strip()
        palette_emph = (vd.get("cultural_palette_emphasis") or "").strip()
        type_voice = (vd.get("typography_voice") or "").strip()
        flavors = vd.get("section_flavors") or {}

        intensity_note = (
            "Cultural cues sit in ACCENT POSITIONS only (a small motif under headlines, "
            "an iconography anchor next to CTAs, a single textural detail). The page reads "
            "modern-upscale with cultural FLAVOR — restraint stays."
            if intensity == "subtle"
            else "Cultural cues take a STRONGER role (larger textures over hero photos, "
                 "decorative motifs as section dividers, iconography woven into headings, "
                 "full-bleed cultural patterns where appropriate). The page reads UNMISTAKABLY "
                 "of this category and culture."
        )

        parts = [
            "\nVISUAL DNA — THIS IS THE PRIMARY VISUAL DIRECTIVE FOR THIS BRAND.",
            "Concrete, culturally-specific visual cues distilled from real grounded research.",
            "Your section MUST visibly reflect VISUAL_DNA in ≥3 distinct ways (color emphasis, "
            "decorative motif, iconography accent, photography style, typography voice, OR layout "
            "signature). A generic 'modern sans + photos + 3-col grid' output that could fit any "
            "brand in this category is a FAILURE.",
            f"\nCultural intensity: {intensity}",
            f"  → {intensity_note}",
        ]
        if palette_emph:
            parts.append(f"\nCultural palette emphasis:\n  {palette_emph}")
        if type_voice:
            parts.append(f"\nTypography voice:\n  {type_voice}")
        if photo:
            parts.append(f"\nPhotography style:\n  {photo}")
        if layout_sig:
            parts.append(f"\nLayout signature:\n  {layout_sig}")
        if motifs:
            parts.append("\nDecorative motifs (USE these as accents — pick ones that fit this section):")
            for m in motifs[:6]:
                parts.append(f"  • {m}")
        if textures:
            parts.append("\nSignature textures (use AT MOST one per section — don't stack):")
            for t in textures[:3]:
                parts.append(f"  • {t}")
        if icons:
            parts.append(
                "\nIconography anchors (when section calls for icons, prefer Lucide icons that "
                "evoke these — not generic Zap/Star/Check; pick lucide names that match these ideas):"
            )
            for ic in icons[:6]:
                parts.append(f"  • {ic}")
        if flavors:
            parts.append("\nPer-section visual flavor (apply when generating that section type):")
            for stype, note in flavors.items():
                if note and isinstance(note, str):
                    parts.append(f"  • {stype}: {note}")
        parts.append(
            "\nAPPLY VISUAL DNA HOLISTICALLY — don't slap a single decorative motif on a generic "
            "skeleton and call it done. The cumulative effect of color emphasis + iconography + "
            "photography + layout signature should make this brand feel UNMISTAKABLY of its "
            "category and culture, not Stripe-with-a-different-logo."
        )
        visual_dna_block = "\n".join(parts) + "\n"

    ref_block = ""
    if references:
        ref_lines = []
        for r in references[:4]:
            name = r.get("name") or r.get("url", "")
            why = r.get("why") or ""
            features = ", ".join((r.get("notable_features") or [])[:3])
            ref_lines.append(f"  • {name} — {why}{(' Features: ' + features) if features else ''}")
        if ref_lines:
            ref_block = (
                "\nREFERENCE SITES (research-grounded — these REAL sites informed this brief; emulate their patterns):\n"
                + "\n".join(ref_lines) + "\n"
            )

    return f"""You are a senior front-end engineer writing ONE attention-grabbing section component for a Next.js landing page. The bar is: real motion, real images, real visual interest, real INTERACTIVITY — never a static text block.

OUTPUT — ONE file via the write_project_files tool:
  • path: src/components/sections/<ComponentName>.jsx (provided in the user message)
  • content: the full source for that component, ready to import

MANDATORY RULES
0. STRING LITERALS — when you write English copy as a JS string (object property, array element,
   variable initializer), use **double quotes** ALWAYS. English copy is full of apostrophes
   ("chef's", "evening's", "don't") which silently terminate single-quoted literals and break
   `next build` with "Unexpected token". Examples:
     ✗ detail: 'Reserved exclusively for the chef's tasting menu.'
     ✓ detail: "Reserved exclusively for the chef's tasting menu."
   The ONLY single-quoted strings allowed are: `'use client'`, import paths (`from '@/...'`),
   and CSS-class strings inside className (where there are no apostrophes). Default to `"..."` for
   all human copy, no exceptions.
1. The component reads ALL content from `@/content/landing.json` — never hardcode copy OR data arrays.
   Pattern:
     import landing from "@/content/landing.json";
     const section = landing.sections.find((s) => s.id === "<section-id>");
     // then render section.headline, section.subheadline, section.items, section.cta, section.images, etc.

   ✗ FORBIDDEN — hardcoded data constants at the top of the file:
       const CAFE_LOCATIONS = [{{ id: 1, name: "Alpine & Bean — Niederdorf", ... }}, ...];
       const MENU_ITEMS = [...];
       const FAQ_QUESTIONS = [...];
       const TESTIMONIALS = [...];
       const TABS = ["Coffee", "Pastries", "Tea"];
       const FLAVOR_FILTERS = ["All", "Chocolate", ...];

   If your section needs an array to render (cards, filter chips, tabs, search results,
   carousel items, FAQ list), the array MUST come from `section.items` in landing.json.
   When `section.items` is empty or missing, render a CALM STATIC LAYOUT (eyebrow +
   headline + subhead + CTA) — do NOT fabricate data to satisfy an interactive widget.
   Interactive widgets (search, filter, tabs) only render WHEN `section.items.length > 0`.
   Fabricated data feels generic and breaks user editability ("our cafe in Niederdorf"
   applies to nobody's actual business).
1.5 VISUAL DNA IS THE LEAD DESIGN DIRECTIVE. The DESIGN CONTEXT block below carries a VISUAL DNA
    section with concrete, culturally-specific cues (decorative motifs, signature textures,
    iconography anchors, photography style, layout signature, palette emphasis, typography
    voice, per-section flavors). YOUR SECTION MUST VISIBLY REFLECT VISUAL_DNA IN ≥3 DISTINCT
    WAYS. Examples of "visibly reflecting":
      • Adopt the cultural_palette_emphasis on a hero gradient or accent surface.
      • Use one decorative_motif as a divider, eyebrow ornament, or hero accent.
      • Pick lucide-react icons that match an iconography_anchor (lantern → 'Lamp', tea cup
        → 'Coffee', olive branch → 'Leaf') instead of generic Zap/Star.
      • Apply the photography_style mood as the photo subject + lighting choice in alt text
        and image_treatment intent.
      • Write headings in the typography_voice register (calligraphy-like serif for Chinese,
        rustic warm serif for Italian, etc.) — match the heading_font's cultural grain.
      • Echo the layout_signature in the section's overall composition.
    A section that could fit any business in this category — generic SaaS-flavored skeleton
    with the brand name swapped in — is a FAILURE. The cumulative cultural specificity should
    make this brand feel UNMISTAKABLY of its category and culture.
    If VISUAL DNA is not present in DESIGN CONTEXT below, fall back to the abstract enums
    (motif / accent_shape / surface) — but keep the same goal: avoid generic SaaS output.
2. Use TAILWIND CLASSES for styling — never inline `style={{...}}` for colors, fonts, or spacing.
   For FONTS specifically: NEVER write `style={{ fontFamily: ... }}`. Headlines / display
   text use `font-[family-name:var(--font-heading)]` (Tailwind arbitrary-property class);
   body text inherits from `<body>` automatically and needs no declaration. Inlining a
   hardcoded family name paints UNDER the next/font CSS variable and produces a visible
   doubled-text artifact (regular + serif stacked).
   For LAYOUT COLORS (page background, section surfaces, headings, body copy, borders,
   the primary→accent gradient set), USE SEMANTIC TOKENS so the palette can change
   between generations: bg-primary, text-foreground, bg-muted, border-border, bg-card,
   text-muted-foreground, plus opacity variants (text-foreground/80, bg-primary/50)
   and gradients across them (from-primary via-accent to-background).
   For STATUS COLORS (form success/error, validation hints), Tailwind named palettes
   are fine: text-green-600, text-red-600, text-amber-600.
   For BRAND-FIXED visuals (a logo SVG fill that must match the company's exact brand
   color, a partner badge, a flag icon), hex/rgb is fine.
   AVOID hex/rgb or Tailwind palette numbers (bg-blue-500, bg-[#2563eb]) for the main
   layout colors — those lock the page to one look and stop tracking the brief's
   palette. The post-gen pipeline logs warnings when it sees layout-color drift; treat
   it as a signal, not a hard error.
3. Render the section to match the provided layout_hint EXACTLY. The layout_hint vocab
   maps to concrete JSX skeletons — pick the one that matches and use it as the structural
   floor. Do NOT collapse `grid-3` to a centered-stack just because items are short, do NOT
   render `split-image-left` as a centered photo, do NOT render `two-column` as a 3-up.
   Vocab → skeleton (the OUTER container; inner card chrome still uses design tokens):
     • centered-stack       → `flex flex-col items-center text-center max-w-3xl mx-auto gap-6`
     • two-column           → `grid lg:grid-cols-2 gap-10 items-center` (text left, content right)
     • split-image-left     → `grid lg:grid-cols-2 gap-10 items-center` — `<Image>` IS the left column
     • split-image-right    → `grid lg:grid-cols-2 gap-10 items-center` — `<Image>` IS the right column
     • grid-2               → `grid sm:grid-cols-2 gap-6 lg:gap-8`
     • grid-3               → `grid sm:grid-cols-2 lg:grid-cols-3 gap-6`
     • grid-4               → `grid sm:grid-cols-2 lg:grid-cols-4 gap-6`
     • carousel             → horizontal `flex overflow-x-auto snap-x snap-mandatory gap-4` with `snap-center` children
     • accordion            → vertical list of `<details>` or controlled `useState` rows with chevron icon
     • logo-strip           → `flex flex-wrap items-center justify-center gap-x-12 gap-y-6`
     • stat-band            → `grid grid-cols-2 md:grid-cols-4 divide-x divide-border` with HUGE numerals
     • timeline             → vertical line with alternating-side cards (use `_NO_PHOTO_VARIANTS` style)
     • comparison-table     → real `<table>` with `<thead>` row of plan names + feature rows
     • media-quote          → `grid lg:grid-cols-[1.2fr_1fr] gap-12 items-center` with pull-quote left, image right
   If layout_hint is missing/empty, use centered-stack for hero/CTA, grid-3 for value_prop/features,
   split-image-right for story, asymmetric-12col bento for gallery — but ALWAYS pick deliberately.
3a. BRIEF CONTENT IS AUTHORITATIVE — DO NOT INVENT WHEN THE BRIEF HAS IT.
    The SECTION SPEC in the user prompt contains the Gemini-written brief for this
    specific brand. When a brief field is non-empty, you MUST use it verbatim. Inventing
    your own copy when the brief already wrote it is the documented root cause of
    "every generated site reads the same." Specifically:
      • If `section.headline` is non-empty, render it verbatim as the section's H1/H2.
        Do NOT paraphrase. Do NOT swap it for a brand-name slogan. Do NOT skip it.
      • If `section.subheadline` is non-empty, render it as the immediate supporting line.
      • If `section.body` is non-empty, render it as the narrative paragraph (preserve
        `\\n\\n` paragraph breaks).
      • If `section.items` is a non-empty array, render EXACTLY `section.items.length`
        items — no fewer, no more. Each item's `title`/`body`/`name`/`role`/`quote`/`value`/
        `price`/`icon` field, when present, is the copy for that slot. Do NOT invent extra
        items to fill a grid. Do NOT drop items because they don't fit your skeleton —
        change the skeleton.
      • If `section.image_queries` is a non-empty array, the binder has pre-bound matching
        URLs into `section.images[i]`. Use `section.images[i]` as the `<Image src>` —
        NEVER invent `/images/...` paths, NEVER hardcode Unsplash URLs, NEVER skip the
        image. The alt text comes from `section.image_alts[i]` (fall back to the i-th
        image_query when absent).
      • If `section.primary_cta` / `section.cta` carries a label, render it verbatim on
        the button. Use `landing.ctas.primary` only as a fallback when the section's
        CTA is absent (see the CTA DATA SHAPE rule below).
    Brief content trumps your priors. If the brief says headline "Freight that moves on
    your schedule" you write that — not "Transform Your Logistics Today" or any other
    generic headline you might prefer.
3b. NEVER USE PLACEHOLDER NAMES OR LOREM IPSUM. If the brief gives you a name (testimonial
    quote attribution, team member), use it. If the brief field is empty AND the section
    requires a name slot, derive one that sounds like the brand's audience (e.g. for
    Brooklyn restaurant: "Maya R., Park Slope"; for trucking: "Frank G., Owner-Operator,
    Texarkana") — NEVER "John Doe", "Jane Smith", or "Customer Name".
4. The component MUST default-export a React function whose name matches the file name
   (e.g. HeroSection.jsx → export default function HeroSection()).
5. The outermost element MUST be `<section id="<section-id>" className="...">` so anchor links work.
6. Use lucide-react icons ONLY when section.items[i].icon is set. Map the string to an import,
   e.g. `import {{ Zap, ShieldCheck }} from "lucide-react"` and resolve via a small map.
7. Mark files that use React hooks, onClick, or `<Reveal>` with `'use client';` as the first line.
8. NO CSS modules, NO styled-components, NO dynamic Tailwind class strings via template literals
   that Tailwind can't parse. All Tailwind classes must be statically present in the JSX.
9. NEVER render body / subheadline / description prose with INLINE PILL CHIPS embedded between
   words. If `section.subheadline` or `section.body` contains text, render it as a single
   continuous `<p>` — NEVER split it into a row of `inline-flex rounded-full px-3 py-1` pill
   spans interleaved with plain words. The pill-in-paragraph pattern looks like un-filled
   template placeholders ("Featured in [pill]culinary publications[/pill] and praised for
   [pill]authentic tapas[/pill]") and is FORBIDDEN. Pills are reserved for:
     • Eyebrow tags above a headline (single short label, ≤3 words).
     • Filter/category buttons in interactive controls (real onClick handlers).
     • Status / "new" badges sitting on top of a card image.
   If you need to highlight a phrase mid-paragraph, use `<span className="text-primary font-semibold">`
   inline — no rounded pill background, no padding box.
10. NEVER detect words inside section.subheadline/body and wrap them in pill spans based on a
    word list, regex, or `.replace()`. Render the string verbatim.
11. NEVER render `{{` or `}}` characters as visible UI. If you see curly-brace placeholder
    syntax inside a copy string (e.g. `"Featured in {{culinary publications}}"`), strip the
    braces and render the inner text inline as a normal `<span className="text-primary
    font-semibold">culinary publications</span>` — NEVER as a rounded pill chip. Treat
    curly-brace text as emphasis, not as form fields.
12. BORDER-RADIUS IS MANDATORY on every clickable affordance and card-like surface.
    The design system defines `--radius` (mapped to Tailwind `rounded-lg`). NEVER ship sharp
    90° corners on these elements:
      • <button>, <Button>, <Link asButton>, CTA links → `rounded-md` MINIMUM
        (use `rounded-lg`, `rounded-xl`, or `rounded-full` for pill-shaped CTAs)
      • <input>, <textarea>, <select>, search bars → `rounded-md` or `rounded-lg`
      • Cards, surfaces, and stat tiles (`bg-card border` containers) → `rounded-2xl` standard,
        `rounded-3xl` for hero/feature blocks
      • Avatars, brand-mark badges, status dots → `rounded-full`
      • Image containers, photo tiles → `rounded-xl` or `rounded-2xl`
    NEVER use `rounded-none` unless the brief explicitly says "brutalist" or "sharp" motif.
    If you write a card, button, or input WITHOUT a `rounded-*` class, you have produced
    a defect — the design system relies on radius for visual identity.
13. JSX SIBLINGS INSIDE ANY EXPRESSION MUST BE WRAPPED. JSX expressions can return
    exactly ONE element. The same rule applies to ternaries, `&&` short-circuits,
    `.map()` callbacks, IIFEs, and any function returning JSX. Multiple siblings
    inside `( ... )` produce a SWC syntax error ("Expected ',', got '<...'").
    Always wrap multiple siblings in a fragment or div:
      ✗  {{hasImage ? ( <Image .../> <div className="overlay" /> ) : ( <Fallback /> )}}
      ✓  {{hasImage ? ( <> <Image .../> <div className="overlay" /> </> ) : ( <Fallback /> )}}
      ✓  {{hasImage ? ( <div className="relative"><Image .../><div className="overlay"/></div> ) : ...}}
      ✗  {{hasImage && ( <Image .../> <div className="overlay" /> )}}
      ✓  {{hasImage && ( <> <Image .../> <div className="overlay" /> </> )}}
    Same rule for `.map()` callbacks: `items.map(x => <><Title/><Body/></>)` not
    `items.map(x => <Title/> <Body/>)`. The outer element of a map iteration must carry
    `key={{...}}` — fragments accept it via `<Fragment key={{...}}>` or a wrapping <div>.
14. CARD GRIDS MUST WRAP — NEVER COMPRESS CARDS INTO A SINGLE NON-WRAPPING ROW.
    Cards squeezed below their natural content width get clipped letters ("V T" instead of
    "Walking Tours"). To prevent this:
      • PREFER CSS Grid with explicit responsive breakpoints:
        `grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-6`
        — guarantees cards wrap to new rows at every viewport size.
      • OR use auto-fit for truly fluid grids:
        `grid grid-cols-[repeat(auto-fit,minmax(240px,1fr))] gap-6`
        — each card is AT LEAST 240px wide, expands to fill, wraps when needed.
      • IF you use flex for cards, you MUST add `flex-wrap` AND `min-w-[200px]` on each card.
        Never `flex` without `flex-wrap` for card collections.
      • NEVER apply `overflow-hidden` to the CARD TEXT CONTAINER (the div holding title +
        description). Overflow-hidden is allowed ONLY on image containers and decorative blobs.
      • NEVER apply `whitespace-nowrap` to card titles or descriptions — let multi-word titles
        wrap to 2 lines instead of getting clipped.
      • NEVER apply a fixed `w-24` / `w-32` / `w-40` / `w-48` to cards unless the design
        signature is icon-only chips (single ≤2-word label). Real cards (title + description
        + icon) need at least `w-full` inside a grid cell OR `min-w-[200px]` in a flex row.
      • For intentional text truncation (long descriptions in a fixed-height card), use
        `line-clamp-2` or `line-clamp-3` on the description block ONLY — never on the title.

ANIMATION (REQUIRED — every section MUST animate on scroll)
  • Import the Reveal component:  `import Reveal from "@/components/ui/Reveal";`
  • Wrap the eyebrow / headline / subheadline group in `<Reveal variant="fade-up">`.
  • Stagger card / list items with increasing `delay`: 0, 80, 160, 240ms (use the index).
  • Allowed variants: fade-up | fade-down | fade-left | fade-right | fade | scale.
  • Hover transitions on cards / buttons / images: `transition-all duration-300 hover:-translate-y-1 hover:shadow-lg` (calibrate to design_system.motion).

INTERACTIVITY (REQUIRED — the section must DO something, not just sit there)
  • The user prompt will tell you the exact interactive behavior under "INTERACTIVITY". Implement it FOR REAL with React state — NEVER stub it as a non-functional class.
  • Patterns by section type (use these unless the brief says otherwise):
      menu     → clickable item cards open a modal/drawer with full description, photo, and details. Use useState for open state. Backdrop closes. Esc closes.
      gallery  → click any tile opens a lightbox overlay (full-image, prev/next arrows, Esc to close, arrow-key nav). useState for the open index.
      faq      → accordion. Use useState OR `<details>`. Smooth height transition on open.
      testimonials → carousel/marquee with autoplay; manual prev/next arrows; pause on hover. useState for active index, useEffect for autoplay timer.
      pricing  → toggle pill switches between monthly / yearly prices (live). useState boolean.
      features → cards have hover lift + reveal a secondary detail line on hover (or use the item.details field if present).
      stats    → numbers count up on scroll into view. useEffect + IntersectionObserver + requestAnimationFrame.
      contact_form / reservation / booking_form / newsletter → REAL form with controlled inputs (useState), client-side validation (required fields, email regex), and a success state shown after submit (does NOT need a backend; show a thank-you panel + reset).
      cta / cta_band → primary button MUST have a real href that smooth-scrolls to a valid section anchor on the page (or a tel:/mailto: link from landing.brand.business_info).
  • Every <Link> / <a> / <button> MUST have a real, working target — never `href="#"` placeholders.
  • Mark the file `'use client';` whenever you use useState/useEffect/onClick/onSubmit. (Required for these patterns.)
  • Keyboard accessibility: modals/drawers/lightboxes close on Esc; carousels respond to arrow keys; focus is trapped inside open modals (use a simple ref + focus on mount).
  • ARIA: aria-label on icon-only buttons, aria-expanded on toggles, role="dialog" + aria-modal on overlays.

IMAGES — PHOTOS BEAT ICONS
  • HARD RULE: when items represent VISUAL/PHYSICAL things (destinations, hotels, dishes, products, properties, rooms, people, events, packages),
    you MUST render each card with a real photo as the primary visual — NOT a Lucide icon-in-rounded-square.
    Use `section.images[i]` (URL string, parallel-indexed with `section.items[i]`) as a `<Image fill>` background of the card.
    Aspect ratios: `aspect-[4/5]` for portraits, `aspect-square` or `aspect-[3/4]` for grids — vary across cards for editorial feel.
    The Lucide icon (if item.icon present) becomes a SMALL accent inside a glass chip on the photo, NOT the centerpiece.
    Only fall back to icon-only cards for ABSTRACT items (perks, benefits, capabilities, plans).

  • PHOTO MISSING? If `section.images[i]` is falsy for a card that *should* have a photo (destinations, dishes, etc.),
    render a soft gradient placeholder + the headline word over it — `bg-gradient-to-br from-muted to-muted/40 relative`
    with the city/dish name as a large serif overlay. NEVER fall back to a generic icon-square card; that erases the section's visual identity.
    CRITICAL: Always GUARD the <Image> render with a JSX truthy check on the URL. NEVER render an
    <Image> tag when the URL is an empty string — Next/Image produces a broken-image icon AND the
    alt text shows in the corner. Pattern (treat the section.images[i] truthy check as REQUIRED):
      const img = section.images?.[i] || "";
      <div className="relative aspect-[4/5] overflow-hidden rounded-2xl bg-muted">
        IF img is truthy → render <Image src=img alt=item.title fill className="object-cover" />
        ELSE             → render gradient placeholder div with item.title overlaid as serif text
    This guarantees the alt text is INSIDE the image element when the image loads and never leaks
    out as visible UI when the image fails.

  • SECTION TYPES THAT ARE INHERENTLY PHOTO-LED (always use photos when images exist):
      gallery, menu, destinations, hotels, properties, rooms, products, team, press,
      featured_listings, popular_destinations, guest_favorites (cards), portfolio.

  • IMAGE SHAPE — section.images is an ARRAY OF URL STRINGS (not objects).
    Each entry is just a fully-qualified Unsplash https:// URL.
    READ it directly: `const heroImg = section.images?.[0];` then `<Image src={{heroImg}} ... />`.
    For alt text, optionally read `section.image_alts?.[i]` (parallel array) — fallback to a hardcoded English string like the section's headline summary.

  • FORBIDDEN — hardcoded image URL constants. NEVER define a local `const UNSPLASH_IMAGES = {{...}}`
    or `const IMAGE_MAP = {{...}}` or any dict mapping item titles/keys to Unsplash URLs in your
    component file. The pipeline guarantees `section.images[i]` (parallel-indexed with
    `section.items[i]`) is already populated in landing.json by the time your component runs.
    If `section.images[i]` is empty/falsy, render the gradient placeholder described above —
    NEVER guess a URL from training data. Hardcoded URLs go stale, return 404, and bypass the
    binder's domain whitelist.
      ✗  const UNSPLASH_IMAGES = {{ peking_duck: "https://images.unsplash.com/photo-...", ... }};
      ✗  const HERO_IMG = "https://images.unsplash.com/photo-...";
      ✓  const heroImg = section.images?.[0];   // empty string → render placeholder
  • Use Next.js `<Image>` from "next/image" with `fill` + `sizes` for hero/large blocks, or fixed `width`/`height` for thumbnails.
  • Wrap each <Image> in a `relative overflow-hidden` container with the design_system.accent_shape rounding.
  • Apply design_system.image_treatment:
      natural   → no overlay
      overlay   → add `<div className="absolute inset-0 bg-gradient-to-t from-foreground/60 to-transparent" />`
      duotone   → add `<div className="absolute inset-0 bg-primary/30 mix-blend-multiply" />`
      bordered  → outer `border border-border` ring
      masked    → outer `[mask-image:linear-gradient(to_bottom,black_75%,transparent)]`
  • Hover: `<Image className="object-cover transition-transform duration-700 hover:scale-105" />`.

GALLERY SECTIONS — render section.images as a real responsive grid:
  layout=grid-3/grid-4 → `grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3 md:gap-4`.
  Make tiles different aspect ratios (aspect-[4/5] | aspect-square | aspect-[3/4]) for a curated look — no uniform thumbnails.

FORM SECTIONS (type contact_form | reservation | booking_form | contact | newsletter)
  • Render an actual `<form>` with proper labels, focus rings, and a submit button.
  • Reservation fields: name, email, phone, date (input type="date"), party size (input type="number"), special requests (textarea).
  • Newsletter: just email + submit.
  • Form panel: `bg-card border border-border` plus the corner-radius implied by design_system.accent_shape (see DESIGN SYSTEM below).
  • Submit button: full primary CTA styling, full width on mobile.
  • Wrap form fields in `<Reveal>` with staggered delay.

DESIGN SYSTEM (apply CONSISTENTLY across the section — this is what makes the whole landing look like one product, not 8 random components)
  Motion intensity: {ds.get("motion", "subtle")}      → subtle = duration-500 only on entrance; energetic = +hover lifts; dramatic = +scale on enter; organic = +slow ease, gentle blur-in.
  Accent shape:     {ds.get("accent_shape", "rounded")}  → squared: rounded-md on every interactive surface, rounded-lg on cards (NEVER rounded-none); rounded: rounded-xl on cards, rounded-md+ on buttons; pill: rounded-full on buttons + rounded-2xl on cards; blob: rounded-[40%_60%_70%_30%/40%_50%_60%_50%] on image masks; hairline: rounded-md on buttons, rounded-lg on cards, with a thin 1px border (NEVER rounded-none — the radius and the hairline border coexist). Rule 12 below is the hard floor — every accent_shape must respect it.
  Surface:          {ds.get("surface", "elevated")}    → flat: bg-card no shadow; elevated: shadow-md hover:shadow-xl; bordered: border-2 border-border no shadow; layered: stacked z-translucent panels (bg-card/80 backdrop-blur); duotone: alternating bg-muted/bg-card per card.
  Image treatment:  {ds.get("image_treatment", "natural")} (see IMAGES rules).
  Section rhythm:   {ds.get("section_rhythm", "balanced")} → tight: py-12 sm:py-16; balanced: py-16 sm:py-20 lg:py-24; airy: py-24 sm:py-32.

PROJECT_DESIGN_TOKENS — USE THESE EXACT TAILWIND CLASS STRINGS VERBATIM (do NOT pick alternates, do NOT improvise radii / shadows / paddings; these strings have already been resolved from the design system above and ARE the spec):
  • Section <section> root padding (vertical):  {dt.get("section_padding_class", "py-16 sm:py-20 lg:py-24")}
  • Card surface (cards, panels, form wrappers): {dt.get("card_class", "bg-card shadow-md hover:shadow-xl rounded-xl")}
  • Button / pill / chip border-radius:          {dt.get("button_radius_class", "rounded-md")}
  • Image / photo / media border-radius:         {dt.get("image_radius_class", "rounded-xl")}
  • Standard transition for hover effects:       {dt.get("transition_class", "transition-all duration-300")}
  • Standard hover lift for interactive cards:   {dt.get("hover_lift_class", "hover:-translate-y-1 hover:shadow-lg")}
  Examples of correct usage:
    <section id="..." className="{dt.get("section_padding_class", "py-16 sm:py-20 lg:py-24")} bg-background">
    <article className="{dt.get("card_class", "bg-card shadow-md hover:shadow-xl rounded-xl")} {dt.get("transition_class", "transition-all duration-300")} {dt.get("hover_lift_class", "hover:-translate-y-1 hover:shadow-lg")} p-6">
    <Image className="object-cover {dt.get("image_radius_class", "rounded-xl")}" ... />
    <button className="{dt.get("button_radius_class", "rounded-md")} bg-primary text-primary-foreground px-5 py-3">
  RULE: every card / panel / form / featured-list-item MUST start with the CARD class string above — no ad-hoc shadow / radius combinations. Every image container MUST use the image radius. Every button MUST use the button radius. The section root MUST use the section padding. This guarantees every section in the build matches.

MODERN PATTERN ANCHORS (calibration set — match THIS quality bar, not generic 2018 SaaS)
  The aesthetic target is the way modern brand sites actually look in 2025-2026, not the
  generic template look that AI generators default to. Concrete reference anchors:
    • BELLA LUNA TRATTORIA (Italian dining) — charcoal background, oversized serif headline
      with ONE italic accent word ("Where Naples Comes to Your <em>Table</em>" in amber),
      single asymmetric photo bottom-right, generous breathing space. Restaurant / fine
      dining / hospitality should look like this — NOT a 3-column icon grid.
    • CHANEL DIFFUSER (luxury / fragrance) — pure dark background, single product photo
      centered with a glow underneath, headline + sub bottom-left, CTA pill bottom-right.
      Luxury / fragrance / fashion / premium beauty should look like this — NOT a SaaS
      hero with three feature cards below.
    • WAAW (premium audio) — dark olive-tinted background, blurred product photo behind a
      huge sans-serif headline with ONE word in lime ("DISCOVER A UNIQUE <em>EXPERIENCE</em>"),
      pill nav with avatar + ORDER NOW CTA in top-right. Premium audio / tech-with-personality
      should look like this.
    • ARCHITECTURE STUDIO — light gray background, asymmetric split (building photo left,
      headline right), purple CTA + purple icon, a stacked photo card with floating numbered
      counter ("03 →"). Architecture / design / portfolio should look like this.
    • VELORETTI (electric bikes) — pure cream/grayscale palette, single hero product photo
      centered, minimalist serif wordmark + thin horizontal nav, small floating "Powerful
      Motor" chip overlay, BUY button bottom-right. Premium product / outdoor / minimalist
      brands should look like this — color discipline through restraint, NOT through
      adding more colors.

  Common DNA across these references — internalize this:
    1. ONE primary surface (charcoal OR cream OR olive-tinted, NEVER pure #fff/#000).
    2. ONE brand-distinctive accent used sparingly (CTA pill, italic word, icon stroke).
    3. Generous breathing space; the headline takes 50%+ of the hero height.
    4. Photo is real (full-bleed, centered, or asymmetric), not a placeholder gradient.
    5. Type pairing has personality — display serif OR weighted geometric sans, not the
       default Inter-on-Inter SaaS look.

DESIGN CONTEXT
  Brand: {brand_name}
  Motif: {motif}
  Heading font: {typography.get("heading_font", "Inter")}
  Body font:    {typography.get("body_font", "Inter")}
  Palette (already wired as CSS vars in globals.css):
{palette_lines}
{visual_dna_block}{pers_block}{ref_block}

SECTION ANATOMY (driven by VISUAL DNA + the user message)
  Every section's user message includes ONE of:
    • "ANATOMY — IMPLEMENT THIS EXACT SPEC" — the spec is research-grounded, written from real
      research about THIS brand. Treat its structural skeleton (z-stacking, container hierarchy,
      grid shape, scale tokens, decorative integration) as non-negotiable.
    • "ANATOMY — STARTING POINT" — a generic structural floor. Use its z-stacking + overflow
      + headline scale as invariants, but compose the actual look from VISUAL DNA above.
  Centered-text-on-flat-color is a guaranteed FAILURE either way — every section must offer
  something visually distinct.

SECTION DENSITY (every non-hero section must feel COMPLETE — sparse = broken)
  Every non-hero section MUST render at least 3 distinct visual content blocks before any
  whitespace/spacer:
    Block A — Heading group (eyebrow + h2 + 1-line subhead OR a short lede paragraph).
    Block B — Primary content (cards / list / form / image grid / quote / stat band).
    Block C — Supporting block (a real, distinct UI element — secondary CTA + meta line,
              attribution row, info-tile triple, micro-stat strip, "as seen in" wordmark
              row, accordion teaser, address card, or trust badges). NEVER fill Block C
              with a giant decorative oversized word watermark; it must be REAL content.
  If section.items has fewer than the required minimum for its type, repeat brand.business_info
  rows, sibling-section CTAs, or attribution lines to reach density — never leave a 600px tall
  section with a single centered headline floating in space.
  DO NOT render a section that is just `<h2>` + `<p>` with nothing below it.

OTHER SECTION RULES (sections without an archetype block fall back to these)
  stats — Big numbers band: 2-4 columns, each item.value in text-5xl sm:text-6xl font-bold text-primary, item.label below in uppercase tracking-widest text-muted-foreground.

  faq — Vertical accordion. `<details>` + `<summary>` pattern (no client JS). Plus icon rotates 45° on open via `[&[open]_.fa-icon]:rotate-45`.
    LAYOUT: SINGLE COLUMN ONLY. FAQ NEVER uses a split layout with a side image panel.
    Heading group goes at the top (centered or left-aligned, max-w-2xl), accordion list
    fills the column below. Why: an FAQ section's value is the questions/answers, not a
    decorative side image — splitting it in two leaves either an underpopulated side or
    (worse) an empty fallback monogram panel that the binder couldn't fill. If you want
    visual interest, use a one-line "Still curious? Contact us →" link at the bottom of
    the accordion column. NO side panels. NO 2-column grids of question cards. ONE column.

  pricing — 2-3 plan cards. Highlight one via `ring-2 ring-primary` + small "Recommended" pill at top. Big price, feature list with check icons.

  cta / cta_band — Full-width band with bg-primary, primary-foreground text, large headline, supporting line, contrasting button (bg-background text-foreground).

  contact_form / reservation / booking_form / newsletter — One real form (see FORM SECTIONS above). Pair with a left-side info panel showing brand.business_info (address / phone / email / hours) for visual weight balance.

  team — Grid of avatar cards (rounded-full or rounded-2xl images), name, role, optional 1-line bio.

  press / logos / publications — A press section MUST have THREE rendered blocks (no exceptions):
    [1] Heading group — eyebrow ("PRESS"), headline (h2), 1-line plain subheadline.
        Render section.subheadline verbatim as a single `<p>` (no pill splitting).
    [2] Logo strip — 4-column wordmarks. `grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-x-6
        md:gap-x-10 gap-y-8 items-center`. Each cell `flex items-center justify-center h-16 md:h-20`.
        INSIDE each cell render the publication name as a uppercase wordmark
        (`<span className="text-sm md:text-base font-semibold tracking-[0.18em] text-foreground/70
        uppercase whitespace-nowrap text-center">{{name}}</span>`). Pull names from
        section.items[i].label OR section.items[i].title. NEVER use `<Image>` or fake SVG logos
        (Unsplash returns garbage for "michelin guide logo"). Add subtle vertical dividers between
        cells with `divide-x divide-border` on the parent on md+.
    [3] Pull-quote block — render section.items[i].description (the 8-16-word quote) as a real
        editorial pull quote: `<figure>` with quote in `text-xl md:text-2xl font-serif italic
        text-foreground leading-snug` and attribution (`item.title` or `item.label`) below in
        `text-sm uppercase tracking-[0.18em] text-muted-foreground`. If 3+ items have quotes,
        render a small carousel (autoplay 6s, 1 quote at a time, dots indicator). Otherwise
        a centered single quote with `max-w-3xl mx-auto`.
    NEVER render the section as JUST a pill row of 3 award-name pills with no logo strip and
    no quote. NEVER place a giant decorative watermark word in the section background.
    NEVER write the body as pill chips embedded in a paragraph (see Rule 9 / 11 above).
    NEVER use absolute-positioned, rotated, or overlapping award/press cards (no `card-stack`
    pattern, no `absolute inset-0`, no `rotate-[Xdeg]` on the cards, no negative margins that
    pull cards over each other). Press cards/logos render in a FLAT, in-flow grid only — every
    cell occupies its own row/column with no overlap. If you write `position: absolute` or
    `rotate-` on a press card, you have produced a defect.
    Section MUST be at least `min-h-[480px]` content-wise — fill all 3 blocks before any spacer.

  experience / journey / process / steps — When the section uses a horizontal carousel of cards,
    EVERY card in the visible viewport MUST have the SAME height. Use `flex` on the track with
    `[&>*]:h-[420px] sm:[&>*]:h-[460px]` (or grid + `auto-rows-fr`). Each card uses
    `relative overflow-hidden rounded-2xl` with the photo as `<Image fill className="object-cover" />`
    INSIDE the card — never let an oversized image push a single card taller than its siblings.
    Bottom-align text via a `bg-gradient-to-t from-foreground/85 via-foreground/40 to-transparent`
    overlay with the title + body in `text-background` on the lower third.

PIXEL-PRECISE LAYOUT TOKENS (use exactly these — they keep the whole page on one rhythm)
  • Outer section: `<section id="..." className="scroll-mt-24 md:scroll-mt-28 <bg> py-20 md:py-28 lg:py-36">` — vertical rhythm is fixed.
      Why `scroll-mt-24 md:scroll-mt-28`: the sticky `<header>` is `h-16` (mobile) → `h-20` (desktop). When the page scrolls to
      `#section-id`, the browser parks the section's top edge AT the viewport top — which puts the section heading directly
      UNDER the sticky nav, hidden. `scroll-mt-*` shifts the scroll target down by that much (~96/112px), so the section
      heading clears the nav. This is MANDATORY on every section — the page-wide sticky nav overlaps EVERY interior section
      header without it. The hero section (index 0) doesn't need this in practice but include it for consistency.
  • Decorative bleed containment: if THIS section uses ANY absolute-positioned decorative element
    (oversized watermark word, decorative blob, motif shape, accent ring, gradient orb, image that
    extends past the section edge for editorial effect, rotated card, sticker badge offset with
    negative inset), the outer `<section>` MUST include `overflow-hidden` (or `overflow-x-clip` if
    the section needs sticky/scrolling children). Decorative elements that escape the section root
    appear over the next section, the footer, the page edges, or as stray dark shapes in empty
    space — every shipped page with a black blob in a corner traces back to a missing `overflow-hidden`
    on the section that owns the decoration. Add it preemptively whenever you introduce an absolute
    decorative element; do NOT wait to see it leak.
  • Container: `<div className="container max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">` — never wider, never narrower.
  • Eyebrow:    `text-[11px] tracking-[0.18em] uppercase text-primary font-semibold mb-3`
      EDITORIAL NUMBERING (REQUIRED on every non-hero, non-cta section). Prefix the eyebrow
      with the section's two-digit index + an em-dash:
        `<p className="..."><span className="tabular-nums">{{indexStr}}</span> &mdash; {{section.nav_label || section.role}}</p>`
        where `indexStr = String(sectionIndex + 1).padStart(2, "0")` and `sectionIndex` is the
        SECTION_INDEX value provided in the user message (NOT a hardcoded number).
      Examples of valid eyebrows: "01 — STORY", "02 — MENU", "03 — RESERVATIONS", "04 — PRESS".
      The hero (index 0) and CTA-band sections may use a plain eyebrow label without the number
      (the hero's eyebrow is a brand tagline, not a section index). Every other section MUST
      carry the numbered prefix — it gives the page editorial rhythm and matches the reference
      aesthetic (Bella Luna, architecture studios, premium product brands).
  • H2:         `text-3xl sm:text-4xl lg:text-5xl font-bold tracking-tight font-[family-name:var(--font-heading)]`
  • Subhead:    `mt-4 text-lg md:text-xl text-muted-foreground max-w-2xl`  (max-w-3xl when centered)
  • Body copy:  `text-base md:text-lg text-muted-foreground leading-relaxed`
  • Section gap: between heading group and content block use `mt-12 md:mt-16 lg:mt-20`.
  • Card grid gap: `gap-4 md:gap-6 lg:gap-8` — never `gap-2` (cramped) or `gap-12` (too sparse).
  • Card chrome: `bg-card border border-border rounded-2xl p-6 lg:p-8 transition-all duration-300 hover:-translate-y-1 hover:shadow-lg hover:border-primary/30`.
  • Button primary: `inline-flex items-center justify-center px-6 py-3 rounded-full bg-primary text-primary-foreground font-medium hover:opacity-90 transition`.
  • Button secondary: same but `bg-card border border-border text-foreground hover:bg-muted`.
  • Anti-stretching: NEVER let text run wider than `max-w-prose` (~65ch); NEVER let card columns exceed 4 on lg.

DESIGN TOKEN DISCIPLINE (every spacing / radius / type / color value snaps to a scale — no drift)
  Incoherence between sections — different paddings, different radii, different type sizes —
  is what makes a page feel "mixed" instead of designed. The brief picks the SCALES; you snap
  every value to them. Don't invent off-scale numbers.

  • SPACING SCALE (Tailwind defaults, used for all margin / padding / gap):
      Allowed: 1, 2, 3, 4, 5, 6, 8, 10, 12, 16, 20, 24, 28, 32, 40, 48, 56, 64, 80, 96.
      FORBIDDEN: arbitrary `p-[37px]`, `mt-[52px]`, `gap-[19px]` — that drift is exactly what
      makes the page feel uneven. If you reach for an arbitrary px value, you picked the
      wrong scale step.
      Section vertical rhythm uses `py-16 md:py-24 lg:py-32` consistently across EVERY interior
      section (hero excluded — hero uses min-h-screen). Every section breathes the same.

  • TYPE SCALE — pick from this 6-step ramp, nothing in between:
      display  → `text-5xl md:text-6xl lg:text-7xl` (hero headline only)
      h1       → `text-4xl md:text-5xl lg:text-6xl` (rare — flagship feature section)
      h2       → `text-3xl sm:text-4xl lg:text-5xl` (every section heading)
      body-lg  → `text-lg md:text-xl` (subhead, intro lede)
      body     → `text-base md:text-lg` (paragraph copy)
      caption  → `text-xs md:text-sm` (eyebrow, micro labels, tabular meta)
      Each step has a fixed line-height: display/h1/h2 → `leading-[1.05]` to `leading-tight`,
      body-lg → `leading-relaxed`, body → `leading-relaxed`, caption → `leading-normal`.
      DO NOT invent `text-[27px]` or `text-[42px]` — snap to the ramp step.

  • RADIUS PAIR — exactly TWO values used across the page, picked from `theme.design_system.radius`:
      CARD radius (cards, tiles, image containers, panels): rounded-2xl OR rounded-3xl
        (pick ONE based on motif: soft brand → 3xl, editorial brand → 2xl, sharp brand → xl).
      PILL radius (buttons, badges, chips, toggles): rounded-full.
      No third radius value. NEVER mix rounded-lg + rounded-2xl + rounded-3xl on the same page —
      the inconsistency reads as carelessness.

  • COLOR ROLES — every surface, text, and accent pulls from a NAMED ROLE, never a literal color:
      Surfaces  → `bg-background`, `bg-card`, `bg-muted`, `bg-primary` (for accent band only)
      Text      → `text-foreground`, `text-muted-foreground`, `text-primary`, `text-primary-foreground`
      Borders   → `border-border`, `border-primary/30` (hover only)
      Accents   → `text-primary`, `bg-primary`, `ring-primary`
      FORBIDDEN: `bg-white`, `bg-black`, `bg-gray-100`, `text-gray-600`, `border-zinc-300`,
      `bg-[#fafafa]`, any hex / rgb literal. The shadcn token system already maps to the brief's
      palette — using a Tailwind gray token bypasses the brand and produces the "every section
      feels different" anti-pattern.

SECTION LAYOUT ARCHETYPES (every section is ONE of 4 shapes — reuse breeds clean)
  Inventing a fresh arrangement per section makes the page read as 12 stitched components.
  Pick ONE archetype for THIS section from the four below, then alternate direction down the
  page for rhythm. Internal structure inside each archetype stays IDENTICAL every time it's used.

    ARCHETYPE 1 — SPLIT (text on one side, media on the other)
      Use for: story, philosophy, about, method, featured-program, single-flagship.
      Shape: `grid lg:grid-cols-2 gap-12 lg:gap-20 items-center`. Text column has eyebrow + h2
      + body + ≤1 CTA. Media column has ONE image OR ONE card stack — never both.
      Alternate direction across sections: 1st split text-left/media-right, next split flips.
      A page that always splits text-left/media-right feels static.

    ARCHETYPE 2 — CENTERED INTRO + GRID BELOW
      Use for: programs, services, faculty, menu, features, gallery, press.
      Shape: centered heading group (eyebrow + h2 + 1-line subhead, max-w-2xl mx-auto text-center),
      then `grid sm:grid-cols-2 lg:grid-cols-3` (or 4 if exactly 4 items, or 2 if exactly 2 items).
      Card grid count matches data count — never pad to fill 3-up when you have 2 items.

    ARCHETYPE 3 — FULL-WIDTH BAND
      Use for: cta, mid_cta_banner, trust_bar, stats, awards strip, single quote.
      Shape: `bg-primary text-primary-foreground` OR `bg-foreground text-background`, no card chrome,
      content centered with generous py-24 md:py-32, ONE primary message + ONE primary action.
      Use 1-2 bands per page max — more turns the page into a marketing brochure.

    ARCHETYPE 4 — FORM + INFO PANEL
      Use for: contact, reservation, booking_form, newsletter, lead_form.
      Shape: `grid lg:grid-cols-2 gap-8 lg:gap-12`. Form on one side, info panel (address /
      hours / phone / map) on the other. Form has real `<form>` element with labeled inputs +
      submit. Info panel uses brand.business_info exclusively.

  PICKING THE ARCHETYPE: the section's `role` / `type` field decides — there is ONE correct
  archetype per type. Don't reach for a fancier shape because it feels more original.
  Repetition of the right archetype is what makes the page read as designed.

ACCENT RESTRAINT (the brand color is a precious resource — spend it sparingly)
  Most generated pages overuse the brand accent — every section gets a colored pill, a colored
  eyebrow, a colored icon, AND a colored heading word. The result reads as noisy: when
  everything is highlighted, nothing is.

  Rule of thumb per VIEWPORT (not per section — per first-screen worth of content):
    • ONE primary CTA in accent fill (`bg-primary text-primary-foreground`).
    • Optionally ONE accent eyebrow (`text-primary uppercase tracking-[0.18em]`).
    • Optionally ONE italic accent word inside a headline (`<span className="italic text-primary">`).
    • Icon strokes in accent are fine when they're small (h-4 w-4) and grouped — they read as
      brand color in aggregate, not as competing accents.

  FORBIDDEN per section:
    • TWO solid `bg-primary` blocks visible at once (e.g. eyebrow pill + CTA pill + sidebar tag).
    • An accent-colored card border AND an accent-colored eyebrow AND an accent-colored CTA all
      visible together — pick one of the three.
    • The body paragraph carrying the accent color — body copy is `text-foreground` or
      `text-muted-foreground`, never `text-primary`.

  A calm, deliberate page uses the accent like punctuation, not paint.

FEATURE RESTRAINT (one section should communicate ONE idea — restraint is the difference between 7 and 9)
  Every section: ONE kicker (eyebrow), ONE h2, AT MOST ONE intro line (1-2 sentences), ONE
  content block (cards / list / form / image), AT MOST ONE CTA. If the brief gave you a badge AND
  a stat trio AND a quote AND two CTAs, the section is trying to be two sections — drop the
  weaker half.

  • Skip stuffing: if section.items has 3 items, render exactly 3 cards. Do NOT add a 4th
    placeholder card. Do NOT add a "View all" trail when there's no more to view.
  • Skip secondary CTAs: a primary CTA + a "Learn more" ghost button is fine; a primary + two
    ghosts + a tertiary outline link is noise.
  • Skip orphan elements: a single floating badge in the corner with no narrative role.

  Restraint here is the difference between "understandable" (clean) and "trying too hard"
  (busy). The reader should be able to summarize each section in ONE phrase. If they can't,
  the section is doing too much.

CONTENT-PRESENCE GATE (a section with empty data must be REMOVED, not rendered as a stub)
  A render-it-anyway pattern is the canonical "broken" feel: filter tabs leading to "No courses
  found", an empty image grid with hover overlays, a testimonials section with two placeholder
  cards, a stat band with zeros.

  Required-data thresholds per section type — if the brief / landing.json doesn't supply at
  least the minimum, the section's render function MUST short-circuit:

    pricing / plans          → ≥1 plan with a real $ figure (NOT "Custom — contact us" alone)
    menu / products          → ≥3 items with name + price OR description
    gallery / portfolio      → ≥3 images with non-empty url
    testimonials             → ≥2 quotes with attribution
    team / faculty           → ≥2 members with photo + role
    locations                → ≥1 address (city minimum)
    press / awards           → ≥3 logos OR ≥2 quotes
    stats                    → ≥3 numbers (NOT zero)
    faq                      → ≥4 questions
    process / how_it_works   → ≥3 steps
    reservation / contact    → form + at least 1 contact channel in info panel

  Implementation pattern:
      `if (!section.items || section.items.length < N) return null;`
  at the top of the component. The page is the union of sections that actually have content,
  not the union of every section the brief tried to draft.

  A half-empty section reads as broken. A removed section reads as intentional.

NO LIVE-RUNTIME DEFAULTS IN COPY (the "Tonight 7:15 PM" / "03:21" failure)
  NEVER render a runtime-computed value as a default placeholder:
    ✗ `new Date().toLocaleTimeString()` in a hero "Now open" badge.
    ✗ A live countdown to "Tonight's special" with a hardcoded target.
    ✗ Inline `Date.now()` formatted as "Updated 2 min ago".
  Use STATIC plausible defaults:
    ✓ "Open until 10pm" (static string from brand.business_info.hours).
    ✓ "Today's special" (static label, no time).
    ✓ "Updated weekly" (cadence, not timestamp).
  A live clock that drifts as the user reads is a visible bug — it makes the page feel like a
  WIP demo. Defaults should be sensible, stable, brand-info-derived strings.

NAV ↔ FOOTER PARITY (same items, same order, one source)
  The header nav and the footer's primary link column MUST consume the SAME data source
  (typically `landing.nav` or a derived list) and render the items in the SAME order. Don't
  let the header show [Programs, Method, Faculty, Contact] and the footer show
  [About, Programs, Blog, Press]. That mismatch is the #1 "tell" that the page is generated.

  Implementation: the nav array lives at `landing.nav` (or pulled from each section's
  `nav_label`). Header maps over it in order; footer's primary column maps over the same array
  in the same order. Footer may have ADDITIONAL columns (Visit / Legal / Social) but its
  primary nav column matches header.

CONTRAST & READABILITY (NON-NEGOTIABLE — every line of text must be plainly legible)
  • Body / paragraph text MUST use `text-foreground` or `text-muted-foreground` — NEVER `text-foreground/40`,
    `text-muted-foreground/60`, or any sub-60% opacity for paragraph copy. Eyebrow tags and timestamps may use
    `text-muted-foreground` (full opacity) but never lower.
  • Headlines: ALWAYS `text-foreground` (or `text-primary-foreground` when sitting on `bg-primary`). Never apply opacity to headlines.
  • Text over IMAGES: stack a real overlay (`bg-foreground/60`, or `bg-gradient-to-t from-foreground/70 to-transparent`)
    BEFORE rendering text, then render text in `text-background`. Never put white text directly on unprocessed photos.
  • Text over `bg-primary`: must be `text-primary-foreground`. Text over `bg-card` / `bg-muted`: must be `text-foreground` (NOT muted).
  • DARK INVERSE SURFACES — when a section, footer, or panel uses ANY of
    `bg-foreground`, `bg-secondary` (when secondary is a dark brand color),
    `bg-foreground/95`, or any dark `bg-*` shade that inverts the page:
      ✗ NEVER use `text-foreground` (same hue as bg → invisible).
      ✗ NEVER use `text-muted-foreground` (slightly darker hue → invisible).
      ✓ Body text MUST be `text-background` (full opacity, max contrast).
      ✓ Muted/secondary text on dark surfaces uses `text-background/70`
        (NEVER lower; 70% on dark inverse stays AA-readable).
      ✓ Headings: `text-background` (no opacity).
      ✓ Links: `text-background hover:text-primary-foreground`
        (or hover to `text-accent` if accent is light).
      ✓ Borders: `border-background/15` for hairlines on dark panels.
      ✓ Form inputs on dark surfaces: `bg-background/10 text-background
        placeholder:text-background/50 border-background/20`.
      ✓ Pairing `bg-primary-foreground` text on `bg-secondary` is a
        FAILURE when secondary isn't paired with primary-foreground in
        the palette — use `text-background` instead. The only safe
        cross-token pairing is `bg-primary` ↔ `text-primary-foreground`.
    This is the #1 footer / dark-band failure mode — invisible nav links and
    body copy because the writer reached for `text-foreground` or
    `text-muted-foreground` reflexively. Always invert text on inverse surfaces.
  • Watermark / decorative oversized type uses `text-foreground/[0.06]` — that is the ONLY place a sub-10% opacity is allowed,
    and it must NEVER be the only text in its block (it sits BEHIND a real headline).
  • Buttons MUST visibly differ from the surface they sit on:
      Primary CTA on light surface  → `bg-primary text-primary-foreground` (solid, no transparency).
      Primary CTA on dark/photo hero → `bg-background text-foreground` (inverted) — never `bg-white/20` glass on a photo
      that already has busy detail; if you want glass, add `backdrop-blur-md bg-background/80` (≥80%, not 20%).
      Secondary button → `border border-border bg-card text-foreground hover:bg-muted` — visible border is REQUIRED.
      NEVER ship a button that is `bg-white text-white`, `bg-primary/10 text-primary` on `bg-card` (too low contrast),
      or any combo where label and surface differ by less than ~3:1 luminance.
  • Disabled / hover states still show a label — never fade label opacity below 70%.

GRID-POSITIONING CLASSES ON WRAPPED CHILDREN — MUST GO ON THE WRAPPER
  CSS Grid only honors `col-span-*`, `row-span-*`, `col-start-*`, `row-start-*`
  on the DIRECT child of the grid container. Putting them on the inner element
  inside a wrapper component (`<Reveal>`, `<motion.div>`, `<Tilt>`, any HOC) is
  SILENTLY IGNORED — every card collapses to 1 column and they STACK over each
  other. This is a hard-fail production bug (Al-Qalam TrustTickerSection,
  2026-06-03 — bento grid with 7 tiles all collapsed into one column).
  ✗ FORBIDDEN — col-span on the inner div inside a Reveal:
      <div className="grid grid-cols-12 gap-4">
        <Reveal variant="fade-up">
          <div className="col-span-12 lg:col-span-7 row-span-2 ...">   ← IGNORED
            ...
          </div>
        </Reveal>
      </div>
  ✓ CORRECT — col-span on the Reveal wrapper (Reveal forwards className to its root):
      <div className="grid grid-cols-12 gap-4">
        <Reveal variant="fade-up" className="col-span-12 lg:col-span-7 row-span-2">
          <div className="bg-card rounded-2xl p-6 ...">    ← only visual classes here
            ...
          </div>
        </Reveal>
      </div>
  Same rule for `<motion.div>` and any other element wrapped between the grid
  and the card: grid-positioning classes belong on the OUTERMOST wrapper that
  is the direct grid child. Every other class (bg-*, p-*, rounded-*, etc.)
  stays on the inner div.
  Final check before output: for every `<div className="grid ...">`, the FIRST
  className token on each direct child MUST include a `col-span-` or
  `col-start-` (or that child must be inside a single-column wrapper that's
  itself a grid child). No direct grid child without grid placement.

CARD ALIGNMENT — PIXEL-PERFECT (cards in a row MUST line up; no jagged grids)
  • Every card in a multi-card row MUST share the SAME outer chrome: same padding, radius, border, height behavior.
  • Apply `h-full` to each card and `items-stretch` (or `grid auto-rows-fr`) on the parent so cards in a row equalize.
  • Internal vertical layout: `flex flex-col gap-3` — title, description, then `mt-auto` on the footer/CTA so footers ALIGN
    across cards with different copy lengths.
  • Card photos in a grid: lock aspect ratio (`aspect-[4/5]`, `aspect-square`, or `aspect-[3/4]`) and use `object-cover`.
    Mixing aspects across the SAME row is forbidden — vary aspects only across DIFFERENT rows for editorial rhythm.
  • Headings inside cards: clamp to 2 lines (`line-clamp-2`); descriptions clamp to 3 lines (`line-clamp-3`) so visual weight stays even.
  • Icon chips inside cards: fixed size — `h-10 w-10` or `h-12 w-12`. Never let a chip resize with the icon.
  • Buttons inside cards: same height (`h-10` or `h-11`) and same padding across all cards in the row.
  • Grid columns: pick ONE of `grid-cols-1 md:grid-cols-2 lg:grid-cols-3`, `grid-cols-1 md:grid-cols-2`, or 4-up — DO NOT mix column counts mid-section.
  • The bottom edges of all cards in a row MUST end on the same Y. If copy varies, push the CTA down with `mt-auto`.

CARD WIDTH — FLUID GRID ONLY (no narrow/collapsed cards, no card-stack improvisations)
  This applies to EVERY section that renders multiple cards (features, value_prop, benefits,
  process, services, why-choose-us, capabilities, team, etc.) — not just press.
  ✗ FORBIDDEN PATTERNS — these produce squished/unreadable cards and are defects:
    • Fixed pixel widths on cards (`w-[280px]`, `w-72`, `min-w-[200px]`). Cards must be GRID-FLUID.
    • Negative margins between cards (`-ml-4`, `-space-x-2`, `-mt-8`) to make them overlap.
    • `position: absolute` / `absolute inset-0` on a card in a list of cards.
    • `rotate-[Xdeg]` on cards (the only legitimate rotated card is a SINGLE editorial accent).
    • Horizontal scroll (`overflow-x-auto flex flex-nowrap`) for ≤ 6 items — use a grid instead.
    • Oversized numbered watermarks `01 02 03` rendered as a background BEHIND cards
      (Claude improvises this from editorial design refs but it always collapses widths). If
      numbering is desired, put it INSIDE each card as a small eyebrow (`text-sm text-primary
      font-bold`), NEVER as an absolute-positioned giant numeral behind the card.
    • Cards rendered in a `flex flex-row` WITHOUT `flex-1` / `basis-0` so each child gets equal width.
  ✓ REQUIRED — every multi-card section uses ONE of:
    • `grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-6` (3-up, for 3-6 items)
    • `grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6` (4-up, for 4 or 8 items)
    • `grid grid-cols-1 md:grid-cols-2 gap-6 lg:gap-8` (2-up, for 2 or 4 large items)
    Each cell is `w-full` — fluid; the grid handles distribution. If you write a width unit
    on a card (px / rem / Tailwind w-N), you have produced a defect.

ABSOLUTE POSITIONING — NEVER OVERLAP COPY (universal rule, all section types)
  Decorative absolute elements (gradient orbs, motif SVGs, watermark numbers, badge dots)
  are allowed but MUST NOT sit on top of section copy. The Reliant Logistics value_prop
  section shipped with 5 brand-pillar cards `absolute`-stacked on top of the headline
  "Freight that moves on your schedule" — 5 cards smeared across 200px of vertical
  space, every card overlapping the next, the headline showing through underneath. This
  is the SAME class of bug as press/timeline overlap, but on a value_prop / why-choose
  section it had no specific rule yet. It does now:
  ✗ FORBIDDEN — ANY section type:
    • Cards (value_prop pillars, why-choose chips, brand_pillars, benefit chips, feature
      tiles, stat tiles, badge rows) rendered with `absolute` / `absolute inset-0` /
      `absolute left-* top-*` so they stack over each other or over copy.
    • Mid-animation states that LOOK overlapping at rest (e.g. cards with `translate-x-0`
      initial that only spread out via a JS hover/animation that never fires server-side).
      If you can't see the final layout in the static HTML, the initial render is broken.
    • Headline copy + a grid of cards where the cards are `absolute` positioned over a
      column the headline also occupies. Two-column splits are flex/grid, never absolute.
    • Brand pillar / icon-label chips positioned absolutely in a "fanned arc" pattern.
      Render them as a `grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-4` row instead.
  ✓ THE ONLY LEGITIMATE absolute children inside a section are:
    • A SINGLE oversized watermark word/number at `text-foreground/[0.06]` BEHIND copy
      (the copy must explicitly carry `relative z-10` to sit above it).
    • A SINGLE decorative blob/orb/ring at `bg-primary/10 blur-3xl` in a corner.
    • Photo overlays: `<div className="absolute inset-0 bg-gradient-to-t ..." />` ON TOP
      of an `<Image fill>` inside the SAME `relative overflow-hidden` parent.
    • The accent_shape decorative SVG in a corner, fixed size, single instance.
  Multiple absolute children in the same section is a smell — if you have 2+, audit
  whether you're trying to express a flow layout as positioned chips. Use grid/flex.
  Final check before output: every `<article>`, `<li>`, `<a>` rendering a card /
  chip / item MUST be a direct child of a `grid` or `flex` parent, NOT `absolute`.

CTA DATA SHAPE — section.cta is {{label, href}} object, NEVER a string, may be absent
  Pattern (verbatim — copy this exactly):
    const cta = (section.cta && section.cta.href) ? section.cta : landing.ctas?.primary;
    // then render: <Link href={{cta?.href || "#"}}>{{cta?.label || "Get started"}}</Link>
  ✗ NEVER write `const cta = section?.cta || "Get started"` — `{{}}` is truthy in JS so
    `cta` becomes `{{}}`, and a later `<span>{{cta}}</span>` throws the runtime error
    "Objects are not valid as a React child (found: object with keys {{}})". This was
    a real production crash on the Reliant Logistics build (6 hero/CTA sections).
  ✗ NEVER render `{{section.cta}}` or `{{cta}}` directly as a JSX child — it is an object.
    Always destructure: `{{cta.label}}` for the visible text, `cta.href` for the link.
  ✓ When the section's CTA is missing/empty, fall back to `landing.ctas.primary`.
    When BOTH are missing, render no CTA (don't emit a button with no label).

CONTENT INSIDE A CARD — overflow + multi-column safety
  This applies to ANY content INSIDE a card or panel: stat triples, label/value rows,
  big-number callouts, side-by-side metrics, mini-tables. The card itself can be
  fluid (per above) and still produce broken layouts INSIDE if these rules slip:
  ✗ FORBIDDEN INSIDE-CARD PATTERNS:
    • Oversized numeric / currency headlines without responsive scaling.
      `text-7xl` on a $1,800 number inside a narrow card WILL clip. Always use a
      RESPONSIVE scale: `text-4xl sm:text-5xl md:text-6xl` and never bigger than
      `text-6xl` for headline numbers inside a card. The hero outside cards is
      a different rule — INSIDE cards, smaller scales are mandatory.
    • Stat / metric triples rendered as `flex flex-row` WITHOUT `min-w-0` on each
      child. flex children default to `min-w-0: auto` which makes long labels
      ("DISPOSABLE INCOME") push siblings off-screen or smash into each other.
      EVERY flex/grid child rendering text MUST have `min-w-0` so text can shrink/wrap.
    • Adjacent label/value columns with no gap. `gap-3` minimum between columns;
      `gap-4` or `gap-6` for stat triples. NEVER `gap-0` or no gap class.
    • Long uppercase labels with `tracking-widest` and no wrapping. "INCOME EXPENSES
      DISPOSABLE" laid out as 3 inline columns at `text-xs uppercase tracking-widest`
      MUST use `break-words` or shorter labels — otherwise letters from one label
      run into the next.
  ✓ REQUIRED INSIDE-CARD PATTERNS:
    • Containers that hold a single oversized headline: outer card has
      `overflow-hidden` or the headline uses `break-all` / `tabular-nums` with
      responsive scaling. Test mentally: "if the number were $999,999 would it fit?"
      If no, scale down or add overflow handling.
    • Stat triples / metric rows: `grid grid-cols-3 gap-4` with each cell
      `min-w-0 flex flex-col items-start gap-1`. Label uses `text-[11px] sm:text-xs
      uppercase tracking-wider text-muted-foreground` (NOT tracking-widest on long
      words). Value uses `text-lg sm:text-xl font-bold tabular-nums`.
    • For currency values: `tabular-nums` so digits align; consider `text-balance`
      on multi-word labels.
    • If a card holds both a giant headline AND a sub-metric row, the headline scales
      down at the card's responsive breakpoints (`text-4xl md:text-5xl`), not at the
      viewport's. A card that takes 50% of the viewport at lg+ has the layout
      constraints of a `md` screen, not a `lg` screen — choose scales accordingly.

SECTION SURFACE RHYTHM (forces the page to alternate, not look monotone)
  The user prompt for THIS section tells you which surface to use. Pick the OUTER section className
  from this menu — and pair it with the INNER card surface that has guaranteed contrast against it:

    Section bg = `bg-background`                     → inner cards: `bg-card border border-border` (lighter pop).
    Section bg = `bg-muted/40` or `bg-muted/50`      → inner cards: `bg-background border border-border/60` (lighter pop).
    Section bg = `bg-card`                           → inner cards: `bg-muted/40 border border-border` (subtle indent).
    Section bg = `bg-primary/5` or `bg-primary/10`   → inner cards: `bg-background border border-primary/20` (warm tint).
    Section bg = `bg-accent/15`                      → inner cards: `bg-background border border-accent/30` (cool tint).
    Section bg = `bg-foreground text-background`     → inner cards: `bg-background/10 border border-background/20 text-background` (dark mode panel).
    Section bg = `bg-primary text-primary-foreground`→ inner cards: `bg-background/15 border border-primary-foreground/25 text-primary-foreground` (full color band).

  Required diversity rules:
    • NEVER ship two adjacent sections with the same outer bg. The user message lists the previous
      section's bg so you can pick a different one.
    • A section's inner card MUST always be a DIFFERENT shade than the section bg — never bg-card on
      bg-card, never bg-background on bg-background. Cards must POP off the section, not blend.
    • Form inputs (input, textarea, select) MUST use `bg-background border border-border` on a non-
      background section, or `bg-muted/40 border border-border` when the section IS bg-background.
      An input that visually disappears against its container is a FAILURE.

BRAND MOTIF AS PAGE-WIDE WATERMARK (cohesion across sections)
  The brief carries ONE `motif` word (e.g. "warm", "editorial", "luxe", "organic") and
  `visual_dna.decorative_motifs` (concrete glyph candidates: ampersand `&`, brand initial
  letter, ornamental dot, simple geometric shape, industry icon — knot, leaf, anchor, gear).
  Pick ONE concrete glyph from `visual_dna.decorative_motifs` (or, when absent, derive one:
  brand initial for editorial/luxury, ampersand for hospitality, leaf for wellness,
  hexagon for industrial). THE SAME glyph MUST appear on EVERY non-hero/non-cta section
  this brand renders — that's how a page reads as ONE brand instead of 8 stitched components.
  Placement rules per section:
    • LIGHT-BG sections (`bg-background`, `bg-muted/40`, `bg-card`): place glyph TOP-RIGHT
      of the section, oversized (`text-[8rem] md:text-[12rem] font-serif`) at very low
      opacity (`text-foreground/[0.04]`) inside an `absolute -top-8 right-4 md:-top-12 md:right-12 pointer-events-none select-none` wrapper. The parent <section> needs `overflow-hidden` (which Rule 1186 already mandates for decorative bleed).
    • DARK-BG sections (`bg-foreground text-background`): place glyph BOTTOM-LEFT at the same
      oversized scale, opacity `text-background/[0.06]` inside `absolute -bottom-12 left-4 md:-bottom-16 md:left-12 pointer-events-none select-none`.
    • SECTION 0 (hero) renders the motif as a SMALL eyebrow ornament next to the eyebrow
      label (not the giant watermark) — the hero already carries the photo + headline weight.
    • CTA-band sections (`bg-primary`) skip the watermark — the band's color is loud enough.
  The watermark glyph is the SAME character on every section (consistency over surprise).
  Do NOT swap glyphs per-section. Do NOT use full words ("Premium", "Quality") as watermarks.
  Do NOT raise opacity above 8% — it stops reading as decoration and starts competing with copy.

BRAND COLOR DISCIPLINE (the references' aesthetic — restrained, not rainbow)
  The way reference sites (Bella Luna, Chanel, Apple, Veloretti, modern studios) use color:
    1. ONE primary surface dominates the page — the palette's `background` slot. This is
       a brand-TUNED neutral (warm cream for Italian dining, charcoal for luxury, sand for
       travel, cool gray for architecture). NOT pure white #fff or pure black #000.
    2. The brand's saturated color (`primary`) appears SPARINGLY but UNMISTAKABLY:
         ✓ The CTA pill (`bg-primary text-primary-foreground rounded-full`)
         ✓ One italic accent WORD in the headline (`<span className="italic text-primary">`)
         ✓ Eyebrow text above the heading (`text-primary uppercase tracking-[0.2em] text-xs`)
         ✓ Icon stroke color (`<Icon className="text-primary" />`)
         ✓ A thin underline beneath an active nav item (`border-b-2 border-primary`)
         ✓ Decorative motif glyph color
    3. The `accent` slot is for secondary highlights (badge underlays, small dots, ornaments) —
       use AT MOST once per section, often not at all.

  WHAT TO AVOID:
    ✗ Section after section in `bg-primary` or `bg-accent/15` — that's the "rainbow page"
      anti-pattern. The references DON'T do this.
    ✗ Section that uses ONLY `bg-background` + `text-foreground` + `text-muted-foreground`
      with NO appearance of `text-primary` / `bg-primary` ANYWHERE — that's monochrome SaaS.
      Every section needs the brand color to APPEAR somewhere, just not as a full surface.
    ✗ Defaulting to pure white or pure black. The palette's `background` and `foreground`
      slots are brand-tuned neutrals; use those tokens, never `bg-white` / `bg-black` literals.

  RULE OF THUMB: if you removed every neutral and only the brand-accent pixels remained,
  there should still be enough to ANCHOR the brand (CTA + eyebrow + icon + accent word),
  but not enough to overwhelm a calm, expensive-feeling page.

GEOGRAPHY CONSISTENCY (one city across every section — entity-level coherence)
  • The brief's `brand.business_info.address` names ONE metro (e.g. Austin TX, Brooklyn NY,
    London UK). EVERY string in this section MUST stay inside that metro. NEVER mention a
    different city, state, neighborhood, area code, or local landmark.
    The failure mode you must NOT produce: footer says "Clarksville, Austin TX 78703" but the
    catering blurb references "Carroll Gardens block parties" or testimonials say
    "Brooklyn's best". Pick the metro from `brand.business_info.address`; every neighborhood,
    venue, outlet, and area code you mention afterwards must be in that metro.
  • If you can't infer a neighborhood within the locked metro confidently, write generic
    in-metro phrasing instead ("a neighborhood spot", "an Austin staple") — never reach
    for a famous-sounding neighborhood from another city.
  • Trust bar / press logos: name outlets that actually cover that metro (Austin → "Texas
    Monthly", "Eater Austin"; NYC → "Eater NY", "Time Out New York"; LA → "LA Times",
    "Eater LA"). Don't sprinkle generic national magazines unless the brand actually
    has that press.
  • Phone numbers in copy match the city's area code: Austin 512, Brooklyn 718, LA 213/323,
    Chicago 312, Houston 713, SF 415, Seattle 206. If `business_info.phone` is set, use that
    exact value; never invent a different one.

QUALITY BAR
  • Sections that are GENERIC (centered headline + 3 plain icon cards) are a FAILURE — every section must offer something visually distinct.
  • Hero must NOT be a centered text block on flat color — it must use a real background image with overlay.
  • Adjacent sections must visually differ (varying bg, layout, or rhythm). The user message tells you which background to use.
  • Cards have hover-lift + accent border or icon — never flat text-only.
  • Spacing follows the PIXEL-PRECISE LAYOUT TOKENS above.
  • DO NOT produce a section that is purely a paragraph of text — every section earns its place visually.
  • DO NOT default to a 3-column icon-card grid for non-photo sections — pick a NO-PHOTO LAYOUT VARIANT from the user message.
  • DO ensure the brand color appears in every section (CTA / eyebrow / icon / accent word) — see BRAND COLOR DISCIPLINE above.

CARD SIZING — CONTENT-FIT, NEVER FORCED HEIGHT
  • NEVER write `min-h-[300px]`, `min-h-[400px]`, `min-h-[480px]`, `min-h-[600px]`,
    `h-[400px]`, or any FIXED-HEIGHT utility on a CARD container (anything that
    holds copy, an icon, a small image, or a stat). Forced heights produce the
    #1 visual failure: cards with rivers of empty space because the content
    didn't fill the box. Cards SIZE TO CONTENT — use `h-full` on grid items
    ONLY to equalize neighbors that already have similar content density.
  • Fixed heights are valid ONLY for:
      - Hero `<section>` outer (`min-h-[640px] md:min-h-[100svh] lg:min-h-screen` is the spec — that's a section, not a card)
      - Image containers with `aspect-[4/5]` / `aspect-square` etc. (aspect IS the height)
      - True hero overlays / decorative SVG containers
    Anywhere else, `min-h-[*]` and `h-[*px]` are BANNED.
  • If you have an item without `description`, `image`, or any rich content,
    DROP that item from the render. A 3-card grid with 1 well-filled card +
    2 stub cards (just a title) is a FAILURE — make it a single hero card
    or 2-up if the data only supports 1-2 items. Use `landing.json` item
    count to decide grid shape, not a hardcoded `grid-cols-3`.
  • Container blocks (`<article>`, `<div>` cards): padding `p-6` to `p-8`,
    content-fit height. Title + ≥1 line of description + ≥1 visual hit
    (icon, image, number) per card MINIMUM. If any card would have less,
    drop it or restructure.
  • Section vertical padding stays in the `py-16 md:py-24 lg:py-32` range
    — that's the SECTION spacing, separate from card sizing.

EMPTY-SPACE / WHITE-SPACE DISCIPLINE
  • A section is FAILING if there's a noticeable rectangle of empty surface
    (>200px tall × >300px wide) with no content, icon, or decorative motif
    in it. That includes: card interiors with too few items, gradient
    panels with one heading at the top and nothing below, hero overlays
    sized for a search widget but holding only one button.
  • If the content density is low (brief gave you 1-3 items), use a layout
    matched to that density: a single editorial hero card, a 2-up split,
    a centered pull-quote — NOT a 4-column grid with placeholder gaps.
  • NEVER reserve a large image panel (≥40% of section width) just to render
    a single decorative letter / oversized monogram / cream gradient with
    nothing inside. If the section has no real photo from
    `landing.json[*].images`, DO NOT render a blank framed area where the
    photo would have gone — restructure the layout to text-led (e.g.
    testimonials carousel without a side panel, story section without a
    photo card). A giant single letter `I` floating in an empty gradient
    panel is the canonical "missing image" failure — avoid it.
  • Decorative whitespace (margins, breathing room) is intentional and
    pixel-precise (the spacing tokens). Accidental empty rectangles are
    not the same thing.

SURFACE CONTRAST — CARDS MUST CONTRAST WITH SECTION BACKGROUND
  • The page uses 3 neutral surface tones: `bg-background` (lightest),
    `bg-muted/40` or `bg-muted` (mid), `bg-card` (slightly off, often warmer).
    When the SECTION wrapper uses one of these, the CARDS inside MUST
    use a DIFFERENT one — never same-on-same:
      ✗ section `bg-muted` + card `bg-muted`            (invisible cards)
      ✗ section `bg-muted/40` + card `bg-muted/40`      (invisible cards)
      ✗ section `bg-card` + card `bg-card`              (invisible cards)
      ✗ section `bg-background` + card `bg-background`  (cards disappear, only borders)
      ✓ section `bg-muted/40` + card `bg-background` + border
      ✓ section `bg-background` + card `bg-card` OR `bg-muted/40` + border
      ✓ section `bg-card` + card `bg-background` + border
      ✓ section `bg-foreground text-background` (inverse) + card `bg-background/10` + border `border-background/20`
  • On INVERSE surfaces (`bg-foreground text-background`, dark hero, dark
    band): NEVER use `text-foreground` or `text-muted-foreground` for body
    copy — they're invisible against the inverted bg. Use `text-background`
    and `text-background/70` for muted. (Same rule as the footer; applies
    to every section that flips to dark.)
  • Headings on muted backgrounds use `text-foreground` (full opacity).
    Body copy on muted backgrounds uses `text-foreground/80` or
    `text-muted-foreground` — they sit on a light surface so contrast is OK.

DROPDOWN / POPOVER STACKING (custom selects, autocompletes, calendars)
  • Any panel positioned with `absolute` that appears on click/hover/focus
    (custom select dropdown, autocomplete suggestions, calendar popover,
    multi-select menu, share menu) MUST include `z-[80]` in its className.
    Why z-[80] and not z-50: floating hero data cards live at z-20, the sticky
    nav at z-50, and the dropdown must beat BOTH of those plus any sibling
    that creates its own stacking context (backdrop-blur, shadow-xl, transform).
    z-50 loses to a sibling card with backdrop-blur — shipped failure where
    a hero "Schedule" card rendered over the open select's option list.
  • The form CTA button (`<button>Find Your Course</button>`) sitting
    next to a custom select MUST NOT carry its own positive z-* class.
    Let normal stacking apply; the dropdown's z-[80] wins.
  • Floating hero data cards (the "Live wait time", "Schedule" overlay
    chips) stay at `z-20` MAX — they exist to decorate the hero photo, not
    to compete with form interactions in sections below the fold.
  • If you're using a `<select>` native element, no z-index is needed —
    browser handles it. Only custom-rolled dropdowns need this rule.

IMAGE RENDERING — NEVER SHIP A BROKEN OR PARTIAL IMAGE
  • EVERY `<Image>` / `<img>` MUST have a real URL coming from
    `landing.json[*].images[i].url` or `section.images[i]` — NEVER hardcode
    Unsplash URLs, NEVER use placeholder URLs like `/placeholder.jpg`,
    NEVER write `src=""`.
  • WRAP EVERY image in a sized container with this pattern:
      <div className="relative aspect-[4/3] overflow-hidden rounded-2xl bg-muted">
        <Image src={{img.url}} alt={{img.alt || section.headline}} fill
               sizes="(min-width: 1024px) 33vw, 100vw"
               className="object-cover" />
      </div>
    The `bg-muted` parent serves as a neutral placeholder while the image
    loads. Without it, a slow / failed image renders as raw white.
  • IF an image URL might be missing at runtime (the binder fills 95%+ but
    not 100%) you have TWO valid responses, depending on the layout:
      A. The image is ONE of N cards in a grid (gallery 3-up, team grid,
         press logos): gate the render with a small brand-tinted gradient
         tile (`bg-gradient-to-br from-muted to-card`) with the brand wordmark
         centered in `font-[family-name:var(--font-heading)] text-3xl
         text-muted-foreground/40`. The grid stays intact.
      B. The image is the ENTIRE SIDE of a split-layout section (story split,
         method split, FAQ split, contact split — anything where the image
         takes ~40-50% of the section width): DO NOT render the giant
         monogram fallback panel. INSTEAD, drop the side panel entirely
         and let the text column span full width:
            const hasHero = section?.images?.[0]?.url;
            return (
              <section ...>
                <div className={{hasHero ? "grid lg:grid-cols-2 gap-12 items-center" : "max-w-3xl mx-auto"}}>
                  <div>{{/* text */}}</div>
                  {{hasHero && <div>{{/* image */}}</div>}}
                </div>
              </section>
            );
         A giant blank cream panel with a single decorative letter floating
         in it is the canonical "missing image" failure — readers see a
         broken layout. Collapsing the side to text-only reads as
         intentional editorial.
    NEVER render an empty rounded rectangle that exposes a hover-state
    overlay button (e.g. "VIEW") sitting underneath — that reads as a
    broken card.
  • ASPECT RATIO discipline: photo cards in a grid SHARE one aspect ratio
    so the grid lines up. Hero photo: `aspect-[4/5]` (portrait) or
    `aspect-[5/4]` (landscape). Card images in a 3-up: `aspect-[4/3]`.
    Bento mosaic: vary aspects intentionally (`aspect-[4/3]`, `aspect-square`,
    `aspect-[3/4]`). NEVER leave an image with no aspect ratio — it will
    render at 0×0 or full container height depending on parent.
  • CSS: ALWAYS use `object-cover` (NEVER `object-fill`, NEVER `object-contain`
    for editorial photo cards — only `object-contain` for logos / wordmarks
    that must not crop). `object-cover` keeps the composition tight.
  • For Next.js `<Image>`: ALWAYS pass `sizes` prop matching the layout
    (`sizes="(min-width: 1024px) 33vw, 100vw"` for a 3-col grid that
    stacks on mobile). Missing `sizes` triggers Next warnings AND ships
    oversized images that hurt LCP.
  • Image LOADING priority: only the HERO image gets `priority` — every
    other image stays lazy-loaded (default). Multiple `priority` images
    contend for bandwidth and hurt first paint.
"""


def _user_prompt(
    section: dict[str, Any],
    siblings: list[dict[str, Any]],
    component_name: str,
    file_path: str,
    *,
    section_index: int = 0,
    section_count: int = 1,
    voice_context: dict[str, Any] | None = None,
    visual_dna: dict | None = None,
    header_archetype: str = "transparent-pill",
) -> str:
    """Per-section user prompt — section spec + 2 sibling specs for cohesion."""
    sib_summaries = []
    for sib in siblings:
        sib_summaries.append(
            f"  - id={sib.get('id')!r} type={sib.get('type')!r} layout={sib.get('layout_hint')!r} archetype={sib.get('archetype','')!r}"
        )
    sib_block = "\n".join(sib_summaries) or "  (none)"

    # Visual rhythm cue: rotate through a NEUTRAL-DOMINANT bg cycle.
    # Reference sites (Bella Luna, Chanel, WAAW, Veloretti, architecture
    # studios) use ONE primary surface across most of the page with subtle
    # card-level variation; brand color appears in CTAs / eyebrows / icons,
    # NOT as alternating full bands. Hero (index 0) is the photo hero so
    # we still use bg-background under it.
    #
    # bold intensity (default) gets ONE optional inverse band (`bg-foreground
    # text-background`) for visual rhythm — that's the "Bella Luna pattern":
    # mostly-light page with one dark band, or vice versa. Subtle intensity
    # never inverts.
    vd_local = visual_dna or {}
    is_bold = (vd_local.get("cultural_intensity") or "bold").strip().lower() != "subtle"

    if is_bold:
        # 5 stops: 3 neutrals + 1 warm tint + 1 inverse band. Neutral surfaces
        # still dominate (3/5 stops) but the cycle has one real color hit
        # and one inverse band over the course of a long page.
        _BG_CYCLE = [
            "bg-background",
            "bg-muted/40",
            "bg-card",
            "bg-foreground text-background",   # one inverse band per cycle
            "bg-background",
        ]
    else:
        _BG_CYCLE = ["bg-background", "bg-muted/40", "bg-card", "bg-background"]

    if section_index == 0:
        bg_hint = "bg-background"
    else:
        bg_hint = _BG_CYCLE[section_index % len(_BG_CYCLE)]
    prev_bg_hint = "bg-background" if section_index <= 1 else _BG_CYCLE[(section_index - 1) % len(_BG_CYCLE)]

    has_images = bool(section.get("image_queries") or section.get("images"))
    image_hint = ""
    if not has_images:
        # Pick a no-photo layout variant deterministically from section_index so
        # adjacent text-only sections naturally land on different patterns.
        # This is the main lever against the "everything looks like a 3-col
        # icon-card grid" monotony problem.
        _NO_PHOTO_VARIANTS = [
            (
                "EDITORIAL_NUMBERED_LIST",
                "Large oversized numerals (text-7xl md:text-8xl font-serif tabular-nums text-primary/80) "
                "in the leftmost column, each followed by item title (text-2xl font-semibold) and a "
                "1-2 sentence body. Hairline `border-t border-border/60` between rows. NO cards, NO icons, "
                "NO uniform tiles — pure editorial typography. Container max-w-4xl mx-auto."
            ),
            (
                "ASYMMETRIC_BENTO_TEXT",
                "Bento grid `grid grid-cols-12 gap-4 lg:gap-6 auto-rows-[180px]`. First item spans "
                "`col-span-12 lg:col-span-7 row-span-2` with a big serif title + body. Second spans "
                "`col-span-12 lg:col-span-5 row-span-1` with stat-style content. Remaining items in "
                "`col-span-6 lg:col-span-4` tiles. Each tile uses `bg-card border border-border rounded-2xl p-6 lg:p-8` — "
                "but vary surface tint: alternate between `bg-card`, `bg-muted/40`, and `bg-primary/5`."
            ),
            (
                "VERTICAL_TIMELINE",
                "Vertical line down the center (or left side on mobile). Each item is a card alternating "
                "`md:translate-x-0` vs `md:translate-x-[55%]` so it zig-zags. Connector dot at the line "
                "(`w-3 h-3 rounded-full bg-primary` absolutely positioned). Card uses `bg-card border border-border` "
                "with the title, description, and (if item.label or item.value) a metric line. Container max-w-5xl."
            ),
            (
                "PULL_QUOTE_NARRATIVE",
                "Single dominant pull-quote in HUGE serif (text-4xl md:text-6xl font-serif leading-[1.1]) "
                "spanning two grid columns. Right column has 3 supporting items as stacked text-only blocks "
                "with a hairline `border-l border-border pl-6` and small uppercase eyebrow. NO card chrome. "
                "Background is `bg-muted/20`. Container max-w-6xl."
            ),
            (
                "STAT_BAND_DETAILED",
                "Headline + intro centered. Below: a 2- or 3-column band of stat blocks (HUGE number + label) "
                "separated by `divide-x divide-border`. Below the band: items as 2-column text rows (icon-chip + "
                "title left, multi-sentence description right) — NOT identical card tiles. The stat band is the "
                "centerpiece; items are supporting copy."
            ),
            (
                "PILL_CLUSTER_NARRATIVE",
                "Centered narrative paragraph (text-xl md:text-2xl text-muted-foreground max-w-3xl) with KEY "
                "category words rendered as inline pill chips (`inline-flex items-center px-3 py-1 rounded-full "
                "bg-primary/10 text-primary text-sm font-medium`). Below the paragraph: a single horizontal row "
                "of larger pills (one per item) — clickable, each opens a slide-down detail panel below the row. "
                "NO grid of cards."
            ),
            (
                "SPLIT_HERO_BLOCK",
                "Two-column split: left col is a giant typographic block (eyebrow + serif headline that wraps "
                "across multiple lines + body + CTA), right col is a single tall accent panel `aspect-[3/4] "
                "bg-gradient-to-br from-primary/15 via-accent/10 to-muted/40 rounded-3xl relative overflow-hidden` "
                "with the brand initial as a HUGE watermark (text-[12rem] font-serif text-primary/20 absolute) and "
                "the items rendered as a small list overlaid bottom-left. NO grid of identical cards."
            ),
        ]
        _variant_idx = section_index % len(_NO_PHOTO_VARIANTS)
        _vname, _vspec = _NO_PHOTO_VARIANTS[_variant_idx]
        image_hint = (
            f"\nNO-PHOTO LAYOUT — IMPLEMENT THIS PATTERN ({_vname}, picked from section position):\n"
            f"  {_vspec}\n"
            "Reason: this section has no images, and a generic 3-column icon-card grid would make the page "
            "feel like a template. Above pattern is structurally distinct from icon-cards. Apply the design "
            "system tokens (motif, accent_shape, motion) within this skeleton."
        )

    # Anatomy injection — research-driven first, fallback skeleton second.
    # `_resolve_anatomy` checks visual_dna.section_anatomies[type] (Gemini-
    # written from grounded research), falls back to _FALLBACK_SKELETONS
    # (minimal structural floor), and finally _GENERIC_FALLBACK. The source
    # determines the framing language: research-grounded anatomies get
    # "IMPLEMENT THIS"; fallback skeletons get "STARTING POINT — overlay
    # cultural cues from visual_dna" so Claude treats them as a floor not
    # a ceiling.
    section_type = (section.get("type") or "").lower()
    anatomy_text, anatomy_source = _resolve_anatomy(section_type, visual_dna)
    if anatomy_source in ("research", "research-aliased"):
        archetype_block = (
            f"\nANATOMY — IMPLEMENT THIS EXACT SPEC (research-grounded for this brand's {section_type} section):\n"
            f"{anatomy_text}\n"
            "Tailwind class choices and copy positioning details are yours, but the structural skeleton, "
            "decorative integration, and scale tokens above are the spec. This anatomy was written from real "
            "grounded research about THIS brand — it is the right shape for this project."
        )
    else:
        archetype_block = (
            f"\nANATOMY — STARTING POINT (generic fallback for {section_type} — no research-grounded anatomy was produced for this brand):\n"
            f"{anatomy_text}\n"
            "This is a STRUCTURAL FLOOR, not a final design. The composition, decorative integration, and "
            "any cultural inflection should be driven by the VISUAL DNA block in the system prompt. Use the "
            "floor's z-stacking, overflow, and headline scale as invariants — but compose the actual look "
            "(asymmetric vs centered, photo-led vs editorial, motif placements) from visual_dna."
        )

    # Interactivity directive from the brief — this is the "must DO something"
    # instruction that gets implemented as React state + handlers.
    interactivity = (section.get("interactivity") or "").strip()
    interactivity_block = ""
    if interactivity:
        interactivity_block = (
            f"\nINTERACTIVITY — IMPLEMENT THIS BEHAVIOR FOR REAL (state + handlers, no stubs):\n"
            f"  {interactivity}\n"
            "Use useState/useEffect as needed. Mark the file 'use client'. "
            "Every clickable element must have a real, working onClick/href. "
            "Add keyboard handlers (Esc closes overlays; arrow keys for carousels) and ARIA attributes."
        )

    # Optional voice/context block — sourced from grounded research signals
    # merged into the brief by enrich_brief_with_signals(). When present,
    # this primes the copywriter on real customer phrasing, regional
    # anchors, and category jargon. Empty / missing fields are skipped.
    voice_block = ""
    vc = voice_context or {}
    voice_phrases   = list(vc.get("voice_phrases") or [])[:6]
    industry_terms  = list(vc.get("industry_terms") or [])[:8]
    regional_refs   = list(vc.get("regional_refs") or [])[:4]
    white_space     = list(vc.get("white_space") or [])[:3]
    if voice_phrases or industry_terms or regional_refs or white_space:
        parts: list[str] = ["\nVOICE & CONTEXT (grounded research signals — use as inspiration, NOT verbatim filler):"]
        if voice_phrases:
            parts.append("  • Real customer phrasing — mirror this register/cadence in headlines, eyebrow text, and CTA microcopy. Do NOT paste these as testimonials unless this is the testimonials section. Do NOT quote them word-for-word in the hero:")
            for p in voice_phrases:
                parts.append(f"      – {p}")
        if industry_terms:
            terms = ", ".join(industry_terms)
            parts.append(f"  • Industry vocabulary — weave 1-2 of these naturally into THIS section's body copy where they fit (skip if forced): {terms}")
        if regional_refs:
            parts.append("  • Regional anchors — for proof / about / location-flavored sections, reference 1 of these by name (skip for generic feature sections):")
            for r in regional_refs:
                if isinstance(r, dict):
                    parts.append(f"      – {r.get('name','')}: {r.get('context','')}")
                else:
                    parts.append(f"      – {r}")
        if white_space:
            parts.append("  • Differentiation angles competitors aren't taking — if THIS section is value-prop / differentiators, work one of these in:")
            for w in white_space:
                parts.append(f"      – {w}")
        parts.append("  IMPORTANT: this is voice priming, not a checklist. If a signal doesn't fit this section's role, ignore it. Never sacrifice clarity to shoehorn a phrase.")
        voice_block = "\n".join(parts)

    # Purpose directive — highest-priority structural law. Emitted whenever
    # analyze_intent picked a known purpose (hiring / lead_generation /
    # ecommerce / booking). Empty string skips the block.
    purpose_directive = (vc.get("purpose_directive") or "").strip()
    purpose_block = ""
    if purpose_directive:
        purpose_block = (
            "\n\nPURPOSE DIRECTIVE — read this BEFORE writing any JSX:\n"
            "This block declares what KIND of page this is and what its sections must accomplish. "
            "If it conflicts with COPY_DECK or LAYOUT_BLUEPRINT on STRUCTURAL questions (which sections "
            "exist, what's mandatory, what's forbidden), the directive wins. The deck still owns exact "
            "string content for sections that DO exist.\n"
            f"{purpose_directive}"
        )

    # ── HERO-HEADER COORDINATION (hero section only) ─────────────────
    # The MarketingHeader is generated as a parallel Claude call so the hero
    # section can't see its exact layout. Without this hint, the hero kept
    # placing floating data cards in the SAME corner as the header CTA,
    # producing visual collisions (e.g. "Order Online" pill button + "ORDER
    # PICKUP" overlay card stacked on top of each other). The header
    # archetype determines which corner the CTA lives in; pass that to the
    # hero so it places floating cards in opposite zones.
    header_block = ""
    if section_index == 0:
        _ha = (header_archetype or "transparent-pill").strip().lower()
        if _ha == "side-rail":
            cta_zone = "LEFT EDGE (vertical sidebar) — top corners of the hero are free"
            safe_zones = "anywhere except the left edge `left-0` strip"
            avoid_zones = "left-0 to left-24 vertical band"
        elif _ha == "centered-logo":
            cta_zone = "TOP-RIGHT (next to centered logo) AND sometimes TOP-LEFT — top edge is busy"
            safe_zones = "bottom-left, bottom-right, bottom-center"
            avoid_zones = "ALL top corners (top-0 to top-20)"
        else:
            # transparent-pill, solid-bar, mega-menu — all have CTA top-right
            cta_zone = "TOP-RIGHT (header pill/bar CTA button lives here)"
            safe_zones = "bottom-left, bottom-right (NEVER top-right, NEVER top-left if logo is wordmark on the right)"
            avoid_zones = "top-right, anywhere within 80px of `top-0 right-0`"
        header_block = (
            f"\n\n── HERO-HEADER COORDINATION (hero section only) ──\n"
            f"The MarketingHeader sits at the top of this section as a `{_ha}` archetype.\n"
            f"  • Header CTA zone:  {cta_zone}\n"
            f"  • Safe zones for floating data cards:  {safe_zones}\n"
            f"  • AVOID these zones (header will overlap):  {avoid_zones}\n"
            f"The hero `<section>` MUST add `pt-24 md:pt-28` so the header doesn't sit on top of the eyebrow/headline.\n"
            f"Floating data cards (per the SYSTEM hero rules) MUST sit in the lower half of the hero — `bottom-6 right-6` / `bottom-6 left-6`.\n"
        )

    return f"""Build ONE section component.

FILE PATH:     {file_path}
COMPONENT:     {component_name}
SECTION ID:    {section.get('id')}
SECTION TYPE:  {section.get('type')}
LAYOUT HINT:   {section.get('layout_hint')}
ROLE:          {section.get('role','')}
POSITION:      section {section_index + 1} of {section_count}.
SECTION_INDEX: {section_index}  (zero-based; use for editorial-numbering eyebrow as `String({section_index}+1).padStart(2,"0")` → `"{section_index + 1:02d}"`)
SECTION BG:    `{bg_hint}` — use this on the outer <section>. Previous section was `{prev_bg_hint}`, so DO NOT
               repeat that surface. Pair the bg with inner-card surfaces per the SECTION SURFACE RHYTHM rules.{header_block}

SECTION SPEC (this is also what `landing.sections.find(s => s.id === {section.get('id')!r})` returns at runtime; use the FIELDS to know what to render, but read VALUES from the JSON at runtime):
{json.dumps(section, indent=2, ensure_ascii=False)}

ADJACENT SECTIONS (these are around yours — your job is to look DIFFERENT from them, not similar):
{sib_block}{voice_block}

ANTI-MONOTONY RULE — CRITICAL:
  • If an adjacent section uses a 3-column card grid, YOURS MUST NOT.
  • If an adjacent section uses centered eyebrow + headline + grid, vary YOUR opening (try left-aligned, asymmetric, or split layout).
  • Pick a DIFFERENT dominant structural shape from your neighbors:
    {{single column narrative, 2-column split, 3-col grid, bento asymmetric, full-bleed band, timeline,
      stat-band, accordion list, marquee row, comparison table, pull-quote dominant, pill cluster}}.
  • If you and a neighbor have the SAME `type`, your `archetype` is your differentiator — use it.
  • Cohesion comes from SHARED design tokens (palette, fonts, accent_shape, motion) — NOT from copying their layout.
{image_hint}{archetype_block}{interactivity_block}{purpose_block}

Generate the component now. Output via write_project_files with exactly ONE file."""


async def _generate_one_section(
    section: dict[str, Any],
    siblings: list[dict[str, Any]],
    *,
    brand_name: str,
    motif: str,
    palette: dict,
    typography: dict,
    design_system: dict,
    personality: dict,
    references: list[dict],
    design_tokens: dict,
    section_index: int,
    section_count: int,
    api_key: str,
    websocket: Any,
    voice_context: dict[str, Any] | None = None,
    visual_dna: dict | None = None,
    header_archetype: str = "transparent-pill",
    reference_images: list[bytes] | None = None,
) -> dict[str, str] | None:
    """Generate one section component. Returns {'path', 'content'} or None on failure."""
    from app.services.project_generator import call_claude_for_json

    filename = _section_filename(section)
    component = _component_name(filename)
    file_path = f"src/components/sections/{filename}"

    sys_p = _system_prompt(
        brand_name, motif, palette, typography, design_system,
        personality=personality, references=references,
        design_tokens=design_tokens,
        visual_dna=visual_dna,
    )
    usr_p = _user_prompt(
        section, siblings, component, file_path,
        section_index=section_index, section_count=section_count,
        voice_context=voice_context,
        visual_dna=visual_dna,
        header_archetype=header_archetype,
    )

    # One retry per section. The model-fallback inside call_claude_for_json
    # handles upstream errors (sonnet → opus) but doesn't retry when Claude
    # returns a successful 200 with empty / missing-jsx content — which is
    # the case we keep losing sections to. Two attempts catches both:
    # transient infra blips AND occasional empty tool_use outputs.
    section_id = section.get("id")
    max_attempts = 2
    last_failure_reason = "unknown"

    # When reference screenshots are available, prepend a guidance note so
    # Claude knows the attached images are visual direction, not pixel-copy
    # targets. Empty list of refs → omitted, leaving the prompt unchanged.
    refs_for_call = list(reference_images or [])
    if refs_for_call:
        usr_p = (
            "VISUAL REFERENCES (attached images): below are 2-3 real reference "
            "sites in this brand's design family. They are DIRECTION, not "
            "templates — match their composition rhythm, type pairing, spacing "
            "discipline, and surface language for THIS brand. Do NOT pixel-copy. "
            "Do NOT use their copy / brand names. Use them to inform your hero "
            "composition choice (which pattern A-E fits this aesthetic), card "
            "treatment, and overall page rhythm.\n\n"
        ) + usr_p

    for attempt in range(1, max_attempts + 1):
        try:
            result = await asyncio.wait_for(
                call_claude_for_json(
                    system_prompt=sys_p,
                    user_prompt=usr_p,
                    api_key=api_key,
                    websocket=websocket,
                    max_tokens=_SECTION_MAX_TOKENS,
                    image_refs=refs_for_call,
                ),
                # Was 90s — at 16K max_tokens that's borderline (~107s if Claude
                # fills the budget at ~150 tok/s). Bumped to 240s so rich
                # sections (pricing tables, testimonial carousels) don't get
                # killed mid-stream and waste the partial response.
                timeout=240.0,
            )
        except asyncio.TimeoutError:
            last_failure_reason = "timeout after 240s"
            logger.warning(
                "section %s: codegen timed out on attempt %d/%d — skipping",
                section_id, attempt, max_attempts,
            )
            continue
        except Exception as exc:
            last_failure_reason = f"exception: {exc}"
            logger.warning(
                "section %s: codegen attempt %d/%d threw — %s",
                section_id, attempt, max_attempts, exc,
            )
            continue

        if not result or "files" not in result:
            last_failure_reason = "empty result (no 'files' key)"
            logger.warning(
                "section %s: codegen attempt %d/%d returned empty result",
                section_id, attempt, max_attempts,
            )
            continue

        files = result.get("files") or []
        if not files:
            last_failure_reason = "Claude returned 0 files"
            logger.warning(
                "section %s: codegen attempt %d/%d returned 0 files",
                section_id, attempt, max_attempts,
            )
            continue

        # Claude was asked for one file — take the first JSX/TSX.
        saw_jsx = False
        for f in files:
            path = (f.get("path") or "").strip()
            content = (f.get("content") or "")
            if path.endswith((".jsx", ".tsx")) and content:
                saw_jsx = True
                valid, reason = _section_content_looks_valid(
                    content,
                    str(section_id or ""),
                    component,
                )
                if not valid:
                    last_failure_reason = f"contract validation failed: {reason}"
                    logger.warning(
                        "section %s: codegen attempt %d/%d failed contract validation — %s",
                        section_id, attempt, max_attempts, reason,
                    )
                    break
                if attempt > 1:
                    logger.info("section %s: succeeded on retry (attempt %d)", section_id, attempt)
                try:
                    from app.services.telemetry import emit as _t_emit
                    _t_emit(
                        "section.generated",
                        section_type=section.get("type") or section.get("role") or "",
                        section_id=str(section_id or ""),
                        index=section_index,
                        attempts=attempt,
                        char_count=len(content),
                        used_image_refs=bool(refs_for_call),
                    )
                except Exception:
                    pass
                # Force the path to our canonical location so Claude can't pick a different folder
                return {"path": file_path, "content": content}

        if not saw_jsx:
            last_failure_reason = "no .jsx/.tsx file in result"
        logger.warning(
            "section %s: codegen attempt %d/%d did not produce an acceptable component — %s",
            section_id, attempt, max_attempts, last_failure_reason,
        )

    logger.error(
        "section %s: codegen FAILED after %d attempts — last failure: %s; writing deterministic fallback",
        section_id, max_attempts, last_failure_reason,
    )
    try:
        from app.services.telemetry import emit as _t_emit
        _t_emit(
            "section.fallback",
            section_type=section.get("type") or section.get("role") or "",
            section_id=str(section_id or ""),
            index=section_index,
            attempts=max_attempts,
            reason=last_failure_reason[:200],
        )
    except Exception:
        pass
    return _fallback_section_component(section, component, file_path)


def _pick_siblings(sections: list[dict[str, Any]], idx: int) -> list[dict[str, Any]]:
    """Two neighboring sections (prev + next) for cohesion context."""
    sibs: list[dict[str, Any]] = []
    if idx - 1 >= 0:
        sibs.append(sections[idx - 1])
    if idx + 1 < len(sections):
        sibs.append(sections[idx + 1])
    return sibs


async def generate_landing_sections(
    *,
    brief: dict[str, Any],
    workspace_path: str,
    api_key: str,
    websocket: Any = None,
    concurrency: int = 3,
    reference_images: list[bytes] | None = None,
) -> dict[str, Any]:
    """Generate all section components in parallel.

    Returns a dict with:
      sections: [{id, type, file_path, ok}]
      page_imports: list of import statements for app/page.jsx
      page_renders: list of <Component /> tags in render order
    """
    # Phase-0 ships MarketingHeader + MarketingFooter as layout-level
    # components reading from landing.json. Generating a per-section
    # HeaderSection / FooterSection here would just render a duplicate
    # below the page content, so we skip those types upfront.
    _LAYOUT_TYPES = {"header", "marketing_header", "navbar", "footer", "marketing_footer", "site_footer"}
    sections = [
        s for s in (brief.get("sections") or [])
        if (s.get("type") or "").lower() not in _LAYOUT_TYPES
    ]
    brand_name = (brief.get("brand") or {}).get("name", "")
    motif = (brief.get("motif") or "minimal").strip().lower()
    palette = dict(brief.get("palette") or {})
    typography = dict(brief.get("typography") or {})
    design_system = dict(brief.get("design_system") or {})
    personality = dict(brief.get("personality") or {})
    references = list(brief.get("references") or [])
    # design_tokens — pre-computed Tailwind class literals derived from
    # design_system in landing_brief._build_design_tokens. Falls back here
    # in case the brief came from an older path that didn't compute them.
    design_tokens = dict(brief.get("design_tokens") or {})
    if not design_tokens:
        from app.services.landing_brief import _build_design_tokens
        design_tokens = _build_design_tokens(design_system)

    # Visual DNA — concrete cultural cues from research. Lead design
    # directive when present; codegen falls back to enums-only if absent.
    visual_dna = dict(brief.get("visual_dna") or {})

    # Voice/context fields populated by enrich_brief_with_signals when the
    # research stage succeeded. Absent on briefs built without research,
    # in which case the user prompt skips the VOICE & CONTEXT block.
    # purpose_directive carries the ===PURPOSE_DIRECTIVE=== block text
    # built from analyze_intent's output (named_roles, primary_purpose,
    # urgency_signals). Empty string when no purpose was detected — every
    # section prompt then skips the directive block.
    voice_context = {
        "voice_phrases":     brief.get("voice_phrases") or [],
        "industry_terms":    brief.get("industry_terms") or [],
        "regional_refs":     brief.get("regional_refs") or [],
        "white_space":       brief.get("white_space") or [],
        "purpose_directive": brief.get("purpose_directive") or "",
    }

    # Header context — surfaced to the HERO section so its floating cards
    # don't collide with the header CTA. Sections are generated in parallel
    # so the hero never sees the actual rendered MarketingHeader; passing
    # the archetype + CTA-zone summary closes that cross-section gap.
    header_archetype = (brief.get("header_archetype") or "transparent-pill").strip().lower()

    # Reference screenshots — when present, attached to each Claude call as
    # image input blocks for visual composition grounding. Empty list = the
    # legacy text-only behavior. See landing_vision_refs.py.
    _refs = list(reference_images or [])
    if _refs:
        logger.info(
            "generate_landing_sections: attaching %d reference screenshots to each section call",
            len(_refs),
        )

    sem = asyncio.Semaphore(concurrency)

    total = len(sections)
    async def _bounded(idx: int, s: dict[str, Any]) -> tuple[int, dict[str, Any], dict[str, str] | None]:
        async with sem:
            try:
                res = await _generate_one_section(
                    s,
                    _pick_siblings(sections, idx),
                    brand_name=brand_name,
                    motif=motif,
                    palette=palette,
                    typography=typography,
                    design_system=design_system,
                    personality=personality,
                    references=references,
                    design_tokens=design_tokens,
                    section_index=idx,
                    section_count=total,
                    api_key=api_key,
                    websocket=websocket,
                    voice_context=voice_context,
                    visual_dna=visual_dna,
                    header_archetype=header_archetype,
                    reference_images=_refs,
                )
            except Exception as exc:
                logger.warning("section %s: unhandled exception in _bounded — %s", s.get("id"), exc)
                res = None
            return idx, s, res

    if websocket is not None:
        try:
            await websocket.send_json({
                "type": "progress",
                "message": f"⚡ Generating {len(sections)} sections in parallel...",
            })
        except Exception:
            pass

    results = await asyncio.gather(
        *(_bounded(i, s) for i, s in enumerate(sections)),
        return_exceptions=True,
    )
    # Restore original order — filter out any stray BaseException results defensively
    valid_results = [r for r in results if not isinstance(r, BaseException)]
    if len(valid_results) < len(results):
        logger.error("generate_landing_sections: %d section(s) raised unhandled exceptions", len(results) - len(valid_results))
    valid_results.sort(key=lambda x: x[0])

    sections_meta: list[dict[str, Any]] = []
    page_imports: list[str] = []
    page_renders: list[str] = []
    files_written: list[str] = []

    sections_dir = os.path.join(workspace_path, "src", "components", "sections")
    os.makedirs(sections_dir, exist_ok=True)

    for _, section, res in valid_results:
        filename = _section_filename(section)
        component = _component_name(filename)
        file_path = f"src/components/sections/{filename}"
        ok = bool(res)
        used_fallback = False

        if ok:
            written_rel = write_text_file(workspace_path, file_path, res["content"])
            if not written_rel:
                # Safe writer rejected the generated content (typically unbalanced
                # braces). Without this fallback, the section was silently dropped
                # and the final page came up missing sections. Inject a deterministic
                # skeleton so the page is always complete.
                logger.warning(
                    "section %s (%s): generated file rejected by safe writer — writing fallback skeleton",
                    section.get("id"), section.get("type"),
                )
                fb = _fallback_section_component(section, component, file_path)
                written_rel = write_text_file(workspace_path, file_path, fb["content"])
                used_fallback = True
            if written_rel:
                files_written.append(written_rel)
                page_imports.append(
                    f'import {component} from "@/components/sections/{component}";'
                )
                page_renders.append(f"<{component} />")
            else:
                ok = False
                logger.error(
                    "section %s (%s): fallback skeleton ALSO rejected by safe writer — section dropped",
                    section.get("id"), section.get("type"),
                )
        else:
            logger.warning("section %s (%s): codegen FAILED — skipping", section.get("id"), section.get("type"))

        sections_meta.append({
            "id": section.get("id"),
            "type": section.get("type"),
            "file_path": file_path,
            "component": component,
            "ok": ok,
            "fallback": bool(used_fallback or (res and res.get("fallback"))),
        })

    if websocket is not None:
        ok_count = sum(1 for m in sections_meta if m["ok"])
        fallback_count = sum(1 for m in sections_meta if m.get("fallback"))
        suffix = f" ({fallback_count} fallback)" if fallback_count else ""
        try:
            await websocket.send_json({
                "type": "progress",
                "message": f"✅ {ok_count}/{len(sections_meta)} sections generated{suffix}",
            })
        except Exception:
            pass

    return {
        "sections": sections_meta,
        "page_imports": page_imports,
        "page_renders": page_renders,
        "files_written": files_written,
    }


# ── Layout components (MarketingHeader + MarketingFooter) ─────────────

def _layout_system_prompt(
    component_name: str,
    file_path: str,
    archetype_label: str,
    anatomy: str,
    brand_name: str,
    motif: str,
    palette: dict,
    typography: dict,
    design_system: dict,
    personality: dict,
    references: list[dict],
    design_tokens: dict | None = None,
    visual_dna: dict | None = None,
    brand: dict | None = None,
    category: str = "",
) -> str:
    """System prompt for header/footer codegen — anatomy-driven, JSON-fed."""
    palette_lines = _format_palette_table(palette)
    ds = design_system or {}
    dt = design_tokens or {}
    pers = personality or {}
    vd = visual_dna or {}
    b = brand or {}
    vibe = ", ".join(pers.get("vibe_keywords") or [])
    pers_block = (
        f"\nPERSONALITY (tone the layout to match this voice):\n"
        f"  Tone: {pers.get('tone', 'confident')}\n"
        f"  Vibe: {vibe or 'modern, clear'}\n"
        f"  Energy: {pers.get('energy', 'medium')}\n"
    )

    # Always-present brand context block. When visual_dna comes back empty
    # (Gemini call failure), this is the only source of per-brand variation
    # that Claude sees — without it every fallback footer ends up looking
    # the same generic 4-col template.
    brand_context_lines = []
    if b.get("tagline"):
        brand_context_lines.append(f"  Tagline: {b['tagline']}")
    desc = (b.get("description") or "").strip()
    if desc:
        # Cap to keep the prompt tight; Claude only needs the gist.
        brand_context_lines.append(f"  Description: {desc[:240]}")
    if category:
        brand_context_lines.append(f"  Category: {category}")
    info = b.get("business_info") or {}
    if isinstance(info, dict) and info:
        info_keys = [k for k in ("address", "phone", "email", "hours", "city") if info.get(k)]
        if info_keys:
            brand_context_lines.append(
                f"  Business info available in landing.brand.business_info: {', '.join(info_keys)} "
                f"(SURFACE these in the footer when the anatomy has room — phone as tel:, email as mailto:, address inline)"
            )
    social = b.get("social") or []
    if social:
        brand_context_lines.append(
            f"  Social handles available in landing.brand.social: {len(social)} entries (render as icon-only links)"
        )
    brand_block = ""
    if brand_context_lines:
        brand_block = "\nBRAND CONTEXT (always-on — use to vary the look even when visual_dna is empty):\n" + "\n".join(brand_context_lines) + "\n"

    # Compact visual_dna block for header/footer — header is small, doesn't
    # need the full per-section flavors, just enough to flavor the brand
    # mark, nav style, and footer mood.
    visual_dna_block = ""
    if vd:
        intensity = (vd.get("cultural_intensity") or "bold").strip().lower()
        motifs = (vd.get("decorative_motifs") or [])[:3]
        type_voice = (vd.get("typography_voice") or "").strip()
        palette_emph = (vd.get("cultural_palette_emphasis") or "").strip()
        parts = [
            "\nVISUAL DNA (apply to brand mark, nav typography, and footer mood — accent positions, not full takeover):",
            f"  Intensity: {intensity}",
        ]
        if palette_emph:
            parts.append(f"  Palette emphasis: {palette_emph}")
        if type_voice:
            parts.append(f"  Typography voice: {type_voice}")
        if motifs:
            parts.append("  Decorative cues you may use sparingly (logo lockup accent, footer divider, social-icon row treatment):")
            for m in motifs:
                parts.append(f"    • {m}")
        parts.append(
            "  The header/footer carries the brand identity quietly — don't overload them. "
            "One brand-mark accent + one nav-type voice + restraint everywhere else."
        )
        visual_dna_block = "\n".join(parts) + "\n"
    ref_block = ""
    if references:
        rls = []
        for r in references[:3]:
            n = r.get("name") or r.get("url", "")
            why = r.get("why", "")
            rls.append(f"  • {n} — {why}")
        if rls:
            ref_block = "\nREFERENCE SITES (real sites this brief is grounded in):\n" + "\n".join(rls) + "\n"

    # Anatomy framing — research-grounded specs are authoritative; fallback
    # skeletons are floors that visual_dna composes on top of. Fallback
    # variants (e.g. "fallback:cta-band") are called out by name so Claude
    # implements the SHAPE described in the anatomy rather than defaulting
    # to its training-data instinct ("every footer is a 4-column megacolumn").
    label = (archetype_label or "").strip()
    if label.startswith("research"):
        anatomy_intro = "IMPLEMENT THIS EXACT SPEC (research-grounded for this brand)"
        anatomy_outro = (
            "Tailwind class choices and decorative details are yours, but the structural skeleton, "
            "scroll behavior, and decorative integration above are the spec. This anatomy was written "
            "from real research about THIS brand."
        )
    elif label.startswith("fallback:"):
        variant_name = label.split(":", 1)[1]
        anatomy_intro = (
            f"IMPLEMENT THIS SHAPE — variant: '{variant_name}'. "
            f"This shape was chosen for THIS brand's personality and category. "
            f"Do NOT silently swap to another footer pattern (a 4-col megacolumn is NOT a centered-stack, "
            f"a CTA-band is NOT a minimalist-row)"
        )
        anatomy_outro = (
            f"You MUST follow the '{variant_name}' structural floor above. "
            "Compose the visual look (decorative motifs, surface treatment, typography flavor) "
            "from the VISUAL DNA + BRAND CONTEXT blocks below — but the SHAPE is fixed."
        )
    else:
        anatomy_intro = "STARTING POINT (generic fallback — no research-grounded anatomy was produced for this layout)"
        anatomy_outro = (
            "This is a STRUCTURAL FLOOR. Use the floor's scroll behavior, container hierarchy, and "
            "responsive bones as invariants — but compose the actual look (brand mark style, nav "
            "typography, social row treatment, footer accent) from the VISUAL DNA block above."
        )

    return f"""You are a senior front-end engineer writing ONE Next.js layout component (header OR footer) for a landing page.

OUTPUT — ONE file via the write_project_files tool:
  • path: {file_path}
  • content: full source ready to import

MANDATORY RULES
1. The component reads its content from `@/content/landing.json` — never hardcode brand name, links, or copy. Pattern:
     import landing from "@/content/landing.json";
     const brand = landing.brand;
     const nav = landing.nav || [];     // header only
     const footer = landing.footer || {{}};   // footer only
     const cta = landing.ctas?.primary;
     const social = brand.social || [];
     const info = brand.business_info || {{}};
2. Use TAILWIND CLASSES ONLY. NEVER use the `style={{}}` prop on any element — not for colors, not for fonts, not for spacing, not for anything. The ONLY exception is `style={{ backgroundImage: `url(...)` }}` when applying a dynamic image. For fonts: brand wordmark, headlines, and any serif/display copy use the Tailwind class `font-[family-name:var(--font-heading)]`. Body / nav / button text inherits the body font from `<body>` automatically — do NOT re-declare it. NEVER write `style={{ fontFamily: ... }}` — that ships a hardcoded family name that paints UNDER the next/font CSS variable and produces a visible double-rendered text artifact (regular + serif overlapping). The `typography.heading_font` value below is INFORMATIONAL ONLY (it tells you what font is loaded as `--font-heading`); never embed the literal name in JSX.
3. Default-export a React function named `{component_name}` (matching filename).
4. Mark `'use client';` as the FIRST line if you use useState / useEffect / onClick.
5. Lucide-react icons for social (Instagram, Twitter, Facebook, Linkedin, Youtube, Github) and any UI affordances (Menu, X, ChevronDown). Map social.label string → icon via a small const dict.
6. NO CSS modules, NO styled-components, NO dynamic class strings Tailwind can't parse.
7. BORDER-RADIUS IS MANDATORY. Buttons, CTAs, and pill-style nav items use the
   PROJECT_DESIGN_TOKENS button_radius_class (below). Mobile-menu icon buttons use the same.
   Newsletter input/email-capture inputs use `rounded-md`. Logo lockup container `rounded-md`
   if it has a background color, no radius if it's transparent. NEVER use `rounded-none`
   on any header/footer element.
8. FOOTER COLUMNS — DRIVEN BY DATA, NEVER PADDED:
   • Render footer columns ONLY from the groups that actually exist in
     `landing.footer.links` (group by the `group` field; if absent, fall
     back to a single 'Explore' column).
   • NEVER hardcode a column-name list like
     `const DESIRED = ['Languages', 'About', 'Support', 'Legal']` and then
     pad missing groups with empty arrays. That produces ghost columns with
     only an em-dash or placeholder, which reads as a broken site.
   • NEVER render any placeholder ('—', '...', '(coming soon)', italicized
     blank) for an empty column. If a column has zero links, the column
     MUST NOT be rendered at all.
   • The grid column count adjusts to actual data, AND when there's only
     1 link group you MUST switch layout to avoid a single lonely column
     of links floating with empty dark space beside it:
       - 1 group  → render the links as a HORIZONTAL inline row above the
         copyright bar (`flex flex-wrap gap-x-6 gap-y-2`), NOT as a 1-of-N
         column. The brand block stays on top. No empty grid cells.
       - 2 groups → `grid-cols-1 sm:grid-cols-2` paired with the brand
         block (3-up overall: brand | group1 | group2).
       - 3 groups → `grid-cols-1 sm:grid-cols-2 md:grid-cols-4` (brand
         takes 1 col, 3 groups take 3 cols — fills the full row).
       - 4+ groups → `grid-cols-2 md:grid-cols-4` for the groups; brand
         block sits ABOVE on its own row at md+ (`md:col-span-full`).
   • The header labels above each list come from the actual `group` field
     in landing.footer.links (Title-Case it). Do not invent labels like
     "Languages", "Support", "Legal" if the data doesn't carry them.
9. FOOTER BODY — NO ORPHAN CTA BUTTONS:
   • The footer's PRIMARY purpose is wayfinding (links) + brand info +
     newsletter sign-up. NEVER place a primary CTA button ("Book a Free
     Trial", "Get Started", "Enroll Now", "Apply Today", "Buy Now") in
     the footer body content area. A primary CTA in the footer reads as
     a leftover orphan — the user has already chosen not to convert in
     the page above; repeating it here is noise.
   • Newsletter sign-up form IS allowed (email input + Subscribe button).
     That's the ONE conversion affordance footers carry.
   • Logos, social icon row, contact details (phone/email/address/hours),
     and link columns are the body. Stick to those.
10. FOOTER BOTTOM BAR — TIGHT, SINGLE ROW:
    • Below the columns + separator line, render ONE row containing:
      `© {year} {brand.name}. All rights reserved.` on the left, optional
      legal-link row (Privacy, Terms) and/or short tagline on the right.
    • NEVER spread the bottom bar across multiple rows with large gaps.
      `flex flex-col gap-3 md:flex-row md:items-center md:justify-between`
      with `py-6` padding. No extra blank space below.

ANATOMY — {anatomy_intro}:
{anatomy}
{anatomy_outro}

INTERACTIVITY (REQUIRED)
  • Sticky/fixed headers: useEffect listens to window.scrollY → setScrolled(true) past 8px → flips classes (transparent → solid w/ backdrop-blur). NO exceptions on mobile-only headers.
  • Mobile menu: useState `open`. Hamburger button toggles. ESC closes. Click outside closes (use a backdrop div with onClick).
  • Mega-menu: useState tracks open panel by index. Hover or click opens. Esc / clicking another link closes.
  • Footer newsletter form (if applicable): useState for email + submitted; client-side email validation; success state.
  • Every <Link> / <a> MUST resolve to a valid in-page anchor (`#features`, `#contact`) or external URL — never `href="#"` placeholders. Use anchors derived from `landing.nav` and `landing.footer.links`.
  • aria-label on all icon-only buttons; aria-expanded on toggles.

DESIGN CONTEXT
  Brand: {brand_name}
  Motif: {motif}
  Heading font: {typography.get("heading_font", "Inter")}
  Body font:    {typography.get("body_font", "Inter")}
  Palette (already wired as CSS vars in globals.css):
{palette_lines}
  Motion: {ds.get("motion", "subtle")}
  Accent shape: {ds.get("accent_shape", "rounded")}
  Surface: {ds.get("surface", "elevated")}

PROJECT_DESIGN_TOKENS — USE THESE EXACT TAILWIND CLASS STRINGS VERBATIM in the layout (do NOT improvise alternates):
  • Button / pill / CTA border-radius:   {dt.get("button_radius_class", "rounded-md")}
  • Standard transition for hover:       {dt.get("transition_class", "transition-all duration-300")}
  Examples:
    <Link className="{dt.get("button_radius_class", "rounded-md")} bg-primary text-primary-foreground px-5 py-2.5 {dt.get("transition_class", "transition-all duration-300")} hover:opacity-90">
    <button aria-label="Open menu" className="{dt.get("button_radius_class", "rounded-md")} p-2 {dt.get("transition_class", "transition-all duration-300")} hover:bg-muted">
{brand_block}{visual_dna_block}{pers_block}{ref_block}

CONTRAST & READABILITY (NON-NEGOTIABLE)
  • Nav links: `text-foreground/80 hover:text-foreground` on solid header surfaces; on transparent-pill / floating-glass
    headers add `backdrop-blur-md bg-background/80` to the pill so links remain readable over any photo behind. Never put
    `text-white` on a transparent header that sits above a light hero image.
  • Header CTA button: `bg-primary text-primary-foreground` (solid). On a transparent-pill, the CTA still uses the SAME
    solid pill chip — never a low-opacity outline that disappears on light photos.
  • The header MUST remain readable when scrolled past the hero (where the page surface is `bg-background`, light): when
    the user scrolls past 8px, swap to a SOLID surface (`bg-background/95 backdrop-blur` + `border-b border-border`) so
    the navigation never becomes invisible.
  • Footer link text: `text-muted-foreground hover:text-foreground` (full opacity). Footer headings: `text-foreground`.
  • DARK FOOTER SURFACES — when the footer wrapper uses `bg-foreground`,
    `bg-secondary` (dark brand color), or any dark `bg-*`:
      ✗ NEVER `text-foreground` or `text-muted-foreground` (invisible — same hue as bg).
      ✗ NEVER `text-primary-foreground` (only pairs with `bg-primary`, NOT secondary).
      ✓ Body / nav links: `text-background hover:text-background/80`.
      ✓ Muted descriptions (newsletter sublabel, copyright): `text-background/70`.
      ✓ Headings (column labels): `text-background` full opacity.
      ✓ Newsletter input: `bg-background/10 text-background placeholder:text-background/50 border-background/20`.
      ✓ Section divider line: `border-background/15`.
    This is the #1 footer failure: white-on-white or brown-on-brown text
    because the writer reached for `text-foreground` / `text-muted-foreground`
    on an inverse surface. Always invert text colors on inverse surfaces.

QUALITY BAR
  • Looks like a real, professional layout for this brand — not a generic template.
  • Real interactivity (state + handlers), not stubs.
  • Pixel-clean spacing (gap-6 / gap-8 / py-3 / py-4 / h-14 / h-16, not random values).
  • Header: max 6 nav links, each 1 short word. The nav array passed in is already short — DO NOT repeat words or
    re-expand them ("Stays" stays "Stays", never "Accommodations Showcase").
"""


def _layout_user_prompt(
    section: dict[str, Any],
    component_name: str,
    file_path: str,
) -> str:
    return f"""Build ONE layout component.

FILE PATH:    {file_path}
COMPONENT:    {component_name}
ARCHETYPE:    (see anatomy in system prompt)

DERIVED CONTEXT (read at runtime from landing.json — your component must do this, do NOT inline):
  • landing.brand               — {{name, tagline, description, business_info, social}}
  • landing.nav                 — [{{label, href}}] for the header
  • landing.footer              — {{brand, links: [{{label, href}}]}} for the footer
  • landing.ctas.primary        — {{label, href}} for the CTA button

Generate the component now. Output via write_project_files with exactly ONE file."""


async def _generate_layout_component(
    *,
    kind: str,  # "header" | "footer"
    archetype_label: str,
    anatomy: str,
    brand_name: str,
    motif: str,
    palette: dict,
    typography: dict,
    design_system: dict,
    personality: dict,
    references: list[dict],
    design_tokens: dict,
    api_key: str,
    websocket: Any,
    visual_dna: dict | None = None,
    brand: dict | None = None,
    category: str = "",
) -> dict[str, str] | None:
    from app.services.project_generator import call_claude_for_json

    component = "MarketingHeader" if kind == "header" else "MarketingFooter"
    file_path = f"src/components/layout/{component}.jsx"

    sys_p = _layout_system_prompt(
        component, file_path, archetype_label, anatomy,
        brand_name, motif, palette, typography, design_system, personality, references,
        design_tokens=design_tokens,
        visual_dna=visual_dna,
        brand=brand,
        category=category,
    )
    usr_p = _layout_user_prompt({}, component, file_path)

    # One retry per layout component, mirroring the section retry. Header
    # and footer falling back to deterministic stubs is the OLD behavior;
    # giving Claude one more shot at producing a real component is cheaper
    # than the user seeing a generic stub.
    max_attempts = 2
    last_failure_reason = "unknown"

    for attempt in range(1, max_attempts + 1):
        try:
            result = await asyncio.wait_for(
                call_claude_for_json(
                    system_prompt=sys_p,
                    user_prompt=usr_p,
                    api_key=api_key,
                    websocket=websocket,
                    max_tokens=_SECTION_MAX_TOKENS,
                ),
                timeout=150.0,
            )
        except asyncio.TimeoutError:
            last_failure_reason = "timeout after 150s"
            logger.warning(
                "layout %s: codegen timed out after 150s on attempt %d/%d — falling back to stub",
                kind, attempt, max_attempts,
            )
            continue
        except Exception as exc:
            last_failure_reason = f"exception: {exc}"
            logger.warning(
                "layout %s: codegen attempt %d/%d threw — %s",
                kind, attempt, max_attempts, exc,
            )
            continue

        if not result or not result.get("files"):
            last_failure_reason = "empty result"
            logger.warning(
                "layout %s: codegen attempt %d/%d returned empty result",
                kind, attempt, max_attempts,
            )
            continue

        saw_jsx = False
        for f in result["files"]:
            path = (f.get("path") or "").strip()
            content = (f.get("content") or "")
            if path.endswith((".jsx", ".tsx")) and content:
                saw_jsx = True
                valid, reason = _layout_content_looks_valid(content, kind, component)
                if not valid:
                    last_failure_reason = f"contract validation failed: {reason}"
                    logger.warning(
                        "layout %s: codegen attempt %d/%d failed contract validation — %s",
                        kind, attempt, max_attempts, reason,
                    )
                    break
                if attempt > 1:
                    logger.info("layout %s: succeeded on retry (attempt %d)", kind, attempt)
                return {"path": file_path, "content": content}

        if not saw_jsx:
            last_failure_reason = "no .jsx/.tsx file in result"
        logger.warning(
            "layout %s: codegen attempt %d/%d did not produce an acceptable component — %s",
            kind, attempt, max_attempts, last_failure_reason,
        )

    logger.error(
        "layout %s: codegen FAILED after %d attempts — last failure: %s",
        kind, max_attempts, last_failure_reason,
    )
    return None


async def generate_layout_components(
    *,
    brief: dict[str, Any],
    workspace_path: str,
    api_key: str,
    websocket: Any = None,
) -> dict[str, bool]:
    """Generate MarketingHeader.jsx + MarketingFooter.jsx in parallel.

    Writes the two files into `src/components/layout/`. Returns a dict
    {header: bool, footer: bool} indicating success per component. Failures
    are logged but non-fatal — Phase-0 already wrote archetype-matched stub
    files so the layout import never 404s.
    """
    brand = dict(brief.get("brand") or {})
    brand_name = brand.get("name", "")
    category = (brief.get("category") or "").strip()
    motif = (brief.get("motif") or "minimal").strip().lower()
    palette = dict(brief.get("palette") or {})
    typography = dict(brief.get("typography") or {})
    design_system = dict(brief.get("design_system") or {})
    personality = dict(brief.get("personality") or {})
    references = list(brief.get("references") or [])
    design_tokens = dict(brief.get("design_tokens") or {})
    if not design_tokens:
        from app.services.landing_brief import _build_design_tokens
        design_tokens = _build_design_tokens(design_system)
    visual_dna = dict(brief.get("visual_dna") or {})

    # Resolve header + footer anatomies via the same pipeline used for sections:
    # research first (visual_dna.section_anatomies), fallback skeleton second.
    # Pass the brief so the footer fallback picker can pick a variant by
    # personality / category instead of always returning the same shape.
    header_anatomy, header_source = _resolve_anatomy("header", visual_dna, brief=brief)
    footer_anatomy, footer_source = _resolve_anatomy("footer", visual_dna, brief=brief)

    if websocket is not None:
        try:
            await websocket.send_json({
                "type": "progress",
                "message": f"⚡ Generating header ({header_source}) + footer ({footer_source}) in parallel...",
            })
        except Exception:
            pass

    header_task = _generate_layout_component(
        kind="header",
        archetype_label=header_source,
        anatomy=header_anatomy,
        brand_name=brand_name, motif=motif, palette=palette, typography=typography,
        design_system=design_system, personality=personality, references=references,
        design_tokens=design_tokens,
        api_key=api_key, websocket=websocket,
        visual_dna=visual_dna,
        brand=brand, category=category,
    )
    footer_task = _generate_layout_component(
        kind="footer",
        archetype_label=footer_source,
        anatomy=footer_anatomy,
        brand_name=brand_name, motif=motif, palette=palette, typography=typography,
        design_system=design_system, personality=personality, references=references,
        design_tokens=design_tokens,
        api_key=api_key, websocket=websocket,
        visual_dna=visual_dna,
        brand=brand, category=category,
    )

    header_res, footer_res = await asyncio.gather(header_task, footer_task, return_exceptions=True)
    if isinstance(header_res, BaseException):
        logger.error("generate_layout_components: header threw — %s", header_res)
        header_res = None
    if isinstance(footer_res, BaseException):
        logger.error("generate_layout_components: footer threw — %s", footer_res)
        footer_res = None

    layout_dir = os.path.join(workspace_path, "src", "components", "layout")
    os.makedirs(layout_dir, exist_ok=True)

    out: dict[str, bool] = {"header": False, "footer": False}

    for kind, res in (("header", header_res), ("footer", footer_res)):
        if not res:
            logger.warning("layout %s: codegen FAILED — pipeline will fall back to deterministic stub", kind)
            continue
        rel = write_text_file(workspace_path, res.get("path", ""), res.get("content", ""))
        if not rel:
            logger.warning("layout %s: skipped unsafe generated path %r", kind, res.get("path"))
            continue
        out[kind] = True
        logger.info("layout %s: wrote %s (%d bytes)", kind, rel, len(res["content"]))

    if websocket is not None:
        ok = sum(1 for v in out.values() if v)
        try:
            await websocket.send_json({
                "type": "progress",
                "message": f"✅ {ok}/2 layout components generated",
            })
        except Exception:
            pass

    return out


_FIXED_TOP_HEADER_PATTERN = re.compile(
    r"\bfixed\s+top-0\b|\bposition:\s*fixed\b|\bfloating\s+(glass\s+)?pill\b",
    re.IGNORECASE,
)


def _header_is_fixed_top(header_anatomy: str) -> bool:
    """Detect whether the resolved header anatomy describes a fixed-top header
    (which floats over content and needs the page to add pt compensation).

    Matches `fixed top-0`, `position: fixed`, or "floating pill" phrasing in
    the anatomy text. Sticky/in-flow headers don't match.
    """
    return bool(header_anatomy and _FIXED_TOP_HEADER_PATTERN.search(header_anatomy))


def write_landing_page_shell(
    workspace_path: str,
    page_imports: list[str],
    page_renders: list[str],
    *,
    header_anatomy: str = "",
) -> str:
    """Write `app/page.jsx` that imports + renders all section components in order.

    The shell is dead-simple — no header/footer here (those are layout-level
    components written by Phase 0 builders). Sections render top-to-bottom.

    `header_anatomy` is the resolved header anatomy text (research-grounded or
    fallback). When it describes a fixed-top header (`fixed top-0`, floating
    pill), <main> gets `pt-20 lg:pt-24` so content doesn't slide under the
    floating header. Sticky/in-flow headers stay padding-free.
    """
    app_dir = os.path.join(workspace_path, "src", "app")
    os.makedirs(app_dir, exist_ok=True)
    # Drop skeleton page.js so Next.js doesn't see two route entrypoints.
    for stale in ("page.js", "page.tsx"):
        sp = os.path.join(app_dir, stale)
        if os.path.exists(sp):
            try:
                os.remove(sp)
            except OSError:
                pass
    # Remove ALL route groups (e.g. `(marketing)`) — the Next.js template ships
    # `src/app/(marketing)/page.js` which resolves to "/" and conflicts with
    # the page.jsx we just wrote at the root. Vercel's build then fails with:
    #   ENOENT: ... `.next/server/app/(marketing)/page_client-reference-manifest.js`
    # because the duplicate route doesn't get a clean compile pass.
    import shutil as _shutil
    try:
        for entry in os.listdir(app_dir):
            full = os.path.join(app_dir, entry)
            if (
                os.path.isdir(full)
                and entry.startswith("(")
                and entry.endswith(")")
            ):
                _shutil.rmtree(full, ignore_errors=True)
                logger.info("write_landing_page_shell: removed conflicting route group %s", entry)
    except OSError as _route_err:
        logger.warning("write_landing_page_shell: route-group cleanup failed (non-fatal): %s", _route_err)

    imports_block = "\n".join(page_imports)
    renders_block = "\n      ".join(page_renders) if page_renders else "<div />"
    needs_top_pad = _header_is_fixed_top(header_anatomy)
    main_class = "min-h-screen bg-background text-foreground"
    if needs_top_pad:
        main_class += " pt-20 lg:pt-24"
    src = f"""{imports_block}

export default function Page() {{
  return (
    <main className="{main_class}">
      {renders_block}
    </main>
  );
}}
"""
    target_rel = write_text_file(workspace_path, "src/app/page.jsx", src)
    if not target_rel:
        raise RuntimeError("safe writer rejected src/app/page.jsx")
    target = os.path.join(workspace_path, target_rel)
    logger.info("write_landing_page_shell: wrote %s with %d sections", target, len(page_renders))
    return target
